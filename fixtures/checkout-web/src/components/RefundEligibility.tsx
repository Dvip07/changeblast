import { useEffect, useState } from "react";
import { fetchRefundEligibility, requestRefund, type RefundEligibility as Eligibility } from "../api/refunds";

// Skip the network call for purchases that are obviously too old to qualify.
const REFUND_WINDOW_MS = 1_209_600_000;

type Props = { subscriptionId: string; purchasedAt: string };

export function RefundEligibility({ subscriptionId, purchasedAt }: Props) {
  const tooOld = Date.now() - new Date(purchasedAt).getTime() > REFUND_WINDOW_MS;
  const [eligibility, setEligibility] = useState<Eligibility | null>(null);

  useEffect(() => {
    if (!tooOld) fetchRefundEligibility(subscriptionId).then(setEligibility);
  }, [subscriptionId, tooOld]);

  if (tooOld || eligibility?.reason === "outside_window") {
    return <p className="muted">This purchase is no longer eligible for a refund.</p>;
  }
  if (!eligibility) return null;
  if (!eligibility.eligible) return <p className="muted">This purchase has already been refunded.</p>;

  return (
    <div>
      <p>
        You can request a full refund for {eligibility.days_remaining} more{" "}
        {eligibility.days_remaining === 1 ? "day" : "days"}.
      </p>
      <button onClick={() => requestRefund(subscriptionId, "not_needed")}>Request refund</button>
    </div>
  );
}
