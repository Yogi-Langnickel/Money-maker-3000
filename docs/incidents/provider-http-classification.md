# Provider HTTP 403 classification

Status: corrected before the rebuilt patch was integrated.

## What happened

On 2026-09-14, the separately authorized, read-only eToro instrument metadata
search stopped after HTTP 403. The collector collapsed every 403 into
`entitlement-failed`. That label exceeded the evidence: an HTTP status alone did
not establish an account entitlement, credential, licensing, or provider policy
diagnosis.

The controlled diagnostic observed an eToro edge response matching Cloudflare
Error 1010's browser-signature category. No credentials, account data, raw
headers, raw response body, or provider payload are retained in this incident.

## Correction

The collector now reads at most 16 KiB of a 403 response only to compare a
strict JSON title/detail pair for that observed Cloudflare category. It returns
`cloudflare-browser-signature-block` only for that exact bounded, valid,
duplicate-free envelope. Malformed, oversized, duplicated, unreadable, or other
403 envelopes return `http-forbidden-cause-unresolved`. Both codes stop the
request run. HTTP 401, 404, and 429 retain their separate controlled codes.
Closing an HTTP error cannot expose a raw exception or change the controlled
classification.

## Scope and follow-up

The account holder later supplied eToro support guidance that the edge rule
rejected urllib's default `Python-urllib/3.13` User-Agent before the Public API
processed the request.
Per the supplied support guidance, the collector now uses the fixed
`personal-research-client/1.0` identifier on its existing read-only allowlisted
GET requests. It does not impersonate a browser, alter credentials, add a
Bearer token, or make a live request as part of this correction. The existing
`x-api-key`, `x-user-key`, and per-request `x-request-id` headers remain
required. A future JSON 401 or 403
is distinct from Error 1010 and must be escalated to eToro with a new request
ID. The collector exposes that current request ID only as in-memory
`last_request_id`; header values and payloads remain unretained. Any later successful
retrieval still needs separate instrument, session, price-basis, adjustment,
cost, model-use, and retention evidence before research use or portability can
be assessed. No post-change provider request or access outcome has yet been
verified.

## Sources

- [Cloudflare Error 1010](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1010/)
- [Cloudflare Browser Integrity Check](https://developers.cloudflare.com/waf/tools/browser-integrity-check/)
- [eToro Builders FAQ](https://builders.etoro.com/faq)
- [eToro market-data API reference](https://api-portal.etoro.com/api-reference/market-data/get-instrument-candle-history)
