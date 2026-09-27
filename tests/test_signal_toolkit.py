from __future__ import annotations

import unittest

from money_maker_3000 import signal_toolkit as S
from money_maker_3000 import research_cycle as R


class SignalToolkitRegressionTests(unittest.TestCase):
    def test_completion_never_becomes_availability_and_fractional_ordering_is_exact(self):
        rows = [{"date": "2024-01-01", "close": 100}, {"date": "2024-01-02", "close": 101}]
        available = S.evaluate_feature("simple-return", rows, completed_at="2024-01-02T00:00:00.000001Z", available_at="2024-01-02T00:00:00.000002Z")
        self.assertEqual(available["completedAt"], "2024-01-02T00:00:00.000001Z")
        self.assertEqual(available["availableAt"], "2024-01-02T00:00:00.000002Z")
        with self.assertRaisesRegex(S.SignalError, "completion-after-availability"):
            S.evaluate_feature("simple-return", rows, completed_at="2024-01-02T00:00:00.000002Z", available_at="2024-01-02T00:00:00.000001Z")

    def test_dictionary_rows_report_whole_cohort_coverage(self):
        rows = [{"date": "2024-01-01", "close": 100, "availableAt": "2024-01-02T00:00:00Z"},
                {"date": "2024-01-02", "close": 101, "availableAt": "2024-01-03T00:00:00Z"}]
        result = S.whole_cohort_coverage(rows, completed_at="2024-01-02T00:00:00Z", available_at="2024-01-03T00:00:00Z")
        self.assertEqual({entry["feature"] for entry in result["coverage"]}, {"simple-return", "rsi", "normalized-atr"})

    def test_legacy_successor_provenance_keeps_predecessor_identity(self):
        model = {"protocolId": "successor", "frozenAt": "later", "retention": {}, "interpretation": {},
                 "signalFeatureBundleSha256": "x" * 64, "signalFeaturePolicy": {}, "modelIdentity": "legacy"}
        self.assertEqual(R.incumbent_provenance(model), {"modelIdentity": "legacy"})
