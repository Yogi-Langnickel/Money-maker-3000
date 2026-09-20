# eToro User-Agent follow-up

## Scope

This patch changes only the read-only eToro collector's request identifier and
its support-escalation handle. The allowlisted paths, GET-only transport, TLS
verification, disabled proxies, request budget, credential profile handling,
retention gate, and model-use gate are unchanged.

## Account-holder-supplied eToro support guidance

On 21 September 2026, the account holder supplied eToro support guidance that
Cloudflare Error 1010 was a Browser Integrity Check rejection of urllib's
default `Python-urllib/3.13` User-Agent before the Public API handled the
request. The guidance specified the fixed `personal-research-client/1.0`
identifier for the existing instrument search request and confirmed the
`x-api-key`, `x-user-key`, and
`x-request-id` authentication pattern. It instructed that no Bearer token be
added to that call.

The collector now sends that exact identifier. It keeps the generated request
ID only in process memory as `last_request_id` so a later JSON 401 or 403 can
be supplied to support. It does not persist request IDs, credentials, headers,
payloads, account information, or observations.

## Verification

The added synthetic transport test asserts the exact User-Agent, the presence
of required header names, absence of `Authorization`, and in-memory request-ID
handoff. A later account-holder-supplied, sanitized metadata-only observation
showed that the custom User-Agent and required authentication headers reached
HTTP 200. Explicitly requesting `instrumentId` produced a duplicate JSON key;
omitting that redundant field returned a unique SPY match with an ID, display
name, and exchange but no `instrumentType`. The collector keeps its strict
parser and rejects that incomplete response as
`instrument-type-or-exchange-unverified`. No price history was retained and no
predictive or portability claim follows. Full standard-library validation and
compilation passed on this branch before review.
