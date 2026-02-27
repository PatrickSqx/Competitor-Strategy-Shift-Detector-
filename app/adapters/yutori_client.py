from __future__ import annotations

from typing import Any

import httpx

from app.models import EvidenceItem, StrategySignal


class YutoriClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.yutori.com",
        timeout_seconds: float = 8.0,
        webhook_url: str = "",
        custom_recommend_url: str = "",
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.webhook_url = webhook_url
        self.custom_recommend_url = custom_recommend_url

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def recommend_action(self, signal: StrategySignal, evidence: list[EvidenceItem]) -> str:
        if not self.enabled or not self.custom_recommend_url:
            return self._heuristic_recommendation(signal)

        try:
            payload = {
                "signal_type": signal.signal_type,
                "severity": signal.severity,
                "competitor": signal.competitor,
                "sku": signal.sku,
                "confidence": signal.confidence,
                "evidence": [item.model_dump() for item in evidence],
            }
            response = httpx.post(
                self.custom_recommend_url,
                json=payload,
                headers={"x-api-key": self.api_key, "Content-Type": "application/json"},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            body: dict[str, Any] = response.json()
            recommendation = str(body.get("recommendation", "")).strip()
            if recommendation:
                return recommendation
        except Exception:
            pass

        return self._heuristic_recommendation(signal)

    def create_scout_task(self, signal: StrategySignal) -> str | None:
        if not self.enabled:
            return None

        payload: dict[str, Any] = {
            "display_name": f"{signal.competitor}-{signal.sku}-strategy-watch",
            "query": (
                f"Track {signal.competitor} pricing and promo changes for {signal.sku}. "
                "Report significant new discount patterns."
            ),
            "output_interval": 1800,
        }
        if self.webhook_url:
            payload["webhook_url"] = self.webhook_url

        try:
            response = httpx.post(
                f"{self.base_url}/v1/scouting/tasks",
                json=payload,
                headers={"x-api-key": self.api_key, "Content-Type": "application/json"},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            body: dict[str, Any] = response.json()
            for key in ("id", "task_id", "scout_id"):
                if key in body:
                    return str(body[key])
        except Exception:
            return None

        return None

    @staticmethod
    def _heuristic_recommendation(signal: StrategySignal) -> str:
        if signal.signal_type == "combined":
            return "Escalate immediately: test selective price match + limited-time promo counter in top regions."
        if signal.signal_type == "undercut":
            return "Open repricing task for top SKUs and monitor conversion before broad price changes."
        return "Increase promo monitoring and prepare defensive campaign assets for rapid launch."
