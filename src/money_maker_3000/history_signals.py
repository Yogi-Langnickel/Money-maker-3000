from __future__ import annotations

import math
from typing import Any, Iterable

from money_maker_3000.contracts import (
    SIMULATION_STRATEGY_PARAMETER_SCHEMAS,
    safe_strategy_parameters_for_output,
    validate_strategy_parameters,
)
from money_maker_3000.market_history import Bar

DIAGNOSTICS_VERSION = "strategy-history-diagnostics.v3"
WALK_FORWARD_VERSION = "strategy-history-walk-forward.v2"
MAX_WALK_FORWARD_FOLDS = 5


def _parameter(strategy_id: str, supplied: dict[str, Any], name: str) -> Any:
    if name in supplied:
        return supplied[name]
    return SIMULATION_STRATEGY_PARAMETER_SCHEMAS[strategy_id][name]["default"]


def _base(strategy_id: str, bars: list[Bar]) -> dict[str, Any]:
    return {
        "dtoVersion": DIAGNOSTICS_VERSION,
        "strategyId": strategy_id,
        "barCount": len(bars),
        "latestDate": (
            bars[-1].date
            if bars and isinstance(bars[-1], Bar) and isinstance(bars[-1].date, str)
            else None
        ),
        "providerCalls": "blocked",
        "accountData": "absent",
        "executionRoutes": "absent",
        "candidateIntent": "skip",
        "performanceClaims": "historical-pattern-diagnostics-only-no-pnl-or-profitability-claim",
    }


def _valid_history(bars: list[Bar]) -> bool:
    if not bars:
        return False
    symbol = bars[0].symbol
    previous_date = ""
    for bar in bars:
        if (
            not isinstance(bar, Bar)
            or bar.symbol != symbol
            or not isinstance(bar.date, str)
            or bar.date <= previous_date
            or not math.isfinite(bar.close)
            or bar.close <= 0
        ):
            return False
        previous_date = bar.date
    return True


def _volatility_band(bars: list[Bar], parameters: dict[str, Any]) -> dict[str, Any]:
    lookback = int(_parameter("volatility-band-accumulator", parameters, "lookbackDays"))
    trigger_pct = float(_parameter("volatility-band-accumulator", parameters, "dropTriggerPct"))
    result = _base("volatility-band-accumulator", bars)
    result["requiredBarCount"] = lookback
    if len(bars) < lookback:
        return {**result, "state": "insufficient-history", "metrics": None}

    window = bars[-lookback:]
    peak_close = max(bar.close for bar in window)
    window_low_close = min(bar.close for bar in window)
    latest_close = window[-1].close
    decline_pct = round(((latest_close - peak_close) / peak_close) * 100, 6)
    running_peak = window[0].close
    maximum_observed_decline_pct = 0.0
    for bar in window:
        running_peak = max(running_peak, bar.close)
        observed_decline_pct = ((bar.close - running_peak) / running_peak) * 100
        maximum_observed_decline_pct = min(maximum_observed_decline_pct, observed_decline_pct)
    maximum_observed_decline_pct = round(maximum_observed_decline_pct, 6)
    if decline_pct <= -trigger_pct:
        state = "trigger-observed"
    elif maximum_observed_decline_pct <= -trigger_pct:
        state = "recovery-observed"
    else:
        state = "no-trigger-observed"
    return {
        **result,
        "state": state,
        "metrics": {
            "lookbackBars": lookback,
            "latestClose": latest_close,
            "rollingPeakClose": peak_close,
            "windowLowClose": window_low_close,
            "declineFromRollingPeakPct": decline_pct,
            "maximumObservedDeclinePct": maximum_observed_decline_pct,
            "dropTriggerPct": trigger_pct,
        },
    }


def _mean_close(bars: list[Bar]) -> float:
    return sum(bar.close for bar in bars) / len(bars)


def _slow_trend(bars: list[Bar], parameters: dict[str, Any]) -> dict[str, Any]:
    short_lookback = int(_parameter("slow-trend-allocation", parameters, "shortLookbackDays"))
    long_lookback = int(_parameter("slow-trend-allocation", parameters, "longLookbackDays"))
    confirmation_bars = int(_parameter("slow-trend-allocation", parameters, "confirmationBars"))
    required = long_lookback + confirmation_bars - 1
    result = _base("slow-trend-allocation", bars)
    result["requiredBarCount"] = required
    if len(bars) < required:
        return {**result, "state": "insufficient-history", "metrics": None}

    confirmations = []
    for offset in range(confirmation_bars - 1, -1, -1):
        endpoint = len(bars) - offset
        short_average = _mean_close(bars[endpoint - short_lookback:endpoint])
        long_average = _mean_close(bars[endpoint - long_lookback:endpoint])
        confirmations.append(short_average > long_average)

    current_short = _mean_close(bars[-short_lookback:])
    current_long = _mean_close(bars[-long_lookback:])
    confirmation_matches = sum(confirmations)
    return {
        **result,
        "state": "trend-confirmed" if confirmation_matches == confirmation_bars else "trend-not-confirmed",
        "metrics": {
            "shortLookbackBars": short_lookback,
            "longLookbackBars": long_lookback,
            "confirmationBars": confirmation_bars,
            "confirmationMatches": confirmation_matches,
            "shortAverageClose": round(current_short, 6),
            "longAverageClose": round(current_long, 6),
        },
    }


def _walk_forward(
    bars: list[Bar],
    *,
    strategy_id: str,
    parameters: dict[str, Any],
    required_bar_count: int,
) -> dict[str, Any]:
    base = {
        "dtoVersion": WALK_FORWARD_VERSION,
        "providerCalls": "blocked",
        "accountData": "absent",
        "executionRoutes": "absent",
        "candidateIntent": "skip",
        "performanceClaims": "historical-state-coverage-only-no-pnl-or-profitability-claim",
    }

    def unavailable(state: str) -> dict[str, Any]:
        return {
            **base,
            "state": state,
            "eligibleObservationCount": 0,
            "firstObservationDate": None,
            "lastObservationDate": None,
            "stateCounts": {},
            "transitionCount": 0,
            "foldCount": 0,
            "folds": [],
        }

    if strategy_id not in {"volatility-band-accumulator", "slow-trend-allocation"}:
        return unavailable("not-applicable")
    if len(bars) < required_bar_count:
        return unavailable("insufficient-history")

    evaluator = _volatility_band if strategy_id == "volatility-band-accumulator" else _slow_trend
    states: list[str] = []
    observation_dates: list[str] = []
    first_observation_date = bars[required_bar_count - 1].date
    for endpoint in range(required_bar_count, len(bars) + 1):
        bounded_window = bars[endpoint - required_bar_count:endpoint]
        states.append(evaluator(bounded_window, parameters)["state"])
        observation_dates.append(bounded_window[-1].date)

    state_counts: dict[str, int] = {}
    for state in states:
        state_counts[state] = state_counts.get(state, 0) + 1
    transition_count = sum(previous != current for previous, current in zip(states, states[1:]))

    fold_count = min(MAX_WALK_FORWARD_FOLDS, len(states))
    base_fold_size, extra_observations = divmod(len(states), fold_count)
    folds: list[dict[str, Any]] = []
    start = 0
    for fold_index in range(fold_count):
        fold_size = base_fold_size + (1 if fold_index < extra_observations else 0)
        end = start + fold_size
        fold_states = states[start:end]
        fold_state_counts: dict[str, int] = {}
        for state in fold_states:
            fold_state_counts[state] = fold_state_counts.get(state, 0) + 1
        folds.append(
            {
                "foldIndex": fold_index,
                "observationCount": fold_size,
                "firstObservationDate": observation_dates[start],
                "lastObservationDate": observation_dates[end - 1],
                "stateCounts": dict(sorted(fold_state_counts.items())),
                "transitionCount": sum(
                    previous != current for previous, current in zip(fold_states, fold_states[1:])
                ),
            }
        )
        start = end
    return {
        **base,
        "state": "available",
        "eligibleObservationCount": len(states),
        "firstObservationDate": first_observation_date,
        "lastObservationDate": bars[-1].date,
        "stateCounts": dict(sorted(state_counts.items())),
        "transitionCount": transition_count,
        "foldCount": fold_count,
        "folds": folds,
    }


def build_strategy_history_diagnostics(
    bars: Iterable[Bar],
    *,
    strategy_id: str,
    strategy_parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    collected = list(bars)
    validation = validate_strategy_parameters(strategy_id, strategy_parameters)
    parameters = safe_strategy_parameters_for_output(strategy_id, strategy_parameters)
    if not _valid_history(collected):
        result = {
            **_base(strategy_id, collected),
            "requiredBarCount": 1,
            "state": "invalid-history",
            "metrics": None,
        }
    elif strategy_id == "volatility-band-accumulator":
        result = _volatility_band(collected, parameters)
    elif strategy_id == "slow-trend-allocation":
        result = _slow_trend(collected, parameters)
    else:
        result = {
            **_base(strategy_id, collected),
            "requiredBarCount": 0,
            "state": "not-applicable",
            "metrics": None,
        }
    if result["state"] == "invalid-history":
        walk_forward = {
            **_walk_forward([], strategy_id=strategy_id, parameters=parameters, required_bar_count=1),
            "state": "invalid-history",
        }
    else:
        walk_forward = _walk_forward(
            collected,
            strategy_id=strategy_id,
            parameters=parameters,
            required_bar_count=result["requiredBarCount"],
        )
    result["walkForward"] = walk_forward
    result["parameterState"] = "valid" if validation.ok else "invalid-defaulted"
    return result
