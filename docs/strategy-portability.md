# Strategy portability v1

`portability.py` measures how the same frozen strategy and fitted probability model
behave on separate local feeds. It never concatenates providers, changes the
learner's source identity check, or enables execution. The resulting verdict is
specific to an instrument, feed pair, strategy family, selected parameters,
comparison dates, and preset tolerances.

Each private `feed-description.v1` records instrument identity, currency, session
boundaries, timestamps, price type, adjustments, costs, retention, and evidence.
Each field is `verified`, `documented`, or `unresolved`; explanations are separately
`documented`, `statistical-association`, or `unresolved`. An unresolved exact cause
is not a rejection. Missing meaning needed for close-based features or labels
makes compatibility inconclusive. Costs remain reported context: these diagnostic
strategies do not model execution costs. Both allowlisted families use closes
only, so explicitly absent OHLC/volume in the separate v2 observation contract
remain absent rather than being invented.

## Documented source differences

Kibot's `unadjusted=1` request preserves prices without split or dividend
back-adjustments. The default adjusted series is different; its dividend
adjustments cannot explain an explicitly unadjusted acquisition. Daily timestamps
use session dates, and the intraday session filter does not alter daily requests.
[Kibot history request](https://www.kibot.com/api/history-request.html).

Kibot documents a regular-session last-trade close and describes differences from
other vendors' closing conventions. That identifies a possible mechanism; it does
not prove the cause of an FMP or eToro discrepancy.
[Kibot adjustments](https://www.kibot.com/file-format/adjusted-vs-unadjusted-data.html),
[Kibot comparison guide](https://www.kibot.com/quality/data-comparison.html).

FMP's particular `historical-price-eod/non-split-adjusted` endpoint documents daily
OHLCV without split adjustment. Its reviewed endpoint page does not establish the
precise close print, session inclusion, timezone, or dividend treatment. Those
remain unresolved in the private comparison descriptor.
[FMP endpoint documentation](https://site.financialmodelingprep.com/developer/docs/stable/historical-price-eod-non-split-adjusted).

An eToro descriptor must use its verified instrument mapping and reviewed price,
session, adjustment, and retention meaning. Missing reuse rights cannot be replaced
by an API success response. Missing observations produce an inconclusive pair
entry. A VAS NAV series is supplemental and cannot be silently presented as market
prices.

## Frozen evaluation

`freeze_protocol(path, ...)` writes immutable dates, bounded candidate grids,
selection rules, minimum sample requirements, and strategy-specific tolerances
before `evaluate_pair(...)` consumes the reserved comparison. The actual creation
clock is retained as `frozenAt`; caller timestamps cannot backdate a prospective
reservation. Already available or inspected history uses
`retrospective-historical-robustness`. Even a prospective reservation is a
cross-source robustness check, never independent predictive confirmation.

For each direction, source-only train and selection periods choose a configuration
from the existing learner's grid. Five-observation outcomes crossing either cutoff
are purged. Selection never consumes target outcomes or reserved outcomes. The
selected source fit is applied unchanged to both feeds, and both fit digests are
recorded. A second, explicitly descriptive comparison independently fits the same
parameters on each feed's training period. Both fits use the same maximum-grid
warmup and training cutoff, with sample counts and feature/outcome dates recorded
for each source. Identical histories therefore produce identical native fits;
different history coverage remains explicit. It does not retune parameters and is
not covered by the same-model-transfer verdict.

Returns and labels use each provider's own observation sequence. Daily and
five-observation returns are comparable only when start and end dates match;
future labels likewise require the same fifth-observation date. Excluded and
unpaired dates are counted, with preset maximum mismatch fractions. No missing
dates are silently compressed into a common-calendar training series. The protocol
also freezes `expectedLastObservation`, defaulting to the evaluation end date.
A non-session end requires an explicitly supplied last session date and calendar
evidence frozen with the protocol. Both feeds must contain that endpoint and the
`as_of` date must reach the full evaluation end; otherwise the reserved period is
incomplete and the verdict remains inconclusive, even when a truncated common
prefix has enough samples. The report exposes expected and actual endpoints.

The report includes directional agreement, return differences, return correlation,
fixed lags of minus two through plus two observations, price differences,
ordinary dates, largest absolute and relative differences, stress dates, and
available reviewed early-close and corporate-action dates. Lag associations do not
realign observations or establish causation. Stress is defined before evaluation
as either feed moving at least 2% in a day. Strategy comparisons include states,
entry trigger dates, probabilities, five-observation labels, Brier diagnostics,
and case-specific state/label evidence. Entry triggers inspect the previous state
before the comparison boundary.

The initial decision thresholds are policy choices, not empirically optimized:

| Metric | Volatility band | Slow trend |
| --- | ---: | ---: |
| Minimum state agreement | 99% | 97% |
| Minimum entry-trigger Jaccard | 95% | 90% |
| Minimum outcome-label agreement | 98% | 95% |
| Maximum mean probability difference | 0.01 | 0.02 |
| Minimum five-observation direction agreement | 98% | 95% |
| Maximum 95th-percentile five-observation return difference | 0.001 | 0.003 |
| Minimum stress-state agreement | 95% | 90% |
| Maximum unpaired prediction/outcome fraction | 1% | 3% |

Both directions need at least 100 comparable matured outcomes, ten stressed
observations, and five trigger transitions in the union. Verified wrong instrument
or currency rejects the comparison. Missing required meaning or insufficient
samples is inconclusive, with any measured breaches still shown. With sufficient
meaning and samples, a threshold breach rejects portability for that frozen
comparison; otherwise it is supported. Exact price matching is never required.
Early-close/action-date coverage and causal explanations remain explicit
limitations; missing event dates are not invented.

There is no normalization in v1. All input observations are preserved. A future
normalization needs its own reviewed version, original-observation retention,
and training-only fitting before reserved evaluation.

## Private evidence and retention

The functions accept validated local observations only. Provider credentials,
requests, and account data are outside this module. `write_report` atomically
creates a private artifact without replacing existing evidence. `load_report`
requires current retention state before reading derivatives. FMP comparisons use
the existing operator-attested active subscription; there is no automatic
subscription detection and no invented daily re-attestation. On termination,
source observations, hashes, selected fits, mixed reports, backups, and other
encodings remain subject to the existing deletion deadline. Set current inventory
state before any later read or replay; do not rely on the old report's attestation.

Local generated reports and replay scripts belong under ignored `data/private/`.
No observed FMP values, data digests, fitted models, or mixed evidence are included
in Git. Private reports record unavailable pair/strategy combinations explicitly.
Cross-provider agreement supports robustness to the data source because the feeds
observe the same market events. Predictive confirmation requires the separate
forward journal and genuinely new, matured observations.
