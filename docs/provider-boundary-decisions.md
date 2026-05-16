# Provider Boundary Decisions

Status: active
Created: 2026-05-16
Updated: 2026-05-17

## Decision

Money-maker-3000 remains Python-first, offline-fixture-first, and
simulation-only.

Provider work should unlock in this order:

1. Historical market-data inputs.
1. Strategy backtest fixtures and deterministic diagnostics.
1. Read-only portfolio-state snapshots.
1. Reconciliation records that compare bot state to provider state.
1. Demo execution design.

Live execution remains out of scope. Demo execution remains disabled.

## Allocation And Reconciliation

Internal bot allocation is separate from provider/demo balance. The worker
models allocation with bot allocation, reserved amount, available amount,
per-order cap, allocation ID, and strategy allocation IDs.

Provider/demo balance is a read-only reconciliation input only. It must never
size orders, budgets, or risk caps. It must not be persisted as a balance in
audit records or dashboard DTOs.

Risk gates fail closed until provider state is known read-only and
reconciliation is available. Loss/drawdown stops stay simulation-only until real
reconciliation exists, and DTOs must say they are not evaluated against real
PnL.

## Dashboard Storage Boundary

The eToro Dashboard should not durably store account-linked data.

Allowed for the dashboard:

- Live read-only provider responses normalized server-side.
- Short in-memory server cache/backoff metadata for rate-limit and freshness
  protection.
- Redacted, non-account-identifying UI DTOs.
- Local simulation bot config that contains strategy, allocation, budget, and
  cadence choices only and no account IDs, provider payloads, balances,
  holdings, position IDs, order IDs, or transaction history.

Not allowed without a new review:

- Durable storage of account-linked dashboard data.
- Raw provider payload persistence.
- Real portfolio exports, balances, holdings, position IDs, order IDs,
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
- Allowed strategy ID and strategy version.
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

## Historical Data Rules

The first provider-adjacent slice is offline fixture data, not live eToro fetch
code.

Fixture format:

```text
symbol,date,open,high,low,close,volume,source
SPY,2026-01-02,1,2,1,2,100,fixture
```

Rules:

- Use stdlib streaming CSV/date validation.
- Expose iterator-based bar parsing.
- Use single-pass reducers for summaries.
- Commit only tiny public market fixtures.
- Keep larger generated downloads under ignored local data paths.
- Do not include account IDs, balances, holdings, position IDs, order IDs,
  transaction history, provider user keys, raw account payloads, or screenshots.
- Do not report real PnL, win rate, Sharpe ratio, drawdown, execution quality,
  or profitability claims.
