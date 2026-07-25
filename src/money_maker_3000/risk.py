from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from math import isfinite
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

RISK_VETO_CODES = frozenset(
    {
        *VALIDATION_VETO_CODES.values(),
        "allocation-drawdown-stop",
        "blocked-instrument-class",
        "cash-reserve-floor",
        "daily-loss-stop",
        "data-future-data",
        "data-missing",
        "data-stale",
        "data-unknown",
        "execution-route-absent",
        "insufficient-available-allocation",
        "invalid-order-intent",
        "invalid-risk-state",
        "invalid-strategy-version",
        "max-open-positions",
        "missing-loss-reconciliation",
        "missing-order-intent",
        "missing-reconciliation",
        "per-instrument-exposure-cap",
        "per-order-cap",
        "provider-not-connected",
        "unknown-provider-state",
        "weekly-loss-stop",
    }
)


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


def _positive_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and isfinite(value)
        and value > 0
    )


def _positive_int(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value > 0


def _non_negative_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and isfinite(value)
        and value >= 0
    )


def _validate_allocation_policy_fail_closed(policy: Any) -> ValidationResult:
    if not isinstance(policy, dict):
        return ValidationResult(ok=False, errors=("allocation policy must be an object",))
    try:
        return validate_allocation_policy(policy)
    except (KeyError, TypeError, ValueError, OverflowError):
        return ValidationResult(ok=False, errors=("allocation policy contains invalid values",))


def _validate_budget_policy_fail_closed(policy: Any) -> ValidationResult:
    if not isinstance(policy, dict):
        return ValidationResult(ok=False, errors=("budget policy must be an object",))
    try:
        return validate_budget_policy(policy)
    except (KeyError, TypeError, ValueError, OverflowError):
        return ValidationResult(ok=False, errors=("budget policy contains invalid values",))


def _validate_schedule_policy_fail_closed(policy: Any) -> ValidationResult:
    if not isinstance(policy, dict):
        return ValidationResult(ok=False, errors=("schedule policy must be an object",))
    try:
        return validate_schedule_policy(policy)
    except (KeyError, TypeError, ValueError, OverflowError):
        return ValidationResult(ok=False, errors=("schedule policy contains invalid values",))


def _safe_risk_input_state(value: Any) -> tuple[RiskInputState, bool]:
    if not isinstance(value, RiskInputState):
        return RiskInputState(), False

    provider_state = (
        value.provider_state
        if value.provider_state in ("unknown", "known-read-only")
        else "unknown"
    )
    reconciliation_state = (
        value.reconciliation_state
        if value.reconciliation_state in ("missing", "available")
        else "missing"
    )
    data_freshness = value.data_freshness if value.data_freshness in (
        "fresh",
        "stale",
        "missing",
        "unknown",
        "future-data",
    ) else "unknown"
    daily_loss_usd = (
        value.daily_loss_usd
        if value.daily_loss_usd is None or _non_negative_number(value.daily_loss_usd)
        else None
    )
    weekly_loss_usd = (
        value.weekly_loss_usd
        if value.weekly_loss_usd is None or _non_negative_number(value.weekly_loss_usd)
        else None
    )
    allocation_drawdown_usd = (
        value.allocation_drawdown_usd
        if value.allocation_drawdown_usd is None or _non_negative_number(value.allocation_drawdown_usd)
        else None
    )
    instrument_exposure_usd = (
        value.instrument_exposure_usd
        if _non_negative_number(value.instrument_exposure_usd)
        else 0.0
    )
    open_positions = (
        value.open_positions
        if isinstance(value.open_positions, int)
        and not isinstance(value.open_positions, bool)
        and value.open_positions >= 0
        else 0
    )
    safe_state = RiskInputState(
        provider_state=provider_state,
        reconciliation_state=reconciliation_state,
        data_freshness=data_freshness,
        daily_loss_usd=daily_loss_usd,
        weekly_loss_usd=weekly_loss_usd,
        allocation_drawdown_usd=allocation_drawdown_usd,
        instrument_exposure_usd=instrument_exposure_usd,
        open_positions=open_positions,
    )
    return safe_state, safe_state == value


def validate_risk_policy(policy: dict[str, Any] | None = None, *, allocation_policy: dict[str, Any] | None = None) -> ValidationResult:
    if policy is not None and not isinstance(policy, dict):
        return ValidationResult(ok=False, errors=("risk policy must be an object",))
    if allocation_policy is not None and not isinstance(allocation_policy, dict):
        return ValidationResult(ok=False, errors=("allocation policy must be an object",))

    risk_policy = DEFAULT_RISK_POLICY if policy is None else policy
    allocation = DEFAULT_ALLOCATION_POLICY if allocation_policy is None else allocation_policy
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
    if not all(_positive_number(value) for value in (daily, weekly, drawdown)):
        errors.append("loss and drawdown stops must be positive")
    elif _positive_number(allocation_usd):
        if not (daily <= weekly <= allocation_usd):
            errors.append("daily loss stop must be <= weekly loss stop <= bot allocation")
        if drawdown > allocation_usd:
            errors.append("max allocation drawdown cannot exceed bot allocation")

    max_order = risk_policy["maxOrderUsd"]
    exposure_cap = risk_policy["perInstrumentExposureCapUsd"]
    cash_reserve = risk_policy["cashReserveFloorUsd"]
    allocation_available = allocation.get("availableUsd")
    allocation_max_order = allocation.get("maxOrderUsd")
    allocation_reserved = allocation.get("reservedUsd")
    if not _positive_number(max_order):
        errors.append("risk max order must be a positive finite number")
    else:
        if _non_negative_number(allocation_available) and max_order > allocation_available:
            errors.append("risk max order cannot exceed available allocation")
        if _positive_number(allocation_max_order) and max_order > allocation_max_order:
            errors.append("risk max order cannot exceed allocation max order")
    if not _positive_number(exposure_cap):
        errors.append("per-instrument exposure cap must be a positive finite number")
    if not _positive_number(cash_reserve):
        errors.append("cash reserve floor must be a positive finite number")
    elif _non_negative_number(allocation_reserved) and cash_reserve > allocation_reserved:
        errors.append("cash reserve floor cannot exceed reserved allocation")
    if not _positive_int(risk_policy["maxOpenPositions"]):
        errors.append("max open positions must be a positive integer")
    if not _positive_int(risk_policy["maxDataAgeDays"]):
        errors.append("max data age days must be a positive integer")
    if isinstance(risk_policy["leverage"], bool) or risk_policy["leverage"] != 1:
        errors.append("leverage must remain 1")
    if risk_policy["shorts"] != "blocked":
        errors.append("shorts must be blocked")
    if risk_policy["copyTrading"] != "blocked":
        errors.append("copy trading must be blocked")
    blocked_instrument_classes = risk_policy["blockedInstrumentClasses"]
    if not isinstance(blocked_instrument_classes, list) or not all(
        isinstance(value, str) for value in blocked_instrument_classes
    ):
        errors.append("blocked instrument classes must be a list of strings")
        blocked_instrument_classes = []
    for blocked_class in ("CFD", "OPTION", "DERIVATIVE", "CRYPTO"):
        if blocked_class not in blocked_instrument_classes:
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
    allocation = DEFAULT_ALLOCATION_POLICY if allocation_policy is None else allocation_policy
    policy = DEFAULT_RISK_POLICY if risk_policy is None else risk_policy
    budget = DEFAULT_BUDGET_POLICY if budget_policy is None else budget_policy
    schedule = DEFAULT_SCHEDULE_POLICY if schedule_policy is None else schedule_policy
    state, risk_state_valid = _safe_risk_input_state(
        RiskInputState() if risk_state is None else risk_state
    )
    safe_simulation_config = simulation_config if isinstance(simulation_config, dict) else {}
    vetoes: list[str] = []
    diagnostics: list[str] = []

    allocation_validation = _validate_allocation_policy_fail_closed(allocation)
    budget_validation = _validate_budget_policy_fail_closed(budget)
    schedule_validation = _validate_schedule_policy_fail_closed(schedule)
    effective_allocation = allocation if allocation_validation.ok else DEFAULT_ALLOCATION_POLICY
    effective_budget = budget if budget_validation.ok else DEFAULT_BUDGET_POLICY
    effective_schedule = schedule if schedule_validation.ok else DEFAULT_SCHEDULE_POLICY
    risk_policy_validation = validate_risk_policy(policy, allocation_policy=effective_allocation)
    effective_policy = policy if risk_policy_validation.ok else DEFAULT_RISK_POLICY
    validations = {
        "strategyRegistry": validate_strategy_registry().to_dict(),
        "budgetPolicy": budget_validation.to_dict(),
        "allocationPolicy": allocation_validation.to_dict(),
        "schedulePolicy": schedule_validation.to_dict(),
        "riskPolicy": risk_policy_validation.to_dict(),
        "simulationConfig": validate_simulation_config(
            simulation_config,
            budget_policy=effective_budget,
            allocation_policy=effective_allocation,
            schedule_policy=effective_schedule,
        ).to_dict(),
    }
    for validation_name, result in validations.items():
        if not result["ok"]:
            vetoes.append(VALIDATION_VETO_CODES[validation_name])
    if not risk_state_valid:
        vetoes.append("invalid-risk-state")

    strategy = strategy_by_id(safe_simulation_config.get("strategyId"))
    selected = safe_simulation_config.get("selectedInstrument", {})
    selected = selected if isinstance(selected, dict) else {}
    if strategy is None:
        vetoes.append("invalid-strategy-version")
    elif (
        safe_simulation_config.get("strategyVersion")
        and safe_simulation_config["strategyVersion"] != strategy["version"]
    ):
        vetoes.append("invalid-strategy-version")
    if selected.get("instrumentClass") in effective_policy["blockedInstrumentClasses"]:
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
        if state.daily_loss_usd >= effective_policy["dailyLossStopUsd"]:
            vetoes.append("daily-loss-stop")
        if state.weekly_loss_usd >= effective_policy["weeklyLossStopUsd"]:
            vetoes.append("weekly-loss-stop")
        if state.allocation_drawdown_usd >= effective_policy["maxAllocationDrawdownUsd"]:
            vetoes.append("allocation-drawdown-stop")

    if proposed_order_usd is None:
        vetoes.append("missing-order-intent")
    elif not _positive_number(proposed_order_usd):
        vetoes.append("invalid-order-intent")
    else:
        if proposed_order_usd > effective_policy["maxOrderUsd"]:
            vetoes.append("per-order-cap")
        if proposed_order_usd > effective_allocation["availableUsd"]:
            vetoes.append("insufficient-available-allocation")
        if effective_allocation["availableUsd"] - proposed_order_usd < effective_policy["cashReserveFloorUsd"]:
            vetoes.append("cash-reserve-floor")
        if state.instrument_exposure_usd + proposed_order_usd > effective_policy["perInstrumentExposureCapUsd"]:
            vetoes.append("per-instrument-exposure-cap")
    if state.open_positions >= effective_policy["maxOpenPositions"]:
        vetoes.append("max-open-positions")

    vetoes.extend(("provider-not-connected", "execution-route-absent"))
    provider_metadata = build_provider_metadata_snapshot()
    safe_allocation = safe_allocation_policy_for_output(effective_allocation)
    return {
        "riskPolicyVersion": RISK_POLICY_VERSION,
        "decision": "blocked",
        "ok": False,
        "vetoes": sorted(set(vetoes)),
        "diagnostics": diagnostics,
        "validations": validations,
        "allocation": {
            "allocationId": safe_allocation["allocationId"],
            "strategyAllocationId": safe_allocation["strategyAllocationIds"].get(
                safe_simulation_config.get("strategyId")
            ),
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
