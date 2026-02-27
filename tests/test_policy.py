from datetime import datetime, timezone

from app.models import ListingSnapshot
from app.policy import StrategyPolicy


def build_snapshot(undercut: bool, promo_score: int) -> ListingSnapshot:
    return ListingSnapshot(
        snapshot_id="s1",
        competitor="CompetitorA",
        sku="SKU-ALPHA",
        source="fallback",
        url="https://example.com",
        captured_at=datetime.now(timezone.utc),
        price=90.0 if undercut else 101.0,
        promo_text="promo" if promo_score > 0 else "",
        promo_score=promo_score,
        stock_flag="in_stock",
        reference_price=100.0,
        undercut=undercut,
    )


def test_detect_combined_signal() -> None:
    policy = StrategyPolicy()
    history = [{"undercut": True, "promo_score": 1}]
    snapshot = build_snapshot(undercut=True, promo_score=3)
    result = policy.detect(snapshot, history)
    assert result is not None
    assert result.signal_type == "combined"
    assert result.severity == "high"


def test_no_signal_when_stable() -> None:
    policy = StrategyPolicy()
    history = [{"undercut": False, "promo_score": 1}]
    snapshot = build_snapshot(undercut=False, promo_score=1)
    result = policy.detect(snapshot, history)
    assert result is None
