from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime
from math import isfinite
from typing import Any

from money_maker_3000.contracts import (
    DEFAULT_ALLOCATION_POLICY,
    DEFAULT_BUDGET_POLICY,
    DEFAULT_SCHEDULE_POLICY,
    SIMULATION_CONTRACT_VERSION,
    SIMULATION_CONFIG_CONTRACT,
    default_simulation_config_for_strategy,
    merge_simulation_config,
    safe_positive_usd_for_output,
    safe_strategy_parameters_for_output,
    utc_iso,
)
from money_maker_3000.providers import build_provider_metadata_snapshot
from money_maker_3000.risk import DEFAULT_RISK_POLICY, RiskInputState, evaluate_risk_gate
from money_maker_3000.strategies import strategy_by_id, validate_strategy_registry

CONFIG_VERSION = SIMULATION_CONTRACT_VERSION

SYNTHETIC_POSITION_CONTEXT = [
    {
        "symbol": "SPY",
        "assetClass": "ETF",
        "exposureState": "synthetic",
        "newsContext": [
            {
                "headline": "Macro calendar context placeholder",
                "source": "synthetic",
                "summary": "Context only; cannot create an order or recommendation.",
            }
        ],
    },
    {
        "symbol": "GLD",
        "assetClass": "ETF",
        "exposureState": "synthetic",
        "newsContext": [
            {
                "headline": "Commodity market context placeholder",
                "source": "synthetic",
                "summary": "Source review is required before live ingestion.",
            }
        ],
    },
]


def stable_config_hash(config: dict[str, Any], allocation_policy: dict[str, Any], risk_policy: dict[str, Any]) -> str:
    payload = {
        "config": config,
        "allocationPolicy": {
            key: value for key, value in allocation_policy.items() if key != "providerDemoBalanceUsd"
        },
        "riskPolicy": risk_policy,
    }
    encoded = json.dumps(_json_safe(payload), sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, float) and not isfinite(value):
        return "invalid-non-finite-number"
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value


def build_simulation_run(
    *,
    strategy_id: str = "dca-cash-reserve",
    now: datetime | None = None,
    simulation_config: dict[str, Any] | None = None,
    budget_policy: dict[str, Any] | None = None,
    allocation_policy: dict[str, Any] | None = None,
    risk_policy: dict[str, Any] | None = None,
    schedule_policy: dict[str, Any] | None = None,
    risk_state: RiskInputState | None = None,
    proposed_order_usd: float | None = None,
    run_id_suffix: str | None = None,
) -> dict[str, Any]:
    evaluated_at = utc_iso(now or datetime.fromisoformat("2026-05-14T00:00:00+00:00"))
    effective_budget_policy = budget_policy or DEFAULT_BUDGET_POLICY
    effective_allocation_policy = allocation_policy or DEFAULT_ALLOCATION_POLICY
    effective_risk_policy = risk_policy or DEFAULT_RISK_POLICY
    effective_schedule_policy = schedule_policy or DEFAULT_SCHEDULE_POLICY
    effective_config = merge_simulation_config(strategy_id, simulation_config)
    strategy = strategy_by_id(effective_config["strategyId"])
    if strategy:
        effective_config["strategyVersion"] = strategy["version"]
    safe_strategy_parameters = safe_strategy_parameters_for_output(
        effective_config["strategyId"],
        effective_config.get("strategyParameters"),
    )
    safe_budget_usd = safe_positive_usd_for_output(
        effective_config.get("budgetUsd"),
        effective_budget_policy["baseBudgetUsd"],
    )
    config_hash = stable_config_hash(effective_config, effective_allocation_policy, effective_risk_policy)
    risk_decision = evaluate_risk_gate(
        simulation_config=effective_config,
        allocation_policy=effective_allocation_policy,
        risk_policy=effective_risk_policy,
        budget_policy=effective_budget_policy,
        schedule_policy=effective_schedule_policy,
        risk_state=risk_state,
        proposed_order_usd=proposed_order_usd,
    )

    run_id = f"sim-{evaluated_at}"
    normalized_run_id_suffix = ""
    if run_id_suffix:
        normalized_run_id_suffix = "".join(
            character if character.isalnum() or character in {"-", "_"} else "-"
            for character in run_id_suffix
        ).strip("-")
        if normalized_run_id_suffix:
            run_id = f"{run_id}-{normalized_run_id_suffix}"

    correlation_id = f"corr-{config_hash[:16]}"
    if normalized_run_id_suffix:
        suffix_hash = hashlib.sha256(normalized_run_id_suffix.encode("utf-8")).hexdigest()[:8]
        correlation_id = f"{correlation_id}-{suffix_hash}"

    return {
        "dtoVersion": "simulation-run.v1",
        "runId": run_id,
        "correlationId": correlation_id,
        "strategyId": strategy["strategyId"] if strategy else effective_config["strategyId"],
        "strategyVersion": strategy["version"] if strategy else "unknown",
        "configVersion": CONFIG_VERSION,
        "configHash": config_hash,
        "mode": "simulation",
        "environment": "synthetic",
        "evaluatedAt": evaluated_at,
        "decision": "skip",
        "riskResult": "blocked",
        "riskDecision": risk_decision,
        "vetoes": risk_decision["vetoes"],
        "schedulePolicy": deepcopy(effective_schedule_policy),
        "strategyRegistryValidation": validate_strategy_registry().to_dict(),
        "simulationConfig": {
            "runMode": effective_config["runMode"],
            "strategyId": effective_config["strategyId"],
            "strategyVersion": effective_config.get("strategyVersion"),
            "selectedInstrument": deepcopy(effective_config["selectedInstrument"]),
            "budgetUsd": safe_budget_usd,
            "allowedMarkets": list(effective_config["allowedMarkets"]),
            "allowedInstrumentClasses": list(effective_config["allowedInstrumentClasses"]),
            "cadence": deepcopy(effective_config["cadence"]),
            "strategyParameters": safe_strategy_parameters,
            "execution": deepcopy(effective_config["execution"]),
        },
        "configValidation": risk_decision["validations"]["simulationConfig"],
        "allocation": risk_decision["allocation"],
        "budget": {
            "allocatedUsd": 0.0,
            "remainingUsd": safe_budget_usd,
            "maxConfigurableBudgetUsd": effective_budget_policy["maxConfigurableBudgetUsd"],
        },
        "providerMetadata": build_provider_metadata_snapshot(),
        "positionContext": deepcopy(SYNTHETIC_POSITION_CONTEXT),
        "tradeLogEntry": {
            "tradeLogId": f"trade-log-{run_id}",
            "correlationId": correlation_id,
            "action": "simulated-skip",
            "strategyId": strategy["strategyId"] if strategy else effective_config["strategyId"],
            "strategyVersion": strategy["version"] if strategy else "unknown",
            "configHash": config_hash,
            "allocationId": risk_decision["allocation"]["allocationId"],
            "strategyAllocationId": risk_decision["allocation"]["strategyAllocationId"],
            "decision": "blocked",
            "riskDecision": "blocked",
            "reasonCode": risk_decision["vetoes"][0] if risk_decision["vetoes"] else "blocked",
            "vetoes": risk_decision["vetoes"],
            "budgetRemainingUsd": safe_budget_usd,
            "dataFreshness": risk_decision["riskState"]["data_freshness"],
            "accountIdentifiers": "redacted",
            "rawProviderPayloads": "absent",
            "providerCall": "not-attempted",
            "executionRoute": "absent",
        },
        "contract": SIMULATION_CONFIG_CONTRACT,
    }


def default_run_config(strategy_id: str = "dca-cash-reserve") -> dict[str, Any]:
    return default_simulation_config_for_strategy(strategy_id)
