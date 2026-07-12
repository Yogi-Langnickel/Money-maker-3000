import json
import unittest
from datetime import datetime

from money_maker_3000.backtest import build_historical_fixture_backtest, build_offline_fixture_batch_diagnostics
from money_maker_3000.market_history import Bar


PARAMETERS = {
    "targetWeights": {"SPY": 0.7, "GLD": 0.3},
    "rebalanceThresholdPct": 5.0,
    "maxOrderUsd": 250.0,
    "minCashReserveUsd": 100.0,
    "maxOpenPositions": 3,
}


def report(symbol: str, closes: list[float]):
    bars = [
        Bar(
            symbol=symbol,
            date=f"2026-05-{11 + index:02d}",
            open=close,
            high=close,
            low=close,
            close=close,
            volume=1000.0,
            source="synthetic-test-fixture",
        )
        for index, close in enumerate(closes)
    ]
    return build_historical_fixture_backtest(
        bars=bars,
        strategy_id="threshold-rebalance",
        selected_instrument={"symbol": symbol, "market": "US_EQUITIES", "instrumentClass": "ETF"},
        strategy_parameters=PARAMETERS,
        started_at=datetime.fromisoformat("2026-05-15T00:00:00+00:00"),
    )


class RebalanceHistoryTests(unittest.TestCase):
    def test_batch_reports_deterministic_relative_weight_drift(self):
        batch = build_offline_fixture_batch_diagnostics(
            reports=[report("SPY", [100.0, 120.0]), report("GLD", [100.0, 90.0])],
            started_at=datetime.fromisoformat("2026-05-15T00:00:00+00:00"),
        )
        diagnostics = batch["rebalanceHistoryDiagnostics"]
        serialized = json.dumps(diagnostics).lower()

        self.assertEqual(diagnostics["state"], "available")
        self.assertEqual(diagnostics["candidateIntent"], "skip")
        self.assertEqual(diagnostics["providerCalls"], "blocked")
        self.assertEqual(diagnostics["accountData"], "absent")
        self.assertEqual(diagnostics["portfolioHoldings"], "absent")
        self.assertEqual(diagnostics["executionRoutes"], "absent")
        self.assertEqual(diagnostics["metrics"]["thresholdState"], "historical-drift-exceeded")
        self.assertEqual(diagnostics["metrics"]["maxAbsoluteDriftPercentagePoints"], 5.675676)
        self.assertAlmostEqual(
            sum(item["normalizedHistoricalWeight"] for item in diagnostics["metrics"]["weights"]),
            1.0,
        )
        for forbidden in ("accountid", "positionid", "orderid", "apikey", "userkey", "winrate", "sharpe"):
            self.assertNotIn(forbidden, serialized)

    def test_batch_reports_within_threshold_and_coverage_mismatch(self):
        within = build_offline_fixture_batch_diagnostics(
            reports=[report("SPY", [100.0, 101.0]), report("GLD", [100.0, 100.0])]
        )["rebalanceHistoryDiagnostics"]
        incomplete = build_offline_fixture_batch_diagnostics(
            reports=[report("SPY", [100.0, 120.0])]
        )["rebalanceHistoryDiagnostics"]

        self.assertEqual(within["metrics"]["thresholdState"], "within-historical-threshold")
        self.assertEqual(incomplete["state"], "coverage-mismatch")
        self.assertEqual(incomplete["metrics"]["targetSymbols"], ["GLD", "SPY"])
        self.assertEqual(incomplete["metrics"]["availableSymbols"], ["SPY"])
        self.assertEqual(incomplete["candidateIntent"], "skip")

    def test_mixed_strategy_batch_is_not_applicable(self):
        threshold = report("SPY", [100.0, 101.0])
        dca = build_historical_fixture_backtest(
            bars=[Bar("GLD", "2026-05-11", 100.0, 100.0, 100.0, 100.0, 1000.0, "synthetic-test-fixture")],
            selected_instrument={"symbol": "GLD", "market": "US_EQUITIES", "instrumentClass": "ETF"},
        )
        diagnostics = build_offline_fixture_batch_diagnostics(reports=[threshold, dca])["rebalanceHistoryDiagnostics"]

        self.assertEqual(diagnostics["state"], "not-applicable")
        self.assertIsNone(diagnostics["metrics"])
        self.assertEqual(diagnostics["candidateIntent"], "skip")

    def test_mismatched_history_windows_fail_closed(self):
        spy = report("SPY", [100.0, 101.0])
        gld = report("GLD", [100.0, 100.0])
        gld["periodDiagnostics"]["periods"][-1]["startDate"] = "2026-05-10"
        diagnostics = build_offline_fixture_batch_diagnostics(
            reports=[spy, gld]
        )["rebalanceHistoryDiagnostics"]

        self.assertEqual(diagnostics["state"], "invalid-input")
        self.assertIsNone(diagnostics["metrics"])
        self.assertEqual(diagnostics["candidateIntent"], "skip")


if __name__ == "__main__":
    unittest.main()
