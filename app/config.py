from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ValidationError

from app.models import ScrapeTarget


ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data" / "snapshots"


class Settings(BaseModel):
    app_env: str = os.getenv("APP_ENV", "dev")

    neo4j_uri: str = os.getenv("NEO4J_URI", "")
    neo4j_user: str = os.getenv("NEO4J_USER", "")
    neo4j_password: str = os.getenv("NEO4J_PASSWORD", "")

    tavily_api_key: str = os.getenv("TAVILY_API_KEY", "")
    tavily_base_url: str = os.getenv("TAVILY_BASE_URL", "https://api.tavily.com")

    yutori_api_key: str = os.getenv("YUTORI_API_KEY", "")
    yutori_base_url: str = os.getenv("YUTORI_BASE_URL", "https://api.yutori.com")
    yutori_custom_recommend_url: str = os.getenv("YUTORI_RECOMMEND_URL", "")
    yutori_webhook_url: str = os.getenv("YUTORI_WEBHOOK_URL", "")

    slack_webhook_url: str = os.getenv("SLACK_WEBHOOK_URL", "")
    slack_high_channel: str = os.getenv("SLACK_HIGH_CHANNEL", "#pricing-incidents")
    slack_watch_channel: str = os.getenv("SLACK_WATCH_CHANNEL", "#pricing-watch")

    scrape_targets_json: str = os.getenv("SCRAPE_TARGETS_JSON", "")
    http_timeout_seconds: float = float(os.getenv("HTTP_TIMEOUT_SECONDS", "8"))
    run_limit: int = int(os.getenv("RUN_LIMIT", "20"))

    @property
    def scrape_targets(self) -> list[ScrapeTarget]:
        if self.scrape_targets_json:
            try:
                payload = json.loads(self.scrape_targets_json)
                return [ScrapeTarget.model_validate(item) for item in payload]
            except (json.JSONDecodeError, ValidationError):
                pass
        return default_targets()

    @property
    def neo4j_enabled(self) -> bool:
        return bool(self.neo4j_uri and self.neo4j_user and self.neo4j_password)

    @property
    def tavily_enabled(self) -> bool:
        return bool(self.tavily_api_key)

    @property
    def yutori_enabled(self) -> bool:
        return bool(self.yutori_api_key)

    @property
    def slack_enabled(self) -> bool:
        return bool(self.slack_webhook_url)


def default_targets() -> list[ScrapeTarget]:
    return [
        ScrapeTarget(
            competitor="CompetitorA",
            sku="SKU-ALPHA",
            url="https://example.com/catalog/alpha",
            reference_price=100.0,
            fallback_file=str(DATA_DIR / "competitor_a_alpha.json"),
        ),
        ScrapeTarget(
            competitor="CompetitorB",
            sku="SKU-BETA",
            url="https://example.com/catalog/beta",
            reference_price=70.0,
            fallback_file=str(DATA_DIR / "competitor_b_beta.json"),
        ),
    ]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
