-- Weekly finance report: refunds issued and the share requested inside the policy window.
SELECT
  date_trunc('week', r.approved_at) AS week,
  count(*) AS refunds,
  sum(r.amount_cents) / 100.0 AS refunded_amount,
  avg(CASE WHEN r.approved_at <= s.purchased_at + INTERVAL '336 hours' THEN 1 ELSE 0 END) AS in_window_share
FROM refunds r
JOIN subscriptions s ON s.id = r.subscription_id
GROUP BY 1
ORDER BY 1 DESC;
