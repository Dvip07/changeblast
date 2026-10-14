"""Refund request handling: decide, price, and announce approved refunds."""
import uuid
from datetime import datetime, timezone

from src.events.bus import EventBus
from src.refunds.policy import is_refund_eligible
from src.subscriptions.credits import prorated_refund_cents
from src.subscriptions.models import Subscription

REFUND_APPROVED_TOPIC = "billing.refund.approved"


class RefundRejected(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class RefundService:
    def __init__(self, bus: EventBus, repo):
        self.bus = bus
        self.repo = repo

    def request_refund(self, subscription: Subscription, reason: str) -> dict:
        now = datetime.now(timezone.utc)
        decision = is_refund_eligible(subscription, now)
        if not decision.eligible:
            raise RefundRejected(decision.reason)

        amount_cents = prorated_refund_cents(subscription)
        refund_id = f"rf_{uuid.uuid4().hex[:12]}"
        event = {
            "refund_id": refund_id,
            "subscription_id": subscription.id,
            "customer_id": subscription.customer_id,
            "charge_id": subscription.charge_id,
            "refund_amount_cents": amount_cents,
            "currency": subscription.currency,
            "reason": reason,
            "purchased_at": subscription.purchased_at.isoformat(),
            "approved_at": now.isoformat(),
            "idempotency_key": f"refund:{subscription.id}:{subscription.charge_id}",
        }
        self.repo.mark_refund_pending(subscription.id, refund_id)
        self.bus.publish(REFUND_APPROVED_TOPIC, event)
        return event
