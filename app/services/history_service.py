from __future__ import annotations

from app.models import CompareHistoryItem, CompareResponse
from app.services.query_discovery import QueryDiscoveryService


class HistoryService:
    def __init__(self, store) -> None:
        self.store = store

    def normalize_query(self, query: str) -> str:
        return QueryDiscoveryService.normalize_query(query)

    def record_compare_response(self, response: CompareResponse) -> None:
        try:
            self.store.record_compare_response(response)
        except Exception:
            return

    def get_history(self, query: str, limit: int = 6) -> list[CompareHistoryItem]:
        normalized_query = self.normalize_query(query)
        try:
            return self.store.get_compare_history(normalized_query, limit=limit)
        except Exception:
            return []
