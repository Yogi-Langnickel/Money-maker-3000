from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Any

SIMULATION_CONTRACT_SOURCE = "Money-maker-3000/src/money_maker_3000/contracts.py"
SIMULATION_CONTRACT_VERSION = "0.1.0-sim"

SIMULATION_MARKETS = ("US_EQUITIES", "AU_EQUITIES", "FOREX", "COMMODITIES")
SIMULATION_INSTRUMENT_CLASSES = ("EQUITY", "ETF", "FOREX", "COMMODITY")
BLOCKED_SIMULATION_INSTRUMENT_CLASSES = ("CFD", "CRYPTO", "DERIVATIVE", "OPTION")
SIMULATION_ALLOWED_RUN_MODES = ("backtest",)
SIMULATION_DISABLED_RUN_MODES = ("execute", "trade", "trading")
SIMULATION_RUN_MODES = SIMULATION_ALLOWED_RUN_MODES + SIMULATION_DISABLED_RUN_MODES
SIMULATION_CADENCES = ("daily", "weekly")
MINIMUM_SIMULATION_EVALUATION_INTERVAL_MINUTES = 240
MAX_SIMULATION_DECISIONS_PER_DAY = 3

SIMULATION_RUN_MODE_POLICY = {
    "backtest": {
        "enabled": True,
        "providerCalls": "blocked",
        "historicalInputs": "offline-fixture-only",
        "accountData": "absent",
        "executionRoutes": "absent",
    },
    "execute": {
        "enabled": False,
        "providerCalls": "blocked",
        "demoExecution": "blocked",
        "liveExecution": "blocked",
        "reason": "demo execution requires a separate review and explicit approval",
    },
    "trade": {
        "enabled": False,
        "providerCalls": "blocked",
        "demoExecution": "blocked",
        "liveExecution": "blocked",
        "reason": "trading aliases are disabled; only offline backtest mode is allowed",
    },
    "trading": {
        "enabled": False,
        "providerCalls": "blocked",
        "demoExecution": "blocked",
        "liveExecution": "blocked",
        "reason": "trading aliases are disabled; only offline backtest mode is allowed",
    },
}

SIMULATION_MARKET_INSTRUMENT_CLASS_RULES = {
    "US_EQUITIES": ("EQUITY", "ETF"),
    "AU_EQUITIES": ("EQUITY", "ETF"),
    "FOREX": ("FOREX",),
    "COMMODITIES": ("COMMODITY", "ETF"),
}

SIMULATION_STRATEGY_CONFIG_RULES = {
    "dca-cash-reserve": {
        "name": "Cash-reserved DCA",
        "version": "0.1.0-sim",
        "status": "simulation-only",
        "cadence": "daily",
        "allowedMarkets": ("US_EQUITIES", "AU_EQUITIES"),
        "allowedInstrumentClasses": ("EQUITY", "ETF"),
    },
    "threshold-rebalance": {
        "name": "Threshold rebalance",
        "version": "0.1.0-sim",
        "status": "simulation-only",
        "cadence": "weekly",
        "allowedMarkets": ("US_EQUITIES", "AU_EQUITIES", "COMMODITIES"),
        "allowedInstrumentClasses": ("EQUITY", "ETF", "COMMODITY"),
    },
    "volatility-band-accumulator": {
        "name": "Volatility band accumulator",
        "version": "0.1.0-sim",
        "status": "simulation-only",
        "cadence": "daily",
        "allowedMarkets": ("US_EQUITIES", "AU_EQUITIES"),
        "allowedInstrumentClasses": ("EQUITY", "ETF"),
    },
    "slow-trend-allocation": {
        "name": "Slow trend allocation",
        "version": "0.1.0-sim",
        "status": "simulation-only",
        "cadence": "weekly",
        "allowedMarkets": ("US_EQUITIES", "AU_EQUITIES"),
        "allowedInstrumentClasses": ("EQUITY", "ETF"),
    },
    "news-aware-watchlist": {
        "name": "News-aware watchlist",
        "version": "0.1.0-plan",
        "status": "context-only",
        "cadence": "daily",
        "allowedMarkets": ("US_EQUITIES", "AU_EQUITIES", "FOREX", "COMMODITIES"),
        "allowedInstrumentClasses": ("EQUITY", "ETF", "FOREX", "COMMODITY"),
    },
}

SIMULATION_STRATEGY_PARAMETER_SCHEMAS = {
    "dca-cash-reserve": {
        "fixedOrderUsd": {"type": "number", "minimum": 25.0, "maximum": 250.0, "default": 100.0},
        "orderFractionPct": {"type": "number", "minimum": 0.01, "maximum": 0.25, "default": 0.1},
        "cashReserveFloorUsd": {"type": "number", "minimum": 100.0, "maximum": 1000.0, "default": 100.0},
        "maxOrdersPerWeek": {"type": "integer", "minimum": 1, "maximum": 3, "default": 2},
        "cooldownDays": {"type": "integer", "minimum": 1, "maximum": 14, "default": 1},
    },
    "threshold-rebalance": {
        "targetWeights": {
            "type": "weights",
            "default": {"SPY": 0.7, "GLD": 0.3},
            "maxSymbols": 8,
        },
        "rebalanceThresholdPct": {"type": "number", "minimum": 1.0, "maximum": 20.0, "default": 5.0},
        "maxOrderUsd": {"type": "number", "minimum": 25.0, "maximum": 250.0, "default": 250.0},
        "minCashReserveUsd": {"type": "number", "minimum": 100.0, "maximum": 1000.0, "default": 100.0},
        "maxOpenPositions": {"type": "integer", "minimum": 1, "maximum": 3, "default": 3},
    },
    "volatility-band-accumulator": {
        "lookbackDays": {"type": "integer", "minimum": 5, "maximum": 252, "default": 20},
        "dropTriggerPct": {"type": "number", "minimum": 1.0, "maximum": 15.0, "default": 3.0},
        "maxOrderUsd": {"type": "number", "minimum": 25.0, "maximum": 250.0, "default": 150.0},
        "maxOrdersPerWeek": {"type": "integer", "minimum": 1, "maximum": 3, "default": 1},
        "cooldownDays": {"type": "integer", "minimum": 1, "maximum": 14, "default": 3},
        "cashReserveFloorUsd": {"type": "number", "minimum": 100.0, "maximum": 1000.0, "default": 150.0},
    },
    "slow-trend-allocation": {
        "shortLookbackDays": {"type": "integer", "minimum": 10, "maximum": 100, "default": 50},
        "longLookbackDays": {"type": "integer", "minimum": 60, "maximum": 252, "default": 200},
        "confirmationBars": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3},
        "orderFractionPct": {"type": "number", "minimum": 0.01, "maximum": 0.2, "default": 0.1},
        "maxOrderUsd": {"type": "number", "minimum": 25.0, "maximum": 250.0, "default": 150.0},
    },
    "news-aware-watchlist": {
        "contextTtlDays": {"type": "integer", "minimum": 1, "maximum": 14, "default": 3},
        "sourceLabels": {"type": "stringList", "maxItems": 8, "default": ["market-calendar", "issuer-filings"]},
        "blackoutTags": {"type": "stringList", "maxItems": 12, "default": ["earnings", "regulatory-halt"]},
        "severityThreshold": {"type": "enum", "values": ["low", "medium", "high"], "default": "medium"},
    },
}

DEFAULT_SIMULATION_CONFIG = {
    "runMode": "backtest",
    "strategyId": "dca-cash-reserve",
    "selectedInstrument": {
        "symbol": "SPY",
        "market": "US_EQUITIES",
        "instrumentClass": "ETF",
    },
    "budgetUsd": 1000.0,
    "allowedMarkets": ["US_EQUITIES", "AU_EQUITIES"],
    "allowedInstrumentClasses": ["EQUITY", "ETF"],
    "cadence": {
        "mode": "low-frequency-only",
        "frequency": "daily",
        "minimumEvaluationIntervalMinutes": MINIMUM_SIMULATION_EVALUATION_INTERVAL_MINUTES,
        "maxDecisionsPerDay": MAX_SIMULATION_DECISIONS_PER_DAY,
    },
    "strategyParameters": {},
    "execution": {
        "mode": "simulation-only",
        "liveTrading": "blocked",
        "demoTrading": "blocked",
        "providerCalls": "blocked",
        "leverage": 1,
        "shorts": "blocked",
        "copyTrading": "blocked",
    },
}

DEFAULT_BUDGET_POLICY = {
    "baseBudgetUsd": 1000.0,
    "selectableBudgetsUsd": [500.0, 1000.0, 1500.0, 2500.0],
    "maxConfigurableBudgetUsd": 2500.0,
    "dailyLossStopUsd": 50.0,
    "weeklyLossStopUsd": 150.0,
    "maxOpenPositions": 3,
    "leverage": 1,
    "shorts": "blocked",
    "copyTrading": "blocked",
}

DEFAULT_ALLOCATION_POLICY = {
    "allocationId": "alloc-sim-default",
    "strategyAllocationIds": {
        "dca-cash-reserve": "alloc-sim-dca",
        "threshold-rebalance": "alloc-sim-rebalance",
        "volatility-band-accumulator": "alloc-sim-volatility-band",
        "slow-trend-allocation": "alloc-sim-slow-trend",
        "news-aware-watchlist": "alloc-sim-watchlist",
    },
    "botAllocationUsd": 1000.0,
    "reservedUsd": 100.0,
    "availableUsd": 900.0,
    "maxOrderUsd": 250.0,
    "maxBotAllocationUsd": 2500.0,
    "providerDemoBalanceUsd": None,
    "providerBalanceUse": "ignored-for-budget",
    "accountBalancePersistence": "redacted",
}

DEFAULT_SCHEDULE_POLICY = {
    "mode": "low-frequency-only",
    "minimumEvaluationIntervalMinutes": 240,
    "defaultCadence": "daily",
    "highFrequencyTrading": "blocked",
    "maxDecisionsPerDay": 3,
}

SIMULATION_CONFIG_CONTRACT = {
    "source": SIMULATION_CONTRACT_SOURCE,
    "version": SIMULATION_CONTRACT_VERSION,
    "markets": list(SIMULATION_MARKETS),
    "instrumentClasses": list(SIMULATION_INSTRUMENT_CLASSES),
    "blockedInstrumentClasses": list(BLOCKED_SIMULATION_INSTRUMENT_CLASSES),
    "runModes": list(SIMULATION_ALLOWED_RUN_MODES),
    "allowedRunModes": list(SIMULATION_ALLOWED_RUN_MODES),
    "disabledRunModes": {
        mode: deepcopy(policy)
        for mode, policy in SIMULATION_RUN_MODE_POLICY.items()
        if not policy.get("enabled")
    },
    "runModePolicy": deepcopy(SIMULATION_RUN_MODE_POLICY),
    "marketInstrumentClassRules": {
        market: list(classes) for market, classes in SIMULATION_MARKET_INSTRUMENT_CLASS_RULES.items()
    },
    "cadences": list(SIMULATION_CADENCES),
    "minimumEvaluationIntervalMinutes": MINIMUM_SIMULATION_EVALUATION_INTERVAL_MINUTES,
    "maxDecisionsPerDay": MAX_SIMULATION_DECISIONS_PER_DAY,
    "strategyRules": {
        strategy_id: {
            **rule,
            "allowedMarkets": list(rule["allowedMarkets"]),
            "allowedInstrumentClasses": list(rule["allowedInstrumentClasses"]),
            "parameterSchema": deepcopy(SIMULATION_STRATEGY_PARAMETER_SCHEMAS[strategy_id]),
        }
        for strategy_id, rule in SIMULATION_STRATEGY_CONFIG_RULES.items()
    },
}

SYMBOL_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,14}$")


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


def utc_iso(value: datetime | None = None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def default_simulation_config_for_strategy(strategy_id: str = "dca-cash-reserve") -> dict[str, Any]:
    rule = SIMULATION_STRATEGY_CONFIG_RULES.get(strategy_id, SIMULATION_STRATEGY_CONFIG_RULES["dca-cash-reserve"])
    config = deepcopy(DEFAULT_SIMULATION_CONFIG)
    config["strategyId"] = strategy_id
    config["allowedMarkets"] = list(rule["allowedMarkets"])
    config["allowedInstrumentClasses"] = list(rule["allowedInstrumentClasses"])
    config["cadence"]["frequency"] = rule["cadence"]
    config["strategyParameters"] = default_strategy_parameters_for_strategy(strategy_id)
    return config


def default_strategy_parameters_for_strategy(strategy_id: str = "dca-cash-reserve") -> dict[str, Any]:
    schema = SIMULATION_STRATEGY_PARAMETER_SCHEMAS.get(
        strategy_id,
        SIMULATION_STRATEGY_PARAMETER_SCHEMAS["dca-cash-reserve"],
    )
    return {name: deepcopy(rule["default"]) for name, rule in schema.items()}


def merge_simulation_config(strategy_id: str, simulation_config: dict[str, Any] | None = None) -> dict[str, Any]:
    incoming = simulation_config or {}
    requested_strategy_id = incoming.get("strategyId", strategy_id)
    base = default_simulation_config_for_strategy(requested_strategy_id)
    merged = deepcopy(base)
    for key, value in incoming.items():
        if key in {"selectedInstrument", "cadence", "execution", "strategyParameters"} and isinstance(value, dict):
            merged[key].update(value)
        else:
            merged[key] = value
    merged["strategyId"] = requested_strategy_id
    return merged


def validate_run_mode(run_mode: str | None) -> ValidationResult:
    if run_mode in {"execute", "trade", "trading"}:
        return ValidationResult(
            ok=False,
            errors=("execution mode is disabled; only backtest mode is currently allowed",),
        )
    if run_mode not in SIMULATION_ALLOWED_RUN_MODES:
        return ValidationResult(
            ok=False,
            errors=(f"run mode must be one of: {', '.join(SIMULATION_ALLOWED_RUN_MODES)}",),
        )
    return ValidationResult(ok=True)


def _is_plain_dict(value: Any) -> bool:
    return isinstance(value, dict)


def _unknown(values: Any, allowed: tuple[str, ...]) -> list[str]:
    if not isinstance(values, list):
        return []
    return [value for value in values if value not in allowed]


def _strategy_blocked(values: Any, allowed: Any) -> list[str]:
    if not isinstance(values, list) or not isinstance(allowed, list):
        return []
    return [value for value in values if value not in allowed]


def _market_instrument_class_mismatches(markets: Any, instrument_classes: Any) -> list[str]:
    if not isinstance(markets, list) or not isinstance(instrument_classes, list):
        return []
    supported = {
        instrument_class
        for market in markets
        for instrument_class in SIMULATION_MARKET_INSTRUMENT_CLASS_RULES.get(market, ())
    }
    return [instrument_class for instrument_class in instrument_classes if instrument_class not in supported]


def validate_strategy_parameters(strategy_id: str, parameters: dict[str, Any] | None = None) -> ValidationResult:
    schema = SIMULATION_STRATEGY_PARAMETER_SCHEMAS.get(strategy_id)
    if schema is None:
        return ValidationResult(ok=False, errors=("strategy parameter schema is missing",))
    if parameters is None:
        parameters = default_strategy_parameters_for_strategy(strategy_id)
    if not _is_plain_dict(parameters):
        return ValidationResult(ok=False, errors=("strategy parameters must be an object",))

    errors: list[str] = []
    unknown_parameters = [name for name in parameters if name not in schema]
    if unknown_parameters:
        errors.append("unsupported strategy parameters are not allowed")

    for name, rule in schema.items():
        value = parameters.get(name, rule["default"])
        parameter_type = rule["type"]
        label = f"strategy parameter {name}"
        if parameter_type == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                errors.append(f"{label} must be an integer")
                continue
            if value < rule["minimum"] or value > rule["maximum"]:
                errors.append(f"{label} must be between {rule['minimum']} and {rule['maximum']}")
        elif parameter_type == "number":
            if not _is_finite_number(value):
                errors.append(f"{label} must be a finite number")
                continue
            if float(value) < rule["minimum"] or float(value) > rule["maximum"]:
                errors.append(f"{label} must be between {rule['minimum']} and {rule['maximum']}")
        elif parameter_type == "enum":
            if value not in rule["values"]:
                errors.append(f"{label} must be one of: {', '.join(rule['values'])}")
        elif parameter_type == "stringList":
            if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
                errors.append(f"{label} must be a list of non-empty strings")
                continue
            if len(value) > rule["maxItems"]:
                errors.append(f"{label} must contain at most {rule['maxItems']} items")
        elif parameter_type == "weights":
            errors.extend(_validate_strategy_weight_parameters(label, value, rule))
        else:
            errors.append(f"{label} has unsupported schema type")

    if strategy_id == "slow-trend-allocation":
        short_window = parameters.get("shortLookbackDays", schema["shortLookbackDays"]["default"])
        long_window = parameters.get("longLookbackDays", schema["longLookbackDays"]["default"])
        if isinstance(short_window, int) and isinstance(long_window, int) and short_window >= long_window:
            errors.append("strategy parameter shortLookbackDays must be less than longLookbackDays")

    return ValidationResult(ok=not errors, errors=tuple(errors))


def _validate_strategy_weight_parameters(label: str, value: Any, rule: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not _is_plain_dict(value) or not value:
        return [f"{label} must be an object of symbol weights"]
    if len(value) > rule["maxSymbols"]:
        errors.append(f"{label} must contain at most {rule['maxSymbols']} symbols")
    total_weight = 0.0
    for symbol, weight in value.items():
        if not isinstance(symbol, str) or not SYMBOL_PATTERN.fullmatch(symbol):
            errors.append(f"{label} symbols must be uppercase market symbols")
        if not _is_finite_number(weight) or float(weight) <= 0:
            errors.append(f"{label} weights must be positive finite numbers")
        else:
            total_weight += float(weight)
    if value and round(total_weight, 4) != 1.0:
        errors.append(f"{label} weights must sum to 1.0")
    return errors


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(float(value))


def safe_positive_usd_for_output(value: Any, default: float) -> float:
    if _is_finite_number(value) and float(value) > 0:
        return float(value)
    return float(default)


def safe_non_negative_usd_for_output(value: Any, default: float) -> float:
    if _is_finite_number(value) and float(value) >= 0:
        return float(value)
    return float(default)


def safe_allocation_policy_for_output(policy: dict[str, Any] | None = None) -> dict[str, Any]:
    source = policy if isinstance(policy, dict) else {}
    defaults = DEFAULT_ALLOCATION_POLICY
    strategy_allocation_ids = source.get("strategyAllocationIds")
    if not isinstance(strategy_allocation_ids, dict):
        strategy_allocation_ids = defaults["strategyAllocationIds"]
    bot_allocation = safe_positive_usd_for_output(source.get("botAllocationUsd"), defaults["botAllocationUsd"])
    reserved = safe_non_negative_usd_for_output(source.get("reservedUsd"), defaults["reservedUsd"])
    available_default = max(bot_allocation - reserved, 0.0)
    return {
        "allocationId": source.get("allocationId") if isinstance(source.get("allocationId"), str) else defaults["allocationId"],
        "strategyAllocationIds": deepcopy(strategy_allocation_ids),
        "botAllocationUsd": bot_allocation,
        "reservedUsd": reserved,
        "availableUsd": safe_non_negative_usd_for_output(source.get("availableUsd"), available_default),
        "maxOrderUsd": safe_positive_usd_for_output(source.get("maxOrderUsd"), defaults["maxOrderUsd"]),
        "maxBotAllocationUsd": safe_positive_usd_for_output(source.get("maxBotAllocationUsd"), defaults["maxBotAllocationUsd"]),
        "providerDemoBalanceUsd": None,
        "providerBalanceUse": "ignored-for-budget",
        "accountBalancePersistence": "redacted",
    }


def safe_strategy_parameters_for_output(strategy_id: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
    defaults = default_strategy_parameters_for_strategy(strategy_id)
    result = validate_strategy_parameters(strategy_id, parameters)
    if not result.ok:
        return defaults
    source = parameters or defaults
    return {name: deepcopy(source.get(name, default_value)) for name, default_value in defaults.items()}


def validate_budget_policy(policy: dict[str, Any] = DEFAULT_BUDGET_POLICY) -> ValidationResult:
    errors: list[str] = []
    if policy["baseBudgetUsd"] not in policy["selectableBudgetsUsd"]:
        errors.append("base budget must be one of the selectable budget options")
    if policy["baseBudgetUsd"] > policy["maxConfigurableBudgetUsd"]:
        errors.append("base budget cannot exceed the maximum configurable budget")
    if policy["dailyLossStopUsd"] <= 0 or policy["weeklyLossStopUsd"] <= 0:
        errors.append("loss stops must be positive")
    if policy["leverage"] != 1:
        errors.append("leverage must remain 1 in simulation")
    if policy.get("shorts") != "blocked":
        errors.append("shorts must be blocked")
    if policy.get("copyTrading") != "blocked":
        errors.append("copy trading must be blocked")
    return ValidationResult(ok=not errors, errors=tuple(errors))


def build_allocation_policy(
    bot_allocation_usd: float | None = None,
    reserved_usd: float | None = None,
    available_usd: float | None = None,
    max_order_usd: float | None = None,
    provider_demo_balance_usd: float | None = None,
) -> dict[str, Any]:
    policy = deepcopy(DEFAULT_ALLOCATION_POLICY)
    if bot_allocation_usd is not None:
        policy["botAllocationUsd"] = float(bot_allocation_usd)
    if reserved_usd is not None:
        policy["reservedUsd"] = float(reserved_usd)
    if available_usd is not None:
        policy["availableUsd"] = float(available_usd)
    elif bot_allocation_usd is not None or reserved_usd is not None:
        policy["availableUsd"] = float(policy["botAllocationUsd"]) - float(policy["reservedUsd"])
    if max_order_usd is not None:
        policy["maxOrderUsd"] = float(max_order_usd)
    if provider_demo_balance_usd is not None:
        policy["providerDemoBalanceUsd"] = float(provider_demo_balance_usd)
    return policy


def validate_allocation_policy(policy: dict[str, Any] = DEFAULT_ALLOCATION_POLICY) -> ValidationResult:
    errors: list[str] = []
    bot_allocation = policy.get("botAllocationUsd")
    reserved = policy.get("reservedUsd")
    available = policy.get("availableUsd")
    max_order = policy.get("maxOrderUsd")
    provider_balance = policy.get("providerDemoBalanceUsd")
    if not _is_finite_number(bot_allocation) or float(bot_allocation) <= 0:
        errors.append("bot allocation must be a positive finite USD amount")
    elif bot_allocation > policy.get("maxBotAllocationUsd", 0):
        errors.append("bot allocation cannot exceed the maximum bot allocation")
    if not _is_finite_number(reserved) or float(reserved) < 0:
        errors.append("reserved allocation must be a non-negative finite USD amount")
    if not _is_finite_number(available) or float(available) < 0:
        errors.append("available allocation must be a non-negative finite USD amount")
    if isinstance(bot_allocation, (int, float)) and isinstance(reserved, (int, float)):
        expected_available = round(float(bot_allocation) - float(reserved), 10)
        if isinstance(available, (int, float)) and round(float(available), 10) != expected_available:
            errors.append("available allocation must equal bot allocation minus reserved allocation")
    if not _is_finite_number(max_order) or float(max_order) <= 0:
        errors.append("max order must be a positive finite USD amount")
    elif isinstance(available, (int, float)) and max_order > available:
        errors.append("max order cannot exceed available allocation")
    if not isinstance(policy.get("strategyAllocationIds"), dict) or not policy["strategyAllocationIds"]:
        errors.append("strategy allocation ids are required")
    if provider_balance is not None and (not _is_finite_number(provider_balance) or float(provider_balance) <= 0):
        errors.append("provider demo balance must be positive and finite when supplied")
    if policy.get("providerBalanceUse") != "ignored-for-budget":
        errors.append("provider account balance must not drive bot budget")
    if policy.get("accountBalancePersistence") != "redacted":
        errors.append("provider account balance persistence must remain redacted")
    return ValidationResult(ok=not errors, errors=tuple(errors))


def validate_schedule_policy(policy: dict[str, Any] = DEFAULT_SCHEDULE_POLICY) -> ValidationResult:
    errors: list[str] = []
    if policy.get("highFrequencyTrading") != "blocked":
        errors.append("high-frequency trading must be blocked")
    if policy.get("minimumEvaluationIntervalMinutes", 0) < 240:
        errors.append("minimum evaluation interval must be at least 240 minutes")
    if policy.get("maxDecisionsPerDay", 0) > 3:
        errors.append("max decisions per day must be at most 3")
    return ValidationResult(ok=not errors, errors=tuple(errors))


def validate_simulation_config(
    config: dict[str, Any] | None = None,
    *,
    strategy_registry: list[dict[str, Any]] | None = None,
    budget_policy: dict[str, Any] | None = None,
    allocation_policy: dict[str, Any] | None = None,
    schedule_policy: dict[str, Any] | None = None,
) -> ValidationResult:
    from money_maker_3000.strategies import STRATEGY_REGISTRY

    effective_config = config or DEFAULT_SIMULATION_CONFIG
    registry = strategy_registry or STRATEGY_REGISTRY
    budget_policy = budget_policy or DEFAULT_BUDGET_POLICY
    allocation_policy = allocation_policy or DEFAULT_ALLOCATION_POLICY
    schedule_policy = schedule_policy or DEFAULT_SCHEDULE_POLICY
    errors: list[str] = []
    warnings: list[str] = []

    if not _is_plain_dict(effective_config):
        return ValidationResult(ok=False, errors=("simulation config must be an object",))

    strategy = next((candidate for candidate in registry if candidate["strategyId"] == effective_config.get("strategyId")), None)
    if strategy is None:
        errors.append("strategy must come from the predefined registry")
    elif strategy.get("status") == "live":
        errors.append("live strategies are not allowed")
    else:
        parameter_result = validate_strategy_parameters(
            strategy["strategyId"],
            effective_config.get("strategyParameters"),
        )
        errors.extend(parameter_result.errors)

    run_mode_result = validate_run_mode(effective_config.get("runMode"))
    errors.extend(run_mode_result.errors)

    selected = effective_config.get("selectedInstrument")
    if not _is_plain_dict(selected):
        errors.append("selected instrument is required")
    else:
        if not SYMBOL_PATTERN.fullmatch(str(selected.get("symbol", ""))):
            errors.append("selected instrument symbol must be an uppercase market symbol")
        if selected.get("market") not in SIMULATION_MARKETS:
            errors.append("selected instrument market is unsupported")
        if selected.get("instrumentClass") not in SIMULATION_INSTRUMENT_CLASSES:
            errors.append("selected instrument class is unsupported")
        if selected.get("instrumentClass") in BLOCKED_SIMULATION_INSTRUMENT_CLASSES:
            errors.append("selected instrument class is blocked")

    budget = effective_config.get("budgetUsd")
    if not _is_finite_number(budget) or float(budget) <= 0:
        errors.append("budget must be a positive finite USD amount")
    else:
        if budget not in budget_policy["selectableBudgetsUsd"]:
            errors.append("budget must be one of the selectable budget options")
        if budget > budget_policy["maxConfigurableBudgetUsd"]:
            errors.append("budget cannot exceed the maximum configurable budget")
        if budget > allocation_policy["botAllocationUsd"]:
            errors.append("budget cannot exceed the internal bot allocation")

    allowed_markets = effective_config.get("allowedMarkets")
    if not isinstance(allowed_markets, list) or len(allowed_markets) == 0:
        errors.append("allowed markets must be a non-empty list")
    unknown_markets = _unknown(allowed_markets, SIMULATION_MARKETS)
    if unknown_markets:
        errors.append(f"unsupported markets: {', '.join(unknown_markets)}")

    allowed_classes = effective_config.get("allowedInstrumentClasses")
    if not isinstance(allowed_classes, list) or len(allowed_classes) == 0:
        errors.append("allowed instrument classes must be a non-empty list")
    unknown_classes = _unknown(allowed_classes, SIMULATION_INSTRUMENT_CLASSES)
    if unknown_classes:
        errors.append(f"unsupported instrument classes: {', '.join(unknown_classes)}")

    blocked_classes = [
        instrument_class
        for instrument_class in (allowed_classes or [])
        if instrument_class in BLOCKED_SIMULATION_INSTRUMENT_CLASSES
    ]
    if blocked_classes:
        errors.append(f"blocked instrument classes configured: {', '.join(blocked_classes)}")

    mismatches = _market_instrument_class_mismatches(allowed_markets, allowed_classes)
    if mismatches:
        errors.append(f"instrument classes not supported by selected markets: {', '.join(mismatches)}")

    if _is_plain_dict(selected):
        selected_market = selected.get("market")
        selected_class = selected.get("instrumentClass")
        selected_market_classes = SIMULATION_MARKET_INSTRUMENT_CLASS_RULES.get(selected_market, ())
        if isinstance(allowed_markets, list) and selected_market not in allowed_markets:
            errors.append("selected instrument market must be included in allowed markets")
        if isinstance(allowed_classes, list) and selected_class not in allowed_classes:
            errors.append("selected instrument class must be included in allowed instrument classes")
        if selected_market in SIMULATION_MARKETS and selected_class in SIMULATION_INSTRUMENT_CLASSES:
            if selected_class not in selected_market_classes:
                errors.append("selected instrument class is not supported by its market")

    if strategy is not None:
        blocked_markets = _strategy_blocked(allowed_markets, strategy["allowedMarkets"])
        if blocked_markets:
            errors.append(
                f"allowed markets are not allowed for {strategy['strategyId']}: {', '.join(blocked_markets)}"
            )
        blocked_strategy_classes = _strategy_blocked(allowed_classes, strategy["allowedInstrumentClasses"])
        if blocked_strategy_classes:
            errors.append(
                "allowed instrument classes are not allowed for "
                f"{strategy['strategyId']}: {', '.join(blocked_strategy_classes)}"
            )
        if _is_plain_dict(selected):
            if selected.get("market") not in strategy["allowedMarkets"]:
                errors.append(f"selected instrument market is not allowed for {strategy['strategyId']}")
            if selected.get("instrumentClass") not in strategy["allowedInstrumentClasses"]:
                errors.append(f"selected instrument class is not allowed for {strategy['strategyId']}")

    cadence = effective_config.get("cadence")
    if not _is_plain_dict(cadence):
        errors.append("cadence policy is required")
    else:
        if cadence.get("mode") != "low-frequency-only":
            errors.append("cadence mode must remain low-frequency-only")
        if not isinstance(cadence.get("frequency"), str):
            errors.append("cadence frequency is required")
        elif strategy and cadence["frequency"] != strategy["cadence"]:
            errors.append(f"cadence frequency is not allowed for {strategy['strategyId']}")
        required_minimum = schedule_policy["minimumEvaluationIntervalMinutes"]
        if not isinstance(cadence.get("minimumEvaluationIntervalMinutes"), (int, float)):
            errors.append(f"minimum evaluation interval must be at least {required_minimum} minutes")
        elif cadence["minimumEvaluationIntervalMinutes"] < required_minimum:
            errors.append(f"minimum evaluation interval must be at least {required_minimum} minutes")
        max_per_day = cadence.get("maxDecisionsPerDay")
        allowed_max = schedule_policy["maxDecisionsPerDay"]
        if not isinstance(max_per_day, int) or max_per_day < 1 or max_per_day > allowed_max:
            errors.append(f"max decisions per day must be between 1 and {allowed_max}")

    execution = effective_config.get("execution")
    if not _is_plain_dict(execution):
        errors.append("execution policy is required")
    else:
        if execution.get("mode") != "simulation-only":
            errors.append("execution mode must remain simulation-only")
        if execution.get("liveTrading") != "blocked":
            errors.append("live trading must be blocked")
        if execution.get("demoTrading") != "blocked":
            errors.append("demo trading must be blocked")
        if execution.get("providerCalls") != "blocked":
            errors.append("provider calls must be blocked")
        if execution.get("leverage") != 1:
            errors.append("leverage must remain 1")
        if execution.get("shorts") != "blocked":
            errors.append("shorts must be blocked")
        if execution.get("copyTrading") != "blocked":
            errors.append("copy trading must be blocked")

    if strategy and strategy["status"] == "context-only":
        warnings.append("strategy is context-only and cannot create simulated trade intent")

    return ValidationResult(ok=not errors, errors=tuple(errors), warnings=tuple(warnings))
