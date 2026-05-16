# Provider Boundary Decisions

Status: active  
Created: 2026-05-16

## Decision

Money-maker-3000 should unlock provider work in this order:

1. Historical market-data inputs.
2. Strategy backtest fixtures and deterministic performance diagnostics.
3. Read-only portfolio-state snapshots.
4. Reconciliation records that compare bot state to provider state.
5. Demo execution design.

Live execution remains out of scope.

## Rationale

Historical market data comes first because it lets the worker test strategy
logic, risk gates, cadence, and slippage assumptions without touching private
account-linked data. Portfolio state and reconciliation are more sensitive
because they can expose holdings, balances, position ids, account behavior, or
provider correlation ids.

This order keeps the next phase useful while preserving the current
simulation-first boundary:

- Strategies can be backtested on selected instruments.
- Provider adapters can stay read-only.
- Account-linked data can remain absent until a private storage design exists.
- Execution paths do not appear before audit, reconciliation, idempotency, and
  operator controls exist.

## Dashboard Storage Boundary

The eToro Dashboard should not durably store account-linked data.

Allowed for the dashboard:

- Live read-only provider responses normalized server-side.
- Short in-memory server cache/backoff metadata for rate-limit and freshness
  protection.
- Redacted, non-account-identifying UI DTOs.
- Local simulation bot config that contains strategy/budget/cadence choices
  only and no account ids, provider payloads, balances, holdings, position ids,
  order ids, or transaction history.

Not allowed without a new review:

- Durable storage of account-linked dashboard data.
- Raw provider payload persistence.
- Real portfolio exports, balances, holdings, position ids, order ids,
  transaction history, or reconciliation records in the dashboard repo.
- Browser-side access to credentials or privileged provider payloads.

If account-linked history is needed later, it belongs in a private worker-side
store with explicit retention, redaction, encryption, and audit rules. The
dashboard should consume redacted summaries only.

## Demo Execution Approval Meaning

Demo execution approval means permission to design and later run code that
sends mutation requests to eToro demo trading endpoints, such as opening,
closing, or cancelling demo orders.

That approval is not implied by approving backtests, read-only portfolio reads,
or simulation ledgers. Before demo execution is implemented, a separate review
must define:

- Demo environment only.
- Allowed strategy id and strategy version.
- Allowed instruments and markets.
- Maximum cash amount or unit size.
- Leverage fixed at 1.
- Order types allowed.
- Stop-loss/take-profit policy.
- Kill switch and pause behavior.
- Idempotency and duplicate-submit protection.
- Reconciliation after provider responses.
- Redacted immutable audit records.
- Confirmation flow for arming demo execution.

Until that explicit approval exists, `execute` mode stays rejected/disabled and
provider execution calls stay absent.

## First Historical Data Slice

Make the first provider-adjacent slice offline-fixture-first, not live eToro
fetch code.

Recommended source order:

1. Public daily OHLCV history for selected instruments, starting with
   `SPY`/`GLD` style ETF or equity symbols that already fit the simulation
   contract.
2. Small deterministic fixtures committed under `test/fixtures/market-history/`
   when they contain only public market bars and no account-linked data.
3. Larger generated downloads under ignored `data/private/market-history/`.

Fixture format:

```text
symbol,date,open,high,low,close,volume,source
SPY,2026-01-02,0,0,0,0,0,fixture
```

Rules:

- No account ids, balances, holdings, position ids, order ids, transaction
  history, provider user keys, raw account payloads, or screenshots.
- No execution quality, real PnL, win-rate, Sharpe ratio, or drawdown claims
  until assumptions and source coverage are reviewed.
- The first parser should validate schema, date ordering, numeric bars,
  source metadata, selected strategy/instrument compatibility, and deterministic
  replay.
- Live historical-data fetching can come after fixture parser tests exist.
