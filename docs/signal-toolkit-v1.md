# Signal Toolkit v1

The offline toolkit freezes a small vetted indicator registry (simple return,
RSI and normalized ATR) for research diagnostics. Completion and availability
are separate full UTC timestamps; a completed candle cannot be treated as
available merely because it exists. Timestamp checks retain fractional seconds.

It accepts both dictionary and object rows, never derives OHLC from close, and
reports whole-cohort availability rather than matched-only coverage. Feature
bundles carry the registry, OHLC attestation/basis, row digest, completion and
availability times. Toolkit context is successor context: semantic replay keeps
the predecessor model identity when a successor changes that context.

The toolkit creates no forecasts, recommendation, order intent or execution.

RSI and normalized ATR use arithmetic averages over the last 14 changes/ranges,
not Wilder recursive smoothing. Their feature identities are
`rsi-simple-average-14.v1` and `normalized-atr-simple-average-14.v1`. Earlier draft
artifacts bearing Wilder labels must not be accepted as equivalent definitions;
regenerate from exact permitted local input rather than relabeling saved evidence.


## Autonomous v1 update

Point-in-time repair: every explicitly supplied row availability must be valid and no later than the decision availability. Empty/false timestamps are invalid. ATR requires consistent low/open/close/high ranges in addition to attested OHLC meaning. Completion alone cannot authorize a late historical row.
