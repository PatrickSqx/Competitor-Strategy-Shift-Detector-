from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx

from app.models import DiscoveryCandidate, EvidenceItem


class TavilyClient:
    def __init__(self, api_key: str, base_url: str = "https://api.tavily.com", timeout_seconds: float = 8.0) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def search_evidence(self, query: str, max_results: int = 3) -> list[EvidenceItem]:
        if not self.enabled:
            return []

        raw = self._search(query=query, max_results=max_results)
        if raw is None:
            return []

        return self._normalize(raw)

    def search_products(
        self,
        query: str,
        domains: list[str],
        max_results_per_domain: int = 5,
    ) -> list[DiscoveryCandidate]:
        if not self.enabled:
            return []

        candidates: list[DiscoveryCandidate] = []
        for domain in domains:
            raw = self._search(
                query=query,
                max_results=max_results_per_domain,
                include_domains=[domain],
            )
            if raw is None:
                continue

            for item in raw.get("results", []):
                url = str(item.get("url", "")).strip()
                if not url:
                    continue
                parsed = urlparse(url)
                platform = self._platform_from_hostname(parsed.netloc or domain)
                candidates.append(
                    DiscoveryCandidate(
                        platform=platform,
                        domain=domain,
                        title=str(item.get("title", "")).strip(),
                        url=url,
                        snippet=str(item.get("content", "")).strip()[:360],
                        score=self._coerce_score(item.get("score")),
                    )
                )
        return candidates

    def _search(
        self,
        query: str,
        max_results: int,
        include_domains: list[str] | None = None,
    ) -> dict[str, Any] | None:
        payload: dict[str, Any] = {
            "query": query,
            "search_depth": "basic",
            "topic": "general",
            "max_results": max_results,
            "include_answer": False,
        }
        if include_domains:
            payload["include_domains"] = include_domains
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            response = httpx.post(
                f"{self.base_url}/search",
                json=payload,
                headers=headers,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            return response.json()
        except Exception:
            return None

    @staticmethod
    def _normalize(payload: dict[str, Any]) -> list[EvidenceItem]:
        results: list[EvidenceItem] = []
        for item in payload.get("results", []):
            results.append(
                EvidenceItem(
                    title=str(item.get("title", "")),
                    url=str(item.get("url", "")),
                    snippet=str(item.get("content", ""))[:360],
                )
            )
        return results

    @staticmethod
    def _coerce_score(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _platform_from_hostname(hostname: str) -> str:
        lowered = hostname.lower()
        if "bestbuy" in lowered:
            return "Best Buy"
        if "walmart" in lowered:
            return "Walmart"
        if "target" in lowered:
            return "Target"
        return hostname
