from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.adapters.neo4j_store import Neo4jStore
from app.adapters.scraper import WebScraper
from app.adapters.slack_client import SlackClient
from app.adapters.tavily_client import TavilyClient
from app.adapters.yutori_client import YutoriClient
from app.config import Settings
from app.models import ActionCard, RunOnceRequest, RunOnceResponse, StrategySignal
from app.policy import StrategyPolicy


class StrategyOrchestrator:
    def __init__(
        self,
        settings: Settings,
        store: Neo4jStore,
        scraper: WebScraper,
        tavily: TavilyClient,
        yutori: YutoriClient,
        slack: SlackClient,
        policy: StrategyPolicy,
    ) -> None:
        self.settings = settings
        self.store = store
        self.scraper = scraper
        self.tavily = tavily
        self.yutori = yutori
        self.slack = slack
        self.policy = policy

    def run_once(self, request: RunOnceRequest) -> RunOnceResponse:
        run_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        snapshots = self.scraper.fetch_many(self.settings.scrape_targets, scenario=request.scenario)
        signals: list[StrategySignal] = []

        for snapshot in snapshots:
            history = self.store.get_recent_snapshots(snapshot.competitor, snapshot.sku, limit=self.settings.run_limit)
            detection = self.policy.detect(snapshot, history)
            self.store.record_snapshot(snapshot)
            if detection is None:
                continue

            query = (
                f"{snapshot.competitor} {snapshot.sku} pricing discount strategy shift "
                f"promo campaign competitor intelligence"
            )
            evidence = self.tavily.search_evidence(query=query, max_results=2)
            prior_signal = self.store.get_recent_signal(
                competitor=snapshot.competitor,
                sku=snapshot.sku,
                signal_type=detection.signal_type,
                hours=24,
            )
            learning_delta = 0.1 if prior_signal else 0.0

            confidence_before, confidence_after = self.store.update_strategy_confidence(
                competitor=snapshot.competitor,
                sku=snapshot.sku,
                signal_type=detection.signal_type,
                delta=learning_delta,
                default_confidence=detection.base_confidence,
            )
            final_confidence = min(1.0, max(0.0, detection.base_confidence + (confidence_after - confidence_before)))

            signal = StrategySignal(
                signal_id=str(uuid.uuid4()),
                competitor=snapshot.competitor,
                sku=snapshot.sku,
                signal_type=detection.signal_type,  # type: ignore[arg-type]
                severity=detection.severity,  # type: ignore[arg-type]
                confidence=final_confidence,
                evidence=evidence,
                recommended_action=self.policy.recommendation_for(detection.signal_type),
                rationale=detection.rationale,
                detected_at=now,
                confidence_before=confidence_before,
                confidence_after=confidence_after,
            )

            action_message = self.yutori.recommend_action(signal, evidence)
            yutori_task_id = self.yutori.create_scout_task(signal) if signal.severity == "high" else None
            channel = (
                self.settings.slack_high_channel if signal.severity == "high" else self.settings.slack_watch_channel
            )
            posted = self.slack.post_strategy_alert(signal, action_message, channel, evidence)

            action = ActionCard(
                action_id=str(uuid.uuid4()),
                signal_id=signal.signal_id,
                channel=channel,
                message=action_message,
                posted=posted,
                posted_at=now,
                yutori_task_id=yutori_task_id,
            )
            self.store.record_signal(signal)
            self.store.record_action(action)
            signals.append(signal)

        return RunOnceResponse(
            run_id=run_id,
            generated_at=now,
            scenario=request.scenario,
            snapshots_count=len(snapshots),
            signals_count=len(signals),
            signals=signals,
        )

    def latest_signals(self, limit: int = 20) -> list[StrategySignal]:
        return self.store.latest_signals(limit=limit)
