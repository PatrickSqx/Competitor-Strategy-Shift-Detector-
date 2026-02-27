from __future__ import annotations

from typing import Any

import httpx

from app.models import EvidenceItem


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

        payload = {
            "query": query,
            "search_depth": "basic",
            "topic": "general",
            "max_results": max_results,
            "include_answer": False,
        }
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
            raw = response.json()
        except Exception:
            return []

        return self._normalize(raw)

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
