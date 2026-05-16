from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime
from typing import Any

from money_maker_3000.contracts import (
    DEFAULT_ALLOCATION_POLICY,
    DEFAULT_BUDGET_POLICY,
    DEFAULT_SCHEDULE_POLICY,
    SIMULATION_CONFIG_CONTRACT,
    default_simulation_config_for_strategy,
    merge_simulation_config,
    utc_iso,
)
from money_maker_3000.providers import build_provider_metadata_snapshot
from money_maker_3000.risk import DEFAULT_RISK_POLICY, RiskInputState, evaluate_risk_gate
from money_maker_3000.strategies import strategy_by_id, validate_strategy_registry

CONFIG_VERSION = "0.1.0-sim"

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
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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

    return {
        "dtoVersion": "simulation-run.v1",
        "runId": f"sim-{evaluated_at}",
        "correlationId": f"corr-{config_hash[:16]}",
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
            "budgetUsd": effective_config["budgetUsd"],
            "allowedMarkets": list(effective_config["allowedMarkets"]),
            "allowedInstrumentClasses": list(effective_config["allowedInstrumentClasses"]),
            "cadence": deepcopy(effective_config["cadence"]),
            "execution": deepcopy(effective_config["execution"]),
        },
        "configValidation": risk_decision["validations"]["simulationConfig"],
        "allocation": risk_decision["allocation"],
        "budget": {
            "allocatedUsd": 0.0,
            "remainingUsd": effective_config.get("budgetUsd", effective_budget_policy["baseBudgetUsd"]),
            "maxConfigurableBudgetUsd": effective_budget_policy["maxConfigurableBudgetUsd"],
        },
        "providerMetadata": build_provider_metadata_snapshot(),
        "positionContext": deepcopy(SYNTHETIC_POSITION_CONTEXT),
        "tradeLogEntry": {
            "tradeLogId": f"trade-log-{evaluated_at}",
            "correlationId": f"corr-{config_hash[:16]}",
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
            "budgetRemainingUsd": effective_config.get("budgetUsd", effective_budget_policy["baseBudgetUsd"]),
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
