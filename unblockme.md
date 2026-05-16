# Money-maker-3000 Unblock Notes

Status: non-blocking for Python historical market-data/backtest implementation
Created: 2026-05-15

Simulation and historical market-data/backtest work can continue locally. These
items block account-linked provider state, reconciliation persistence, or any
execution capability. Review ownership remains the only open Git Flow decision.

## Git Flow

- `origin` is configured at `https://github.com/Yogi-Langnickel/Money-maker-3000.git`.
- `develop` tracks `origin/develop` and should remain the integration branch.
- Use scoped feature branches from `develop` for new PR/review work.

Unblock questions:

1. Who should review simulation contract changes before merge?

## Provider And Performance Inputs

- Historical market-data inputs are the first approved provider-adjacent
  direction. Start there before portfolio state or reconciliation records.
- Use historical data to improve deterministic diagnostics for selected
  instruments. Backtest output must remain diagnostics only.
- Portfolio state and reconciliation records can be designed against an EC2
  worker plus DynamoDB storage boundary, but implementation remains blocked
  until the table schema, IAM role, and retention job are added.
- The eToro Dashboard should not durably store account-linked data. Dashboard
  reads may use live provider data and short in-memory server cache/backoff
  metadata only.
- Do not add credentials, demo execution, live execution, account-linked
  durable storage, real PnL, win-rate, drawdown, Sharpe ratio, profitability, or
  execution-quality reporting until separate review gates approve the inputs and
  storage boundary.
- Demo execution remains disabled. Planning inputs are recorded: USD 10,000
  demo bot allocation, USD 500 maximum order size, 20 maximum open positions,
  10% maximum daily drawdown, 25% maximum weekly drawdown, and user-selected
  instruments subject to allowlist/risk gates.
- Future demo design should start with limit orders only and mandatory order
  preview before submit. Market orders should stay disabled until slippage
  controls and reconciliation prove reliable.

See `docs/provider-boundary-decisions.md` for the current provider/storage
ordering and the exact meaning of demo execution approval.

First historical-data implementation boundary:

- Start offline-fixture-first, not with live eToro fetch code.
- Use small public daily OHLCV fixtures under
  `tests/fixtures/market_history/` when committed.
- Keep larger generated downloads under ignored
  `data/private/market-history/`.
- Start with `SPY`/`GLD` style ETF or equity symbols that already fit the
  simulation contract.
- Fixture rows may contain symbol, date, open, high, low, close, volume, and
  public source metadata only.
- Do not include account ids, balances, holdings, position ids, order ids,
  transaction history, provider user keys, raw account payloads, or screenshots.

Remaining unblock questions:

1. Should the DynamoDB account-linked audit retention defaults be accepted as:
   7 years for orders/risk/reconciliation, 90 days detailed portfolio snapshots
   compacted to daily summaries, and no default raw provider payload storage?
1. Which initial instrument allowlist should the user-selectable instrument
   field offer for demo planning?
1. Who should review simulation contract changes before merge?
