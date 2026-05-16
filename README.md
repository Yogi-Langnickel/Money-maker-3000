# Money-maker-3000

Simulation-first trading-bot worker scaffold for the eToro Dashboard.

The current worker does not call eToro, does not require secrets, and cannot
place demo or live orders. It produces redacted simulation DTOs that support the
dashboard's strategy dropdown, selected instrument, budget posture, trade
logging, no-HFT guardrails, and market/news context for portfolio positions.

## Run

```sh
npm run start
```

The CLI runs a synthetic backtest report and accepts an explicit safe mode plus
strategy/instrument selection:

```sh
npm run start -- --mode backtest --strategy threshold-rebalance --symbol GLD --market COMMODITIES --instrument-class ETF
```

`--mode execute` is intentionally rejected until a separate reviewed execution
path exists.

To export a redacted dashboard DTO from an existing local simulation ledger
JSONL file:

```sh
npm run start -- --ledger-report .local/simulation-ledger.jsonl
```

The ledger report export only reads local synthetic records and preserves
blocked provider calls, absent execution routes, and blocked demo/live
execution.

## Validate

```sh
npm run check
```

## Synthetic Backtest

`src/backtest.mjs` produces deterministic simulation diagnostics over the
existing strategy registry. It does not call providers, does not calculate real
PnL, and keeps every decision skipped/blocked until a later reviewed provider
mode exists.

## Current Scope

- Predefined strategy registry only.
- Explicit run mode contract: `backtest` is allowed, `execute` is disabled.
- Selected instrument contract with symbol, market group, and instrument class.
- Canonical simulation contract in `src/simulation-contract.mjs`, using market
  groups `US_EQUITIES`, `AU_EQUITIES`, `FOREX`, and `COMMODITIES`.
- USD budget limits and loss stops.
- Low-frequency scheduling guardrails.
- Synthetic portfolio/news context.
- Redacted simulation trade log.
- Provider readiness metadata that explicitly blocks credentials, provider
  calls, account data, order previews, demo execution, and live execution.
- Synthetic backtest/performance summary for veto and warning coverage.
- No provider calls, no execution routes, no live trading.

## Safety Defaults

- Live execution: unavailable.
- Demo execution: unavailable.
- Leverage: 1 only.
- Shorts, copy trading, CFDs, derivatives, and crypto: blocked.
- News can explain context but cannot trigger orders.
