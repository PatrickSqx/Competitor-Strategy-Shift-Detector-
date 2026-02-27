from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable

from app.adapters.llm_client import LLMClient
from app.models import OfferView, ProductCluster

COMMON_BRANDS = {
    'apple', 'sony', 'samsung', 'bose', 'google', 'microsoft', 'jbl', 'beats', 'lg', 'hp', 'dell', 'lenovo',
    'asus', 'acer', 'canon', 'nikon', 'meta', 'anker', 'nintendo', 'logitech', 'marshall'
}
CAPACITY_PATTERN = re.compile(r'\b(32gb|64gb|128gb|256gb|512gb|1tb|2tb)\b', re.IGNORECASE)
MODEL_PATTERNS = [
    re.compile(r'\b[A-Z]{1,5}[-/][A-Z0-9-]{2,}\b', re.IGNORECASE),
    re.compile(r'\b[A-Z0-9]{2,}(?:[-/][A-Z0-9]{2,})+\b', re.IGNORECASE),
    re.compile(r'\b[A-Z]{1,4}[0-9][A-Z0-9-]{2,}\b', re.IGNORECASE),
]


def normalize_text(value: str) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', value.lower()).strip()


def infer_brand(title: str) -> str:
    lowered = normalize_text(title)
    tokens = lowered.split()
    for token in tokens[:3]:
        if token in COMMON_BRANDS:
            return token.title()
    return tokens[0].title() if tokens else ''


def extract_model_identifier(text: str) -> str:
    for pattern in MODEL_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(0).upper()
    tokens = [token for token in re.findall(r'\b[a-zA-Z0-9-]{4,}\b', text) if re.search(r'[a-zA-Z]', token) and re.search(r'[0-9]', token)]
    return tokens[0].upper() if tokens else ''


def extract_variant_token(text: str) -> str:
    match = CAPACITY_PATTERN.search(text)
    if match:
        return match.group(1).lower()
    return ''


class ProductMatcherService:
    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm

    def match(self, query: str, offers: list[OfferView]) -> tuple[ProductCluster | None, list[OfferView], list[str]]:
        warnings: list[str] = []
        if len(offers) < 2:
            warnings.append('At least two priced product pages are required for cross-platform comparison.')
            return None, offers, warnings

        enriched = [self._enrich_offer(offer) for offer in offers]
        exact_cluster = self._exact_cluster(enriched)
        if exact_cluster is not None:
            cluster, matched_offers = exact_cluster
            return cluster, matched_offers, warnings

        warnings.append('Exact model matching did not find a confident cross-platform cluster.')
        if self.llm is None or not self.llm.enabled:
            return None, enriched, warnings

        fuzzy = self.llm.match_same_product(query, enriched)
        if fuzzy is None:
            warnings.append('Gemini fuzzy matching did not produce a confident same-product cluster.')
            return None, enriched, warnings

        matched = [enriched[index].model_copy(update={'match_confidence': max(0.8, fuzzy.confidence)}) for index in fuzzy.matched_indexes]
        platforms = sorted({offer.platform for offer in matched})
        if len(platforms) < 2:
            warnings.append('Gemini returned fewer than two distinct platforms for the same product.')
            return None, enriched, warnings
        brand = self._dominant_value(offer.brand for offer in matched)
        model = self._dominant_value(offer.model for offer in matched) or extract_model_identifier(query)
        cluster = ProductCluster(
            cluster_id=f'fuzzy::{normalize_text(brand)}::{normalize_text(model or query)}',
            brand=brand or infer_brand(query),
            model=model or query.upper(),
            match_method='fuzzy_llm',
            confidence=max(0.8, fuzzy.confidence),
            platforms=platforms,
            offer_count=len(matched),
        )
        warnings.append(fuzzy.rationale or 'Gemini fuzzy matching selected the best same-product cluster.')
        return cluster, matched, warnings

    def _exact_cluster(self, offers: list[OfferView]) -> tuple[ProductCluster, list[OfferView]] | None:
        groups: dict[str, list[OfferView]] = defaultdict(list)
        for offer in offers:
            if not offer.brand or not offer.model:
                continue
            variant = extract_variant_token(f'{offer.title} {offer.model}')
            key = f"{normalize_text(offer.brand)}::{normalize_text(offer.model)}::{variant}"
            groups[key].append(offer)

        viable_groups = [group for group in groups.values() if len({offer.platform for offer in group}) >= 2]
        if not viable_groups:
            return None

        viable_groups.sort(key=lambda group: (len({offer.platform for offer in group}), len(group)), reverse=True)
        selected = viable_groups[0]
        platforms = sorted({offer.platform for offer in selected})
        model = self._dominant_value(offer.model for offer in selected)
        brand = self._dominant_value(offer.brand for offer in selected)
        match_method = classify_exact_match_method(model)
        confidence = 0.96 if match_method == 'exact_model' else 0.93
        matched = [offer.model_copy(update={'match_confidence': confidence}) for offer in selected]
        cluster = ProductCluster(
            cluster_id=f'exact::{normalize_text(brand)}::{normalize_text(model)}',
            brand=brand,
            model=model,
            match_method=match_method,
            confidence=confidence,
            platforms=platforms,
            offer_count=len(matched),
        )
        return cluster, matched

    def _enrich_offer(self, offer: OfferView) -> OfferView:
        brand = offer.brand or infer_brand(offer.title)
        model = offer.model or extract_model_identifier(offer.title)
        match_key = normalize_text(model)
        notes = list(offer.parse_notes)
        if offer.brand == '' and brand:
            notes.append('brand-inferred')
        if offer.model == '' and model:
            notes.append('model-inferred')
        return offer.model_copy(update={'brand': brand, 'model': model, 'match_key': match_key, 'parse_notes': notes})

    @staticmethod
    def _dominant_value(values: Iterable[str]) -> str:
        counts: dict[str, int] = defaultdict(int)
        for value in values:
            if value:
                counts[value] += 1
        if not counts:
            return ''
        return max(counts, key=counts.get)


def classify_exact_match_method(model: str) -> str:
    normalized = model.upper()
    letters = re.findall(r'[A-Z]', normalized)
    digits = re.findall(r'\d', normalized)
    if digits and not letters:
        return 'exact_sku'
    if re.fullmatch(r'[A-Z0-9-]{6,}', normalized) and len(digits) >= len(letters) * 2 and len(letters) <= 2:
        return 'exact_sku'
    return 'exact_model'
