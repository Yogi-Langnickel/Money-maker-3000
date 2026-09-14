# Continuous research v1 review log

The delivery targets reviewed code on `origin/develop`. Provider data, forecasts,
models, and source-derived hashes are private and excluded from this log.

## Developer scopes

- `research_core`: isolated implementation worktree; frozen research runner,
  forward journal, configured CLI, skill/helper, focused tests, protocol and goal
  documents. The original dirty checkout remains preserved.
- `feed_collection`: separate read-only collector, exact rights/interpretation
  gates, immutable observation versions, availability revisions, collector tests.
- `feed_portability`: frozen source comparisons, strategy behavior and transfer
  evaluation, retention-gated reports, portability tests and documentation.

## Component reviews

Collector completed two component review rounds and the subsequent correction
subset, with 29 tests passing before durable withdrawal tracking. The latter adds
cumulative unresolved missing dates and explicit restoration evidence; its 31
focused tests pass. No live credentials were retried after approval review rejected
the probe, and no eToro authentication or retained-history result is asserted.

Portability completed two component review rounds, with 18 focused tests passing.
The reserved-period selection boundary, frozen transfer parameters, native-fit
cohort reporting, private retention gates, and explicit unavailable source pairs
were reviewed. Agreement remains source robustness, not independent predictive
confirmation.

## Integrated persona iteration 1

Reviewers: `review_qa` (implementation, failure recovery, helper behavior) and
`review_domain` (research protocol, chronology, comparability, evidence meaning).
The baseline fingerprint was recorded by the coordinator beginning `1389102c`.
Required compile and full unittest validation passed with 292 tests before the
integrated correction round. Ten isolated helper routing scenarios passed; the
legacy learning functions remained unchanged.

Accepted required findings and corrections:

- Bind stores to a source using metadata only and apply current retention before
  derived reads, including status and replay.
- Only models enrolled by a completed experiment can forecast. Verify their
  source, price basis, classification, protocol, and saved fit before writes.
- Preserve disappeared outcome evidence as explicit unavailable/restored events;
  carry collector unresolved withdrawals even when old rows remain retained.
- Choose the latest score revision before checking eligibility. Reevaluate the
  original fixed checkpoint dates and recheck paired outcome hashes; later dates
  cannot replace a mismatched original pair.
- Validate the immutable journal and checkpoint schema before repairing a
  checkpoint-to-reference interruption. Reference repair is idempotent; retraction
  affects only the pair that owns the active replacement.
- Propagate the frozen runtime budget through the incumbent comparator and retain
  durable candidate attempt/result records.
- Gate portability reads with exactly the explicitly configured report sources.
- Report active, historically scored, pending, and unavailable counts separately,
  plus source-gated Brier/prior, calibration, fallback, and period diagnostics.

Two minor helper findings were also addressed: the description distinguishes
configured refresh from legacy offline learning, and a dangling configuration
symlink fails closed instead of silently selecting legacy behavior.

## Integrated persona iteration 2

Both personas reviewed the corrected surface. Two remaining required findings
were accepted and addressed: source-wide retention resolution now rejects
conflicting attestations before portability reads, and corrected restoration now
publishes availability only after the correct score is durable. Regressions use
an actual synthetic portability report and interruptions before/after corrected
score publication. Both personas accepted the targeted fixes on frozen public-file fingerprint
`12539e805c68c14f8f7aab37e070307389456a30e2c43fb03378f855de7c693f`.
Final compile passed and the coordinator independently ran all 299 tests
successfully in 57.344 seconds. At review time, publication and external skill installation were the remaining
follow-up steps after private result inspection. Final real-data evidence
uses a new active store; pre-review engineering records are preserved, rejected
by the final contract where incomplete, and excluded from active evidence counts.

The configuration routing metadata limitation is documented explicitly: locating
the inventory reads configuration first. Mixed configurations and copies remain
FMP-derived deletion material; the implementation makes no claim that this initial
routing read has already passed the source-specific gate. No broad routing
refactor was added to this milestone.

## Delivery closeout

The accepted implementation is unchanged after final persona acceptance. Subsequent
public edits are this review log and a brief repository context index link. Private
final-cycle, no-change repeat, status, and integrity-replay artifacts are written
to the authoritative worktree's ignored storage with an explicit FMP deletion map.
At that delivery, the eToro research gate blocked credential access and the earlier
rejected probe had not been retried. No private source observations, metrics, hashes,
configurations, or model identifiers are included in this log.

## Profile compatibility follow-up (2026-09-14)

The bounded research-core developer added support for the existing legacy public
and private credential names alongside the canonical naming pair. Mixed naming
families and duplicates fail before transport construction; synthetic tests check
header mapping and preservation of the profile. The account holder's clarified
ordinary read-only authorization is recorded separately from the unchanged
model-training gate and its specific terms interpretation.

Both QA and domain personas accepted two iterations without required findings on
frozen patch fingerprint
`d5a0f44f0efab6e50d11df0e1d1e309ecfb73650c2db4eb6b2bbd2d79c70e53e`.
The 33 focused collector tests passed. Compile and all 301 tests passed in the
developer run (58.634 seconds) and independent coordinator run (58.801 seconds).
The subsequently authorized metadata-only probe returned HTTP 403 for the first
instrument search and stopped remaining requests; its cause remains unresolved.
The final documentation records this controlled finding without credential values,
raw responses, private evidence, or changes to the reviewed implementation.
