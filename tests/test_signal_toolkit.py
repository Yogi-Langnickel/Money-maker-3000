from __future__ import annotations

import unittest
from datetime import date, timedelta

from money_maker_3000 import signal_toolkit as S
from money_maker_3000 import research_cycle as R


class SignalToolkitRegressionTests(unittest.TestCase):
    def test_extreme_input_is_controlled_rejection(self):
        with self.assertRaisesRegex(S.SignalError,'invalid-close'):
            S.evaluate_feature('simple-return',[{'date':'2024-01-01','close':100},
                {'date':'2024-01-02','close':10**1000}],completed_at='2024-01-02T00:00:00Z',
                available_at='2024-01-03T00:00:00Z')

    def test_simple_average_features_ignore_older_seed_history(self):
        closes=[100+i for i in range(20)]
        rows=[{'date':(date(2024,1,1)+timedelta(days=i)).isoformat(),
               'close':v,'high':v+1,'low':v-1} for i,v in enumerate(closes)]
        rows[0].update(close=500,high=501,low=499)
        rows[-1].update(close=117,high=118,low=116)
        args={'completed_at':'2024-01-20T00:00:00Z','available_at':'2024-01-21T00:00:00Z'}
        rsi=S.evaluate_feature('rsi',rows,**args)
        atr=S.evaluate_feature('normalized-atr',rows,ohlc_attested=True,ohlc_basis='unadjusted',**args)
        self.assertEqual(rsi['feature']['version'],'rsi-simple-average-14.v1')
        self.assertAlmostEqual(rsi['value'],100-100/(1+13))
        self.assertEqual(atr['feature']['version'],'normalized-atr-simple-average-14.v1')
        self.assertAlmostEqual(atr['value'],2/117)

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
