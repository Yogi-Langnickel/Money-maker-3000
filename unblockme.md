# Money-maker-3000 research unblock record

## Standing eToro customer authorization (2026-10-03)

The customer confirms all necessary eToro storage/retention, private research and
model-use rights while active. Carry this forward without repeated requests.
Earlier written-exception blockers are superseded for this customer; this does
not claim a provider-written exception. See [the canonical customer policy and
remaining data-meaning constraints](docs/feed-collection.md). Inactivity, revocation and provider
requests block use; account/execution/write boundaries remain unchanged.



Updated: 2026-10-03

Money Maker remains simulation-only. Provider data cannot create an order
intent, and demo/live execution, account reads, credentials in evidence, and
provider mutation remain disabled.

## Development phase and future product direction

Simulation-only is the current development phase. The eventual product goal is
autonomous operation using a dedicated, bounded allocation, but it requires a
separate explicit go-live authorization after the roadmap gates are met. No
demo or live execution, account access, or allocation has been authorized or
implemented now. See the [autonomous trading roadmap](docs/autonomous-trading-roadmap.md).

## Resolved

- The earlier Cloudflare 1010 edge block has a documented transport path: the
  account holder supplied eToro support guidance, and the fixed non-browser
  User-Agent reached HTTP 200 for a metadata-only request.
- A sanitized, non-retained structural exploration established that instrument
  search can return the exact SPY match, identifier, display name, and exchange,
  and that the daily endpoint exposes OHLCV-shaped candles. It did not retain
  values, payloads, request identifiers, prices, or account data.

## Remaining eToro data-meaning constraints

Instrument type lookup is repaired using the support-confirmed display/type
endpoints; exact symbol identity no longer depends on omitted search fields.
Rights are established by the standing customer authorization above.

The support reply supplied on 2026-10-03 confirms equal duplicated search IDs
identify the same instrument; the collector avoids the redundant projection and
keeps duplicate-key rejection. Rates `date` is ISO 8601 UTC and candle timestamps
are ISO 8601. Support cannot establish listing currency, session convention,
price basis, adjustments, historical retention duration, or model-use permissions
from the API contract. Contract silence does not revoke the customer's rights
attestation. See [the current support record](docs/feed-collection.md#support-confirmed-metadata-repair-2026-10-03).

Separate listing-currency evidence is required before retention. Unknown session
or price/adjustment basis can be recorded for source-native observations, but
model fitting needs a supported, evidenced price-basis manifest value; do not
invent `unadjusted`. Portability needs session, close, adjustments and cost
comparability evidence. The latest 1,000 daily observations do not establish the
reserved history required by a portability evaluation.

## FMP and Kibot

Their comparison is retrospective robustness evidence, not independent
predictive confirmation. The current portability verdict remains inconclusive:
FMP adjustment, price-type, session, and timestamp semantics remain unresolved;
the volatility strategy breaches its frozen trigger-agreement tolerance; and the
supplied early-close and corporate-action cases are not a complete calendar.
See [strategy portability](docs/strategy-portability.md). Current source and
retention attestations must be checked before any private FMP/Kibot artifact is
read, replayed, refreshed, or reused.

## Forward journal

The forward journal contains pending evidence only. It is not a predictive
result until genuinely later approved observations mature and are scored; no
pending entry supports a profitability or improvement claim.

## Safe next actions

1. Use the recorded customer authorization; do not ask again for rights or a
   written model-use exception.
2. Obtain reliable listing-currency evidence and record unknown feed details
   honestly. Confirm price-basis/adjustments before enabling learner intake.
3. Obtain session alignment, timestamp meaning, close, adjustment and cost
   evidence before declaring eToro compatible with another feed. Keep research
   protocols frozen.
4. Before FMP/Kibot reuse, verify their separate active subscription/retention
   attestations and preserve immutable dated input versions.
5. Score pending forward evidence only after genuinely later approved data arrives.

See [continuous research status](docs/continuous-research-goal-status.md) and
[feed collection evidence](docs/feed-collection.md) for the detailed evidence
and limits.
