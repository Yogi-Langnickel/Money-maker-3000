# Financial and causal review gaps resolved for autonomous research

Detected 2026-10-03 on reviewed develop c17248c, before autonomous economic use.
Six defects escaped previous diagnostic/fixture review: completion-only feature
validation admitted late rows; ATR accepted inconsistent OHLC; terminal open
candidate positions omitted selling costs while the benchmark charged them;
finite input prices could overflow arithmetic; empty policies silently became
defaults; three closed trades could receive the highest hypothesis grade while
independent evidence was insufficient.

The implementation now checks every supplied row availability against decision
time, rejects inconsistent indicator OHLC, explicitly liquidates terminal
positions with costs, checks intermediate/output portfolio arithmetic and rejects
empty explicit policies. The legacy hypothesis grade requires at least ten closed
trades and labels remaining retrospective dependence. The new economic workflow
requires fixed genuinely prospective evidence, full completed round trips,
paired moving-block uncertainty and lifetime multiplicity controls. Higher
interval gates now name their availability/freshness meaning rather than pretending
to confirm a strategy; UTC weekdays never establish exchange sessions.

The detection gap was treating passing schema and directional diagnostics as
adequate assurance for monetary/causal claims. Prevention uses independent
financial/recovery personas, known-answer cash conservation and fee parity,
future-data perturbation, explicit malformed/late/overflow inputs, interrupted
publication, revision/withdrawal/restoration and isolated semantic replay.
Immutable history and current eligibility remain distinct. These controls transfer
to any pipeline joining point-in-time forecasts to financial simulations: validate
availability, match benchmark accounting and reject evidence laundering through
replay, corrections, restored favorable prior scores or expanded testing windows.

No broker action, account state, credentials or future-profit claim was involved.
Unknown eToro candle semantics remain an eligibility restriction, not a claimed
rights revocation or a reason to fabricate completed exchange-session evidence.

Final independent financial and recovery reviews approved the immutable v2
implementation after regressions covering original protocol cadence, withdrawn
pre-maturity horizon restoration, fixed contributor membership, original
comparison identity and actual interrupted hardlink publication. The 37 focused
tests and full 389-test suite passed; compilation, contract-manifest and
fixture-provenance checks passed independently for developer and coordinator.
No required review findings remain. See the review report for exact snapshot
identity and validation evidence; integration/publication remains coordinator-owned.
