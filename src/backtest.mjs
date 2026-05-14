import { buildSimulationRun } from "./contracts.mjs";
import { buildLedgerRecord } from "./ledger.mjs";

const DEFAULT_SCENARIOS = Object.freeze([
  Object.freeze({ strategyId: "dca-cash-reserve", budgetUsd: 500 }),
  Object.freeze({ strategyId: "dca-cash-reserve", budgetUsd: 1000 }),
  Object.freeze({ strategyId: "threshold-rebalance", budgetUsd: 1500 }),
  Object.freeze({ strategyId: "news-aware-watchlist", budgetUsd: 1000 }),
]);

function incrementHistogram(histogram, values) {
  for (const value of values) {
    histogram[value] = (histogram[value] ?? 0) + 1;
  }
}

export function buildSyntheticBacktest({
  scenarios = DEFAULT_SCENARIOS,
  startedAt = new Date("2026-05-15T00:00:00.000Z"),
  includeLedgerRecords = false,
} = {}) {
  const runs = scenarios.map((scenario, index) => {
    const now = new Date(startedAt.getTime() + index * 60_000);

    return buildSimulationRun({
      strategyId: scenario.strategyId,
      now,
      simulationConfig: {
        budgetUsd: scenario.budgetUsd,
      },
    });
  });
  const vetoHistogram = {};
  const warningHistogram = {};
  const remainingBudgets = runs.map((run) => run.budget.remainingUsd);

  for (const run of runs) {
    incrementHistogram(vetoHistogram, run.vetoes);
    incrementHistogram(warningHistogram, run.configValidation.warnings);
  }

  return {
    mode: "synthetic-backtest",
    environment: "synthetic",
    providerCalls: "blocked",
    executionRoutes: "absent",
    startedAt: startedAt.toISOString(),
    summary: {
      runCount: runs.length,
      skipCount: runs.filter((run) => run.decision === "skip").length,
      blockedCount: runs.filter((run) => run.riskResult === "blocked").length,
      configValidCount: runs.filter((run) => run.configValidation.ok).length,
      minRemainingBudgetUsd: Math.min(...remainingBudgets),
      maxRemainingBudgetUsd: Math.max(...remainingBudgets),
      vetoHistogram,
      warningHistogram,
    },
    runs: runs.map((run) => ({
      runId: run.runId,
      strategyId: run.strategyId,
      decision: run.decision,
      riskResult: run.riskResult,
      budgetRemainingUsd: run.budget.remainingUsd,
      vetoes: run.vetoes,
      configWarnings: run.configValidation.warnings,
    })),
    ledgerRecords: includeLedgerRecords
      ? runs.map((run) => buildLedgerRecord({ run }))
      : [],
  };
}

export { DEFAULT_SCENARIOS };
