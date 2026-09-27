# Instrument Profit Hypothesis Research v1

`profit-hypothesis` is an offline, one-instrument research workflow. It accepts
only already-local candles and produces explicitly labelled **simulated** P&L
diagnostics. It has no collector, credentials, provider client, order route,
account balance, leverage, short, CFD, derivative, crypto, or execution path.
Synthetic input requires `--allow-synthetic-smoke` and proves mechanics only;
it never demonstrates empirical or future profitability.

## Contract

The input is `instrument-profit-hypothesis.v1`: an exact SPY, QQQ, or VAS ETF
contract (symbol, ETF product, currency, regular session, source, rights and
adjustment basis) plus 2–4 local `1h`, `4h`, `1d`, or `1w` candle lists. Each
list is capped at 1,000 completed candles. Every candle has UTC start, end and
availability timestamps, genuine OHLCV, and source/rights/retrieval/basis
provenance. A requested product not in that contract is rejected: GLD is not
spot gold, and neither is a CFD silently substituted.

Coverage reports preserve each interval's actual first/last start/end and
availability facts plus their overlap. The workflow never invents a page,
candle, OHLC field, adjustment, or availability time. A primary-bar signal is
eligible only if each other supplied timeframe had a completed candle available
at that primary bar's availability time; fills are at the next executable
primary-bar open.

## Rules and evaluation

The fixed small register contains a three-bar trend rule and a declared
RSI-14-below-70 addition. Parameters, indicator roles, entry, conservative gap/
intrabar stop policy, three-bar maximum hold, all-cash long-only sizing and
costs are frozen in the report. The second rule records incremental simulated
net return over the first. No executable code is generated.

The simulator records cash, one long position, costs, next-bar fills, marked-to-
market equity, gaps, ambiguous intrabar execution (stop first), and final open
positions. Results divide chronology into development and untouched evaluation
with fixed boundaries. Reported selection accounting includes every attempted
trial and comparison count. Holdout refinement is rejected unless a fresh
dataset/hypothesis is created.

The report separately names operational status, validation stage, grade and
grade reason. Its frozen rubric makes missing trade evidence `unclear`, a
nonpositive/non-benchmark-improving result `unlikely`, a non-robust positive
result `weak`, and `strong` a monitoring candidate only—not a profit promise.
It includes simulated net return, benchmark return, drawdown, expectancy,
trade count, exposure, gross turnover, explicit costs, double-cost sensitivity,
small-sample uncertainty and chronological stability.

Artifacts are canonical JSON: frozen rules/dataset hashes, trial ledger, equity
curve and report. The private artifact directory uses a 0700 directory, 0600
locked atomic links and exact-byte retry equality. A repeat run resumes safely;
a controlled invalid input can retain a value-blind failed-run record. Frozen
weak/strong candidates can be retested on a different permitted instrument only
with exact original rule bytes and cost; any retuning is a new hypothesis.

## Invocation

```sh
PYTHONPATH=src python3.13 -m money_maker_3000.cli profit-hypothesis \
  --config /private/path/synthetic-hypothesis.json \
  --evidence-root /private/path/hypothesis-evidence \
  --allow-synthetic-smoke
```

Do not pass a provider URL, credential, account data or execution option: none
is accepted. For a frozen cross-instrument check, use `profit-hypothesis-retest`
with the saved report and a new local-candle config; it rejects a `retune` flag.
