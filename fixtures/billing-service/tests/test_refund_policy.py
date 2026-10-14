from datetime import datetime, timedelta, timezone

from src.refunds.policy import is_refund_eligible
from src.subscriptions.models import Subscription

PURCHASED = datetime(2026, 3, 1, tzinfo=timezone.utc)


def make_sub(**overrides) -> Subscription:
    base = dict(id="sub_1", customer_id="cus_1", plan="pro", status="active", purchased_at=PURCHASED,
                price_cents=12000, currency="USD", credits_granted=1000, credits_used=0, charge_id="ch_1")
    return Subscription(**{**base, **overrides})


def test_eligible_inside_window():
    decision = is_refund_eligible(make_sub(), PURCHASED + timedelta(days=13))
    assert decision.eligible and decision.days_remaining == 1


def test_ineligible_after_window():
    decision = is_refund_eligible(make_sub(), PURCHASED + timedelta(days=15))
    assert not decision.eligible and decision.reason == "outside_window"


def test_already_refunded():
    assert is_refund_eligible(make_sub(status="refunded"), PURCHASED).reason == "already_refunded"
