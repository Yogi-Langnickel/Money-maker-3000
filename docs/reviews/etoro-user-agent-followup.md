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
handoff. No provider request was made. Full standard-library validation and
compilation passed on this branch before review. No post-change provider
request or access outcome has yet been verified.
