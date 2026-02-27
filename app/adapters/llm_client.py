from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from app.models import EvidenceItem, ListingSnapshot, OfferView


@dataclass
class LLMAnalysis:
    rationale: str
    recommended_action: str
    relevant_evidence_indexes: list[int]
    confidence_adjustment: float = 0.0


@dataclass
class LLMSameProductMatch:
    matched_indexes: list[int]
    confidence: float
    rationale: str


@dataclass
class LLMPricingNarrative:
    reasoning: str
    claim_style_text: str
    confidence_adjustment: float = 0.0


class LLMClient:
    def __init__(
        self,
        provider: str,
        api_key: str,
        model: str,
        base_url: str = "",
        timeout_seconds: float = 12.0,
    ) -> None:
        self.provider = provider.strip().lower() if provider else "generic"
        self.api_key = api_key
        self.model = model or "gemini-2.5-pro"
        self.base_url = self._resolve_base_url(base_url)
        self.timeout_seconds = timeout_seconds

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.model)

    def analyze_signal(
        self,
        snapshot: ListingSnapshot,
        history: list[dict[str, Any]],
        detection_rationale: str,
        default_action: str,
        evidence: list[EvidenceItem],
    ) -> LLMAnalysis | None:
        if not self.enabled or self.provider != "gemini":
            return None

        prompt = {
            "snapshot": snapshot.model_dump(mode="json"),
            "history": history[:5],
            "rule_detection_rationale": detection_rationale,
            "default_action": default_action,
            "evidence_candidates": [item.model_dump() for item in evidence],
            "instructions": [
                "You are a pricing strategy analyst.",
                "Use the product snapshot and history as primary truth.",
                "Treat evidence only as support.",
                "Ignore generic blogs and SEO content.",
            ],
        }
        schema = {
            "type": "object",
            "properties": {
                "rationale": {"type": "string"},
                "recommended_action": {"type": "string"},
                "relevant_evidence_indexes": {"type": "array", "items": {"type": "integer"}},
                "confidence_adjustment": {"type": "number"},
            },
            "required": [
                "rationale",
                "recommended_action",
                "relevant_evidence_indexes",
                "confidence_adjustment",
            ],
        }
        raw = self._call_json(prompt, schema)
        if raw is None:
            return None

        rationale = str(raw.get("rationale", "")).strip()
        recommended_action = str(raw.get("recommended_action", "")).strip()
        if not rationale or not recommended_action:
            return None

        indexes = self._sanitize_indexes(raw.get("relevant_evidence_indexes", []), len(evidence))
        confidence_adjustment = self._clamp_float(raw.get("confidence_adjustment", 0.0), -0.15, 0.15)
        return LLMAnalysis(
            rationale=rationale,
            recommended_action=recommended_action,
            relevant_evidence_indexes=indexes,
            confidence_adjustment=confidence_adjustment,
        )

    def match_same_product(self, query: str, offers: list[OfferView]) -> LLMSameProductMatch | None:
        if not self.enabled or self.provider != "gemini" or len(offers) < 2:
            return None

        prompt = {
            "query": query,
            "offers": [offer.model_dump(mode="json") for offer in offers],
            "instructions": [
                "Select only offers that refer to the exact same consumer-electronics product.",
                "Brand must match.",
                "Storage or capacity variants must match if present.",
                "Do not invent products; only choose from provided offers.",
                "Return at least two indexes only if you are confident.",
            ],
        }
        schema = {
            "type": "object",
            "properties": {
                "matched_indexes": {"type": "array", "items": {"type": "integer"}},
                "confidence": {"type": "number"},
                "rationale": {"type": "string"},
            },
            "required": ["matched_indexes", "confidence", "rationale"],
        }
        raw = self._call_json(prompt, schema)
        if raw is None:
            return None

        indexes = self._sanitize_indexes(raw.get("matched_indexes", []), len(offers))
        if len(indexes) < 2:
            return None
        return LLMSameProductMatch(
            matched_indexes=indexes,
            confidence=self._clamp_float(raw.get("confidence", 0.0), 0.0, 1.0),
            rationale=str(raw.get("rationale", "")).strip(),
        )

    def explain_pricing_comparison(
        self,
        query: str,
        offers: list[OfferView],
        draft_finding: dict[str, Any],
    ) -> LLMPricingNarrative | None:
        if not self.enabled or self.provider != "gemini" or len(offers) < 2:
            return None

        prompt = {
            "query": query,
            "offers": [offer.model_dump(mode="json") for offer in offers],
            "draft_finding": draft_finding,
            "instructions": [
                "Write a concise explanation of whether the public listed prices suggest suspicious differential pricing.",
                "Do not claim illegality.",
                "Mention that taxes, shipping, and hidden checkout adjustments are excluded.",
                "Use a neutral, analyst tone.",
            ],
        }
        schema = {
            "type": "object",
            "properties": {
                "reasoning": {"type": "string"},
                "claim_style_text": {"type": "string"},
                "confidence_adjustment": {"type": "number"},
            },
            "required": ["reasoning", "claim_style_text", "confidence_adjustment"],
        }
        raw = self._call_json(prompt, schema)
        if raw is None:
            return None

        reasoning = str(raw.get("reasoning", "")).strip()
        claim_style_text = str(raw.get("claim_style_text", "")).strip()
        if not reasoning or not claim_style_text:
            return None
        return LLMPricingNarrative(
            reasoning=reasoning,
            claim_style_text=claim_style_text,
            confidence_adjustment=self._clamp_float(raw.get("confidence_adjustment", 0.0), -0.1, 0.1),
        )

    def _call_json(self, prompt: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any] | None:
        try:
            response = httpx.post(
                f"{self.base_url}/models/{self.model}:generateContent",
                headers={
                    "x-goog-api-key": self.api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "contents": [
                        {
                            "role": "user",
                            "parts": [{"text": json.dumps(prompt, ensure_ascii=True)}],
                        }
                    ],
                    "generationConfig": {
                        "temperature": 0.1,
                        "responseMimeType": "application/json",
                        "responseJsonSchema": schema,
                    },
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            candidates = payload.get("candidates", [])
            first = candidates[0]
            parts = first["content"]["parts"]
            text = "".join(str(part.get("text", "")) for part in parts if "text" in part).strip()
            return json.loads(text)
        except Exception:
            return None

    def _resolve_base_url(self, configured_base_url: str) -> str:
        if configured_base_url:
            return configured_base_url.rstrip("/")
        if self.provider == "gemini":
            return "https://generativelanguage.googleapis.com/v1beta"
        return configured_base_url.rstrip("/")

    @staticmethod
    def _sanitize_indexes(items: list[Any], size: int) -> list[int]:
        indexes: list[int] = []
        for item in items:
            if isinstance(item, int) and 0 <= item < size and item not in indexes:
                indexes.append(item)
        return indexes

    @staticmethod
    def _clamp_float(value: Any, minimum: float, maximum: float) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            numeric = 0.0
        return max(minimum, min(maximum, numeric))
