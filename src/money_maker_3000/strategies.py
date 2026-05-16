from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from money_maker_3000.contracts import (
    SIMULATION_CADENCES,
    SIMULATION_INSTRUMENT_CLASSES,
    SIMULATION_MARKET_INSTRUMENT_CLASS_RULES,
    SIMULATION_MARKETS,
    SIMULATION_STRATEGY_CONFIG_RULES,
    ValidationResult,
)

STRATEGY_STATUSES = ("simulation-only", "context-only")
BLOCKED_SIMULATION_STRATEGY_INSTRUMENT_CLASSES = ("FOREX",)
HIGH_FREQUENCY_HOLDING_PERIOD_PATTERN = re.compile(r"(second|minute|intraday|scalp|high-frequency)", re.I)
STRATEGY_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

STRATEGY_REGISTRY = [
    {
        "strategyId": "dca-cash-reserve",
        **SIMULATION_STRATEGY_CONFIG_RULES["dca-cash-reserve"],
        "expectedHoldingPeriod": "weeks-to-months",
    },
    {
        "strategyId": "threshold-rebalance",
        **SIMULATION_STRATEGY_CONFIG_RULES["threshold-rebalance"],
        "expectedHoldingPeriod": "weeks-to-months",
    },
    {
        "strategyId": "news-aware-watchlist",
        **SIMULATION_STRATEGY_CONFIG_RULES["news-aware-watchlist"],
        "expectedHoldingPeriod": "not-trading-from-news",
    },
]

for strategy in STRATEGY_REGISTRY:
    strategy["allowedMarkets"] = list(strategy["allowedMarkets"])
    strategy["allowedInstrumentClasses"] = list(strategy["allowedInstrumentClasses"])


def strategy_by_id(strategy_id: str) -> dict[str, Any] | None:
    for strategy in STRATEGY_REGISTRY:
        if strategy["strategyId"] == strategy_id:
            return deepcopy(strategy)
    return None


def _market_instrument_class_mismatches(markets: list[str], instrument_classes: list[str]) -> list[str]:
    supported = {
        instrument_class
        for market in markets
        for instrument_class in SIMULATION_MARKET_INSTRUMENT_CLASS_RULES.get(market, ())
    }
    return [instrument_class for instrument_class in instrument_classes if instrument_class not in supported]


def validate_strategy_registry(registry: list[dict[str, Any]] | None = None) -> ValidationResult:
    candidates = registry if registry is not None else STRATEGY_REGISTRY
    errors: list[str] = []
    seen_strategy_ids: set[str] = set()

    if not isinstance(candidates, list) or len(candidates) == 0:
        return ValidationResult(ok=False, errors=("strategy registry must be a non-empty predefined list",))

    for index, strategy in enumerate(candidates):
        label = strategy.get("strategyId", f"strategy at index {index}") if isinstance(strategy, dict) else f"strategy at index {index}"
        if not isinstance(strategy, dict):
            errors.append(f"{label} must be an object")
            continue

        strategy_id = strategy.get("strategyId")
        if not isinstance(strategy_id, str) or not strategy_id:
            errors.append(f"{label} must have a strategy id")
        else:
            if not STRATEGY_ID_PATTERN.fullmatch(strategy_id):
                errors.append(f"{label} strategy id must be kebab-case")
            if strategy_id in seen_strategy_ids:
                errors.append(f"{label} strategy id must be unique")
            seen_strategy_ids.add(strategy_id)

        if not isinstance(strategy.get("name"), str) or not strategy["name"]:
            errors.append(f"{label} must have a display name")
        if not isinstance(strategy.get("version"), str) or not strategy["version"]:
            errors.append(f"{label} must have a version")
        if strategy.get("status") not in STRATEGY_STATUSES:
            errors.append(f"{label} status must be simulation-only or context-only")
        if strategy.get("status") == "live":
            errors.append(f"{label} live strategies are not allowed")
        if strategy.get("cadence") not in SIMULATION_CADENCES:
            errors.append(f"{label} cadence must be low-frequency daily or weekly")

        allowed_markets = strategy.get("allowedMarkets")
        if not isinstance(allowed_markets, list) or len(allowed_markets) == 0:
            errors.append(f"{label} allowed markets must be a non-empty list")
            allowed_markets = []
        else:
            unknown_markets = [market for market in allowed_markets if market not in SIMULATION_MARKETS]
            if unknown_markets:
                errors.append(f"{label} has unknown markets: {', '.join(unknown_markets)}")

        allowed_classes = strategy.get("allowedInstrumentClasses")
        if not isinstance(allowed_classes, list) or len(allowed_classes) == 0:
            errors.append(f"{label} allowed instrument classes must be a non-empty list")
            allowed_classes = []
        else:
            unknown_classes = [
                instrument_class
                for instrument_class in allowed_classes
                if instrument_class not in SIMULATION_INSTRUMENT_CLASSES
            ]
            if unknown_classes:
                errors.append(f"{label} has unknown instrument classes: {', '.join(unknown_classes)}")
            blocked = [
                instrument_class
                for instrument_class in allowed_classes
                if instrument_class in BLOCKED_SIMULATION_STRATEGY_INSTRUMENT_CLASSES
            ]
            if strategy.get("status") == "simulation-only" and blocked:
                errors.append(
                    f"{label} simulation strategy includes blocked execution instrument classes: {', '.join(blocked)}"
                )

        mismatches = _market_instrument_class_mismatches(allowed_markets, allowed_classes)
        if mismatches:
            errors.append(
                f"{label} instrument classes are not supported by its allowed markets: {', '.join(mismatches)}"
            )

        holding_period = strategy.get("expectedHoldingPeriod")
        if not isinstance(holding_period, str) or not holding_period:
            errors.append(f"{label} must describe an expected holding period")
        elif HIGH_FREQUENCY_HOLDING_PERIOD_PATTERN.search(holding_period):
            errors.append(f"{label} expected holding period must remain low-frequency")

    return ValidationResult(ok=not errors, errors=tuple(errors))
