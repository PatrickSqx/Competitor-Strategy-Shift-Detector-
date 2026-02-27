from __future__ import annotations

import re

from app.adapters.llm_client import LLMClient
from app.models import ComparisonCluster, PricingFinding

PROMO_PERCENT_PATTERN = re.compile(r"(\d{1,2})\s*%\s*off", re.IGNORECASE)
PROMO_DOLLAR_PATTERN = re.compile(r"save\s*\$\s*([0-9]+(?:\.[0-9]{1,2})?)", re.IGNORECASE)


class DifferentialPricingService:
    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm

    def analyze(self, query: str, cluster: ComparisonCluster) -> PricingFinding:
        offers = sorted(cluster.offers, key=lambda offer: offer.price)
        lowest = offers[0]
        highest = offers[-1]
        spread_percent = 0.0
        if lowest.price > 0:
            spread_percent = round(((highest.price - lowest.price) / lowest.price) * 100, 2)

        promo_gap = self._promo_gap_percent(offers)
        effective_gap = max(spread_percent, promo_gap)
        alert_eligible = (
            len(offers) >= 3
            and all(offer.condition in {"new", "unknown"} for offer in offers)
            and cluster.confidence >= 0.85
            and (spread_percent >= 8.0 or promo_gap >= 10.0)
        )

        label = "none"
        if alert_eligible:
            if effective_gap >= 25.0:
                label = "critical"
            elif effective_gap >= 15.0:
                label = "high"
            elif effective_gap >= 8.0:
                label = "watch"

        confidence = round(min(0.99, max(0.25, cluster.confidence - (0.05 if len(offers) < 3 else 0.0))), 2)
        if len(offers) < 3:
            reasoning = (
                f"The exact-match cluster currently has {len(offers)} new-price offers. "
                f"The visible spread is {spread_percent:.2f}% between {lowest.seller_name} and {highest.seller_name}, "
                "but that is not enough evidence for a differential-pricing alert."
            )
            claim_style_text = "Insufficient evidence for a suspicious differential-pricing alert."
        elif alert_eligible:
            reasoning = (
                f"Public listed prices for the exact-match cluster vary by {spread_percent:.2f}% between "
                f"{lowest.seller_name} and {highest.seller_name}. Promotion asymmetry is "
                f"{promo_gap:.2f}% and the cluster confidence is {cluster.confidence:.2f}."
            )
            claim_style_text = "Public listed prices suggest a suspicious differential-pricing pattern, not unlawful discrimination."
        else:
            reasoning = (
                f"The exact-match cluster has {len(offers)} offers and a visible spread of {spread_percent:.2f}%, "
                f"but the evidence does not clear the strict alert gate."
            )
            claim_style_text = "No suspicious differential-pricing alert is warranted from the current public listed prices."

        evidence_notes = "; ".join(
            [
                "Compares public list prices only.",
                "Only new or unknown-condition offers are included.",
                "Taxes, shipping, and checkout-only adjustments are excluded.",
                "This does not prove unlawful discrimination.",
            ]
        )

        if self.llm is not None and self.llm.enabled:
            narrative = self.llm.explain_pricing_comparison(
                query=query,
                offers=offers,
                draft_finding={
                    "label": label,
                    "alert_eligible": alert_eligible,
                    "spread_percent": spread_percent,
                    "promo_gap_percent": promo_gap,
                    "cluster_confidence": cluster.confidence,
                    "offer_count": len(offers),
                },
            )
            if narrative is not None:
                reasoning = narrative.reasoning
                claim_style_text = narrative.claim_style_text
                confidence = round(max(0.25, min(0.99, confidence + narrative.confidence_adjustment)), 2)

        return PricingFinding(
            label=label,
            alert_eligible=alert_eligible,
            spread_percent=spread_percent,
            lowest_offer_id=lowest.offer_id,
            highest_offer_id=highest.offer_id,
            reasoning=reasoning,
            confidence=confidence,
            claim_style_text=claim_style_text,
            evidence_notes=evidence_notes,
        )

    def _promo_gap_percent(self, offers) -> float:
        promo_values = [self._promo_percent(offer.promo_text, offer.price) for offer in offers]
        if max(promo_values, default=0.0) == 0.0:
            return 0.0
        return round(max(promo_values) - min(promo_values), 2)

    @staticmethod
    def _promo_percent(promo_text: str, price: float) -> float:
        text = promo_text or ""
        match = PROMO_PERCENT_PATTERN.search(text)
        if match:
            return float(match.group(1))
        match = PROMO_DOLLAR_PATTERN.search(text)
        if match and price > 0:
            return round((float(match.group(1)) / price) * 100, 2)
        if text:
            return 10.0
        return 0.0
