from __future__ import annotations

import math
from datetime import date
from typing import Any, Iterable

from money_maker_3000.contracts import SYMBOL_PATTERN

DTO_VERSION = "rebalance-history-diagnostics.v1"


def _base(state: str) -> dict[str, Any]:
    return {
        "dtoVersion": DTO_VERSION,
        "state": state,
        "candidateIntent": "skip",
        "providerCalls": "blocked",
        "accountData": "absent",
        "portfolioHoldings": "absent",
        "executionRoutes": "absent",
        "performanceClaims": "historical-relative-weight-drift-only-no-pnl-or-profitability-claim",
    }


def _max_period(report: dict[str, Any]) -> dict[str, Any] | None:
    periods = report.get("periodDiagnostics", {}).get("periods")
    if not isinstance(periods, list):
        return None
    return next((period for period in periods if isinstance(period, dict) and period.get("period") == "max"), None)


def _threshold_parameters(report: dict[str, Any]) -> dict[str, Any] | None:
    runs = report.get("runs")
    if not isinstance(runs, list) or not runs:
        return None
    diagnostics = runs[0].get("intentDiagnostics") if isinstance(runs[0], dict) else None
    if not isinstance(diagnostics, dict) or diagnostics.get("strategyId") != "threshold-rebalance":
        return None
    parameters = diagnostics.get("strategyParameters")
    return parameters if isinstance(parameters, dict) else None


def build_rebalance_history_diagnostics(reports: Iterable[dict[str, Any]]) -> dict[str, Any]:
    collected = list(reports)
    if not collected or any(report.get("metadata", {}).get("strategyId") != "threshold-rebalance" for report in collected):
        return {**_base("not-applicable"), "metrics": None}

    first_parameters = _threshold_parameters(collected[0])
    if first_parameters is None:
        return {**_base("invalid-input"), "metrics": None}
    target_weights = first_parameters.get("targetWeights")
    threshold_pct = first_parameters.get("rebalanceThresholdPct")
    if (
        not isinstance(target_weights, dict)
        or not 1 <= len(target_weights) <= 8
        or not isinstance(threshold_pct, (int, float))
        or isinstance(threshold_pct, bool)
        or not math.isfinite(float(threshold_pct))
        or float(threshold_pct) <= 0
        or float(threshold_pct) > 20
    ):
        return {**_base("invalid-input"), "metrics": None}

    normalized_targets: dict[str, float] = {}
    for symbol, weight in target_weights.items():
        if (
            not isinstance(symbol, str)
            or not SYMBOL_PATTERN.fullmatch(symbol)
            or not isinstance(weight, (int, float))
            or isinstance(weight, bool)
            or not math.isfinite(float(weight))
            or float(weight) <= 0
        ):
            return {**_base("invalid-input"), "metrics": None}
        normalized_targets[symbol] = float(weight)
    if not math.isclose(sum(normalized_targets.values()), 1.0, rel_tol=0, abs_tol=1e-9):
        return {**_base("invalid-input"), "metrics": None}

    report_by_symbol: dict[str, dict[str, Any]] = {}
    for report in collected:
        parameters = _threshold_parameters(report)
        symbol = report.get("history", {}).get("symbol")
        if (
            parameters != first_parameters
            or not isinstance(symbol, str)
            or not SYMBOL_PATTERN.fullmatch(symbol)
            or symbol in report_by_symbol
        ):
            return {**_base("invalid-input"), "metrics": None}
        report_by_symbol[symbol] = report
    if set(report_by_symbol) != set(normalized_targets):
        return {
            **_base("coverage-mismatch"),
            "metrics": {
                "targetSymbols": sorted(normalized_targets),
                "availableSymbols": sorted(report_by_symbol),
            },
        }

    relative_values: dict[str, float] = {}
    coverage: dict[str, dict[str, Any]] = {}
    common_window: tuple[str, str] | None = None
    for symbol in sorted(normalized_targets):
        target_weight = normalized_targets[symbol]
        report = report_by_symbol[symbol]
        if (
            report.get("providerCalls") != "blocked"
            or report.get("executionRoutes") != "absent"
            or report.get("accountData") != "absent"
        ):
            return {**_base("invalid-input"), "metrics": None}
        period = _max_period(report)
        period_diagnostics = report.get("periodDiagnostics", {})
        if (
            period is None
            or period_diagnostics.get("providerCalls") != "blocked"
            or period_diagnostics.get("accountData") != "absent"
            or period_diagnostics.get("execution") != "blocked"
        ):
            return {**_base("invalid-input"), "metrics": None}
        start_close = period.get("startClose")
        end_close = period.get("endClose")
        start_date = period.get("startDate")
        end_date = period.get("endDate")
        bar_count = period.get("barCount")
        if (
            not isinstance(start_close, (int, float))
            or isinstance(start_close, bool)
            or not math.isfinite(float(start_close))
            or float(start_close) <= 0
            or not isinstance(end_close, (int, float))
            or isinstance(end_close, bool)
            or not math.isfinite(float(end_close))
            or float(end_close) <= 0
            or not isinstance(start_date, str)
            or not isinstance(end_date, str)
            or not isinstance(bar_count, int)
            or isinstance(bar_count, bool)
            or bar_count < 2
            or period.get("coverageState") != "available"
            or period.get("performanceClaims") != "market-history-change-only"
        ):
            return {**_base("invalid-input"), "metrics": None}
        try:
            parsed_start = date.fromisoformat(start_date)
            parsed_end = date.fromisoformat(end_date)
        except ValueError:
            return {**_base("invalid-input"), "metrics": None}
        if parsed_start.isoformat() != start_date or parsed_end.isoformat() != end_date or parsed_start > parsed_end:
            return {**_base("invalid-input"), "metrics": None}
        window = (start_date, end_date)
        if common_window is None:
            common_window = window
        elif common_window != window:
            return {**_base("invalid-input"), "metrics": None}
        relative_values[symbol] = target_weight * (float(end_close) / float(start_close))
        coverage[symbol] = {
            "startDate": start_date,
            "endDate": end_date,
            "barCount": bar_count,
        }

    relative_total = sum(relative_values.values())
    if not math.isfinite(relative_total) or relative_total <= 0:
        return {**_base("invalid-input"), "metrics": None}

    weights = []
    max_absolute_drift = 0.0
    for symbol in sorted(normalized_targets):
        target_weight = normalized_targets[symbol]
        final_weight = relative_values[symbol] / relative_total
        drift_points = (final_weight - target_weight) * 100
        max_absolute_drift = max(max_absolute_drift, abs(drift_points))
        weights.append(
            {
                "symbol": symbol,
                "targetWeight": round(target_weight, 8),
                "normalizedHistoricalWeight": round(final_weight, 8),
                "driftPercentagePoints": round(drift_points, 6),
                "coverage": coverage[symbol],
            }
        )

    max_absolute_drift = round(max_absolute_drift, 6)
    return {
        **_base("available"),
        "metrics": {
            "rebalanceThresholdPct": float(threshold_pct),
            "maxAbsoluteDriftPercentagePoints": max_absolute_drift,
            "thresholdState": (
                "historical-drift-exceeded"
                if max_absolute_drift >= float(threshold_pct)
                else "within-historical-threshold"
            ),
            "weights": weights,
        },
    }
