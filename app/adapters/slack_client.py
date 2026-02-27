from __future__ import annotations

import httpx

from app.models import EvidenceItem, StrategySignal


class SlackClient:
    def __init__(self, webhook_url: str, timeout_seconds: float = 8.0) -> None:
        self.webhook_url = webhook_url
        self.timeout_seconds = timeout_seconds

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url)

    def post_strategy_alert(
        self,
        signal: StrategySignal,
        message: str,
        channel: str,
        evidence: list[EvidenceItem],
    ) -> bool:
        if not self.enabled:
            return False

        evidence_line = "No external evidence returned."
        if evidence:
            top = evidence[0]
            evidence_line = f"{top.title or 'External signal'} - {top.url}"

        text = (
            f"[{signal.severity.upper()}] {signal.competitor} {signal.sku} {signal.signal_type}\n"
            f"Confidence: {signal.confidence:.2f}\n"
            f"Recommendation: {message}\n"
            f"Evidence: {evidence_line}"
        )

        payload = {
            "text": text,
            "channel": channel,
            "mrkdwn": True,
        }
        try:
            response = httpx.post(self.webhook_url, json=payload, timeout=self.timeout_seconds)
            response.raise_for_status()
            return True
        except Exception:
            return False
