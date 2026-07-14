import json
import unittest
from datetime import date, timedelta

from money_maker_3000.market_history import Bar
from money_maker_3000.sampling_quality import (
    build_sampling_quality,
    sampling_quality_warning,
    validated_sampling_quality,
)


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
        self.assertIsNone(sampling_quality_warning(quality))

    def test_single_weekend_observation_stays_insufficient_and_warns(self):
        quality = build_sampling_quality([bar("2026-05-09")])

        self.assertEqual(quality["state"], "insufficient-history")
        self.assertEqual(quality["observedWeekendCount"], 1)
        self.assertIn("exchange-calendar review", sampling_quality_warning(quality))

    def test_friday_to_monday_is_covered_on_weekday_grid(self):
        quality = build_sampling_quality([bar("2026-05-08"), bar("2026-05-11")])

        self.assertEqual(quality["state"], "weekday-grid-covered")
        self.assertEqual(quality["potentialMissingWeekdayCount"], 0)
        self.assertEqual(quality["intervalsOverThreeCalendarDays"], 0)
        self.assertEqual(quality["maximumCalendarGapDays"], 3)
        self.assertIsNone(sampling_quality_warning(quality))

    def test_holiday_like_gap_is_potential_not_proven_missing_session(self):
        quality = build_sampling_quality([bar("2026-05-08"), bar("2026-05-12")])

        self.assertEqual(quality["state"], "potential-weekday-gaps")
        self.assertEqual(quality["potentialMissingWeekdayCount"], 1)
        self.assertEqual(quality["intervalsOverThreeCalendarDays"], 1)
        self.assertEqual(quality["maximumCalendarGapDays"], 4)
        self.assertIn("not proof of missing market sessions", quality["weekdayGapCaveat"])
        self.assertIn("exchange-calendar review", sampling_quality_warning(quality))

    def test_weekend_observation_is_explicit_without_weekday_gap(self):
        quality = build_sampling_quality(
            [bar("2026-05-08"), bar("2026-05-09"), bar("2026-05-11")]
        )

        self.assertEqual(quality["state"], "non-weekday-observations")
        self.assertEqual(quality["observedWeekdayCount"], 2)
        self.assertEqual(quality["observedWeekendCount"], 1)
        self.assertEqual(quality["potentialMissingWeekdayCount"], 0)
        self.assertIn("exchange-calendar review", sampling_quality_warning(quality))

    def test_weekend_observation_and_weekday_gap_are_mixed_irregular_sampling(self):
        quality = build_sampling_quality(
            [bar("2026-05-08"), bar("2026-05-09"), bar("2026-05-12")]
        )

        self.assertEqual(quality["state"], "mixed-irregular-sampling")
        self.assertEqual(quality["observedWeekendCount"], 1)
        self.assertEqual(quality["potentialMissingWeekdayCount"], 1)
        self.assertEqual(quality["intervalsOverThreeCalendarDays"], 0)
        self.assertEqual(quality["maximumCalendarGapDays"], 3)
        self.assertIn("exchange-calendar review", sampling_quality_warning(quality))

    def test_warning_helper_rejects_malformed_state_and_counter_evidence(self):
        quality = build_sampling_quality([bar("2026-05-11")])

        with self.assertRaisesRegex(ValueError, "sampling quality is invalid"):
            sampling_quality_warning("insufficient-history")

        malformed = dict(quality)
        malformed["observedWeekendCount"] = 1
        with self.assertRaisesRegex(ValueError, "sampling quality is invalid"):
            sampling_quality_warning(malformed)

    def test_validator_rejects_singleton_with_distinct_dates_and_span(self):
        malformed = build_sampling_quality([bar("2026-05-11")])
        malformed["lastDate"] = "2026-05-12"
        malformed["calendarSpanDays"] = 1

        with self.assertRaisesRegex(ValueError, "sampling quality is invalid"):
            validated_sampling_quality(malformed)

    def test_validator_rejects_impossible_adjacent_weekday_gap_count(self):
        malformed = build_sampling_quality([bar("2026-05-11"), bar("2026-05-12")])
        malformed["potentialMissingWeekdayCount"] = 1
        malformed["state"] = "potential-weekday-gaps"

        with self.assertRaisesRegex(ValueError, "sampling quality is invalid"):
            validated_sampling_quality(malformed)

    def test_validator_rejects_endpoint_weekdays_missing_from_observed_count(self):
        malformed = build_sampling_quality([bar("2026-05-11"), bar("2026-05-12")])
        malformed["observedWeekdayCount"] = 1
        malformed["observedWeekendCount"] = 1
        malformed["state"] = "non-weekday-observations"

        with self.assertRaisesRegex(ValueError, "sampling quality is invalid"):
            validated_sampling_quality(malformed)

    def test_validator_rejects_interior_weekend_count_without_weekend_dates(self):
        malformed = build_sampling_quality(
            [bar("2026-05-11"), bar("2026-05-12"), bar("2026-05-14")]
        )
        malformed["observedWeekdayCount"] = 2
        malformed["observedWeekendCount"] = 1
        malformed["potentialMissingWeekdayCount"] = 2
        malformed["state"] = "mixed-irregular-sampling"

        with self.assertRaisesRegex(ValueError, "sampling quality is invalid"):
            validated_sampling_quality(malformed)

    def test_validator_rejects_impossible_span_maximum_gap_combination(self):
        malformed = build_sampling_quality(
            [bar("2026-01-01"), bar("2026-01-02"), bar("2026-01-04"), bar("2026-01-05")]
        )
        malformed["maximumCalendarGapDays"] = 3

        with self.assertRaisesRegex(ValueError, "sampling quality is invalid"):
            validated_sampling_quality(malformed)

    def test_validator_rejects_impossible_long_gap_summary(self):
        malformed = build_sampling_quality(
            [
                bar("2026-01-05"),
                bar("2026-01-10"),
                bar("2026-01-14"),
                bar("2026-01-15"),
                bar("2026-01-16"),
            ]
        )
        malformed["maximumCalendarGapDays"] = 6

        with self.assertRaisesRegex(ValueError, "sampling quality is invalid"):
            validated_sampling_quality(malformed)

    def test_validator_accepts_exact_short_and_long_gap_feasibility_boundaries(self):
        cases = (
            (date(2026, 1, 5), (3, 1, 1), 0, 3, 5),
            (date(2026, 1, 5), (3, 3, 3), 0, 3, 9),
            (date(2026, 1, 5), (5, 4, 1, 1), 2, 5, 11),
            (date(2026, 1, 5), (5, 5, 3, 3), 2, 5, 16),
        )
        for first, gaps, expected_long_count, expected_maximum, expected_span in cases:
            with self.subTest(gaps=gaps):
                dates = [first]
                for gap in gaps:
                    dates.append(dates[-1] + timedelta(days=gap))
                quality = build_sampling_quality([bar(observed.isoformat()) for observed in dates])

                self.assertEqual(quality["intervalsOverThreeCalendarDays"], expected_long_count)
                self.assertEqual(quality["maximumCalendarGapDays"], expected_maximum)
                self.assertEqual(quality["calendarSpanDays"], expected_span)
                self.assertEqual(validated_sampling_quality(quality), quality)

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
