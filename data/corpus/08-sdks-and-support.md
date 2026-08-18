---
title: SDKs, Versioning, and Support
doc_id: aurora-sdks-support
version: 2024.11
owner: Developer Experience
---

# SDKs, Versioning, and Support

## Official SDKs

| Language   | Package                  | Minimum runtime |
|------------|--------------------------|-----------------|
| Python     | `aurora-python`          | Python 3.9      |
| Node.js    | `@aurorapay/node`        | Node 18         |
| Go         | `github.com/aurorapay/aurora-go` | Go 1.21 |
| Java       | `dev.aurorapay:aurora-java` | Java 11      |
| Ruby       | `aurora-ruby`            | Ruby 3.0        |
| PHP        | `aurorapay/aurora-php`   | PHP 8.1         |

All official SDKs implement automatic retries with exponential backoff for `429`, `500`, and
`503`, and automatically attach an idempotency key to every write request that does not already
carry one. Community SDKs exist for Rust and Elixir but are not covered by the support SLA.

## API versioning

The API is versioned by date. Your account is pinned to the version in effect at your first
successful API call, and that pin never changes on its own. Override per request with the
`Aurora-Version` header, or change the account default in the dashboard.

Aurora publishes a new version roughly **twice a year**. Every version is supported for a
**minimum of 24 months** after its successor ships. Deprecations are announced by email and in
the changelog at least **6 months** before removal, and a `Aurora-Deprecation-Warning` response
header appears on affected endpoints during that period.

### What counts as a breaking change

Breaking (requires a new API version):

- Removing a field or endpoint
- Renaming a field
- Changing a field's type
- Adding a new required request parameter
- Changing the meaning of an existing value

Non-breaking (ships to all versions immediately):

- Adding a new optional request parameter
- Adding a new response field
- Adding a new event type
- Adding a new enum value to an existing field

Because new enum values are non-breaking, clients must tolerate unknown enum values rather
than crashing on them. Treat an unrecognised `status` as "not yet terminal" and re-fetch.

## Support plans

| Plan       | Channels                   | First response  | Included with                |
|------------|----------------------------|-----------------|-------------------------------|
| Standard   | Email, docs                | 24 hours        | All accounts                  |
| Priority   | Email, chat                | 4 hours         | €50k+ monthly volume          |
| Enterprise | Email, chat, phone, Slack  | 1 hour          | Contract; named account team  |

Response targets apply to business hours (09:00–18:00 CET, Monday to Friday) except on the
Enterprise plan, which is 24×7 for **Severity 1** issues.

### Severity definitions

- **Sev 1** — production is fully down or funds are at risk.
- **Sev 2** — a major feature is broken with no workaround.
- **Sev 3** — degraded behaviour with a workaround available.
- **Sev 4** — a question or feature request.

When opening a ticket, always include the `request_id` from the failing response. Without it,
support cannot locate the request in Aurora's logs and the first reply will simply ask for it.

## Debugging tools

- **Request log** — every API call from the last 90 days, searchable by `request_id`, endpoint,
  and status, with the full request and response body.
- **Webhook inspector** — every delivery attempt with the response code and body, plus a
  one-click **replay** button. Delivery logs are kept for 30 days.
- **Sandbox time travel** — `POST /v1/testing/advance_clock` moves a sandbox account's clock
  forward so you can observe payout timing, authorisation expiry, and reserve release without
  waiting. Sandbox only; it has no effect in production.

## Changelog and status

The changelog lives at `https://docs.aurorapay.dev/changelog` and has an RSS feed. Incidents
and scheduled maintenance are published at `https://status.aurorapay.dev`. Subscribe to the
status page rather than polling the API to detect outages.
