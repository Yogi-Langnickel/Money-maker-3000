import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "market_history" / "spy-daily.csv"
GLD_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "market_history" / "gld-daily.csv"


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
        self.assertEqual(payload["scenarioSummaries"][0]["allocation"]["providerDemoBalance"], "redacted")

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
        self.assertEqual(payload["coverage"]["fixtureCount"], 2)
        self.assertEqual(payload["coverage"]["totalRows"], 6)
        self.assertEqual(payload["metadata"]["symbols"], ["GLD", "SPY"])
        self.assertEqual(payload["perSymbolDiagnostics"][0]["periodDiagnostics"]["providerCalls"], "blocked")
        self.assertEqual(payload["perSymbolDiagnostics"][1]["coverage"]["rowCount"], 3)
        self.assertNotIn("1000000", serialized)

    def test_fixture_batch_cli_accepts_repeated_fixture_entries(self):
        result = self.run_cli(
            "fixture-batch",
            "--fixture",
            f"SPY={FIXTURE_PATH}",
            "--fixture",
            f"GLD={GLD_FIXTURE_PATH}",
            "--started-at",
            "2026-05-15T00:00:00Z",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["coverage"]["fixtureCount"], 2)
        self.assertEqual([item["symbol"] for item in payload["perSymbolDiagnostics"]], ["SPY", "GLD"])

    def test_readiness_cli_reports_backtest_ready_without_provider_calls(self):
        result = self.run_cli(
            "readiness",
            "--fixture",
            f"SPY={FIXTURE_PATH}",
            "--fixture",
            f"GLD={GLD_FIXTURE_PATH}",
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
        self.assertEqual(payload["metadata"]["symbols"], ["GLD", "SPY"])
        self.assertEqual([fixture["symbol"] for fixture in payload["fixtureDiagnostics"]], ["SPY", "GLD"])
        self.assertTrue(all(gate["ok"] for gate in payload["gates"]))
        self.assertTrue(payload["nextSafeCommands"])
        self.assertNotIn("1000000", serialized)

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


if __name__ == "__main__":
    unittest.main()
