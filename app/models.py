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
    domain: str
    title: str = ""
    url: str
    snippet: str = ""
    score: float | None = None
    source: Literal["tavily", "site_search"] = "tavily"
    preview_price: float | None = None
    preview_currency: str = "USD"
    preview_availability: str = ""
    preview_condition: Literal["new", "unknown", "used", "refurbished", "open_box"] = "unknown"


class PurchaseOption(BaseModel):
    offer_id: str
    seller_name: str
    source_domain: str
    title: str
    brand: str = ""
    model: str = ""
    variant: str = ""
    price: float = Field(gt=0)
    currency: str = "USD"
    condition: Literal["new", "unknown", "used", "refurbished", "open_box"] = "unknown"
    promo_text: str = ""
    availability: str = "unknown"
    url: str
    image: str = ""
    relevance_score: float = Field(default=0.0, ge=0.0, le=1.0)
    match_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    parse_notes: list[str] = Field(default_factory=list)


class ComparisonCluster(BaseModel):
    cluster_id: str
    brand: str
    model: str
    variant: str = ""
    match_method: Literal["exact_model", "exact_sku", "fuzzy_llm"]
    confidence: float = Field(ge=0.0, le=1.0)
    offer_count: int = Field(ge=2)
    domains: list[str] = Field(default_factory=list)
    offers: list[PurchaseOption] = Field(default_factory=list)


class PricingFinding(BaseModel):
    label: Literal["none", "watch", "high", "critical"]
    alert_eligible: bool = False
    spread_percent: float = Field(ge=0.0)
    lowest_offer_id: str = ""
    highest_offer_id: str = ""
    reasoning: str
    confidence: float = Field(ge=0.0, le=1.0)
    claim_style_text: str
    evidence_notes: str


class CompareResponse(BaseModel):
    query: str
    normalized_query: str
    generated_at: datetime
    scan_status: Literal["full", "partial", "insufficient", "degraded"]
    scan_duration_ms: int = Field(ge=0)
    offers_scanned: int = Field(ge=0)
    offers_kept: int = Field(ge=0)
    sources_seen: list[str] = Field(default_factory=list)
    purchase_options: list[PurchaseOption] = Field(default_factory=list)
    comparison_cluster: ComparisonCluster | None = None
    finding: PricingFinding | None = None
    warnings: list[str] = Field(default_factory=list)


class CompareHistoryItem(BaseModel):
    compare_id: str
    query: str
    normalized_query: str
    generated_at: datetime
    scan_status: Literal["full", "partial", "insufficient", "degraded"]
    offers_kept: int = Field(ge=0)
    cluster_offer_count: int = Field(ge=0)
    finding_label: Literal["none", "watch", "high", "critical"]
    spread_percent: float = Field(ge=0.0)
    top_domains: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: Literal["ok"]
    neo4j_enabled: bool
    tavily_enabled: bool
    llm_enabled: bool
    site_search_fallback_enabled: bool
    open_web_mode: bool


OfferView = PurchaseOption
ProductCluster = ComparisonCluster
