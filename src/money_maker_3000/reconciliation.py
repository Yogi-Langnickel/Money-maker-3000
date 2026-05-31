from __future__ import annotations

from datetime import datetime
from typing import Any

from money_maker_3000.contracts import DEFAULT_ALLOCATION_POLICY, utc_iso, validate_allocation_policy
from money_maker_3000.ledger import ABSENT, REDACTED, redact_mapping
from money_maker_3000.risk import RiskInputState, assess_data_freshness

RECONCILIATION_VERSION = "0.1.0-sim"
SAFE_RECONCILIATION_SOURCES = ("synthetic", "offline-fixture")
SAFE_PROVIDER_CALL_STATUSES = ("blocked", "not-attempted", "read-only-fixture")
SAFE_FRESHNESS_STATES = ("fresh", "stale", "missing", "unknown", "future-data")


def _non_negative_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and value >= 0


def _non_negative_int(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


def _freshness_from_inputs(
    *,
    data_freshness: str | None,
    data_last_seen_date: str | None,
    started_at_date: str | None,
    max_age_days: int,
    problems: list[str],
) -> str:
    if data_last_seen_date and started_at_date:
        derived = assess_data_freshness(
            last_date=data_last_seen_date,
            started_at_date=started_at_date,
            max_age_days=max_age_days,
        )
        if data_freshness and data_freshness != derived:
            problems.append("data freshness conflicts with provided dates")
        return derived
    if data_freshness:
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
    allocation = allocation_policy or DEFAULT_ALLOCATION_POLICY
    problems: list[str] = []
    allocation_validation = validate_allocation_policy(allocation)
    problems.extend(allocation_validation.errors)

    provider_state = _provider_state(provider_snapshot, problems)
    freshness = _freshness_from_inputs(
        data_freshness=data_freshness,
        data_last_seen_date=data_last_seen_date,
        started_at_date=started_at_date,
        max_age_days=max_data_age_days,
        problems=problems,
    )

    for label, value in (
        ("daily loss", daily_loss_usd),
        ("weekly loss", weekly_loss_usd),
        ("allocation drawdown", allocation_drawdown_usd),
    ):
        if value is not None and not _non_negative_number(value):
            problems.append(f"{label} must be a non-negative USD amount")
    if not _non_negative_number(instrument_exposure_usd):
        problems.append("instrument exposure must be a non-negative USD amount")
    if not _non_negative_int(open_positions):
        problems.append("open positions must be a non-negative integer")

    has_loss_context = all(
        value is not None
        for value in (daily_loss_usd, weekly_loss_usd, allocation_drawdown_usd)
    )
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
        daily_loss_usd=daily_loss_usd,
        weekly_loss_usd=weekly_loss_usd,
        allocation_drawdown_usd=allocation_drawdown_usd,
        instrument_exposure_usd=parsed_instrument_exposure_usd,
        open_positions=parsed_open_positions,
    )
    safe_provider_snapshot = provider_snapshot if isinstance(provider_snapshot, dict) else {}

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
            "allocationId": allocation.get("allocationId"),
            "botAllocationUsd": allocation.get("botAllocationUsd"),
            "reservedUsd": allocation.get("reservedUsd"),
            "availableUsd": allocation.get("availableUsd"),
            "maxOrderUsd": allocation.get("maxOrderUsd"),
            "providerDemoBalance": REDACTED,
            "providerBalanceUse": "ignored-for-budget",
        },
        "providerSnapshot": redact_mapping(safe_provider_snapshot),
        "lossReconciliation": {
            "dailyLossUsd": daily_loss_usd,
            "weeklyLossUsd": weekly_loss_usd,
            "allocationDrawdownUsd": allocation_drawdown_usd,
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
