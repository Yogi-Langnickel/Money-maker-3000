import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from money_maker_3000.backtest import build_synthetic_backtest

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "market_history" / "spy-daily.csv"
GLD_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "market_history" / "gld-daily.csv"
GLD_COMMODITY_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "market_history" / "gld-commodity-synthetic-20-daily.csv"
QQQ_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "market_history" / "qqq-daily.csv"
SLOW_TREND_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "market_history" / "spy-slow-trend-202-daily.csv"
VOLATILITY_STABLE_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "market_history" / "spy-volatility-stable-20-daily.csv"
VOLATILITY_DECLINE_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "market_history" / "spy-volatility-decline-20-daily.csv"
VOLATILITY_RECOVERY_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "market_history" / "spy-volatility-recovery-20-daily.csv"
AU_ETF_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "market_history" / "vas-au-etf-synthetic-20-daily.csv"


class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "money_maker_3000.cli", *args],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_backtest_cli_runs_historical_fixture_without_provider_calls(self):
        result = self.run_cli(
            "backtest",
            "--history-csv",
            str(FIXTURE_PATH),
            "--symbol",
            "SPY",
            "--market",
            "US_EQUITIES",
            "--instrument-class",
            "ETF",
            "--started-at",
            "2026-05-15T00:00:00Z",
            "--provider-demo-balance-usd",
            "1000000",
            "--bot-allocation-usd",
            "1000",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["mode"], "historical-fixture-backtest")
        self.assertEqual(payload["providerCalls"], "blocked")
        self.assertEqual(payload["metadata"]["rowCount"], 3)
        self.assertEqual(payload["metadata"]["maxFixtureRows"], 10000)
        self.assertEqual(payload["periodDiagnostics"]["dtoVersion"], "market-period-diagnostics.v1")
        self.assertEqual(payload["periodDiagnostics"]["periods"][0]["period"], "24h")
        self.assertEqual(payload["samplingQuality"]["dtoVersion"], "market-history-sampling-quality.v1")
        self.assertEqual(payload["samplingQuality"]["state"], "weekday-grid-covered")
        self.assertEqual(payload["samplingQuality"]["observationCount"], 3)
        self.assertEqual(payload["samplingQuality"]["intervalCalendarDays"], [1, 1])
        self.assertEqual(payload["samplingQuality"]["providerCalls"], "blocked")
        self.assertEqual(payload["samplingQuality"]["accountData"], "absent")
        self.assertEqual(payload["samplingQuality"]["execution"], "blocked")
        self.assertEqual(payload["scenarioSummaries"][0]["allocation"]["providerDemoBalance"], "redacted")

    def test_readiness_cli_warns_on_potential_weekday_gap_without_blocking(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture_path = Path(temp_dir) / "gap.csv"
            fixture_path.write_text(
                "symbol,date,open,high,low,close,volume,source\n"
                "SPY,2026-05-08,100,100,100,100,1000,synthetic-test-fixture\n"
                "SPY,2026-05-12,100,100,100,100,1000,synthetic-test-fixture\n",
                encoding="utf-8",
            )
            result = self.run_cli(
                "readiness",
                "--fixture",
                f"SPY={fixture_path}",
                "--started-at",
                "2026-05-13T00:00:00Z",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        fixture = payload["fixtureDiagnostics"][0]
        sampling_quality = fixture["samplingQuality"]
        offline_gate = next(gate for gate in payload["gates"] if gate["name"] == "offline-fixtures")

        self.assertTrue(payload["ready"])
        self.assertTrue(fixture["ok"])
        self.assertEqual(sampling_quality["state"], "potential-weekday-gaps")
        self.assertEqual(sampling_quality["potentialMissingWeekdayCount"], 1)
        self.assertNotIn("firstDate", sampling_quality)
        self.assertNotIn("lastDate", sampling_quality)
        self.assertNotIn("intervalCalendarDays", sampling_quality)
        self.assertNotIn("price", json.dumps(sampling_quality).lower())
        self.assertEqual(len(fixture["warnings"]), 1)
        self.assertIn("exchange-calendar review", fixture["warnings"][0])
        self.assertIn("not proof of missing market sessions", fixture["warnings"][0])
        self.assertEqual(offline_gate["errors"], [])
        self.assertEqual(offline_gate["warnings"], [f"SPY: {fixture['warnings'][0]}"])

    def test_readiness_cli_single_observation_warning_uses_weekend_counter(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            weekday_path = temp_path / "weekday.csv"
            weekend_path = temp_path / "weekend.csv"
            header = "symbol,date,open,high,low,close,volume,source\n"
            weekday_path.write_text(
                header + "SPY,2026-05-11,100,100,100,100,1000,synthetic-test-fixture\n",
                encoding="utf-8",
            )
            weekend_path.write_text(
                header + "SPY,2026-05-09,100,100,100,100,1000,synthetic-test-fixture\n",
                encoding="utf-8",
            )
            weekday_result = self.run_cli(
                "readiness",
                "--fixture",
                f"SPY={weekday_path}",
                "--started-at",
                "2026-05-13T00:00:00Z",
            )
            weekend_result = self.run_cli(
                "readiness",
                "--fixture",
                f"SPY={weekend_path}",
                "--started-at",
                "2026-05-13T00:00:00Z",
            )
            duplicate_result = self.run_cli(
                "readiness",
                "--fixture",
                f"SPY={weekend_path}",
                "--fixture",
                f"SPY={weekend_path}",
                "--started-at",
                "2026-05-13T00:00:00Z",
            )

        self.assertEqual(weekday_result.returncode, 0, weekday_result.stderr)
        weekday = json.loads(weekday_result.stdout)
        weekday_fixture = weekday["fixtureDiagnostics"][0]
        weekday_gate = next(gate for gate in weekday["gates"] if gate["name"] == "offline-fixtures")
        self.assertTrue(weekday["ready"])
        self.assertEqual(weekday_fixture["samplingQuality"]["state"], "insufficient-history")
        self.assertEqual(weekday_fixture["samplingQuality"]["observedWeekendCount"], 0)
        self.assertEqual(weekday_fixture["warnings"], [])
        self.assertEqual(weekday_gate["warnings"], [])

        self.assertEqual(weekend_result.returncode, 0, weekend_result.stderr)
        weekend = json.loads(weekend_result.stdout)
        weekend_fixture = weekend["fixtureDiagnostics"][0]
        weekend_gate = next(gate for gate in weekend["gates"] if gate["name"] == "offline-fixtures")
        self.assertTrue(weekend["ready"])
        self.assertEqual(weekend_fixture["samplingQuality"]["state"], "insufficient-history")
        self.assertEqual(weekend_fixture["samplingQuality"]["observedWeekendCount"], 1)
        self.assertEqual(len(weekend_fixture["warnings"]), 1)
        self.assertEqual(weekend_gate["warnings"], [f"SPY: {weekend_fixture['warnings'][0]}"])

        self.assertEqual(duplicate_result.returncode, 1, duplicate_result.stderr)
        duplicate = json.loads(duplicate_result.stdout)
        duplicate_gate = next(gate for gate in duplicate["gates"] if gate["name"] == "offline-fixtures")
        self.assertFalse(duplicate["ready"])
        self.assertEqual(duplicate_gate["errors"], ["duplicate fixture symbols: SPY"])
        self.assertEqual(len(duplicate_gate["warnings"]), 2)
        self.assertTrue(
            all("exchange-calendar review" in warning for warning in duplicate_gate["warnings"])
        )

    def test_backtest_cli_proves_default_slow_trend_window(self):
        result = self.run_cli(
            "backtest",
            "--history-csv",
            str(SLOW_TREND_FIXTURE_PATH),
            "--symbol",
            "SPY",
            "--strategy",
            "slow-trend-allocation",
            "--market",
            "US_EQUITIES",
            "--instrument-class",
            "ETF",
            "--started-at",
            "2025-10-13T00:00:00Z",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        diagnostics = payload["strategyHistoryDiagnostics"]
        serialized = json.dumps(payload).lower()
        self.assertEqual(payload["metadata"]["rowCount"], 202)
        self.assertEqual(payload["metadata"]["firstDate"], "2025-01-02")
        self.assertEqual(payload["metadata"]["lastDate"], "2025-10-10")
        self.assertEqual(
            payload["metadata"]["inputSha256"],
            "ecaec707c5bc6dccc05f0ed5b52f1110ba08a0e0431c59e0d9223baf9ae546d9",
        )
        self.assertEqual(diagnostics["state"], "trend-confirmed")
        self.assertEqual(diagnostics["parameterState"], "valid")
        self.assertEqual(diagnostics["requiredBarCount"], 202)
        self.assertEqual(diagnostics["metrics"]["shortLookbackBars"], 50)
        self.assertEqual(diagnostics["metrics"]["longLookbackBars"], 200)
        self.assertEqual(diagnostics["metrics"]["confirmationMatches"], 3)
        self.assertEqual(diagnostics["candidateIntent"], "skip")
        self.assertEqual(diagnostics["providerCalls"], "blocked")
        self.assertEqual(diagnostics["executionRoutes"], "absent")
        self.assertEqual(diagnostics["walkForward"]["dtoVersion"], "strategy-history-walk-forward.v2")
        self.assertEqual(diagnostics["walkForward"]["eligibleObservationCount"], 1)
        self.assertEqual(diagnostics["walkForward"]["stateCounts"], {"trend-confirmed": 1})
        self.assertEqual(diagnostics["walkForward"]["transitionCount"], 0)
        self.assertEqual(diagnostics["walkForward"]["foldCount"], 1)
        self.assertEqual(diagnostics["walkForward"]["folds"][0]["observationCount"], 1)
        for forbidden in ("accountid", "positionid", "orderid", "apikey", "userkey", "winrate", "sharpe"):
            self.assertNotIn(forbidden, serialized)

    def test_readiness_cli_redacts_available_multi_fold_walk_forward_summary(self):
        result = self.run_cli(
            "readiness",
            "--fixture",
            f"SPY={SLOW_TREND_FIXTURE_PATH}",
            "--strategy",
            "slow-trend-allocation",
            "--strategy-params-json",
            '{"shortLookbackDays":10,"longLookbackDays":190,"confirmationBars":2}',
            "--market",
            "US_EQUITIES",
            "--instrument-class",
            "ETF",
            "--started-at",
            "2025-10-13T00:00:00Z",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        walk_forward = payload["fixtureDiagnostics"][0]["strategyHistoryDiagnostics"]["walkForward"]
        self.assertEqual(walk_forward["dtoVersion"], "strategy-history-walk-forward.v2")
        self.assertEqual(walk_forward["state"], "available")
        self.assertEqual(walk_forward["eligibleObservationCount"], 12)
        self.assertEqual(walk_forward["foldCount"], 5)
        self.assertEqual([fold["observationCount"] for fold in walk_forward["folds"]], [3, 3, 2, 2, 2])
        self.assertTrue(
            all(
                set(fold) == {"foldIndex", "observationCount", "stateCounts", "transitionCount"}
                for fold in walk_forward["folds"]
            )
        )
        self.assertNotIn("firstObservationDate", walk_forward)
        self.assertNotIn("lastObservationDate", walk_forward)
        self.assertNotIn("metrics", json.dumps(walk_forward))
        self.assertEqual(walk_forward["providerCalls"], "blocked")
        self.assertEqual(walk_forward["accountData"], "absent")
        self.assertEqual(walk_forward["executionRoutes"], "absent")
        self.assertEqual(walk_forward["candidateIntent"], "skip")

    def test_backtest_cli_proves_checksum_pinned_volatility_scenarios(self):
        cases = (
            (
                VOLATILITY_STABLE_FIXTURE_PATH,
                "no-trigger-observed",
                "7e7aa8344d04b62a09d0f1dfb87ba218bad982ec8476e5bcd40666c0859fa56c",
                -0.496032,
                -0.695134,
            ),
            (
                VOLATILITY_DECLINE_FIXTURE_PATH,
                "trigger-observed",
                "5621200eb3e8b3d0b87ea7479812c38ad1fa46aeca308ea654043bf0f76c8d5c",
                -7.428571,
                -7.428571,
            ),
            (
                VOLATILITY_RECOVERY_FIXTURE_PATH,
                "recovery-observed",
                "8566f55c59ffd4f0f29c0b228422079d70dc90707345bbf922a87772ffdb076d",
                -0.952381,
                -10.47619,
            ),
        )

        for (
            fixture_path,
            expected_state,
            expected_sha256,
            expected_decline,
            expected_maximum_decline,
        ) in cases:
            with self.subTest(fixture=fixture_path.name):
                result = self.run_cli(
                    "backtest",
                    "--history-csv",
                    str(fixture_path),
                    "--symbol",
                    "SPY",
                    "--strategy",
                    "volatility-band-accumulator",
                    "--market",
                    "US_EQUITIES",
                    "--instrument-class",
                    "ETF",
                    "--started-at",
                    "2025-01-30T00:00:00Z",
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                diagnostics = payload["strategyHistoryDiagnostics"]
                serialized = json.dumps(payload).lower()
                self.assertEqual(payload["metadata"]["rowCount"], 20)
                self.assertEqual(payload["metadata"]["dataSource"], "synthetic-volatility-fixture")
                self.assertEqual(payload["metadata"]["inputSha256"], expected_sha256)
                self.assertEqual(diagnostics["state"], expected_state)
                self.assertEqual(diagnostics["requiredBarCount"], 20)
                self.assertEqual(diagnostics["metrics"]["declineFromRollingPeakPct"], expected_decline)
                self.assertEqual(diagnostics["metrics"]["maximumObservedDeclinePct"], expected_maximum_decline)
                self.assertEqual(diagnostics["metrics"]["dropTriggerPct"], 3.0)
                self.assertEqual(diagnostics["candidateIntent"], "skip")
                self.assertEqual(diagnostics["providerCalls"], "blocked")
                self.assertEqual(diagnostics["executionRoutes"], "absent")
                self.assertEqual(diagnostics["accountData"], "absent")
                self.assertEqual(payload["providerCalls"], "blocked")
                self.assertEqual(payload["executionRoutes"], "absent")
                for forbidden in ("accountid", "positionid", "orderid", "apikey", "userkey", "winrate", "sharpe"):
                    self.assertNotIn(forbidden, serialized)

    def test_backtest_cli_accepts_checksum_pinned_synthetic_au_etf_fixture(self):
        result = self.run_cli(
            "backtest",
            "--history-csv",
            str(AU_ETF_FIXTURE_PATH),
            "--symbol",
            "VAS",
            "--strategy",
            "dca-cash-reserve",
            "--market",
            "AU_EQUITIES",
            "--instrument-class",
            "ETF",
            "--started-at",
            "2025-01-30T00:00:00Z",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        first_scenario = payload["scenarioSummaries"][0]
        self.assertEqual(payload["metadata"]["rowCount"], 20)
        self.assertEqual(payload["metadata"]["dataSource"], "synthetic-au-etf-fixture")
        self.assertEqual(
            payload["metadata"]["inputSha256"],
            "6e7c464580d94a33f2f14613e7301af99af7a513e82ee8a71d92c782caf82c4b",
        )
        self.assertEqual(
            first_scenario["selectedInstrument"],
            {"symbol": "VAS", "market": "AU_EQUITIES", "instrumentClass": "ETF"},
        )
        self.assertEqual(first_scenario["decision"], "skip")
        self.assertEqual(first_scenario["providerCalls"], "blocked")
        self.assertEqual(first_scenario["executionRoute"], "absent")
        self.assertEqual(payload["accountData"], "absent")

    def test_backtest_cli_accepts_allowlisted_strategy_parameter_json(self):
        result = self.run_cli(
            "backtest",
            "--history-csv",
            str(FIXTURE_PATH),
            "--symbol",
            "SPY",
            "--market",
            "US_EQUITIES",
            "--instrument-class",
            "ETF",
            "--strategy-params-json",
            json.dumps({"fixedOrderUsd": 125.0, "maxOrdersPerWeek": 2}),
            "--started-at",
            "2026-05-15T00:00:00Z",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        diagnostics = payload["runs"][0]["intentDiagnostics"]
        self.assertEqual(diagnostics["candidateOrderUsd"], 125.0)
        self.assertEqual(diagnostics["strategyParameters"]["fixedOrderUsd"], 125.0)
        self.assertEqual(diagnostics["strategyParameters"]["maxOrdersPerWeek"], 2)
        self.assertEqual(diagnostics["providerCalls"], "blocked")
        self.assertEqual(diagnostics["executionRoute"], "absent")

    def test_backtest_cli_rejects_non_finite_strategy_parameter_json(self):
        result = self.run_cli(
            "backtest",
            "--history-csv",
            str(FIXTURE_PATH),
            "--symbol",
            "SPY",
            "--market",
            "US_EQUITIES",
            "--instrument-class",
            "ETF",
            "--strategy-params-json",
            '{"fixedOrderUsd":NaN}',
            "--started-at",
            "2026-05-15T00:00:00Z",
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("fixedOrderUsd must be a finite number", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_backtest_cli_synthetic_run_honors_selected_strategy_inputs(self):
        result = self.run_cli(
            "backtest",
            "--strategy",
            "slow-trend-allocation",
            "--symbol",
            "QQQ",
            "--market",
            "US_EQUITIES",
            "--instrument-class",
            "ETF",
            "--budget-usd",
            "750",
            "--bot-allocation-usd",
            "900",
            "--reserved-usd",
            "100",
            "--max-order-usd",
            "125",
            "--strategy-params-json",
            json.dumps(
                {
                    "shortLookbackDays": 20,
                    "longLookbackDays": 120,
                    "confirmationBars": 2,
                    "orderFractionPct": 0.1,
                    "maxOrderUsd": 125,
                }
            ),
            "--started-at",
            "2026-05-15T00:00:00Z",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        scenario = payload["scenarioSummaries"][0]
        diagnostics = scenario["intentDiagnostics"]
        self.assertEqual(payload["summary"]["eventCount"], 1)
        self.assertEqual(scenario["strategyId"], "slow-trend-allocation")
        self.assertEqual(scenario["selectedInstrument"]["symbol"], "QQQ")
        self.assertEqual(scenario["requestedBudgetUsd"], 750.0)
        self.assertEqual(scenario["allocation"]["botAllocationUsd"], 900.0)
        self.assertEqual(diagnostics["strategyParameters"]["maxOrderUsd"], 125)
        self.assertEqual(diagnostics["candidateOrderUsd"], 75.0)

    def test_fixture_batch_cli_runs_manifest_without_provider_calls(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "fixtures.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "fixtures": [
                            {"symbol": "SPY", "path": str(FIXTURE_PATH)},
                            {"symbol": "GLD", "path": str(GLD_FIXTURE_PATH)},
                            {"symbol": "QQQ", "path": str(QQQ_FIXTURE_PATH)},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            result = self.run_cli(
                "fixture-batch",
                "--manifest",
                str(manifest_path),
                "--started-at",
                "2026-05-15T00:00:00Z",
                "--provider-demo-balance-usd",
                "1000000",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        serialized = json.dumps(payload)
        self.assertEqual(payload["mode"], "offline-fixture-batch-diagnostics")
        self.assertEqual(payload["providerCalls"], "blocked")
        self.assertEqual(payload["executionRoutes"], "absent")
        self.assertEqual(payload["coverage"]["fixtureCount"], 3)
        self.assertEqual(payload["coverage"]["totalRows"], 9)
        self.assertEqual(payload["metadata"]["symbols"], ["GLD", "QQQ", "SPY"])
        self.assertEqual(payload["perSymbolDiagnostics"][0]["periodDiagnostics"]["providerCalls"], "blocked")
        self.assertEqual(
            payload["perSymbolDiagnostics"][0]["strategyHistoryDiagnostics"]["providerCalls"],
            "blocked",
        )
        self.assertEqual(payload["summary"]["strategyHistoryStateHistogram"], {"not-applicable": 3})
        self.assertEqual(payload["perSymbolDiagnostics"][1]["coverage"]["rowCount"], 3)
        self.assertEqual(payload["perSymbolDiagnostics"][2]["symbol"], "QQQ")
        self.assertNotIn("1000000", serialized)

    def test_fixture_batch_cli_accepts_repeated_fixture_entries(self):
        result = self.run_cli(
            "fixture-batch",
            "--fixture",
            f"SPY={FIXTURE_PATH}",
            "--fixture",
            f"GLD={GLD_FIXTURE_PATH}",
            "--fixture",
            f"QQQ={QQQ_FIXTURE_PATH}",
            "--started-at",
            "2026-05-15T00:00:00Z",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["coverage"]["fixtureCount"], 3)
        self.assertEqual([item["symbol"] for item in payload["perSymbolDiagnostics"]], ["SPY", "GLD", "QQQ"])

    def test_fixture_batch_cli_preserves_strategy_history_states(self):
        result = self.run_cli(
            "fixture-batch",
            "--fixture",
            f"SPY={FIXTURE_PATH}",
            "--strategy",
            "volatility-band-accumulator",
            "--strategy-params-json",
            (
                '{"lookbackDays":5,"dropTriggerPct":3,"maxOrderUsd":150,'
                '"maxOrdersPerWeek":1,"cooldownDays":3,"cashReserveFloorUsd":150}'
            ),
            "--started-at",
            "2026-05-15T00:00:00Z",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        diagnostics = payload["perSymbolDiagnostics"][0]["strategyHistoryDiagnostics"]
        self.assertEqual(diagnostics["strategyId"], "volatility-band-accumulator")
        self.assertEqual(diagnostics["state"], "insufficient-history")
        self.assertEqual(diagnostics["candidateIntent"], "skip")
        self.assertEqual(payload["summary"]["strategyHistoryStateHistogram"], {"insufficient-history": 1})

    def test_fixture_batch_cli_reports_rebalance_history_without_intent(self):
        result = self.run_cli(
            "fixture-batch",
            "--fixture",
            f"SPY={FIXTURE_PATH}",
            "--fixture",
            f"GLD={GLD_FIXTURE_PATH}",
            "--strategy",
            "threshold-rebalance",
            "--strategy-params-json",
            (
                '{"targetWeights":{"SPY":0.7,"GLD":0.3},"rebalanceThresholdPct":5,'
                '"maxOrderUsd":250,"minCashReserveUsd":100,"maxOpenPositions":3}'
            ),
            "--started-at",
            "2026-05-15T00:00:00Z",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        diagnostics = payload["rebalanceHistoryDiagnostics"]
        self.assertEqual(diagnostics["state"], "available")
        self.assertEqual(diagnostics["candidateIntent"], "skip")
        self.assertEqual(diagnostics["providerCalls"], "blocked")
        self.assertEqual(diagnostics["portfolioHoldings"], "absent")
        self.assertEqual(diagnostics["executionRoutes"], "absent")
        self.assertIn(
            diagnostics["metrics"]["thresholdState"],
            {"historical-drift-exceeded", "within-historical-threshold"},
        )

    def test_fixture_batch_cli_proves_mixed_universe_rebalance_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "mixed-universe-fixtures.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "fixtures": [
                            {
                                "symbol": "SPY",
                                "path": str(VOLATILITY_STABLE_FIXTURE_PATH),
                                "market": "US_EQUITIES",
                                "instrumentClass": "ETF",
                            },
                            {
                                "symbol": "VAS",
                                "path": str(AU_ETF_FIXTURE_PATH),
                                "market": "AU_EQUITIES",
                                "instrumentClass": "ETF",
                            },
                            {
                                "symbol": "GLD",
                                "path": str(GLD_COMMODITY_FIXTURE_PATH),
                                "market": "COMMODITIES",
                                "instrumentClass": "ETF",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            result = self.run_cli(
                "fixture-batch",
                "--manifest",
                str(manifest_path),
                "--strategy",
                "threshold-rebalance",
                "--strategy-params-json",
                (
                    '{"targetWeights":{"SPY":0.4,"VAS":0.3,"GLD":0.3},'
                    '"rebalanceThresholdPct":1,"maxOrderUsd":250,'
                    '"minCashReserveUsd":100,"maxOpenPositions":3}'
                ),
                "--started-at",
                "2025-01-30T00:00:00Z",
            )
            readiness_result = self.run_cli(
                "readiness",
                "--manifest",
                str(manifest_path),
                "--strategy",
                "threshold-rebalance",
                "--strategy-params-json",
                (
                    '{"targetWeights":{"SPY":0.4,"VAS":0.3,"GLD":0.3},'
                    '"rebalanceThresholdPct":1,"maxOrderUsd":250,'
                    '"minCashReserveUsd":100,"maxOpenPositions":3}'
                ),
                "--started-at",
                "2025-01-30T00:00:00Z",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        diagnostics = payload["rebalanceHistoryDiagnostics"]
        serialized = json.dumps(payload).lower()
        self.assertEqual(payload["metadata"]["symbols"], ["GLD", "SPY", "VAS"])
        self.assertEqual(payload["coverage"]["fixtureCount"], 3)
        self.assertEqual(payload["coverage"]["totalRows"], 60)
        self.assertEqual(diagnostics["state"], "available")
        self.assertEqual(diagnostics["metrics"]["thresholdState"], "historical-drift-exceeded")
        self.assertEqual(diagnostics["metrics"]["maxAbsoluteDriftPercentagePoints"], 1.575085)
        self.assertEqual(
            [
                (weight["symbol"], weight["normalizedHistoricalWeight"], weight["driftPercentagePoints"])
                for weight in diagnostics["metrics"]["weights"]
            ],
            [
                ("GLD", 0.28424915, -1.575085),
                ("SPY", 0.40247312, 0.247312),
                ("VAS", 0.31327774, 1.327774),
            ],
        )
        self.assertTrue(
            all(
                weight["coverage"] == {"startDate": "2025-01-02", "endDate": "2025-01-29", "barCount": 20}
                for weight in diagnostics["metrics"]["weights"]
            )
        )
        self.assertEqual(diagnostics["candidateIntent"], "skip")
        self.assertEqual(diagnostics["providerCalls"], "blocked")
        self.assertEqual(diagnostics["portfolioHoldings"], "absent")
        self.assertEqual(diagnostics["accountData"], "absent")
        self.assertEqual(diagnostics["executionRoutes"], "absent")
        self.assertEqual(
            diagnostics["performanceClaims"],
            "historical-relative-weight-drift-only-no-pnl-or-profitability-claim",
        )
        for forbidden in ("accountid", "positionid", "orderid", "apikey", "userkey", "winrate", "sharpe"):
            self.assertNotIn(forbidden, serialized)

        self.assertEqual(readiness_result.returncode, 0, readiness_result.stderr)
        readiness = json.loads(readiness_result.stdout)
        fixture_boundaries = {
            fixture["symbol"]: (fixture["market"], fixture["instrumentClass"])
            for fixture in readiness["fixtureDiagnostics"]
        }
        self.assertTrue(readiness["ready"])
        self.assertEqual(readiness["readinessScope"], "offline-backtest-only")
        self.assertEqual(
            fixture_boundaries,
            {
                "SPY": ("US_EQUITIES", "ETF"),
                "VAS": ("AU_EQUITIES", "ETF"),
                "GLD": ("COMMODITIES", "ETF"),
            },
        )
        self.assertEqual(readiness["providerCalls"], "blocked")
        self.assertEqual(readiness["executionRoutes"], "absent")
        self.assertEqual(readiness["demoExecution"], "blocked")
        self.assertEqual(readiness["liveExecution"], "blocked")

    def test_readiness_cli_reports_backtest_ready_without_provider_calls(self):
        result = self.run_cli(
            "readiness",
            "--fixture",
            f"SPY={FIXTURE_PATH}",
            "--fixture",
            f"GLD={GLD_FIXTURE_PATH}",
            "--fixture",
            f"QQQ={QQQ_FIXTURE_PATH}",
            "--started-at",
            "2026-05-15T00:00:00Z",
            "--provider-demo-balance-usd",
            "1000000",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        serialized = json.dumps(payload)
        self.assertEqual(payload["dtoVersion"], "backtest-readiness.v1")
        self.assertTrue(payload["ready"])
        self.assertEqual(payload["readinessScope"], "offline-backtest-only")
        self.assertEqual(payload["providerCalls"], "blocked")
        self.assertEqual(payload["executionRoutes"], "absent")
        self.assertEqual(payload["demoExecution"], "blocked")
        self.assertEqual(payload["liveExecution"], "blocked")
        self.assertEqual(payload["metadata"]["symbols"], ["GLD", "QQQ", "SPY"])
        self.assertEqual([fixture["symbol"] for fixture in payload["fixtureDiagnostics"]], ["SPY", "GLD", "QQQ"])
        self.assertTrue(
            all(
                fixture["strategyHistoryDiagnostics"]["candidateIntent"] == "skip"
                for fixture in payload["fixtureDiagnostics"]
            )
        )
        self.assertTrue(
            all(
                fixture["strategyHistoryDiagnostics"]["walkForward"]
                == {
                    "dtoVersion": "strategy-history-walk-forward.v2",
                    "state": "not-applicable",
                    "eligibleObservationCount": 0,
                    "transitionCount": 0,
                    "foldCount": 0,
                    "folds": [],
                    "providerCalls": "blocked",
                    "accountData": "absent",
                    "executionRoutes": "absent",
                    "candidateIntent": "skip",
                }
                for fixture in payload["fixtureDiagnostics"]
            )
        )
        self.assertTrue(all(gate["ok"] for gate in payload["gates"]))
        self.assertTrue(payload["nextSafeCommands"])
        self.assertNotIn("1000000", serialized)

    def test_readiness_cli_accepts_synthetic_au_etf_for_offline_diagnostics_only(self):
        result = self.run_cli(
            "readiness",
            "--fixture",
            f"VAS={AU_ETF_FIXTURE_PATH}",
            "--strategy",
            "dca-cash-reserve",
            "--market",
            "AU_EQUITIES",
            "--instrument-class",
            "ETF",
            "--started-at",
            "2025-01-30T00:00:00Z",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        fixture = payload["fixtureDiagnostics"][0]
        self.assertTrue(payload["ready"])
        self.assertEqual(payload["readinessScope"], "offline-backtest-only")
        self.assertEqual(fixture["symbol"], "VAS")
        self.assertEqual(fixture["market"], "AU_EQUITIES")
        self.assertEqual(fixture["instrumentClass"], "ETF")
        self.assertEqual(
            fixture["inputSha256"],
            "6e7c464580d94a33f2f14613e7301af99af7a513e82ee8a71d92c782caf82c4b",
        )
        self.assertEqual(fixture["strategyHistoryDiagnostics"]["candidateIntent"], "skip")
        self.assertEqual(payload["providerCalls"], "blocked")
        self.assertEqual(payload["executionRoutes"], "absent")
        self.assertEqual(payload["demoExecution"], "blocked")
        self.assertEqual(payload["liveExecution"], "blocked")
        self.assertEqual(payload["accountData"], "absent")

    def test_readiness_cli_returns_redacted_not_ready_report_for_missing_fixture(self):
        result = self.run_cli(
            "readiness",
            "--fixture",
            "SPY=/tmp/money-maker-missing-fixture.csv",
            "--started-at",
            "2026-05-15T00:00:00Z",
        )

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ready"])
        self.assertEqual(payload["providerCalls"], "blocked")
        self.assertEqual(payload["executionRoutes"], "absent")
        self.assertEqual(payload["fixtureDiagnostics"][0]["errors"], ["offline fixture file does not exist"])

    def test_backtest_cli_rejects_fixture_over_row_limit(self):
        result = self.run_cli(
            "backtest",
            "--history-csv",
            str(FIXTURE_PATH),
            "--symbol",
            "SPY",
            "--max-fixture-rows",
            "2",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("maxFixtureRows=2", result.stderr)

    def test_backtest_cli_rejects_unknown_strategy_parameter_json(self):
        result = self.run_cli(
            "backtest",
            "--history-csv",
            str(FIXTURE_PATH),
            "--symbol",
            "SPY",
            "--strategy-params-json",
            json.dumps({"executionRoute": "demo"}),
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported strategy parameters", result.stderr)

    def test_backtest_cli_rejects_non_finite_budget_inputs(self):
        result = self.run_cli(
            "backtest",
            "--history-csv",
            str(FIXTURE_PATH),
            "--symbol",
            "SPY",
            "--budget-usd",
            "NaN",
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("budget-usd must be a finite number", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_cli_rejects_execute_and_trade_modes(self):
        for mode in ("execute", "trade", "trading"):
            result = self.run_cli("backtest", "--mode", mode)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("execution mode is disabled", result.stderr)

    def test_cli_profile_hook_writes_stdlib_profile_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_path = Path(temp_dir) / "profile.txt"
            result = self.run_cli("--profile", str(profile_path), "backtest", "--started-at", "2026-05-15T00:00:00Z")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(profile_path.exists())
            self.assertIn("function calls", profile_path.read_text(encoding="utf-8"))

    def test_ledger_report_cli_returns_controlled_corruption_report(self):
        legacy_record = {
            "ledgerVersion": 1,
            "strategyId": "dca-cash-reserve",
            "decision": "skip",
            "riskResult": "blocked",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger_path = Path(temp_dir) / "mixed-ledger.jsonl"
            ledger_path.write_text(json.dumps(legacy_record) + "\n{bad-json}\n", encoding="utf-8")
            result = self.run_cli("ledger-report", str(ledger_path))

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertEqual(payload["integrity"]["state"], "corrupted")
        self.assertFalse(payload["integrity"]["complete"])
        self.assertEqual(payload["integrity"]["acceptedRecordCount"], 1)
        self.assertEqual(payload["integrity"]["rejectedRecordCount"], 1)
        self.assertEqual(payload["integrity"]["sourceMutation"], "not-attempted")
        self.assertEqual(payload["providerCalls"], "blocked")
        self.assertEqual(payload["executionRoutes"], "absent")

    def test_ledger_report_cli_accepts_clean_v2_ledger(self):
        record = build_synthetic_backtest(include_ledger_records=True)["ledgerRecords"][0]
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger_path = Path(temp_dir) / "clean-ledger.jsonl"
            ledger_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
            result = self.run_cli("ledger-report", str(ledger_path))

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["integrity"]["state"], "clean")
        self.assertTrue(payload["integrity"]["complete"])
        self.assertEqual(payload["integrity"]["acceptedRecordCount"], 1)
        self.assertEqual(payload["integrity"]["rejectedRecordCount"], 0)


if __name__ == "__main__":
    unittest.main()
