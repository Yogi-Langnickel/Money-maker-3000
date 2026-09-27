from __future__ import annotations

import tempfile
import unittest
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path

from money_maker_3000 import profit_hypothesis as P
from money_maker_3000 import signal_toolkit as S
from money_maker_3000 import research_cycle as R
from money_maker_3000 import cli


def rows(count: int = 48, *, downward: bool = False, interval: str = "1d"):
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    values = []
    for index in range(count):
        when = start + timedelta(days=index)
        # A modest trend normally earns more than a 500bp round-trip cost,
        # while negative gap bars supply a deterministic losing case.
        close = 100 + index * .7
        if downward and index % 5 == 4:
            close -= 16
        open_value = (100 + (index - 1) * .7) if index else 100
        if downward and index % 5 == 4:
            open_value = close + 2
        values.append({"start": when.isoformat().replace("+00:00", "Z"),
                       "end": (when + timedelta(hours=20)).isoformat().replace("+00:00", "Z"),
                       "availableAt": (when + timedelta(hours=21)).isoformat().replace("+00:00", "Z"),
                       "open": open_value, "high": max(open_value, close) + 1, "low": min(open_value, close) - 1,
                       "close": close, "volume": 1000, "provenance": {"source": "synthetic", "adjustmentBasis": "unadjusted", "retrievedAt": "2024-03-01T00:00:00Z", "rights": "synthetic-test-only"}})
    return values


def config(count: int = 48, *, downward: bool = False):
    data = rows(count, downward=downward)
    return {"version": P.VERSION, "classification": "synthetic", "primaryInterval": "1d", "costBps": 10,
            "instrument": {"symbol": "SPY", "product": "ETF", "currency": "USD", "session": "US-equities-regular", "source": "synthetic", "rights": "synthetic-test-only", "adjustmentBasis": "unadjusted"},
            "datasets": {"1d": data, "4h": data}}


class ProfitHypothesisTests(unittest.TestCase):
    def test_profitable_and_unprofitable_simulated_cases(self):
        candidate = P.frozen_hypotheses(0)[0]
        profitable = P.backtest(candidate, rows(), end_index=47)
        losing = P.backtest(candidate, rows(downward=True), end_index=47)
        self.assertGreater(profitable["netSimulatedReturn"], 0)
        self.assertLess(losing["netSimulatedReturn"], profitable["netSimulatedReturn"])
        self.assertTrue(profitable["simulated"])

    def test_costs_eliminate_an_apparent_edge_and_never_fill_same_bar(self):
        candidate = P.frozen_hypotheses(0)[0]
        edge = P.backtest(candidate, rows(), cost_bps=0)
        expensive = P.backtest(candidate, rows(), cost_bps=500)
        self.assertGreater(edge["netSimulatedReturn"], expensive["netSimulatedReturn"])
        for entry in [item for item in edge["tradeLedger"] if item["type"] == "entry"]:
            self.assertEqual(entry["fillBar"], entry["signalBar"] + 1)

    def test_interval_contract_overlap_caps_and_exact_timestamps(self):
        dataset = P.validate_dataset(config())
        self.assertTrue(dataset["overlap"]["usable"])
        too_many = config(); too_many["datasets"]["1d"] = rows(1001)
        with self.assertRaisesRegex(P.HypothesisError, "candle-count"):
            P.validate_dataset(too_many)
        wrong = config(); wrong["instrument"]["product"] = "spot-gold"
        with self.assertRaisesRegex(P.HypothesisError, "instrument-substitution"):
            P.validate_dataset(wrong)
        history = [{"date": "2024-01-01", "close": 100}]
        with self.assertRaisesRegex(S.SignalError, "completion-after-availability"):
            S.evaluate_feature("simple-return", history, completed_at="2024-01-01T00:00:00.000001Z", available_at="2024-01-01T00:00:00Z")

    def test_dict_coverage_preserves_availability_not_completion(self):
        history = [{"date": "2024-01-01", "close": 100, "availableAt": "2024-01-02T00:00:00.123456Z"},
                   {"date": "2024-01-02", "close": 101, "availableAt": "2024-01-03T00:00:00.123456Z"}]
        coverage = S.whole_cohort_coverage(history, completed_at="2024-01-02T00:00:00Z", available_at="2024-01-03T00:00:00.123456Z")
        self.assertEqual(coverage["availableAt"], "2024-01-03T00:00:00.123456Z")
        self.assertEqual(coverage["coverage"][0]["endpoints"], 2)

    def test_insufficient_evidence_and_holdout_refinement_are_rejected(self):
        with self.assertRaisesRegex(P.HypothesisError, "insufficient-evidence"):
            P.run(config(24), allow_synthetic_smoke=True)
        refine = config(); refine["refinementRequested"] = True
        with self.assertRaisesRegex(P.HypothesisError, "holdout-refinement"):
            P.run(refine, allow_synthetic_smoke=True)

    def test_artifact_idempotent_recovery_and_frozen_retest_boundary(self):
        with tempfile.TemporaryDirectory() as root:
            first = P.run(config(), evidence_root=Path(root), allow_synthetic_smoke=True)
            second = P.run(config(), evidence_root=Path(root), allow_synthetic_smoke=True)
            self.assertEqual(first["sha256"], second["sha256"])
            self.assertTrue((Path(root) / first["sha256"] / "report.json").exists())
        report = {"version": P.VERSION, "trials": [{"grade": "weak", "hypothesis": P.frozen_hypotheses(10)[0]}]}
        report["sha256"] = P._digest(report)
        retuned = config(); retuned["retune"] = True
        with self.assertRaisesRegex(P.HypothesisError, "retuning-requires"):
            P.frozen_retest(report, retuned, allow_synthetic_smoke=True)

    def test_successor_provenance_drops_only_successor_toolkit_context(self):
        predecessor = {"protocolId": "new", "frozenAt": "new", "retention": {}, "interpretation": {},
                       "signalFeatureBundleSha256": "a" * 64, "signalFeaturePolicy": {"attested": False}, "stable": "identity"}
        self.assertEqual(R.incumbent_provenance(predecessor), {"stable": "identity"})

    def test_cli_synthetic_smoke_invokes_real_workflow(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "config.json"
            path.write_text(__import__("json").dumps(config()), encoding="utf-8")
            output = io.StringIO()
            from unittest.mock import patch
            with patch("sys.stdout", output):
                status = cli.main(["profit-hypothesis", "--config", str(path), "--evidence-root", str(Path(root) / "evidence"), "--allow-synthetic-smoke"])
            self.assertEqual(status, 0)
            self.assertIn('"simulated": true', output.getvalue())
