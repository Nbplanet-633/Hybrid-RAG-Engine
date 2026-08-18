---
title: Security and Compliance
doc_id: aurora-security-compliance
version: 2024.11
owner: Security Engineering
---

# Security and Compliance

## Certifications

Aurora is certified **PCI DSS Level 1** as a service provider, audited annually by a Qualified
Security Assessor. The current Attestation of Compliance (AOC) is available under a mutual NDA
from the Trust Center at `https://trust.aurorapay.dev`.

Aurora also holds **SOC 2 Type II** (annual, covering security and availability) and
**ISO 27001:2022**. Penetration tests are performed by an external firm **twice per year**, and
a summary report is shared with enterprise customers on request.

## Reducing your PCI scope

If card details never touch your servers, your obligation is the shortest questionnaire,
**SAQ A**. To stay in that scope, tokenize in the browser or mobile client with the
publishable key and Aurora Elements, and send only the resulting `pm_` token to your backend.

Posting raw PAN (primary account number) data to your own server pushes you into **SAQ D**,
which is roughly 300 questions and requires quarterly ASV scans. Aurora's API will accept raw
card data only from accounts that have submitted their own valid AOC.

## Encryption

- **In transit:** TLS 1.2 or higher. TLS 1.0 and 1.1 were disabled on 1 March 2024. The
  production endpoint prefers TLS 1.3 and supports HSTS with a one-year max-age.
- **At rest:** AES-256-GCM. Card data is held in a dedicated vault with its own key hierarchy,
  isolated from the application databases.
- **Key management:** data keys are wrapped by a hardware security module (HSM) and rotated
  automatically every **90 days**. Rotation is transparent — no action is required from you.

## API key hygiene

Secret keys are shown once and stored only as a salted hash. Recommended practice:

1. Use separate keys per service so a compromise can be contained.
2. Rotate keys at least every **180 days**.
3. Never commit keys; Aurora scans public GitHub and automatically revokes any leaked
   `sk_live_` key it finds, then emails your account administrators.
4. Restrict keys by IP allowlist from the dashboard where the caller has a stable egress IP.

Because an account may hold up to five active secret keys, rotation is zero-downtime: create
the new key, deploy it, confirm traffic has moved, then revoke the old one.

## Access control

Dashboard access supports five roles:

| Role          | Capabilities                                             |
|---------------|-----------------------------------------------------------|
| `owner`       | Everything, including closing the account                 |
| `admin`       | Everything except closing the account                     |
| `developer`   | Manage API keys and webhooks; read all objects            |
| `analyst`     | Read-only access to payments and reports                  |
| `support`     | Read payments and issue refunds; cannot view API keys      |

**Two-factor authentication is mandatory for `owner` and `admin`.** SSO via SAML 2.0 and SCIM
user provisioning are available on the Enterprise plan.

## Data retention

| Data class                     | Retention                                    |
|--------------------------------|----------------------------------------------|
| Payment and refund records     | 7 years (financial-records obligation)       |
| Full card numbers (PAN)        | Never stored outside the vault; tokenized    |
| CVC / CVV                      | **Never stored**, not even transiently       |
| API request logs               | 90 days                                      |
| Webhook delivery logs          | 30 days                                      |
| Sandbox data                   | Reset every 30 days                          |

CVC is forwarded to the issuer for authorisation and discarded immediately; it is never
written to disk. This is a PCI DSS requirement, not an Aurora policy choice.

## GDPR

Aurora acts as a **data processor** for cardholder data you send, and as a **data controller**
for your account data. A Data Processing Agreement is available in the dashboard under
Settings → Legal, and is countersigned automatically.

EU and UK cardholder data is stored in `eu-west-1` (Ireland) and never leaves the region.
Transfers to Aurora's US support staff rely on Standard Contractual Clauses and are limited to
metadata; no PAN is transferred.

Data subject erasure requests can be submitted via `POST /v1/privacy/erasure_requests`.
Aurora completes erasure within **30 days**, with one exception: transaction records that must
be retained for the 7-year financial-records obligation are pseudonymised rather than deleted,
because deleting them would breach anti-money-laundering law.

## Reporting a vulnerability

Email `security@aurorapay.dev` with the PGP key published at `/.well-known/security.txt`.
Aurora acknowledges within **one business day** and runs a paid bug bounty; critical findings
are eligible for up to **€10,000**. Please do not test against production accounts you do not
own — use sandbox.
