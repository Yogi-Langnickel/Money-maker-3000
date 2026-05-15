# Money-maker-3000

Simulation-first trading-bot worker scaffold for the eToro Dashboard.

The current worker does not call eToro, does not require secrets, and cannot
place demo or live orders. It produces redacted simulation DTOs that support the
dashboard's strategy dropdown, budget posture, trade logging, no-HFT guardrails,
and market/news context for portfolio positions.

## Run

```sh
npm run start
```

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
