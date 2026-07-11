from __future__ import annotations

from datetime import datetime
from math import isfinite
from typing import Any

from money_maker_3000.contracts import (
    DEFAULT_ALLOCATION_POLICY,
    ValidationResult,
    safe_allocation_policy_for_output,
    utc_iso,
    validate_allocation_policy,
)
from money_maker_3000.ledger import ABSENT, REDACTED, redact_mapping
from money_maker_3000.risk import RiskInputState, assess_data_freshness

RECONCILIATION_VERSION = "0.1.0-sim"
SAFE_RECONCILIATION_SOURCES = ("synthetic", "offline-fixture")
SAFE_PROVIDER_CALL_STATUSES = ("blocked", "not-attempted", "read-only-fixture")
SAFE_FRESHNESS_STATES = ("fresh", "stale", "missing", "unknown", "future-data")


def _non_negative_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and isfinite(value)
        and value >= 0
    )


def _non_negative_int(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


def _validate_allocation_policy_fail_closed(policy: Any) -> ValidationResult:
    if not isinstance(policy, dict):
        return ValidationResult(ok=False, errors=("allocation policy must be an object",))
    try:
        return validate_allocation_policy(policy)
    except (KeyError, TypeError, ValueError, OverflowError):
        return ValidationResult(ok=False, errors=("allocation policy contains invalid values",))


def _sanitize_non_finite_values(value: Any) -> tuple[Any, bool]:
    if isinstance(value, float) and not isfinite(value):
        return REDACTED, False
    if isinstance(value, (list, tuple)):
        sanitized_items = [_sanitize_non_finite_values(item) for item in value]
        return [item for item, _ in sanitized_items], all(valid for _, valid in sanitized_items)
    if isinstance(value, dict):
        sanitized_items = {
            key: _sanitize_non_finite_values(item)
            for key, item in value.items()
            if isinstance(key, str)
        }
        return (
            {key: item for key, (item, _) in sanitized_items.items()},
            len(sanitized_items) == len(value)
            and all(valid for _, valid in sanitized_items.values()),
        )
    return value, True


def _freshness_from_inputs(
    *,
    data_freshness: str | None,
    data_last_seen_date: str | None,
    started_at_date: str | None,
    max_age_days: int,
    problems: list[str],
) -> str:
    if data_last_seen_date and started_at_date:
        try:
            derived = assess_data_freshness(
                last_date=data_last_seen_date,
                started_at_date=started_at_date,
                max_age_days=max_age_days,
            )
        except (TypeError, ValueError):
            problems.append("freshness dates must use valid ISO calendar dates")
            return "unknown"
        if data_freshness and data_freshness != derived:
            problems.append("data freshness conflicts with provided dates")
        return derived
    if data_freshness is not None:
        if data_freshness not in SAFE_FRESHNESS_STATES:
            problems.append("data freshness state is invalid")
            return "unknown"
        return data_freshness
    return "unknown"


def _provider_state(provider_snapshot: dict[str, Any] | None, problems: list[str]) -> str:
    snapshot = provider_snapshot or {}
    if not isinstance(snapshot, dict):
        problems.append("provider snapshot must be an object")
        return "unknown"
    if snapshot.get("source", "synthetic") not in SAFE_RECONCILIATION_SOURCES:
        problems.append("provider snapshot source must be synthetic or offline-fixture")
    if snapshot.get("providerCallStatus", "not-attempted") not in SAFE_PROVIDER_CALL_STATUSES:
        problems.append("provider call status must be blocked, not-attempted, or read-only-fixture")
    if snapshot.get("providerState") == "known-read-only":
        return "known-read-only"
    problems.append("provider state must be known-read-only")
    return "unknown"


def build_simulation_reconciliation_record(
    *,
    allocation_policy: dict[str, Any] | None = None,
    provider_snapshot: dict[str, Any] | None = None,
    observed_at: datetime | None = None,
    data_freshness: str | None = None,
    data_last_seen_date: str | None = None,
    started_at_date: str | None = None,
    max_data_age_days: int = 7,
    daily_loss_usd: float | None = None,
    weekly_loss_usd: float | None = None,
    allocation_drawdown_usd: float | None = None,
    instrument_exposure_usd: float = 0.0,
    open_positions: int = 0,
) -> dict[str, Any]:
    allocation = DEFAULT_ALLOCATION_POLICY if allocation_policy is None else allocation_policy
    problems: list[str] = []
    allocation_validation = _validate_allocation_policy_fail_closed(allocation)
    problems.extend(allocation_validation.errors)
    effective_allocation = allocation if allocation_validation.ok else DEFAULT_ALLOCATION_POLICY

    parsed_max_data_age_days = max_data_age_days
    if not _non_negative_int(max_data_age_days):
        problems.append("max data age days must be a non-negative integer")
        parsed_max_data_age_days = 7

    provider_state = _provider_state(provider_snapshot, problems)
    freshness = _freshness_from_inputs(
        data_freshness=data_freshness,
        data_last_seen_date=data_last_seen_date,
        started_at_date=started_at_date,
        max_age_days=parsed_max_data_age_days,
        problems=problems,
    )

    loss_inputs = {
        "dailyLossUsd": ("daily loss", daily_loss_usd),
        "weeklyLossUsd": ("weekly loss", weekly_loss_usd),
        "allocationDrawdownUsd": ("allocation drawdown", allocation_drawdown_usd),
    }
    safe_loss_values: dict[str, float | int | None] = {}
    for field, (label, value) in loss_inputs.items():
        if value is not None and not _non_negative_number(value):
            problems.append(f"{label} must be a non-negative USD amount")
            safe_loss_values[field] = None
        else:
            safe_loss_values[field] = value
    if not _non_negative_number(instrument_exposure_usd):
        problems.append("instrument exposure must be a non-negative USD amount")
    if not _non_negative_int(open_positions):
        problems.append("open positions must be a non-negative integer")

    has_loss_context = all(value is not None for value in safe_loss_values.values())
    reconciliation_state = "available" if not problems and has_loss_context else "missing"
    if not has_loss_context:
        problems.append("loss reconciliation metrics are required")

    parsed_instrument_exposure_usd = (
        float(instrument_exposure_usd) if _non_negative_number(instrument_exposure_usd) else 0.0
    )
    parsed_open_positions = open_positions if _non_negative_int(open_positions) else 0
    risk_state = RiskInputState(
        provider_state=provider_state,
        reconciliation_state=reconciliation_state,
        data_freshness=freshness,
        daily_loss_usd=safe_loss_values["dailyLossUsd"],
        weekly_loss_usd=safe_loss_values["weeklyLossUsd"],
        allocation_drawdown_usd=safe_loss_values["allocationDrawdownUsd"],
        instrument_exposure_usd=parsed_instrument_exposure_usd,
        open_positions=parsed_open_positions,
    )
    raw_provider_snapshot = provider_snapshot if isinstance(provider_snapshot, dict) else {}
    safe_provider_snapshot, provider_snapshot_values_valid = _sanitize_non_finite_values(
        raw_provider_snapshot
    )
    if not provider_snapshot_values_valid:
        problems.append("provider snapshot must not contain non-finite numbers")
        reconciliation_state = "missing"
        risk_state = RiskInputState(
            **{
                **risk_state.to_dict(),
                "reconciliation_state": reconciliation_state,
            }
        )
    safe_allocation = safe_allocation_policy_for_output(effective_allocation)

    return {
        "reconciliationVersion": RECONCILIATION_VERSION,
        "dtoVersion": "simulation-reconciliation-record.v1",
        "mode": "simulation",
        "environment": "synthetic",
        "providerCalls": "blocked",
        "executionRoutes": ABSENT,
        "providerState": provider_state,
        "reconciliationState": reconciliation_state,
        "observedAt": utc_iso(observed_at) if observed_at else utc_iso(),
        "allocation": {
            "allocationId": safe_allocation["allocationId"],
            "botAllocationUsd": safe_allocation["botAllocationUsd"],
            "reservedUsd": safe_allocation["reservedUsd"],
            "availableUsd": safe_allocation["availableUsd"],
            "maxOrderUsd": safe_allocation["maxOrderUsd"],
            "providerDemoBalance": REDACTED,
            "providerBalanceUse": "ignored-for-budget",
        },
        "providerSnapshot": redact_mapping(safe_provider_snapshot),
        "lossReconciliation": {
            **safe_loss_values,
            "evaluation": "simulation-only-not-real-pnl",
        },
        "exposure": {
            "instrumentExposureUsd": parsed_instrument_exposure_usd,
            "openPositions": parsed_open_positions,
        },
        "riskInputState": risk_state.to_dict(),
        "validation": {
            "ok": reconciliation_state == "available",
            "problems": problems,
            "allocation": allocation_validation.to_dict(),
            "redaction": {
                "providerDemoBalance": REDACTED,
                "accountIdentifiers": REDACTED,
                "rawProviderPayloads": ABSENT,
            },
        },
    }


def risk_input_state_from_reconciliation(record: dict[str, Any] | None) -> RiskInputState:
    if not isinstance(record, dict):
        return RiskInputState(data_freshness="missing")
    state = record.get("riskInputState")
    if not isinstance(state, dict) or record.get("reconciliationState") != "available":
        return RiskInputState(
            provider_state=record.get("providerState", "unknown"),
            reconciliation_state=record.get("reconciliationState", "missing"),
            data_freshness="missing",
        )
    return RiskInputState(
        provider_state=state.get("provider_state", "unknown"),
        reconciliation_state=state.get("reconciliation_state", "missing"),
        data_freshness=state.get("data_freshness", "unknown"),
        daily_loss_usd=state.get("daily_loss_usd"),
        weekly_loss_usd=state.get("weekly_loss_usd"),
        allocation_drawdown_usd=state.get("allocation_drawdown_usd"),
        instrument_exposure_usd=state.get("instrument_exposure_usd", 0.0),
        open_positions=state.get("open_positions", 0),
    )
