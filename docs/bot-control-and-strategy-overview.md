# Bot Control And Strategy Overview

Status: active simulation design
Created: 2026-06-06

Money-maker-3000 remains a simulation-only worker core. The eToro Dashboard can
show bot state, strategy selection, budget posture, redacted audit summaries,
and safe controls, but it must not execute strategy code or place orders.

## Control Model

| Control | Source of truth | Current state |
| --- | --- | --- |
| Run mode | `contracts.py` | `backtest` only; `execute`, `trade`, and `trading` are rejected. |
| Strategy list | `strategies.py` registry | Predefined entries only; arbitrary uploaded/operator code is blocked. |
| Strategy parameters | `contracts.py` schemas | Allowlisted per predefined strategy; unknown keys and out-of-range values fail before CLI report generation. |
| Allocation | Internal bot allocation policy | Separate from provider/demo balance; provider balances are ignored for sizing. |
| Risk gate | `risk.py` | Fail-closed before any candidate intent can become an order intent. |
| Provider state | Metadata snapshot | Provider calls, credentials, account data, order previews, demo execution, and live execution are unavailable. |
| Audit | Local redacted JSONL ledger helpers | Synthetic/redacted diagnostics only; no account-linked provider data; duplicate checks and appends are single-writer locked. |
| Backtest readiness | `readiness.py` / CLI | `backtest-readiness.v1` verifies registry, allocation, run modes, provider boundary, and offline fixtures before diagnostics. |
| Dashboard controls | Future worker DTO consumer | Disabled until worker API, auth, CSRF, rate limits, and review gates exist. |

## Required Risk Gates

Every strategy must stay behind the existing fail-closed checks:

- run mode is `backtest`
- provider calls are blocked
- execution routes are absent
- allocation and strategy allocation IDs exist
- reconciliation input exists and is fresh enough
- selected strategy/instrument is valid for the registry
- data freshness is valid
- daily, weekly, and allocation drawdown policy is present
- per-order cap, exposure cap, open-position cap, and cash reserve are present
- leverage is exactly `1`
- shorts, copy trading, CFDs, options, derivatives, and crypto are blocked

## Predefined Strategies

| Strategy ID | Purpose | Instruments / markets | Cadence | Config knobs to model next |
| --- | --- | --- | --- | --- |
| `dca-cash-reserve` | Scheduled long-only accumulation while preserving cash reserve. | `EQUITY`, `ETF`; `US_EQUITIES`, `AU_EQUITIES`. | Daily | `fixedOrderUsd`, `orderFractionPct`, `cashReserveFloorUsd`, `maxOrdersPerWeek`, `cooldownDays`. |
| `threshold-rebalance` | Drift-based portfolio balancing without shorts or leverage. | `EQUITY`, `ETF`, `COMMODITY`; `US_EQUITIES`, `AU_EQUITIES`, `COMMODITIES`. | Weekly | `targetWeights`, `rebalanceThresholdPct`, `maxOrderUsd`, `minCashReserveUsd`, `maxOpenPositions`. |
| `volatility-band-accumulator` | Conservative buy-skip diagnostics when daily movement enters a historical volatility band. | `EQUITY`, `ETF`; `US_EQUITIES`, `AU_EQUITIES`. | Daily | `lookbackDays`, `dropTriggerPct`, `maxOrderUsd`, `maxOrdersPerWeek`, `cooldownDays`, `cashReserveFloorUsd`. |
| `slow-trend-allocation` | Long-only slow trend filter for hold/add/skip backtest behavior. | `EQUITY`, `ETF`; `US_EQUITIES`, `AU_EQUITIES`. | Weekly | `shortLookbackDays`, `longLookbackDays`, `confirmationBars`, `orderFractionPct`, `maxOrderUsd`. |
| `news-aware-watchlist` | Context annotations and blackout flags only. News cannot create orders, recommendations, or sizing. | Context may cover allowed registry markets; trading output is absent. | Daily | `contextTtlDays`, `sourceLabels`, `blackoutTags`, `severityThreshold`. |

Backtest reports include `strategy-intent-diagnostics.v1` for deterministic
candidate-order context, but the intent remains `skip`, provider calls stay
blocked, execution routes stay absent, and output carries no profitability
claim.

`strategy-history-diagnostics.v3` adds a walk-forward state-coverage summary
for volatility-band and slow-trend fixtures. It evaluates every eligible
historical endpoint and returns only observation counts, state counts,
transition count, date coverage, and up to five nonempty balanced chronological
folds. Fold output is limited to index, observation/date coverage, state counts,
and transition count. Offline batch validation recomputes the canonical folds
from the report's authoritative historical bars, binding every fold date and
transition to the source observations and the terminal strategy state. It does
not create orders,
recommendations, return metrics, or execution-quality claims.

`market-history-sampling-quality.v1` adds non-financial temporal coverage for
offline fixtures. It uses a weekday grid rather than an exchange calendar and
reports observation/interval counts, calendar span, weekday/weekend coverage,
potential intervening weekdays, and gap counts without exposing prices,
returns, PnL, provider/account data, execution routes/actions, or
recommendations. Potential weekday gaps are not proof of missing market
sessions because holidays and exchange calendars are not modeled. Readiness
emits an exchange-calendar-review warning whenever validated gap/weekend
counters are nonzero, including a single weekend observation whose state remains
`insufficient-history`, while readiness remains governed by actual errors.

The `readiness` command is the operator gate before offline fixture diagnostics.
It reports only offline-backtest readiness and suggests safe `backtest`
commands for passing fixtures. It must not be interpreted as demo/live trading
approval.

## Future Strategy Candidates

A read-only strategy-design subagent review on 2026-06-06 recommended these
future predefined candidates, pending separate schema/test implementation:

- `calendar-accumulator`
- `moving-average-pullback-accumulator`
- `drawdown-ladder-accumulator`
- `cash-reserve-replenish-hold`
- `static-basket-contribution`

## Fixture Needs

Use only offline CSV fixtures with this schema:

```text
symbol,date,open,high,low,close,volume,source
```

Recommended fixture coverage:

- `dca-cash-reserve`: existing `SPY`, plus the checksum-pinned synthetic `VAS`
  AU ETF contract fixture.
- `threshold-rebalance`: a checksum-pinned aligned synthetic batch now covers a
  U.S. ETF, AU ETF, and `GLD`-style commodity/non-CFD fixture.
- `volatility-band-accumulator`: checksum-pinned synthetic stable, falling, and
  recovering daily windows now cover all default states.
- `slow-trend-allocation`: at least 250 daily bars for slow-window diagnostics.
- `news-aware-watchlist`: synthetic/redacted context fixture only; no provider
  payloads, account data, or news-driven order fields.

## Next Implementation Slice

1. Add durable worker leases before any scheduled worker operation.
1. Add dashboard rendering for the schema metadata and intent diagnostics.
1. Add the next predefined strategy only after schema and safety tests are
   written first.
1. Keep dashboard-facing DTO changes redacted and simulation-only.
