from __future__ import annotations

import re
from typing import Iterable

from app.models import PurchaseOption
from app.services.product_matcher import extract_model_identifier, extract_variant_token, infer_brand, normalize_text

ACCESSORY_KEYWORDS = {
    "case",
    "cover",
    "cable",
    "charger",
    "dock",
    "adapter",
    "replacement",
    "protector",
    "skin",
    "sleeve",
    "stand",
    "mount",
    "battery",
    "earpad",
    "ear pad",
}
USED_CONDITIONS = {"used", "refurbished", "open_box"}


class RelevanceRanker:
    def score(self, query: str, offer: PurchaseOption) -> float:
        rejection = self.reject_reason(query, offer)
        if rejection is not None:
            return 0.0

        query_brand = infer_brand(query)
        query_model = extract_model_identifier(query)
        query_variant = extract_variant_token(query)
        score = 0.0

        if query_model and offer.model and normalize_text(query_model) == normalize_text(offer.model):
            score += 0.45
        if query_brand and offer.brand and normalize_text(query_brand) == normalize_text(offer.brand):
            score += 0.20
        if query_variant:
            if offer.variant and query_variant == offer.variant:
                score += 0.15
            elif offer.variant and query_variant != offer.variant:
                score -= 0.25
        elif offer.variant:
            score += 0.05

        score += self._domain_confidence(offer)
        score += self._query_overlap(query, offer) * 0.10

        if self._wrong_model_family(query_model, offer.model):
            score -= 0.35
        if self._looks_like_accessory(query, offer):
            score -= 0.40

        return max(0.0, min(1.0, round(score, 4)))

    def rank(self, query: str, offers: list[PurchaseOption]) -> list[PurchaseOption]:
        ranked: list[PurchaseOption] = []
        for offer in offers:
            rejection = self.reject_reason(query, offer)
            if rejection is not None:
                notes = list(offer.parse_notes)
                notes.append(f"rejected:{rejection}")
                ranked.append(offer.model_copy(update={"parse_notes": notes, "relevance_score": 0.0}))
                continue
            score = self.score(query, offer)
            notes = list(offer.parse_notes)
            if score >= 0.8:
                notes.append("high-relevance")
            ranked.append(offer.model_copy(update={"relevance_score": score, "parse_notes": notes}))
        kept = [offer for offer in ranked if offer.relevance_score >= 0.55]
        kept.sort(key=lambda offer: (-offer.relevance_score, offer.price, offer.source_domain))
        return kept

    def reject_reason(self, query: str, offer: PurchaseOption) -> str | None:
        if offer.condition in USED_CONDITIONS:
            return offer.condition
        if offer.price <= 0:
            return "missing-price"
        corpus = f"{offer.title} {offer.url} {offer.promo_text}".lower()
        if any(token in corpus for token in ("/search", "search?", "?k=", "?s=", "category", "forum", "review")):
            return "search-or-review-page"
        if self._looks_like_accessory(query, offer):
            return "accessory"
        return None

    def _query_overlap(self, query: str, offer: PurchaseOption) -> float:
        query_tokens = self._tokens(query)
        if not query_tokens:
            return 0.0
        haystack = self._tokens(f"{offer.title} {offer.brand} {offer.model} {offer.variant} {offer.source_domain}")
        hits = sum(1 for token in query_tokens if token in haystack)
        return hits / max(len(query_tokens), 1)

    @staticmethod
    def _domain_confidence(offer: PurchaseOption) -> float:
        notes = set(offer.parse_notes)
        if "jsonld" in notes and "product-page" in notes:
            return 0.10
        if "product-page" in notes:
            return 0.08
        if "jsonld" in notes:
            return 0.06
        return 0.02

    @staticmethod
    def _looks_like_accessory(query: str, offer: PurchaseOption) -> bool:
        query_text = normalize_text(query)
        title = normalize_text(offer.title)
        return any(keyword in title and keyword not in query_text for keyword in ACCESSORY_KEYWORDS)

    @staticmethod
    def _wrong_model_family(query_model: str, offer_model: str) -> bool:
        if not query_model or not offer_model:
            return False
        query_family = re.split(r"[-/ ]", query_model.upper())[0]
        offer_family = re.split(r"[-/ ]", offer_model.upper())[0]
        return bool(query_family and offer_family and query_family != offer_family)

    @staticmethod
    def _tokens(value: str) -> set[str]:
        return {
            token
            for token in (re.sub(r"[^a-z0-9]+", "", chunk.lower()) for chunk in value.split())
            if token and (len(token) >= 3 or any(char.isdigit() for char in token))
        }
