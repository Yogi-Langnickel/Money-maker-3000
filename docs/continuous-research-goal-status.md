# Continuous research milestone scope

The implementation joins bounded source-native historical research, immutable
forward forecasts and correction-aware scoring, source-pair portability evidence,
and a short configured workflow. Simulation and execution boundaries remain
unchanged. Historical scoring tests are labelled synthetic contract or
retrospective evidence; pending forecasts are not improvement evidence.

The real eToro acquisition and first eToro forward predictions remain externally
blocked. The account holder supplied eToro support guidance for the earlier
Cloudflare 1010 edge block, and a later metadata-only request using the fixed
non-browser User-Agent reached HTTP 200. That request did not establish usable
semantic intake: explicitly projecting `instrumentId` produced a duplicate JSON
key, so the collector omits only that redundant projection. The corrected search
returned a unique SPY match with an ID, display name, and exchange, but its
runtime response omitted `instrumentType`. The official [search schema](https://api-portal.etoro.com/api-reference/market-data/search-for-instruments)
lists `instrumentType` as a string response field, so this discrepancy remains
unresolved. The collector stays fail-closed at
`instrument-type-or-exchange-unverified`; it does not infer ETF status. No
history or price data was retained, and this metadata observation provides no
research, predictive, or portability proof.

A separate sanitized, non-retained market-data exploration establishes only
transport and parser shape. Search type-field variants continued to return an
exact SPY match with ID, display name, and exchange while omitting both text and
numeric type fields. A `OneDay/1` candle response exposed OHLCV fields. A
`OneDay/1000` response contained 1,000 candles; the strict parser accepted 999
complete rows from 2022-08-22 through 2026-09-18, excluded one unfinished
current candle, and found no duplicates. OHLC values were numeric; volume was
null in 188 rows from 2022-08-22 through 2023-05-19. Timestamps, session and
close conventions, price basis, corporate-action adjustments, and costs remain
unresolved. No values or payload were retained, and this does not make the feed
eligible for retention, research, prediction, or portability.

Separately, this workflow conservatively applies Part V 1.7's model-training
restriction to its probability fitting and retains the written model-use evidence
gate. Ordinary personal API use and storage within Permitted Use are not subject
to a blanket exception requirement. See [collection evidence](feed-collection.md)
for the official guidance and remaining interpretation limits. VAS NAV remains
supplemental until eligible market-price history is available.

Private FMP/Kibot inputs, artifacts, reports, and their hashes are excluded from
this document and from Git. Current source approvals and retention attestations
must be checked before reproducing a private cycle. Delivery of code does not
complete an externally blocked real-data acceptance criterion, nor establish
predictive improvement or profitability.
