# Money-maker-3000 Architecture

Status: scaffold

Money-maker-3000 is intended to run as a separate worker/service from the eToro
Dashboard. The dashboard consumes redacted status and ledger DTOs; the worker
owns strategy evaluation, risk vetoes, scheduling, trade logging, and eventual
reconciliation.

## First Slice

The first slice is local and synthetic:

1. Load a predefined strategy registry.
1. Apply budget and cadence guardrails.
1. Evaluate a synthetic portfolio context.
1. Record a why-no-trade simulation ledger entry in local append-only JSONL.
1. Return redacted DTOs and provider-readiness metadata suitable for dashboard
   display.

## Local Simulation Ledger

The durable first-slice ledger is local JSONL written only when the caller
provides a path. Records are append-only at the application layer, synthetic,
and redacted before persistence. They must not contain account identifiers,
provider payloads, balances, credentials, tokens, portfolio exports, or other
private provider state.

The ledger is a simulation audit trail, not an execution log. Provider calls,
demo trading, and live trading remain blocked.

## Provider Metadata

`src/providers.mjs` exposes a provider registry and metadata snapshot for
dashboard readiness checks. The current eToro entry is metadata-only: provider
calls are blocked, credentials are not loaded, account and market data are
absent, order preview is absent, and demo/live execution are blocked.

The provider metadata validator rejects enabled provider calls, loaded
credentials, loaded account data, demo/live execution, non-simulation modes, or
enabled capabilities. It is a contract scaffold only and must not become an
adapter or credential boundary without a separate review gate.

## Synthetic Backtest/Performance Summary

The first performance review path is synthetic diagnostics, not trading
performance. `src/backtest.mjs` runs deterministic scenarios through the
existing simulation contracts and summarizes run count, skipped decisions,
blocked risk results, veto frequency, warning frequency, and budget ranges.

This gives a stable way to review strategy coverage, config warnings, and
guardrail pressure before provider adapters exist. It must not report real PnL,
win rate, Sharpe ratio, drawdown, or execution quality until market history,
portfolio state, and reconciliation inputs are designed and reviewed.

## Configuration Validation

Strategy configuration is validated before each simulation run. Valid
configuration must use a predefined strategy, selectable USD budget, approved
market groups, approved instrument classes, low-frequency cadence, provider
calls blocked, live/demo execution blocked, shorts blocked, copy trading
blocked, and leverage fixed at 1. The canonical config contract lives in
`src/simulation-contract.mjs` and uses `US_EQUITIES`, `AU_EQUITIES`, `FOREX`,
and `COMMODITIES` market groups. Runtime config is checked against the selected
strategy's allowed market groups, allowed instrument classes, and cadence before
a run DTO is returned.

## Strategy Registry Validation

The predefined registry is also validated as a contract. Strategy entries must
have unique kebab-case identifiers, simulation-safe statuses, low-frequency
cadence, known market groups, known instrument classes, compatible
market/instrument-class pairings, and non-HFT holding-period descriptions.
Simulation strategies cannot include blocked execution instrument classes such
as FOREX; context-only strategies can describe broader context, but still cannot
create orders or recommendations.

## Not Implemented

- Provider adapters.
- Credential loading.
- Durable leases.
- Demo order preview or execution.
- Live trading.
