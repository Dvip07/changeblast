export type RefundEligibility = {
  eligible: boolean;
  reason: "eligible" | "outside_window" | "already_refunded";
  days_remaining: number;
};

export async function fetchRefundEligibility(subscriptionId: string): Promise<RefundEligibility> {
  const res = await fetch(`/billing/subscriptions/${subscriptionId}/refund-eligibility`);
  if (!res.ok) throw new Error(`eligibility check failed: ${res.status}`);
  return res.json();
}

export async function requestRefund(subscriptionId: string, reason: string): Promise<void> {
  const res = await fetch(`/billing/refunds`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ subscription_id: subscriptionId, reason }),
  });
  if (res.status === 409) throw new Error("not_eligible");
  if (!res.ok) throw new Error(`refund request failed: ${res.status}`);
}
