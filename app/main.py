from __future__ import annotations

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
    PlatformCoverage,
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
)
product_extractor = ProductExtractorService(timeout_seconds=settings.http_timeout_seconds)
product_matcher = ProductMatcherService(llm=llm)
differential_pricing = DifferentialPricingService(llm=llm)
history_service = HistoryService(store=store)

app = FastAPI(title="Pricing Comparison Intelligence", version="0.2.0")
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
    sample_buttons = "\n".join(
        f'<button type="button" class="sample-chip" data-query="{html_escape(sample)}">{html_escape(sample)}</button>'
        for sample in settings.sample_queries
    )
    status_cards = "\n".join(
        (
            f'<article class="status-card pending" data-platform="{html_escape(platform)}">'
            f'<span class="status-platform">{html_escape(platform)}</span>'
            '<span class="status-label">Waiting</span>'
            "</article>"
        )
        for platform in ["Best Buy", "Walmart", "Target"]
    )
    page = INDEX_TEMPLATE.replace("__SAMPLE_CHIPS__", sample_buttons).replace("__STATUS_CARDS__", status_cards)
    return HTMLResponse(page)


@app.get("/healthz", response_model=HealthResponse)
def healthz() -> HealthResponse:
    return HealthResponse(
        status="ok",
        neo4j_enabled=store.enabled,
        tavily_enabled=tavily.enabled,
        yutori_enabled=yutori.enabled,
        slack_enabled=settings.slack_enabled,
        discord_enabled=settings.discord_enabled,
        llm_enabled=settings.llm_enabled,
        supported_platforms=["Best Buy", "Walmart", "Target"],
    )


@app.post("/api/compare", response_model=CompareResponse)
def compare(request: CompareRequest) -> CompareResponse:
    normalized_query, candidates, platform_statuses, warnings = query_discovery.discover(request.query)
    offers, extract_warnings = product_extractor.extract_many(candidates)
    warnings.extend(extract_warnings)

    matched_cluster = None
    selected_offers = offers
    finding = None

    if offers:
        matched_cluster, selected_offers, match_warnings = product_matcher.match(normalized_query, offers)
        warnings.extend(match_warnings)

        if matched_cluster is not None:
            coverage_status = _coverage_status(selected_offers)
            finding = differential_pricing.analyze(
                query=normalized_query,
                matched_offers=selected_offers,
                cluster=matched_cluster,
                coverage_status=coverage_status,
            )
            platform_statuses = _merge_platform_statuses(platform_statuses, selected_offers, matched=True)
        else:
            coverage_status = _coverage_status(offers)
            platform_statuses = _merge_platform_statuses(platform_statuses, offers, matched=False)
    else:
        coverage_status = "insufficient"

    if not offers:
        warnings.append("No priced product pages could be extracted from the discovered candidates.")
    if matched_cluster is None and offers:
        warnings.append("No confident same-product cluster was found across the supported platforms.")

    response = CompareResponse(
        query=request.query,
        normalized_query=normalized_query,
        generated_at=_utc_now(),
        coverage_status=coverage_status,
        matched_cluster=matched_cluster,
        offers=selected_offers,
        finding=finding,
        warnings=dedupe(warnings),
        platform_statuses=platform_statuses,
    )
    history_service.record_compare_response(response)
    return response


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


def _coverage_status(offers) -> str:
    platforms = {offer.platform for offer in offers}
    if len(platforms) >= 3:
        return "full"
    if len(platforms) >= 2:
        return "partial"
    return "insufficient"


def _merge_platform_statuses(
    existing: list[PlatformCoverage],
    offers,
    matched: bool,
) -> list[PlatformCoverage]:
    offer_map: dict[str, int] = {}
    for offer in offers:
        offer_map[offer.platform] = offer_map.get(offer.platform, 0) + 1

    merged: list[PlatformCoverage] = []
    for status in existing:
        if status.platform in offer_map:
            merged.append(
                status.model_copy(
                    update={
                        "status": "matched" if matched else "parsed",
                        "candidate_count": max(status.candidate_count, offer_map[status.platform]),
                        "note": (
                            "Matched offer included in comparison."
                            if matched
                            else "Parsed priced product page, but no confident same-product match yet."
                        ),
                    }
                )
            )
        else:
            merged.append(status)
    return merged


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
