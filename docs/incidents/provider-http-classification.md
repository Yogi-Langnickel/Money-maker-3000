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

This fixes observability, not provider access. It does not bypass Cloudflare,
alter client identity, retry credentials, or make any live request. eToro must
identify the applicable API/security configuration if the access issue is to be
resolved. Any later successful retrieval still needs separate instrument,
session, price-basis, adjustment, cost, model-use, and retention evidence before
research use or portability can be assessed.

## Sources

- [Cloudflare Error 1010](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1010/)
- [Cloudflare Browser Integrity Check](https://developers.cloudflare.com/waf/tools/browser-integrity-check/)
- [eToro Builders FAQ](https://builders.etoro.com/faq)
- [eToro market-data API reference](https://api-portal.etoro.com/api-reference/market-data/get-instrument-candle-history)
