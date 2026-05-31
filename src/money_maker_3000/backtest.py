from __future__ import annotations

import platform
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Iterable, Iterator

from money_maker_3000.contracts import (
    DEFAULT_ALLOCATION_POLICY,
    build_allocation_policy,
    merge_simulation_config,
    utc_iso,
)
from money_maker_3000.engine import CONFIG_VERSION, build_simulation_run
from money_maker_3000.market_history import (
    Bar,
    MarketHistoryAccumulator,
    PARSER_VERSION,
    build_period_performance_diagnostics,
)
from money_maker_3000.risk import DEFAULT_RISK_POLICY, RiskInputState, assess_data_freshness

DEFAULT_SCENARIOS = (
    {"scenarioId": "dca-500", "strategyId": "dca-cash-reserve", "budgetUsd": 500.0},
    {"scenarioId": "dca-1000", "strategyId": "dca-cash-reserve", "budgetUsd": 1000.0},
    {"scenarioId": "rebalance-1500", "strategyId": "threshold-rebalance", "budgetUsd": 1500.0},
    {"scenarioId": "watchlist-1000", "strategyId": "news-aware-watchlist", "budgetUsd": 1000.0},
)


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


def iter_decision_events(
    bars: Iterable[Bar],
    *,
    strategy_id: str = "dca-cash-reserve",
    selected_instrument: dict[str, Any] | None = None,
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
        allocation = build_allocation_policy(bot_allocation_usd=max(float(scenario.get("budgetUsd", 1000.0)), 1000.0))
        run = build_simulation_run(
            strategy_id=scenario["strategyId"],
            now=started,
            simulation_config={
                **scenario.get("simulationConfig", {}),
                "budgetUsd": float(scenario.get("budgetUsd", scenario.get("simulationConfig", {}).get("budgetUsd", 1000.0))),
            },
            allocation_policy=allocation,
            risk_state=RiskInputState(data_freshness="fresh"),
        )
        event = DecisionEvent(eventId=f"synthetic-{index + 1}", bar={}, run=run)
        summary_accumulator.update(event)
        run_objects.append(run)
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
    budget_usd: float = 1000.0,
    allocation_policy: dict[str, Any] | None = None,
    started_at: datetime | None = None,
    input_sha256: str | None = None,
) -> dict[str, Any]:
    started = started_at or datetime.fromisoformat("2026-05-15T00:00:00+00:00")
    history = MarketHistoryAccumulator()
    period_bars: list[Bar] = []
    event_summary = DecisionSummaryAccumulator()
    scenario_summaries: list[dict[str, Any]] = []
    runs: list[dict[str, Any]] = []
    for event in iter_decision_events(
        _tee_history(bars, history, period_bars),
        strategy_id=strategy_id,
        selected_instrument=selected_instrument,
        budget_usd=budget_usd,
        allocation_policy=allocation_policy,
        started_at=started,
    ):
        event_summary.update(event)
        run = event.run
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
            "inputSha256": input_sha256,
            "parserVersion": PARSER_VERSION,
            "pythonVersion": platform.python_version(),
            "performanceClaims": "diagnostics-only-no-return-or-execution-quality-metrics",
        },
        "history": history_summary,
        "periodDiagnostics": build_period_performance_diagnostics(period_bars),
        "summary": event_summary.to_dict(),
        "scenarioSummaries": scenario_summaries,
        "runs": runs,
        "ledgerRecords": [],
    }


def _tee_history(
    bars: Iterable[Bar],
    accumulator: MarketHistoryAccumulator,
    period_bars: list[Bar],
) -> Iterator[Bar]:
    for bar in bars:
        accumulator.update(bar)
        period_bars.append(bar)
        yield bar
