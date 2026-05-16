import {
  BLOCKED_SIMULATION_INSTRUMENT_CLASSES,
  DEFAULT_SIMULATION_CONFIG,
  SIMULATION_INSTRUMENT_CLASSES,
  SIMULATION_MARKET_INSTRUMENT_CLASS_RULES,
  SIMULATION_MARKETS,
  SIMULATION_RUN_MODES,
} from "./simulation-contract.mjs";

export const ALLOWED_MARKETS = SIMULATION_MARKETS;
export const ALLOWED_INSTRUMENT_CLASSES = SIMULATION_INSTRUMENT_CLASSES;
export const BLOCKED_INSTRUMENT_CLASSES = BLOCKED_SIMULATION_INSTRUMENT_CLASSES;
export const ALLOWED_RUN_MODES = SIMULATION_RUN_MODES;
export { DEFAULT_SIMULATION_CONFIG };

const SYMBOL_PATTERN = /^[A-Z0-9][A-Z0-9.-]{0,14}$/;

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function listUnknownValues(values, allowedValues) {
  if (!Array.isArray(values)) {
    return [];
  }

  return values.filter((value) => !allowedValues.includes(value));
}

function listStrategyBlockedValues(values, allowedValues) {
  if (!Array.isArray(values) || !Array.isArray(allowedValues)) {
    return [];
  }

  return values.filter((value) => !allowedValues.includes(value));
}

function listMarketInstrumentClassMismatches(allowedMarkets, allowedInstrumentClasses) {
  if (!Array.isArray(allowedMarkets) || !Array.isArray(allowedInstrumentClasses)) {
    return [];
  }

  const marketAllowedClasses = new Set(
    allowedMarkets.flatMap((market) => SIMULATION_MARKET_INSTRUMENT_CLASS_RULES[market] ?? []),
  );

  return allowedInstrumentClasses.filter((instrumentClass) => !marketAllowedClasses.has(instrumentClass));
}

export function validateSimulationConfig(
  config = DEFAULT_SIMULATION_CONFIG,
  { strategyRegistry = [], budgetPolicy, schedulePolicy } = {},
) {
  const errors = [];
  const warnings = [];

  if (!isPlainObject(config)) {
    return {
      ok: false,
      errors: ["simulation config must be an object"],
      warnings,
    };
  }

  const strategy = strategyRegistry.find((candidate) => candidate.strategyId === config.strategyId);

  if (!strategy) {
    errors.push("strategy must come from the predefined registry");
  }

  if (strategy?.status === "live") {
    errors.push("live strategies are not allowed");
  }

  if (!ALLOWED_RUN_MODES.includes(config.runMode)) {
    errors.push(`run mode must be one of: ${ALLOWED_RUN_MODES.join(", ")}`);
  } else if (config.runMode === "execute") {
    errors.push("execution mode is disabled; only backtest mode is currently allowed");
  }

  if (!isPlainObject(config.selectedInstrument)) {
    errors.push("selected instrument is required");
  } else {
    if (!SYMBOL_PATTERN.test(config.selectedInstrument.symbol ?? "")) {
      errors.push("selected instrument symbol must be an uppercase market symbol");
    }

    if (!ALLOWED_MARKETS.includes(config.selectedInstrument.market)) {
      errors.push("selected instrument market is unsupported");
    }

    if (!ALLOWED_INSTRUMENT_CLASSES.includes(config.selectedInstrument.instrumentClass)) {
      errors.push("selected instrument class is unsupported");
    }

    if (BLOCKED_INSTRUMENT_CLASSES.includes(config.selectedInstrument.instrumentClass)) {
      errors.push("selected instrument class is blocked");
    }
  }

  if (!Number.isFinite(config.budgetUsd) || config.budgetUsd <= 0) {
    errors.push("budget must be a positive USD amount");
  }

  if (budgetPolicy) {
    if (!budgetPolicy.selectableBudgetsUsd.includes(config.budgetUsd)) {
      errors.push("budget must be one of the selectable budget options");
    }

    if (config.budgetUsd > budgetPolicy.maxConfigurableBudgetUsd) {
      errors.push("budget cannot exceed the maximum configurable budget");
    }
  }

  if (!Array.isArray(config.allowedMarkets) || config.allowedMarkets.length === 0) {
    errors.push("allowed markets must be a non-empty list");
  }

  const unknownMarkets = listUnknownValues(config.allowedMarkets, ALLOWED_MARKETS);
  if (unknownMarkets.length > 0) {
    errors.push(`unsupported markets: ${unknownMarkets.join(", ")}`);
  }

  if (!Array.isArray(config.allowedInstrumentClasses) || config.allowedInstrumentClasses.length === 0) {
    errors.push("allowed instrument classes must be a non-empty list");
  }

  const unknownInstrumentClasses = listUnknownValues(
    config.allowedInstrumentClasses,
    ALLOWED_INSTRUMENT_CLASSES,
  );
  if (unknownInstrumentClasses.length > 0) {
    errors.push(`unsupported instrument classes: ${unknownInstrumentClasses.join(", ")}`);
  }

  const blockedInstrumentClasses = config.allowedInstrumentClasses?.filter((instrumentClass) =>
    BLOCKED_INSTRUMENT_CLASSES.includes(instrumentClass),
  );
  if (blockedInstrumentClasses?.length > 0) {
    errors.push(`blocked instrument classes configured: ${blockedInstrumentClasses.join(", ")}`);
  }

  const marketInstrumentClassMismatches = listMarketInstrumentClassMismatches(
    config.allowedMarkets,
    config.allowedInstrumentClasses,
  );
  if (marketInstrumentClassMismatches.length > 0) {
    errors.push(
      `instrument classes not supported by selected markets: ${marketInstrumentClassMismatches.join(", ")}`,
    );
  }

  if (isPlainObject(config.selectedInstrument)) {
    const selectedMarket = config.selectedInstrument.market;
    const selectedInstrumentClass = config.selectedInstrument.instrumentClass;
    const selectedMarketClasses = SIMULATION_MARKET_INSTRUMENT_CLASS_RULES[selectedMarket] ?? [];

    if (Array.isArray(config.allowedMarkets) && !config.allowedMarkets.includes(selectedMarket)) {
      errors.push("selected instrument market must be included in allowed markets");
    }

    if (
      Array.isArray(config.allowedInstrumentClasses) &&
      !config.allowedInstrumentClasses.includes(selectedInstrumentClass)
    ) {
      errors.push("selected instrument class must be included in allowed instrument classes");
    }

    if (
      ALLOWED_MARKETS.includes(selectedMarket) &&
      ALLOWED_INSTRUMENT_CLASSES.includes(selectedInstrumentClass) &&
      !selectedMarketClasses.includes(selectedInstrumentClass)
    ) {
      errors.push("selected instrument class is not supported by its market");
    }
  }

  const strategyBlockedMarkets = listStrategyBlockedValues(config.allowedMarkets, strategy?.allowedMarkets);
  if (strategyBlockedMarkets.length > 0) {
    errors.push(
      `allowed markets are not allowed for ${strategy.strategyId}: ${strategyBlockedMarkets.join(", ")}`,
    );
  }

  const strategyBlockedInstrumentClasses = listStrategyBlockedValues(
    config.allowedInstrumentClasses,
    strategy?.allowedInstrumentClasses,
  );
  if (strategyBlockedInstrumentClasses.length > 0) {
    errors.push(
      `allowed instrument classes are not allowed for ${strategy.strategyId}: ${strategyBlockedInstrumentClasses.join(", ")}`,
    );
  }

  if (strategy && isPlainObject(config.selectedInstrument)) {
    if (!strategy.allowedMarkets.includes(config.selectedInstrument.market)) {
      errors.push(`selected instrument market is not allowed for ${strategy.strategyId}`);
    }

    if (!strategy.allowedInstrumentClasses.includes(config.selectedInstrument.instrumentClass)) {
      errors.push(`selected instrument class is not allowed for ${strategy.strategyId}`);
    }
  }

  if (!isPlainObject(config.cadence)) {
    errors.push("cadence policy is required");
  } else {
    if (config.cadence.mode !== "low-frequency-only") {
      errors.push("cadence mode must remain low-frequency-only");
    }

    if (!config.cadence.frequency || typeof config.cadence.frequency !== "string") {
      errors.push("cadence frequency is required");
    } else if (strategy?.cadence && config.cadence.frequency !== strategy.cadence) {
      errors.push(`cadence frequency is not allowed for ${strategy.strategyId}`);
    }

    const minimumInterval = config.cadence.minimumEvaluationIntervalMinutes;
    const requiredMinimum = schedulePolicy?.minimumEvaluationIntervalMinutes ?? 240;
    if (!Number.isFinite(minimumInterval) || minimumInterval < requiredMinimum) {
      errors.push(`minimum evaluation interval must be at least ${requiredMinimum} minutes`);
    }

    const maxDecisionsPerDay = config.cadence.maxDecisionsPerDay;
    const allowedMaxDecisionsPerDay = schedulePolicy?.maxDecisionsPerDay ?? 3;
    if (
      !Number.isInteger(maxDecisionsPerDay) ||
      maxDecisionsPerDay < 1 ||
      maxDecisionsPerDay > allowedMaxDecisionsPerDay
    ) {
      errors.push(`max decisions per day must be between 1 and ${allowedMaxDecisionsPerDay}`);
    }
  }

  if (!isPlainObject(config.execution)) {
    errors.push("execution policy is required");
  } else {
    if (config.execution.mode !== "simulation-only") {
      errors.push("execution mode must remain simulation-only");
    }

    if (config.execution.liveTrading !== "blocked") {
      errors.push("live trading must be blocked");
    }

    if (config.execution.demoTrading !== "blocked") {
      errors.push("demo trading must be blocked");
    }

    if (config.execution.providerCalls !== "blocked") {
      errors.push("provider calls must be blocked");
    }

    if (config.execution.leverage !== 1) {
      errors.push("leverage must remain 1");
    }

    if (config.execution.shorts !== "blocked") {
      errors.push("shorts must be blocked");
    }

    if (config.execution.copyTrading !== "blocked") {
      errors.push("copy trading must be blocked");
    }
  }

  if (strategy?.status === "context-only") {
    warnings.push("strategy is context-only and cannot create simulated trade intent");
  }

  return {
    ok: errors.length === 0,
    errors,
    warnings,
  };
}
