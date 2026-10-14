"""Refund eligibility rules for subscription purchases."""
from dataclasses import dataclass
from datetime import datetime, timedelta

from src.subscriptions.models import Subscription

REFUND_WINDOW_DAYS = 14

INELIGIBLE_STATUSES = {"refunded", "chargeback"}


@dataclass(frozen=True)
class RefundDecision:
    eligible: bool
    reason: str
    days_remaining: int


def is_refund_eligible(subscription: Subscription, requested_at: datetime) -> RefundDecision:
    """Decide whether a subscription purchase can be refunded at `requested_at`."""
    if subscription.status in INELIGIBLE_STATUSES:
        return RefundDecision(False, "already_refunded", 0)

    age = requested_at - subscription.purchased_at
    window = timedelta(days=REFUND_WINDOW_DAYS)
    if age > window:
        return RefundDecision(False, "outside_window", 0)

    days_remaining = (window - age).days
    return RefundDecision(True, "eligible", days_remaining)
