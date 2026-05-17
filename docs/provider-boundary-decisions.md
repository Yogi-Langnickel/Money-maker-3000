# Provider Boundary Decisions

Status: active
Created: 2026-05-16
Updated: 2026-05-18

## Decision

Money-maker-3000 remains Python-first, offline-fixture-first, and
simulation-only.

Provider work should unlock in this order:

1. Historical market-data inputs.
1. Strategy backtest fixtures and deterministic diagnostics.
1. Read-only portfolio-state snapshots.
1. Simulation-only reconciliation records that compare bot state to
   caller-supplied synthetic/read-only provider context.
1. Demo execution design.

Live execution remains out of scope. Demo execution remains disabled.

## Allocation And Reconciliation

Internal bot allocation is separate from provider/demo balance. The worker
models allocation with bot allocation, reserved amount, available amount,
per-order cap, allocation ID, and strategy allocation IDs.

Current planning inputs:

- Initial bot allocation: USD 10,000.
- Maximum order size: USD 500.
- Maximum open positions: 20.
- Maximum daily drawdown stop: 10%.
- Maximum weekly drawdown stop: 25%.
- Instrument choice: user-selected instrument, constrained by the strategy,
  risk policy, and provider capability registry.

Provider/demo balance is a read-only reconciliation input only. It must never
size orders, budgets, or risk caps. It must not be persisted as a balance in
audit records or dashboard DTOs.

Risk gates fail closed until provider state is known read-only and
reconciliation is available. Loss/drawdown stops stay simulation-only until real
reconciliation exists, and DTOs must say they are not evaluated against real
PnL.

Current reconciliation core is offline and simulation-only. It accepts
caller-supplied synthetic/read-only context, redacts provider balance and
account-linked fields, performs no provider calls, and can feed `RiskInputState`
only when loss, drawdown, exposure, and open-position inputs are complete.

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

## EC2 Worker Storage Direction

If the bot runs on a dedicated EC2 instance, DynamoDB is the recommended
worker-side durable store for account-linked worker records.

Recommended shape:

- DynamoDB is the source of truth for bot allocations, strategy allocations,
  order intents, provider order references, reconciliation snapshots, risk
  decisions, and immutable audit events.
- AWS Secrets Manager is the source of credentials and provider secrets once
  the worker runs on EC2. Secrets must not be stored in environment variables
  except local development placeholders.
- Google Sheets may be used as a reporting/export mirror for trade details and
  reconciliation summaries, but not as the source of truth. Sheets exports
  should be append-only or replace-by-report-window, redacted, and recoverable
  from DynamoDB.
- DynamoDB writes should use conditional expressions for idempotency keys,
  order intent state transitions, reconciliation checkpoints, and duplicate
  submit protection.
- Account-linked DynamoDB tables must use encryption at rest, least-privilege
  IAM, and no raw provider payload persistence unless explicitly approved.

Recommended retention:

- Order intents, submitted demo orders, cancellations, risk decisions, and
  reconciliation events: retain for 7 years once any real account access is
  contemplated. This gives enough audit history to reconstruct behavior.
- High-frequency portfolio snapshots: retain detailed snapshots for 90 days,
  then compact to daily summaries for 7 years.
- Raw provider responses: do not persist by default. If later required for
  debugging, store redacted payload excerpts for 30 days maximum.
- Google Sheets report mirrors: keep the current year plus previous year, or
  regenerate from DynamoDB when needed.
- Local development JSONL/SQLite artifacts: retain 30 to 90 days and keep
  under ignored private paths.

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

Current demo-execution planning inputs are recorded for future design only:

- Initial demo allocation: USD 10,000.
- Maximum order size: USD 500.
- Maximum open positions: 20.
- Daily drawdown stop: 10%.
- Weekly drawdown stop: 25%.
- Instrument can be selected by the user, subject to allowlist and risk gates.

Order type recommendation:

- Start demo execution with limit orders only. Limit orders cap entry price and
  make preview, idempotency, and reconciliation easier to reason about.
- Add market orders later only behind a separate capability flag, tighter
  slippage guard, and explicit user opt-in.

Order preview recommendation:

- Require an order preview phase before demo submit. Preview should return the
  normalized order intent, allocation impact, risk checks, estimated fees if
  available, required idempotency key, and expiry time.
- Submit should require the preview ID and fail if the preview is stale,
  modified, already consumed, or no longer passes reconciliation/risk checks.

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
