from __future__ import annotations

import platform
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from money_maker_3000.backtest import DEFAULT_MAX_FIXTURE_ROWS, build_historical_fixture_backtest
from money_maker_3000.contracts import (
    DEFAULT_ALLOCATION_POLICY,
    DEFAULT_BUDGET_POLICY,
    SIMULATION_DISABLED_RUN_MODES,
    build_allocation_policy,
    default_simulation_config_for_strategy,
    safe_positive_usd_for_output,
    utc_iso,
    validate_allocation_policy,
    validate_run_mode,
    validate_simulation_config,
    validate_strategy_parameters,
)
from money_maker_3000.engine import CONFIG_VERSION
from money_maker_3000.market_history import PARSER_VERSION, iter_market_history_bars, sha256_file
from money_maker_3000.providers import build_provider_metadata_snapshot
from money_maker_3000.strategies import validate_strategy_registry
from money_maker_3000.sampling_quality import sampling_quality_warning


@dataclass(frozen=True)
class FixtureReadinessSpec:
    symbol: str
    path: Path
    market: str = "US_EQUITIES"
    instrument_class: str = "ETF"
    strategy_id: str = "dca-cash-reserve"
    budget_usd: float = 1000.0
    max_fixture_rows: int = DEFAULT_MAX_FIXTURE_ROWS
    strategy_parameters: dict[str, Any] | None = None


def build_backtest_readiness_report(
    *,
    fixture_specs: Iterable[FixtureReadinessSpec],
    started_at: datetime,
    allocation_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    allocation = allocation_policy or DEFAULT_ALLOCATION_POLICY
    gates = [
        _gate("strategy-registry", validate_strategy_registry().to_dict()),
        _gate("allocation-policy", validate_allocation_policy(allocation).to_dict()),
        _run_mode_gate(),
        _provider_boundary_gate(),
    ]
    fixture_diagnostics: list[dict[str, Any]] = []
    symbols: set[str] = set()
    duplicate_symbols: set[str] = set()

    for spec in fixture_specs:
        symbol = spec.symbol.strip().upper()
        if symbol in symbols:
            duplicate_symbols.add(symbol)
        symbols.add(symbol)
        diagnostic = _fixture_readiness_diagnostic(spec, started_at=started_at, allocation_policy=allocation)
        fixture_diagnostics.append(diagnostic)

    fixture_warnings = [
        f"{diagnostic['symbol']}: {warning}"
        for diagnostic in fixture_diagnostics
        for warning in diagnostic["warnings"]
    ]
    if not fixture_diagnostics:
        gates.append(
            {
                "name": "offline-fixtures",
                "ok": False,
                "errors": ["at least one offline fixture is required"],
                "warnings": [],
            }
        )
    elif duplicate_symbols:
        gates.append(
            {
                "name": "offline-fixtures",
                "ok": False,
                "errors": [f"duplicate fixture symbols: {', '.join(sorted(duplicate_symbols))}"],
                "warnings": fixture_warnings,
            }
        )
    else:
        fixture_errors = [
            f"{diagnostic['symbol']}: {'; '.join(diagnostic['errors'])}"
            for diagnostic in fixture_diagnostics
            if not diagnostic["ok"]
        ]
        gates.append(
            {
                "name": "offline-fixtures",
                "ok": not fixture_errors,
                "errors": fixture_errors,
                "warnings": fixture_warnings,
            }
        )

    ready = all(gate["ok"] for gate in gates)
    return {
        "dtoVersion": "backtest-readiness.v1",
        "mode": "backtest-readiness",
        "ready": ready,
        "readinessScope": "offline-backtest-only",
        "providerCalls": "blocked",
        "executionRoutes": "absent",
        "demoExecution": "blocked",
        "liveExecution": "blocked",
        "accountData": "absent",
        "startedAt": utc_iso(started_at),
        "metadata": {
            "configVersion": CONFIG_VERSION,
            "parserVersion": PARSER_VERSION,
            "pythonVersion": platform.python_version(),
            "fixtureCount": len(fixture_diagnostics),
            "symbols": sorted(symbols),
            "performanceClaims": "diagnostics-only-no-return-or-execution-quality-metrics",
        },
        "gates": gates,
        "fixtureDiagnostics": fixture_diagnostics,
        "nextSafeCommands": _next_safe_commands(fixture_diagnostics),
    }


def _gate(name: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "ok": bool(result.get("ok")),
        "errors": list(result.get("errors", [])),
        "warnings": list(result.get("warnings", [])),
    }


def _run_mode_gate() -> dict[str, Any]:
    backtest_result = validate_run_mode("backtest")
    disabled_errors = []
    for mode in SIMULATION_DISABLED_RUN_MODES:
        result = validate_run_mode(mode)
        if result.ok:
            disabled_errors.append(f"{mode} mode must stay disabled")
    return {
        "name": "run-mode-policy",
        "ok": backtest_result.ok and not disabled_errors,
        "errors": list(backtest_result.errors) + disabled_errors,
        "warnings": [],
    }


def _provider_boundary_gate() -> dict[str, Any]:
    snapshot = build_provider_metadata_snapshot()
    errors = []
    if snapshot.get("providerCalls") != "blocked":
        errors.append("provider calls must be blocked")
    if snapshot.get("executionRoutes") != "absent":
        errors.append("execution routes must be absent")
    if snapshot.get("credentials") != "not-loaded":
        errors.append("credentials must not be loaded")
    if snapshot.get("accountData") != "absent":
        errors.append("account data must be absent")
    validation = snapshot.get("validation", {})
    if not validation.get("ok"):
        errors.append("provider metadata validation failed")
    return {
        "name": "provider-boundary",
        "ok": not errors,
        "errors": errors,
        "warnings": [],
    }


def _fixture_readiness_diagnostic(
    spec: FixtureReadinessSpec,
    *,
    started_at: datetime,
    allocation_policy: dict[str, Any],
) -> dict[str, Any]:
    symbol = spec.symbol.strip().upper()
    selected_instrument = {
        "symbol": symbol,
        "market": spec.market,
        "instrumentClass": spec.instrument_class,
    }
    errors: list[str] = []
    parameter_result = validate_strategy_parameters(spec.strategy_id, spec.strategy_parameters)
    if not parameter_result.ok:
        errors.extend(parameter_result.errors)
    config = default_simulation_config_for_strategy(spec.strategy_id)
    config.update(
        {
            "budgetUsd": spec.budget_usd,
            "selectedInstrument": selected_instrument,
        }
    )
    if spec.strategy_parameters is not None:
        config["strategyParameters"] = spec.strategy_parameters
    config_result = validate_simulation_config(config, allocation_policy=allocation_policy)
    if not config_result.ok:
        errors.extend(config_result.errors)

    if not spec.path.exists():
        errors.append("offline fixture file does not exist")
    elif not spec.path.is_file():
        errors.append("offline fixture path must be a file")

    if errors:
        return _fixture_error(symbol, spec, errors)

    try:
        input_sha256 = sha256_file(spec.path)
        with spec.path.open("r", encoding="utf-8", newline="") as source:
            report = build_historical_fixture_backtest(
                bars=iter_market_history_bars(source, selected_symbol=symbol),
                strategy_id=spec.strategy_id,
                selected_instrument=selected_instrument,
                strategy_parameters=spec.strategy_parameters,
                budget_usd=spec.budget_usd,
                allocation_policy=allocation_policy,
                started_at=started_at,
                input_sha256=input_sha256,
                max_fixture_rows=spec.max_fixture_rows,
            )
    except Exception as exc:
        return _fixture_error(symbol, spec, [str(exc)])

    sampling_quality = report["samplingQuality"]
    sampling_warning = sampling_quality_warning(str(sampling_quality["state"]))
    return {
        "symbol": symbol,
        "ok": True,
        "path": str(spec.path),
        "strategyId": report["metadata"]["strategyId"],
        "market": spec.market,
        "instrumentClass": spec.instrument_class,
        "budgetUsd": safe_positive_usd_for_output(spec.budget_usd, DEFAULT_BUDGET_POLICY["baseBudgetUsd"]),
        "inputSha256": report["metadata"]["inputSha256"],
        "rowCount": report["metadata"]["rowCount"],
        "firstDate": report["metadata"]["firstDate"],
        "lastDate": report["metadata"]["lastDate"],
        "maxFixtureRows": report["metadata"]["maxFixtureRows"],
        "providerCalls": report["providerCalls"],
        "executionRoutes": report["executionRoutes"],
        "accountData": report["accountData"],
        "summary": {
            "eventCount": report["summary"]["eventCount"],
            "blockedCount": report["summary"]["blockedCount"],
            "vetoHistogram": report["summary"]["vetoHistogram"],
            "performanceClaims": report["summary"]["performanceClaims"],
        },
        "periodDiagnostics": {
            "dtoVersion": report["periodDiagnostics"]["dtoVersion"],
            "periods": [period["period"] for period in report["periodDiagnostics"]["periods"]],
            "providerCalls": report["periodDiagnostics"]["providerCalls"],
            "accountData": report["periodDiagnostics"]["accountData"],
            "execution": report["periodDiagnostics"]["execution"],
        },
        "samplingQuality": {
            "dtoVersion": sampling_quality["dtoVersion"],
            "state": sampling_quality["state"],
            "observationCount": sampling_quality["observationCount"],
            "intervalCount": sampling_quality["intervalCount"],
            "calendarSpanDays": sampling_quality["calendarSpanDays"],
            "observedWeekdayCount": sampling_quality["observedWeekdayCount"],
            "observedWeekendCount": sampling_quality["observedWeekendCount"],
            "potentialMissingWeekdayCount": sampling_quality["potentialMissingWeekdayCount"],
            "intervalsOverThreeCalendarDays": sampling_quality["intervalsOverThreeCalendarDays"],
            "maximumCalendarGapDays": sampling_quality["maximumCalendarGapDays"],
            "calendarBasis": sampling_quality["calendarBasis"],
            "weekdayGapCaveat": sampling_quality["weekdayGapCaveat"],
            "providerCalls": sampling_quality["providerCalls"],
            "accountData": sampling_quality["accountData"],
            "execution": sampling_quality["execution"],
            "candidateIntent": sampling_quality["candidateIntent"],
            "claimBoundary": sampling_quality["claimBoundary"],
        },
        "strategyHistoryDiagnostics": {
            "dtoVersion": report["strategyHistoryDiagnostics"]["dtoVersion"],
            "strategyId": report["strategyHistoryDiagnostics"]["strategyId"],
            "state": report["strategyHistoryDiagnostics"]["state"],
            "parameterState": report["strategyHistoryDiagnostics"]["parameterState"],
            "requiredBarCount": report["strategyHistoryDiagnostics"]["requiredBarCount"],
            "providerCalls": report["strategyHistoryDiagnostics"]["providerCalls"],
            "accountData": report["strategyHistoryDiagnostics"]["accountData"],
            "executionRoutes": report["strategyHistoryDiagnostics"]["executionRoutes"],
            "candidateIntent": report["strategyHistoryDiagnostics"]["candidateIntent"],
            "walkForward": {
                "dtoVersion": report["strategyHistoryDiagnostics"]["walkForward"]["dtoVersion"],
                "state": report["strategyHistoryDiagnostics"]["walkForward"]["state"],
                "eligibleObservationCount": report["strategyHistoryDiagnostics"]["walkForward"][
                    "eligibleObservationCount"
                ],
                "transitionCount": report["strategyHistoryDiagnostics"]["walkForward"]["transitionCount"],
                "foldCount": report["strategyHistoryDiagnostics"]["walkForward"]["foldCount"],
                "folds": [
                    {
                        "foldIndex": fold["foldIndex"],
                        "observationCount": fold["observationCount"],
                        "stateCounts": fold["stateCounts"],
                        "transitionCount": fold["transitionCount"],
                    }
                    for fold in report["strategyHistoryDiagnostics"]["walkForward"]["folds"]
                ],
                "providerCalls": report["strategyHistoryDiagnostics"]["walkForward"]["providerCalls"],
                "accountData": report["strategyHistoryDiagnostics"]["walkForward"]["accountData"],
                "executionRoutes": report["strategyHistoryDiagnostics"]["walkForward"]["executionRoutes"],
                "candidateIntent": report["strategyHistoryDiagnostics"]["walkForward"]["candidateIntent"],
            },
        },
        "errors": [],
        "warnings": [sampling_warning] if sampling_warning else [],
    }


def _fixture_error(symbol: str, spec: FixtureReadinessSpec, errors: list[str]) -> dict[str, Any]:
    return {
        "symbol": symbol or "UNKNOWN",
        "ok": False,
        "path": str(spec.path),
        "strategyId": spec.strategy_id,
        "market": spec.market,
        "instrumentClass": spec.instrument_class,
        "budgetUsd": safe_positive_usd_for_output(spec.budget_usd, DEFAULT_BUDGET_POLICY["baseBudgetUsd"]),
        "providerCalls": "blocked",
        "executionRoutes": "absent",
        "accountData": "absent",
        "errors": errors,
        "warnings": [],
    }


def _next_safe_commands(fixture_diagnostics: list[dict[str, Any]]) -> list[str]:
    commands = []
    for diagnostic in fixture_diagnostics:
        if diagnostic["ok"]:
            commands.append(
                "PYTHONPATH=src python3.13 -m money_maker_3000.cli backtest "
                f"--history-csv {diagnostic['path']} "
                f"--strategy {diagnostic['strategyId']} "
                f"--symbol {diagnostic['symbol']} "
                f"--market {diagnostic['market']} "
                f"--instrument-class {diagnostic['instrumentClass']}"
            )
    return commands


def build_allocation_policy_for_readiness(
    *,
    bot_allocation_usd: float,
    reserved_usd: float,
    max_order_usd: float,
    provider_demo_balance_usd: float | None = None,
) -> dict[str, Any]:
    return build_allocation_policy(
        bot_allocation_usd=bot_allocation_usd,
        reserved_usd=reserved_usd,
        max_order_usd=max_order_usd,
        provider_demo_balance_usd=provider_demo_balance_usd,
    )
