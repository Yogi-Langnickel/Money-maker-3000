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
    duration = {"1h": timedelta(hours=1), "4h": timedelta(hours=4), "1d": timedelta(days=1), "1w": timedelta(days=7)}[interval]
    values = []
    for index in range(count):
        when = start + duration * index
        # A modest trend normally earns more than a 500bp round-trip cost,
        # while negative gap bars supply a deterministic losing case.
        close = 100 + index * .7
        if downward and index % 5 == 4:
            close -= 16
        open_value = (100 + (index - 1) * .7) if index else 100
        if downward and index % 5 == 4:
            open_value = close + 2
        values.append({"start": when.isoformat().replace("+00:00", "Z"),
                       "end": (when + duration).isoformat().replace("+00:00", "Z"),
                       "availableAt": (when + duration).isoformat().replace("+00:00", "Z"),
                       "open": open_value, "high": max(open_value, close) + 1, "low": min(open_value, close) - 1,
                       "close": close, "volume": 1000, "provenance": {"source": "synthetic", "adjustmentBasis": "unadjusted", "retrievedAt": (when + duration).isoformat().replace("+00:00", "Z"), "rights": "synthetic-test-only"}})
    return values


def config(count: int = 48, *, downward: bool = False):
    data = rows(count, downward=downward, interval="1d")
    return {"version": P.VERSION, "classification": "synthetic", "primaryInterval": "1d", "costBps": 10,
            "instrument": {"symbol": "SPY", "product": "ETF", "currency": "USD", "session": "US-equities-regular", "source": "synthetic", "rights": "synthetic-test-only", "adjustmentBasis": "unadjusted"},
            "datasets": {"1d": data, "4h": rows(count, downward=downward, interval="4h"), "1w": rows(count, downward=downward, interval="1w")}}


class ProfitHypothesisTests(unittest.TestCase):
    def test_profitable_and_unprofitable_simulated_cases(self):
        candidate = P.frozen_hypotheses(0)[0]
        profitable = P.backtest(candidate, rows(), end_index=47)
        losing = P.backtest(candidate, rows(downward=True), end_index=47)
        self.assertGreater(profitable["netSimulatedReturn"], 0)
        self.assertLess(losing["netSimulatedReturn"], 0)
        self.assertTrue(profitable["simulated"])

    def test_costs_eliminate_an_apparent_edge_and_never_fill_same_bar(self):
        candidate = P.frozen_hypotheses(0)[0]
        edge = P.backtest(candidate, rows(), cost_bps=0)
        expensive = P.backtest(candidate, rows(), cost_bps=500)
        self.assertGreater(edge["netSimulatedReturn"], expensive["netSimulatedReturn"])
        self.assertLessEqual(expensive["netSimulatedReturn"], 0)
        for entry in [item for item in edge["tradeLedger"] if item["type"] == "entry"]:
            self.assertEqual(entry["fillBar"], entry["signalBar"] + 1)

    def test_late_earlier_candle_is_rejected_and_cannot_drive_a_signal(self):
        late = config()
        late["datasets"]["1d"][13]["availableAt"] = "2024-02-15T00:00:00Z"
        late["datasets"]["1d"][13]["provenance"]["retrievedAt"] = "2024-02-15T00:00:00Z"
        with self.assertRaisesRegex(P.HypothesisError, "nonmonotonic-availability"):
            P.validate_dataset(late)
        direct = rows()
        direct[13]["availableAt"] = "2024-02-15T00:00:00Z"
        # A direct backtest call receives the same eligibility protection: the
        # delayed bar is in the RSI lookback for signal 14, so no Jan-16 fill.
        result = P.backtest(P.frozen_hypotheses(10)[0], direct, end_index=15)
        self.assertEqual(result["tradeLedger"], [])

    def test_closed_pnl_expectancy_and_cash_reconcile_after_both_costs(self):
        result = P.backtest(P.frozen_hypotheses(100)[0], rows(), end_index=18)
        exits = [item for item in result["tradeLedger"] if item["type"] == "exit"]
        self.assertEqual(len(exits), 1)
        exit = exits[0]
        self.assertAlmostEqual(exit["simulatedNetPnl"], exit["notional"] - exit["cost"] - exit["entryCashSpent"])
        self.assertAlmostEqual(result["expectancy"], exit["simulatedNetPnl"])
        self.assertAlmostEqual(result["finalEquity"], result["initialCash"] + result["closedSimulatedPnl"])
        self.assertAlmostEqual(result["transactionCosts"], exit["entryCost"] + exit["cost"])

    def test_interval_contract_overlap_caps_and_exact_timestamps(self):
        dataset = P.validate_dataset(config())
        self.assertTrue(dataset["overlap"]["usable"])
        self.assertEqual(P.run(config(), allow_synthetic_smoke=True)["frozen"]["timeframeAlignment"]["roles"], {"1d": "primary", "4h": "lower-coverage-only", "1w": "higher-confirmation"})
        invalid_duration = config(); invalid_duration["datasets"]["4h"][0]["end"] = "2024-01-01T20:00:00Z"; invalid_duration["datasets"]["4h"][0]["availableAt"] = "2024-01-01T20:00:00Z"; invalid_duration["datasets"]["4h"][0]["provenance"]["retrievedAt"] = "2024-01-01T20:00:00Z"
        with self.assertRaisesRegex(P.HypothesisError, "interval-duration"):
            P.validate_dataset(invalid_duration)
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
        self.assertEqual(coverage["coverage"][0]["endpointAvailabilityEvidence"][0]["availableAt"], "2024-01-02T00:00:00.123456Z")

    def test_insufficient_evidence_and_holdout_refinement_are_rejected(self):
        with self.assertRaisesRegex(P.HypothesisError, "insufficient-evidence"):
            P.run(config(24), allow_synthetic_smoke=True)
        refine = config(); refine["refinementRequested"] = True
        with self.assertRaisesRegex(P.HypothesisError, "holdout-refinement"):
            P.run(refine, allow_synthetic_smoke=True)
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(P.HypothesisError, "insufficient-evidence"):
                P.run(config(24), evidence_root=Path(root), allow_synthetic_smoke=True)
            self.assertEqual(len(list(Path(root).glob("*/failed-run.json"))), 1)

    def test_artifact_idempotent_recovery_and_frozen_retest_boundary(self):
        with tempfile.TemporaryDirectory() as root:
            first = P.run(config(), evidence_root=Path(root), allow_synthetic_smoke=True)
            second = P.run(config(), evidence_root=Path(root), allow_synthetic_smoke=True)
            self.assertEqual(first["sha256"], second["sha256"])
            self.assertTrue((Path(root) / first["sha256"] / "report.json").exists())
            self.assertEqual(P.replay(first, config(), evidence_root=Path(root), allow_synthetic_smoke=True)["replay"], "verified")
            ledger = Path(root) / first["sha256"] / "trial-0-ledger.json"
            ledger.write_text('{"simulated":true,"trades":[]}', encoding="utf-8")
            with self.assertRaisesRegex(P.HypothesisError, "replay-artifact"):
                P.replay(first, config(), evidence_root=Path(root), allow_synthetic_smoke=True)
            changed = config(); changed["costBps"] = 11
            second_config = P.run(changed, evidence_root=Path(root), allow_synthetic_smoke=True)
            self.assertEqual(second_config["frozen"]["selectionAccounting"]["retainedAttemptCount"], 2)
        report = {"version": P.VERSION, "trials": [{"grade": "weak", "hypothesis": P.frozen_hypotheses(10)[0]}]}
        report["sha256"] = P._digest(report)
        retuned = config(); retuned["retune"] = True
        with self.assertRaisesRegex(P.HypothesisError, "retuning-requires"):
            P.frozen_retest(report, retuned, allow_synthetic_smoke=True)
        report = {"version": P.VERSION, "frozen": {"instrument": config()["instrument"]}, "trials": [{"grade": "weak", "hypothesis": P.frozen_hypotheses(10)[0]}]}
        report["sha256"] = P._digest(report)
        other = config(); other["instrument"] = {**other["instrument"], "symbol": "QQQ"}
        with tempfile.TemporaryDirectory() as root:
            retest = P.frozen_retest(report, other, evidence_root=Path(root), allow_synthetic_smoke=True)
            self.assertTrue(retest["frozenRuleRetest"])
            self.assertEqual(retest["originalReportSha256"], report["sha256"])
            self.assertEqual(retest["sha256"], P._digest({key: value for key, value in retest.items() if key != "sha256"}))
            stored = __import__("json").loads((Path(root) / retest["sha256"] / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(stored, retest)
            self.assertEqual(stored["originalReportSha256"], report["sha256"])
            self.assertTrue(stored["frozenRuleRetest"])
            self.assertIn("dataset", retest["frozen"])

    def test_observed_data_requires_existing_source_rights_gates(self):
        observed = config(); observed["classification"] = "observed-attested"
        with self.assertRaisesRegex(Exception, "observed-source-unapproved"):
            P.run(observed)

    def test_synthetic_nested_metadata_and_nonboolean_flags_reject_before_hash(self):
        for field,value in [('retention',{'accountToken':'sensitive-test-value'}),
                            ('interpretation',{'accountToken':'sensitive-test-value'}),
                            ('retune','sensitive-test-value'),('refinementRequested',1)]:
            data=config();data[field]=value
            with self.subTest(field=field), tempfile.TemporaryDirectory() as root:
                with self.assertRaises(P.HypothesisError):
                    P.run(data,evidence_root=Path(root),allow_synthetic_smoke=True)
                raw=next(Path(root).glob('*/failed-run.json')).read_text()
                self.assertNotIn('sensitive-test-value',raw)
                self.assertNotIn(P._digest(data),raw)

    def test_blocked_attempt_counts_conservatively_without_dataset_hash(self):
        import json
        with tempfile.TemporaryDirectory() as root:
            invalid=config();invalid['costBps']=-1
            for _ in range(2):
                with self.assertRaisesRegex(P.HypothesisError,'invalid-cost'):
                    P.run(invalid,evidence_root=Path(root),allow_synthetic_smoke=True)
            failed=list(Path(root).glob('*/failed-run.json'))
            self.assertEqual(len(failed),1)
            self.assertIsNone(json.loads(failed[0].read_text())['attemptScope']['datasetInputSha256'])
            report=P.run(config(),evidence_root=Path(root),allow_synthetic_smoke=True)
            self.assertEqual(report['frozen']['selectionAccounting']['retainedAttemptCount'],2)
            changed=config(49)
            other=P.run(changed,evidence_root=Path(root),allow_synthetic_smoke=True)
            self.assertEqual(other['frozen']['selectionAccounting']['retainedAttemptCount'],2)

    def test_unknown_config_field_and_denied_observed_have_no_payload_hash(self):
        import json
        invalid=config();invalid['accountToken']='sensitive-test-value'
        denied=config();denied['classification']='observed-attested'
        denied['instrument']['source']='fmp-eod'
        denied['retention']={'policy':R.FMP_POLICY,'subscriptionStatus':'expired','terminationDate':None}
        for data, code in [(invalid,'invalid-config-fields'),(denied,'observed-gate-rejected')]:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as root:
                with self.assertRaisesRegex(P.HypothesisError,code):
                    P.run(data,evidence_root=Path(root),allow_synthetic_smoke=True)
                raw=next(Path(root).glob('*/failed-run.json')).read_text()
                artifact=json.loads(raw)
                self.assertIsNone(artifact['attemptScope']['datasetInputSha256'])
                self.assertNotIn('sensitive-test-value',raw)
                self.assertNotIn(P._digest(data),raw)
                self.assertNotIn(P._digest(data['datasets']),raw)

    def test_invalid_instrument_failure_artifact_has_no_sensitive_payload(self):
        data=config();data['instrument']['accountToken']='sensitive-test-value'
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(P.HypothesisError,'invalid-instrument-contract'):
                P.run(data,evidence_root=Path(root),allow_synthetic_smoke=True)
            artifact=next(Path(root).glob('*/failed-run.json')).read_text()
            self.assertNotIn('accountToken',artifact)
            self.assertNotIn('sensitive-test-value',artifact)
            self.assertNotIn(P._digest(data),artifact)

    def test_extreme_numeric_candle_and_cost_are_controlled_rejections(self):
        data=config();data['datasets']['1d'][0]['close']=10**1000
        with self.assertRaisesRegex(P.HypothesisError,'invalid-ohlcv'):
            P.validate_dataset(data)
        with self.assertRaisesRegex(P.HypothesisError,'invalid-cost'):
            P.frozen_hypotheses(10**1000)

    def test_observed_adjustment_attestation_must_match_instrument(self):
        data=config();data['classification']='observed-attested'
        data['instrument']['source']='fmp-eod'
        data['retention']={'policy':R.FMP_POLICY,'subscriptionStatus':'active','terminationDate':None}
        data['interpretation']={'instrumentVerified':True,'trainingPermitted':True,'currency':'USD',
            'session':'US-equities-regular','timestampMeaning':'source-close','priceType':'ohlcv',
            'adjustments':'split-adjusted','costs':'modeled','evidence':'synthetic-test-attestation'}
        with self.assertRaisesRegex(P.HypothesisError,'observed-instrument-or-rights-unapproved'):
            P.run(data)

    def test_observed_shared_gate_failure_persists_and_weekend_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            denied = config(); denied["classification"] = "observed-attested"
            denied["instrument"] = {**denied["instrument"], "source": "fmp-eod"}
            for series in denied["datasets"].values():
                for candle in series:
                    candle["provenance"]["source"] = "fmp-eod"
            denied["retention"] = {"policy": R.FMP_POLICY, "subscriptionStatus": "expired", "terminationDate": None}
            with self.assertRaisesRegex(P.HypothesisError, "observed-gate-rejected"):
                P.run(denied, evidence_root=Path(root))
            failed = list(Path(root).glob("*/failed-run.json"))
            self.assertEqual(len(failed), 1)
        with tempfile.TemporaryDirectory() as root:
            etoro = config(); etoro["classification"] = "observed-attested"
            etoro["instrument"] = {**etoro["instrument"], "source": "etoro"}
            for series in etoro["datasets"].values():
                for candle in series:
                    candle["provenance"]["source"] = "etoro"
            etoro["retention"] = {"policy": "source-terms"}
            with self.assertRaisesRegex(P.HypothesisError, "observed-gate-rejected"):
                P.run(etoro, evidence_root=Path(root))
            self.assertEqual(len(list(Path(root).glob("*/failed-run.json"))), 1)
        weekend = config(); weekend["classification"] = "observed-attested"
        weekend["instrument"] = {**weekend["instrument"], "source": "fmp-eod"}
        weekend["retention"] = {"policy": R.FMP_POLICY, "subscriptionStatus": "active", "terminationDate": None}
        weekend["interpretation"] = {"instrumentVerified": True, "trainingPermitted": True, "currency": "USD", "session": "US-equities-regular", "timestampMeaning": "source-close", "priceType": "ohlcv", "adjustments": "unadjusted", "costs": "modeled", "evidence": "source calendar not supplied"}
        for series in weekend["datasets"].values():
            for candle in series:
                for key in ("start", "end", "availableAt"):
                    candle[key] = (datetime.fromisoformat(candle[key].replace("Z", "+00:00")) + timedelta(days=5)).isoformat().replace("+00:00", "Z")
                candle["provenance"]["retrievedAt"] = (datetime.fromisoformat(candle["provenance"]["retrievedAt"].replace("Z", "+00:00")) + timedelta(days=5)).isoformat().replace("+00:00", "Z")
                candle["provenance"]["source"] = "fmp-eod"
        with self.assertRaisesRegex(P.HypothesisError, "regular-session-weekend"):
            P.run(weekend)

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
