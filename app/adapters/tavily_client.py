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
        max_results: int = 8,
        query_variants: list[str] | None = None,
        include_domains: list[str] | None = None,
    ) -> list[DiscoveryCandidate]:
        if not self.enabled:
            return []

        variants = query_variants or [query]
        candidates: list[DiscoveryCandidate] = []
        seen_urls: set[str] = set()
        per_variant_results = max(2, min(max_results, 5))

        for variant in variants:
            raw = self._search(
                query=variant,
                max_results=per_variant_results,
                include_domains=include_domains,
                search_depth="advanced",
            )
            if raw is None:
                continue

            for item in raw.get("results", []):
                url = str(item.get("url", "")).strip()
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                parsed = urlparse(url)
                hostname = (parsed.netloc or "").lower()
                if not hostname:
                    continue
                candidates.append(
                    DiscoveryCandidate(
                        domain=hostname.replace("www.", ""),
                        title=str(item.get("title", "")).strip(),
                        url=url,
                        snippet=str(item.get("content", "")).strip()[:360],
                        score=self._coerce_score(item.get("score")),
                        source="tavily",
                    )
                )
                if len(candidates) >= max_results:
                    return candidates
        return candidates

    def _search(
        self,
        query: str,
        max_results: int,
        include_domains: list[str] | None = None,
        search_depth: str = "basic",
    ) -> dict[str, Any] | None:
        payload: dict[str, Any] = {
            "query": query,
            "search_depth": search_depth,
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
