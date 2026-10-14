"""Labeled yes/no test for the relevance gate: real fixture lines, scenario A."""
import asyncio, time
from changeblast import router, store

Q = ("A developer will implement this change: \"Extend the refund eligibility window from 14 to 30 days and keep "
     "customers who used over 50% of credits ineligible.\" Could this line of code, config, or text need to change "
     "or be affected by it? Lines that merely mention a related word in an unrelated sense are not affected.")
POS = [
    "billing-service/src/refunds/policy.py:7: REFUND_WINDOW_DAYS = 14",
    "billing-service/src/reports/refund_metrics.sql:6: avg(CASE WHEN r.approved_at <= s.purchased_at + INTERVAL '336 hours' THEN 1 ELSE 0 END) AS in_window_share",
    "checkout-web/src/components/RefundEligibility.tsx:5: const REFUND_WINDOW_MS = 1_209_600_000;",
    "notification-service/templates/refund_approved.html:8: Our refund policy covers subscriptions cancelled within two weeks of purchase.",
    "notification-service/templates/refund_denied.html:4: We couldn't approve your refund request because it was made after our 14-day refund window.",
    "billing-service/openapi.yaml:45: days_remaining: { type: integer, minimum: 0, maximum: 14 }",
    "payments-service/src/refunds/processor.py:7: CARD_REVERSAL_MAX_AGE = timedelta(days=20)",
    "billing-service/src/subscriptions/credits.py:5: def credit_usage_ratio(subscription: Subscription) -> float:",
    "billing-service/tests/test_refund_policy.py:20: decision = is_refund_eligible(make_sub(), PURCHASED + timedelta(days=13))",
]
NEG = [
    "notification-service/templates/refund_approved.html:6: <p>Card refunds usually appear on your statement within 5 to 10 business days.</p>",
    "payments-service/contracts/refund-request.schema.json:9: \"idempotency_key\": { \"type\": \"string\", \"maxLength\": 255 }",
    "billing-service/src/events/bus.py:10: self.producer.send(topic, json.dumps(payload).encode(\"utf-8\"))",
    "checkout-web/src/api/refunds.ts:9: if (!res.ok) throw new Error(`eligibility check failed: ${res.status}`);",
    "notification-service/src/mailer.py:6: self.client.post(\"/messages\", json={\"to\": to, \"subject\": subject, \"html\": html, \"dedupe_key\": dedupe_key})",
    "billing-service/src/subscriptions/models.py:9: plan: str",
    "payments-service/src/events/consumer.py:21: currency=event[\"currency\"],",
    "shared-contracts/README.md:8: - Adding an optional field is a minor change and keeps the same `$id`.",
    "notification-service/src/render.py:3: TEMPLATES = Path(__file__).parent.parent / \"templates\"",
]

async def run(model: str):
    items = POS + NEG
    t = time.monotonic()
    answers = await asyncio.gather(*(router._structured("bool", [model], router.YesNo, router.BOOL_SYSTEM,
        f"Question: {Q}\n\nMaterial:\n{m}", f"gate-eval-{time.time()}", 1500, False) for m in items), return_exceptions=True)
    secs = time.monotonic() - t
    tp = sum(1 for a in answers[:len(POS)] if not isinstance(a, Exception) and a.answer)
    fn = [POS[i].split(":")[0] for i, a in enumerate(answers[:len(POS)]) if not isinstance(a, Exception) and not a.answer]
    fp = sum(1 for a in answers[len(POS):] if not isinstance(a, Exception) and a.answer)
    err = sum(1 for a in answers if isinstance(a, Exception))
    return dict(model=model, recall=f"{tp}/{len(POS)}", false_pos=f"{fp}/{len(NEG)}", errors=err, seconds=round(secs, 1), missed=fn)

async def main():
    await store.pool.open()
    router.current_investigation.set("gate-eval")
    for model in ["typesafe/jev-router", "openai/gpt-5-nano"]:
        for rep in range(2):
            print(await run(model), flush=True)
    s = await router.usage_summary("gate-eval")
    for m in s["by_model"]:
        print(f"  {m['model']:24} calls={m['calls']} cost=${m['cost_usd']:.5f} per_call=${m['cost_usd']/max(m['calls'],1):.6f} avg={m['avg_latency_ms']}ms")
    await store.pool.close()

asyncio.run(main())
