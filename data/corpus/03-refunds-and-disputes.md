---
title: Refunds and Disputes
doc_id: aurora-refunds-disputes
version: 2024.11
owner: Risk Operations
---

# Refunds and Disputes

## Refunds

A **Refund** returns captured funds to the cardholder. Refunds are always created against a
payment that is in the `succeeded` status; refunding any other status returns
`payment_not_refundable`.

### Creating a refund

`POST /v1/refunds`

```json
{
  "payment": "pay_3Kq9x2Lm",
  "amount": 1000,
  "reason": "requested_by_customer"
}
```

Omitting `amount` refunds the full remaining refundable balance. Accepted values for `reason`
are `duplicate`, `fraudulent`, `requested_by_customer`, and `product_not_received`. The reason
is reported to the card network and influences your dispute-prevention scoring, so it should be
accurate.

### Partial and multiple refunds

A payment may be refunded up to **20 times**, as long as the sum of all refunds does not exceed
the captured amount. Attempting a 21st refund returns `too_many_refunds`. Attempting to refund
more than the captured amount returns `refund_exceeds_payment`.

### Timing

Refunds are submitted to the card network immediately, but the cardholder sees the money
returned after the issuer processes it. Typical timelines:

- Visa and Mastercard: **5 to 10 business days**
- Amex: **3 to 5 business days**
- SEPA Direct Debit: **up to 14 calendar days**

A payment can be refunded for **180 days** after capture. Past that window the card network
refuses the refund and you must pay the customer by bank transfer.

### Refund fees

Aurora does not charge a fee to process a refund, but **the original processing fee is not
returned**. This is the single most common billing question raised in support tickets.

## Disputes

A **Dispute** (also called a chargeback) is raised by the cardholder's issuing bank. Unlike a
refund, a dispute is adversarial: the funds are withdrawn from your balance immediately and are
only returned if you win.

### Dispute lifecycle

1. `warning_needs_response` — an early-warning notice from the network. Refunding now avoids a
   formal chargeback and protects your dispute ratio.
2. `needs_response` — a formal dispute. Evidence is required.
3. `under_review` — evidence submitted, network is deliberating.
4. `won` — funds returned to your balance.
5. `lost` — funds stay with the cardholder.

### Evidence window

**You have 7 calendar days from the `dispute.created` webhook to submit evidence.** This is
shorter than the card network's own deadline because Aurora needs time to assemble and forward
the submission. Aurora sends reminder webhooks at 72 hours and again at 24 hours before the
deadline. Missing the window means the dispute is automatically lost.

Evidence is submitted once and cannot be amended. Assemble everything before you call
`POST /v1/disputes/{id}/evidence`.

### Useful evidence

The fields that most affect outcomes, in Aurora's own win-rate analysis:

- `receipt` — the itemised receipt sent to the customer
- `shipping_documentation` — carrier tracking showing delivery to the billing address
- `customer_communication` — email or chat proving the customer acknowledged the purchase
- `service_date` and `service_documentation` — for non-shipped services
- `refund_policy` — your published policy, plus proof the customer accepted it

### Dispute fees

Every formal dispute incurs a **€15.00 dispute fee**, charged when the dispute is opened. The
fee is **refunded if you win**. Early-warning notices in `warning_needs_response` do not incur
a fee.

### Dispute ratio and monitoring

Card networks place merchants into a monitoring program when the dispute ratio exceeds
**0.9% of monthly transaction count** or when monthly disputes exceed 100, whichever comes
first. Aurora sends a `risk.threshold_warning` webhook at 0.65% so you have room to react.
Sustained monitoring can result in higher fees or account termination by the network — not by
Aurora.

## Choosing between a refund and fighting a dispute

If the customer is right, refund immediately at the `warning_needs_response` stage. A refund
costs you the original processing fee; a lost dispute costs you the transaction amount, the
processing fee, **and** the €15.00 dispute fee, and it counts against your ratio. Fight only
when you hold delivery or usage evidence you can produce within the 7-day window.
