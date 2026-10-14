from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from src.refunds.processor import RefundProcessor


class MemoryStore(dict):
    def put(self, key, value):
        self[key] = value

    def queue_manual_payout(self, *args):
        self.setdefault("_payouts", []).append(args)


def recent(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def test_same_key_reverses_once():
    gateway = MagicMock()
    gateway.reverse.return_value = "rev_1"
    processor = RefundProcessor(gateway, MemoryStore())
    for _ in range(3):
        processor.process("rf_1", "ch_1", 500, "USD", recent(3), "refund:sub_1:ch_1")
    gateway.reverse.assert_called_once()


def test_old_charge_becomes_account_credit():
    gateway = MagicMock()
    store = MemoryStore()
    result = RefundProcessor(gateway, store).process("rf_2", "ch_2", 500, "USD", recent(25), "k2")
    assert result.method == "account_credit"
    gateway.reverse.assert_not_called()
