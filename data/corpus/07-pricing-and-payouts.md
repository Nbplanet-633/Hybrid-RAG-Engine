---
title: Pricing, Fees, and Payouts
doc_id: aurora-pricing-payouts
version: 2024.11
owner: Finance Operations
---

# Pricing, Fees, and Payouts

## Processing fees

Fees are charged per successful payment and deducted from the amount before it reaches your
balance. There is no monthly platform fee and no setup fee on the Standard plan.

| Card origin                        | Fee                        |
|------------------------------------|----------------------------|
| European Economic Area (EEA) cards | **1.4% + €0.25**           |
| UK domestic cards                  | **1.5% + £0.20**           |
| Non-EEA / international cards      | **2.9% + €0.25**           |
| American Express (all regions)     | **3.1% + €0.25**           |

A €100.00 payment on an EEA card therefore costs €1.65 in fees (1.4% = €1.40, plus €0.25) and
credits €98.35 to your balance.

Currency conversion adds a **1% FX margin** on top, applied when the presentment currency
differs from your settlement currency.

## Other fees

| Item                                | Fee                                    |
|-------------------------------------|-----------------------------------------|
| Refund                              | Free (original processing fee is **not** returned) |
| Dispute                             | **€15.00**, refunded if you win         |
| Failed payout (bank rejection)      | €5.00                                   |
| Additional payout currency account  | €10.00 per month                        |
| Instant payout                      | 1% of the payout amount, minimum €0.50  |

Volume pricing begins at **€250,000 in monthly processed volume**; contact sales for a custom
rate card. Interchange-plus pricing is available on the Enterprise plan.

## Balance

Your balance has two components:

- **Available** — cleared funds eligible for payout.
- **Pending** — funds captured but still inside the settlement window.

`GET /v1/balance` returns both, broken down per currency.

Funds move from pending to available after the **standard settlement window of 2 business
days** for EEA and UK cards, and **5 business days** for international cards. New accounts are
placed on a 7-day window for their first 30 days, which shortens automatically once a payment
history exists.

## Payout schedule

Payouts are **daily and automatic** by default, sent every business day for the whole available
balance. Alternatives configurable in the dashboard:

- `daily` (default)
- `weekly` — choose a weekday; sent on that day
- `monthly` — choose a day of month between 1 and 28
- `manual` — you call `POST /v1/payouts` yourself

Payouts are only initiated on **TARGET2 business days**, so weekends and Eurosystem holidays
are skipped. Funds usually appear in your bank account **1 business day** after the payout is
created; SEPA payouts under €100,000 are sent via SEPA Instant and typically arrive within
seconds.

The **minimum payout amount is €10.00**. A balance below that rolls over to the next scheduled
payout rather than being sent.

## Payout failures

If your bank rejects a payout — closed account, wrong IBAN, name mismatch — Aurora returns the
funds to your available balance, charges the €5.00 failed-payout fee, and emits a
`payout.failed` webhook with a `failure_code`. Automatic payouts are **paused** after a
failure and stay paused until you update your bank details and re-enable them.

## Negative balances

Refunds and disputes can push your balance negative. Aurora attempts to recover a negative
balance from the next incoming payments. If the balance stays negative for **14 days**, Aurora
debits the registered bank account by direct debit. Persistent negative balances can lead to
the account being suspended.

## Reserves

Aurora may apply a **rolling reserve** to accounts in higher-risk categories — typically
travel, ticketing, subscriptions with long fulfilment horizons, and any account whose dispute
ratio exceeds 0.9%. A typical reserve holds **5% of processed volume for 90 days**. Reserve
terms are always communicated in writing before they take effect, and are reviewed quarterly.

## Reporting and reconciliation

Every balance movement — payment, refund, fee, dispute, payout — is a **Balance Transaction**.
`GET /v1/balance_transactions?payout=<payout_id>` returns exactly the transactions included in
a payout, which is the intended way to reconcile a bank deposit line-by-line against Aurora.

Monthly statements in CSV and PDF are generated on the **third business day** of the following
month and are available under Reports in the dashboard. VAT invoices for Aurora's own fees are
issued at the same time.
