---
title: Webhooks
doc_id: aurora-webhooks
version: 2024.11
owner: Platform Integrations
---

# Webhooks

Webhooks deliver asynchronous state changes to an HTTPS endpoint you control. Anything that can
happen without an API call from you — a 3-D Secure completion, a dispute, a payout — is
communicated by webhook.

## Registering an endpoint

`POST /v1/webhook_endpoints` with a `url` and a list of `enabled_events`. The URL must be
HTTPS; plain HTTP endpoints are rejected at registration time. You may register up to
**10 endpoints per account**, which is enough to fan out to separate staging, analytics, and
production consumers.

Each endpoint has its own **signing secret**, returned once at creation with the prefix
`whsec_`. Rotating an endpoint's secret via `POST /v1/webhook_endpoints/{id}/rotate_secret`
keeps the old secret valid for **24 hours** so you can deploy the new one without dropping
deliveries.

## Event types

| Event                     | Fires when                                          |
|---------------------------|-----------------------------------------------------|
| `payment.succeeded`       | Funds captured                                      |
| `payment.failed`          | Issuer or network declined                          |
| `payment.requires_action` | 3-D Secure authentication needed                    |
| `refund.succeeded`        | Refund accepted by the network                      |
| `refund.failed`           | Refund rejected (usually a closed card account)     |
| `dispute.created`         | A formal dispute was opened — starts the 7-day clock |
| `dispute.closed`          | Dispute resolved; `data.status` is `won` or `lost`  |
| `payout.paid`             | A payout landed in your bank account                |
| `payout.failed`           | A payout was returned by your bank                  |
| `account.verified`        | KYB review completed                                |
| `risk.threshold_warning`  | Dispute ratio crossed the 0.65% early warning       |

Subscribing to `*` enables all current and future event types. Aurora recommends listing events
explicitly instead, so a newly introduced event type cannot break a strict consumer.

## Verifying signatures

Every delivery carries an `Aurora-Signature` header:

```
Aurora-Signature: t=1730812800,v1=5257a869e7ecebeda32affa62cdca3fa51cad7e77a0e56ff536d0ce8e108d8bd
```

Verification is mandatory. An unverified endpoint will accept forged events from anyone who
learns the URL.

1. Split the header on `,` and read `t` (unix timestamp) and `v1` (hex signature).
2. Build the signed payload as `"{t}.{raw_request_body}"` — the **raw** body bytes, before any
   JSON parsing or re-serialisation.
3. Compute `HMAC-SHA256` of that string using the endpoint's `whsec_` signing secret.
4. Compare with `v1` using a **constant-time** comparison (`hmac.compare_digest` in Python).
5. Reject the delivery if `t` is more than **5 minutes** old. This is what prevents replay
   attacks; a valid signature stays valid forever without a timestamp check.

```python
import hashlib, hmac, time

def verify(raw_body: bytes, header: str, secret: str, tolerance: int = 300) -> bool:
    parts = dict(p.split("=", 1) for p in header.split(","))
    timestamp, signature = parts["t"], parts["v1"]
    if abs(time.time() - int(timestamp)) > tolerance:
        return False
    expected = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
```

Re-serialising the parsed JSON before hashing is the most common cause of verification
failures: key order and whitespace change, so the HMAC no longer matches.

## Retries and delivery semantics

Aurora expects a `2xx` response within **10 seconds**. Anything else — a non-2xx status, a
timeout, a connection error — counts as a failure and is retried.

Failed deliveries are retried **8 times over 24 hours** with exponential backoff at roughly
10s, 1m, 5m, 30m, 2h, 5h, 10h, and 24h. After the final attempt the event is dropped and a
`webhook.delivery_failed` notification is emailed to account administrators.

An endpoint that fails **every** delivery for 72 consecutive hours is automatically disabled
and must be re-enabled from the dashboard.

### At-least-once delivery

Webhook delivery is **at-least-once**, not exactly-once. Network partitions and retry races
mean your endpoint will occasionally see the same event twice. Every event carries a unique
`id` with the prefix `evt_`; persist processed IDs and ignore duplicates.

Events are **not ordered**. A `payment.succeeded` can arrive after the `refund.succeeded` for
the same payment. Do not derive state from arrival order — every event includes the full
current object, so treat the payload as the source of truth and discard any event whose
`created_at` is older than the state you already have.

## Responding quickly

Do the minimum in the request handler: verify the signature, enqueue the event, return `200`.
Long-running work inside the handler is the usual cause of timeout-driven retry storms, which
then multiply the load that caused the timeout in the first place.
