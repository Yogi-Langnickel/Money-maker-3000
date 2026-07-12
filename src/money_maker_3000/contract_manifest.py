from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Sequence

from money_maker_3000.contracts import (
    BLOCKED_SIMULATION_INSTRUMENT_CLASSES,
    DEFAULT_BUDGET_POLICY,
    MAX_SIMULATION_DECISIONS_PER_DAY,
    MINIMUM_SIMULATION_EVALUATION_INTERVAL_MINUTES,
    SIMULATION_ALLOWED_RUN_MODES,
    SIMULATION_CADENCES,
    SIMULATION_CONTRACT_SOURCE,
    SIMULATION_CONTRACT_VERSION,
    SIMULATION_DISABLED_RUN_MODES,
    SIMULATION_INSTRUMENT_CLASSES,
    SIMULATION_MARKET_INSTRUMENT_CLASS_RULES,
    SIMULATION_MARKETS,
    SIMULATION_RUN_MODE_POLICY,
    SIMULATION_STRATEGY_CONFIG_RULES,
    SIMULATION_STRATEGY_PARAMETER_SCHEMAS,
)

MANIFEST_SCHEMA_VERSION = "dashboard-simulation-contract.v1"
DEFAULT_MANIFEST_PATH = Path("contracts/dashboard-simulation-contract.json")


def build_dashboard_contract_manifest() -> dict[str, Any]:
    strategy_rules = {}
    for strategy_id, rule in SIMULATION_STRATEGY_CONFIG_RULES.items():
        strategy_rules[strategy_id] = {
            **deepcopy(rule),
            "allowedMarkets": list(rule["allowedMarkets"]),
            "allowedInstrumentClasses": list(rule["allowedInstrumentClasses"]),
            "parameterSchema": deepcopy(SIMULATION_STRATEGY_PARAMETER_SCHEMAS[strategy_id]),
        }

    return {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "source": SIMULATION_CONTRACT_SOURCE,
        "version": SIMULATION_CONTRACT_VERSION,
        "markets": list(SIMULATION_MARKETS),
        "instrumentClasses": list(SIMULATION_INSTRUMENT_CLASSES),
        "blockedInstrumentClasses": list(BLOCKED_SIMULATION_INSTRUMENT_CLASSES),
        "runModes": list(SIMULATION_ALLOWED_RUN_MODES),
        "disabledRunModes": list(SIMULATION_DISABLED_RUN_MODES),
        "runModePolicy": deepcopy(SIMULATION_RUN_MODE_POLICY),
        "marketInstrumentClassRules": {
            market: list(classes)
            for market, classes in SIMULATION_MARKET_INSTRUMENT_CLASS_RULES.items()
        },
        "cadences": list(SIMULATION_CADENCES),
        "minimumEvaluationIntervalMinutes": MINIMUM_SIMULATION_EVALUATION_INTERVAL_MINUTES,
        "maxDecisionsPerDay": MAX_SIMULATION_DECISIONS_PER_DAY,
        "selectableBudgetsUsd": list(DEFAULT_BUDGET_POLICY["selectableBudgetsUsd"]),
        "maxConfigurableBudgetUsd": DEFAULT_BUDGET_POLICY["maxConfigurableBudgetUsd"],
        "strategyIds": list(SIMULATION_STRATEGY_CONFIG_RULES),
        "strategyRules": strategy_rules,
        "safety": {
            "providerCalls": "blocked",
            "credentials": "absent",
            "accountData": "absent",
            "orderPreview": "blocked",
            "demoExecution": "blocked",
            "liveExecution": "blocked",
            "arbitraryStrategyCode": "blocked",
        },
    }


def render_dashboard_contract_manifest() -> str:
    return json.dumps(build_dashboard_contract_manifest(), indent=2, sort_keys=True) + "\n"


def write_dashboard_contract_manifest(path: Path = DEFAULT_MANIFEST_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_dashboard_contract_manifest(), encoding="utf-8")


def check_dashboard_contract_manifest(path: Path = DEFAULT_MANIFEST_PATH) -> bool:
    try:
        existing = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return existing == render_dashboard_contract_manifest()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate or validate the redacted dashboard contract manifest.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="write the canonical manifest")
    mode.add_argument("--check", action="store_true", help="fail when the committed manifest has drifted")
    parser.add_argument("--path", type=Path, default=DEFAULT_MANIFEST_PATH)
    args = parser.parse_args(argv)

    if args.write:
        write_dashboard_contract_manifest(args.path)
        return 0
    if check_dashboard_contract_manifest(args.path):
        return 0
    parser.error(f"dashboard contract manifest drift detected: run with --write --path {args.path}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
