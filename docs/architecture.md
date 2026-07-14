# Money-maker-3000 Architecture

Status: Python-first core

Money-maker-3000 runs as a separate simulation worker/service from the eToro
Dashboard. The dashboard consumes versioned, redacted JSON DTOs; the worker owns
strategy registry validation, allocation policy, pure risk gating, fixture
backtests, audit ledgers, and eventual reconciliation design.

## Python Core

The runtime package lives under `src/money_maker_3000/`.

- `contracts.py`: simulation config, run-mode policy, allocation policy, and
  validators, including per-strategy allowlisted parameter schemas.
- `strategies.py`: predefined registry, parameter-schema metadata, and registry
  validator.
- `contract_manifest.py`: canonical redacted dashboard manifest generation and
  drift validation from the Python contract source.
- `fixture_provenance.py`: canonical inventory and fail-closed drift validation
  for every committed offline market-history CSV.
- `risk.py`: pure fail-closed risk gate.
- `market_history.py`: stdlib streaming CSV parser and single-pass history
  accumulator plus selected-period market diagnostics for dashboard charting.
- `sampling_quality.py`: O(n) calendar-agnostic observation-gap diagnostics
  using bounded weekday arithmetic, with no exchange-session completeness
  claim or financial output.
- `history_signals.py`: deterministic rolling-window observations for the
  predefined volatility-band and slow-trend strategies. It emits no orders,
  recommendations, profitability claims, or provider/account data and fails
  closed on malformed history or parameters.
- `rebalance_history.py`: multi-symbol historical relative-weight drift for the
  predefined threshold-rebalance strategy. It requires identical offline
  windows and target coverage and never consumes holdings, balances, P/L, or
  provider data.
- `backtest.py`: `Iterable[Bar] -> Iterator[DecisionEvent] -> Summary`.
- `readiness.py`: offline backtest-readiness gates for strategy registry,
  allocation policy, run-mode policy, provider boundary, and fixture coverage.
- `ledger.py`: redacted append/read/report JSONL audit records with exclusive
  writer locking around duplicate checks and append, plus fail-closed malformed
  ledger recovery for reporting.
- `worker_leases.py`: strict local simulation-worker coordination with bounded
  TTLs/completion markers, hashed opaque identities, monotonic fencing, atomic
  persistence, replay protection, and a persisted kill switch.
- `reconciliation.py`: simulation-only reconciliation records that redact
  provider/account fields and feed the pure risk-state contract.
- `providers.py`: metadata-only provider boundary and disabled execution
  gateway contract.
- `cli.py`: backtest/readiness/fixture diagnostics plus read-only ledger and
  worker-lease reporting commands.

The committed `contracts/dashboard-simulation-contract.json` artifact is the
only cross-repository dashboard contract source. Consumers pin the producer
commit and artifact hash; they do not manually recreate Python constants.

The committed `contracts/market-history-fixture-provenance.json` artifact pins
every current synthetic CSV and refuses observed-market classification without
explicit source, license, attribution, and redistribution metadata.

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

`reconciliation.py` accepts caller-supplied synthetic/read-only reconciliation
context only. It performs no provider calls, persists no account-linked data,
redacts provider balance/account identifiers in DTOs, and only marks
reconciliation available when provider state is known-read-only and loss,
drawdown, exposure, and open-position context are complete.

## Ledger Boundary

The local JSONL ledger is append-only at the application layer and remains
synthetic/redacted. `append_ledger_record` validates the DTO, acquires a
sidecar writer lock, checks existing run and correlation identities while the
lock is held, then appends and fsyncs one compact JSON line. This keeps repeated
or concurrent simulation worker attempts idempotent without introducing live
provider state, account identifiers, or an external storage dependency.

Reporting reads the source in bounded binary lines and does not repair or
rewrite it. Valid v2 records are strictly validated; legacy records are
normalized and redacted with warnings. Oversized lines, invalid UTF-8/JSON,
non-object values, and invalid v2 records are rejected. The report contains
only allowlisted issue codes, line numbers, severities, and counts—never raw
malformed content. Any rejected record marks integrity `corrupted` and makes
the CLI exit non-zero after emitting the controlled report.

## Simulation Worker Lease Boundary

`worker_leases.py` coordinates at most one local simulation worker operation.
Initialization is explicit. Acquire, renew, release, completion, kill-switch,
and re-enable transitions use one exact-shape, size-capped JSON state file.
Opaque holder and idempotency inputs are domain-separated SHA-256 hashes before
persistence; raw inputs are never stored. A same-holder acquire retry is
byte-stable and does not extend TTL. Exact expiry permits takeover with a newer
persisted fence, while stale same-owner/ABA credentials fail authorization.

Completion atomically appends to a bounded 4,096-marker set keyed globally by
idempotency hash and releases the lease. Markers are never silently evicted;
capacity exhaustion blocks new acquisition before work or a new fence can be
created. The 2 MiB store cap and marker limit cover roughly 1.8 years at six
completed runs per day. Before capacity is reached, an explicit reviewed
migration/archive must preserve durable duplicate suppression; automatic
marker eviction is forbidden. An exact original completion replay is a
controlled byte-stable success. Release keeps one exact replay tombstone until
the next acquisition, allowing a byte-stable retry without making stale
release credentials valid after reacquisition.
Engaging the persisted kill switch with an allowlisted `operator-stop`,
`risk-stop`, or `maintenance` reason revokes and fences any active lease.
Repeated engagement is byte-stable. Re-enable records `operator-reenable`,
advances revision/fence, and never resurrects the revoked lease.

All existing-state access requires a bounded POSIX `fcntl` lock; there is no
unlocked or non-POSIX fallback. State and lock symlinks, non-regular files,
hardlinks, broad file modes, duplicate JSON keys, unknown fields/versions,
invalid numbers, time reversal relative to the last persisted mutation, and
oversized/corrupt state fail closed. Writes use a unique mode-`0600` same-dir
temporary file, fsync it, atomically replace state, then fsync the directory.
The stable lock inode is checked around each transition. A missing read-only
report creates nothing and returns canonical blocked/uninitialized output.

Lease authorization is a snapshot, not permission for a later side effect.
Any future side-effect implementation must hold the lease lock and atomically
recheck holder, idempotency key, fence, expiry, and kill switch immediately
before the effect. This slice intentionally does not wire leases into the
scheduler or ledger and adds no provider or execution behavior.

## EC2 And DynamoDB Direction

When the worker moves to a dedicated EC2 instance, DynamoDB is the preferred
durable worker-side store. It fits the expected access pattern better than
Google Sheets because the bot needs conditional writes, idempotency keys,
append-only audit events, state transitions, and reliable reconciliation
history.

Secrets on EC2 should be loaded from AWS Secrets Manager through IAM. Google
Sheets can still receive redacted reporting exports for human review, but the
sheet must not be the source of truth for trade state or reconciliation.

The local simulation lease file is a single-host coordination primitive, not
the future distributed store. A multi-host worker must move fencing and
idempotency transitions to conditional writes in the durable store rather than
sharing or copying this local file.

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
test helpers or bounded period diagnostics.

Backtest DTOs include deterministic metadata:

- strategy ID/version
- config version
- data source
- first/last date
- row count
- input SHA-256
- `maxFixtureRows`, defaulting to 10,000 rows
- parser version
- Python version
- explicit `startedAt`
- `periodDiagnostics` for `24h`, `1w`, `1m`, `1y`, `5y`, and `max`
- `samplingQuality` using the explicit `weekday-grid-not-exchange-calendar`
  basis and exact adjacent-date interval evidence

The `readiness` CLI command emits `backtest-readiness.v1` before operator-run
fixture diagnostics. It returns `ready: true` only when strategy registry,
allocation policy, run-mode policy, provider boundary, and offline fixtures are
safe for diagnostics. The readiness scope is explicitly
`offline-backtest-only`; provider calls remain blocked, execution routes remain
absent, demo/live execution remains blocked, and account data remains absent.
Sampling anomalies are warnings that require exchange-calendar review; they do
not block readiness and do not prove that any market session is missing. The
warning derives from validated counters, so a single weekend observation warns
without replacing its `insufficient-history` state. Full historical and batch
diagnostics retain adjacent-date interval evidence for exact validation;
readiness deliberately omits that evidence and observation dates.

Backtest output is diagnostics only: coverage, veto counts, config errors,
cadence/risk gate behavior, and fixture source/date coverage. It must not
report real PnL, win rate, Sharpe ratio, drawdown, execution quality, or
profitability claims.

Strategy parameters are declarative JSON only and validated against
allowlisted schemas in `contracts.py`. CLI JSON input fails fast on unknown
keys or out-of-range values before report generation. Backtest reports include
`strategy-intent-diagnostics.v1` for deterministic candidate-order context, but
intent remains `skip` while provider calls are blocked and execution routes are
absent.

Period diagnostics are market-history change context only. They include source,
coverage state, start/end dates, start/end closes, absolute change, percentage
change, and bar count for the selected window. They are intended for dashboard
instrument-row charts and must not be described as bot performance.

The period-diagnostics path keeps a bounded in-memory fixture buffer. Historical
fixture reports fail fast when the input exceeds `maxFixtureRows`; callers may
raise the limit only for explicit offline fixture analysis.

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
- Scheduler/ledger integration with local simulation leases.
- Demo order preview or execution.
- Live trading.
