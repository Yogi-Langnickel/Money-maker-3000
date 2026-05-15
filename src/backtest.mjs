import { buildSimulationRun } from "./contracts.mjs";
import { buildLedgerRecord } from "./ledger.mjs";

const DEFAULT_SCENARIOS = Object.freeze([
  Object.freeze({ scenarioId: "dca-500", strategyId: "dca-cash-reserve", budgetUsd: 500 }),
  Object.freeze({ scenarioId: "dca-1000", strategyId: "dca-cash-reserve", budgetUsd: 1000 }),
  Object.freeze({ scenarioId: "rebalance-1500", strategyId: "threshold-rebalance", budgetUsd: 1500 }),
  Object.freeze({ scenarioId: "watchlist-1000", strategyId: "news-aware-watchlist", budgetUsd: 1000 }),
]);

function incrementHistogram(histogram, values) {
  for (const value of values) {
    histogram[value] = (histogram[value] ?? 0) + 1;
  }
}

function buildBudgetDiagnostics(runs) {
  const requestedBudgets = runs
    .map((run) => run.simulationConfig.budgetUsd)
    .filter((budgetUsd) => Number.isFinite(budgetUsd));
  const remainingBudgets = runs
    .map((run) => run.budget.remainingUsd)
    .filter((budgetUsd) => Number.isFinite(budgetUsd));
  const maxConfigurableBudgetUsd = runs[0]?.budget.maxConfigurableBudgetUsd ?? null;
  const selectableBudgetsUsd = [...new Set(requestedBudgets)].sort((left, right) => left - right);

  return {
    requestedBudgetUsd: {
      min: requestedBudgets.length > 0 ? Math.min(...requestedBudgets) : null,
      max: requestedBudgets.length > 0 ? Math.max(...requestedBudgets) : null,
      unique: selectableBudgetsUsd,
    },
    remainingBudgetUsd: {
      min: remainingBudgets.length > 0 ? Math.min(...remainingBudgets) : null,
      max: remainingBudgets.length > 0 ? Math.max(...remainingBudgets) : null,
    },
    maxConfigurableBudgetUsd,
    overMaxConfigurableCount: runs.filter(
      (run) =>
        Number.isFinite(run.simulationConfig.budgetUsd) &&
        Number.isFinite(run.budget.maxConfigurableBudgetUsd) &&
        run.simulationConfig.budgetUsd > run.budget.maxConfigurableBudgetUsd,
    ).length,
  };
}

function buildScenarioSummary({ scenario, run, index }) {
  const scenarioId = scenario.scenarioId ?? `${run.strategyId}-${index + 1}`;

  return {
    scenarioId,
    runId: run.runId,
    strategyId: run.strategyId,
    decision: run.decision,
    riskResult: run.riskResult,
    requestedBudgetUsd: run.simulationConfig.budgetUsd,
    remainingBudgetUsd: run.budget.remainingUsd,
    cadence: {
      frequency: run.simulationConfig.cadence.frequency,
      minimumEvaluationIntervalMinutes: run.simulationConfig.cadence.minimumEvaluationIntervalMinutes,
      maxDecisionsPerDay: run.simulationConfig.cadence.maxDecisionsPerDay,
      lowFrequencyOnly: run.simulationConfig.cadence.mode === "low-frequency-only",
    },
    config: {
      ok: run.configValidation.ok,
      errorCount: run.configValidation.errors.length,
      warningCount: run.configValidation.warnings.length,
      errors: run.configValidation.errors,
      warnings: run.configValidation.warnings,
    },
    vetoes: run.vetoes,
    providerCalls: run.providerMetadata.providerCalls,
    executionRoute: run.tradeLogEntry.executionRoute,
  };
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
        ...scenario.simulationConfig,
        budgetUsd: scenario.budgetUsd ?? scenario.simulationConfig?.budgetUsd,
      },
    });
  });
  const vetoHistogram = {};
  const warningHistogram = {};
  const configErrorHistogram = {};
  const budgetDiagnostics = buildBudgetDiagnostics(runs);
  const remainingBudgets = runs.map((run) => run.budget.remainingUsd);

  for (const run of runs) {
    incrementHistogram(vetoHistogram, run.vetoes);
    incrementHistogram(warningHistogram, run.configValidation.warnings);
    incrementHistogram(configErrorHistogram, run.configValidation.errors);
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
      configInvalidCount: runs.filter((run) => !run.configValidation.ok).length,
      lowFrequencyOnlyCount: runs.filter(
        (run) =>
          run.simulationConfig.cadence.mode === "low-frequency-only" &&
          run.simulationConfig.cadence.minimumEvaluationIntervalMinutes >=
            run.schedulePolicy.minimumEvaluationIntervalMinutes &&
          run.simulationConfig.cadence.maxDecisionsPerDay <= run.schedulePolicy.maxDecisionsPerDay,
      ).length,
      minRemainingBudgetUsd: remainingBudgets.length > 0 ? Math.min(...remainingBudgets) : null,
      maxRemainingBudgetUsd: remainingBudgets.length > 0 ? Math.max(...remainingBudgets) : null,
      budgetDiagnostics,
      vetoHistogram,
      warningHistogram,
      configErrorHistogram,
    },
    scenarioSummaries: runs.map((run, index) =>
      buildScenarioSummary({ scenario: scenarios[index], run, index }),
    ),
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
