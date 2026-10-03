# Continuous research milestone scope

## Standing eToro customer authorization (2026-10-03)

The customer confirms all necessary eToro storage/retention, private research and
model-use rights while active. Carry this forward without repeated requests.
Earlier written-exception blockers are superseded for this customer; this does
not claim a provider-written exception. See [the canonical customer policy and
remaining data-meaning constraints](feed-collection.md). Inactivity, revocation and provider
requests block use; account/execution/write boundaries remain unchanged.



The implementation joins bounded source-native historical research, immutable
forward forecasts and correction-aware scoring, source-pair portability evidence,
and a short configured workflow. Simulation and execution boundaries remain
unchanged. Historical scoring tests are labelled synthetic contract or
retrospective evidence; pending forecasts are not improvement evidence.

Simulation-only is the current development phase. The eventual product goal is
autonomous operation with a dedicated, bounded allocation, subject to explicit
go-live authorization and the evidence and risk gates in the
[autonomous trading roadmap](autonomous-trading-roadmap.md). No demo/live
execution or account access is authorized or implemented now. Offline strategy
refinement may generate candidates, but the active execution version must stay
frozen; any promotion requires an auditable reviewed gate.

The collector implementation now uses the support-confirmed instrument display
and type-catalog endpoints for authoritative ETF identity. Search omits the
redundant projected `instrumentId` and selects an exact symbol match. Support
confirmed the missing search fields are API behavior. Customer rights are
recorded above. This implementation has synthetic validation; no authenticated
collection or first eToro forward prediction was performed by this repair.
Remaining currency evidence and learner price-basis eligibility are described
in [collection evidence](feed-collection.md). Model fitting and portability must
not assume unknown source semantics.

A separate sanitized, non-retained market-data exploration establishes only
transport and parser shape. Search type-field variants continued to return an
exact SPY match with ID, display name, and exchange while omitting both text and
numeric type fields. A `OneDay/1` candle response exposed OHLCV fields. A
`OneDay/1000` response contained 1,000 candles; the strict parser accepted 999
complete rows from 2022-08-22 through 2026-09-18, excluded one unfinished
current candle, and found no duplicates. OHLC values were numeric; volume was
null in 188 rows from 2022-08-22 through 2023-05-19. Timestamps, session and
close conventions, price basis, corporate-action adjustments, and costs remain
unresolved. No values or payload were retained, and this does not make the feed
eligible for retention, research, prediction, or portability.

The historical exploration above established transport/parser shape only. Its
closed-rights interpretation is superseded by the standing customer authorization;
source-data meaning remains separate. VAS NAV remains supplemental until eligible
market-price history is available.

Private FMP/Kibot inputs, artifacts, reports, and their hashes are excluded from
this document and from Git. Current source approvals and retention attestations
must be checked before reproducing a private cycle. Delivery of code does not
complete an externally blocked real-data acceptance criterion, nor establish
predictive improvement or profitability.
