from __future__ import annotations

from app.models import EvidenceItem


class MockTavilyClient:
    enabled = False

    @staticmethod
    def search_evidence(query: str, max_results: int = 3) -> list[EvidenceItem]:
        return [
            EvidenceItem(
                title="Mock evidence",
                url="https://example.com/mock-evidence",
                snippet=f"Fallback evidence for query: {query[:120]}",
            )
        ][:max_results]


class MockYutoriClient:
    enabled = False

    @staticmethod
    def recommend_action(signal, evidence) -> str:
        if signal.signal_type == "combined":
            return "Mock recommendation: escalate dual-play response immediately."
        if signal.signal_type == "undercut":
            return "Mock recommendation: open repricing task."
        return "Mock recommendation: monitor promotion cadence."

    @staticmethod
    def create_scout_task(signal) -> str | None:
        return None
