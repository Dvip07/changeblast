"""Executes refunds against the card gateway exactly once per idempotency key."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# The acquirer only supports card reversals for charges younger than this. Older charges are
# refunded as account credit and need a manual payout by the finance team.
CARD_REVERSAL_MAX_AGE = timedelta(days=20)


@dataclass(frozen=True)
class RefundResult:
    refund_id: str
    method: str  # card_reversal | account_credit
    gateway_ref: str | None


class RefundProcessor:
    def __init__(self, gateway, store):
        self.gateway = gateway
        self.store = store  # idempotency store keyed by idempotency_key

    def process(self, refund_id: str, charge_id: str, amount_cents: int, currency: str,
                purchased_at: str, idempotency_key: str) -> RefundResult:
        existing = self.store.get(idempotency_key)
        if existing:
            return existing

        if amount_cents <= 0:
            raise ValueError("refund amount must be positive")

        age = datetime.now(timezone.utc) - datetime.fromisoformat(purchased_at)
        if age <= CARD_REVERSAL_MAX_AGE:
            ref = self.gateway.reverse(charge_id, amount_cents, currency, idempotency_key=idempotency_key)
            result = RefundResult(refund_id, "card_reversal", ref)
        else:
            self.store.queue_manual_payout(refund_id, amount_cents, currency)
            result = RefundResult(refund_id, "account_credit", None)

        self.store.put(idempotency_key, result)
        return result
