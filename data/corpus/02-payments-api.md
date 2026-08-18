---
title: Payments API Reference
doc_id: aurora-payments-api
version: 2024.11
owner: Payments Core
---

# Payments API Reference

A **Payment** represents a single attempt to move money from a customer to your account.
Payments are immutable in their financial details: once created, `amount` and `currency` can
never be changed. To charge a different amount you must cancel and create a new payment.

## The payment lifecycle

A payment moves through these statuses:

1. `requires_payment_method` — created without a usable payment method.
2. `requires_action` — customer authentication (usually 3-D Secure) is pending.
3. `processing` — submitted to the card network; the outcome is not yet known.
4. `requires_capture` — authorised but not captured (manual capture only).
5. `succeeded` — funds captured. **Terminal.**
6. `canceled` — abandoned before capture. **Terminal.**
7. `failed` — declined by the issuer or the network. **Terminal.**

Terminal statuses never change. A `succeeded` payment can be refunded, but refunding does not
alter the payment's status — it creates a separate Refund object.

## Capture modes

`capture_method` accepts two values:

- `automatic` (default) — authorisation and capture happen in one step.
- `manual` — Aurora authorises the card and holds the payment in `requires_capture` until you
  call the capture endpoint.

**An uncaptured authorisation expires after 7 days.** At expiry Aurora automatically
transitions the payment to `canceled` and releases the hold on the customer's card. Visa and
Mastercard both permit longer holds for some merchant categories, but Aurora applies the
7-day limit uniformly to keep behaviour predictable.

Partial capture is supported: you may capture any amount from 1 minor unit up to the
authorised amount. Capturing less than the authorised amount automatically releases the
remainder. You cannot capture more than you authorised.

## Idempotency

Every mutating request (`POST`, `DELETE`) accepts an `Idempotency-Key` header. Aurora stores
the first response for a given key and replays it for any repeat of the same request.

- Keys are scoped to your account and to the endpoint.
- Keys are retained for **24 hours**, after which the same key is treated as new.
- Maximum key length is **255 characters**. Aurora recommends a UUID v4 or a
  `<order-id>-<attempt>` composite.
- Replaying a key with a *different* request body returns HTTP 422 with the error code
  `idempotency_key_reuse`. This is a guardrail against accidentally charging twice for two
  genuinely different orders.

Idempotency keys are the correct answer to network timeouts. If a `POST /v1/payments` call
times out, retry it with the same key: you will either create the payment once or receive the
original response.

## Creating a payment

`POST /v1/payments`

| Field             | Type    | Required | Notes                                            |
|-------------------|---------|----------|--------------------------------------------------|
| `amount`          | integer | yes      | Minor units; minimum 50, maximum 99999999        |
| `currency`        | string  | yes      | ISO 4217, lowercase or uppercase accepted        |
| `payment_method`  | string  | yes      | A `pm_` token                                    |
| `capture_method`  | string  | no       | `automatic` (default) or `manual`                |
| `statement_descriptor` | string | no  | Max 22 characters, appears on the card statement |
| `metadata`        | object  | no       | Up to 40 keys, each value at most 500 characters |

The minimum chargeable amount is **50 minor units** in any currency (€0.50, $0.50, ¥50).
Requests below the minimum are rejected with `amount_too_small`.

## Supported currencies

Aurora settles in EUR, GBP, USD, CAD, SEK, DKK, NOK, PLN, and CHF. Payments may be *presented*
in 34 currencies; anything outside the settlement list is converted at the daily reference rate
plus a 1% FX margin at settlement time. The reference rate is fixed once per day at 14:00 CET.

## Cancelling

`POST /v1/payments/{id}/cancel` works only while the payment is in
`requires_payment_method`, `requires_action`, or `requires_capture`. Cancelling a payment in
`processing` returns `payment_not_cancelable`; you must wait for it to reach a terminal state
and then issue a refund instead.

## Metadata

Metadata is returned on every read of the payment and is included in webhook payloads. It is
never shown to the customer. **Do not store cardholder data, full card numbers, or personal
identifiers in metadata** — the field is not covered by Aurora's PCI scope and is replicated to
analytics systems with a lower security classification.

## Listing and pagination

`GET /v1/payments` returns cursor-paginated results. Pass `limit` (default 25, maximum 100) and
`starting_after=<payment_id>`. Results are ordered by `created_at` descending. The response
includes a `has_more` boolean; there is no total count, because computing one over the full
payment history is prohibitively expensive.
