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
} = {}) {
  const strategy = strategyById(strategyId);
  const budgetValidation = validateBudgetPolicy(budgetPolicy);
  const scheduleValidation = validateSchedulePolicy(schedulePolicy);
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

  vetoes.push("provider-not-connected", "execution-route-absent");

  return {
    runId: `sim-${now.toISOString()}`,
    strategyId: strategy?.strategyId ?? strategyId,
    strategyVersion: strategy?.version ?? "unknown",
    mode: "simulation",
    environment: "synthetic",
    evaluatedAt: now.toISOString(),
    decision: "skip",
    riskResult: "blocked",
    vetoes,
    schedulePolicy,
    budget: {
      allocatedUsd: 0,
      remainingUsd: budgetPolicy.baseBudgetUsd,
      maxConfigurableBudgetUsd: budgetPolicy.maxConfigurableBudgetUsd,
    },
    positionContext: SYNTHETIC_POSITION_CONTEXT,
    tradeLogEntry: {
      tradeLogId: `trade-log-${now.getTime()}`,
      action: "simulated-skip",
      strategyId: strategy?.strategyId ?? strategyId,
      decision: "blocked",
      reasonCode: vetoes[0],
      budgetRemainingUsd: budgetPolicy.baseBudgetUsd,
      accountIdentifiers: "redacted",
      rawProviderPayloads: "absent",
    },
  };
}
