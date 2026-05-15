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
- Current performance diagnostics are synthetic only: run counts, skipped and
  blocked decisions, veto histograms, warning histograms, and budget ranges.
- Provider readiness metadata is metadata-only and explicitly blocks provider
  calls, credentials, account data, order previews, demo execution, and live
  execution.
- Do not report real PnL, win rate, drawdown, Sharpe ratio, or execution quality
  until provider history, market data, portfolio state, and reconciliation
  inputs are explicitly designed and reviewed.

## Commands

- `npm run check`: run the current Node test suite.
- `npm run start`: run one synthetic simulation and print the DTO.

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
- `unblockme.md` before provider, git remote, or execution work.
