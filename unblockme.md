# Current research blockers

Updated: 2026-10-25. This file describes what prevents training on the currently
configured SPY data and the work needed to remove those blockers.

## Current snapshot

The read-only eToro collector works. On 25 October 2026 at 13:53 AEDT it added
15 SPY observations, bringing retained history to 1,015 observations dated
2022-09-02 through 2026-10-23. No existing observations were revised or reported
missing. Two offline passes were identical; semantic replay matched two input
events, with zero models, trials or shadow decisions.

SPY identity, ETF type and listing-currency evidence are accepted by the
configured collector. The standing customer attestation authorizes private
retention, research and model use while active; no repeated permission or
provider-written model-use exception is required. These are resolved items.
Fresh retrieval does not establish verified completed-session freshness.

## What currently blocks training

1. **The meaning and availability of the observed candles remain unverified.**
   The autonomous pipeline reports five missing facts: price basis, adjustment
   basis, session convention, timestamp meaning and completed-OHLC availability.
   Without them, it cannot establish what a price or outcome represents or when
   that information was available to a decision. The current collector adapter
   explicitly supplies unknown timestamp/adjustment meaning and null session
   boundaries/availability. External evidence and a reviewed adapter change are
   needed before these observations qualify for model or economic research;
   refreshing the data or editing an approval flag cannot resolve this.
1. **The standard learning helper does not reach the private research inputs.**
   It discovers the clean `develop` checkout, which has neither its expected
   learning inventory nor continuous-research configuration. The private
   autonomous data/configuration lives in the primary project checkout. The
   helper therefore returns `missing-parent-directory`. This is a routing and
   configuration gap, separate from provider permission and candle meaning.

## Next work

1. Establish source-backed candle semantics and implement the reviewed intake
   mapping, keeping unresolved facts explicit.
1. Connect the learning entrypoint to the existing private configuration without
   duplicating data, changing rights or bypassing eligibility checks.
1. Once the input qualifies, run one or two bounded, frozen training experiments
   and verify their replay. Report unavailable results honestly.

Prospective outcomes are a later evaluation requirement: after fitting, record
forecasts and score genuinely later observations. They are not an additional
permission blocker to collection or initial training. Replaying existing history
does not create independent predictive evidence or establish profitability.

Money Maker remains simulation-only: no account reads, provider writes or
demo/live execution. For policy, history and later stages, see
[feed collection](docs/feed-collection.md),
[autonomous research](docs/autonomous-profit-shadow-v1.md),
[research protocol](docs/continuous-research-protocol.md),
[conditional source portability](docs/strategy-portability.md) and the
[trading roadmap](docs/autonomous-trading-roadmap.md).
