---
title: Rate Limits and Error Handling
doc_id: aurora-limits-errors
version: 2024.11
owner: Platform Reliability
---

# Rate Limits and Error Handling

## Rate limits

Limits are enforced per account, not per API key, using a token bucket that refills
continuously. Creating extra keys does not increase your quota.

| Environment | Read requests | Write requests |
|-------------|---------------|----------------|
| Sandbox     | 25 / second   | 10 / second    |
| Production  | 100 / second  | 50 / second    |

Bursts are absorbed up to **2× the steady-state limit for 5 seconds**, so a production account
can briefly reach 200 reads per second before throttling begins.

Every response carries the current state of your bucket:

```
Aurora-RateLimit-Limit: 100
Aurora-RateLimit-Remaining: 87
Aurora-RateLimit-Reset: 1730812815
```

`Aurora-RateLimit-Reset` is a unix timestamp for when the bucket returns to full.

When throttled, Aurora returns **HTTP 429** with a `Retry-After` header in seconds. Honour that
header; retrying sooner extends the throttle rather than shortening it.

Batch and reporting endpoints under `/v1/reports` have a separate, much lower limit of
**10 requests per minute**, because each one triggers a warehouse query.

## Error format

Every error response uses the same envelope:

```json
{
  "error": {
    "type": "card_error",
    "code": "insufficient_funds",
    "message": "The card has insufficient funds to complete the purchase.",
    "param": "payment_method",
    "request_id": "req_8Hs2kQ1x",
    "doc_url": "https://docs.aurorapay.dev/errors/insufficient_funds"
  }
}
```

Always log `request_id`. Aurora support cannot investigate a failure without it, and it is the
only way to correlate your logs with Aurora's.

## Error types

| `type`                 | HTTP  | Meaning                                              | Retry?           |
|------------------------|-------|------------------------------------------------------|------------------|
| `authentication_error` | 401   | Missing, malformed, or revoked key                   | No               |
| `permission_error`     | 403   | Key lacks the required scope                         | No               |
| `invalid_request_error`| 400   | Malformed request or missing required field          | No               |
| `validation_error`     | 422   | Well-formed but semantically invalid                 | No               |
| `card_error`           | 402   | Issuer declined the card                             | No — ask for another card |
| `rate_limit_error`     | 429   | Quota exceeded                                       | Yes, after `Retry-After` |
| `api_error`            | 500   | Fault inside Aurora                                  | Yes, with backoff |
| `service_unavailable`  | 503   | Dependency degraded or maintenance                   | Yes, with backoff |

The rule of thumb: **4xx errors are your bug and should not be retried; 429, 500, and 503 are
transient and should be.** Retrying a 402 in a loop is a common integration mistake — the card
will keep declining and the issuer may flag your merchant ID for abuse.

## Common card decline codes

| Code                    | Customer-facing meaning                     |
|-------------------------|----------------------------------------------|
| `card_declined`         | Generic decline; the issuer gave no reason   |
| `insufficient_funds`    | Not enough balance                           |
| `expired_card`          | Card is past its expiry date                 |
| `incorrect_cvc`         | CVC did not match                            |
| `processing_error`      | Temporary issuer fault — retry once is fine  |
| `authentication_required` | 3-D Secure needed; create a new payment with authentication |

`processing_error` is the one card decline worth retrying, and only once.

## Recommended retry policy

Retry only `429`, `500`, `503`, and connection-level failures. Use exponential backoff with
full jitter, a base of 500 ms, a cap of 30 seconds, and **at most 5 attempts**:

```python
delay = min(30.0, 0.5 * (2 ** attempt))
sleep(random.uniform(0, delay))   # full jitter
```

Always send the same `Idempotency-Key` across retries of a write. Without it, a request that
succeeded but whose response was lost will be executed a second time.

## Timeouts

Aurora's own timeout is **30 seconds**; a request still running at that point is terminated
with `503`. Set your client timeout slightly above that — 35 seconds is the recommended
value — so that you observe Aurora's error instead of your own client aborting first and
leaving the outcome unknown.

## Status and incidents

Live status is published at `https://status.aurorapay.dev`, which also exposes an RSS feed and
a webhook for incident notifications. The production API carries a **99.95% monthly uptime
SLA**; scheduled maintenance is announced at least 14 days in advance and is excluded from the
SLA calculation.
