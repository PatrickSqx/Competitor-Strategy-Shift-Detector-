from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ScrapeTarget(BaseModel):
    competitor: str
    sku: str
    url: str
    reference_price: float = Field(gt=0)
    fallback_file: str
    price_selector: str | None = None
    promo_selector: str | None = None


class ListingSnapshot(BaseModel):
    snapshot_id: str
    competitor: str
    sku: str
    source: Literal["live", "fallback"]
    url: str
    captured_at: datetime
    price: float = Field(gt=0)
    promo_text: str = ""
    promo_score: int = 0
    stock_flag: Literal["in_stock", "low_stock", "out_of_stock"] = "in_stock"
    reference_price: float = Field(gt=0)
    undercut: bool = False


class EvidenceItem(BaseModel):
    title: str
    url: str
    snippet: str


class StrategySignal(BaseModel):
    signal_id: str
    competitor: str
    sku: str
    signal_type: Literal["undercut", "promo_intensity", "combined"]
    severity: Literal["medium", "high"]
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    recommended_action: str
    rationale: str
    detected_at: datetime
    confidence_before: float = Field(ge=0.0, le=1.0)
    confidence_after: float = Field(ge=0.0, le=1.0)


class ActionCard(BaseModel):
    action_id: str
    signal_id: str
    channel: str
    message: str
    posted: bool
    posted_at: datetime
    yutori_task_id: str | None = None


class RunOnceRequest(BaseModel):
    scenario: Literal["current", "shock"] = "current"


class RunOnceResponse(BaseModel):
    run_id: str
    generated_at: datetime
    scenario: Literal["current", "shock"]
    snapshots_count: int
    signals_count: int
    signals: list[StrategySignal]


class CompareRequest(BaseModel):
    query: str = Field(min_length=2, max_length=200)
    category: Literal["electronics"] = "electronics"


class DiscoveryCandidate(BaseModel):
    platform: str
    domain: str
    title: str = ""
    url: str
    snippet: str = ""
    score: float | None = None


class OfferView(BaseModel):
    platform: str
    title: str
    brand: str = ""
    model: str = ""
    price: float = Field(gt=0)
    currency: str = "USD"
    promo_text: str = ""
    availability: str = "unknown"
    url: str
    image: str = ""
    match_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source_domain: str = ""
    match_key: str = ""
    parse_notes: list[str] = Field(default_factory=list)


class ProductCluster(BaseModel):
    cluster_id: str
    brand: str
    model: str
    match_method: Literal["exact_model", "exact_sku", "fuzzy_llm"]
    confidence: float = Field(ge=0.0, le=1.0)
    platforms: list[str]
    offer_count: int = Field(ge=2)


class PricingFinding(BaseModel):
    label: Literal["none", "watch", "high", "critical"]
    spread_percent: float = Field(ge=0.0)
    lowest_platform: str
    highest_platform: str
    reasoning: str
    confidence: float = Field(ge=0.0, le=1.0)
    claim_style_text: str
    evidence_notes: str


class PlatformCoverage(BaseModel):
    platform: str
    status: Literal["found", "parsed", "matched", "missing", "error"]
    candidate_count: int = Field(default=0, ge=0)
    note: str = ""


class CompareResponse(BaseModel):
    query: str
    normalized_query: str
    generated_at: datetime
    coverage_status: Literal["full", "partial", "insufficient"]
    matched_cluster: ProductCluster | None = None
    offers: list[OfferView] = Field(default_factory=list)
    finding: PricingFinding | None = None
    warnings: list[str] = Field(default_factory=list)
    platform_statuses: list[PlatformCoverage] = Field(default_factory=list)


class CompareHistoryItem(BaseModel):
    compare_id: str
    query: str
    normalized_query: str
    generated_at: datetime
    coverage_status: Literal["full", "partial", "insufficient"]
    label: Literal["none", "watch", "high", "critical"]
    spread_percent: float = Field(ge=0.0)
    confidence: float = Field(ge=0.0, le=1.0)
    platforms: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: Literal["ok"]
    neo4j_enabled: bool
    tavily_enabled: bool
    yutori_enabled: bool
    slack_enabled: bool
    discord_enabled: bool
    llm_enabled: bool
    supported_platforms: list[str] = Field(default_factory=list)
