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
