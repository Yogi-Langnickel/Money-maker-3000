"""Synthetic contracts only; these observations are never research evidence."""
import copy
import math
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from money_maker_3000 import learning, portability
from money_maker_3000.market_history import Bar


def bars(source="alpha", size=700):
    result = []
    day = date(2018, 1, 1)
    for i in range(size):
        close = 100 * math.exp(.30 * math.sin(i / 8) + .003 * math.sin(i / 2))
        result.append(Bar("SPY", (day + timedelta(days=i)).isoformat(), close, close, close, close, 100, source))
    return result


def descriptor(source="alpha", series=None, retrieved_at="2026-09-14T23:59:00Z"):
    values = {"instrument": "SPY-US-ETF", "currency": "USD", "session": "regular",
              "timestamps": "session-date", "priceType": "last-trade", "adjustments": "unadjusted",
              "costs": "no-transaction-cost-model"}
    series = bars(source) if series is None else series
    return {"version": "feed-description.v2", "source": source, "symbol": "SPY",
            "fields": {key: {"value": value, "status": "verified", "evidence": "synthetic-contract-only"}
                       for key, value in values.items()}, "retention": {"policy": "source-terms"},
            "retrieval": {"retrievedAt": retrieved_at, "inputSeriesSha256": portability._series_digest(series),
                          "evidence": "synthetic retrieval journal contract only"},
            "findings": [{"classification": "unresolved", "explanation": "small differences unresolved",
                          "evidence": "synthetic-contract-only"}]}


class PortabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name).resolve() / "protocol.json"
        self.left = bars()
        self.right = bars("beta")
        portability.freeze_protocol(self.path, train_end=self.left[250].date, selection_end=self.left[350].date,
            evaluation_start=self.left[351].date, evaluation_end=self.left[-1].date,
            created_at="2026-09-14T00:00:00Z")

    def evaluate(self, a=None, b=None, da=None, db=None):
        left, right = self.left if a is None else a, self.right if b is None else b
        return portability.evaluate_pair(left, right,
            descriptor("alpha", left) if da is None else da, descriptor("beta", right) if db is None else db,
            self.path, as_of="2026-09-14")

    def test_identical_inputs_are_endpoint_complete_but_interval_unproven(self):
        report = self.evaluate()
        self.assertEqual(report["dailyMovements"]["directionAgreement"], 1)
        self.assertEqual(report["reserveEndpointCoverage"]["status"], "complete")
        self.assertEqual(report["reserveIntervalCompleteness"]["status"], "unproven")
        for strategy in report["strategies"]:
            self.assertEqual(strategy["verdict"], "inconclusive")
            self.assertIn("reserved-comparison-interval-completeness-unproven", strategy["reasons"])
            for direction in strategy["directions"]:
                self.assertEqual(direction["sourceFitSha256"], direction["transferFitSha256"])
                self.assertEqual(direction["transfer"]["stateAgreement"], 1)
                self.assertEqual(direction["transfer"]["probabilityMeanAbsoluteDifference"], 0)
                self.assertEqual(direction["independentNativeFitSameParameters"]["probabilityMeanAbsoluteDifference"], 0)
                self.assertEqual(direction["nativeTrainingCohorts"]["source"], direction["nativeTrainingCohorts"]["target"])

    def test_truncated_matching_feeds_do_not_complete_reserved_period(self):
        complete = self.evaluate()
        self.assertEqual(complete["reserveEndpointCoverage"]["status"], "complete")
        truncated = self.evaluate(a=self.left[:-100], b=self.right[:-100])
        self.assertEqual(truncated["reserveEndpointCoverage"]["status"], "incomplete")
        self.assertTrue(all(s["verdict"] == "inconclusive" and "reserved-comparison-endpoint-incomplete" in s["reasons"]
                            for s in truncated["strategies"]))

    def test_one_sided_and_shared_interior_gaps_never_claim_complete_interval(self):
        one_sided = self.evaluate(b=self.right[:500] + self.right[501:])
        self.assertEqual(one_sided["reserveEndpointCoverage"]["status"], "complete")
        self.assertEqual(one_sided["missingFromRight"], [self.right[500].date])
        self.assertEqual(one_sided["reserveIntervalCompleteness"]["status"], "unproven")
        self.assertTrue(all(item["verdict"] == "inconclusive" for item in one_sided["strategies"]))
        shared = self.evaluate(a=self.left[:500] + self.left[501:], b=self.right[:500] + self.right[501:])
        self.assertEqual(shared["reserveEndpointCoverage"]["status"], "complete")
        self.assertEqual(shared["missingFromLeft"], [])
        self.assertEqual(shared["missingFromRight"], [])
        self.assertEqual(shared["reserveIntervalCompleteness"]["status"], "unproven")
        self.assertTrue(all(item["verdict"] == "inconclusive" for item in shared["strategies"]))

    def test_as_of_requires_non_future_retrieval_bound_to_the_input_digest(self):
        left = descriptor("alpha", self.left, retrieved_at="2026-09-20T00:00:00Z")
        right = descriptor("beta", self.right, retrieved_at="2026-09-20T00:00:00Z")
        with self.assertRaisesRegex(learning.LearningError, "future-as-of"):
            portability.evaluate_pair(self.left, self.right, left, right, self.path, as_of="2026-09-20",
                                      clock=lambda: portability._timestamp("2026-09-19T00:00:00Z"))
        left = descriptor("alpha", self.left, retrieved_at="2026-09-14T23:59:00Z")
        right = descriptor("beta", self.right, retrieved_at="2026-09-14T23:59:00Z")
        with self.assertRaisesRegex(learning.LearningError, "as-of-not-bound-to-retrieval"):
            portability.evaluate_pair(self.left, self.right, left, right, self.path, as_of="2026-09-15",
                                      clock=lambda: portability._timestamp("2026-09-20T00:00:00Z"))
        left["retrieval"]["inputSeriesSha256"] = "a" * 64
        with self.assertRaisesRegex(learning.LearningError, "invalid-retrieval-evidence"):
            portability.evaluate_pair(self.left, self.right, left, right, self.path, as_of="2026-09-14")

    def test_non_session_end_requires_frozen_evidence_and_elapsed_reserve(self):
        path = self.path.parent/"calendar-end.json"
        last = self.left[-1].date
        end = (date.fromisoformat(last)+timedelta(days=2)).isoformat()
        kwargs = dict(train_end=self.left[250].date, selection_end=self.left[350].date,
                      evaluation_start=self.left[351].date, evaluation_end=end, expected_last_observation=last,
                      created_at="2026-09-14T00:00:00Z")
        with self.assertRaisesRegex(learning.LearningError,"non-session-end-evidence-required"):
            portability.freeze_protocol(path,**kwargs)
        portability.freeze_protocol(path,**kwargs,end_session_evidence="synthetic-calendar-contract-only")
        self.path=path
        self.assertTrue(all(s["verdict"] == "inconclusive" for s in self.evaluate()["strategies"]))
        pending = portability.evaluate_pair(self.left,self.right,descriptor("alpha", self.left),descriptor("beta", self.right),path,as_of=last)
        self.assertFalse(pending["reserveEndpointCoverage"]["evaluationPeriodElapsed"])
        self.assertTrue(all(s["verdict"] == "inconclusive" for s in pending["strategies"]))

    def test_material_behaviour_divergence_rejected_when_meaning_and_samples_complete(self):
        changed = [Bar(b.symbol,b.date,b.open,b.high,b.low,b.close*(1.20 if i%2 else .80),b.volume,b.source)
                   if i>350 else b for i,b in enumerate(self.right)]
        report = self.evaluate(b=changed)
        self.assertTrue(all(s["verdict"] == "inconclusive" for s in report["strategies"]))
        self.assertTrue(any("returnDifferenceP95" in s["toleranceBreaches"] for s in report["strategies"]))

    def test_close_only_contract_preserves_absence(self):
        close_only = [Bar(b.symbol,b.date,None,None,None,b.close,None,b.source) for b in self.right]
        report = self.evaluate(b=close_only)
        self.assertTrue(all(s["verdict"] == "inconclusive" for s in report["strategies"]))
        self.assertTrue(all(b.open is None for b in close_only))

    def test_unknown_costs_are_unused_context_and_do_not_block(self):
        desc = descriptor("beta")
        desc["fields"]["costs"]["status"] = "unresolved"
        self.assertTrue(all(s["verdict"] == "inconclusive" for s in self.evaluate(db=desc)["strategies"]))

    def test_reserve_prices_cannot_change_source_selection_or_fits(self):
        before = self.evaluate()
        changed = [Bar(b.symbol,b.date,b.open,b.high,b.low,b.close*(1.10 if i%2 else .90),b.volume,b.source)
                   if i>350 else b for i,b in enumerate(self.right)]
        after = self.evaluate(b=changed)
        for a,b in zip(before["strategies"],after["strategies"]):
            for x,y in zip(a["directions"],b["directions"]):
                self.assertEqual(x["parameters"],y["parameters"])
                self.assertEqual(x["sourceFit"],y["sourceFit"])
                self.assertEqual(x["candidateAttempts"],y["candidateAttempts"])

    def test_unknown_meaning_inconclusive_versus_verified_wrong_currency_rejected(self):
        unknown = descriptor("beta")
        unknown["fields"]["priceType"]["status"] = "unresolved"
        self.assertTrue(all(s["verdict"] == "inconclusive" for s in self.evaluate(db=unknown)["strategies"]))
        wrong = descriptor("beta")
        wrong["fields"]["currency"]["value"] = "AUD"
        self.assertTrue(all(s["verdict"] == "rejected" for s in self.evaluate(db=wrong)["strategies"]))

    def test_missing_date_does_not_turn_five_observations_into_five_common_dates(self):
        report = self.evaluate(b=self.right[:420]+self.right[421:])
        self.assertEqual(report["fiveObservationMovements"]["excludedMismatchedStartDates"], 5)
        self.assertEqual(report["missingFromRight"], [self.left[420].date])
        self.assertTrue(any(s["directions"][0]["transfer"]["excludedOutcomeDateMismatch"] > 0 for s in report["strategies"]))

    def test_threshold_and_crossover_sensitivity(self):
        # Tiny close changes at the exact decline trigger create different states.
        left = [Bar("SPY", f"2020-01-{i+1:02}",100,100,100,100,1,"alpha") for i in range(10)]
        left[-1] = Bar("SPY", "2020-01-10",98,98,98,98,1,"alpha")
        right = left[:-1]+[Bar("SPY","2020-01-10",98.001,98.001,98.001,98.001,1,"alpha")]
        params = {"lookbackDays":10,"dropTriggerPct":2.0}
        self.assertNotEqual(learning._state(left,9,"volatility-band-accumulator",params),
                            learning._state(right,9,"volatility-band-accumulator",params))
        left = [Bar("SPY",(date(2020,1,1)+timedelta(days=i)).isoformat(),100,100,100,100,1,"alpha") for i in range(60)]
        right = left[:-1]+[Bar("SPY",left[-1].date,100.001,100.001,100.001,100.001,1,"alpha")]
        params = {"shortLookbackDays":10,"longLookbackDays":60,"confirmationBars":1}
        self.assertNotEqual(learning._state(left,59,"slow-trend-allocation",params),
                            learning._state(right,59,"slow-trend-allocation",params))

    def test_fmp_every_derivative_requires_active_attestation(self):
        desc = descriptor("fmp-test")
        fmp = bars("fmp-test")
        with self.assertRaisesRegex(learning.LearningError,"retention-policy-required"):
            self.evaluate(a=fmp,da=desc)
        desc["retention"] = {"policy":"fmp-active-subscription-delete-within-30-days", "subscriptionStatus":"active",
                              "terminationDate":None}
        desc["retrieval"]["inputSeriesSha256"] = portability._series_digest(fmp)
        report = self.evaluate(a=fmp,da=desc)
        self.assertIn("hashes-models-and-mixed-reports",report["retentionScope"])
        desc["retention"]["subscriptionStatus"] = "terminated"
        with self.assertRaisesRegex(learning.LearningError,"inactive-delete"):
            self.evaluate(a=fmp,da=desc)

    def test_immutable_protocol_and_future_reservation(self):
        with self.assertRaisesRegex(learning.LearningError,"already-exists"):
            portability.write_report({"replace":True},self.path)
        with self.assertRaisesRegex(learning.LearningError,"reserve-already-observed"):
            portability.freeze_protocol(Path(self.temp.name).resolve()/"future.json", train_end="2020-01-01",selection_end="2021-01-01",
                evaluation_start="2022-01-01",evaluation_end="2023-01-01",created_at="2026-09-14T00:00:00Z",
                evidence_kind="prospectively-reserved-historical-robustness")

    def test_empty_missing_source_stays_inconclusive(self):
        report = self.evaluate(b=[])
        self.assertTrue(all(s["verdict"] == "inconclusive" for s in report["strategies"]))
        self.assertEqual(report["caseCoverage"]["earlyClose"], "unavailable")

    def test_duplicate_and_source_mismatch_fail_closed(self):
        with self.assertRaisesRegex(learning.LearningError,"duplicate-unordered"):
            self.evaluate(b=self.right[:50]+self.right[49:])
        with self.assertRaisesRegex(learning.LearningError,"source-identity"):
            self.evaluate(db=descriptor("gamma"))

    def test_interrupted_atomic_write_does_not_publish_partial_report(self):
        target = Path(self.temp.name).resolve()/"interrupted.json"
        with patch("money_maker_3000.portability.os.link", side_effect=OSError("synthetic interruption")):
            with self.assertRaises(learning.LearningError):
                portability.write_report({"synthetic":True},target)
        self.assertFalse(target.exists())
        self.assertEqual(list(Path(self.temp.name).resolve().glob(".portability-pending-*")),[])
        portability.write_report({"synthetic":True},target)
        self.assertTrue(target.exists())

    def test_report_read_checks_current_retention_before_data_access(self):
        with patch("money_maker_3000.learning._read") as reader:
            with self.assertRaisesRegex(learning.LearningError,"inactive-delete"):
                portability.load_report("unused",protocol_path="unused-protocol",current_retentions={"fmp-eod-unadjusted":{
                    "policy":"fmp-active-subscription-delete-within-30-days", "subscriptionStatus":"terminated",
                    "terminationDate":"2026-09-13"}})
            reader.assert_not_called()

    def test_completed_report_is_sealed_and_rejects_tampered_verdicts(self):
        report = self.evaluate()
        target = self.path.parent / "report.json"
        portability.write_report(report, target)
        loaded = portability.load_report(target, protocol_path=self.path, current_retentions={"alpha": {"policy": "source-terms"},
                                                                      "beta": {"policy": "source-terms"}})
        self.assertIn("sha256", loaded)
        changed = __import__("json").loads(target.read_text())
        changed["strategies"][0]["verdict"] = "supported" if changed["strategies"][0]["verdict"] != "supported" else "rejected"
        target.write_text(__import__("json").dumps(changed))
        with self.assertRaisesRegex(learning.LearningError, "report-checksum-mismatch"):
            portability.load_report(target, protocol_path=self.path, current_retentions={"alpha": {"policy": "source-terms"},
                                                                 "beta": {"policy": "source-terms"}})
        malformed = __import__("json").loads(__import__("json").dumps(loaded))
        malformed["reserveEndpointCoverage"] = "fabricated"
        malformed["sha256"] = learning._digest({key: value for key, value in malformed.items() if key != "sha256"})
        target.write_text(__import__("json").dumps(malformed))
        with self.assertRaisesRegex(learning.LearningError, "portability-invalid-report"):
            portability.load_report(target, protocol_path=self.path, current_retentions={"alpha": {"policy": "source-terms"},
                                                                 "beta": {"policy": "source-terms"}})
        inconsistent_coverage = __import__("json").loads(__import__("json").dumps(loaded))
        inconsistent_coverage["reserveEndpointCoverage"]["evaluationPeriodElapsed"] = False
        inconsistent_coverage["reserveEndpointCoverage"]["status"] = "incomplete"
        inconsistent_coverage["sha256"] = learning._digest({key: value for key, value in inconsistent_coverage.items() if key != "sha256"})
        target.write_text(__import__("json").dumps(inconsistent_coverage))
        with self.assertRaisesRegex(learning.LearningError, "reserve-coverage-mismatch"):
            portability.load_report(target, protocol_path=self.path, current_retentions={"alpha": {"policy": "source-terms"},
                                                                 "beta": {"policy": "source-terms"}})
        insufficient = __import__("json").loads(__import__("json").dumps(loaded))
        insufficient["strategies"][0]["directions"] = [
            {"from": "alpha", "to": "beta", "status": "insufficient-source-development-history"},
            {"from": "beta", "to": "alpha", "status": "insufficient-source-development-history"}]
        insufficient["sha256"] = learning._digest({key: value for key, value in insufficient.items() if key != "sha256"})
        target.write_text(__import__("json").dumps(insufficient))
        with self.assertRaisesRegex(learning.LearningError, "verdict-contract-mismatch"):
            portability.load_report(target, protocol_path=self.path, current_retentions={"alpha": {"policy": "source-terms"},
                                                                 "beta": {"policy": "source-terms"}})
        blank_directions = __import__("json").loads(__import__("json").dumps(loaded))
        blank_directions["strategies"][0]["directions"] = [{}, {}]
        blank_directions["sha256"] = learning._digest({key: value for key, value in blank_directions.items() if key != "sha256"})
        target.write_text(__import__("json").dumps(blank_directions))
        with self.assertRaisesRegex(learning.LearningError, "portability-invalid-report"):
            portability.load_report(target, protocol_path=self.path, current_retentions={"alpha": {"policy": "source-terms"},
                                                                 "beta": {"policy": "source-terms"}})

    def test_active_state_at_reserve_start_is_not_a_new_trigger(self):
        protocol = portability.load_protocol(self.path)
        series = [Bar(b.symbol,b.date,100+i,100+i,100+i,100+i,1,b.source) for i,b in enumerate(self.left)]
        params = {"shortLookbackDays":10,"longLookbackDays":60,"confirmationBars":1}
        fit = learning._fit(["trend-confirmed"]*20,[1]*20,"slow-trend-allocation")
        rows = portability._observations(series,"slow-trend-allocation",params,fit,protocol)
        result = portability._behaviour(rows,rows,"slow-trend-allocation",set())
        self.assertEqual(result["triggerUnionCount"],0)

    def test_protocol_tampering_rejected(self):
        value = portability.load_protocol(self.path)
        value["tolerances"]["slow-trend-allocation"]["stateAgreement"] = .01
        self.path.write_text(__import__("json").dumps(value))
        with self.assertRaisesRegex(learning.LearningError,"checksum-mismatch"):
            self.evaluate()
