import assert from "node:assert/strict";
import test from "node:test";
import { buildSyntheticBacktest } from "../src/backtest.mjs";

test("synthetic backtest summarizes deterministic blocked simulation runs", () => {
  const report = buildSyntheticBacktest();

  assert.equal(report.mode, "synthetic-backtest");
  assert.equal(report.environment, "synthetic");
  assert.equal(report.providerCalls, "blocked");
  assert.equal(report.executionRoutes, "absent");
  assert.equal(report.summary.runCount, 4);
  assert.equal(report.summary.skipCount, 4);
  assert.equal(report.summary.blockedCount, 4);
  assert.equal(report.summary.configValidCount, 4);
  assert.equal(report.summary.configInvalidCount, 0);
  assert.equal(report.summary.lowFrequencyOnlyCount, 4);
  assert.equal(report.summary.minRemainingBudgetUsd, 500);
  assert.equal(report.summary.maxRemainingBudgetUsd, 1500);
  assert.deepEqual(report.summary.budgetDiagnostics.requestedBudgetUsd, {
    min: 500,
    max: 1500,
    unique: [500, 1000, 1500],
  });
  assert.deepEqual(report.summary.budgetDiagnostics.remainingBudgetUsd, {
    min: 500,
    max: 1500,
  });
  assert.equal(report.summary.budgetDiagnostics.maxConfigurableBudgetUsd, 2500);
  assert.equal(report.summary.budgetDiagnostics.overMaxConfigurableCount, 0);
  assert.equal(report.summary.vetoHistogram["provider-not-connected"], 4);
  assert.equal(report.summary.vetoHistogram["execution-route-absent"], 4);
  assert.equal(report.summary.warningHistogram["strategy is context-only and cannot create simulated trade intent"], 1);
  assert.deepEqual(
    report.scenarioSummaries.map((scenario) => scenario.scenarioId),
    ["dca-500", "dca-1000", "rebalance-1500", "watchlist-1000"],
  );
  assert.equal(report.scenarioSummaries.every((scenario) => scenario.runMode === "backtest"), true);
  assert.deepEqual(report.scenarioSummaries[0].selectedInstrument, {
    symbol: "SPY",
    market: "US_EQUITIES",
    instrumentClass: "ETF",
  });
  assert.deepEqual(
    report.scenarioSummaries.map((scenario) => scenario.providerCalls),
    ["blocked", "blocked", "blocked", "blocked"],
  );
  assert.deepEqual(
    report.scenarioSummaries.map((scenario) => scenario.executionRoute),
    ["absent", "absent", "absent", "absent"],
  );
  assert.equal(report.runs.every((run) => run.decision === "skip"), true);
});

test("synthetic backtest reports no-HFT and strategy config compatibility diagnostics", () => {
  const report = buildSyntheticBacktest({
    scenarios: [
      {
        scenarioId: "threshold-invalid-daily",
        strategyId: "threshold-rebalance",
        budgetUsd: 1000,
        simulationConfig: {
          cadence: {
            frequency: "daily",
          },
        },
      },
      {
        scenarioId: "dca-hft-veto",
        strategyId: "dca-cash-reserve",
        budgetUsd: 500,
        simulationConfig: {
          cadence: {
            mode: "high-frequency",
            frequency: "daily",
            minimumEvaluationIntervalMinutes: 5,
            maxDecisionsPerDay: 20,
          },
        },
      },
      {
        scenarioId: "dca-budget-over-max",
        strategyId: "dca-cash-reserve",
        budgetUsd: 5000,
      },
    ],
  });

  assert.equal(report.summary.runCount, 3);
  assert.equal(report.summary.configValidCount, 0);
  assert.equal(report.summary.configInvalidCount, 3);
  assert.equal(report.summary.lowFrequencyOnlyCount, 2);
  assert.equal(report.summary.budgetDiagnostics.overMaxConfigurableCount, 1);
  assert.equal(report.summary.budgetDiagnostics.requestedBudgetUsd.max, 5000);
  assert.equal(
    report.summary.configErrorHistogram["cadence frequency is not allowed for threshold-rebalance"],
    1,
  );
  assert.equal(report.summary.configErrorHistogram["cadence mode must remain low-frequency-only"], 1);
  assert.equal(
    report.summary.configErrorHistogram["minimum evaluation interval must be at least 240 minutes"],
    1,
  );
  assert.equal(report.summary.configErrorHistogram["max decisions per day must be between 1 and 3"], 1);
  assert.equal(report.summary.configErrorHistogram["budget must be one of the selectable budget options"], 1);
  assert.equal(report.summary.configErrorHistogram["budget cannot exceed the maximum configurable budget"], 1);
  assert.equal(report.summary.vetoHistogram["invalid-simulation-config"], 3);
  assert.equal(report.summary.vetoHistogram["provider-not-connected"], 3);
  assert.equal(report.summary.vetoHistogram["execution-route-absent"], 3);
  assert.deepEqual(
    report.scenarioSummaries.map((scenario) => ({
      scenarioId: scenario.scenarioId,
      configOk: scenario.config.ok,
      errorCount: scenario.config.errorCount,
      providerCalls: scenario.providerCalls,
      executionRoute: scenario.executionRoute,
    })),
    [
      {
        scenarioId: "threshold-invalid-daily",
        configOk: false,
        errorCount: 1,
        providerCalls: "blocked",
        executionRoute: "absent",
      },
      {
        scenarioId: "dca-hft-veto",
        configOk: false,
        errorCount: 3,
        providerCalls: "blocked",
        executionRoute: "absent",
      },
      {
        scenarioId: "dca-budget-over-max",
        configOk: false,
        errorCount: 2,
        providerCalls: "blocked",
        executionRoute: "absent",
      },
    ],
  );
});

test("synthetic backtest ledger records remain redacted", () => {
  const report = buildSyntheticBacktest({ includeLedgerRecords: true });
  const serialized = JSON.stringify(report);

  assert.equal(report.ledgerRecords.length, 4);
  assert.equal(report.ledgerRecords[0].mode, "simulation");
  assert.equal(report.ledgerRecords[0].tradeLogEntry.accountIdentifiers, "redacted");
  assert.equal(report.ledgerRecords[0].tradeLogEntry.providerCall, "not-attempted");
  assert.equal(serialized.includes("apiKey"), false);
  assert.equal(serialized.includes("userKey"), false);
  assert.equal(serialized.includes('"accountId"'), false);
  assert.equal(serialized.includes('"positionId"'), false);
});
