from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from app.models import ListingSnapshot, ScrapeTarget


PROMO_KEYWORDS = [
    "sale",
    "discount",
    "coupon",
    "promo",
    "% off",
    "save",
    "deal",
    "flash",
]


class WebScraper:
    def __init__(self, timeout_seconds: float = 8.0) -> None:
        self.timeout_seconds = timeout_seconds

    def fetch_many(self, targets: list[ScrapeTarget], scenario: str = "current") -> list[ListingSnapshot]:
        snapshots: list[ListingSnapshot] = []
        for target in targets:
            snapshot = self.fetch_target(target)
            if scenario == "shock":
                snapshot = self._apply_shock(snapshot)
            snapshots.append(snapshot)
        return snapshots

    def fetch_target(self, target: ScrapeTarget) -> ListingSnapshot:
        try:
            response = httpx.get(
                target.url,
                timeout=self.timeout_seconds,
                headers={"User-Agent": "strategy-shift-agent/1.0"},
                follow_redirects=True,
            )
            response.raise_for_status()
            snapshot = self._parse_html(target, response.text)
            if snapshot is not None:
                return snapshot
        except Exception:
            pass

        return self._from_fallback(target)

    def _parse_html(self, target: ScrapeTarget, html: str) -> ListingSnapshot | None:
        soup = BeautifulSoup(html, "html.parser")

        if target.price_selector:
            node = soup.select_one(target.price_selector)
            price_value = self._extract_price(node.get_text(" ", strip=True) if node else "")
        else:
            price_value = self._extract_price(soup.get_text(" ", strip=True))

        if price_value is None:
            return None

        if target.promo_selector:
            promo_node = soup.select_one(target.promo_selector)
            promo_text = promo_node.get_text(" ", strip=True) if promo_node else ""
        else:
            full_text = soup.get_text(" ", strip=True)
            promo_text = self._extract_promo_line(full_text)

        stock_flag = self._detect_stock_flag(soup.get_text(" ", strip=True))
        promo_score = self._promo_score(promo_text)

        return ListingSnapshot(
            snapshot_id=str(uuid.uuid4()),
            competitor=target.competitor,
            sku=target.sku,
            source="live",
            url=target.url,
            captured_at=datetime.now(timezone.utc),
            price=price_value,
            promo_text=promo_text,
            promo_score=promo_score,
            stock_flag=stock_flag,
            reference_price=target.reference_price,
            undercut=price_value <= (target.reference_price * 0.95),
        )

    def _from_fallback(self, target: ScrapeTarget) -> ListingSnapshot:
        fallback_path = Path(target.fallback_file)
        payload = {}
        if fallback_path.exists():
            payload = json.loads(fallback_path.read_text(encoding="utf-8"))

        price = float(payload.get("price", target.reference_price))
        promo_text = str(payload.get("promo_text", ""))
        stock_flag = str(payload.get("stock_flag", "in_stock"))
        url = str(payload.get("url", target.url))

        return ListingSnapshot(
            snapshot_id=str(uuid.uuid4()),
            competitor=target.competitor,
            sku=target.sku,
            source="fallback",
            url=url,
            captured_at=datetime.now(timezone.utc),
            price=price,
            promo_text=promo_text,
            promo_score=self._promo_score(promo_text),
            stock_flag=stock_flag if stock_flag in {"in_stock", "low_stock", "out_of_stock"} else "in_stock",
            reference_price=target.reference_price,
            undercut=price <= (target.reference_price * 0.95),
        )

    def _apply_shock(self, snapshot: ListingSnapshot) -> ListingSnapshot:
        new_price = max(0.01, round(snapshot.price * 0.88, 2))
        promo_text = (snapshot.promo_text + " Flash sale 30% off + coupon available today").strip()
        promo_score = self._promo_score(promo_text)
        return snapshot.model_copy(
            update={
                "price": new_price,
                "promo_text": promo_text,
                "promo_score": promo_score,
                "undercut": new_price <= (snapshot.reference_price * 0.95),
            }
        )

    @staticmethod
    def _extract_price(text: str) -> float | None:
        match = re.search(r"\$?\s*([0-9]+(?:\.[0-9]{1,2})?)", text.replace(",", ""))
        if not match:
            return None
        return float(match.group(1))

    @staticmethod
    def _extract_promo_line(text: str) -> str:
        lowered = text.lower()
        for keyword in PROMO_KEYWORDS:
            idx = lowered.find(keyword)
            if idx >= 0:
                return text[max(0, idx - 40) : idx + 80].strip()
        return ""

    @staticmethod
    def _detect_stock_flag(text: str) -> str:
        lowered = text.lower()
        if "out of stock" in lowered:
            return "out_of_stock"
        if "low stock" in lowered or "only" in lowered:
            return "low_stock"
        return "in_stock"

    @staticmethod
    def _promo_score(text: str) -> int:
        lowered = text.lower()
        return sum(lowered.count(keyword) for keyword in PROMO_KEYWORDS)
