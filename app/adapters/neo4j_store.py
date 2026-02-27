from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError

from app.models import (
    ActionCard,
    CompareHistoryItem,
    CompareResponse,
    ListingSnapshot,
    ScrapeTarget,
    StrategySignal,
)


class Neo4jStore:
    def __init__(self, uri: str, user: str, password: str) -> None:
        self.enabled = bool(uri and user and password)
        self._driver = None
        if self.enabled:
            try:
                self._driver = GraphDatabase.driver(uri, auth=(user, password))
                self._driver.verify_connectivity()
            except Exception:
                self.enabled = False
                self._driver = None

        self._memory_snapshots: list[dict[str, Any]] = []
        self._memory_signals: list[dict[str, Any]] = []
        self._memory_actions: list[dict[str, Any]] = []
        self._memory_confidence: dict[str, float] = {}
        self._memory_compare_runs: list[dict[str, Any]] = []

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()

    def ensure_schema(self) -> None:
        if not self.enabled or self._driver is None:
            return

        queries = [
            "CREATE CONSTRAINT competitor_name IF NOT EXISTS FOR (c:Competitor) REQUIRE c.name IS UNIQUE",
            "CREATE CONSTRAINT sku_id IF NOT EXISTS FOR (s:SKU) REQUIRE s.id IS UNIQUE",
            "CREATE CONSTRAINT snapshot_id IF NOT EXISTS FOR (s:Snapshot) REQUIRE s.snapshot_id IS UNIQUE",
            "CREATE CONSTRAINT signal_id IF NOT EXISTS FOR (s:Signal) REQUIRE s.signal_id IS UNIQUE",
            "CREATE CONSTRAINT action_id IF NOT EXISTS FOR (a:Action) REQUIRE a.action_id IS UNIQUE",
            "CREATE CONSTRAINT search_query_key IF NOT EXISTS FOR (q:SearchQuery) REQUIRE q.normalized_query IS UNIQUE",
            "CREATE CONSTRAINT search_run_id IF NOT EXISTS FOR (r:SearchRun) REQUIRE r.compare_id IS UNIQUE",
            "CREATE CONSTRAINT product_cluster_id IF NOT EXISTS FOR (c:CompareProductCluster) REQUIRE c.cluster_id IS UNIQUE",
            "CREATE CONSTRAINT offer_observation_id IF NOT EXISTS FOR (o:OfferObservation) REQUIRE o.offer_id IS UNIQUE",
            "CREATE CONSTRAINT finding_id IF NOT EXISTS FOR (f:Finding) REQUIRE f.finding_id IS UNIQUE",
        ]
        try:
            with self._driver.session() as session:
                for query in queries:
                    session.run(query)
        except Neo4jError:
            self.enabled = False

    def seed_targets(self, targets: list[ScrapeTarget]) -> None:
        if self.enabled and self._driver is not None:
            with self._driver.session() as session:
                for target in targets:
                    session.run(
                        """
                        MERGE (c:Competitor {name: $competitor})
                        MERGE (k:SKU {id: $sku})
                        MERGE (c)-[:LISTS]->(k)
                        MERGE (r:Runbook {competitor: $competitor, sku: $sku, signal_type: "combined"})
                        ON CREATE SET r.confidence = 0.80, r.updated_at = datetime()
                        """,
                        competitor=target.competitor,
                        sku=target.sku,
                    )
            return

        for target in targets:
            key = self._confidence_key(target.competitor, target.sku, "combined")
            self._memory_confidence.setdefault(key, 0.80)

    def record_snapshot(self, snapshot: ListingSnapshot) -> None:
        data = snapshot.model_dump(mode="json")
        if self.enabled and self._driver is not None:
            with self._driver.session() as session:
                session.run(
                    """
                    MERGE (c:Competitor {name: $competitor})
                    MERGE (k:SKU {id: $sku})
                    MERGE (c)-[:LISTS]->(k)
                    CREATE (s:Snapshot {
                        snapshot_id: $snapshot_id,
                        source: $source,
                        url: $url,
                        captured_at: datetime($captured_at),
                        price: $price,
                        promo_text: $promo_text,
                        promo_score: $promo_score,
                        stock_flag: $stock_flag,
                        reference_price: $reference_price,
                        undercut: $undercut
                    })
                    MERGE (k)-[:HAS_SNAPSHOT]->(s)
                    """,
                    **data,
                )
            return
        self._memory_snapshots.append(data)

    def get_recent_snapshots(self, competitor: str, sku: str, limit: int = 7) -> list[dict[str, Any]]:
        if self.enabled and self._driver is not None:
            with self._driver.session() as session:
                result = session.run(
                    """
                    MATCH (c:Competitor {name: $competitor})-[:LISTS]->(k:SKU {id: $sku})-[:HAS_SNAPSHOT]->(s:Snapshot)
                    RETURN s.undercut AS undercut, s.promo_score AS promo_score, s.price AS price,
                           toString(s.captured_at) AS captured_at
                    ORDER BY s.captured_at DESC
                    LIMIT $limit
                    """,
                    competitor=competitor,
                    sku=sku,
                    limit=limit,
                )
                return [dict(record) for record in result]

        rows = [r for r in self._memory_snapshots if r["competitor"] == competitor and r["sku"] == sku]
        rows.sort(key=lambda item: item["captured_at"], reverse=True)
        return [
            {
                "undercut": row.get("undercut", False),
                "promo_score": row.get("promo_score", 0),
                "price": row.get("price", 0),
                "captured_at": row.get("captured_at"),
            }
            for row in rows[:limit]
        ]

    def get_recent_signal(
        self, competitor: str, sku: str, signal_type: str, hours: int = 24
    ) -> dict[str, Any] | None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        if self.enabled and self._driver is not None:
            with self._driver.session() as session:
                record = session.run(
                    """
                    MATCH (s:Signal {competitor: $competitor, sku: $sku, signal_type: $signal_type})
                    WHERE s.detected_at >= datetime($cutoff)
                    RETURN s.signal_id AS signal_id, s.confidence AS confidence
                    ORDER BY s.detected_at DESC
                    LIMIT 1
                    """,
                    competitor=competitor,
                    sku=sku,
                    signal_type=signal_type,
                    cutoff=cutoff.isoformat(),
                ).single()
                return dict(record) if record else None

        rows = [
            row
            for row in self._memory_signals
            if row["competitor"] == competitor
            and row["sku"] == sku
            and row["signal_type"] == signal_type
            and datetime.fromisoformat(str(row["detected_at"]).replace("Z", "+00:00")) >= cutoff
        ]
        if not rows:
            return None
        rows.sort(key=lambda item: item["detected_at"], reverse=True)
        return {"signal_id": rows[0]["signal_id"], "confidence": rows[0]["confidence"]}

    def update_strategy_confidence(
        self,
        competitor: str,
        sku: str,
        signal_type: str,
        delta: float,
        default_confidence: float,
    ) -> tuple[float, float]:
        key = self._confidence_key(competitor, sku, signal_type)
        if self.enabled and self._driver is not None:
            with self._driver.session() as session:
                record = session.run(
                    """
                    MERGE (r:Runbook {competitor: $competitor, sku: $sku, signal_type: $signal_type})
                    ON CREATE SET r.confidence = $default_confidence
                    WITH r, coalesce(r.confidence, $default_confidence) AS before
                    WITH r, before,
                        CASE
                            WHEN before + $delta > 1.0 THEN 1.0
                            WHEN before + $delta < 0.0 THEN 0.0
                            ELSE before + $delta
                        END AS after
                    SET r.confidence = after, r.updated_at = datetime($now)
                    RETURN before, after
                    """,
                    competitor=competitor,
                    sku=sku,
                    signal_type=signal_type,
                    default_confidence=default_confidence,
                    delta=delta,
                    now=datetime.now(timezone.utc).isoformat(),
                ).single()
                if record:
                    return float(record["before"]), float(record["after"])
                return default_confidence, default_confidence

        before = self._memory_confidence.get(key, default_confidence)
        after = min(1.0, max(0.0, before + delta))
        self._memory_confidence[key] = after
        return before, after

    def record_signal(self, signal: StrategySignal) -> None:
        payload = signal.model_dump(mode="json")
        if self.enabled and self._driver is not None:
            with self._driver.session() as session:
                session.run(
                    """
                    MERGE (c:Competitor {name: $competitor})
                    MERGE (k:SKU {id: $sku})
                    MERGE (c)-[:LISTS]->(k)
                    MERGE (s:Signal {signal_id: $signal_id})
                    SET s.competitor = $competitor,
                        s.sku = $sku,
                        s.signal_type = $signal_type,
                        s.severity = $severity,
                        s.confidence = $confidence,
                        s.rationale = $rationale,
                        s.recommended_action = $recommended_action,
                        s.detected_at = datetime($detected_at),
                        s.confidence_before = $confidence_before,
                        s.confidence_after = $confidence_after,
                        s.evidence_urls = $evidence_urls,
                        s.evidence_titles = $evidence_titles
                    MERGE (k)-[:TRIGGERED]->(s)
                    """,
                    signal_id=payload["signal_id"],
                    competitor=payload["competitor"],
                    sku=payload["sku"],
                    signal_type=payload["signal_type"],
                    severity=payload["severity"],
                    confidence=payload["confidence"],
                    rationale=payload["rationale"],
                    recommended_action=payload["recommended_action"],
                    detected_at=payload["detected_at"],
                    confidence_before=payload["confidence_before"],
                    confidence_after=payload["confidence_after"],
                    evidence_urls=[item.get("url", "") for item in payload["evidence"]],
                    evidence_titles=[item.get("title", "") for item in payload["evidence"]],
                )
            return
        self._memory_signals.append(payload)

    def record_action(self, action: ActionCard) -> None:
        payload = action.model_dump(mode="json")
        if self.enabled and self._driver is not None:
            with self._driver.session() as session:
                session.run(
                    """
                    MERGE (a:Action {action_id: $action_id})
                    SET a.channel = $channel,
                        a.message = $message,
                        a.posted = $posted,
                        a.posted_at = datetime($posted_at),
                        a.yutori_task_id = $yutori_task_id
                    WITH a
                    MATCH (s:Signal {signal_id: $signal_id})
                    MERGE (s)-[:RESPONDED_WITH]->(a)
                    """,
                    **payload,
                )
            return
        self._memory_actions.append(payload)

    def latest_signals(self, limit: int = 20) -> list[StrategySignal]:
        if self.enabled and self._driver is not None:
            with self._driver.session() as session:
                rows = session.run(
                    """
                    MATCH (s:Signal)
                    RETURN s.signal_id AS signal_id,
                           s.competitor AS competitor,
                           s.sku AS sku,
                           s.signal_type AS signal_type,
                           s.severity AS severity,
                           s.confidence AS confidence,
                           s.rationale AS rationale,
                           s.recommended_action AS recommended_action,
                           toString(s.detected_at) AS detected_at,
                           s.confidence_before AS confidence_before,
                           s.confidence_after AS confidence_after,
                           s.evidence_urls AS evidence_urls,
                           s.evidence_titles AS evidence_titles
                    ORDER BY s.detected_at DESC
                    LIMIT $limit
                    """,
                    limit=limit,
                )
                output: list[StrategySignal] = []
                for row in rows:
                    urls = row.get("evidence_urls") or []
                    titles = row.get("evidence_titles") or []
                    evidence = [
                        {"title": titles[idx] if idx < len(titles) else "Evidence", "url": url, "snippet": ""}
                        for idx, url in enumerate(urls)
                    ]
                    output.append(
                        StrategySignal.model_validate(
                            {
                                "signal_id": row["signal_id"],
                                "competitor": row["competitor"],
                                "sku": row["sku"],
                                "signal_type": row["signal_type"],
                                "severity": row["severity"],
                                "confidence": row["confidence"],
                                "evidence": evidence,
                                "recommended_action": row["recommended_action"],
                                "rationale": row["rationale"],
                                "detected_at": row["detected_at"],
                                "confidence_before": row["confidence_before"],
                                "confidence_after": row["confidence_after"],
                            }
                        )
                    )
                return output

        sorted_rows = sorted(self._memory_signals, key=lambda item: item["detected_at"], reverse=True)
        return [StrategySignal.model_validate(row) for row in sorted_rows[:limit]]

    def record_compare_response(self, response: CompareResponse) -> None:
        payload = response.model_dump(mode="json")
        history_row = {
            "compare_id": self._compare_id(payload),
            "query": payload["query"],
            "normalized_query": payload["normalized_query"],
            "generated_at": payload["generated_at"],
            "scan_status": payload["scan_status"],
            "offers_scanned": int(payload.get("offers_scanned", 0)),
            "offers_kept": int(payload.get("offers_kept", 0)),
            "cluster_offer_count": int((payload.get("comparison_cluster") or {}).get("offer_count", 0)),
            "finding_label": (payload.get("finding") or {}).get("label", "none"),
            "spread_percent": float((payload.get("finding") or {}).get("spread_percent", 0.0)),
            "top_domains": [str(item) for item in payload.get("sources_seen", [])[:8]],
            "top_offer_titles": [offer.get("title", "") for offer in payload.get("purchase_options", [])[:5] if offer.get("title")],
        }

        if self.enabled and self._driver is not None:
            with self._driver.session() as session:
                session.run(
                    """
                    MERGE (q:SearchQuery {normalized_query: $normalized_query})
                    SET q.last_query = $query, q.updated_at = datetime($generated_at)
                    MERGE (r:SearchRun {compare_id: $compare_id})
                    SET r.query = $query,
                        r.normalized_query = $normalized_query,
                        r.generated_at = datetime($generated_at),
                        r.scan_status = $scan_status,
                        r.offers_scanned = $offers_scanned,
                        r.offers_kept = $offers_kept,
                        r.cluster_offer_count = $cluster_offer_count,
                        r.finding_label = $finding_label,
                        r.spread_percent = $spread_percent,
                        r.top_domains = $top_domains,
                        r.top_offer_titles = $top_offer_titles
                    MERGE (q)-[:HAS_RUN]->(r)
                    """,
                    **history_row,
                )
            return

        self._memory_compare_runs.append(history_row)

    def get_compare_history(self, normalized_query: str, limit: int = 6) -> list[CompareHistoryItem]:
        if self.enabled and self._driver is not None:
            with self._driver.session() as session:
                count_row = session.run(
                    """
                    MATCH (r:SearchRun)
                    RETURN count(r) AS count
                    """
                ).single()
                if not count_row or int(count_row["count"]) == 0:
                    return []

                rows = session.run(
                    """
                    MATCH (r:SearchRun {normalized_query: $normalized_query})
                    RETURN r.compare_id AS compare_id,
                           r.query AS query,
                           r.normalized_query AS normalized_query,
                           toString(r.generated_at) AS generated_at,
                           coalesce(r.scan_status, 'insufficient') AS scan_status,
                           coalesce(r.offers_kept, 0) AS offers_kept,
                           coalesce(r.cluster_offer_count, 0) AS cluster_offer_count,
                           coalesce(r.finding_label, 'none') AS finding_label,
                           coalesce(r.spread_percent, 0.0) AS spread_percent,
                           coalesce(r.top_domains, []) AS top_domains
                    ORDER BY r.generated_at DESC
                    LIMIT $limit
                    """,
                    normalized_query=normalized_query,
                    limit=limit,
                )
                return [CompareHistoryItem.model_validate(dict(row)) for row in rows]

        rows = [row for row in self._memory_compare_runs if row["normalized_query"] == normalized_query]
        rows.sort(key=lambda item: item["generated_at"], reverse=True)
        return [CompareHistoryItem.model_validate(row) for row in rows[:limit]]

    @staticmethod
    def _confidence_key(competitor: str, sku: str, signal_type: str) -> str:
        return f"{competitor}::{sku}::{signal_type}"

    @staticmethod
    def _compare_id(payload: dict[str, Any]) -> str:
        cluster = payload.get("comparison_cluster") or {}
        cluster_id = cluster.get("cluster_id", "no-cluster")
        return f"{payload['normalized_query']}::{payload['generated_at']}::{cluster_id}"
