"""Emails customers when billing approves a refund."""
import json

from src.mailer import Mailer
from src.render import render_template

TOPIC = "billing.refund.approved"

REASON_COPY = {
    "not_needed": "you no longer needed the plan",
    "too_expensive": "the plan did not fit your budget",
    "missing_feature": "a feature you needed was missing",
    "other": "the reason you gave us",
}


def handle_refund_approved(raw: bytes, mailer: Mailer, customers) -> None:
    event = json.loads(raw)
    customer = customers.get(event["customer_id"])
    amount = event["refund_amount_cents"] / 100
    html = render_template(
        "refund_approved.html",
        name=customer.first_name,
        amount=f"{amount:,.2f} {event['currency']}",
        reason=REASON_COPY.get(event["reason"], REASON_COPY["other"]),
    )
    mailer.send(to=customer.email, subject="Your refund is on its way", html=html,
                dedupe_key=event["idempotency_key"])
