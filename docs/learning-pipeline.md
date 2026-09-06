# Offline learning pipeline v1

The pipeline fits a supervised probability model from local daily OHLCV history,
selects a predefined feature configuration on validation data, saves a frozen JSON
model, and evaluates it on a later chronological holdout. The output is a
classification diagnostic. It cannot place orders, change the automated runner,
or establish research or trading readiness.

Only synthetic contract fixtures are currently supplied. Their explicit smoke
mode demonstrates the plumbing; training on verified observed market data still
requires a suitable local dataset and provenance review. No download, provider,
credential, account, or execution integration is included.

## Run the supplied synthetic smoke

From the repository root, create an ignored local output directory, then run:

```sh
mkdir -p .local/learning
PYTHONPATH=src python3.13 -m money_maker_3000.cli learning-train \
  --history-csv tests/fixtures/market_history/spy-slow-trend-202-daily.csv \
  --dataset-manifest contracts/learning-synthetic-spy.json \
  --strategy volatility-band-accumulator \
  --horizon-bars 5 \
  --train-end 2025-06-02 \
  --validation-end 2025-08-01 \
  --allow-synthetic-smoke \
  --output .local/learning/spy-smoke-v1.json

PYTHONPATH=src python3.13 -m money_maker_3000.cli learning-predict \
  --model .local/learning/spy-smoke-v1.json \
  --history-csv tests/fixtures/market_history/spy-slow-trend-202-daily.csv \
  --dataset-manifest contracts/learning-synthetic-spy.json \
  --allow-synthetic-smoke
```

The training command prints and saves the same bundle. Prediction prints its
probability, observed feature state, training support, and prior-fallback status.
Both retain `candidateIntent: skip` and `researchReady: false`. This particular
fixture increases monotonically: `oneClassTraining` and `constantTrainingFeature`
are true, and it does not improve on the train-only prior. Its forecast is marked
`historical-overlap` because the input endpoint was in the evaluation dataset.

An existing output is never overwritten. Use a new output filename for a repeat
run and compare the bytes to check deterministic replay. Repeating the same
holdout is not independent scientific evidence.

## Learning method and boundaries

The target is `close[t + H] > close[t]`. Equal or lower prices have label zero.
`H` is 1–20 **observed bars**, not calendar days. Date gaps are not filled. Rows
are treated as daily observations; no exchange-calendar, adjustment, or data
vendor verification is performed.

Features are the existing terminal strategy-history states, calculated from
bounded windows ending at each observation. The fixed, registry-validated grids
are:

| Strategy | Candidate parameters | Common warmup |
| --- | --- | --- |
| `volatility-band-accumulator` | Lookback 10, 20, 40 × decline trigger 2%, 3%, 5% | 40 bars |
| `slow-trend-allocation` | Short/long/confirmation 10/60/1, 20/100/3, 50/200/3 | 202 bars |

Every candidate uses the same eligible feature endpoints. `--train-end` and
`--validation-end` are inclusive observed dates. Training features and their
future labels must both end by the training cutoff. Validation features begin
later and their labels must end by the validation cutoff. Holdout features begin
after validation. Endpoints whose labels cross a cutoff are purged, and the final
H bars have no labels. Minimum retained counts are 20 training, 10 validation,
and 10 holdout observations. These are software minimums, not statistical
adequacy thresholds. The default slow-trend grid cannot train on the supplied
202-bar fixture.

Training fits a separate label frequency for each categorical state with Laplace
smoothing: `(positive labels + 1) / (state support + 2)`. States with fewer than
five training samples, including unseen states, use the global training prior
`(all training positives + 1) / (all training samples + 2)`. Saved state counts
and probabilities make the learned model inspectable.

Validation selects the lowest mean squared probability error (binary Brier
score, range 0–1), breaking exact ties by fixed grid index. The train-only global
prior is also scored on validation and holdout. The selected model is frozen
without refitting and evaluated on holdout once per invocation. Reports include
split date bounds/counts, purged endpoints, sparse/unseen fallback counts,
one-class/constant-feature flags, and whether validation beats the prior.

Overlapping labels are dependent; one holdout provides limited evidence. Repeated
inspection can bias later decisions. The pipeline has no return objective,
transaction-cost model, execution simulation, profitability metric, or automated
promotion. Conceptual references: scikit-learn's
[Brier score definition](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.brier_score_loss.html)
and [time-series splitting guidance](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html).
The implementation uses only the Python standard library.

## Supply a local dataset

Keep observed CSVs and their manifests under ignored
`data/private/market-history/`. The exact CSV header is:

```text
symbol,date,open,high,low,close,volume,source
```

Use a single symbol from the initial ETF allowlist `SPY`, `QQQ`, `VAS`, strictly
increasing unique ISO dates, finite positive consistent OHLC prices, nonnegative
volume, and one exact source slug. Maximums are 10,000 rows, 8 MiB CSV, and
128 KiB JSON. Prices must be between 1e-8 and 1e12, volume at most 1e15.
Account-linked columns, extra columns, and missing fields fail closed.

Copy the shape of `contracts/learning-synthetic-spy.json` into a private manifest
and replace every value with evidence for the actual dataset. The exact required
fields are:

| Field | Meaning |
| --- | --- |
| `version` | Exactly `learning-dataset.v1` |
| `symbol` | Matching allowlisted symbol |
| `classification` | `synthetic` or `observed-attested` |
| `source` | Matching lowercase slug, 1–64 letters/digits/hyphens, starting with a letter |
| `sourceEvidence` | Actual origin evidence |
| `licenseEvidence` | Terms supporting the intended local use |
| `attribution` | Required attribution |
| `rightsEvidence` | Evidence of rights to use this dataset |
| `priceBasis` | `unadjusted`, `split-adjusted`, or `total-return-adjusted` |
| `sha256` | SHA-256 of the exact CSV bytes, including line endings |
| `rowCount` | Exact number of data rows |

The four evidence strings must contain 1–512 printable characters. They remain
in the input manifest; the output stores its digest and a source-identity digest.
For example, obtain a local CSV's checksum with `shasum -a 256 <csv-path>`.
Use `observed-attested` only for actual observed data with provenance evidence;
it means an **unverified operator attestation**, even when the checksum matches.
A checksum establishes byte identity, not authenticity, licensing, accuracy, or
corporate-action correctness. Known committed synthetic hashes and synthetic
source markers cannot be relabeled as observed. Arbitrary mislabeling cannot be
detected reliably, so provenance review remains necessary.

Run `learning-train` with the actual CSV, private manifest, dates, and fresh
output path; omit `--allow-synthetic-smoke` for observed-attested inputs.
Insufficient or unavailable data returns nonzero and creates no model. Do not
commit observed datasets, private manifests, model bundles, or reports.

## Frozen prediction and artifact handling

Prediction consumes the whole supplied prefix, uses only its final bounded
feature window, and needs no future labels. Symbol, price basis, classification,
and source identity must match training. Its endpoint must follow the last
validation label used for selection. Endpoints at or before the original dataset
end are marked `historical-overlap`; later endpoints are marked
`post-training-dataset`. Neither designation proves freshness or predictive
quality, and synthetic models retain smoke status.

Bundles use strict JSON with duplicate/unknown-key rejection, a versioned exact
schema, canonical SHA-256, fixed candidate checks, split/count parity, and learned
probability reconstruction. They contain no executable model serialization.
The digest detects accidental edits; it is not a signature and cannot establish
trust against someone who can rewrite both the artifact and digest. Saved evaluation
metrics receive range and structural checks; loading does not recompute them from
source history. Full metric verification requires the original dataset and rerun.
Changes to feature reducer semantics or the fixed candidate grid require a pipeline
artifact `VERSION` bump so older models cannot be silently reinterpreted.

Files are read with bounded no-follow regular-file checks. Artifacts are created
exclusively with mode 0600, never replacing an existing file or symlink. Parent
directories must already exist and be trusted; the application does not defend
against malicious changes to path ancestors or same-user tampering. An interrupted
write can leave an incomplete file, which the loader rejects; choose a new output
name after inspecting such a failure. CLI errors are value-blind and profiling is
disabled for these commands to prevent private path disclosure.
