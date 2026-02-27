from __future__ import annotations

import re

from app.adapters.llm_client import LLMClient
from app.models import OfferView, PricingFinding, ProductCluster

PROMO_PERCENT_PATTERN = re.compile(r'(\d{1,2})\s*%\s*off', re.IGNORECASE)
PROMO_DOLLAR_PATTERN = re.compile(r'save\s*\$\s*([0-9]+(?:\.[0-9]{1,2})?)', re.IGNORECASE)


class DifferentialPricingService:
    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm

    def analyze(
        self,
        query: str,
        matched_offers: list[OfferView],
        cluster: ProductCluster,
        coverage_status: str,
    ) -> PricingFinding:
        offers = sorted(matched_offers, key=lambda offer: offer.price)
        lowest = offers[0]
        highest = offers[-1]
        spread_percent = 0.0
        if lowest.price > 0:
            spread_percent = round(((highest.price - lowest.price) / lowest.price) * 100, 2)

        promo_gap = self._promo_gap_percent(offers)
        effective_gap = max(spread_percent, promo_gap)
        label = 'none'
        if effective_gap >= 25:
            label = 'critical'
        elif effective_gap >= 15:
            label = 'high'
        elif effective_gap >= 8:
            label = 'watch'

        coverage_factor = 1.0 if len({offer.platform for offer in offers}) == 3 else 0.88
        fuzzy_factor = 0.9 if cluster.match_method == 'fuzzy_llm' else 1.0
        completeness_factor = 0.92 if any('fallback' in note for offer in offers for note in offer.parse_notes) else 1.0
        confidence = max(0.2, min(0.98, round(cluster.confidence * coverage_factor * fuzzy_factor * completeness_factor, 2)))

        promo_note = 'Promotion asymmetry observed.' if promo_gap >= 10 else 'No material promotion asymmetry detected.'
        reasoning = (
            f'Public listed prices for the matched product vary by {spread_percent:.2f}% between '
            f'{lowest.platform} and {highest.platform}. {promo_note}'
        )
        claim_style_text = (
            'Suspicious differential pricing based on public listed prices only.'
            if label != 'none'
            else 'No strong suspicious differential pricing signal from public listed prices.'
        )
        evidence_notes = '; '.join(
            [
                'Compares public list prices only.',
                'Taxes, shipping, and checkout-only adjustments are excluded.',
                'This does not prove unlawful discrimination.',
                'Coverage is partial.' if coverage_status != 'full' else 'All supported platforms were covered in this run.',
            ]
        )

        if self.llm is not None and self.llm.enabled:
            narrative = self.llm.explain_pricing_comparison(
                query=query,
                offers=offers,
                draft_finding={
                    'label': label,
                    'spread_percent': spread_percent,
                    'promo_gap_percent': promo_gap,
                    'coverage_status': coverage_status,
                    'confidence': confidence,
                },
            )
            if narrative is not None:
                reasoning = narrative.reasoning
                claim_style_text = narrative.claim_style_text
                confidence = max(0.2, min(0.99, round(confidence + narrative.confidence_adjustment, 2)))

        return PricingFinding(
            label=label,
            spread_percent=spread_percent,
            lowest_platform=lowest.platform,
            highest_platform=highest.platform,
            reasoning=reasoning,
            confidence=confidence,
            claim_style_text=claim_style_text,
            evidence_notes=evidence_notes,
        )

    def _promo_gap_percent(self, offers: list[OfferView]) -> float:
        promo_values = [self._promo_percent(offer) for offer in offers]
        if max(promo_values, default=0.0) == 0.0:
            return 0.0
        return round(max(promo_values) - min(promo_values), 2)

    @staticmethod
    def _promo_percent(offer: OfferView) -> float:
        text = offer.promo_text or ''
        match = PROMO_PERCENT_PATTERN.search(text)
        if match:
            return float(match.group(1))
        match = PROMO_DOLLAR_PATTERN.search(text)
        if match and offer.price > 0:
            return round((float(match.group(1)) / offer.price) * 100, 2)
        if text:
            return 10.0
        return 0.0