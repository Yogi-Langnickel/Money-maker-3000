from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from money_maker_3000.learning import (
    BOUNDARY, LearningError, _canonical, _digest, _fit, _state, candidate_grid,
    load_artifact, load_dataset, predict, train, validate_artifact, write_artifact,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/market_history/spy-slow-trend-202-daily.csv"


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.csv = self.root / "history.csv"
        self.manifest = self.root / "dataset.json"
        self.output = self.root / "model.json"
        day = date(2023, 1, 2)
        self.dates = []
        while len(self.dates) < 320:
            if day.weekday() < 5:
                self.dates.append(day.isoformat())
            day += timedelta(days=1)
        self.prices = [100 + 9 * math.sin(i / 7) + i / 30 for i in range(320)]
        self.source = "synthetic-learning-test"
        self.write_dataset()
        self.options = dict(strategy="volatility-band-accumulator", horizon_bars=5,
                            train_end=self.dates[169], validation_end=self.dates[249],
                            allow_synthetic_smoke=True)

    def write_dataset(self, *, classification="synthetic", symbol="SPY", price_basis="unadjusted"):
        rows = ["symbol,date,open,high,low,close,volume,source"]
        for day, close in zip(self.dates, self.prices):
            rows.append(f"{symbol},{day},{close},{close + 1},{close - 1},{close},1000000,{self.source}")
        self.csv.write_text("\n".join(rows) + "\n")
        self.write_manifest(classification=classification, symbol=symbol, price_basis=price_basis)

    def write_manifest(self, *, classification="synthetic", symbol="SPY", price_basis="unadjusted"):
        value = {"version": "learning-dataset.v1", "symbol": symbol,
                 "classification": classification, "source": self.source,
                 "sourceEvidence": "Ephemeral deterministic test series.",
                 "licenseEvidence": "Project test data", "attribution": "Test suite",
                 "rightsEvidence": "Generated test-only data", "priceBasis": price_basis,
                 "sha256": hashlib.sha256(self.csv.read_bytes()).hexdigest(),
                 "rowCount": len(self.dates)}
        self.manifest.write_text(json.dumps(value))
        return value

    def fit(self, **overrides):
        return train(self.csv, self.manifest, **{**self.options, **overrides})

    def reseal(self, artifact):
        artifact["sha256"] = _digest({k: v for k, v in artifact.items() if k != "sha256"})
        return artifact

    def test_documented_fixture_smoke(self):
        artifact = train(FIXTURE, ROOT / "contracts/learning-synthetic-spy.json",
                         strategy="volatility-band-accumulator", horizon_bars=5,
                         train_end="2025-06-02", validation_end="2025-08-01",
                         allow_synthetic_smoke=True)
        self.assertEqual([artifact["report"]["splits"][name]["count"]
                          for name in ("train", "validation", "holdout")], [64, 39, 45])
        self.assertTrue(artifact["report"]["oneClassTraining"])
        self.assertTrue(artifact["report"]["constantTrainingFeature"])
        self.assertFalse(artifact["report"]["validationBeatsPrior"])
        relabeled = copy.deepcopy(artifact)
        relabeled["model"]["classification"] = "observed-attested"
        relabeled["report"]["status"] = "offline-fit-provenance-unverified"
        with self.assertRaisesRegex(LearningError, "synthetic-relabel"):
            validate_artifact(self.reseal(relabeled))
        write_artifact(artifact, self.output)
        result = predict(self.output, FIXTURE, ROOT / "contracts/learning-synthetic-spy.json",
                         allow_synthetic_smoke=True)
        self.assertEqual(result["status"], "synthetic-smoke-only")

    def test_train_is_supervised_and_reproducible(self):
        first = self.fit()
        self.assertEqual(first, self.fit())
        self.assertEqual(first["boundary"], BOUNDARY)
        fit = first["model"]["fit"]
        self.assertGreater(sum(item["support"] > 0 for item in fit["states"].values()), 1)
        self.assertGreater(len({item["probabilityUp"] for item in fit["states"].values()}), 1)
        self.assertEqual(first["report"]["status"], "synthetic-smoke-only")
        self.assertFalse(first["report"]["oneClassTraining"])

    def test_laplace_fit_and_unseen_sparse_prior(self):
        fit = _fit(["trend-confirmed"] * 21 + ["trend-not-confirmed"] * 2,
                   [1] * 20 + [0] * 3, "slow-trend-allocation")
        self.assertEqual(fit["priorProbabilityUp"], 21 / 25)
        self.assertEqual(fit["states"]["trend-confirmed"]["probabilityUp"], 21 / 23)
        self.assertEqual(fit["states"]["trend-not-confirmed"]["probabilityUp"], 21 / 25)
        self.assertTrue(fit["states"]["trend-not-confirmed"]["usesPrior"])
        unseen = _fit(["trend-confirmed"] * 20, [0] * 20, "slow-trend-allocation")
        self.assertEqual(unseen["states"]["trend-not-confirmed"]["support"], 0)
        self.assertEqual(unseen["states"]["trend-not-confirmed"]["probabilityUp"], 1 / 22)

    def test_holdout_changes_never_change_fit_or_selection(self):
        before = self.fit()
        self.prices[250:] = [100 + i * 0.5 for i in range(70)]
        self.write_dataset()
        after = self.fit()
        self.assertEqual(before["model"], after["model"])
        self.assertEqual(before["report"]["candidates"], after["report"]["candidates"])
        self.assertEqual(before["report"]["selectedCandidate"], after["report"]["selectedCandidate"])
        self.assertNotEqual(before["report"]["holdoutBrier"], after["report"]["holdoutBrier"])

    def test_validation_never_refits_counts(self):
        self.prices[170:250] = [90 + i * 0.2 for i in range(80)]
        self.write_dataset()
        artifact = self.fit()
        bars, _ = load_dataset(self.csv, self.manifest, allow_synthetic_smoke=True)
        indices = list(range(39, 165))
        labels = [int(bars[i + 5].close > bars[i].close) for i in indices]
        model = artifact["model"]
        states = [_state(bars, i, model["strategy"], model["parameters"]) for i in indices]
        self.assertEqual(model["fit"], _fit(states, labels, model["strategy"]))
        self.assertEqual(model["fit"]["support"], 126)

    def test_purging_uniform_dates_and_bar_horizon(self):
        artifact = self.fit()
        report = artifact["report"]
        splits = report["splits"]
        self.assertEqual(report["warmupBars"], 40)
        self.assertEqual(report["purgedEndpointCount"], 10)
        self.assertEqual(splits["train"]["lastFeatureDate"], self.dates[164])
        self.assertEqual(splits["train"]["lastLabelDate"], self.dates[169])
        self.assertEqual(splits["validation"]["firstFeatureDate"], self.dates[170])
        self.assertEqual(splits["validation"]["lastLabelDate"], self.dates[249])
        self.assertEqual(splits["holdout"]["firstFeatureDate"], self.dates[250])
        self.assertEqual(splits["holdout"]["lastLabelDate"], self.dates[319])
        self.assertEqual(splits["train"]["firstFeatureDate"], self.dates[39])

    def test_constant_data_tie_and_baseline_are_explicit(self):
        self.prices = [100.0] * 320
        self.write_dataset()
        artifact = self.fit()
        report = artifact["report"]
        self.assertTrue(report["oneClassTraining"])
        self.assertTrue(report["constantTrainingFeature"])
        self.assertFalse(report["validationBeatsPrior"])
        self.assertEqual(report["selectedCandidate"], 0)
        self.assertEqual(report["candidates"][0]["validationBrier"], report["baselineValidationBrier"])
        self.assertFalse(artifact["boundary"]["researchReady"])

    def test_default_slow_grid_requires_sufficient_history(self):
        with self.assertRaisesRegex(LearningError, "insufficient-learning-splits"):
            self.fit(strategy="slow-trend-allocation")
        self.fit(strategy="slow-trend-allocation", train_end=self.dates[239], validation_end=self.dates[279])
        self.assertEqual(len(candidate_grid("slow-trend-allocation")), 3)

    def test_parameter_and_date_fail_closed(self):
        for overrides in ({"horizon_bars": True}, {"horizon_bars": 0}, {"horizon_bars": 21},
                          {"strategy": "operator-code"}, {"train_end": "2023-02-30"},
                          {"train_end": self.dates[250]}, {"validation_end": "2024-12-31"}):
            with self.subTest(overrides=overrides), self.assertRaises(LearningError):
                self.fit(**overrides)

    def test_synthetic_opt_in_and_relabel_rejection(self):
        with self.assertRaisesRegex(LearningError, "synthetic-smoke-opt-in"):
            self.fit(allow_synthetic_smoke=False)
        self.write_manifest(classification="observed-attested")
        with self.assertRaisesRegex(LearningError, "synthetic-relabel"):
            self.fit()

    def test_known_synthetic_hash_cannot_be_attested_observed(self):
        self.csv.write_bytes(FIXTURE.read_bytes())
        self.dates = [row.split(",")[1] for row in self.csv.read_text().splitlines()[1:]]
        self.source = "synthetic-test-fixture"
        self.write_manifest(classification="observed-attested")
        with self.assertRaisesRegex(LearningError, "synthetic-relabel"):
            load_dataset(self.csv, self.manifest, allow_synthetic_smoke=True)

    def test_attestation_does_not_claim_verification(self):
        self.source = "operator-dataset"
        self.write_dataset(classification="observed-attested")
        artifact = self.fit(allow_synthetic_smoke=False)
        self.assertEqual(artifact["report"]["status"], "offline-fit-provenance-unverified")
        self.assertEqual(artifact["report"]["provenanceVerification"], "operator-attestation-unverified")
        self.assertNotIn("Ephemeral", json.dumps(artifact))

    def test_manifest_strictness_checksum_and_rows(self):
        value = json.loads(self.manifest.read_text())
        for key, replacement in (("unexpected", True), ("rowCount", True), ("rowCount", 10001),
                                 ("sha256", "0" * 64), ("rowCount", 319), ("symbol", "BTC"),
                                 ("source", "secret@example.com"), ("licenseEvidence", "")):
            with self.subTest(key=key, replacement=replacement):
                changed = {**value, key: replacement}
                self.manifest.write_text(json.dumps(changed))
                with self.assertRaises(LearningError):
                    self.fit()
        self.manifest.write_text('{"version":1,"version":2}')
        with self.assertRaises(LearningError):
            self.fit()

    def test_invalid_history_and_numerical_bounds(self):
        original = self.csv.read_text()
        rows = original.splitlines()
        for row in (rows[1].replace("1000000", "NaN"), rows[1].replace(self.dates[0], "bad-date"),
                    rows[1].replace(",100.0,", ",1e309,"), rows[1] + ",extra"):
            self.csv.write_text("\n".join([rows[0], row] + rows[2:]) + "\n")
            self.write_manifest()
            with self.assertRaises(LearningError):
                self.fit()
        self.csv.write_text(original.replace("1000000", "1e100"))
        self.write_manifest()
        with self.assertRaisesRegex(LearningError, "unsupported-volume-range"):
            self.fit()
        self.csv.write_text(rows[0] + "\n")
        self.write_manifest()
        with self.assertRaises(LearningError):
            self.fit()

    def test_input_symlink_fifo_oversize_and_missing(self):
        link = self.root / "link.csv"
        link.symlink_to(self.csv)
        with self.assertRaises(LearningError):
            load_dataset(link, self.manifest, allow_synthetic_smoke=True)
        fifo = self.root / "pipe"
        os.mkfifo(fifo)
        with self.assertRaises(LearningError):
            load_dataset(fifo, self.manifest, allow_synthetic_smoke=True)
        self.csv.unlink()
        with self.assertRaises(LearningError):
            self.fit()
        with self.csv.open("wb") as handle:
            handle.truncate(8 * 1024 * 1024 + 1)
        with self.assertRaises(LearningError):
            self.fit()

    def test_artifact_roundtrip_exclusive_output_and_modes(self):
        artifact = self.fit()
        write_artifact(artifact, self.output)
        self.assertEqual(load_artifact(self.output), artifact)
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o600)
        original = self.output.read_bytes()
        with self.assertRaises(LearningError):
            write_artifact(artifact, self.output)
        self.assertEqual(self.output.read_bytes(), original)
        link = self.root / "link.json"
        link.symlink_to(self.output)
        with self.assertRaises(LearningError):
            write_artifact(artifact, link)
        with self.assertRaises(LearningError):
            load_artifact(link)

    def test_artifact_tamper_even_with_recomputed_checksum(self):
        base = self.fit()
        changes = [
            lambda x: x["model"]["fit"].update(priorProbabilityUp=0.3),
            lambda x: x["model"]["fit"].update(support=True),
            lambda x: x["model"]["fit"]["states"]["trigger-observed"].update(probabilityUp=0.8),
            lambda x: x["model"].update(parameters={"lookbackDays": 11, "dropTriggerPct": 2.0}),
            lambda x: x["model"].update(unexpected="value"),
            lambda x: x["boundary"].update(candidateIntent="buy"),
            lambda x: x["report"].update(holdoutBrier=float("inf")),
            lambda x: x["report"].update(selectedCandidate=100),
            lambda x: x["report"]["splits"]["train"].update(count=99),
            lambda x: x["report"].update(purgedEndpointCount=True),
            lambda x: x["report"].update(baselineValidationBrier=0.999),
            lambda x: x["report"].update(oneClassTraining=1),
        ]
        for change in changes:
            changed = copy.deepcopy(base)
            change(changed)
            with self.subTest(change=change), self.assertRaises((LearningError, ValueError)):
                validate_artifact(self.reseal(changed))
        self.output.write_text(json.dumps({**base, "sha256": "0" * 64}))
        with self.assertRaises(LearningError):
            load_artifact(self.output)
        self.output.write_text('{"version":1,"version":2}')
        with self.assertRaises(LearningError):
            load_artifact(self.output)

    def test_prediction_roundtrip_compatibility_overlap_and_cutoff(self):
        artifact = self.fit()
        write_artifact(artifact, self.output)
        prediction = predict(self.output, self.csv, self.manifest, allow_synthetic_smoke=True)
        self.assertEqual(prediction["evaluationContext"], "historical-overlap")
        state = prediction["state"]
        self.assertEqual(prediction["probabilityUp"], artifact["model"]["fit"]["states"][state]["probabilityUp"])
        self.assertEqual(prediction["boundary"], BOUNDARY)
        self.write_manifest(price_basis="split-adjusted")
        with self.assertRaisesRegex(LearningError, "prediction-dataset-incompatible"):
            predict(self.output, self.csv, self.manifest, allow_synthetic_smoke=True)
        self.source = "synthetic-other-source"
        self.write_dataset()
        with self.assertRaisesRegex(LearningError, "prediction-source-incompatible"):
            predict(self.output, self.csv, self.manifest, allow_synthetic_smoke=True)
        self.source = "synthetic-learning-test"
        self.dates, self.prices = self.dates[:250], self.prices[:250]
        self.write_dataset()
        with self.assertRaisesRegex(LearningError, "prediction-before-selection-cutoff"):
            predict(self.output, self.csv, self.manifest, allow_synthetic_smoke=True)

    def test_prediction_is_prefix_only_and_post_dataset_labeled(self):
        artifact = self.fit()
        write_artifact(artifact, self.output)
        self.dates = self.dates + [(date.fromisoformat(self.dates[-1]) + timedelta(days=1)).isoformat()]
        self.prices = self.prices + [105]
        self.write_dataset()
        result = predict(self.output, self.csv, self.manifest, allow_synthetic_smoke=True)
        self.assertEqual(result["evaluationContext"], "post-training-dataset")
        self.assertEqual(result["asOfDate"], self.dates[-1])

    def cli(self, *args):
        return subprocess.run([sys.executable, "-m", "money_maker_3000.cli", *map(str, args)],
                              cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
                              capture_output=True, text=True, timeout=20)

    def test_cli_argument_failures_are_redacted_and_help_preserved(self):
        private_value = "token-do-not-echo"
        train_args = ["learning-train", "--history-csv", self.csv, "--dataset-manifest", self.manifest,
                      "--train-end", self.options["train_end"], "--validation-end", self.options["validation_end"],
                      "--output", self.output, "--allow-synthetic-smoke"]
        predict_args = ["learning-predict", "--model", self.output, "--history-csv", self.csv,
                        "--dataset-manifest", self.manifest]
        for args in ((*train_args, "--horizon-bars", private_value),
                     (*train_args, "--" + private_value),
                     (*predict_args, "--" + private_value),
                     ("learning-train", "--horizon-bars", private_value)):
            with self.subTest(args=args):
                result = self.cli(*args)
                self.assertEqual(result.returncode, 2)
                self.assertIn("invalid-learning-arguments", result.stderr)
                self.assertNotIn(private_value, result.stderr)
                self.assertNotIn(str(self.root), result.stderr)
                self.assertFalse(self.output.exists())
        for command in ("learning-train", "learning-predict", "backtest"):
            result = self.cli(command, "--help")
            self.assertEqual(result.returncode, 0)
            self.assertIn("usage:", result.stdout)
        result = self.cli("backtest", "--budget-usd", "not-a-number")
        self.assertEqual(result.returncode, 2)
        self.assertIn("not-a-number", result.stderr)
        result = self.cli("backtest", "--unknown-flag")
        self.assertEqual(result.returncode, 2)
        self.assertIn("unrecognized arguments: --unknown-flag", result.stderr)

    def test_cli_train_predict_and_sanitized_failures(self):
        args = ["learning-train", "--history-csv", self.csv, "--dataset-manifest", self.manifest,
                "--train-end", self.options["train_end"], "--validation-end", self.options["validation_end"],
                "--output", self.output, "--allow-synthetic-smoke"]
        result = self.cli(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), load_artifact(self.output))
        result = self.cli("learning-predict", "--model", self.output, "--history-csv", self.csv,
                          "--dataset-manifest", self.manifest, "--allow-synthetic-smoke")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.cli(*args)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr.strip(), "artifact-output-unavailable-or-exists")
        self.assertNotIn(str(self.root), result.stderr)
        profile = self.root / "profile.prof"
        result = self.cli("--profile", profile, *args)
        self.assertEqual(result.stderr.strip(), "learning-profile-disabled")
        self.assertFalse(profile.exists())
        self.csv.write_text("token_customer_secret_header\n")
        self.write_manifest()
        result = self.cli(*args)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("token_customer", result.stderr)
        self.assertFalse(result.stdout)


if __name__ == "__main__":
    unittest.main()
