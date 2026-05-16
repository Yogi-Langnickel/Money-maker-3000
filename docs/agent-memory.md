# Money-maker-3000 Agent Memory

Status: active  
Created: 2026-05-15

## Current Truth

- Money-maker-3000 is a simulation-first trading-bot worker scaffold for the
  eToro Dashboard.
- The worker does not call eToro, load credentials, place demo orders, place
  live orders, or expose execution routes.
- Strategy selection comes only from the predefined registry.
- The predefined strategy registry is contract-validated for unique IDs,
  simulation-safe statuses, low-frequency cadence, and instrument metadata
  before run DTOs are returned.
- `src/simulation-contract.mjs` owns the canonical simulation config mirror
  shape for dashboard consumers: run modes are `backtest` and `execute`
  (`execute` is rejected/disabled), market groups are `US_EQUITIES`,
  `AU_EQUITIES`, `FOREX`, and `COMMODITIES`; selected config must satisfy the
  chosen strategy's selected instrument, allowed markets, allowed instrument
  classes, and cadence.
- The simulation contract now exposes a run-mode policy: `backtest` is enabled
  for offline fixture inputs only, while `execute` remains disabled with
  provider calls, demo execution, live execution, and execution routes blocked.
- Current performance diagnostics are synthetic only: run counts, skipped and
  blocked decisions, deterministic scenario summaries, veto histograms, warning
  histograms, config error histograms, no-HFT cadence counts, and budget ranges.
- `src/ledger.mjs` exports redacted simulation ledger report DTOs for dashboard
  consumption; reports summarize synthetic records, strategies, decisions, risk
  results, vetoes, and record time bounds while preserving blocked provider and
  execution fields.
- Provider readiness metadata is metadata-only and explicitly blocks provider
  calls, credentials, account data, order previews, demo execution, and live
  execution.
- Provider work should proceed in this order: historical market-data inputs,
  deterministic backtest fixtures, read-only portfolio-state snapshots, then
  reconciliation records. Demo execution is a later separate design and is not
  approved by historical data or read-only portfolio work.
- The eToro Dashboard should not durably store account-linked data. It may use
  live read-only data and short in-memory server cache/backoff metadata only;
  future account-linked history belongs in a private worker-side store with
  explicit retention/redaction/audit rules.
- Do not report real PnL, win rate, drawdown, Sharpe ratio, or execution quality
  until provider history, market data, portfolio state, and reconciliation
  inputs are explicitly designed and reviewed.

## Commands

- `npm run check`: run the current Node test suite.
- `npm run start`: run one synthetic simulation and print the DTO.
- `npm run start -- --mode backtest --strategy threshold-rebalance --symbol GLD --market COMMODITIES --instrument-class ETF`:
  run one synthetic backtest-mode DTO for a selected strategy/instrument.
- `npm run start -- --ledger-report .local/simulation-ledger.jsonl`:
  export a redacted dashboard DTO from an existing local synthetic ledger.

## Performance And Context Notes

- Keep cadence low-frequency only; current minimum evaluation interval is 240
  minutes.
- Treat future provider rate limits as a budgeted resource before enabling any
  adapter.
- Keep local ledgers, generated reports, and provider-like fixtures ignored.
- Use `.contextignore` to avoid loading generated ledgers, reports, and build
  output during agent context retrieval.

## Read Next

- `AGENTS.md` for hard safety rules.
- `docs/architecture.md` for simulation ledger and backtest/performance shape.
- `docs/provider-boundary-decisions.md` before provider, storage, portfolio, or
  execution work.
- `unblockme.md` before provider, git remote, or execution work.
