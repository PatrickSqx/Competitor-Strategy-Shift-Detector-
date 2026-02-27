from __future__ import annotations

from fastapi import Body, FastAPI, Query

from app.adapters.neo4j_store import Neo4jStore
from app.adapters.scraper import WebScraper
from app.adapters.slack_client import SlackClient
from app.adapters.tavily_client import TavilyClient
from app.adapters.yutori_client import YutoriClient
from app.config import get_settings
from app.models import HealthResponse, RunOnceRequest, RunOnceResponse, StrategySignal
from app.orchestrator import StrategyOrchestrator
from app.policy import StrategyPolicy


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
slack = SlackClient(
    webhook_url=settings.slack_webhook_url,
    timeout_seconds=settings.http_timeout_seconds,
)
policy = StrategyPolicy()
orchestrator = StrategyOrchestrator(settings, store, scraper, tavily, yutori, slack, policy)

app = FastAPI(title="Competitor Strategy Shift Detector", version="0.1.0")


@app.on_event("startup")
def startup() -> None:
    store.ensure_schema()
    store.seed_targets(settings.scrape_targets)


@app.on_event("shutdown")
def shutdown() -> None:
    store.close()


@app.get("/healthz", response_model=HealthResponse)
def healthz() -> HealthResponse:
    return HealthResponse(
        status="ok",
        neo4j_enabled=store.enabled,
        tavily_enabled=tavily.enabled,
        yutori_enabled=yutori.enabled,
        slack_enabled=slack.enabled,
    )


@app.post("/run-once", response_model=RunOnceResponse)
def run_once(request: RunOnceRequest = Body(default_factory=RunOnceRequest)) -> RunOnceResponse:
    return orchestrator.run_once(request)


@app.post("/webhooks/scheduler", response_model=RunOnceResponse)
def scheduler(request: RunOnceRequest = Body(default_factory=RunOnceRequest)) -> RunOnceResponse:
    return orchestrator.run_once(request)


@app.get("/signals/latest", response_model=list[StrategySignal])
def latest_signals(limit: int = Query(default=20, ge=1, le=100)) -> list[StrategySignal]:
    return orchestrator.latest_signals(limit)
