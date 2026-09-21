# Money-maker-3000 research unblock record

Updated: 2026-09-21

Money Maker remains simulation-only. Provider data cannot create an order
intent, and demo/live execution, account reads, credentials in evidence, and
provider mutation remain disabled.

## Resolved

- The earlier Cloudflare 1010 edge block has a documented transport path: the
  account holder supplied eToro support guidance, and the fixed non-browser
  User-Agent reached HTTP 200 for a metadata-only request.
- A sanitized, non-retained structural exploration established that instrument
  search can return the exact SPY match, identifier, display name, and exchange,
  and that the daily endpoint exposes OHLCV-shaped candles. It did not retain
  values, payloads, request identifiers, prices, or account data.

## Active eToro research blockers

- The [official search schema](https://api-portal.etoro.com/api-reference/market-data/search-for-instruments)
  documents `instrumentType` and `instrumentTypeID`, but both were absent in
  the runtime type-field variants. The collector must not infer ETF status from
  name or exchange, so semantic intake remains fail-closed.
- Instrument identity, listing currency, timestamps, session boundaries, close
  or price basis, corporate-action adjustments, and cost treatment remain
  unresolved.
- The available daily response is capped at the latest 1,000 observations; it
  has no documented date cursor. It does not yet establish the reserved history
  needed for a portability evaluation.
- The separate retention and model-use evidence gate remains closed. A
  successful metadata or candle response does not authorize storage, research,
  model fitting, or source portability.

## FMP and Kibot

Their comparison is retrospective robustness evidence, not independent
predictive confirmation. The current portability verdict remains inconclusive.
Current source and retention attestations must be checked before any private
FMP/Kibot artifact is read, replayed, refreshed, or reused.

## Forward journal

The forward journal contains pending evidence only. It is not a predictive
result until genuinely later approved observations mature and are scored; no
pending entry supports a profitability or improvement claim.

## Safe next actions

1. Ask eToro to explain why its documented type fields are absent at runtime and
   identify the supported read-only way to verify type, currency, session, and
   price semantics. Do not provide credentials, request IDs, or raw payloads.
2. Obtain reviewed written evidence for eToro retention and model use before
   retaining any observations or derived artifacts.
3. Obtain source documentation for timestamp, session, price basis, adjustment,
   and cost treatment before declaring eToro compatible with another feed.
4. Before FMP/Kibot reuse or refresh, verify the applicable active subscription
   and retention attestations, then preserve a new immutable, dated input
   version rather than overwriting prior evidence.
5. Score the forward journal only when new approved observations have arrived;
   keep source-pair evaluation frozen and do not retune for a target source.

See [continuous research status](docs/continuous-research-goal-status.md) and
[feed collection evidence](docs/feed-collection.md) for the detailed evidence
and limits.
