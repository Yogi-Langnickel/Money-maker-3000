# Money-maker-3000 Agent Memory

Status: active
Created: 2026-05-15
Updated: 2026-05-18

## Current Truth

- Money-maker-3000 is now a Python-first simulation worker core under
  `src/money_maker_3000/`; the previous Node runtime scaffold is retired.
- The worker does not call eToro, load credentials, preview orders, place demo
  orders, place live orders, or expose execution routes.
- CLI entrypoints are `PYTHONPATH=src python3.13 -m money_maker_3000.cli backtest`
  and `PYTHONPATH=src python3.13 -m money_maker_3000.cli ledger-report ...`.
- Strategy selection comes only from the predefined registry in
  `strategies.py`; arbitrary operator-provided strategy code is not allowed.
- `contracts.py` owns the simulation config/run-mode/allocation contract.
  `backtest` is enabled for offline fixtures; `execute`, `trade`, and
  `trading` are rejected.
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
- Backtests flow as `Iterable[Bar] -> Iterator[DecisionEvent] -> Summary` and
  emit diagnostics only: coverage, veto counts, config errors, cadence/risk
  gate behavior, fixture source/date coverage, input SHA-256, parser version,
  Python version, and explicit started time.
- `ledger.py` writes/reads redacted local JSONL audit records with correlation
  IDs, strategy version, config hash, allocation IDs, risk decision, vetoes,
  data freshness, and provider call status.
- `reconciliation.py` builds simulation-only reconciliation records from
  caller-supplied synthetic/read-only inputs, redacts provider/account balance
  fields, and converts available records into `RiskInputState`.
- Provider readiness metadata is metadata-only and explicitly blocks provider
  calls, credentials, account data, order previews, demo execution, and live
  execution.
- Do not report real PnL, win rate, drawdown, Sharpe ratio, execution quality,
  or profitability claims.

## Commands

- `PYTHONPYCACHEPREFIX=.pycache python3.13 -m compileall src tests`: compile the
  Python package and tests.
- `PYTHONPATH=src python3.13 -m unittest discover tests`: run the unittest suite.
- `PYTHONPATH=src python3.13 -m money_maker_3000.cli backtest --history-csv tests/fixtures/market_history/spy-daily.csv --started-at 2026-05-15T00:00:00Z`:
  run the offline fixture diagnostics DTO.
- `PYTHONPATH=src python3.13 -m money_maker_3000.cli ledger-report .local/simulation-ledger.jsonl`:
  export a redacted dashboard DTO from an existing local synthetic ledger.

## Performance And Context Notes

- Use standard library only unless a dependency is already present and reviewed.
- Keep CSV parsing streaming and reducer summaries single-pass.
- Keep cadence low-frequency only; current minimum evaluation interval is 240
  minutes.
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
- `unblockme.md` before provider, git remote, or execution work.
