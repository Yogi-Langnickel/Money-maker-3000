# Money-maker-3000 Unblock Notes

Status: non-blocking for historical market-data/backtest implementation
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
- Use historical data to improve strategy backtests and deterministic
  diagnostics for selected instruments.
- Portfolio state and reconciliation records remain blocked until a private
  worker-side storage boundary is designed.
- The eToro Dashboard should not durably store account-linked data. Dashboard
  reads may use live provider data and short in-memory server cache/backoff
  metadata only.
- Do not add credentials, demo execution, live execution, account-linked
  durable storage, real PnL, win-rate, drawdown, or execution-quality reporting
  until separate review gates approve the inputs and storage boundary.
- Demo execution approval means approval to design and call eToro demo mutation
  endpoints. That is not approved yet; `execute` mode remains disabled.

See `docs/provider-boundary-decisions.md` for the current provider/storage
ordering and the exact meaning of demo execution approval.

First historical-data implementation boundary:

- Start offline-fixture-first, not with live eToro fetch code.
- Use small public daily OHLCV fixtures under
  `test/fixtures/market-history/` when committed.
- Keep larger generated downloads under ignored
  `data/private/market-history/`.
- Start with `SPY`/`GLD` style ETF or equity symbols that already fit the
  simulation contract.
- Fixture rows may contain symbol, date, open, high, low, close, volume, and
  public source metadata only.
- Do not include account ids, balances, holdings, position ids, order ids,
  transaction history, provider user keys, raw account payloads, or screenshots.

Unblock questions:

1. What private worker-side storage boundary should hold future account-linked
   portfolio and reconciliation data if we need it later?
1. Should demo execution remain blocked until after historical backtests and
   read-only reconciliation are useful? Current recommendation: yes.
