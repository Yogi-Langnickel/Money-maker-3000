import { DEFAULT_SIMULATION_CONFIG, validateSimulationConfig } from "./config.mjs";
import { buildProviderMetadataSnapshot } from "./providers.mjs";

export const DEFAULT_BUDGET_POLICY = Object.freeze({
  baseBudgetUsd: 1000,
  selectableBudgetsUsd: Object.freeze([500, 1000, 1500, 2500]),
  maxConfigurableBudgetUsd: 2500,
  dailyLossStopUsd: 50,
  weeklyLossStopUsd: 150,
  maxOpenPositions: 3,
  leverage: 1,
  shorts: "blocked",
  copyTrading: "blocked",
});

export const DEFAULT_SCHEDULE_POLICY = Object.freeze({
  mode: "low-frequency-only",
  minimumEvaluationIntervalMinutes: 240,
  defaultCadence: "daily",
  highFrequencyTrading: "blocked",
  maxDecisionsPerDay: 3,
});

const STRATEGY_STATUSES = Object.freeze(["simulation-only", "context-only"]);
const STRATEGY_CADENCES = Object.freeze(["daily", "weekly"]);
const STRATEGY_INSTRUMENTS = Object.freeze([
  "US_EQUITIES",
  "AU_EQUITIES",
  "COMMODITIES",
  "FOREX",
]);
const BLOCKED_SIMULATION_STRATEGY_INSTRUMENTS = Object.freeze(["FOREX"]);
const HIGH_FREQUENCY_HOLDING_PERIOD_PATTERN = /(second|minute|intraday|scalp|high-frequency)/i;
const STRATEGY_ID_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

export const STRATEGY_REGISTRY = Object.freeze([
  Object.freeze({
    strategyId: "dca-cash-reserve",
    name: "Cash-reserved DCA",
    version: "0.1.0-sim",
    status: "simulation-only",
    cadence: "daily",
    allowedInstruments: Object.freeze(["US_EQUITIES", "AU_EQUITIES"]),
    expectedHoldingPeriod: "weeks-to-months",
  }),
  Object.freeze({
    strategyId: "threshold-rebalance",
    name: "Threshold rebalance",
    version: "0.1.0-sim",
    status: "simulation-only",
    cadence: "weekly",
    allowedInstruments: Object.freeze(["US_EQUITIES", "AU_EQUITIES", "COMMODITIES"]),
    expectedHoldingPeriod: "weeks-to-months",
  }),
  Object.freeze({
    strategyId: "news-aware-watchlist",
    name: "News-aware watchlist",
    version: "0.1.0-plan",
    status: "context-only",
    cadence: "daily",
    allowedInstruments: Object.freeze(["US_EQUITIES", "AU_EQUITIES", "FOREX", "COMMODITIES"]),
    expectedHoldingPeriod: "not-trading-from-news",
  }),
]);

export const SYNTHETIC_POSITION_CONTEXT = Object.freeze([
  Object.freeze({
    symbol: "SPY",
    assetClass: "ETF",
    exposureState: "synthetic",
    newsContext: Object.freeze([
      Object.freeze({
        headline: "Macro calendar context placeholder",
        source: "synthetic",
        summary: "Context only; cannot create an order or recommendation.",
      }),
    ]),
  }),
  Object.freeze({
    symbol: "GLD",
    assetClass: "ETF",
    exposureState: "synthetic",
    newsContext: Object.freeze([
      Object.freeze({
        headline: "Commodity market context placeholder",
        source: "synthetic",
        summary: "Source review is required before live ingestion.",
      }),
    ]),
  }),
]);

export function strategyById(strategyId) {
  return STRATEGY_REGISTRY.find((strategy) => strategy.strategyId === strategyId) ?? null;
}

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function validateStrategyRegistry(registry = STRATEGY_REGISTRY) {
  const errors = [];
  const seenStrategyIds = new Set();

  if (!Array.isArray(registry) || registry.length === 0) {
    return {
      ok: false,
      errors: ["strategy registry must be a non-empty predefined list"],
    };
  }

  registry.forEach((strategy, index) => {
    const label = strategy?.strategyId ?? `strategy at index ${index}`;

    if (!isPlainObject(strategy)) {
      errors.push(`${label} must be an object`);
      return;
    }

    if (!strategy.strategyId || typeof strategy.strategyId !== "string") {
      errors.push(`${label} must have a strategy id`);
    } else {
      if (!STRATEGY_ID_PATTERN.test(strategy.strategyId)) {
        errors.push(`${label} strategy id must be kebab-case`);
      }

      if (seenStrategyIds.has(strategy.strategyId)) {
        errors.push(`${label} strategy id must be unique`);
      }

      seenStrategyIds.add(strategy.strategyId);
    }

    if (!strategy.name || typeof strategy.name !== "string") {
      errors.push(`${label} must have a display name`);
    }

    if (!strategy.version || typeof strategy.version !== "string") {
      errors.push(`${label} must have a version`);
    }

    if (!STRATEGY_STATUSES.includes(strategy.status)) {
      errors.push(`${label} status must be simulation-only or context-only`);
    }

    if (strategy.status === "live") {
      errors.push(`${label} live strategies are not allowed`);
    }

    if (!STRATEGY_CADENCES.includes(strategy.cadence)) {
      errors.push(`${label} cadence must be low-frequency daily or weekly`);
    }

    if (!Array.isArray(strategy.allowedInstruments) || strategy.allowedInstruments.length === 0) {
      errors.push(`${label} allowed instruments must be a non-empty list`);
    } else {
      const unknownInstruments = strategy.allowedInstruments.filter(
        (instrument) => !STRATEGY_INSTRUMENTS.includes(instrument),
      );
      if (unknownInstruments.length > 0) {
        errors.push(`${label} has unknown instruments: ${unknownInstruments.join(", ")}`);
      }

      const blockedSimulationInstruments = strategy.allowedInstruments.filter((instrument) =>
        BLOCKED_SIMULATION_STRATEGY_INSTRUMENTS.includes(instrument),
      );
      if (strategy.status === "simulation-only" && blockedSimulationInstruments.length > 0) {
        errors.push(
          `${label} simulation strategy includes blocked execution instruments: ${blockedSimulationInstruments.join(", ")}`,
        );
      }
    }

    if (!strategy.expectedHoldingPeriod || typeof strategy.expectedHoldingPeriod !== "string") {
      errors.push(`${label} must describe an expected holding period`);
    } else if (HIGH_FREQUENCY_HOLDING_PERIOD_PATTERN.test(strategy.expectedHoldingPeriod)) {
      errors.push(`${label} expected holding period must remain low-frequency`);
    }
  });

  return {
    ok: errors.length === 0,
    errors,
  };
}

export function validateBudgetPolicy(policy = DEFAULT_BUDGET_POLICY) {
  const errors = [];

  if (!policy.selectableBudgetsUsd.includes(policy.baseBudgetUsd)) {
    errors.push("base budget must be one of the selectable budget options");
  }

  if (policy.baseBudgetUsd > policy.maxConfigurableBudgetUsd) {
    errors.push("base budget cannot exceed the maximum configurable budget");
  }

  if (policy.dailyLossStopUsd <= 0 || policy.weeklyLossStopUsd <= 0) {
    errors.push("loss stops must be positive");
  }

  if (policy.leverage !== 1) {
    errors.push("leverage must remain 1 in simulation");
  }

  return {
    ok: errors.length === 0,
    errors,
  };
}

export function validateSchedulePolicy(policy = DEFAULT_SCHEDULE_POLICY) {
  const errors = [];

  if (policy.highFrequencyTrading !== "blocked") {
    errors.push("high-frequency trading must be blocked");
  }

  if (policy.minimumEvaluationIntervalMinutes < 240) {
    errors.push("minimum evaluation interval must be at least 240 minutes");
  }

  return {
    ok: errors.length === 0,
    errors,
  };
}

export function buildSimulationRun({
  strategyId = "dca-cash-reserve",
  now = new Date("2026-05-14T00:00:00.000Z"),
  budgetPolicy = DEFAULT_BUDGET_POLICY,
  schedulePolicy = DEFAULT_SCHEDULE_POLICY,
  simulationConfig,
} = {}) {
  const effectiveSimulationConfig = {
    ...DEFAULT_SIMULATION_CONFIG,
    ...simulationConfig,
    strategyId: simulationConfig?.strategyId ?? strategyId,
  };
  const effectiveStrategyId = effectiveSimulationConfig.strategyId;
  const strategy = strategyById(effectiveStrategyId);
  const budgetValidation = validateBudgetPolicy(budgetPolicy);
  const scheduleValidation = validateSchedulePolicy(schedulePolicy);
  const strategyRegistryValidation = validateStrategyRegistry(STRATEGY_REGISTRY);
  const configValidation = validateSimulationConfig(effectiveSimulationConfig, {
    strategyRegistry: STRATEGY_REGISTRY,
    budgetPolicy,
    schedulePolicy,
  });
  const vetoes = [];

  if (!strategy) {
    vetoes.push("unknown-strategy");
  }

  if (!budgetValidation.ok) {
    vetoes.push("invalid-budget-policy");
  }

  if (!scheduleValidation.ok) {
    vetoes.push("invalid-schedule-policy");
  }

  if (!strategyRegistryValidation.ok) {
    vetoes.push("invalid-strategy-registry");
  }

  if (!configValidation.ok) {
    vetoes.push("invalid-simulation-config");
  }

  vetoes.push("provider-not-connected", "execution-route-absent");

  return {
    runId: `sim-${now.toISOString()}`,
    strategyId: strategy?.strategyId ?? effectiveStrategyId,
    strategyVersion: strategy?.version ?? "unknown",
    mode: "simulation",
    environment: "synthetic",
    evaluatedAt: now.toISOString(),
    decision: "skip",
    riskResult: "blocked",
    vetoes,
    schedulePolicy,
    strategyRegistryValidation,
    simulationConfig: {
      strategyId: effectiveStrategyId,
      budgetUsd: effectiveSimulationConfig.budgetUsd,
      allowedMarkets: effectiveSimulationConfig.allowedMarkets,
      allowedInstrumentClasses: effectiveSimulationConfig.allowedInstrumentClasses,
      cadence: effectiveSimulationConfig.cadence,
      execution: effectiveSimulationConfig.execution,
    },
    configValidation,
    budget: {
      allocatedUsd: 0,
      remainingUsd: effectiveSimulationConfig.budgetUsd ?? budgetPolicy.baseBudgetUsd,
      maxConfigurableBudgetUsd: budgetPolicy.maxConfigurableBudgetUsd,
    },
    providerMetadata: buildProviderMetadataSnapshot(),
    positionContext: SYNTHETIC_POSITION_CONTEXT,
    tradeLogEntry: {
      tradeLogId: `trade-log-${now.getTime()}`,
      action: "simulated-skip",
      strategyId: strategy?.strategyId ?? effectiveStrategyId,
      decision: "blocked",
      reasonCode: vetoes[0],
      budgetRemainingUsd: effectiveSimulationConfig.budgetUsd ?? budgetPolicy.baseBudgetUsd,
      accountIdentifiers: "redacted",
      rawProviderPayloads: "absent",
      providerCall: "not-attempted",
      executionRoute: "absent",
    },
  };
}

export { DEFAULT_SIMULATION_CONFIG, validateSimulationConfig };
