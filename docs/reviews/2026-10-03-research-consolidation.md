# Offline research consolidation review — 2026-10-03

The retained research branch tip `919082a` comprises six unique commits:
`370e912`, `7707f02`, `c35b122`, `f0a9d27`, `1548bea`, and `919082a`.
It was merged for review onto collector repair `d72f237` without conflicts,
preserving standing customer authorization and metadata lookup guidance.

The work adds already-local hypothesis/toolkit diagnostics and repairs research
withdrawal/restoration, evidence replay and portability validation. Every reported
hypothesis return/cash/drawdown is simulated; synthetic inputs prove mechanics
only. No collection, credential loading, account reads, execution or provider
writes were added. Existing registry and strategy boundaries remain intact.

Two reviewer personas assessed domain/data integrity and safety/governance in
planning and implementation iterations. Required feedback repaired numeric
integer overflow at hypothesis, toolkit and portability boundaries; bound observed
adjustment attestations to instrument basis; renamed arithmetic-average RSI/ATR
features accurately; and rejected unknown config fields before hashes. Synthetic inputs reject nested
retention/interpretation metadata, and optional control flags are strict booleans. Failure
records retain only validated enum instrument scope and never hash rejected
market datasets or arbitrary fields. Distinct malformed inputs with the same safe
scope/error intentionally deduplicate. Retained counts include blocked identities
by safe instrument independently of absent dataset hashes, conservatively
potentially overcounting blocked attempts across datasets.

The UTC weekday completion heuristic can reject legitimate exchange sessions.
This conservative limitation remains explicitly documented, with no fabricated
exchange calendar or stronger portability claim. Earlier draft Wilder-labelled
feature bundles require regeneration from permitted exact local input rather than
relabeling historical artifacts.

Validation: standard-library compileall and full unittest suite, canonical
contract-manifest and fixture-provenance checks, plus diff whitespace checks.
Developer `collector_repair` and independent personas `collector_domain_review`
and `collector_governance_review` completed planning and implementation review
iterations. All required findings above were repaired and both reviewers approved
the final implementation. The documented conservative UTC calendar heuristic and
sanitized failure-class counting limits were accepted with the stated rationale.

Final validation: 350 unittest tests passed in developer and independent primary
runs (primary: 61.516 seconds). Compilation, contract-manifest, fixture-provenance
and diff-whitespace checks all passed. Private source inputs and generated artifacts remain ignored.
