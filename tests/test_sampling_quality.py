import json
import unittest

from money_maker_3000.market_history import Bar
from money_maker_3000.sampling_quality import build_sampling_quality, sampling_quality_warning


def bar(observed_date: str) -> Bar:
    return Bar("SPY", observed_date, 100.0, 100.0, 100.0, 100.0, 1000.0, "synthetic-test-fixture")


class SamplingQualityTests(unittest.TestCase):
    def test_single_observation_is_insufficient_without_anomaly_warning(self):
        quality = build_sampling_quality([bar("2026-05-11")])

        self.assertEqual(quality["state"], "insufficient-history")
        self.assertEqual(quality["observationCount"], 1)
        self.assertEqual(quality["intervalCount"], 0)
        self.assertEqual(quality["firstDate"], "2026-05-11")
        self.assertEqual(quality["lastDate"], "2026-05-11")
        self.assertEqual(quality["calendarSpanDays"], 0)
        self.assertEqual(quality["observedWeekdayCount"], 1)
        self.assertEqual(quality["observedWeekendCount"], 0)
        self.assertEqual(quality["maximumCalendarGapDays"], 0)
        self.assertIsNone(sampling_quality_warning(quality["state"]))

    def test_friday_to_monday_is_covered_on_weekday_grid(self):
        quality = build_sampling_quality([bar("2026-05-08"), bar("2026-05-11")])

        self.assertEqual(quality["state"], "weekday-grid-covered")
        self.assertEqual(quality["potentialMissingWeekdayCount"], 0)
        self.assertEqual(quality["intervalsOverThreeCalendarDays"], 0)
        self.assertEqual(quality["maximumCalendarGapDays"], 3)
        self.assertIsNone(sampling_quality_warning(quality["state"]))

    def test_holiday_like_gap_is_potential_not_proven_missing_session(self):
        quality = build_sampling_quality([bar("2026-05-08"), bar("2026-05-12")])

        self.assertEqual(quality["state"], "potential-weekday-gaps")
        self.assertEqual(quality["potentialMissingWeekdayCount"], 1)
        self.assertEqual(quality["intervalsOverThreeCalendarDays"], 1)
        self.assertEqual(quality["maximumCalendarGapDays"], 4)
        self.assertIn("not proof of missing market sessions", quality["weekdayGapCaveat"])
        self.assertIn("exchange-calendar review", sampling_quality_warning(quality["state"]))

    def test_weekend_observation_is_explicit_without_weekday_gap(self):
        quality = build_sampling_quality(
            [bar("2026-05-08"), bar("2026-05-09"), bar("2026-05-11")]
        )

        self.assertEqual(quality["state"], "non-weekday-observations")
        self.assertEqual(quality["observedWeekdayCount"], 2)
        self.assertEqual(quality["observedWeekendCount"], 1)
        self.assertEqual(quality["potentialMissingWeekdayCount"], 0)
        self.assertIn("exchange-calendar review", sampling_quality_warning(quality["state"]))

    def test_weekend_observation_and_weekday_gap_are_mixed_irregular_sampling(self):
        quality = build_sampling_quality(
            [bar("2026-05-08"), bar("2026-05-09"), bar("2026-05-12")]
        )

        self.assertEqual(quality["state"], "mixed-irregular-sampling")
        self.assertEqual(quality["observedWeekendCount"], 1)
        self.assertEqual(quality["potentialMissingWeekdayCount"], 1)
        self.assertEqual(quality["intervalsOverThreeCalendarDays"], 0)
        self.assertEqual(quality["maximumCalendarGapDays"], 3)
        self.assertIn("exchange-calendar review", sampling_quality_warning(quality["state"]))

    def test_far_apart_dates_use_bounded_interval_arithmetic(self):
        quality = build_sampling_quality([bar("1900-01-01"), bar("9999-12-31")])

        self.assertEqual(quality["calendarSpanDays"], 2_958_463)
        self.assertEqual(quality["potentialMissingWeekdayCount"], 2_113_188)
        self.assertEqual(quality["intervalsOverThreeCalendarDays"], 1)
        self.assertEqual(quality["maximumCalendarGapDays"], 2_958_463)

    def test_output_is_non_financial_and_fail_closed_at_all_boundaries(self):
        quality = build_sampling_quality([bar("2026-05-08"), bar("2026-05-11")])
        serialized = json.dumps(quality).lower()

        self.assertEqual(quality["calendarBasis"], "weekday-grid-not-exchange-calendar")
        self.assertEqual(quality["providerCalls"], "blocked")
        self.assertEqual(quality["accountData"], "absent")
        self.assertEqual(quality["execution"], "blocked")
        self.assertEqual(quality["candidateIntent"], "skip")
        for forbidden in (
            "price",
            "return",
            "pnl",
            "profitability",
            "recommendation",
            "executionquality",
        ):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
