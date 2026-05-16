import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "market_history" / "spy-daily.csv"


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
        self.assertEqual(payload["scenarioSummaries"][0]["allocation"]["providerDemoBalance"], "redacted")

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
