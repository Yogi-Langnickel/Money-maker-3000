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
1. Return redacted DTOs suitable for dashboard display.

## Local Simulation Ledger

The durable first-slice ledger is local JSONL written only when the caller
provides a path. Records are append-only at the application layer, synthetic,
and redacted before persistence. They must not contain account identifiers,
provider payloads, balances, credentials, tokens, portfolio exports, or other
private provider state.

The ledger is a simulation audit trail, not an execution log. Provider calls,
demo trading, and live trading remain blocked.

## Configuration Validation

Strategy configuration is validated before each simulation run. Valid
configuration must use a predefined strategy, selectable USD budget, approved
markets, approved instrument classes, low-frequency cadence, provider calls
blocked, live/demo execution blocked, shorts blocked, copy trading blocked, and
leverage fixed at 1.

## Not Implemented

- Provider adapters.
- Credential loading.
- Durable leases.
- Demo order preview or execution.
- Live trading.
