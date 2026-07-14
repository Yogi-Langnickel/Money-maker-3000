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
        self.assertEqual(diagnostics["walkForward"]["eligibleObservationCount"], 1)
        self.assertEqual(diagnostics["walkForward"]["stateCounts"], {"trigger-observed": 1})
        self.assertEqual(diagnostics["walkForward"]["foldCount"], 1)
        self.assertEqual(
            diagnostics["walkForward"]["folds"],
            [
                {
                    "foldIndex": 0,
                    "observationCount": 1,
                    "firstObservationDate": "2025-01-05",
                    "lastObservationDate": "2025-01-05",
                    "stateCounts": {"trigger-observed": 1},
                    "transitionCount": 0,
                }
            ],
        )

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

    def test_volatility_walk_forward_counts_states_and_transitions(self):
        diagnostics = build_strategy_history_diagnostics(
            bars_from_closes([100.0, 100.0, 100.0, 100.0, 100.0, 95.0, 92.0, 97.0, 100.0]),
            strategy_id="volatility-band-accumulator",
            strategy_parameters={"lookbackDays": 5, "dropTriggerPct": 3.0},
        )

        walk_forward = diagnostics["walkForward"]
        self.assertEqual(walk_forward["state"], "available")
        self.assertEqual(walk_forward["eligibleObservationCount"], 5)
        self.assertEqual(
            walk_forward["stateCounts"],
            {"no-trigger-observed": 1, "recovery-observed": 1, "trigger-observed": 3},
        )
        self.assertEqual(walk_forward["transitionCount"], 2)
        self.assertEqual(walk_forward["firstObservationDate"], "2025-01-05")
        self.assertEqual(walk_forward["lastObservationDate"], "2025-01-09")
        self.assertEqual(walk_forward["candidateIntent"], "skip")
        self.assertEqual(walk_forward["providerCalls"], "blocked")
        self.assertEqual(walk_forward["foldCount"], 5)
        self.assertEqual([fold["foldIndex"] for fold in walk_forward["folds"]], [0, 1, 2, 3, 4])
        self.assertEqual([fold["observationCount"] for fold in walk_forward["folds"]], [1, 1, 1, 1, 1])
        self.assertEqual(walk_forward["folds"][0]["firstObservationDate"], "2025-01-05")
        self.assertEqual(walk_forward["folds"][-1]["lastObservationDate"], "2025-01-09")
        self.assertEqual(sum(fold["transitionCount"] for fold in walk_forward["folds"]), 0)

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
        self.assertEqual(insufficient["walkForward"]["state"], "insufficient-history")
        self.assertEqual(insufficient["walkForward"]["eligibleObservationCount"], 0)
        self.assertEqual(insufficient["walkForward"]["foldCount"], 0)
        self.assertEqual(insufficient["walkForward"]["folds"], [])
        self.assertEqual(rising["walkForward"]["stateCounts"], {"trend-confirmed": 1})

    def test_slow_trend_walk_forward_reports_historical_state_coverage(self):
        diagnostics = build_strategy_history_diagnostics(
            bars_from_closes([100.0] * 62 + [110.0] * 10),
            strategy_id="slow-trend-allocation",
            strategy_parameters={"shortLookbackDays": 10, "longLookbackDays": 60, "confirmationBars": 2},
        )

        walk_forward = diagnostics["walkForward"]
        self.assertEqual(walk_forward["eligibleObservationCount"], 12)
        self.assertEqual(walk_forward["stateCounts"], {"trend-confirmed": 9, "trend-not-confirmed": 3})
        self.assertEqual(walk_forward["transitionCount"], 1)
        self.assertEqual(walk_forward["candidateIntent"], "skip")
        self.assertEqual(walk_forward["executionRoutes"], "absent")
        self.assertEqual(walk_forward["foldCount"], 5)
        self.assertEqual([fold["observationCount"] for fold in walk_forward["folds"]], [3, 3, 2, 2, 2])
        self.assertEqual(
            [fold["stateCounts"] for fold in walk_forward["folds"]],
            [
                {"trend-not-confirmed": 3},
                {"trend-confirmed": 3},
                {"trend-confirmed": 2},
                {"trend-confirmed": 2},
                {"trend-confirmed": 2},
            ],
        )
        self.assertEqual(walk_forward["folds"][0]["firstObservationDate"], "2025-03-02")
        self.assertEqual(walk_forward["folds"][0]["lastObservationDate"], "2025-03-04")
        self.assertEqual(walk_forward["folds"][-1]["firstObservationDate"], "2025-03-12")
        self.assertEqual(walk_forward["folds"][-1]["lastObservationDate"], "2025-03-13")

    def test_diagnostics_are_redacted_and_diagnostics_only(self):
        diagnostics = build_strategy_history_diagnostics(
            bars_from_closes([100.0] * 5),
            strategy_id="dca-cash-reserve",
        )
        serialized = json.dumps(diagnostics).lower()

        self.assertEqual(diagnostics["state"], "not-applicable")
        self.assertEqual(diagnostics["accountData"], "absent")
        self.assertEqual(diagnostics["walkForward"]["state"], "not-applicable")
        self.assertEqual(diagnostics["walkForward"]["foldCount"], 0)
        self.assertEqual(diagnostics["walkForward"]["folds"], [])
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
        self.assertEqual(diagnostics["walkForward"]["state"], "invalid-history")
        self.assertEqual(diagnostics["walkForward"]["foldCount"], 0)
        self.assertEqual(diagnostics["walkForward"]["folds"], [])
        self.assertEqual(diagnostics["candidateIntent"], "skip")
        self.assertNotIn("nan", json.dumps(diagnostics).lower())


if __name__ == "__main__":
    unittest.main()
