---
title: Getting Started with the Aurora Payments API
doc_id: aurora-getting-started
version: 2024.11
owner: Developer Experience
---

# Getting Started with the Aurora Payments API

Aurora Payments is a card-present and card-not-present payment platform for marketplaces
operating in the EU, UK, and North America. This guide covers account setup, authentication,
environments, and your first API call.

## Creating an account

Sign up at `https://dashboard.aurorapay.dev/signup`. Every new account starts in **sandbox
mode**. Sandbox accounts are fully functional but never move real money, and they are reset
every 30 days. To move to production you must complete Know Your Business (KYB) verification,
which requires a certificate of incorporation, proof of a business bank account, and identity
documents for every beneficial owner holding 25% or more of the company.

KYB review takes **two business days** for sole traders and **five business days** for
companies with more than one beneficial owner. You will receive a `account.verified` webhook
when review completes.

## Environments

Aurora exposes two environments. They are completely isolated: sandbox keys never work
against production, and objects created in one environment are not visible in the other.

| Environment | Base URL                          | Key prefix   |
|-------------|-----------------------------------|--------------|
| Sandbox     | `https://api.sandbox.aurorapay.dev` | `sk_sandbox_` |
| Production  | `https://api.aurorapay.dev`         | `sk_live_`    |

All requests must be made over HTTPS with TLS 1.2 or higher. Plain HTTP requests are rejected
at the load balancer and never reach the API.

## Authentication

Aurora uses bearer token authentication. Pass your secret key in the `Authorization` header:

```
Authorization: Bearer sk_live_9f2c4a...
```

Secret keys are shown exactly once, at creation time. Aurora stores only a salted hash, so a
lost key cannot be recovered — it must be rotated. Each account may hold at most **five active
secret keys** simultaneously, which is what makes zero-downtime rotation possible.

Publishable keys (prefix `pk_live_` / `pk_sandbox_`) are safe to embed in browser and mobile
clients. They can only tokenize card details; they cannot create payments, issue refunds, or
read any object.

### Required headers

Every request must include:

- `Authorization: Bearer <secret key>`
- `Aurora-Version: 2024-11-01` — pins the API version for that request
- `Content-Type: application/json` for any request with a body

If `Aurora-Version` is omitted, the request is served with the version pinned to your account
at the time of your first successful API call. Relying on that default is discouraged, because
it makes behaviour depend on account history rather than on your code.

## Your first request

The following call creates a €25.00 payment in sandbox:

```bash
curl https://api.sandbox.aurorapay.dev/v1/payments \
  -H "Authorization: Bearer $AURORA_SANDBOX_KEY" \
  -H "Aurora-Version: 2024-11-01" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: order-1001-attempt-1" \
  -d '{
    "amount": 2500,
    "currency": "EUR",
    "payment_method": "pm_card_visa",
    "capture_method": "automatic"
  }'
```

Amounts are always integers in the **minor unit** of the currency: `2500` means €25.00.
Zero-decimal currencies such as JPY and KRW use the major unit directly, so `2500` means
¥2,500. Aurora rejects any amount field sent as a decimal string.

## Test cards

Sandbox recognises a fixed set of test payment methods:

| Token                    | Behaviour                                     |
|--------------------------|-----------------------------------------------|
| `pm_card_visa`           | Succeeds immediately                          |
| `pm_card_mastercard_3ds` | Requires 3-D Secure authentication            |
| `pm_card_declined`       | Declined with `card_declined`                 |
| `pm_card_insufficient`   | Declined with `insufficient_funds`            |
| `pm_card_dispute`        | Succeeds, then opens a dispute after 1 minute |

## SDK quickstart

Install the Python SDK and issue the same call:

```python
import aurora

client = aurora.Client(api_key=os.environ["AURORA_SANDBOX_KEY"])
payment = client.payments.create(
    amount=2500,
    currency="EUR",
    payment_method="pm_card_visa",
    idempotency_key="order-1001-attempt-1",
)
print(payment.status)  # "succeeded"
```

## Where to go next

- **Payments API** — the payment lifecycle, capture modes, and idempotency.
- **Webhooks** — receiving asynchronous state changes and verifying signatures.
- **Rate limits and errors** — quotas, error taxonomy, and the retry policy.
