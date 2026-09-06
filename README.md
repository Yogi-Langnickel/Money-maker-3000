# Money-maker-3000

Python-first, simulation-only trading-bot worker core for the eToro Dashboard.

The worker does not call eToro, load credentials, preview orders, expose
execution routes, or place demo/live orders. It emits versioned JSON DTOs with
the same safety semantics the dashboard expects: provider calls blocked,
execution absent, demo/live execution blocked, and account-linked data
redacted or absent.

## Offline learning

The [learning pipeline](docs/learning-pipeline.md) fits a local supervised
probability model with purged chronological training/validation/holdout splits,
a fixed strategy-feature grid, train-only prior comparisons, and strict frozen
JSON artifacts. Use `learning-train` and `learning-predict`; the guide includes
an executable synthetic smoke example and the private dataset manifest format.
Current supplied data supports smoke testing only. Models remain offline
classification diagnostics, with no research-readiness or trading claim.

## Run

Backtest readiness gate:

```sh
PYTHONPATH=src python3.13 -m money_maker_3000.cli readiness \
  --fixture SPY=tests/fixtures/market_history/spy-daily.csv \
  --fixture GLD=tests/fixtures/market_history/gld-daily.csv \
  --started-at 2026-05-15T00:00:00Z
```

The readiness report emits `backtest-readiness.v1` with `ready: true` only
when strategy registry validation, allocation policy, run-mode policy, provider
boundary, and offline fixtures all pass. It remains offline-backtest-only:
provider calls are blocked, execution routes are absent, demo/live execution is
blocked, and account data is absent. If a fixture gate fails, the command still
prints a redacted JSON report and exits non-zero.

Synthetic diagnostics:

```sh
PYTHONPATH=src python3.13 -m money_maker_3000.cli backtest
```

Offline fixture backtest:

```sh
PYTHONPATH=src python3.13 -m money_maker_3000.cli backtest \
  --history-csv tests/fixtures/market_history/spy-daily.csv \
  --strategy dca-cash-reserve \
  --symbol SPY \
  --market US_EQUITIES \
  --instrument-class ETF \
  --bot-allocation-usd 1000 \
  --provider-demo-balance-usd 1000000 \
  --started-at 2026-05-15T00:00:00Z
```

Offline fixture backtest with allowlisted strategy parameters:

```sh
PYTHONPATH=src python3.13 -m money_maker_3000.cli backtest \
  --history-csv tests/fixtures/market_history/spy-daily.csv \
  --strategy dca-cash-reserve \
  --strategy-params-json '{"fixedOrderUsd":125,"maxOrdersPerWeek":2}' \
  --started-at 2026-05-15T00:00:00Z
```

Historical fixture backtests include a `periodDiagnostics` DTO for dashboard
portfolio charting. It covers `24h`, `1w`, `1m`, `1y`, `5y`, and `max` with
market-history change values, coverage state, source, and freshness-safe
metadata. These are market context diagnostics only, not bot profitability,
real P/L, win-rate, drawdown, Sharpe ratio, or execution-quality metrics.
The period-diagnostics fixture buffer is capped by `--max-fixture-rows`, which
defaults to 10,000 rows.

Historical reports also include `strategyHistoryDiagnostics`. Volatility-band
fixtures report a deterministic price decline from a rolling close peak; slow-trend
fixtures report deterministic short/long average and confirmation-window
observations. Insufficient, invalid, or malformed inputs fail closed. Every
state remains diagnostics-only with candidate intent `skip`, provider calls
blocked, account data absent, and execution routes absent.
Volatility windows distinguish no-trigger, active-decline trigger, and
recovered-after-decline observations using only the bounded offline close
history; none of those states is a recommendation.
Checksum-pinned synthetic fixtures cover those three default states plus an AU
ETF instrument contract. They are offline contract evidence only and must not
be represented as observed market data or performance evidence.
Fixture-batch output retains these per-symbol states and emits only a state
histogram in its summary. Readiness output carries the same redacted boundary
fields without raw metric payloads.

When every batch entry uses `threshold-rebalance` with the same target weights
and an identical historical window, the batch also emits
`rebalanceHistoryDiagnostics`. It normalizes target weights by relative offline
price change and reports historical weight drift only. It uses no portfolio
holdings or account balance, always keeps candidate intent `skip`, and fails
closed on incomplete or mismatched windows.
An aligned, checksum-pinned 20-bar synthetic batch proves this path across a
U.S. ETF, an AU ETF, and a commodity entry with per-symbol market/instrument
metadata. The result remains relative historical-weight drift only.

`--mode execute`, `--mode trade`, and `--mode trading` are rejected before any
work runs.

Canonical dashboard contract:

```sh
PYTHONPATH=src python3.13 -m money_maker_3000.contract_manifest --check
```

`contracts/dashboard-simulation-contract.json` is generated from the Python
contract source and contains only redacted strategy/configuration metadata.
After an intentional contract change, regenerate it with `--write`. CI fails
when the committed artifact drifts from `contracts.py`.

Canonical fixture provenance:

```sh
PYTHONPATH=src python3.13 -m money_maker_3000.fixture_provenance --check
```

`contracts/market-history-fixture-provenance.json` inventories every committed
CSV fixture and pins its classification, SHA-256, symbol, row count, and source
label. CI rejects unlisted, malformed, symlinked, relabeled, or byte-drifted
fixtures. An observed-data classification additionally requires explicit
source, license, attribution, and redistribution evidence.

Ledger report:

```sh
PYTHONPATH=src python3.13 -m money_maker_3000.cli ledger-report .local/simulation-ledger.jsonl
```

The report reader recovers valid records from malformed JSONL without changing
the source file. It emits allowlisted integrity issue codes and counts only;
raw malformed content is never copied into the report. A corrupted ledger
returns exit status `1`, while a clean or warning-only recovery returns `0`.

`simulation-audit-record.v2` is frozen as an exact-shape diagnostic record. It
can represent only synthetic `skip`/`blocked` outcomes; it is not a virtual
order or fill record. Every top-level, allocation, and trade-log field is
required and validated, including canonical UTC timestamps, SHA-256 config
hashes, v2-owned historical contract/strategy version allowlists, identities,
finite bounded allocation values, allowlisted unique vetoes, and matching
parent/trade-log facts. The frozen allowlists preserve readable v2 history when
the current producer advances. Trade-log IDs are deterministically bound to run
IDs. Missing, unknown, duplicate-key, mixed-version, and future records are
rejected instead of being completed with defaults.

Explicit `simulation-audit-record.v1` records remain read/report/redact-only.
They cannot be appended to, and an existing corrupt, legacy, or mixed ledger
blocks further appends without changing the source. Actual virtual orders,
fills, cash, and positions belong to a separately reviewed v3/SQLite event
model; v2 will not be widened for that capability.

Both ledger persistence and CLI output serialize with `allow_nan=False`.
Unexpected non-standard numeric output therefore fails closed even if an
upstream validation or sanitization regression occurs.

Readers take a shared sidecar lock while scanning. Appends take the exclusive
form of the same lock and use an already-locked internal reader, so reporting
cannot observe a partial append and the writer does not recursively reacquire
its own lock. Ordinary reads open the sidecar in writable mode and create it
when absent; the process therefore needs write access to the lock file and
directory creation permission on first use. There is no unlocked read-only
archive mode.

Simulation worker lease report:

```sh
install -d -m 700 .local
PYTHONPATH=src python3.13 -m money_maker_3000.cli lease-report \
  .local/simulation-worker-leases.json
```

Lease storage never creates its parent directory. Pre-create an euid-owned
directory with mode `0700`; the runtime rejects any group/world permissions.
`.local/` is ignored by Git and agent context retrieval.

`lease-report` is read-only. A missing store emits the canonical
`uninitialized`/blocked report, exits `1`, and creates neither the state file
nor its lock. Existing state is read under a bounded POSIX `fcntl` lock. The
DTO exposes no owner, idempotency value, hash, fencing generation, path, or raw
content; it remains synthetic with provider calls blocked, account data and
execution routes absent, demo/live execution blocked, and candidate intent
`skip`. Use `--observed-at` with a timezone-aware ISO timestamp for a
deterministic snapshot.

Clean reports expose only safe completion count/capacity totals. A full marker
set reports `completion-capacity-blocked`; it cannot mint a lease or fence for
new work.

Optional stdlib profiling:

```sh
PYTHONPATH=src python3.13 -m money_maker_3000.cli --profile profile.txt backtest
```

## Validate

```sh
PYTHONPYCACHEPREFIX=.pycache python3.13 -m compileall src tests
PYTHONPATH=src python3.13 -m unittest discover tests
PYTHONPATH=src python3.13 -m money_maker_3000.fixture_provenance --check
```

GitHub Actions runs the same compile and standard-library test gates on Python
3.11, 3.12, and 3.13 for pull requests and pushes to `develop`.

## Current Scope

- Python package under `src/money_maker_3000/`.
- Predefined strategy registry only.
- Strategy parameters are schema allowlisted per predefined strategy; unknown
  keys or out-of-range values fail before CLI report generation.
- Explicit run mode contract: `backtest` is allowed, `execute` is disabled.
- Internal allocation ledger fields separate bot allocation from any
  provider/demo account balance: `bot_allocation_usd`, `reserved_usd`,
  `available_usd`, `max_order_usd`, and strategy allocation IDs.
- Provider/demo balance is read-only reconciliation context only and is never a
  sizing source or persisted balance.
- Pure risk gate runs before any strategy output could become an order intent
  and fails closed on missing reconciliation, unknown provider state, stale or
  missing data, invalid strategy/instrument/version, incomplete policies, or
  missing allocation.
- Risk policy covers daily loss stop, weekly loss stop, max allocation
  drawdown, per-order cap, per-instrument exposure cap, max open positions,
  cash reserve floor, leverage fixed at 1, and blocked shorts/copy
  trading/CFDs/options/derivatives/crypto.
- Loss and drawdown stops are simulation-only until real reconciliation exists;
  outputs state they are not evaluated against real PnL.
- Streaming CSV ingestion uses stdlib `csv` and date validation. Backtests flow
  as `Iterable[Bar] -> Iterator[DecisionEvent] -> Summary`.
- Offline market-history diagnostics now emit selected-period context for
  `24h`, `1w`, `1m`, `1y`, `5y`, and `max`, suitable for dashboard
  instrument rows without provider calls or account data.
- Synthetic, checksum-pinned fixtures prove all default volatility-band states
  and the `AU_EQUITIES`/ETF instrument path without provider access.
- Historical fixture reports fail fast above `maxFixtureRows` instead of
  materializing unbounded period-diagnostics input.
- Backtest readiness reports verify the offline fixtures and safety posture
  before running diagnostics, and return suggested safe backtest commands for
  fixtures that passed readiness.
- Backtest output is diagnostics only: coverage, veto counts, config errors,
  cadence/risk gate behavior, fixture source/date coverage, deterministic
  SHA-256 input metadata, parser version, Python version, and explicit
  `started_at`.
- Backtests include `strategy-intent-diagnostics.v1` candidate-intent context,
  but intent remains skipped while risk/provider/execution gates are blocked.
- Redacted local JSONL ledger appends are single-writer locked so run and
  correlation duplicate checks happen in the same critical section as the
  append.
- Ledger report recovery rejects malformed, oversized, non-object, invalid
  UTF-8, and invalid v2 records; accepts redacted legacy records with explicit
  warnings; never mutates the source; and fails closed when any record is
  rejected.
- Durable local simulation worker leases use strict bounded JSON, hashed opaque
  holder/idempotency values, bounded TTLs and completion markers, a random
  store epoch, persisted monotonic fencing, bounded POSIX locking, atomic
  replacement, exact release and completion replay protection, and a persisted
  kill switch with allowlisted reasons. Missing or corrupt state is never
  silently repaired by a worker mutation. Authorization is only a snapshot:
  any future side effect must atomically recheck holder, idempotency key, epoch,
  fence, expiry, and kill-switch state while holding the lease lock.
- No real PnL, win-rate, Sharpe, drawdown, execution quality, profitability
  claims, provider calls, or account-linked persistence.

## Safety Defaults

- Live execution: unavailable.
- Demo execution: unavailable.
- Credential loading: unavailable.
- Provider mutation endpoints: absent.
- Order preview: absent.
- Leverage: 1 only.
- Shorts, copy trading, CFDs, derivatives, options, and crypto: blocked.
- News/social context can explain context but cannot trigger orders.
