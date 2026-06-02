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
        self.assertEqual(payload["periodDiagnostics"]["dtoVersion"], "market-period-diagnostics.v1")
        self.assertEqual(payload["periodDiagnostics"]["periods"][0]["period"], "24h")
        self.assertEqual(payload["scenarioSummaries"][0]["allocation"]["providerDemoBalance"], "redacted")

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
