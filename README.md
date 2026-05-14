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

## Current Scope

- Predefined strategy registry only.
- USD budget limits and loss stops.
- Low-frequency scheduling guardrails.
- Synthetic portfolio/news context.
- Redacted simulation trade log.
- No provider calls, no execution routes, no live trading.

## Safety Defaults

- Live execution: unavailable.
- Demo execution: unavailable.
- Leverage: 1 only.
- Shorts, copy trading, CFDs, derivatives, and crypto: blocked.
- News can explain context but cannot trigger orders.
