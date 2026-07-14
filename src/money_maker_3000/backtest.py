from __future__ import annotations

import math
import platform
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any, Iterable, Iterator

from money_maker_3000.contracts import (
    DEFAULT_ALLOCATION_POLICY,
    SIMULATION_STRATEGY_PARAMETER_SCHEMAS,
    build_allocation_policy,
    merge_simulation_config,
    utc_iso,
)
from money_maker_3000.engine import CONFIG_VERSION, build_simulation_run
from money_maker_3000.history_signals import build_strategy_history_diagnostics
from money_maker_3000.market_history import (
    Bar,
    MarketHistoryAccumulator,
    PARSER_VERSION,
    build_period_performance_diagnostics,
)
from money_maker_3000.risk import DEFAULT_RISK_POLICY, RiskInputState, assess_data_freshness
from money_maker_3000.rebalance_history import build_rebalance_history_diagnostics

DEFAULT_SCENARIOS = (
    {"scenarioId": "dca-500", "strategyId": "dca-cash-reserve", "budgetUsd": 500.0},
    {"scenarioId": "dca-1000", "strategyId": "dca-cash-reserve", "budgetUsd": 1000.0},
    {"scenarioId": "rebalance-1500", "strategyId": "threshold-rebalance", "budgetUsd": 1500.0},
    {"scenarioId": "watchlist-1000", "strategyId": "news-aware-watchlist", "budgetUsd": 1000.0},
)
DEFAULT_MAX_FIXTURE_ROWS = 10_000


@dataclass(frozen=True)
class DecisionEvent:
    eventId: str
    bar: dict[str, Any]
    run: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DecisionSummaryAccumulator:
    def __init__(self) -> None:
        self.event_count = 0
        self.skip_count = 0
        self.blocked_count = 0
        self.config_valid_count = 0
        self.config_invalid_count = 0
        self.low_frequency_only_count = 0
        self.veto_histogram: dict[str, int] = {}
        self.warning_histogram: dict[str, int] = {}
        self.config_error_histogram: dict[str, int] = {}
        self.risk_gate_histogram: dict[str, int] = {}

    def _increment(self, histogram: dict[str, int], values: Iterable[str]) -> None:
        for value in values:
            histogram[value] = histogram.get(value, 0) + 1

    def update(self, event: DecisionEvent) -> None:
        run = event.run
        self.event_count += 1
        self.skip_count += 1 if run["decision"] == "skip" else 0
        self.blocked_count += 1 if run["riskResult"] == "blocked" else 0
        self.config_valid_count += 1 if run["configValidation"]["ok"] else 0
        self.config_invalid_count += 1 if not run["configValidation"]["ok"] else 0
        cadence = run["simulationConfig"]["cadence"]
        if (
            cadence["mode"] == "low-frequency-only"
            and cadence["minimumEvaluationIntervalMinutes"] >= run["schedulePolicy"]["minimumEvaluationIntervalMinutes"]
            and cadence["maxDecisionsPerDay"] <= run["schedulePolicy"]["maxDecisionsPerDay"]
        ):
            self.low_frequency_only_count += 1
        self._increment(self.veto_histogram, run["vetoes"])
        self._increment(self.warning_histogram, run["configValidation"].get("warnings", []))
        self._increment(self.config_error_histogram, run["configValidation"].get("errors", []))
        self._increment(self.risk_gate_histogram, [run["riskDecision"]["decision"]])

    def to_dict(self) -> dict[str, Any]:
        return {
            "eventCount": self.event_count,
            "skipCount": self.skip_count,
            "blockedCount": self.blocked_count,
            "configValidCount": self.config_valid_count,
            "configInvalidCount": self.config_invalid_count,
            "lowFrequencyOnlyCount": self.low_frequency_only_count,
            "vetoHistogram": self.veto_histogram,
            "warningHistogram": self.warning_histogram,
            "configErrorHistogram": self.config_error_histogram,
            "riskGateHistogram": self.risk_gate_histogram,
            "performanceClaims": "diagnostics-only-no-return-or-execution-quality-metrics",
        }


def build_intent_diagnostics(run: dict[str, Any], bar: dict[str, Any] | None = None) -> dict[str, Any]:
    config = run["simulationConfig"]
    parameters = config.get("strategyParameters", {})
    strategy_id = run["strategyId"]
    candidate_order_usd = _candidate_order_usd(strategy_id, parameters, config["budgetUsd"])
    reasons = list(run["vetoes"])
    if run["configValidation"].get("warnings"):
        reasons.extend(run["configValidation"]["warnings"])
    return {
        "dtoVersion": "strategy-intent-diagnostics.v1",
        "strategyId": strategy_id,
        "strategyVersion": run["strategyVersion"],
        "selectedInstrument": config["selectedInstrument"],
        "strategyParameters": parameters,
        "bar": bar or {},
        "candidateIntent": "skip",
        "candidateOrderUsd": candidate_order_usd,
        "blockedByRiskGate": run["riskResult"] == "blocked",
        "riskDecision": run["riskDecision"]["decision"],
        "reasons": reasons,
        "providerCalls": "blocked",
        "executionRoute": "absent",
        "accountData": "absent",
        "performanceClaims": "diagnostics-only-no-order-or-profitability-claim",
    }


def _candidate_order_usd(strategy_id: str, parameters: dict[str, Any], budget_usd: float) -> float | None:
    if strategy_id == "news-aware-watchlist":
        return None
    if "fixedOrderUsd" in parameters:
        return round(min(float(parameters["fixedOrderUsd"]), float(budget_usd)), 2)
    if "maxOrderUsd" in parameters and "orderFractionPct" in parameters:
        return round(min(float(parameters["maxOrderUsd"]), float(budget_usd) * float(parameters["orderFractionPct"])), 2)
    if "maxOrderUsd" in parameters:
        return round(min(float(parameters["maxOrderUsd"]), float(budget_usd)), 2)
    return None


def iter_decision_events(
    bars: Iterable[Bar],
    *,
    strategy_id: str = "dca-cash-reserve",
    selected_instrument: dict[str, Any] | None = None,
    strategy_parameters: dict[str, Any] | None = None,
    budget_usd: float = 1000.0,
    allocation_policy: dict[str, Any] | None = None,
    started_at: datetime | None = None,
) -> Iterator[DecisionEvent]:
    started = started_at or datetime.fromisoformat("2026-05-15T00:00:00+00:00")
    started_date = utc_iso(started)[:10]
    for index, bar in enumerate(bars, start=1):
        selected = dict(selected_instrument or {})
        selected["symbol"] = bar.symbol
        config = merge_simulation_config(
            strategy_id,
            {
                "runMode": "backtest",
                "budgetUsd": float(budget_usd),
                "selectedInstrument": selected,
                **({"strategyParameters": strategy_parameters} if strategy_parameters is not None else {}),
            },
        )
        freshness = assess_data_freshness(
            last_date=bar.date,
            started_at_date=started_date,
            max_age_days=DEFAULT_RISK_POLICY["maxDataAgeDays"],
        )
        run = build_simulation_run(
            strategy_id=strategy_id,
            now=started,
            simulation_config=config,
            allocation_policy=allocation_policy or DEFAULT_ALLOCATION_POLICY,
            risk_state=RiskInputState(data_freshness=freshness),
            proposed_order_usd=None,
            run_id_suffix=f"{bar.symbol}-{bar.date}-{index}",
        )
        yield DecisionEvent(eventId=f"decision-{bar.symbol}-{bar.date}-{index}", bar=bar.to_dict(), run=run)


def summarize_decision_events(events: Iterable[DecisionEvent]) -> dict[str, Any]:
    accumulator = DecisionSummaryAccumulator()
    for event in events:
        accumulator.update(event)
    return accumulator.to_dict()


def build_synthetic_backtest(
    *,
    scenarios: Iterable[dict[str, Any]] = DEFAULT_SCENARIOS,
    started_at: datetime | None = None,
    include_ledger_records: bool = False,
) -> dict[str, Any]:
    from money_maker_3000.ledger import build_ledger_record

    started = started_at or datetime.fromisoformat("2026-05-15T00:00:00+00:00")
    run_objects = []
    runs = []
    scenario_summaries = []
    summary_accumulator = DecisionSummaryAccumulator()
    for index, scenario in enumerate(scenarios):
        scenario_id = str(scenario.get("scenarioId", f"synthetic-{index + 1}"))
        allocation = scenario.get("allocationPolicy")
        if allocation is None:
            allocation = build_allocation_policy(
                bot_allocation_usd=max(float(scenario.get("budgetUsd", 1000.0)), 1000.0)
            )
        run = build_simulation_run(
            strategy_id=scenario["strategyId"],
            now=started,
            simulation_config={
                **scenario.get("simulationConfig", {}),
                "budgetUsd": float(scenario.get("budgetUsd", scenario.get("simulationConfig", {}).get("budgetUsd", 1000.0))),
            },
            allocation_policy=allocation,
            risk_state=RiskInputState(data_freshness="fresh"),
            run_id_suffix=f"{scenario_id}-{index + 1}",
        )
        event = DecisionEvent(eventId=f"synthetic-{index + 1}", bar={}, run=run)
        summary_accumulator.update(event)
        run_objects.append(run)
        intent_diagnostics = build_intent_diagnostics(run)
        runs.append(
            {
                "runId": run["runId"],
                "strategyId": run["strategyId"],
                "strategyVersion": run["strategyVersion"],
                "decision": run["decision"],
                "riskResult": run["riskResult"],
                "riskDecision": run["riskDecision"]["decision"],
                "budgetRemainingUsd": run["budget"]["remainingUsd"],
                "vetoes": run["vetoes"],
                "configWarnings": run["configValidation"]["warnings"],
                "intentDiagnostics": intent_diagnostics,
            }
        )
        scenario_summaries.append(
            {
                "scenarioId": scenario.get("scenarioId", f"{run['strategyId']}-{index + 1}"),
                "runId": run["runId"],
                "runMode": run["simulationConfig"]["runMode"],
                "strategyId": run["strategyId"],
                "strategyVersion": run["strategyVersion"],
                "selectedInstrument": run["simulationConfig"]["selectedInstrument"],
                "decision": run["decision"],
                "riskResult": run["riskResult"],
                "riskDecision": run["riskDecision"],
                "requestedBudgetUsd": run["simulationConfig"]["budgetUsd"],
                "allocation": run["allocation"],
                "cadence": {
                    "frequency": run["simulationConfig"]["cadence"]["frequency"],
                    "minimumEvaluationIntervalMinutes": run["simulationConfig"]["cadence"][
                        "minimumEvaluationIntervalMinutes"
                    ],
                    "maxDecisionsPerDay": run["simulationConfig"]["cadence"]["maxDecisionsPerDay"],
                    "lowFrequencyOnly": run["simulationConfig"]["cadence"]["mode"] == "low-frequency-only",
                },
                "config": run["configValidation"],
                "vetoes": run["vetoes"],
                "providerCalls": run["providerMetadata"]["providerCalls"],
                "executionRoute": run["tradeLogEntry"]["executionRoute"],
                "intentDiagnostics": intent_diagnostics,
            }
        )
    return {
        "dtoVersion": "backtest-summary.v1",
        "mode": "synthetic-backtest",
        "environment": "synthetic",
        "providerCalls": "blocked",
        "executionRoutes": "absent",
        "startedAt": utc_iso(started),
        "metadata": {
            "configVersion": CONFIG_VERSION,
            "parserVersion": None,
            "pythonVersion": platform.python_version(),
            "performanceClaims": "diagnostics-only",
        },
        "summary": summary_accumulator.to_dict(),
        "scenarioSummaries": scenario_summaries,
        "runs": runs,
        "ledgerRecords": [build_ledger_record(run=run) for run in run_objects] if include_ledger_records else [],
    }


def build_historical_fixture_backtest(
    *,
    bars: Iterable[Bar],
    strategy_id: str = "dca-cash-reserve",
    selected_instrument: dict[str, Any],
    strategy_parameters: dict[str, Any] | None = None,
    budget_usd: float = 1000.0,
    allocation_policy: dict[str, Any] | None = None,
    started_at: datetime | None = None,
    input_sha256: str | None = None,
    max_fixture_rows: int = DEFAULT_MAX_FIXTURE_ROWS,
) -> dict[str, Any]:
    if max_fixture_rows <= 0:
        raise ValueError("max fixture rows must be positive")
    started = started_at or datetime.fromisoformat("2026-05-15T00:00:00+00:00")
    history = MarketHistoryAccumulator()
    period_bars: list[Bar] = []
    event_summary = DecisionSummaryAccumulator()
    scenario_summaries: list[dict[str, Any]] = []
    runs: list[dict[str, Any]] = []
    for event in iter_decision_events(
        _tee_history(bars, history, period_bars, max_rows=max_fixture_rows),
        strategy_id=strategy_id,
        selected_instrument=selected_instrument,
        strategy_parameters=strategy_parameters,
        budget_usd=budget_usd,
        allocation_policy=allocation_policy,
        started_at=started,
    ):
        event_summary.update(event)
        run = event.run
        intent_diagnostics = build_intent_diagnostics(run, event.bar)
        runs.append(
            {
                "runId": run["runId"],
                "strategyId": run["strategyId"],
                "strategyVersion": run["strategyVersion"],
                "decision": run["decision"],
                "riskResult": run["riskResult"],
                "riskDecision": run["riskDecision"]["decision"],
                "budgetRemainingUsd": run["budget"]["remainingUsd"],
                "vetoes": run["vetoes"],
                "configWarnings": run["configValidation"]["warnings"],
                "intentDiagnostics": intent_diagnostics,
            }
        )
        scenario_summaries.append(
            {
                "scenarioId": f"{strategy_id}-{event.bar['symbol']}-{event.bar['date']}-historical-fixture",
                "runId": run["runId"],
                "runMode": run["simulationConfig"]["runMode"],
                "strategyId": run["strategyId"],
                "strategyVersion": run["strategyVersion"],
                "selectedInstrument": run["simulationConfig"]["selectedInstrument"],
                "decision": run["decision"],
                "riskResult": run["riskResult"],
                "riskDecision": run["riskDecision"],
                "requestedBudgetUsd": run["simulationConfig"]["budgetUsd"],
                "allocation": run["allocation"],
                "config": run["configValidation"],
                "vetoes": run["vetoes"],
                "providerCalls": run["providerMetadata"]["providerCalls"],
                "executionRoute": run["tradeLogEntry"]["executionRoute"],
                "intentDiagnostics": intent_diagnostics,
            }
        )

    history_summary = history.to_summary()
    return {
        "dtoVersion": "backtest-summary.v1",
        "mode": "historical-fixture-backtest",
        "environment": "offline-fixture",
        "providerCalls": "blocked",
        "executionRoutes": "absent",
        "accountData": "absent",
        "startedAt": utc_iso(started),
        "metadata": {
            "strategyId": strategy_id,
            "strategyVersion": scenario_summaries[0]["strategyVersion"] if scenario_summaries else "unknown",
            "configVersion": CONFIG_VERSION,
            "dataSource": history_summary["source"],
            "firstDate": history_summary["firstDate"],
            "lastDate": history_summary["lastDate"],
            "rowCount": history_summary["barCount"],
            "maxFixtureRows": max_fixture_rows,
            "inputSha256": input_sha256,
            "parserVersion": PARSER_VERSION,
            "pythonVersion": platform.python_version(),
            "performanceClaims": "diagnostics-only-no-return-or-execution-quality-metrics",
        },
        "history": history_summary,
        "periodDiagnostics": build_period_performance_diagnostics(period_bars),
        "strategyHistoryDiagnostics": build_strategy_history_diagnostics(
            period_bars,
            strategy_id=strategy_id,
            strategy_parameters=strategy_parameters,
        ),
        "summary": event_summary.to_dict(),
        "scenarioSummaries": scenario_summaries,
        "runs": runs,
        "ledgerRecords": [],
    }


def _canonical_strategy_history_from_report(report: dict[str, Any]) -> dict[str, Any]:
    metadata = report.get("metadata")
    history = report.get("history")
    runs = report.get("runs")
    scenario_summaries = report.get("scenarioSummaries")
    if (
        not isinstance(metadata, dict)
        or not isinstance(history, dict)
        or not isinstance(runs, list)
        or not runs
        or not isinstance(scenario_summaries, list)
        or len(scenario_summaries) != len(runs)
    ):
        raise ValueError("offline fixture batch authoritative history is invalid")
    strategy_id = metadata.get("strategyId")
    if strategy_id not in SIMULATION_STRATEGY_PARAMETER_SCHEMAS:
        raise ValueError("offline fixture batch authoritative history is invalid")

    expected_bar_keys = {"symbol", "date", "open", "high", "low", "close", "volume", "source"}
    bars: list[Bar] = []
    strategy_parameters: dict[str, Any] | None = None
    for run in runs:
        intent = run.get("intentDiagnostics") if isinstance(run, dict) else None
        bar = intent.get("bar") if isinstance(intent, dict) else None
        parameters = intent.get("strategyParameters") if isinstance(intent, dict) else None
        if (
            not isinstance(intent, dict)
            or intent.get("strategyId") != strategy_id
            or not isinstance(bar, dict)
            or set(bar) != expected_bar_keys
            or not isinstance(parameters, dict)
            or not isinstance(bar["symbol"], str)
            or not isinstance(bar["date"], str)
            or not isinstance(bar["source"], str)
            or any(
                isinstance(bar[key], bool)
                or not isinstance(bar[key], (int, float))
                or not math.isfinite(float(bar[key]))
                for key in ("open", "high", "low", "close", "volume")
            )
        ):
            raise ValueError("offline fixture batch authoritative history is invalid")
        if strategy_parameters is None:
            strategy_parameters = parameters
        elif parameters != strategy_parameters:
            raise ValueError("offline fixture batch authoritative history is invalid")
        bars.append(Bar(**bar))

    if (
        len(bars) != metadata.get("rowCount")
        or len(bars) != history.get("barCount")
        or bars[0].symbol != history.get("symbol")
        or bars[0].date != metadata.get("firstDate")
        or bars[-1].date != metadata.get("lastDate")
    ):
        raise ValueError("offline fixture batch authoritative history is invalid")
    canonical = build_strategy_history_diagnostics(
        bars,
        strategy_id=strategy_id,
        strategy_parameters=strategy_parameters,
    )
    if canonical["state"] == "invalid-history":
        raise ValueError("offline fixture batch authoritative history is invalid")
    parameter_error_prefixes = (
        "strategy parameter ",
        "strategy parameters ",
        "unsupported strategy parameters ",
    )
    parameter_states: set[str] = set()
    for summary in scenario_summaries:
        config = summary.get("config") if isinstance(summary, dict) else None
        errors = config.get("errors") if isinstance(config, dict) else None
        if not isinstance(errors, list) or any(not isinstance(error, str) for error in errors):
            raise ValueError("offline fixture batch authoritative history is invalid")
        parameter_states.add(
            "invalid-defaulted"
            if any(error.startswith(parameter_error_prefixes) for error in errors)
            else "valid"
        )
    if len(parameter_states) != 1:
        raise ValueError("offline fixture batch authoritative history is invalid")
    canonical["parameterState"] = parameter_states.pop()
    return canonical


def build_offline_fixture_batch_diagnostics(
    *,
    reports: Iterable[dict[str, Any]],
    started_at: datetime | None = None,
) -> dict[str, Any]:
    started = started_at or datetime.fromisoformat("2026-05-15T00:00:00+00:00")
    report_list = list(reports)
    per_symbol = []
    symbols: set[str] = set()
    total_rows = 0
    total_events = 0
    blocked_events = 0
    veto_histogram: dict[str, int] = {}
    strategy_history_state_histogram: dict[str, int] = {}

    for report in report_list:
        if report.get("providerCalls") != "blocked":
            raise ValueError("offline fixture batch reports must block provider calls")
        if report.get("executionRoutes") != "absent":
            raise ValueError("offline fixture batch reports must not include execution routes")
        metadata = report["metadata"]
        history = report["history"]
        symbol = str(history["symbol"])
        if symbol in symbols:
            raise ValueError(f"duplicate offline fixture symbol: {symbol}")
        symbols.add(symbol)

        row_count = int(metadata["rowCount"])
        event_count = int(report["summary"]["eventCount"])
        blocked_count = int(report["summary"]["blockedCount"])
        canonical_strategy_history = _canonical_strategy_history_from_report(report)
        strategy_history = _validated_strategy_history_diagnostics(
            report.get("strategyHistoryDiagnostics"),
            canonical=canonical_strategy_history,
        )
        total_rows += row_count
        total_events += event_count
        blocked_events += blocked_count
        _merge_histogram(veto_histogram, report["summary"]["vetoHistogram"])
        _merge_histogram(strategy_history_state_histogram, {strategy_history["state"]: 1})
        per_symbol.append(
            {
                "symbol": symbol,
                "mode": report["mode"],
                "environment": report["environment"],
                "providerCalls": "blocked",
                "executionRoutes": "absent",
                "accountData": "absent",
                "inputSha256": metadata["inputSha256"],
                "parserVersion": metadata["parserVersion"],
                "coverage": {
                    "source": metadata["dataSource"],
                    "rowCount": row_count,
                    "firstDate": metadata["firstDate"],
                    "lastDate": metadata["lastDate"],
                },
                "periodDiagnostics": report["periodDiagnostics"],
                "strategyHistoryDiagnostics": strategy_history,
                "summary": report["summary"],
                "performanceClaims": "diagnostics-only-no-return-or-execution-quality-metrics",
            }
        )

    if not per_symbol:
        raise ValueError("offline fixture batch requires at least one fixture report")

    return {
        "dtoVersion": "offline-fixture-batch-diagnostics.v1",
        "mode": "offline-fixture-batch-diagnostics",
        "environment": "offline-fixture",
        "providerCalls": "blocked",
        "executionRoutes": "absent",
        "accountData": "absent",
        "startedAt": utc_iso(started),
        "metadata": {
            "configVersion": CONFIG_VERSION,
            "parserVersion": PARSER_VERSION,
            "pythonVersion": platform.python_version(),
            "fixtureCount": len(per_symbol),
            "symbols": sorted(symbols),
            "performanceClaims": "diagnostics-only-no-return-or-execution-quality-metrics",
        },
        "coverage": {
            "fixtureCount": len(per_symbol),
            "totalRows": total_rows,
            "totalEvents": total_events,
            "blockedEvents": blocked_events,
            "providerCalls": "blocked",
            "execution": "blocked",
        },
        "perSymbolDiagnostics": per_symbol,
        "rebalanceHistoryDiagnostics": build_rebalance_history_diagnostics(report_list),
        "summary": {
            "eventCount": total_events,
            "blockedCount": blocked_events,
            "vetoHistogram": veto_histogram,
            "strategyHistoryStateHistogram": strategy_history_state_histogram,
            "performanceClaims": "diagnostics-only-no-return-or-execution-quality-metrics",
        },
    }


def _tee_history(
    bars: Iterable[Bar],
    accumulator: MarketHistoryAccumulator,
    period_bars: list[Bar],
    *,
    max_rows: int,
) -> Iterator[Bar]:
    for row_count, bar in enumerate(bars, start=1):
        if row_count > max_rows:
            raise ValueError(f"offline fixture row count exceeds maxFixtureRows={max_rows}")
        accumulator.update(bar)
        period_bars.append(bar)
        yield bar


def _merge_histogram(target: dict[str, int], source: dict[str, int]) -> None:
    for key, value in source.items():
        target[key] = target.get(key, 0) + int(value)


def _validated_strategy_history_diagnostics(value: Any, *, canonical: dict[str, Any]) -> dict[str, Any]:
    ordered_keys = (
        "dtoVersion",
        "strategyId",
        "barCount",
        "latestDate",
        "providerCalls",
        "accountData",
        "executionRoutes",
        "candidateIntent",
        "performanceClaims",
        "requiredBarCount",
        "state",
        "metrics",
        "walkForward",
        "parameterState",
    )
    if not isinstance(value, dict) or set(value) != set(ordered_keys):
        raise ValueError("offline fixture batch strategy history diagnostics are invalid")
    if (
        value["dtoVersion"] != "strategy-history-diagnostics.v3"
        or value["providerCalls"] != "blocked"
        or value["accountData"] != "absent"
        or value["executionRoutes"] != "absent"
        or value["candidateIntent"] != "skip"
        or value["performanceClaims"] != "historical-pattern-diagnostics-only-no-pnl-or-profitability-claim"
        or value["strategyId"] not in SIMULATION_STRATEGY_PARAMETER_SCHEMAS
        or value["parameterState"] not in {"valid", "invalid-defaulted"}
        or value["state"] not in {
            "not-applicable",
            "insufficient-history",
            "invalid-history",
            "trigger-observed",
            "recovery-observed",
            "no-trigger-observed",
            "trend-confirmed",
            "trend-not-confirmed",
        }
        or not isinstance(value["barCount"], int)
        or isinstance(value["barCount"], bool)
        or value["barCount"] < 0
        or not isinstance(value["requiredBarCount"], int)
        or isinstance(value["requiredBarCount"], bool)
        or value["requiredBarCount"] < 0
    ):
        raise ValueError("offline fixture batch strategy history diagnostics are invalid")

    latest_date = value["latestDate"]
    if latest_date is not None:
        try:
            parsed_latest_date = date.fromisoformat(latest_date)
        except (TypeError, ValueError) as exc:
            raise ValueError("offline fixture batch strategy history diagnostics are invalid") from exc
        if parsed_latest_date.isoformat() != latest_date:
            raise ValueError("offline fixture batch strategy history diagnostics are invalid")
    if value["barCount"] > 0 and latest_date is None and value["state"] != "invalid-history":
        raise ValueError("offline fixture batch strategy history diagnostics are invalid")

    volatility_states = {"trigger-observed", "recovery-observed", "no-trigger-observed"}
    trend_states = {"trend-confirmed", "trend-not-confirmed"}
    if value["state"] in volatility_states and value["strategyId"] != "volatility-band-accumulator":
        raise ValueError("offline fixture batch strategy history diagnostics are invalid")
    if value["state"] in trend_states and value["strategyId"] != "slow-trend-allocation":
        raise ValueError("offline fixture batch strategy history diagnostics are invalid")

    ordered_metric_keys = (
        "lookbackBars",
        "latestClose",
        "rollingPeakClose",
        "windowLowClose",
        "declineFromRollingPeakPct",
        "maximumObservedDeclinePct",
        "dropTriggerPct",
        "shortLookbackBars",
        "longLookbackBars",
        "confirmationBars",
        "confirmationMatches",
        "shortAverageClose",
        "longAverageClose",
    )
    metrics = value["metrics"]
    metric_states = volatility_states | trend_states
    if (value["state"] in metric_states) != (metrics is not None):
        raise ValueError("offline fixture batch strategy history metrics are invalid")
    if metrics is not None:
        if not isinstance(metrics, dict) or not set(metrics).issubset(set(ordered_metric_keys)):
            raise ValueError("offline fixture batch strategy history metrics are invalid")
        if any(
            isinstance(metric, bool) or not isinstance(metric, (int, float)) or not math.isfinite(float(metric))
            for metric in metrics.values()
        ):
            raise ValueError("offline fixture batch strategy history metrics are invalid")
        volatility_metric_keys = {
            "lookbackBars",
            "latestClose",
            "rollingPeakClose",
            "windowLowClose",
            "declineFromRollingPeakPct",
            "maximumObservedDeclinePct",
            "dropTriggerPct",
        }
        trend_metric_keys = {
            "shortLookbackBars",
            "longLookbackBars",
            "confirmationBars",
            "confirmationMatches",
            "shortAverageClose",
            "longAverageClose",
        }
        if value["state"] in volatility_states and set(metrics) != volatility_metric_keys:
            raise ValueError("offline fixture batch strategy history metrics are invalid")
        if value["state"] in trend_states and set(metrics) != trend_metric_keys:
            raise ValueError("offline fixture batch strategy history metrics are invalid")
        if value["state"] in volatility_states and (
            not isinstance(metrics["lookbackBars"], int)
            or metrics["lookbackBars"] <= 0
            or metrics["latestClose"] <= 0
            or metrics["rollingPeakClose"] < metrics["latestClose"]
            or metrics["windowLowClose"] > metrics["latestClose"]
            or metrics["windowLowClose"] <= 0
            or metrics["declineFromRollingPeakPct"] > 0
            or metrics["maximumObservedDeclinePct"] > metrics["declineFromRollingPeakPct"]
            or metrics["dropTriggerPct"] <= 0
        ):
            raise ValueError("offline fixture batch strategy history metrics are invalid")
        if (
            value["state"] == "trigger-observed"
            and metrics["declineFromRollingPeakPct"] > -metrics["dropTriggerPct"]
        ):
            raise ValueError("offline fixture batch strategy history metrics are invalid")
        if value["state"] == "recovery-observed" and (
            metrics["declineFromRollingPeakPct"] <= -metrics["dropTriggerPct"]
            or metrics["maximumObservedDeclinePct"] > -metrics["dropTriggerPct"]
        ):
            raise ValueError("offline fixture batch strategy history metrics are invalid")
        if value["state"] == "no-trigger-observed" and (
            metrics["declineFromRollingPeakPct"] <= -metrics["dropTriggerPct"]
            or metrics["maximumObservedDeclinePct"] <= -metrics["dropTriggerPct"]
        ):
            raise ValueError("offline fixture batch strategy history metrics are invalid")
        if value["state"] in trend_states and (
            any(
                not isinstance(metrics[key], int)
                for key in ("shortLookbackBars", "longLookbackBars", "confirmationBars", "confirmationMatches")
            )
            or metrics["shortLookbackBars"] <= 0
            or metrics["longLookbackBars"] <= metrics["shortLookbackBars"]
            or metrics["confirmationBars"] <= 0
            or not 0 <= metrics["confirmationMatches"] <= metrics["confirmationBars"]
            or metrics["shortAverageClose"] <= 0
            or metrics["longAverageClose"] <= 0
        ):
            raise ValueError("offline fixture batch strategy history metrics are invalid")
        metrics = {key: metrics[key] for key in ordered_metric_keys if key in metrics}

    walk_forward = _validated_walk_forward_diagnostics(
        value["walkForward"],
        strategy_id=value["strategyId"],
        history_state=value["state"],
        bar_count=value["barCount"],
        required_bar_count=value["requiredBarCount"],
        latest_date=latest_date,
        canonical=canonical["walkForward"],
    )
    validated = {
        key: (
            dict(metrics)
            if key == "metrics" and metrics is not None
            else walk_forward
            if key == "walkForward"
            else value[key]
        )
        for key in ordered_keys
    }
    if validated != canonical:
        raise ValueError("offline fixture batch strategy history diagnostics are invalid")
    return validated


def _validated_walk_forward_diagnostics(
    value: Any,
    *,
    strategy_id: str,
    history_state: str,
    bar_count: int,
    required_bar_count: int,
    latest_date: str | None,
    canonical: dict[str, Any],
) -> dict[str, Any]:
    keys = {
        "dtoVersion",
        "providerCalls",
        "accountData",
        "executionRoutes",
        "candidateIntent",
        "performanceClaims",
        "state",
        "eligibleObservationCount",
        "firstObservationDate",
        "lastObservationDate",
        "stateCounts",
        "transitionCount",
        "foldCount",
        "folds",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("offline fixture batch walk-forward diagnostics are invalid")
    observation_count = value["eligibleObservationCount"]
    transition_count = value["transitionCount"]
    state_counts = value["stateCounts"]
    supported_states = (
        {"trigger-observed", "recovery-observed", "no-trigger-observed"}
        if strategy_id == "volatility-band-accumulator"
        else {"trend-confirmed", "trend-not-confirmed"}
        if strategy_id == "slow-trend-allocation"
        else set()
    )
    if (
        value["dtoVersion"] != "strategy-history-walk-forward.v2"
        or value["providerCalls"] != "blocked"
        or value["accountData"] != "absent"
        or value["executionRoutes"] != "absent"
        or value["candidateIntent"] != "skip"
        or value["performanceClaims"] != "historical-state-coverage-only-no-pnl-or-profitability-claim"
        or value["state"] not in {"available", "insufficient-history", "invalid-history", "not-applicable"}
        or not isinstance(observation_count, int)
        or isinstance(observation_count, bool)
        or observation_count < 0
        or not isinstance(transition_count, int)
        or isinstance(transition_count, bool)
        or transition_count < 0
        or not isinstance(state_counts, dict)
        or any(
            state not in supported_states
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count <= 0
            for state, count in state_counts.items()
        )
        or sum(state_counts.values()) != observation_count
        or transition_count > max(0, observation_count - 1)
    ):
        raise ValueError("offline fixture batch walk-forward diagnostics are invalid")

    expected_state = (
        "invalid-history"
        if history_state == "invalid-history"
        else "not-applicable"
        if not supported_states
        else "insufficient-history"
        if bar_count < required_bar_count
        else "available"
    )
    expected_count = max(0, bar_count - required_bar_count + 1) if expected_state == "available" else 0
    if value["state"] != expected_state or observation_count != expected_count:
        raise ValueError("offline fixture batch walk-forward diagnostics are invalid")
    if expected_state == "available" and (
        history_state not in state_counts
        or transition_count < max(0, len(state_counts) - 1)
    ):
        raise ValueError("offline fixture batch walk-forward diagnostics are invalid")

    first_date = value["firstObservationDate"]
    last_date = value["lastObservationDate"]
    if expected_state != "available":
        if first_date is not None or last_date is not None or state_counts or transition_count:
            raise ValueError("offline fixture batch walk-forward diagnostics are invalid")
    else:
        try:
            parsed_first = date.fromisoformat(first_date)
            parsed_last = date.fromisoformat(last_date)
        except (TypeError, ValueError) as exc:
            raise ValueError("offline fixture batch walk-forward diagnostics are invalid") from exc
        if (
            parsed_first.isoformat() != first_date
            or parsed_last.isoformat() != last_date
            or parsed_first > parsed_last
            or last_date != latest_date
        ):
            raise ValueError("offline fixture batch walk-forward diagnostics are invalid")

    fold_count = value["foldCount"]
    folds = value["folds"]
    if (
        not isinstance(fold_count, int)
        or isinstance(fold_count, bool)
        or fold_count < 0
        or fold_count > 5
        or not isinstance(folds, list)
        or len(folds) != fold_count
    ):
        raise ValueError("offline fixture batch walk-forward diagnostics are invalid")
    expected_fold_count = min(5, observation_count) if expected_state == "available" else 0
    if fold_count != expected_fold_count:
        raise ValueError("offline fixture batch walk-forward diagnostics are invalid")

    aggregate_fold_counts: dict[str, int] = {}
    within_fold_transitions = 0
    previous_fold_last: date | None = None
    if fold_count:
        base_fold_size, extra_observations = divmod(observation_count, fold_count)
        for fold_index, fold in enumerate(folds):
            fold_keys = {
                "foldIndex",
                "observationCount",
                "firstObservationDate",
                "lastObservationDate",
                "stateCounts",
                "transitionCount",
            }
            expected_fold_size = base_fold_size + (1 if fold_index < extra_observations else 0)
            if not isinstance(fold, dict) or set(fold) != fold_keys:
                raise ValueError("offline fixture batch walk-forward diagnostics are invalid")
            fold_observation_count = fold["observationCount"]
            fold_transition_count = fold["transitionCount"]
            fold_state_counts = fold["stateCounts"]
            if (
                not isinstance(fold["foldIndex"], int)
                or isinstance(fold["foldIndex"], bool)
                or fold["foldIndex"] != fold_index
                or not isinstance(fold_observation_count, int)
                or isinstance(fold_observation_count, bool)
                or fold_observation_count != expected_fold_size
                or not isinstance(fold_transition_count, int)
                or isinstance(fold_transition_count, bool)
                or fold_transition_count < 0
                or fold_transition_count > fold_observation_count - 1
                or not isinstance(fold_state_counts, dict)
                or any(
                    state not in supported_states
                    or not isinstance(count, int)
                    or isinstance(count, bool)
                    or count <= 0
                    for state, count in fold_state_counts.items()
                )
                or sum(fold_state_counts.values()) != fold_observation_count
                or fold_transition_count < max(0, len(fold_state_counts) - 1)
            ):
                raise ValueError("offline fixture batch walk-forward diagnostics are invalid")
            try:
                fold_first = date.fromisoformat(fold["firstObservationDate"])
                fold_last = date.fromisoformat(fold["lastObservationDate"])
            except (TypeError, ValueError) as exc:
                raise ValueError("offline fixture batch walk-forward diagnostics are invalid") from exc
            if (
                fold_first.isoformat() != fold["firstObservationDate"]
                or fold_last.isoformat() != fold["lastObservationDate"]
                or fold_first > fold_last
                or (fold_observation_count == 1 and fold_first != fold_last)
                or (fold_observation_count > 1 and fold_first >= fold_last)
                or (previous_fold_last is not None and fold_first <= previous_fold_last)
            ):
                raise ValueError("offline fixture batch walk-forward diagnostics are invalid")
            previous_fold_last = fold_last
            _merge_histogram(aggregate_fold_counts, fold_state_counts)
            within_fold_transitions += fold_transition_count

        if (
            folds[0]["firstObservationDate"] != first_date
            or folds[-1]["lastObservationDate"] != last_date
            or sum(fold["observationCount"] for fold in folds) != observation_count
            or aggregate_fold_counts != state_counts
            or transition_count < within_fold_transitions
            or transition_count > within_fold_transitions + fold_count - 1
        ):
            raise ValueError("offline fixture batch walk-forward diagnostics are invalid")
    elif folds:
        raise ValueError("offline fixture batch walk-forward diagnostics are invalid")

    validated = {
        "dtoVersion": value["dtoVersion"],
        "providerCalls": "blocked",
        "accountData": "absent",
        "executionRoutes": "absent",
        "candidateIntent": "skip",
        "performanceClaims": value["performanceClaims"],
        "state": value["state"],
        "eligibleObservationCount": observation_count,
        "firstObservationDate": first_date,
        "lastObservationDate": last_date,
        "stateCounts": {state: state_counts[state] for state in sorted(state_counts)},
        "transitionCount": transition_count,
        "foldCount": fold_count,
        "folds": [
            {
                "foldIndex": fold["foldIndex"],
                "observationCount": fold["observationCount"],
                "firstObservationDate": fold["firstObservationDate"],
                "lastObservationDate": fold["lastObservationDate"],
                "stateCounts": {state: fold["stateCounts"][state] for state in sorted(fold["stateCounts"])},
                "transitionCount": fold["transitionCount"],
            }
            for fold in folds
        ],
    }
    if validated != canonical:
        raise ValueError("offline fixture batch walk-forward diagnostics are invalid")
    return validated
