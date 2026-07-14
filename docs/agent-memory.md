# Money-maker-3000 Agent Memory

Status: active
Created: 2026-05-15
Updated: 2026-07-15

## Current Truth

- Money-maker-3000 is now a Python-first simulation worker core under
  `src/money_maker_3000/`; the previous Node runtime scaffold is retired.
- The worker does not call eToro, load credentials, preview orders, place demo
  orders, place live orders, or expose execution routes.
- CLI entrypoints are `PYTHONPATH=src python3.13 -m money_maker_3000.cli readiness`,
  `PYTHONPATH=src python3.13 -m money_maker_3000.cli backtest`,
  `PYTHONPATH=src python3.13 -m money_maker_3000.cli fixture-batch`, and
  `PYTHONPATH=src python3.13 -m money_maker_3000.cli ledger-report ...`.
- Strategy selection comes only from the predefined registry in
  `strategies.py`; arbitrary operator-provided strategy code is not allowed.
  The current registry contains `dca-cash-reserve`, `threshold-rebalance`,
  `volatility-band-accumulator`, `slow-trend-allocation`, and
  `news-aware-watchlist`.
- `contracts.py` owns the simulation config/run-mode/allocation contract.
  `backtest` is enabled for offline fixtures; `execute`, `trade`, and
  `trading` are rejected.
- Per-strategy parameter knobs are allowlisted in `contracts.py` and exposed
  through the predefined registry; CLI JSON input fails fast on unknown keys or
  out-of-range values before report generation.
- Internal bot allocation is separate from provider/demo balance. Provider/demo
  balance is read-only reconciliation context only, never a sizing source, and
  is redacted from ledger output.
- `risk.py` is a pure fail-closed risk gate before any strategy output could
  become an order intent. It blocks missing allocation, unknown provider state,
  missing reconciliation, stale data, invalid strategy version/instrument,
  incomplete policy, missing loss reconciliation, and absent execution routes.
- Risk policy enforces daily loss stop, weekly loss stop, max allocation
  drawdown, per-order cap, per-instrument exposure cap, max open positions,
  cash reserve floor, leverage fixed at 1, no shorts, no copy trading, and
  blocked CFDs/options/derivatives/crypto.
- Loss/drawdown stops are simulation-only until real reconciliation exists; DTOs
  state they are not evaluated against real PnL.
- `market_history.py` provides stdlib streaming CSV/date validation and
  single-pass history summaries.
- `market-history-sampling-quality.v1` reports calendar-agnostic observation
  coverage using O(n) interval arithmetic over already parsed bars. It counts
  weekday/weekend observations, potential weekdays strictly between adjacent
  observations, gaps over three calendar days, and the maximum calendar gap.
  Its weekday grid is not an exchange calendar, so potential gaps are never
  claimed as proof of missing market sessions. Readiness derives warnings from
  the validated gap/weekend counters, so even one weekend observation warns
  while retaining the `insufficient-history` state. Warnings do not change
  error-based readiness, and the projection omits observation dates.
- Offline historical fixture coverage now includes `SPY`, `GLD`, and `QQQ`
  daily synthetic short fixtures. Historical backtest reports include
  `periodDiagnostics` with `24h`, `1w`, `1m`, `1y`, `5y`, and `max` market
  change diagnostics for dashboard portfolio charting. The DTO explicitly
  blocks provider calls, account data, and execution, and carries
  market-history-only performance claims.
- Historical reports also include deterministic strategy-history diagnostics.
  Volatility-band reports price-decline-from-rolling-peak observations and slow-trend
  reports short/long average confirmation observations. Insufficient or
  malformed history and invalid parameters fail closed; candidate intent stays
  `skip`, provider calls stay blocked, and no execution/profitability claim is
  produced.
- `strategy-history-diagnostics.v3` adds a bounded walk-forward summary over
  every eligible historical endpoint for volatility-band and slow-trend
  strategies. `strategy-history-walk-forward.v2` partitions eligible endpoints
  into at most five deterministic, nonempty, balanced chronological folds and
  reports only fold index, observation/date coverage, state counts, and
  transition count. Candidate intent remains `skip`, provider calls are blocked,
  account data is absent, and no P/L or profitability claim is made.
- Offline batch validation reconstructs canonical walk-forward diagnostics from
  the historical bars retained in the report. Fold dates, state counts, within-
  and cross-fold transitions, and the terminal strategy state must match that
  authoritative reconstruction exactly; merely ordered ISO dates are not
  accepted as observation evidence.
- Canonical replay accepts only exact safe strategy-parameter dictionaries from
  every historical run. Scenario config `ok`/error evidence is structurally
  checked and must reproduce the report's valid/invalid counts and error
  histogram before it can preserve an original `invalid-defaulted` state.
- Volatility history distinguishes stable/no-trigger, active-decline trigger,
  and recovered-after-decline observations from the bounded offline window;
  every state remains a non-recommendation with candidate intent `skip`.
- The committed 202-bar weekday-only SPY slow-trend fixture is explicitly
  synthetic and SHA-256-pinned in CLI tests. It proves the default 50/200/3
  lookback/confirmation contract end to end without observed-market or
  profitability claims.
- Three committed 20-bar SPY volatility fixtures are explicitly synthetic and
  SHA-256 pinned in CLI tests. They prove the default stable/no-trigger,
  active-decline, and recovery-after-decline states. A checksum-pinned
  synthetic 20-bar `VAS` fixture proves the `AU_EQUITIES`/ETF instrument path.
  None is observed-market or performance evidence.
- Fixture-batch diagnostics preserve validated per-symbol strategy-history
  observations and aggregate only a state histogram. Readiness diagnostics
  expose the state and fail-closed boundary fields without copying raw metrics.
- All-threshold-rebalance fixture batches now emit deterministic historical
  relative-weight drift when target symbols and date windows match exactly.
  Missing targets, mixed windows, malformed prices, or altered provider safety
  fields fail closed. The DTO declares holdings/account data absent and keeps
  candidate intent `skip`.
- A checksum-pinned aligned 20-bar synthetic batch now proves threshold
  rebalance diagnostics and readiness across `SPY` as a U.S. ETF, `VAS` as an
  AU ETF, and `GLD` as an ETF in the `COMMODITIES` market. Exact normalized
  historical weights and drift percentages are pinned; no holdings, P/L,
  provider data, or execution is introduced.
- Offline fixture batch diagnostics can run multiple fixture files from a JSON
  manifest or repeated `SYMBOL=PATH` CLI entries, aggregate coverage/veto
  summaries, and emit per-symbol SHA-256/parser metadata/period diagnostics
  with provider calls blocked.
- Backtest readiness diagnostics emit `backtest-readiness.v1` for repeated
  fixture entries or manifest entries. Readiness checks strategy registry,
  allocation policy, run-mode policy, provider boundary, and offline fixtures;
  it returns `ready: true` only for offline-backtest-only diagnostics with
  provider calls blocked, execution routes absent, demo/live execution blocked,
  and account data absent.
- Backtests flow as `Iterable[Bar] -> Iterator[DecisionEvent] -> Summary` and
  emit diagnostics only: coverage, veto counts, config errors, cadence/risk
  gate behavior, fixture source/date coverage, input SHA-256, parser version,
  Python version, explicit started time, and event-unique historical run IDs.
- CLI synthetic backtests must honor the selected strategy, symbol, budget,
  allocation, and allowlisted strategy parameters instead of falling back to
  the default scenario batch.
- CLI money inputs for backtest-style commands must be finite numbers. Budget,
  bot allocation, max order, and provider demo balance must be positive;
  reserved allocation must be non-negative. Direct run DTOs sanitize invalid
  budget/allocation numbers before output while preserving config validation
  errors.
- Risk-policy, allocation, direct risk-state/order-intent, and reconciliation
  freshness validation must fail closed for malformed scalar or ISO-date
  inputs; invalid values must not escape as exceptions, `NaN`, or `Infinity`
  in DTOs. Nested JSON-compatible sequence containers, including tuples, must
  be traversed and normalized before DTO output. This rule is transferable
  across financial worker boundaries.
- GitHub Actions validates Python 3.11, 3.12, and 3.13 with compileall and the
  standard-library unittest suite.
- `contract_manifest.py` generates the canonical redacted
  `contracts/dashboard-simulation-contract.json` artifact from Python strategy,
  parameter, run-mode, budget, market, cadence, and safety metadata. CI checks
  exact generated bytes and fails on drift; dashboard consumers must pin the
  producer commit/hash instead of manually mirroring constants.
- Backtest reports include `strategy-intent-diagnostics.v1` candidate-order
  context only; intent remains skipped, provider calls are blocked, execution
  routes are absent, account data is absent, and no profitability claim is made.
- Historical fixture reports cap materialized period-diagnostics input at
  `maxFixtureRows` with a default of 10,000 rows. Larger files must opt in
  explicitly and still remain offline fixture inputs only.
- `fixture_provenance.py` generates and checks the canonical
  `contracts/market-history-fixture-provenance.json` inventory. Every committed
  CSV must be listed with an exact SHA-256, classification, symbol, row count,
  and source label. CI rejects unlisted, malformed, symlinked, relabeled, or
  drifted fixtures. Observed-data classification requires explicit source,
  license, attribution, and redistribution evidence.
- `ledger.py` writes/reads redacted local JSONL audit records with correlation
  IDs, strategy version, config hash, allocation IDs, risk decision, vetoes,
  data freshness, and provider call status. Appends use an exclusive sidecar
  writer lock so duplicate identity checks and JSONL writes are one critical
  section. It rejects sensitive key names and generic scalar values that look
  like emails, provider/account/order IDs, or token-like secrets.
- Ledger reporting recovers valid records from malformed JSONL without
  modifying the source. Integrity metadata is structurally validated and
  contains only allowlisted issue codes/counts with raw content absent.
  Rejected records mark the report `corrupted`; the CLI still emits controlled
  JSON but returns exit status 1. Clean and warning-only reports return 0.
- `reconciliation.py` builds simulation-only reconciliation records from
  caller-supplied synthetic/read-only inputs, redacts provider/account balance
  fields, derives freshness from supplied dates when present, rejects
  contradictory caller-supplied freshness, and converts available records into
  `RiskInputState`.
- Provider readiness metadata is metadata-only and explicitly blocks provider
  calls, credentials, account data, order previews, demo execution, and live
  execution.
- Do not report real PnL, win rate, drawdown, Sharpe ratio, execution quality,
  or profitability claims.

## Commands

- `PYTHONPYCACHEPREFIX=.pycache python3.13 -m compileall src tests`: compile the
  Python package and tests.
- `PYTHONPATH=src python3.13 -m unittest discover tests`: run the unittest suite.
- `PYTHONPATH=src python3.13 -m money_maker_3000.cli readiness --fixture SPY=tests/fixtures/market_history/spy-daily.csv --fixture GLD=tests/fixtures/market_history/gld-daily.csv --fixture QQQ=tests/fixtures/market_history/qqq-daily.csv --started-at 2026-05-15T00:00:00Z`:
  run the offline backtest readiness gate; current committed fixtures report
  `ready: true` with provider calls blocked and execution absent.
- `PYTHONPATH=src python3.13 -m money_maker_3000.cli backtest --history-csv tests/fixtures/market_history/spy-daily.csv --started-at 2026-05-15T00:00:00Z`:
  run the offline fixture diagnostics DTO, including selected-period market
  change diagnostics.
- `PYTHONPATH=src python3.13 -m money_maker_3000.cli fixture-batch --fixture SPY=tests/fixtures/market_history/spy-daily.csv --fixture GLD=tests/fixtures/market_history/gld-daily.csv --fixture QQQ=tests/fixtures/market_history/qqq-daily.csv --started-at 2026-05-15T00:00:00Z`:
  run batch offline fixture diagnostics with per-symbol metadata and period
  diagnostics.
- `PYTHONPATH=src python3.13 -m money_maker_3000.cli ledger-report .local/simulation-ledger.jsonl`:
  export a redacted dashboard DTO from an existing local synthetic ledger;
  malformed-record recovery never mutates the source and exits 1 when any
  record is rejected.
- `PYTHONPATH=src python3.13 -m money_maker_3000.contract_manifest --check`:
  verify the committed dashboard contract artifact matches `contracts.py`.
- `PYTHONPATH=src python3.13 -m money_maker_3000.fixture_provenance --check`:
  verify every committed market-history CSV and the generated provenance
  artifact match the canonical inventory.

## Performance And Context Notes

- Use standard library only unless a dependency is already present and reviewed.
- Keep CSV parsing streaming and reducer summaries single-pass.
- Keep cadence low-frequency only; current minimum evaluation interval is 240
  minutes.
- Period diagnostics may materialize the already parsed fixture bars within the
  `maxFixtureRows` cap to compute dashboard windows; do not use that as
  permission to persist or load account-linked data.
- Synthetic backtest run IDs, run-level correlation IDs, and nested
  `tradeLogEntry.correlationId` values include the same deterministic scenario
  suffix so generated ledger records can be appended without manual identity
  mutation. Ledger appends also hold the writer lock through duplicate identity
  checks to keep repeated worker attempts idempotent.
- Treat future provider rate limits as a budgeted resource before enabling any
  adapter.
- Keep local ledgers, generated reports, profiles, caches, and provider-like
  fixtures ignored.
- Use `.contextignore` to avoid loading generated ledgers, profiles, caches,
  reports, and build output during agent context retrieval.

## Read Next

- `AGENTS.md` for hard safety rules and validation commands.
- `docs/architecture.md` for Python module boundaries, allocation/risk,
  backtest, and ledger shape.
- `docs/provider-boundary-decisions.md` before provider, storage, portfolio,
  reconciliation, or execution work.
- `docs/bot-control-and-strategy-overview.md` for the simulation-only control
  model and predefined strategy catalog.
- `unblockme.md` before provider, git remote, or execution work.
