from __future__ import annotations

import time

from fastapi import Body, FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.adapters.llm_client import LLMClient
from app.adapters.neo4j_store import Neo4jStore
from app.adapters.scraper import WebScraper
from app.adapters.slack_client import SlackClient
from app.adapters.tavily_client import TavilyClient
from app.adapters.yutori_client import YutoriClient
from app.config import ROOT_DIR, get_settings
from app.models import (
    CompareHistoryItem,
    CompareRequest,
    CompareResponse,
    HealthResponse,
    PurchaseOption,
    RunOnceRequest,
    RunOnceResponse,
    StrategySignal,
)
from app.orchestrator import StrategyOrchestrator
from app.policy import StrategyPolicy
from app.services.differential_pricing import DifferentialPricingService
from app.services.history_service import HistoryService
from app.services.product_extractor import ProductExtractorService
from app.services.product_matcher import ProductMatcherService
from app.services.query_discovery import QueryDiscoveryService
from app.services.relevance_ranker import RelevanceRanker


settings = get_settings()
store = Neo4jStore(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
scraper = WebScraper(timeout_seconds=settings.http_timeout_seconds)
tavily = TavilyClient(
    api_key=settings.tavily_api_key,
    base_url=settings.tavily_base_url,
    timeout_seconds=settings.http_timeout_seconds,
)
yutori = YutoriClient(
    api_key=settings.yutori_api_key,
    base_url=settings.yutori_base_url,
    timeout_seconds=settings.http_timeout_seconds,
    webhook_url=settings.yutori_webhook_url,
    custom_recommend_url=settings.yutori_custom_recommend_url,
)
llm = LLMClient(
    provider=settings.llm_provider,
    api_key=settings.llm_api_key,
    model=settings.llm_model,
    base_url=settings.llm_base_url,
    timeout_seconds=settings.http_timeout_seconds,
)
slack = SlackClient(
    webhook_url=settings.slack_webhook_url,
    timeout_seconds=settings.http_timeout_seconds,
    discord_webhook_url=settings.discord_webhook_url,
)
policy = StrategyPolicy()
orchestrator = StrategyOrchestrator(settings, store, scraper, tavily, yutori, llm, slack, policy)
query_discovery = QueryDiscoveryService(
    tavily=tavily,
    supported_domains=settings.supported_retail_domains,
    max_results_per_domain=settings.discovery_results_per_domain,
    timeout_seconds=settings.discovery_timeout_seconds,
)
product_extractor = ProductExtractorService(
    timeout_seconds=settings.extraction_timeout_seconds,
    max_workers=settings.compare_max_workers,
    max_candidates_per_platform=settings.extraction_candidates_per_platform,
)
relevance_ranker = RelevanceRanker()
product_matcher = ProductMatcherService(llm=llm)
differential_pricing = DifferentialPricingService(llm=llm)
history_service = HistoryService(store=store)

app = FastAPI(title="Pricing Comparison Intelligence", version="0.3.0")
app.mount("/static", StaticFiles(directory=ROOT_DIR / "static"), name="static")
INDEX_TEMPLATE = (ROOT_DIR / "templates" / "index.html").read_text(encoding="utf-8")


@app.on_event("startup")
def startup() -> None:
    store.ensure_schema()
    store.seed_targets(settings.scrape_targets)


@app.on_event("shutdown")
def shutdown() -> None:
    store.close()


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    del request
    sample_buttons = "\n".join(
        f'<button type="button" class="sample-chip" data-query="{html_escape(sample)}">{html_escape(sample)}</button>'
        for sample in settings.sample_queries
    )
    page = INDEX_TEMPLATE.replace("__SAMPLE_CHIPS__", sample_buttons)
    return HTMLResponse(page)


@app.get("/healthz", response_model=HealthResponse)
def healthz() -> HealthResponse:
    return HealthResponse(
        status="ok",
        neo4j_enabled=store.enabled,
        tavily_enabled=tavily.enabled,
        llm_enabled=settings.llm_enabled,
        site_search_fallback_enabled=True,
        open_web_mode=True,
    )


@app.post("/api/compare", response_model=CompareResponse)
def compare(request: CompareRequest) -> CompareResponse:
    started = time.perf_counter()
    try:
        discovery = query_discovery.discover(request.query)
        extracted_offers, extract_warnings = product_extractor.extract_many(discovery.candidates)
        ranked_offers = relevance_ranker.rank(discovery.normalized_query, extracted_offers)
        if len(ranked_offers) >= 2:
            cluster, matched_offers, match_warnings = product_matcher.match(discovery.normalized_query, ranked_offers)
        else:
            cluster, matched_offers, match_warnings = None, ranked_offers, []
        purchase_options = _merge_match_confidence(ranked_offers, matched_offers)
        finding = differential_pricing.analyze(discovery.normalized_query, cluster) if cluster is not None else None

        warnings = list(discovery.warnings)
        warnings.extend(extract_warnings)
        warnings.extend(match_warnings)
        filtered_count = len(extracted_offers) - len(ranked_offers)
        if filtered_count > 0:
            warnings.append(f"Filtered out {filtered_count} low-relevance or non-new purchase options.")

        response = CompareResponse(
            query=request.query,
            normalized_query=discovery.normalized_query,
            generated_at=_utc_now(),
            scan_status=_scan_status(discovery.degraded, purchase_options, cluster),
            scan_duration_ms=int((time.perf_counter() - started) * 1000),
            offers_scanned=len(discovery.candidates),
            offers_kept=len(purchase_options),
            sources_seen=discovery.sources_seen,
            purchase_options=purchase_options,
            comparison_cluster=cluster,
            finding=finding,
            warnings=dedupe(warnings),
        )
        history_service.record_compare_response(response)
        return response
    except Exception as exc:
        return CompareResponse(
            query=request.query,
            normalized_query=QueryDiscoveryService.normalize_query(request.query),
            generated_at=_utc_now(),
            scan_status="degraded",
            scan_duration_ms=int((time.perf_counter() - started) * 1000),
            offers_scanned=0,
            offers_kept=0,
            sources_seen=[],
            purchase_options=[],
            comparison_cluster=None,
            finding=None,
            warnings=[
                f"Backend compare failed: {exc.__class__.__name__}.",
                "Check Tavily, Gemini, Neo4j, and page-extraction settings.",
            ],
        )


@app.get("/api/history", response_model=list[CompareHistoryItem])
def history(query: str = Query(..., min_length=2), limit: int = Query(default=settings.history_limit, ge=1, le=20)) -> list[CompareHistoryItem]:
    return history_service.get_history(query, limit=limit)


@app.post("/run-once", response_model=RunOnceResponse)
def run_once(request: RunOnceRequest = Body(default_factory=RunOnceRequest)) -> RunOnceResponse:
    return orchestrator.run_once(request)


@app.post("/webhooks/scheduler", response_model=RunOnceResponse)
def scheduler(request: RunOnceRequest = Body(default_factory=RunOnceRequest)) -> RunOnceResponse:
    return orchestrator.run_once(request)


@app.get("/signals/latest", response_model=list[StrategySignal])
def latest_signals(limit: int = Query(default=20, ge=1, le=100)) -> list[StrategySignal]:
    return orchestrator.latest_signals(limit)


def _merge_match_confidence(ranked_offers: list[PurchaseOption], matched_offers: list[PurchaseOption]) -> list[PurchaseOption]:
    matched_by_id = {offer.offer_id: offer for offer in matched_offers}
    merged: list[PurchaseOption] = []
    for offer in ranked_offers:
        merged.append(matched_by_id.get(offer.offer_id, offer))
    return merged


def _scan_status(degraded: bool, purchase_options: list[PurchaseOption], cluster) -> str:
    if degraded and purchase_options:
        return "degraded"
    if degraded and not purchase_options:
        return "degraded"
    if not purchase_options:
        return "insufficient"
    if cluster is not None and cluster.offer_count >= 3 and len(purchase_options) >= 4:
        return "full"
    return "partial"


def dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            output.append(item)
    return output


def _utc_now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def html_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )
