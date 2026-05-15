export const SIMULATION_CONTRACT_SOURCE = "Money-maker-3000/src/simulation-contract.mjs";
export const SIMULATION_CONTRACT_VERSION = "0.1.0-sim";

export const SIMULATION_MARKETS = Object.freeze([
  "US_EQUITIES",
  "AU_EQUITIES",
  "FOREX",
  "COMMODITIES",
]);

export const SIMULATION_INSTRUMENT_CLASSES = Object.freeze(["EQUITY", "ETF", "FOREX", "COMMODITY"]);
export const BLOCKED_SIMULATION_INSTRUMENT_CLASSES = Object.freeze([
  "CFD",
  "CRYPTO",
  "DERIVATIVE",
  "OPTION",
]);
export const SIMULATION_CADENCES = Object.freeze(["daily", "weekly"]);
export const MINIMUM_SIMULATION_EVALUATION_INTERVAL_MINUTES = 240;
export const MAX_SIMULATION_DECISIONS_PER_DAY = 3;

export const SIMULATION_MARKET_INSTRUMENT_CLASS_RULES = Object.freeze({
  US_EQUITIES: Object.freeze(["EQUITY", "ETF"]),
  AU_EQUITIES: Object.freeze(["EQUITY", "ETF"]),
  FOREX: Object.freeze(["FOREX"]),
  COMMODITIES: Object.freeze(["COMMODITY", "ETF"]),
});

export const SIMULATION_STRATEGY_CONFIG_RULES = Object.freeze({
  "dca-cash-reserve": Object.freeze({
    name: "Cash-reserved DCA",
    version: "0.1.0-sim",
    status: "simulation-only",
    cadence: "daily",
    allowedMarkets: Object.freeze(["US_EQUITIES", "AU_EQUITIES"]),
    allowedInstrumentClasses: Object.freeze(["EQUITY", "ETF"]),
  }),
  "threshold-rebalance": Object.freeze({
    name: "Threshold rebalance",
    version: "0.1.0-sim",
    status: "simulation-only",
    cadence: "weekly",
    allowedMarkets: Object.freeze(["US_EQUITIES", "AU_EQUITIES", "COMMODITIES"]),
    allowedInstrumentClasses: Object.freeze(["EQUITY", "ETF", "COMMODITY"]),
  }),
  "news-aware-watchlist": Object.freeze({
    name: "News-aware watchlist",
    version: "0.1.0-plan",
    status: "context-only",
    cadence: "daily",
    allowedMarkets: Object.freeze(["US_EQUITIES", "AU_EQUITIES", "FOREX", "COMMODITIES"]),
    allowedInstrumentClasses: Object.freeze(["EQUITY", "ETF", "FOREX", "COMMODITY"]),
  }),
});

export const DEFAULT_SIMULATION_CONFIG = Object.freeze({
  strategyId: "dca-cash-reserve",
  budgetUsd: 1000,
  allowedMarkets: Object.freeze(["US_EQUITIES", "AU_EQUITIES"]),
  allowedInstrumentClasses: Object.freeze(["EQUITY", "ETF"]),
  cadence: Object.freeze({
    mode: "low-frequency-only",
    frequency: "daily",
    minimumEvaluationIntervalMinutes: MINIMUM_SIMULATION_EVALUATION_INTERVAL_MINUTES,
    maxDecisionsPerDay: MAX_SIMULATION_DECISIONS_PER_DAY,
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

export const SIMULATION_CONFIG_CONTRACT = Object.freeze({
  source: SIMULATION_CONTRACT_SOURCE,
  version: SIMULATION_CONTRACT_VERSION,
  markets: SIMULATION_MARKETS,
  instrumentClasses: SIMULATION_INSTRUMENT_CLASSES,
  blockedInstrumentClasses: BLOCKED_SIMULATION_INSTRUMENT_CLASSES,
  marketInstrumentClassRules: SIMULATION_MARKET_INSTRUMENT_CLASS_RULES,
  cadences: SIMULATION_CADENCES,
  minimumEvaluationIntervalMinutes: MINIMUM_SIMULATION_EVALUATION_INTERVAL_MINUTES,
  maxDecisionsPerDay: MAX_SIMULATION_DECISIONS_PER_DAY,
  strategyRules: SIMULATION_STRATEGY_CONFIG_RULES,
});

export function defaultSimulationConfigForStrategy(strategyId = DEFAULT_SIMULATION_CONFIG.strategyId) {
  const strategyRule = SIMULATION_STRATEGY_CONFIG_RULES[strategyId] ?? SIMULATION_STRATEGY_CONFIG_RULES[
    DEFAULT_SIMULATION_CONFIG.strategyId
  ];

  return {
    ...DEFAULT_SIMULATION_CONFIG,
    strategyId,
    allowedMarkets: [...strategyRule.allowedMarkets],
    allowedInstrumentClasses: [...strategyRule.allowedInstrumentClasses],
    cadence: {
      ...DEFAULT_SIMULATION_CONFIG.cadence,
      frequency: strategyRule.cadence,
    },
    execution: {
      ...DEFAULT_SIMULATION_CONFIG.execution,
    },
  };
}
