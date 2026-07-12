from __future__ import annotations

import math
from typing import Any, Iterable

from money_maker_3000.contracts import (
    SIMULATION_STRATEGY_PARAMETER_SCHEMAS,
    safe_strategy_parameters_for_output,
    validate_strategy_parameters,
)
from money_maker_3000.market_history import Bar

DIAGNOSTICS_VERSION = "strategy-history-diagnostics.v1"


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
    latest_close = window[-1].close
    decline_pct = round(((latest_close - peak_close) / peak_close) * 100, 6)
    return {
        **result,
        "state": "trigger-observed" if decline_pct <= -trigger_pct else "no-trigger-observed",
        "metrics": {
            "lookbackBars": lookback,
            "latestClose": latest_close,
            "rollingPeakClose": peak_close,
            "declineFromRollingPeakPct": decline_pct,
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
    result["parameterState"] = "valid" if validation.ok else "invalid-defaulted"
    return result
