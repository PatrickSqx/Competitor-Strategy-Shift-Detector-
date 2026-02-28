from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable

from app.adapters.llm_client import LLMClient
from app.models import ComparisonCluster, PurchaseOption

COMMON_BRANDS = {
    "apple", "sony", "samsung", "bose", "google", "microsoft", "jbl", "beats", "lg", "hp", "dell", "lenovo",
    "asus", "acer", "canon", "nikon", "meta", "anker", "nintendo", "logitech", "marshall", "razer"
}
GENERIC_MODEL_STOPWORDS = {
    "wireless", "gaming", "mouse", "headphones", "headset", "earbuds", "esports", "black", "white",
    "silver", "blue", "red", "green", "pink", "edition", "series", "generation", "gen", "with", "for",
}
CAPACITY_PATTERN = re.compile(r"\b(32gb|64gb|128gb|256gb|512gb|1tb|2tb)\b", re.IGNORECASE)
MODEL_PATTERNS = [
    re.compile(r"\b[A-Z]{1,5}[-/][A-Z0-9-]{2,}\b", re.IGNORECASE),
    re.compile(r"\b[A-Z0-9]{2,}(?:[-/][A-Z0-9]{2,})+\b", re.IGNORECASE),
    re.compile(r"\b[A-Z]{1,4}[0-9][A-Z0-9-]{2,}\b", re.IGNORECASE),
]


def normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def infer_brand(title: str) -> str:
    lowered = normalize_text(title)
    tokens = lowered.split()
    for token in tokens[:3]:
        if token in COMMON_BRANDS:
            return token.title()
    return tokens[0].title() if tokens else ""


def extract_model_identifier(text: str) -> str:
    for pattern in MODEL_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(0).upper()
    combined = _extract_compound_model_identifier(text)
    if combined:
        return combined
    tokens = [
        token
        for token in re.findall(r"\b[a-zA-Z0-9-]{4,}\b", text)
        if re.search(r"[a-zA-Z]", token) and re.search(r"[0-9]", token)
    ]
    return tokens[0].upper() if tokens else ""


def extract_variant_token(text: str) -> str:
    match = CAPACITY_PATTERN.search(text)
    if match:
        return match.group(1).lower()
    return ""


class ProductMatcherService:
    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm

    def match(self, query: str, offers: list[PurchaseOption]) -> tuple[ComparisonCluster | None, list[PurchaseOption], list[str]]:
        warnings: list[str] = []
        if len(offers) < 2:
            warnings.append("At least two relevant purchase options are required for exact-match comparison.")
            return None, offers, warnings

        enriched = [self._enrich_offer(offer) for offer in offers]
        exact_cluster = self._exact_cluster(enriched)
        if exact_cluster is not None:
            return exact_cluster, exact_cluster.offers, warnings

        warnings.append("Exact model matching did not find a confident same-product cluster.")
        if self.llm is None or not self.llm.enabled:
            return None, enriched, warnings

        fuzzy = self.llm.match_same_product(query, enriched[:6])
        if fuzzy is None:
            warnings.append("Gemini fuzzy matching did not produce a confident same-product cluster.")
            return None, enriched, warnings

        matched = [enriched[index] for index in fuzzy.matched_indexes if 0 <= index < len(enriched[:6])]
        if len(matched) < 2:
            return None, enriched, warnings
        if not self._same_brand(matched) or self._variant_conflict(matched):
            warnings.append("Gemini returned a cluster that failed deterministic brand or variant checks.")
            return None, enriched, warnings
        if len({self._seller_or_domain(offer) for offer in matched}) < 2:
            warnings.append("Gemini returned fewer than two distinct sellers or domains.")
            return None, enriched, warnings

        confidence = max(0.8, min(0.95, fuzzy.confidence))
        updated = [offer.model_copy(update={"match_confidence": confidence}) for offer in matched]
        cluster = ComparisonCluster(
            cluster_id=f"fuzzy::{normalize_text(self._dominant_value(offer.brand for offer in updated))}::{normalize_text(self._dominant_value(offer.model for offer in updated) or query)}",
            brand=self._dominant_value(offer.brand for offer in updated) or infer_brand(query),
            model=self._dominant_value(offer.model for offer in updated) or extract_model_identifier(query) or query.upper(),
            variant=self._dominant_value(offer.variant for offer in updated),
            match_method="fuzzy_llm",
            confidence=round(confidence, 2),
            offer_count=len(updated),
            domains=sorted({offer.source_domain for offer in updated}),
            offers=updated,
        )
        warnings.append(fuzzy.rationale or "Gemini selected the strongest same-product cluster from the top ranked offers.")
        return cluster, updated, warnings

    def _exact_cluster(self, offers: list[PurchaseOption]) -> ComparisonCluster | None:
        groups: dict[str, list[PurchaseOption]] = defaultdict(list)
        for offer in offers:
            if not offer.brand or not offer.model:
                continue
            key = f"{normalize_text(offer.brand)}::{normalize_text(offer.model)}::{offer.variant}"
            groups[key].append(offer)

        viable_groups = [
            group for group in groups.values()
            if len(group) >= 2 and len({self._seller_or_domain(offer) for offer in group}) >= 2
        ]
        if not viable_groups:
            return None

        viable_groups.sort(
            key=lambda group: (
                len(group),
                len({self._seller_or_domain(offer) for offer in group}),
                round(sum(offer.relevance_score for offer in group) / max(len(group), 1), 4),
            ),
            reverse=True,
        )
        selected = viable_groups[0]
        model = self._dominant_value(offer.model for offer in selected)
        brand = self._dominant_value(offer.brand for offer in selected)
        variant = self._dominant_value(offer.variant for offer in selected)
        match_method = classify_exact_match_method(model)
        avg_relevance = sum(offer.relevance_score for offer in selected) / max(len(selected), 1)
        confidence = 0.96 if match_method == "exact_model" else 0.93
        confidence = round(max(0.85, min(0.99, confidence - 0.05 + avg_relevance * 0.05)), 2)
        updated = [offer.model_copy(update={"match_confidence": confidence}) for offer in selected]
        return ComparisonCluster(
            cluster_id=f"exact::{normalize_text(brand)}::{normalize_text(model)}::{variant}",
            brand=brand,
            model=model,
            variant=variant,
            match_method=match_method,
            confidence=confidence,
            offer_count=len(updated),
            domains=sorted({offer.source_domain for offer in updated}),
            offers=updated,
        )

    def _enrich_offer(self, offer: PurchaseOption) -> PurchaseOption:
        brand = offer.brand or infer_brand(offer.title)
        model = offer.model or extract_model_identifier(offer.title)
        variant = offer.variant or extract_variant_token(f"{offer.title} {model}")
        notes = list(offer.parse_notes)
        if offer.brand == "" and brand:
            notes.append("brand-inferred")
        if offer.model == "" and model:
            notes.append("model-inferred")
        if offer.variant == "" and variant:
            notes.append("variant-inferred")
        return offer.model_copy(update={"brand": brand, "model": model, "variant": variant, "parse_notes": notes})

    @staticmethod
    def _seller_or_domain(offer: PurchaseOption) -> str:
        return normalize_text(offer.seller_name or offer.source_domain)

    @staticmethod
    def _same_brand(offers: list[PurchaseOption]) -> bool:
        brands = {normalize_text(offer.brand) for offer in offers if offer.brand}
        return len(brands) <= 1

    @staticmethod
    def _variant_conflict(offers: list[PurchaseOption]) -> bool:
        variants = {offer.variant for offer in offers if offer.variant}
        return len(variants) > 1

    @staticmethod
    def _dominant_value(values: Iterable[str]) -> str:
        counts: dict[str, int] = defaultdict(int)
        for value in values:
            if value:
                counts[value] += 1
        if not counts:
            return ""
        return max(counts, key=counts.get)


def classify_exact_match_method(model: str) -> str:
    normalized = model.upper()
    letters = re.findall(r"[A-Z]", normalized)
    digits = re.findall(r"\d", normalized)
    if digits and not letters:
        return "exact_sku"
    if re.fullmatch(r"[A-Z0-9-]{6,}", normalized) and len(digits) >= len(letters) * 2 and len(letters) <= 2:
        return "exact_sku"
    return "exact_model"


def _extract_compound_model_identifier(text: str) -> str:
    tokens = re.findall(r"\b[A-Za-z0-9]+\b", text)
    best = ""
    best_score = -1
    for window in (3, 2):
        for index in range(len(tokens) - window + 1):
            parts = tokens[index:index + window]
            if any(part.lower() in COMMON_BRANDS or part.lower() in GENERIC_MODEL_STOPWORDS for part in parts):
                continue
            if not re.search(r"\d", parts[0]) and len(parts[0]) > 4:
                continue
            combined = "".join(parts).upper()
            if len(combined) < 4 or len(combined) > 18:
                continue
            if not re.search(r"[A-Z]", combined) or not re.search(r"\d", combined):
                continue
            digit_parts = sum(1 for part in parts if re.search(r"\d", part))
            short_parts = sum(1 for part in parts if len(part) <= 4)
            score = digit_parts * 3 + short_parts * 2 - max(len(combined) - 10, 0)
            if score > best_score:
                best = combined
                best_score = score
    return best
