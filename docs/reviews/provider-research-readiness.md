# Provider research readiness rebuild review record

Status: accepted after two review passes with required corrections.

## Scope

The original reviewed-but-uncommitted provider-readiness patch was lost with its
temporary worktree. This rebuild restores its bounded public surface only:

- `src/money_maker_3000/feed_collection.py`
- `tests/test_feed_collection.py`
- `docs/continuous-research-protocol.md`
- `docs/agent-memory.md`
- `docs/incidents/provider-http-classification.md`
- `docs/reviews/provider-research-readiness.md`

The rebuilt collector classifies the exact observed Cloudflare browser-signature
403 envelope within a 16 KiB inspection limit, treats all other 403 responses as
unresolved, preserves separate 401/404/429 behavior, and stops after a 403. It
does not make a provider request, load a real profile, retain provider output,
or alter the separate eToro model-use/retention gate.

Synthetic tests cover the exact category, generic/malformed/duplicate/oversized
envelopes, close failures, strict boolean-ID rejection, ambiguous instruments,
UTC-offset normalization, exact 26-hour completion, and extreme timestamps.

## Developer validation

- Focused collector suite: `PYTHONPATH=src python3.13 -m unittest
  tests.test_feed_collection` — 43 tests passed after the security correction.
- `PYTHONDONTWRITEBYTECODE=1 PYTHONPYCACHEPREFIX=.pycache python3.13 -m
  compileall src tests` — passed.
- `PYTHONPATH=src python3.13 -m unittest discover tests` — 311 tests passed in
  54.408 seconds after the overflow regression correction.
- `git diff --check` — passed.

## Persona review evidence

Reviewers: `/root/review_research_governance` and
`/root/review_security_semantics`.

| Pass | Stable fingerprint | Governance result | Security result | Disposition |
| --- | --- | --- | --- | --- |
| 1 | `bc8569a2c3127555b3930217a5a7d49c74ca2c3e8037100972eb965200d6b832` | Accepted | Required exact-key envelope and persistence-overflow corrections | Returned to developer |
| 2 | `a54e6fc9aa2af59657f813c51a44c0bf381dacc0542f823476e008a78243c998` | Accepted | Required a near-`datetime.max` persistence regression | Returned to developer |
| 3 | `5dfa42cb57334c4368cdfa93b70769542123d2399913078508d4b36da3d29841` | No further findings | Accepted | Final accepted code baseline |

The two required review areas completed two substantive passes with corrections;
the third security check accepted the resulting final code baseline. No review
evidence includes provider observations, credentials, account data, or private
research artifacts.

## Security review corrections

The first security review required an exact top-level `title`/`detail` envelope
shape for the Cloudflare classification and identified a second 26-hour
completion check in `persist_version` that could overflow at `datetime.max`.
The rebuilt patch now rejects a matching envelope with an extra field and uses
subtraction for both completion checks. The second review corrected the
persistence regression to exercise the near-`datetime.max` unfinished path,
where the previous addition would overflow. Synthetic regressions cover both
cases.

## Prior review evidence

The prior component had already received review feedback before its temporary
worktree disappeared. This file records reconstruction provenance only; it does
not treat that earlier review as evidence for this rebuilt patch. The review
evidence above applies only to the reconstructed source surface.
