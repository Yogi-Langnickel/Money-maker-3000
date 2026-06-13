from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from money_maker_3000.contracts import (
    DEFAULT_ALLOCATION_POLICY,
    DEFAULT_BUDGET_POLICY,
    DEFAULT_SCHEDULE_POLICY,
    ValidationResult,
    safe_allocation_policy_for_output,
    validate_allocation_policy,
    validate_budget_policy,
    validate_schedule_policy,
    validate_simulation_config,
)
from money_maker_3000.providers import build_provider_metadata_snapshot
from money_maker_3000.strategies import strategy_by_id, validate_strategy_registry

RISK_POLICY_VERSION = "0.1.0-sim"

DEFAULT_RISK_POLICY = {
    "dailyLossStopUsd": 50.0,
    "weeklyLossStopUsd": 150.0,
    "maxAllocationDrawdownUsd": 250.0,
    "maxOrderUsd": 250.0,
    "perInstrumentExposureCapUsd": 500.0,
    "maxOpenPositions": 3,
    "cashReserveFloorUsd": 100.0,
    "leverage": 1,
    "shorts": "blocked",
    "copyTrading": "blocked",
    "blockedInstrumentClasses": ["CFD", "OPTION", "DERIVATIVE", "CRYPTO"],
    "lossStopEvaluation": "simulation-only",
    "realPnlEvaluation": "not-evaluated-until-reconciliation-exists",
    "maxDataAgeDays": 7,
}

VALIDATION_VETO_CODES = {
    "strategyRegistry": "invalid-strategy-registry",
    "budgetPolicy": "invalid-budget-policy",
    "allocationPolicy": "invalid-allocation-policy",
    "schedulePolicy": "invalid-schedule-policy",
    "riskPolicy": "invalid-risk-policy",
    "simulationConfig": "invalid-simulation-config",
}


@dataclass(frozen=True)
class RiskInputState:
    provider_state: str = "unknown"
    reconciliation_state: str = "missing"
    data_freshness: str = "unknown"
    daily_loss_usd: float | None = None
    weekly_loss_usd: float | None = None
    allocation_drawdown_usd: float | None = None
    instrument_exposure_usd: float = 0.0
    open_positions: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_risk_policy(policy: dict[str, Any] | None = None, *, allocation_policy: dict[str, Any] | None = None) -> ValidationResult:
    risk_policy = policy or DEFAULT_RISK_POLICY
    allocation = allocation_policy or DEFAULT_ALLOCATION_POLICY
    errors: list[str] = []
    required = (
        "dailyLossStopUsd",
        "weeklyLossStopUsd",
        "maxAllocationDrawdownUsd",
        "maxOrderUsd",
        "perInstrumentExposureCapUsd",
        "maxOpenPositions",
        "cashReserveFloorUsd",
        "leverage",
        "shorts",
        "copyTrading",
        "blockedInstrumentClasses",
        "lossStopEvaluation",
        "realPnlEvaluation",
        "maxDataAgeDays",
    )
    for key in required:
        if key not in risk_policy:
            errors.append(f"risk policy is incomplete: {key}")
    if errors:
        return ValidationResult(ok=False, errors=tuple(errors))

    daily = risk_policy["dailyLossStopUsd"]
    weekly = risk_policy["weeklyLossStopUsd"]
    drawdown = risk_policy["maxAllocationDrawdownUsd"]
    allocation_usd = allocation.get("botAllocationUsd")
    if not all(isinstance(value, (int, float)) and value > 0 for value in (daily, weekly, drawdown)):
        errors.append("loss and drawdown stops must be positive")
    if isinstance(allocation_usd, (int, float)):
        if not (daily <= weekly <= allocation_usd):
            errors.append("daily loss stop must be <= weekly loss stop <= bot allocation")
        if drawdown > allocation_usd:
            errors.append("max allocation drawdown cannot exceed bot allocation")
    if risk_policy["maxOrderUsd"] > allocation.get("availableUsd", 0):
        errors.append("risk max order cannot exceed available allocation")
    if risk_policy["maxOrderUsd"] > allocation.get("maxOrderUsd", 0):
        errors.append("risk max order cannot exceed allocation max order")
    if risk_policy["cashReserveFloorUsd"] > allocation.get("reservedUsd", 0):
        errors.append("cash reserve floor cannot exceed reserved allocation")
    if risk_policy["leverage"] != 1:
        errors.append("leverage must remain 1")
    if risk_policy["shorts"] != "blocked":
        errors.append("shorts must be blocked")
    if risk_policy["copyTrading"] != "blocked":
        errors.append("copy trading must be blocked")
    for blocked_class in ("CFD", "OPTION", "DERIVATIVE", "CRYPTO"):
        if blocked_class not in risk_policy["blockedInstrumentClasses"]:
            errors.append(f"{blocked_class} must remain blocked")
    if risk_policy["lossStopEvaluation"] != "simulation-only":
        errors.append("loss stops must remain simulation-only")
    if risk_policy["realPnlEvaluation"] != "not-evaluated-until-reconciliation-exists":
        errors.append("real PnL must not be evaluated until reconciliation exists")
    return ValidationResult(ok=not errors, errors=tuple(errors))


def assess_data_freshness(*, last_date: str | None, started_at_date: str, max_age_days: int) -> str:
    if not last_date:
        return "missing"
    last = date.fromisoformat(last_date)
    started = date.fromisoformat(started_at_date)
    age_days = (started - last).days
    if age_days < 0:
        return "future-data"
    if age_days > max_age_days:
        return "stale"
    return "fresh"


def evaluate_risk_gate(
    *,
    simulation_config: dict[str, Any],
    allocation_policy: dict[str, Any] | None = None,
    risk_policy: dict[str, Any] | None = None,
    budget_policy: dict[str, Any] | None = None,
    schedule_policy: dict[str, Any] | None = None,
    risk_state: RiskInputState | None = None,
    proposed_order_usd: float | None = None,
) -> dict[str, Any]:
    allocation = allocation_policy or DEFAULT_ALLOCATION_POLICY
    policy = risk_policy or DEFAULT_RISK_POLICY
    state = risk_state or RiskInputState()
    vetoes: list[str] = []
    diagnostics: list[str] = []

    validations = {
        "strategyRegistry": validate_strategy_registry().to_dict(),
        "budgetPolicy": validate_budget_policy(budget_policy or DEFAULT_BUDGET_POLICY).to_dict(),
        "allocationPolicy": validate_allocation_policy(allocation).to_dict(),
        "schedulePolicy": validate_schedule_policy(schedule_policy or DEFAULT_SCHEDULE_POLICY).to_dict(),
        "riskPolicy": validate_risk_policy(policy, allocation_policy=allocation).to_dict(),
        "simulationConfig": validate_simulation_config(
            simulation_config,
            budget_policy=budget_policy or DEFAULT_BUDGET_POLICY,
            allocation_policy=allocation,
            schedule_policy=schedule_policy or DEFAULT_SCHEDULE_POLICY,
        ).to_dict(),
    }
    for validation_name, result in validations.items():
        if not result["ok"]:
            vetoes.append(VALIDATION_VETO_CODES[validation_name])

    strategy = strategy_by_id(simulation_config.get("strategyId"))
    selected = simulation_config.get("selectedInstrument", {})
    if strategy is None:
        vetoes.append("invalid-strategy-version")
    elif simulation_config.get("strategyVersion") and simulation_config["strategyVersion"] != strategy["version"]:
        vetoes.append("invalid-strategy-version")
    if selected.get("instrumentClass") in policy.get("blockedInstrumentClasses", []):
        vetoes.append("blocked-instrument-class")

    if state.provider_state != "known-read-only":
        vetoes.append("unknown-provider-state")
    if state.reconciliation_state != "available":
        vetoes.append("missing-reconciliation")
    if state.data_freshness != "fresh":
        vetoes.append(f"data-{state.data_freshness}")

    diagnostics.append("loss/drawdown stops are simulation-only; not evaluated against real PnL")
    if state.daily_loss_usd is None or state.weekly_loss_usd is None or state.allocation_drawdown_usd is None:
        vetoes.append("missing-loss-reconciliation")
    else:
        if state.daily_loss_usd >= policy["dailyLossStopUsd"]:
            vetoes.append("daily-loss-stop")
        if state.weekly_loss_usd >= policy["weeklyLossStopUsd"]:
            vetoes.append("weekly-loss-stop")
        if state.allocation_drawdown_usd >= policy["maxAllocationDrawdownUsd"]:
            vetoes.append("allocation-drawdown-stop")

    if proposed_order_usd is None:
        vetoes.append("missing-order-intent")
    else:
        if proposed_order_usd > policy["maxOrderUsd"]:
            vetoes.append("per-order-cap")
        if proposed_order_usd > allocation["availableUsd"]:
            vetoes.append("insufficient-available-allocation")
        if allocation["availableUsd"] - proposed_order_usd < policy["cashReserveFloorUsd"]:
            vetoes.append("cash-reserve-floor")
        if state.instrument_exposure_usd + proposed_order_usd > policy["perInstrumentExposureCapUsd"]:
            vetoes.append("per-instrument-exposure-cap")
    if state.open_positions >= policy["maxOpenPositions"]:
        vetoes.append("max-open-positions")

    vetoes.extend(("provider-not-connected", "execution-route-absent"))
    provider_metadata = build_provider_metadata_snapshot()
    safe_allocation = safe_allocation_policy_for_output(allocation)
    return {
        "riskPolicyVersion": RISK_POLICY_VERSION,
        "decision": "blocked",
        "ok": False,
        "vetoes": sorted(set(vetoes)),
        "diagnostics": diagnostics,
        "validations": validations,
        "allocation": {
            "allocationId": safe_allocation["allocationId"],
            "strategyAllocationId": safe_allocation["strategyAllocationIds"].get(simulation_config.get("strategyId")),
            "botAllocationUsd": safe_allocation["botAllocationUsd"],
            "reservedUsd": safe_allocation["reservedUsd"],
            "availableUsd": safe_allocation["availableUsd"],
            "maxOrderUsd": safe_allocation["maxOrderUsd"],
            "providerDemoBalance": "redacted",
            "providerBalanceUse": "ignored-for-budget",
        },
        "riskState": state.to_dict(),
        "providerMetadata": provider_metadata,
    }
