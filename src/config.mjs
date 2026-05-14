export const ALLOWED_MARKETS = Object.freeze(["US", "AU"]);

export const ALLOWED_INSTRUMENT_CLASSES = Object.freeze(["EQUITY", "ETF", "COMMODITY"]);

export const BLOCKED_INSTRUMENT_CLASSES = Object.freeze([
  "CFD",
  "CRYPTO",
  "DERIVATIVE",
  "FOREX",
  "OPTION",
]);

export const DEFAULT_SIMULATION_CONFIG = Object.freeze({
  strategyId: "dca-cash-reserve",
  budgetUsd: 1000,
  allowedMarkets: Object.freeze(["US", "AU"]),
  allowedInstrumentClasses: Object.freeze(["EQUITY", "ETF"]),
  cadence: Object.freeze({
    mode: "low-frequency-only",
    minimumEvaluationIntervalMinutes: 240,
    maxDecisionsPerDay: 3,
  }),
  execution: Object.freeze({
    mode: "simulation-only",
    liveTrading: "blocked",
    demoTrading: "blocked",
    providerCalls: "blocked",
    leverage: 1,
    shorts: "blocked",
    copyTrading: "blocked",
  }),
});

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function listUnknownValues(values, allowedValues) {
  if (!Array.isArray(values)) {
    return [];
  }

  return values.filter((value) => !allowedValues.includes(value));
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

  if (!isPlainObject(config.cadence)) {
    errors.push("cadence policy is required");
  } else {
    if (config.cadence.mode !== "low-frequency-only") {
      errors.push("cadence mode must remain low-frequency-only");
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
