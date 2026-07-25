# Incident Review: Ledger v2 Incomplete Schema Reached Develop

Date: 2026-07-25
Status: closed
Severity: high
Type: financial-data-integrity

## Summary

The `simulation-audit-record.v2` append validator on `develop` checked only
unknown top-level fields, the DTO label, the simulation/synthetic boundary,
and sensitive-value redaction. It did not require the complete record shape or
validate identities, timestamps, hashes, nested allocation/trade-log data,
decision semantics, vetoes, or parent/nested consistency.

The report path could therefore accept an incomplete v2 record and fill
missing diagnostic facts with defaults. Append also inspected existing JSONL
with a raw reader, so legacy or corrupt existing content was not consistently
treated as an append blocker.

## Impact

- Affected workflow: local synthetic diagnostic ledger append and report.
- User-visible impact: no confirmed user-visible incident.
- Financial impact: none confirmed; provider calls and execution remained
  blocked.
- Data impact: a malformed local v2 diagnostic record could be accepted or
  presented with invented default fields, reducing audit reliability.
- Security impact: no credential or account-data exposure was found.

## Timeline

- Before 2026-07-25: permissive v2 validation reached `develop`.
- 2026-07-25: production-readiness review identified incomplete ledger
  validation and report defaulting.
- 2026-07-25: exact-shape validation, append integrity gating, focused schema
  tests, and durable documentation were implemented on a scoped repair branch.

## Root Cause

### Five Whys

1. Why could incomplete v2 records be accepted? The validator checked a small
   boundary subset instead of the complete schema.
1. Why could reports hide missing facts? Sanitization helpers defaulted invalid
   or absent v2 values to safe-looking `skip`, `blocked`, and `unknown` output.
1. Why was this not detected? Tests concentrated on redaction, duplicate
   identities, malformed JSONL recovery, and happy-path generated records.
1. Why did those tests pass? There was no field-deletion matrix, nested exact
   shape test, version-classification matrix, nonfinite-number matrix, or
   parent/nested parity test.
1. Why did the gap reach `develop`? The v2 DTO label was treated as sufficient
   schema ownership without a canonical exhaustive validator and matching
   negative contract suite.

## Resolution

- Froze v2 as an exact diagnostic-only `skip`/`blocked` schema.
- Required exact top-level, allocation, and trade-log shapes.
- Added canonical UTC, SHA-256, identifier, finite-number, allocation
  consistency, veto, provider-boundary, and parent/nested parity validation.
- Made direct v2 report construction validate before sanitization, eliminating
  defaults for malformed v2.
- Limited compatibility recovery to explicit v1 read/report/redact behavior.
- Made append inspect the existing source through the integrity reader and
  refuse corrupt, legacy, or mixed content without mutation.
- Moved the canonical risk-veto catalog to `risk.py` and added the previously
  omitted `invalid-risk-state` and `invalid-order-intent` parity.
- Reserved actual virtual-trade accounting for a future v3/SQLite event model.

## Prevention

- Every versioned financial/audit DTO needs one exact canonical validator.
- New schema versions require exhaustive negative tests for every required
  field and nested field, plus unknown-field and mixed/future-version cases.
- Report sanitization may redact explicit legacy records but must not invent
  facts for the current schema.
- Append-only storage must validate both the incoming record and the complete
  existing append target inside the writer lock.
- Shared enumerations emitted by business logic and accepted by persistence
  must have one owner and a parity test.

## Verification

- `tests/test_ledger_schema.py` covers exact shapes, every missing field,
  unknown fields, versions, timestamps, hashes, numeric edge cases, duplicate
  and mismatched vetoes, parent/nested mismatches, legacy/corrupt append
  refusal, and risk-veto parity.
- Repository compile and full unittest gates pass on the repair branch.
- No provider, credential, or execution behavior changed.

## Transferability

- Category: `workspace-general`
- Suggested propagation targets: repositories with versioned audit,
  reconciliation, financial, security, or append-only event DTOs.
- Propagation disposition: the orchestrator should promote the general rule
  through workspace QA/review guidance: current-version DTOs use exact
  validators and exhaustive negative schema tests; compatibility sanitization
  is explicit-version-only.
