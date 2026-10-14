"""Consumes approved refunds from billing and hands them to the processor."""
import json
import logging

from src.refunds.processor import RefundProcessor

log = logging.getLogger(__name__)

TOPIC = "billing.refund.approved"


class RefundEventConsumer:
    def __init__(self, processor: RefundProcessor, validator):
        self.processor = processor
        self.validator = validator  # validates against shared-contracts RefundApproved v1

    def handle(self, raw: bytes) -> None:
        event = json.loads(raw)
        self.validator.validate(TOPIC, event)
        result = self.processor.process(
            refund_id=event["refund_id"],
            charge_id=event["charge_id"],
            amount_cents=event["refund_amount_cents"],
            currency=event["currency"],
            purchased_at=event["purchased_at"],
            idempotency_key=event["idempotency_key"],
        )
        log.info("refund %s processed via %s", event["refund_id"], result.method)
