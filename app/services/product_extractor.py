from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import re
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from app.models import DiscoveryCandidate, PurchaseOption
from app.services.product_matcher import extract_model_identifier, extract_variant_token, infer_brand

PROMO_PATTERNS = ("sale", "save", "discount", "coupon", "deal", "% off", "clearance", "gift card")
USED_MARKERS = {
    "used": "used",
    "pre-owned": "used",
    "pre owned": "used",
    "renewed": "refurbished",
    "refurbished": "refurbished",
    "open box": "open_box",
    "open-box": "open_box",
}
AVAILABILITY_MAP = {
    "instock": "in stock",
    "outofstock": "out of stock",
    "preorder": "preorder",
    "limitedavailability": "limited availability",
}


class ProductExtractorService:
    def __init__(
        self,
        timeout_seconds: float = 3.0,
        max_workers: int = 4,
        max_candidates_per_platform: int = 2,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_workers = max(1, max_workers)
        self.max_candidates_per_platform = max(1, max_candidates_per_platform)

    def extract_many(self, candidates: list[DiscoveryCandidate]) -> tuple[list[PurchaseOption], list[str]]:
        offers: list[PurchaseOption] = []
        warnings: list[str] = []
        selected = self._select_candidates(candidates)
        if not selected:
            return [], []

        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(selected))) as executor:
            future_map = {executor.submit(self.extract_one, candidate): candidate for candidate in selected}
            for future in as_completed(future_map):
                candidate = future_map[future]
                try:
                    offer = future.result()
                except Exception as exc:
                    warnings.append(f"Extractor failed for {candidate.domain}: {exc.__class__.__name__}.")
                    continue
                if offer is None:
                    warnings.append(f"Could not parse a priced product page for {candidate.domain}.")
                    continue
                offers.append(offer)
        offers.sort(key=lambda offer: (offer.source_domain, offer.price))
        return offers, warnings

    def extract_one(self, candidate: DiscoveryCandidate) -> PurchaseOption | None:
        try:
            response = httpx.get(
                candidate.url,
                timeout=self.timeout_seconds,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                follow_redirects=True,
            )
            response.raise_for_status()
        except Exception:
            return self._offer_from_candidate_preview(candidate)

        final_url = str(response.url)
        parsed = urlparse(final_url)
        source_domain = self._root_domain(parsed.netloc or candidate.domain)
        html = response.text
        soup = BeautifulSoup(html, "html.parser")
        page_text = soup.get_text(" ", strip=True)
        jsonld_product = self._extract_product_jsonld(soup)
        meta = self._extract_meta(soup)

        title = self._first_non_empty(
            self._from_product(jsonld_product, "name"),
            meta.get("og:title", ""),
            meta.get("twitter:title", ""),
            candidate.title,
            soup.title.get_text(strip=True) if soup.title else "",
        )
        brand = self._first_non_empty(self._brand_from_product(jsonld_product), infer_brand(title))
        model = self._first_non_empty(
            self._from_product(jsonld_product, "model"),
            self._from_product(jsonld_product, "mpn"),
            self._from_product(jsonld_product, "sku"),
            extract_model_identifier(title),
        )
        variant = extract_variant_token(f"{title} {model}")
        price = self._extract_price(jsonld_product, meta, soup, html)
        if price is None:
            return self._offer_from_candidate_preview(candidate, final_url=final_url, title=title, brand=brand, model=model, variant=variant)
        currency = self._first_non_empty(
            self._offer_value(jsonld_product, "priceCurrency"),
            meta.get("product:price:currency", ""),
            "USD",
        )
        promo_text = self._extract_promo_text(page_text, candidate.snippet)
        availability = self._normalize_availability(
            self._offer_value(jsonld_product, "availability") or meta.get("product:availability", "") or page_text
        )
        condition = self._infer_condition(title, candidate.snippet, page_text, final_url)
        image = self._first_non_empty(
            self._from_product(jsonld_product, "image"),
            meta.get("og:image", ""),
        )
        seller_name = self._first_non_empty(
            self._offer_seller_name(jsonld_product),
            meta.get("og:site_name", ""),
            self._humanize_domain(source_domain),
        )
        parse_notes = [candidate.source]
        if jsonld_product:
            parse_notes.append("jsonld")
        else:
            parse_notes.append("fallback")
        if any(marker in final_url.lower() for marker in ("/dp/", "/product/", "/site/", "/item/")):
            parse_notes.append("product-page")
        if candidate.snippet and promo_text == candidate.snippet:
            parse_notes.append("promo-from-discovery")

        return PurchaseOption(
            offer_id=self._offer_id(final_url),
            seller_name=seller_name,
            source_domain=source_domain,
            title=title or candidate.title or final_url,
            brand=brand,
            model=model,
            variant=variant,
            price=price,
            currency=currency.upper()[:3],
            condition=condition,
            promo_text=promo_text,
            availability=availability,
            url=final_url,
            image=image,
            relevance_score=0.0,
            match_confidence=0.0,
            parse_notes=parse_notes,
        )

    def _offer_from_candidate_preview(
        self,
        candidate: DiscoveryCandidate,
        final_url: str | None = None,
        title: str = "",
        brand: str = "",
        model: str = "",
        variant: str = "",
    ) -> PurchaseOption | None:
        if candidate.preview_price is None:
            return None
        url = final_url or candidate.url
        source_domain = self._root_domain(urlparse(url).netloc or candidate.domain)
        resolved_title = title or candidate.title or url
        resolved_brand = brand or infer_brand(resolved_title)
        resolved_model = model or extract_model_identifier(resolved_title)
        resolved_variant = variant or extract_variant_token(f"{resolved_title} {resolved_model}")
        condition = candidate.preview_condition if candidate.preview_condition else self._infer_condition(resolved_title, candidate.snippet, candidate.snippet, url)
        availability = candidate.preview_availability or self._normalize_availability(candidate.snippet)
        return PurchaseOption(
            offer_id=self._offer_id(url),
            seller_name=self._humanize_domain(source_domain),
            source_domain=source_domain,
            title=resolved_title,
            brand=resolved_brand,
            model=resolved_model,
            variant=resolved_variant,
            price=candidate.preview_price,
            currency=(candidate.preview_currency or "USD").upper()[:3],
            condition=condition,
            promo_text=self._extract_promo_text(candidate.snippet, candidate.snippet),
            availability=availability,
            url=url,
            image="",
            relevance_score=0.0,
            match_confidence=0.0,
            parse_notes=[candidate.source, "preview-price"],
        )

    def _extract_product_jsonld(self, soup: BeautifulSoup) -> dict[str, Any] | None:
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            content = script.string or script.get_text(strip=True)
            if not content:
                continue
            try:
                payload = json.loads(content)
            except json.JSONDecodeError:
                continue
            for node in self._iter_json_nodes(payload):
                type_value = node.get("@type")
                if type_value == "Product" or (isinstance(type_value, list) and "Product" in type_value):
                    return node
        return None

    def _iter_json_nodes(self, payload: Any):
        if isinstance(payload, list):
            for item in payload:
                yield from self._iter_json_nodes(item)
            return
        if isinstance(payload, dict):
            yield payload
            if "@graph" in payload:
                yield from self._iter_json_nodes(payload["@graph"])
            for value in payload.values():
                if isinstance(value, (dict, list)):
                    yield from self._iter_json_nodes(value)

    @staticmethod
    def _extract_meta(soup: BeautifulSoup) -> dict[str, str]:
        meta: dict[str, str] = {}
        for node in soup.find_all("meta"):
            key = node.get("property") or node.get("name") or node.get("itemprop")
            value = node.get("content")
            if key and value:
                meta[key.lower()] = value.strip()
        return meta

    @staticmethod
    def _from_product(product: dict[str, Any] | None, key: str) -> str:
        if not product:
            return ""
        value = product.get(key)
        if isinstance(value, list):
            value = value[0] if value else ""
        if isinstance(value, dict):
            value = value.get("name") or value.get("@id") or ""
        return str(value).strip()

    @staticmethod
    def _brand_from_product(product: dict[str, Any] | None) -> str:
        if not product:
            return ""
        brand = product.get("brand", "")
        if isinstance(brand, dict):
            return str(brand.get("name", "")).strip()
        if isinstance(brand, list) and brand:
            first = brand[0]
            if isinstance(first, dict):
                return str(first.get("name", "")).strip()
            return str(first).strip()
        return str(brand).strip()

    @staticmethod
    def _offer_value(product: dict[str, Any] | None, key: str) -> str:
        if not product:
            return ""
        offers = product.get("offers")
        if isinstance(offers, list):
            offers = offers[0] if offers else None
        if isinstance(offers, dict):
            return str(offers.get(key, "")).strip()
        return ""

    @staticmethod
    def _offer_seller_name(product: dict[str, Any] | None) -> str:
        if not product:
            return ""
        offers = product.get("offers")
        if isinstance(offers, list):
            offers = offers[0] if offers else None
        if isinstance(offers, dict):
            seller = offers.get("seller")
            if isinstance(seller, dict):
                return str(seller.get("name", "")).strip()
            if seller:
                return str(seller).strip()
        return ""

    def _extract_price(
        self,
        product: dict[str, Any] | None,
        meta: dict[str, str],
        soup: BeautifulSoup,
        html: str,
    ) -> float | None:
        candidates = [
            self._offer_value(product, "price"),
            meta.get("product:price:amount", ""),
            meta.get("price", ""),
            meta.get("twitter:data1", ""),
        ]
        itemprop_price = soup.select_one('[itemprop="price"]')
        if itemprop_price:
            candidates.append(itemprop_price.get("content") or itemprop_price.get_text(" ", strip=True))
        candidates.append(self._regex_find_price(html))
        for candidate in candidates:
            value = self._parse_price(candidate)
            if value is not None:
                return value
        return None

    @staticmethod
    def _parse_price(value: str | None) -> float | None:
        if not value:
            return None
        match = re.search(r"([0-9]+(?:\.[0-9]{1,2})?)", str(value).replace(",", ""))
        if not match:
            return None
        try:
            return float(match.group(1))
        except ValueError:
            return None

    @staticmethod
    def _regex_find_price(html: str) -> str:
        patterns = [
            r'"price"\s*:\s*"?([0-9]+(?:\.[0-9]{1,2})?)',
            r'\$\s*([0-9]+(?:\.[0-9]{1,2})?)',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                return match.group(1)
        return ""

    @staticmethod
    def _extract_promo_text(page_text: str, snippet: str) -> str:
        lowered = page_text.lower()
        for token in PROMO_PATTERNS:
            idx = lowered.find(token)
            if idx >= 0:
                return page_text[max(0, idx - 40): idx + 120].strip()
        return snippet.strip()

    @staticmethod
    def _normalize_availability(value: str) -> str:
        lowered = value.lower().replace(" ", "")
        for key, label in AVAILABILITY_MAP.items():
            if key in lowered:
                return label
        if "out of stock" in value.lower():
            return "out of stock"
        if "in stock" in value.lower():
            return "in stock"
        return "unknown"

    @staticmethod
    def _infer_condition(title: str, snippet: str, page_text: str, url: str) -> str:
        corpus = f"{title} {snippet} {page_text[:1500]} {url}".lower()
        for marker, condition in USED_MARKERS.items():
            if marker in corpus:
                return condition
        return "new" if "new" in corpus else "unknown"

    @staticmethod
    def _humanize_domain(domain: str) -> str:
        cleaned = domain.replace("www.", "").split(".")[0]
        return " ".join(part.capitalize() for part in cleaned.replace("-", " ").split())

    @staticmethod
    def _root_domain(domain: str) -> str:
        cleaned = domain.lower().replace("www.", "")
        parts = cleaned.split(".")
        if len(parts) >= 2:
            return ".".join(parts[-2:])
        return cleaned

    @staticmethod
    def _offer_id(url: str) -> str:
        return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _first_non_empty(*values: str) -> str:
        for value in values:
            if value and str(value).strip():
                return str(value).strip()
        return ""

    def _select_candidates(self, candidates: list[DiscoveryCandidate]) -> list[DiscoveryCandidate]:
        grouped: dict[str, list[DiscoveryCandidate]] = defaultdict(list)
        seen_urls: set[str] = set()
        for candidate in candidates:
            if candidate.url in seen_urls:
                continue
            seen_urls.add(candidate.url)
            grouped[candidate.domain].append(candidate)

        selected: list[DiscoveryCandidate] = []
        for domain in sorted(grouped):
            ranked = sorted(
                grouped[domain],
                key=lambda candidate: (candidate.score is not None, candidate.score or 0.0, len(candidate.title)),
                reverse=True,
            )
            selected.extend(ranked[: self.max_candidates_per_platform])
        return selected
