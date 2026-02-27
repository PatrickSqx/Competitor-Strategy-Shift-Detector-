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


class HealthResponse(BaseModel):
    status: Literal["ok"]
    neo4j_enabled: bool
    tavily_enabled: bool
    yutori_enabled: bool
    slack_enabled: bool
