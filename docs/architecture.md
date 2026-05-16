# Money-maker-3000 Architecture

Status: Python-first core

Money-maker-3000 runs as a separate simulation worker/service from the eToro
Dashboard. The dashboard consumes versioned, redacted JSON DTOs; the worker owns
strategy registry validation, allocation policy, pure risk gating, fixture
backtests, audit ledgers, and eventual reconciliation design.

## Python Core

The runtime package lives under `src/money_maker_3000/`.

- `contracts.py`: simulation config, run-mode policy, allocation policy, and
  validators.
- `strategies.py`: predefined registry and registry validator.
- `risk.py`: pure fail-closed risk gate.
- `market_history.py`: stdlib streaming CSV parser and single-pass history
  accumulator.
- `backtest.py`: `Iterable[Bar] -> Iterator[DecisionEvent] -> Summary`.
- `ledger.py`: redacted append/read/report JSONL audit records.
- `providers.py`: metadata-only provider boundary and disabled execution
  gateway contract.
- `cli.py`: `backtest` and `ledger-report` commands.

There is no Node runtime requirement.

## Allocation Boundary

The bot has an internal allocation model independent of provider/demo account
balances:

- `allocationId`
- per-strategy allocation IDs
- `botAllocationUsd`
- `reservedUsd`
- `availableUsd`
- `maxOrderUsd`

The current future-demo planning defaults are a USD 10,000 bot allocation, USD
500 maximum order size, 20 maximum open positions, 10% maximum daily drawdown,
and 25% maximum weekly drawdown. These values are documentation inputs only
until demo execution is separately implemented and approved.

Provider/demo balances are read-only reconciliation inputs only. They are never
used as sizing inputs and are redacted from audit records and reports.

## EC2 And DynamoDB Direction

When the worker moves to a dedicated EC2 instance, DynamoDB is the preferred
durable worker-side store. It fits the expected access pattern better than
Google Sheets because the bot needs conditional writes, idempotency keys,
append-only audit events, state transitions, and reliable reconciliation
history.

Secrets on EC2 should be loaded from AWS Secrets Manager through IAM. Google
Sheets can still receive redacted reporting exports for human review, but the
sheet must not be the source of truth for trade state or reconciliation.

Recommended retention is 7 years for order/risk/reconciliation audit records,
90 days of detailed portfolio snapshots compacted into daily summaries, 30 days
maximum for any explicitly approved redacted raw-provider debugging payloads,
and no default persistence of raw provider payloads.

## Risk Gate

The risk engine runs before any strategy output could become an order intent and
fails closed when inputs are missing or invalid. It blocks on missing allocation,
unknown provider state, missing reconciliation, stale or missing data, invalid
strategy version/instrument, incomplete risk policy, missing loss
reconciliation, and absent execution routes.

The policy enforces daily loss stop, weekly loss stop, max allocation drawdown,
per-order cap, per-instrument exposure cap, max open positions, cash reserve
floor, leverage fixed at 1, no shorts, no copy trading, and blocked CFDs,
options, derivatives, and crypto.

Loss and drawdown stops are simulation-only until real reconciliation exists.
DTO diagnostics explicitly state they are not evaluated against real PnL.

## Provider Boundary

`src/money_maker_3000/providers.py` exposes metadata only. Provider calls,
credentials, account data, market-data adapters, order previews, demo execution,
live execution, and execution routes are blocked or absent.

`DisabledExecutionGateway` is an interface/contract stub that raises
`PermissionError` for order preview and submit attempts. It must not become an
adapter without a separate provider/execution review.

## Market History And Backtests

Historical market data is offline fixture only. The parser uses stdlib `csv`
with date/schema validation and exposes an iterator-based bar parser. Reducers
are single-pass accumulators and avoid materializing bars except in tiny fixture
test helpers.

Backtest DTOs include deterministic metadata:

- strategy ID/version
- config version
- data source
- first/last date
- row count
- input SHA-256
- parser version
- Python version
- explicit `startedAt`

Backtest output is diagnostics only: coverage, veto counts, config errors,
cadence/risk gate behavior, and fixture source/date coverage. It must not
report real PnL, win rate, Sharpe ratio, drawdown, execution quality, or
profitability claims.

## Audit Ledger

Audit records are local append-only JSONL at the application layer. They are
redacted before persistence and contain:

- correlation ID
- strategy version
- config hash
- allocation ID and strategy allocation ID
- risk decision
- veto reasons
- data freshness
- provider call status

They must not contain account IDs, balances, holdings, position IDs, order IDs,
raw provider payloads, credentials, or account-linked history.

## Provider Input Order

Provider-adjacent work should proceed in this order:

1. Offline historical market-data fixtures.
1. Deterministic strategy backtest diagnostics.
1. Read-only portfolio-state snapshot design.
1. Reconciliation records.
1. Demo execution design.

Demo execution approval means permission to design and call eToro demo mutation
endpoints. That approval is not implied by backtests, read-only provider work,
or simulation ledgers. Until a separate approval exists, `execute` mode stays
rejected and provider execution calls remain absent.

When demo execution is later approved, start with limit orders and mandatory
order preview. Market orders should remain disabled until slippage controls,
preview semantics, and reconciliation are proven.

## Not Implemented

- Provider adapters.
- Credential loading.
- Durable account-linked storage.
- Demo order preview or execution.
- Live trading.
