import json
import unittest
from datetime import date, timedelta

from money_maker_3000.history_signals import build_strategy_history_diagnostics
from money_maker_3000.market_history import Bar


def bars_from_closes(closes: list[float]) -> list[Bar]:
    first = date(2025, 1, 1)
    return [
        Bar(
            symbol="SPY",
            date=(first + timedelta(days=index)).isoformat(),
            open=close,
            high=close,
            low=close,
            close=close,
            volume=1000.0,
            source="synthetic-test-fixture",
        )
        for index, close in enumerate(closes)
    ]


class HistorySignalTests(unittest.TestCase):
    def test_volatility_band_reports_deterministic_trigger_without_action(self):
        bars = bars_from_closes([100.0, 102.0, 101.0, 99.0, 96.0])
        diagnostics = build_strategy_history_diagnostics(
            bars,
            strategy_id="volatility-band-accumulator",
            strategy_parameters={"lookbackDays": 5, "dropTriggerPct": 3.0},
        )

        self.assertEqual(diagnostics["state"], "trigger-observed")
        self.assertEqual(diagnostics["metrics"]["rollingPeakClose"], 102.0)
        self.assertEqual(diagnostics["metrics"]["windowLowClose"], 96.0)
        self.assertEqual(diagnostics["metrics"]["declineFromRollingPeakPct"], -5.882353)
        self.assertEqual(diagnostics["metrics"]["maximumObservedDeclinePct"], -5.882353)
        self.assertEqual(diagnostics["candidateIntent"], "skip")
        self.assertEqual(diagnostics["providerCalls"], "blocked")
        self.assertEqual(diagnostics["executionRoutes"], "absent")

    def test_volatility_band_distinguishes_stable_and_recovering_windows(self):
        stable = build_strategy_history_diagnostics(
            bars_from_closes([100.0, 100.5, 100.0, 100.4, 100.2]),
            strategy_id="volatility-band-accumulator",
            strategy_parameters={"lookbackDays": 5, "dropTriggerPct": 3.0},
        )
        recovering = build_strategy_history_diagnostics(
            bars_from_closes([100.0, 95.0, 92.0, 97.0, 99.0]),
            strategy_id="volatility-band-accumulator",
            strategy_parameters={"lookbackDays": 5, "dropTriggerPct": 3.0},
        )

        self.assertEqual(stable["state"], "no-trigger-observed")
        self.assertEqual(recovering["state"], "recovery-observed")
        self.assertEqual(recovering["metrics"]["declineFromRollingPeakPct"], -1.0)
        self.assertEqual(recovering["metrics"]["maximumObservedDeclinePct"], -8.0)
        self.assertEqual(recovering["candidateIntent"], "skip")

    def test_slow_trend_requires_full_window_and_confirmation(self):
        insufficient = build_strategy_history_diagnostics(
            bars_from_closes([100.0] * 10),
            strategy_id="slow-trend-allocation",
            strategy_parameters={"shortLookbackDays": 10, "longLookbackDays": 60, "confirmationBars": 3},
        )
        rising = build_strategy_history_diagnostics(
            bars_from_closes([float(value) for value in range(1, 63)]),
            strategy_id="slow-trend-allocation",
            strategy_parameters={"shortLookbackDays": 10, "longLookbackDays": 60, "confirmationBars": 3},
        )

        self.assertEqual(insufficient["state"], "insufficient-history")
        self.assertEqual(insufficient["requiredBarCount"], 62)
        self.assertEqual(rising["state"], "trend-confirmed")
        self.assertEqual(rising["metrics"]["confirmationMatches"], 3)
        self.assertGreater(rising["metrics"]["shortAverageClose"], rising["metrics"]["longAverageClose"])

    def test_diagnostics_are_redacted_and_diagnostics_only(self):
        diagnostics = build_strategy_history_diagnostics(
            bars_from_closes([100.0] * 5),
            strategy_id="dca-cash-reserve",
        )
        serialized = json.dumps(diagnostics).lower()

        self.assertEqual(diagnostics["state"], "not-applicable")
        self.assertEqual(diagnostics["accountData"], "absent")
        for forbidden in ("apikey", "accountid", "positionid", "orderid", "winrate", "sharpe", "approved-order"):
            self.assertNotIn(forbidden, serialized)

    def test_invalid_parameters_fail_closed_to_defaults(self):
        diagnostics = build_strategy_history_diagnostics(
            bars_from_closes([100.0] * 20),
            strategy_id="volatility-band-accumulator",
            strategy_parameters={"lookbackDays": "bad", "dropTriggerPct": float("nan")},
        )

        self.assertEqual(diagnostics["parameterState"], "invalid-defaulted")
        self.assertEqual(diagnostics["requiredBarCount"], 20)
        self.assertEqual(diagnostics["metrics"]["dropTriggerPct"], 3.0)

    def test_malformed_history_fails_closed_without_non_finite_metrics(self):
        malformed = bars_from_closes([100.0, float("nan")])
        diagnostics = build_strategy_history_diagnostics(
            malformed,
            strategy_id="volatility-band-accumulator",
        )

        self.assertEqual(diagnostics["state"], "invalid-history")
        self.assertIsNone(diagnostics["metrics"])
        self.assertEqual(diagnostics["candidateIntent"], "skip")
        self.assertNotIn("nan", json.dumps(diagnostics).lower())


if __name__ == "__main__":
    unittest.main()
