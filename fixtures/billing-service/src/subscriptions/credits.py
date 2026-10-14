"""Usage-credit accounting for subscription plans."""
from src.subscriptions.models import Subscription


def credit_usage_ratio(subscription: Subscription) -> float:
    """Fraction of granted credits consumed so far, between 0.0 and 1.0."""
    if subscription.credits_granted <= 0:
        return 0.0
    return min(subscription.credits_used / subscription.credits_granted, 1.0)


def prorated_refund_cents(subscription: Subscription) -> int:
    """Refund the unused share of the subscription price, rounded down to the cent."""
    unused = 1.0 - credit_usage_ratio(subscription)
    return int(subscription.price_cents * unused)
