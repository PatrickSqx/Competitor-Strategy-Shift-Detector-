from __future__ import annotations

import statistics
from dataclasses import dataclass

from app.models import ListingSnapshot


@dataclass
class DetectionResult:
    signal_type: str
    severity: str
    base_confidence: float
    rationale: str
    sustained_undercut: bool
    promo_spike: bool


class StrategyPolicy:
    def __init__(self, undercut_threshold: float = 0.05, promo_multiplier: float = 2.0) -> None:
        self.undercut_threshold = undercut_threshold
        self.promo_multiplier = promo_multiplier

    def detect(self, snapshot: ListingSnapshot, history: list[dict]) -> DetectionResult | None:
        previous_undercut = bool(history and history[0].get("undercut"))
        sustained_undercut = snapshot.undercut and previous_undercut

        promo_values = [int(item.get("promo_score", 0)) for item in history if item.get("promo_score") is not None]
        promo_baseline = statistics.median(promo_values) if promo_values else 1.0
        promo_threshold = max(2.0, promo_baseline * self.promo_multiplier)
        promo_spike = float(snapshot.promo_score) >= promo_threshold

        if not sustained_undercut and not promo_spike:
            return None

        if sustained_undercut and promo_spike:
            return DetectionResult(
                signal_type="combined",
                severity="high",
                base_confidence=0.80,
                rationale=(
                    "Sustained undercut detected for two consecutive runs and promo intensity "
                    f"({snapshot.promo_score}) exceeds baseline threshold ({promo_threshold:.1f})."
                ),
                sustained_undercut=True,
                promo_spike=True,
            )

        if sustained_undercut:
            return DetectionResult(
                signal_type="undercut",
                severity="medium",
                base_confidence=0.65,
                rationale=(
                    f"Consecutive undercut detected at >= {int(self.undercut_threshold * 100)}% below reference price."
                ),
                sustained_undercut=True,
                promo_spike=False,
            )

        return DetectionResult(
            signal_type="promo_intensity",
            severity="medium",
            base_confidence=0.65,
            rationale=(
                f"Promo intensity score {snapshot.promo_score} exceeds threshold {promo_threshold:.1f}."
            ),
            sustained_undercut=False,
            promo_spike=True,
        )

    @staticmethod
    def recommendation_for(signal_type: str) -> str:
        if signal_type == "combined":
            return "Launch a targeted counter-offer and review floor price guardrails within 30 minutes."
        if signal_type == "undercut":
            return "Open a repricing review task and assess selective match on high-volume SKUs."
        return "Increase promotional monitoring cadence and prepare a time-boxed campaign response."
