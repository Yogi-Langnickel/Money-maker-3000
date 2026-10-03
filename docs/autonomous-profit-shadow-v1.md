# Autonomous Profit Research and Shadow Portfolio v1

This is private, simulation-only economic research. Its version is
`autonomous-profit-shadow.v1`. It connects the existing predefined
`slow-trend-allocation` state forecasts to frozen monetary selection and later
shadow portfolio evidence. It creates no broker orders or recommendations and
makes no future-profit promise. SPY/QQQ books use USD; VAS books use AUD. Each
store owns one source/instrument book. No FX conversion or provider balance is
used to size capital.

## Bounded workflow

`autonomous-cycle --config PATH` validates current rights and exact local inputs,
optionally invokes the existing separate GET-only collector, resolves its latest
immutable version, records source withdrawals/restorations, resumes every frozen
trial, makes eligible latest-endpoint shadow decisions, scores later observations,
and evaluates fixed economic checkpoints. It installs no service. Status, replay,
snapshot and restore never construct a credential reader or collect data.

```sh
PYTHONPATH=src python3.13 -m money_maker_3000.cli autonomous-status
PYTHONPATH=src python3.13 -m money_maker_3000.cli autonomous-cycle --config PRIVATE_CONFIG
PYTHONPATH=src python3.13 -m money_maker_3000.cli autonomous-status --config PRIVATE_CONFIG
PYTHONPATH=src python3.13 -m money_maker_3000.cli autonomous-replay --config PRIVATE_CONFIG
PYTHONPATH=src python3.13 -m money_maker_3000.cli autonomous-snapshot --config PRIVATE_CONFIG --snapshot PRIVATE_SNAPSHOT_DIRECTORY
PYTHONPATH=src python3.13 -m money_maker_3000.cli autonomous-restore --config PRIVATE_CONFIG --snapshot PRIVATE_SNAPSHOT_FILE --restore-root EMPTY_ISOLATED_PRIVATE_DIRECTORY
```

An unconfigured invocation reports `no-data`. Unqualified source-native data
reports `input-ineligible` and names each missing semantic fact. Short qualified
history reports `insufficient-development-history`. These are functioning
outcomes and do not fabricate models, trades or economic evidence.

The private JSON config requires `version`, `source`, `retention`, `storeRoot`,
`packetPath`, `collectorVersionPath`, `collectorRoot`, `legacyRoot` and
`profitRoot`. Absent paths are null. Optional `symbol` chooses one collector book;
optional `collection` is the existing fixed collector config (profilePath,
outputRoot, retention, interpretations, symbols; optional caFile). A configured
collectorRoot follows its latest immutable retrieval version automatically.
`collection: null` makes the complete cycle offline. Packet and collector paths
cannot supply competing inputs. Source and current retention must match exactly.

## Input meaning and causality

A normalized qualified packet has exact keys `version`, `source`, `symbol`,
`classification`, `retention`, `interpretation` and `observations`. Every row has
exact date/start/end/availableAt/open/high/low/close/volume keys. Null fields stay
null. Qualification requires finite positive consistent OHLC, explicit completed
session boundaries and availability, chronological nonoverlap, and at least
240 minutes per observation. It does not turn UTC weekdays or fixed nominal
seconds into exchange calendar proof.

Interpretation has identity/currency/sessionConvention/timestampMeaning/
adjustmentBasis/priceBasis plus evidence. Each qualification fact requires a
`verified` status and a nonempty source reference. Unknown source facts may be
retained under active rights but cannot train the economic pipeline. Exact source,
instrument, currency, classification, interpretation and retention are hashed
into each model and rechecked on forecasts, scores and comparison books.

The existing eToro `fromDate` is preserved in its source-native collector version.
It does not establish candle close, exchange session, publication availability,
price basis or adjustment semantics. The adapter therefore supplies null
start/end/availableAt and explicit unknown meaning, even when OHLC exists. Fresh
retrieval is not a fresh observation. Status separately reports completion,
availability, wall-clock freshness and unverified calendar freshness. Shadow
requires fresh completion as well as availability and the actual durable store
clock. No caller can backdate a prospective decision.

## Frozen economics

The three existing slow-trend parameter sets are fitted on the first 60% of the
pinned dataset, using only five-observation labels completed in that partition.
The next 20% selects the greatest simulated net edge over the matched passive
book, with lowest candidate index breaking a tie. Every candidate must finish,
including retained mechanical rejections, before selection. Only the selected
candidate enters the last 20% evaluation and double-cost check. The last partition
can reject or report insufficient evidence; it never selects an alternate model.
The entire original history is known at registration and stays retrospective.
Replaying it cannot become independent confirmation.

The frozen default book has 10,000 simulated currency units, 20% cash reserve,
80% target exposure, 10 bp each-way costs, 55% forecast threshold, five-observation
cadence, 10% initial-capital loss stop and 15% liquidation-equity drawdown stop.
Base research costs are capped at 500 bp so double-cost evaluation stays within
the simulator's 1,000 bp safe bound. Explicit invalid or empty policies reject.
Only predefined strategy models and immutable data enter this pipeline.

A signal fills at a later eligible open after its durable availability. Cash,
quantity, fees, turnover and liquidation equity use checked Decimal accounting.
Cash reserve is a fraction of post-fee equity at rebalance. Positive price drift
can exceed target exposure between observations; a corrective reduction fills
at the next causal open. A cash signal or risk exit takes priority over that
correction. Loss/drawdown thresholds observe completed close and sell at the next
open, including adverse gaps. They are never magical stop-price fills.

Candidate, cash and passive books share currency, initial capital, window and
fee rules. Passive buys once at the first window open with the same initial
allocation limit and holds until terminal selling costs; subsequent passive
weight drift is disclosed. It does not trade a constant-weight rebalance or
candidate risk stops. Liquidation equity includes the selling cost throughout;
terminal quantity is sold explicitly in every exposed book. Reports label net
returns, drawdown, turnover, exposure and costs as simulated. Brier/calibration
remain supporting evidence and cannot promote a reference.

## Prospective evidence and reference policy

The initial active reference is cash. The selected research model may collect
private challenger shadow evidence, including rejected retrospective candidates,
without becoming an active investment recommendation. A decision requires an
endpoint strictly beyond that model's frozen known-history end. Ordinary decisions
are at least five observations apart and at least 240 minutes apart. The original
forecast/probability, input hash and decision creation clock are immutable.
Later fills must start after that durable creation, and scores use genuinely
later available observations. Synthetic fixtures stay `synthetic-mechanics`.

The first later observations are bound even before the fifth arrives. First
maturation freezes each decision's five exact outcome dates. Corrections cannot
shift the horizon when an interior date disappears. Withdrawals dominate prior
favorable scores, including explicitly unavailable dates absent from the current
packet. Restoration requires a new durable score before requalification.
Feature corrections invalidate affected original forecasts rather than rewriting
their probabilities. Prior scores remain immutable; A→B→A creates a complete
supersedes chain and unchanged current retries reuse the latest event.

Each model tests exactly the first 105 prospective portfolio observations. Its
contributing decision membership and dates stay fixed even across unavailable revisions; later unrelated corrections cannot
change that comparison. Monitoring requires positive simulated net return versus
cash/passive, positive double-cost edge, no candidate risk stop, at least ten
completed full position round trips and twenty nonoverlapping five-observation
paired differences. Partial exposure reductions and terminal-only liquidation
are not those round trips. Circular moving-block resampling at ten and fifteen
observations addresses serial dependence; both conservative lower bounds must
pass. Finite sample/block-dependence assumptions still limit any conclusion.

Each protocol freezes family alpha `.05/(epoch*(epoch+1))`; three candidate
comparisons and three economic comparators each receive Bonferroni allocation.
The summable lifetime allowance avoids harmonic repeated testing. At most five
protocol epochs fit the fixed 300-comparison computational budget. A completed
fixed comparison cannot expand its sample to retry a favorable endpoint.
An active challenger must also beat the original comparator fixed by its first checkpoint, on every correction/retraction/restoration, on an identically
initialized matched date window with paired adjusted uncertainty. The active
reference stays frozen during challenger research. Corrected/unavailable/rejected
current evidence retracts it to cash, with crash-safe checkpoint/reference retries.

After the prior fixed comparison and sixty genuinely later observations finish,
the bounded coordinator may register a successor on longer history. It retains
the old reference/model protocol while the new challenger is tested. It never
retunes the previously inspected holdout or erases unsuccessful attempts.

## Recovery and private retention

Mode-0700 stores and mode-0600 files contain chained, immutable events. A source
binding, POSIX lock, checked sequence/digest/time chain and atomic fsync publication
protect compound operations. Lock-scoped caches reset between operations. A verified producer pending hardlink left by a crash is readable under a shared lock without mutation; the next writer removes only that exact private same-inode remnant under the exclusive lock. Arbitrary extra hardlinks remain blocked. Source binding and interrupted restore publication use the same checked recovery.
Trials resume after publication interruptions at start/model/result/final-result
without rerunning durable results, dropping candidates or selecting a partial grid.

Snapshots freeze the entire autonomous graph and any configured collector,
legacy research and profit artifacts under stable locks. Collector retrievals
must resolve to exact retained version hashes. Legacy experiments require their
exact row history and replay through the existing semantic verifier. A legacy
root without that source history fails explicitly. A profitRoot is either null
or `{ "root": REPORT_DIRECTORY, "configPath": FROZEN_INPUT_CONFIG }`; source
rights, the exact config, all artifact references and simulated semantics must
pass before snapshot inclusion. Profit configs are stored canonically.

Restore requires a separate empty private directory, or an exact interrupted
restore of the same snapshot. It restores source bindings and required locks,
checks the collector/legacy/profit graph, then recomputes fitted models, candidate
ranking, holdout economics, original forecasts, score revisions and checkpoint
semantics. Rehashing a false cash balance is insufficient. Restore never modifies
the original store. Active/revoked rights, provider deletion requests and source
specific retention rules gate original evidence, snapshots and restored copies.

## Observed input pass on 2026-10-03

Initial known data/inventory locations were empty. The existing permitted SPY
metadata/daily-history collector succeeded with the existing profile and trusted
CA bundle; no credentials or raw provider payload were retained or logged. It
retained 1,000 normalized source-native records spanning 2022-09-02 to 2026-10-02,
retrieved at 2026-10-03T09:24:34.124299Z. OHLC had no nulls; 179 volume fields were
null. No unresolved withdrawals were reported. Listing identity/currency were
independently reconciled against the issuer; API exchange group `NYSE` was not
substituted for exact `NYSE ARCA` session meaning.

The private offline cycle correctly reports healthy software, unavailable
completed-session freshness, ineligible research and no evaluated economics. Its
five missing facts are adjustment basis, completed OHLC availability, price basis,
session convention and timestamp meaning. Two independent offline repetitions
produced identical output and evidence bytes: one input event, zero models,
trials or decisions. Its semantic replay matched. A consistent snapshot covered
that event, its normalized collector version and retrieval journal; isolated
restore verified/replayed the same graph. This demonstrates retention, integration
and recovery mechanics. It does not demonstrate a profitable strategy or an
observed economic/model evaluation.

The retained ignored config/evidence lives under primary
`.local/autonomous-profit-shadow-v1`; collector versions live under primary
`data/etoro-autonomous-v1`. `offline-config.json` has collection disabled;
`config.json` explicitly enables the separate bounded collector. These private
files are excluded from Git and survive removal of the task checkout.

The coordinator may fast-forward the original preservation branch to published develop only after proving the committed unblockme.md blob is identical and the actual pre-existing dirty bytes and diff remain exactly unchanged. The durable develop worktree remains clean; this preserves the user edit while making the new implementation available at the original project path.
