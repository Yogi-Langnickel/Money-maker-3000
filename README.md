# Money-maker-3000

Python-first, simulation-only trading-bot worker core for the eToro Dashboard.

The worker does not call eToro, load credentials, preview orders, expose
execution routes, or place demo/live orders. It emits versioned JSON DTOs with
the same safety semantics the dashboard expects: provider calls blocked,
execution absent, demo/live execution blocked, and account-linked data
redacted or absent.

## Run

Backtest readiness gate:

```sh
PYTHONPATH=src python3.13 -m money_maker_3000.cli readiness \
  --fixture SPY=tests/fixtures/market_history/spy-daily.csv \
  --fixture GLD=tests/fixtures/market_history/gld-daily.csv \
  --started-at 2026-05-15T00:00:00Z
```

The readiness report emits `backtest-readiness.v1` with `ready: true` only
when strategy registry validation, allocation policy, run-mode policy, provider
boundary, and offline fixtures all pass. It remains offline-backtest-only:
provider calls are blocked, execution routes are absent, demo/live execution is
blocked, and account data is absent. If a fixture gate fails, the command still
prints a redacted JSON report and exits non-zero.

Synthetic diagnostics:

```sh
PYTHONPATH=src python3.13 -m money_maker_3000.cli backtest
```

Offline fixture backtest:

```sh
PYTHONPATH=src python3.13 -m money_maker_3000.cli backtest \
  --history-csv tests/fixtures/market_history/spy-daily.csv \
  --strategy dca-cash-reserve \
  --symbol SPY \
  --market US_EQUITIES \
  --instrument-class ETF \
  --bot-allocation-usd 1000 \
  --provider-demo-balance-usd 1000000 \
  --started-at 2026-05-15T00:00:00Z
```

Offline fixture backtest with allowlisted strategy parameters:

```sh
PYTHONPATH=src python3.13 -m money_maker_3000.cli backtest \
  --history-csv tests/fixtures/market_history/spy-daily.csv \
  --strategy dca-cash-reserve \
  --strategy-params-json '{"fixedOrderUsd":125,"maxOrdersPerWeek":2}' \
  --started-at 2026-05-15T00:00:00Z
```

Historical fixture backtests include a `periodDiagnostics` DTO for dashboard
portfolio charting. It covers `24h`, `1w`, `1m`, `1y`, `5y`, and `max` with
market-history change values, coverage state, source, and freshness-safe
metadata. These are market context diagnostics only, not bot profitability,
real P/L, win-rate, drawdown, Sharpe ratio, or execution-quality metrics.
The period-diagnostics fixture buffer is capped by `--max-fixture-rows`, which
defaults to 10,000 rows.

`--mode execute`, `--mode trade`, and `--mode trading` are rejected before any
work runs.

Ledger report:

```sh
PYTHONPATH=src python3.13 -m money_maker_3000.cli ledger-report .local/simulation-ledger.jsonl
```

Optional stdlib profiling:

```sh
PYTHONPATH=src python3.13 -m money_maker_3000.cli --profile profile.txt backtest
```

## Validate

```sh
PYTHONPYCACHEPREFIX=.pycache python3.13 -m compileall src tests
PYTHONPATH=src python3.13 -m unittest discover tests
```

GitHub Actions runs the same compile and standard-library test gates on Python
3.11, 3.12, and 3.13 for pull requests and pushes to `develop`.

## Current Scope

- Python package under `src/money_maker_3000/`.
- Predefined strategy registry only.
- Strategy parameters are schema allowlisted per predefined strategy; unknown
  keys or out-of-range values fail before CLI report generation.
- Explicit run mode contract: `backtest` is allowed, `execute` is disabled.
- Internal allocation ledger fields separate bot allocation from any
  provider/demo account balance: `bot_allocation_usd`, `reserved_usd`,
  `available_usd`, `max_order_usd`, and strategy allocation IDs.
- Provider/demo balance is read-only reconciliation context only and is never a
  sizing source or persisted balance.
- Pure risk gate runs before any strategy output could become an order intent
  and fails closed on missing reconciliation, unknown provider state, stale or
  missing data, invalid strategy/instrument/version, incomplete policies, or
  missing allocation.
- Risk policy covers daily loss stop, weekly loss stop, max allocation
  drawdown, per-order cap, per-instrument exposure cap, max open positions,
  cash reserve floor, leverage fixed at 1, and blocked shorts/copy
  trading/CFDs/options/derivatives/crypto.
- Loss and drawdown stops are simulation-only until real reconciliation exists;
  outputs state they are not evaluated against real PnL.
- Streaming CSV ingestion uses stdlib `csv` and date validation. Backtests flow
  as `Iterable[Bar] -> Iterator[DecisionEvent] -> Summary`.
- Offline market-history diagnostics now emit selected-period context for
  `24h`, `1w`, `1m`, `1y`, `5y`, and `max`, suitable for dashboard
  instrument rows without provider calls or account data.
- Historical fixture reports fail fast above `maxFixtureRows` instead of
  materializing unbounded period-diagnostics input.
- Backtest readiness reports verify the offline fixtures and safety posture
  before running diagnostics, and return suggested safe backtest commands for
  fixtures that passed readiness.
- Backtest output is diagnostics only: coverage, veto counts, config errors,
  cadence/risk gate behavior, fixture source/date coverage, deterministic
  SHA-256 input metadata, parser version, Python version, and explicit
  `started_at`.
- Backtests include `strategy-intent-diagnostics.v1` candidate-intent context,
  but intent remains skipped while risk/provider/execution gates are blocked.
- Redacted local JSONL ledger appends are single-writer locked so run and
  correlation duplicate checks happen in the same critical section as the
  append.
- No real PnL, win-rate, Sharpe, drawdown, execution quality, profitability
  claims, provider calls, or account-linked persistence.

## Safety Defaults

- Live execution: unavailable.
- Demo execution: unavailable.
- Credential loading: unavailable.
- Provider mutation endpoints: absent.
- Order preview: absent.
- Leverage: 1 only.
- Shorts, copy trading, CFDs, derivatives, options, and crypto: blocked.
- News/social context can explain context but cannot trigger orders.
