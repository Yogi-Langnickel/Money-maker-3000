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
1. Record a why-no-trade simulation ledger entry.
1. Return redacted DTOs suitable for dashboard display.

## Not Implemented

- Provider adapters.
- Credential loading.
- Durable leases.
- SQLite/Postgres audit ledger.
- Demo order preview or execution.
- Live trading.
