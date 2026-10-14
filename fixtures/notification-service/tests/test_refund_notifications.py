import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.consumers.refund_events import handle_refund_approved


def test_amount_formatted_from_cents():
    mailer = MagicMock()
    customers = {"cus_1": SimpleNamespace(first_name="Ada", email="ada@example.com")}
    event = {"customer_id": "cus_1", "refund_amount_cents": 123456, "currency": "USD",
             "reason": "not_needed", "idempotency_key": "refund:sub_1:ch_1"}
    handle_refund_approved(json.dumps(event).encode(), mailer, customers)
    assert "1,234.56 USD" in mailer.send.call_args.kwargs["html"]
