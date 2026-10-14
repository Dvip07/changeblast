from dataclasses import dataclass
from datetime import datetime


@dataclass
class Subscription:
    id: str
    customer_id: str
    plan: str
    status: str  # active | cancelled | refunded | chargeback
    purchased_at: datetime
    price_cents: int
    currency: str
    credits_granted: int
    credits_used: int
    charge_id: str
