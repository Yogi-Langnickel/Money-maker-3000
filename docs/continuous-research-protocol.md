# Continuous Strategy Research and Forward Evaluation v1

This workflow evaluates forecast probabilities and strategy states. It cannot
create orders, change balances, or provide profitability evidence. All historical
results, including the previously inspected holdout, are retrospective evidence.
Cross-provider agreement supports robustness to data-source choice. The providers
observe the same market events; agreement is not independent predictive evidence.

The configured command is `python3.13 -m money_maker_3000.cli research-cycle
--config <private-config.json>` with `PYTHONPATH=src`. The short Money Maker skill
uses the configured cycle automatically, retaining the previous fixed-split
workflow through `--legacy`. `research-status` reads existing status without
creating a store; `research-replay` verifies immutable record digests and model
contracts against the original dataset version without recomputing historical
metrics. The existing learner's verified replay remains available separately.

## Frozen research

The allowlisted existing volatility-band family has nine configurations; slow
trend has three. The protocol caps each family at 24 and freezes the five-valid-
observation label, candidate order, feature version, source interpretation,
retention, data version, dates, metrics, and selection rule. No new strategy code
or unreviewed feature is generated. At least three expanding chronological
windows are required: initial training ends at 40% of history, followed by
40–50%, 50–60%, and 60–70% development windows. Labels crossing each boundary
are purged. The remaining history is reserved from this cycle's selection;
it is still known retrospective evidence, not a fresh independent holdout.

Selection minimizes pooled development Brier error, with candidate order breaking
ties. Train-only smoothed probability priors, calibration bins, support counts,
and prior fallback frequency are retained for each window. Every candidate is
recorded, including interrupted or unattempted candidates. A 120-second monotonic
budget is checked between feature evaluations. Budget exhaustion never selects a
partial-grid winner. An interrupted experiment remains blocked for review on
retry. The actual prior saved learner is imported when supplied, keeping its
original fit and selection cutoff. Its metrics are labelled a known retrospective
comparator because its earlier selection may overlap this cycle's reserved dates.
If unavailable, the report explicitly says so and starts with the first frozen
configuration as the research reference.

The initial reference and at most two selected challengers are saved. The source,
currency, session, timestamp meaning, price type, adjustments, costs, and evidence
must be described. `instrumentVerified` means the operator has attested the
source-specific mapping; it is not cryptographic verification or a claim that all
feed semantics are resolved. Unknown session/adjustment/cost details remain
explicit and can limit portability while allowing approved source-native
historical diagnostics. No series is spliced and source checks remain enforced.

## Forward evidence and corrections

A forecast is saved only for the latest completed endpoint, with the actual
system creation time, source retrieval time, completion metadata, model identity,
input digest, state, probability, and pending five-observation outcome. Availability
metadata must match that dataset version. A replay is explicitly historical.
Observed, approved, sufficiently fresh input can be genuine forward evidence only
when the prediction exists before its fifth subsequent observation; scoring checks
that relationship again. The completion timestamp must fall on the observation
date or the following UTC date. Scoring conservatively excludes observations on
the retrieval UTC date, as well as future observations.

Retries preserve predictions and scores. A feature correction records an explicit
revision notice without changing the saved probability. Revised outcome windows
create score revisions with an unbroken supersedes chain, including A→B→A price
corrections. Feature revisions make checkpoint conclusions inconclusive. No
forecast whose outcome has already been observed is reclassified as forward.

Each protocol freezes a checkpoint at the first 60 matched completed forward
outcomes, minimum useful Brier improvement 0.005, two challenger comparisons, and
one research-reference replacement. Uncertainty uses a deterministic 2,000-draw
paired circular moving-block bootstrap with blocks of five observations and a
95% interval. This accounts for the known five-observation overlap; it does not
prove independence beyond the chosen block length. Supported requires the lower
bound above 0.005; rejected requires the upper bound below 0.005; otherwise the
comparison is inconclusive. Corrections create checkpoint revisions over the
original fixed dates, preserving previous conclusions and retracting a replacement
when corrected evidence no longer supports it. Replacement affects a research
reference only and never enables execution.

## Private configuration and retention

`continuous-research-config.v1` has exactly `version`, `dataRoot`, `storeRoot`,
`inventoryPath`, `interpretations`, `existingModels`, `availability`, `collection`,
and `portability`. The inventory is the existing approved
`money-maker-learning-inventory.v1`. Paths within it must remain below `dataRoot`
without symlinks. Store data is separated by source, symbol, and strategy.
`interpretations` maps source to the nine fields described above: the two boolean
attestations plus `currency`, `session`, `timestampMeaning`, `priceType`,
`adjustments`, `costs`, and `evidence`. `existingModels` maps
`source/symbol/strategy` to a previously validated learner artifact path.
`availability` maps `source/symbol` to `retrievedAt`, `completedAt`, and
`datasetSha256`, optionally `missingObservationDates` from explicit source withdrawal evidence. Missing availability blocks forward prediction while historical
research can continue.

`collection` is null or the fixed read-only collector configuration with
`profilePath`, `outputRoot`, `retention`, `interpretations`, and `symbols`.
Its rights and exact interpretation preflight runs before credential access.
There is no arbitrary command or executable hook. `portability` is null or
`{"reportPath": "<private saved portability report>", "reportSources": ["source-a", "source-b"]}`. The workflow loads it
only with current inventory retention attestations for exactly those two sources. Status and replay never
collect. Partial source access is reported explicitly.

FMP requires active-subscription attestation and no termination date before source
data, models, journal records, or portability reports are read, including status
and replay. Conflicting attestations for the same source block that source and
its reports; an active entry cannot overwrite an expired entry. Subscription
status is not detected automatically. All FMP data, hashes, encodings, models,
protocols, forecasts, scores, and mixed reports remain subject to deletion within
30 days after termination. Stores never automatically delete or assume that full
artifacts are exempt aggregates. Operator notification requires separately scoped
cleanup covering every retained source-derived location.

Evidence records are private JSON documents outside the simulation audit ledger.
An exclusive lock protects compound changes; mode-0600 temporary writes, fsync,
and atomic exclusive links preserve immutable canonical records. A crash before
publication leaves an unreferenced temporary; a crash after a started research
attempt blocks rerunning that experiment without review. There is no guarantee
against malicious same-user filesystem tampering. Private data and evidence are
ignored by Git; committed tests are synthetic contract tests only.

Collector output is consumed through `market-observations.v2` and a reviewed
close-only `ResearchObservation` adapter. Missing open/high/low/volume fields stay
`None`; both current strategy families use close only. A required close field,
price-basis contract, or source mapping mismatch blocks that dataset. Successful
collector versions are joined to the cycle's in-memory input list using immutable
version references; the original inventory is not rewritten. A successor cycle
can start after the previous fixed comparisons finish and at least 60 observations
arrive after its known-history endpoint. It inherits the current research
reference, and its forecast evidence begins at its own actual freeze time.

Final readers validate protocol, model, experiment, forecast, and score contracts,
including the six-observation outcome window, saved probability, exact Brier
calculation, chronological availability, and revision chain. Earlier engineering
artifacts missing the final contract are preserved but fail closed as inactive
pre-review evidence; they are not silently migrated into active forward counts.

A metadata-only source-name binding is checked before any derived store record is
read. Retention therefore cannot be bypassed by pointing a different source's
status call at the store. Previously matured outcomes that disappear become
explicitly unavailable. Collector snapshots retain original rows but carry
cumulative `unresolvedMissingDates`; the adapter excludes those observations
from current research and forwards the withdrawal overlay to scoring. Only an
explicit provider return clears a collector withdrawal. Status distinguishes
`scoredHistoricalTotal`, `validScored`, `pending`, and `currentlyUnavailable`;
withdrawn/revised feature evidence is excluded from active diagnostic metrics.
Checkpoint revisions report `fixedDateCount` and `currentEligiblePairs`, and a
retry repairs an interrupted reference update without reevaluating the outcome.

The coordinator reads configuration routing metadata to locate the authoritative
inventory before applying source-specific gates. Inline availability hashes and
existing-model paths mean that configuration is itself mixed source-derived
material; this routing read is not claimed to be retention-gated. Every private
configuration, copied report, pre-review artifact, and final FMP-derived store
must therefore appear in the bounded deletion map and remains subject to the
30-day termination obligation. No configuration or private hash is committed.

A restored outcome becomes available only after its corrected score is durable,
or after equality with the latest durable score is verified. An interruption
between those operations leaves the outcome conservatively unavailable; replay
repairs availability without exposing an older, superseded outcome.
