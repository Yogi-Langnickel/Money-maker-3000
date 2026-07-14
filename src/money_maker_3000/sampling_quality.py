from __future__ import annotations

from datetime import date
from typing import Any, Iterable

from money_maker_3000.market_history import Bar

SAMPLING_QUALITY_VERSION = "market-history-sampling-quality.v1"
CALENDAR_BASIS = "weekday-grid-not-exchange-calendar"
WEEKDAY_GAP_CAVEAT = (
    "potential weekday gaps are not proof of missing market sessions because holidays and exchange calendars "
    "are not modeled"
)
SAMPLING_QUALITY_KEYS = (
    "dtoVersion",
    "state",
    "observationCount",
    "intervalCount",
    "firstDate",
    "lastDate",
    "calendarSpanDays",
    "observedWeekdayCount",
    "observedWeekendCount",
    "potentialMissingWeekdayCount",
    "intervalsOverThreeCalendarDays",
    "maximumCalendarGapDays",
    "calendarBasis",
    "weekdayGapCaveat",
    "providerCalls",
    "accountData",
    "execution",
    "candidateIntent",
    "claimBoundary",
)
SAMPLING_STATES = {
    "insufficient-history",
    "weekday-grid-covered",
    "potential-weekday-gaps",
    "non-weekday-observations",
    "mixed-irregular-sampling",
}
INTEGER_KEYS = (
    "observationCount",
    "intervalCount",
    "calendarSpanDays",
    "observedWeekdayCount",
    "observedWeekendCount",
    "potentialMissingWeekdayCount",
    "intervalsOverThreeCalendarDays",
    "maximumCalendarGapDays",
)


def _weekdays_strictly_between(start: date, end: date) -> int:
    interior_days = (end - start).days - 1
    if interior_days <= 0:
        return 0
    complete_weeks, remainder = divmod(interior_days, 7)
    first_interior_weekday = (start.weekday() + 1) % 7
    remainder_weekdays = sum(
        1 for offset in range(remainder) if (first_interior_weekday + offset) % 7 < 5
    )
    return complete_weeks * 5 + remainder_weekdays


def build_sampling_quality(bars: Iterable[Bar]) -> dict[str, object]:
    observation_dates: list[date] = []
    previous: date | None = None
    for bar in bars:
        if not isinstance(bar, Bar) or not isinstance(bar.date, str):
            raise ValueError("sampling quality requires parsed market-history bars")
        try:
            observed = date.fromisoformat(bar.date)
        except ValueError as exc:
            raise ValueError("sampling quality requires canonical ISO observation dates") from exc
        if observed.isoformat() != bar.date or (previous is not None and observed <= previous):
            raise ValueError("sampling quality requires strictly chronological observation dates")
        observation_dates.append(observed)
        previous = observed

    observation_count = len(observation_dates)
    interval_count = max(0, observation_count - 1)
    observed_weekday_count = sum(observed.weekday() < 5 for observed in observation_dates)
    observed_weekend_count = observation_count - observed_weekday_count
    potential_missing_weekday_count = 0
    intervals_over_three_calendar_days = 0
    maximum_calendar_gap_days = 0
    for start, end in zip(observation_dates, observation_dates[1:]):
        gap_days = (end - start).days
        potential_missing_weekday_count += _weekdays_strictly_between(start, end)
        intervals_over_three_calendar_days += int(gap_days > 3)
        maximum_calendar_gap_days = max(maximum_calendar_gap_days, gap_days)

    if observation_count < 2:
        state = "insufficient-history"
    elif potential_missing_weekday_count and observed_weekend_count:
        state = "mixed-irregular-sampling"
    elif potential_missing_weekday_count:
        state = "potential-weekday-gaps"
    elif observed_weekend_count:
        state = "non-weekday-observations"
    else:
        state = "weekday-grid-covered"

    return {
        "dtoVersion": SAMPLING_QUALITY_VERSION,
        "state": state,
        "observationCount": observation_count,
        "intervalCount": interval_count,
        "firstDate": observation_dates[0].isoformat() if observation_dates else None,
        "lastDate": observation_dates[-1].isoformat() if observation_dates else None,
        "calendarSpanDays": (observation_dates[-1] - observation_dates[0]).days if observation_dates else 0,
        "observedWeekdayCount": observed_weekday_count,
        "observedWeekendCount": observed_weekend_count,
        "potentialMissingWeekdayCount": potential_missing_weekday_count,
        "intervalsOverThreeCalendarDays": intervals_over_three_calendar_days,
        "maximumCalendarGapDays": maximum_calendar_gap_days,
        "calendarBasis": CALENDAR_BASIS,
        "weekdayGapCaveat": WEEKDAY_GAP_CAVEAT,
        "providerCalls": "blocked",
        "accountData": "absent",
        "execution": "blocked",
        "candidateIntent": "skip",
        "claimBoundary": "sampling-coverage-only-no-financial-or-session-completeness-claim",
    }


def validated_sampling_quality(value: Any) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != set(SAMPLING_QUALITY_KEYS):
        raise ValueError("sampling quality is invalid")
    if (
        value["dtoVersion"] != SAMPLING_QUALITY_VERSION
        or value["state"] not in SAMPLING_STATES
        or any(
            not isinstance(value[key], int) or isinstance(value[key], bool) or value[key] < 0
            for key in INTEGER_KEYS
        )
        or value["intervalCount"] != max(0, value["observationCount"] - 1)
        or value["observedWeekdayCount"] + value["observedWeekendCount"] != value["observationCount"]
        or value["intervalsOverThreeCalendarDays"] > value["intervalCount"]
        or value["calendarBasis"] != CALENDAR_BASIS
        or value["weekdayGapCaveat"] != WEEKDAY_GAP_CAVEAT
        or value["providerCalls"] != "blocked"
        or value["accountData"] != "absent"
        or value["execution"] != "blocked"
        or value["candidateIntent"] != "skip"
        or value["claimBoundary"] != "sampling-coverage-only-no-financial-or-session-completeness-claim"
    ):
        raise ValueError("sampling quality is invalid")

    observation_count = value["observationCount"]
    interval_count = value["intervalCount"]
    first_date = value["firstDate"]
    last_date = value["lastDate"]
    if observation_count == 0:
        if first_date is not None or last_date is not None or value["calendarSpanDays"] != 0:
            raise ValueError("sampling quality is invalid")
    else:
        try:
            parsed_first = date.fromisoformat(first_date)
            parsed_last = date.fromisoformat(last_date)
        except (TypeError, ValueError) as exc:
            raise ValueError("sampling quality is invalid") from exc
        if (
            parsed_first.isoformat() != first_date
            or parsed_last.isoformat() != last_date
            or parsed_first > parsed_last
            or (parsed_last - parsed_first).days != value["calendarSpanDays"]
        ):
            raise ValueError("sampling quality is invalid")
    if interval_count == 0:
        if (
            value["potentialMissingWeekdayCount"] != 0
            or value["intervalsOverThreeCalendarDays"] != 0
            or value["maximumCalendarGapDays"] != 0
        ):
            raise ValueError("sampling quality is invalid")
    elif not 1 <= value["maximumCalendarGapDays"] <= value["calendarSpanDays"]:
        raise ValueError("sampling quality is invalid")

    if observation_count < 2:
        expected_state = "insufficient-history"
    elif value["potentialMissingWeekdayCount"] and value["observedWeekendCount"]:
        expected_state = "mixed-irregular-sampling"
    elif value["potentialMissingWeekdayCount"]:
        expected_state = "potential-weekday-gaps"
    elif value["observedWeekendCount"]:
        expected_state = "non-weekday-observations"
    else:
        expected_state = "weekday-grid-covered"
    if value["state"] != expected_state:
        raise ValueError("sampling quality is invalid")

    return {key: value[key] for key in SAMPLING_QUALITY_KEYS}


def sampling_quality_warning(value: Any) -> str | None:
    quality = validated_sampling_quality(value)
    if not quality["observedWeekendCount"] and not quality["potentialMissingWeekdayCount"]:
        return None
    return (
        f"sampling quality state {quality['state']} requires exchange-calendar review; "
        "potential weekday gaps are not proof of missing market sessions"
    )
