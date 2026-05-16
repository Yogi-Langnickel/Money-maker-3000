# Money-maker-3000

Python-first, simulation-only trading-bot worker core for the eToro Dashboard.

The worker does not call eToro, load credentials, preview orders, expose
execution routes, or place demo/live orders. It emits versioned JSON DTOs with
the same safety semantics the dashboard expects: provider calls blocked,
execution absent, demo/live execution blocked, and account-linked data
redacted or absent.

## Run

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

## Current Scope

- Python package under `src/money_maker_3000/`.
- Predefined strategy registry only.
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
- Backtest output is diagnostics only: coverage, veto counts, config errors,
  cadence/risk gate behavior, fixture source/date coverage, deterministic
  SHA-256 input metadata, parser version, Python version, and explicit
  `started_at`.
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
