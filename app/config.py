from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ValidationError
from dotenv import load_dotenv

from app.models import ScrapeTarget


ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data" / "snapshots"
load_dotenv(ROOT_DIR / ".env")

DEFAULT_RETAIL_DOMAINS = ["bestbuy.com", "microcenter.com", "amazon.com"]
DEFAULT_SAMPLE_QUERIES = [
    "sony wh-1000xm5",
    "ipad 10th generation 64gb",
    "logitech g pro x superlight 2",
]


class Settings(BaseModel):
    app_env: str = os.getenv("APP_ENV", "dev")

    neo4j_uri: str = os.getenv("NEO4J_URI", "")
    neo4j_user: str = os.getenv("NEO4J_USER", os.getenv("NEO4J_USERNAME", ""))
    neo4j_password: str = os.getenv("NEO4J_PASSWORD", "")
    neo4j_database: str = os.getenv("NEO4J_DATABASE", "")

    tavily_api_key: str = os.getenv("TAVILY_API_KEY", "")
    tavily_base_url: str = os.getenv("TAVILY_BASE_URL", "https://api.tavily.com")

    yutori_api_key: str = os.getenv("YUTORI_API_KEY", "")
    yutori_base_url: str = os.getenv("YUTORI_BASE_URL", "https://api.yutori.com")
    yutori_custom_recommend_url: str = os.getenv("YUTORI_RECOMMEND_URL", "")
    yutori_webhook_url: str = os.getenv("YUTORI_WEBHOOK_URL", "")

    llm_provider: str = os.getenv(
        "LLM_PROVIDER",
        "gemini" if os.getenv("GEMINI_API_KEY") or os.getenv("GEMINI_MODEL") or os.getenv("GEMINI_BASE_URL") else "generic",
    )
    llm_api_key: str = os.getenv(
        "LLM_API_KEY",
        os.getenv("OPENAI_API_KEY", os.getenv("GEMINI_API_KEY", "")),
    )
    llm_base_url: str = os.getenv(
        "LLM_BASE_URL",
        os.getenv("OPENAI_BASE_URL", os.getenv("GEMINI_BASE_URL", "")),
    )
    llm_model: str = os.getenv(
        "LLM_MODEL",
        os.getenv("OPENAI_MODEL", os.getenv("GEMINI_MODEL", "")),
    )

    slack_webhook_url: str = os.getenv("SLACK_WEBHOOK_URL", "")
    discord_webhook_url: str = os.getenv("DISCORD_WEBHOOK_URL", "")
    slack_high_channel: str = os.getenv("SLACK_HIGH_CHANNEL", "#pricing-incidents")
    slack_watch_channel: str = os.getenv("SLACK_WATCH_CHANNEL", "#pricing-watch")

    scrape_targets_json: str = os.getenv("SCRAPE_TARGETS_JSON", "")
    supported_retail_domains_json: str = os.getenv("SUPPORTED_RETAIL_DOMAINS_JSON", "")
    sample_queries_json: str = os.getenv("SAMPLE_QUERIES_JSON", "")
    http_timeout_seconds: float = float(os.getenv("HTTP_TIMEOUT_SECONDS", "8"))
    run_limit: int = int(os.getenv("RUN_LIMIT", "20"))
    discovery_results_per_domain: int = int(os.getenv("DISCOVERY_RESULTS_PER_DOMAIN", "5"))
    history_limit: int = int(os.getenv("HISTORY_LIMIT", "6"))

    @property
    def supported_retail_domains(self) -> list[str]:
        if self.supported_retail_domains_json:
            try:
                payload = json.loads(self.supported_retail_domains_json)
                if isinstance(payload, list):
                    domains = [str(item).strip().lower() for item in payload if str(item).strip()]
                    if domains:
                        return domains
            except json.JSONDecodeError:
                pass
        return DEFAULT_RETAIL_DOMAINS.copy()

    @property
    def sample_queries(self) -> list[str]:
        if self.sample_queries_json:
            try:
                payload = json.loads(self.sample_queries_json)
                if isinstance(payload, list):
                    queries = [str(item).strip() for item in payload if str(item).strip()]
                    if queries:
                        return queries
            except json.JSONDecodeError:
                pass
        return DEFAULT_SAMPLE_QUERIES.copy()

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

    @property
    def discord_enabled(self) -> bool:
        return bool(self.discord_webhook_url)

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key)


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
