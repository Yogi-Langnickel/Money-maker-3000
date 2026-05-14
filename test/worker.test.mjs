import assert from "node:assert/strict";
import test from "node:test";
import {
  DEFAULT_BUDGET_POLICY,
  DEFAULT_SCHEDULE_POLICY,
  STRATEGY_REGISTRY,
  buildSimulationRun,
  validateBudgetPolicy,
  validateSchedulePolicy,
} from "../src/contracts.mjs";

test("strategy registry is predefined and simulation safe", () => {
  assert.equal(STRATEGY_REGISTRY.length, 3);
  assert.equal(STRATEGY_REGISTRY.some((strategy) => strategy.strategyId === "news-aware-watchlist"), true);
  assert.equal(STRATEGY_REGISTRY.every((strategy) => strategy.status !== "live"), true);
});

test("default budget policy enforces hard limits", () => {
  const result = validateBudgetPolicy(DEFAULT_BUDGET_POLICY);

  assert.equal(result.ok, true);
  assert.deepEqual(DEFAULT_BUDGET_POLICY.selectableBudgetsUsd, [500, 1000, 1500, 2500]);
  assert.equal(DEFAULT_BUDGET_POLICY.baseBudgetUsd, 1000);
  assert.equal(DEFAULT_BUDGET_POLICY.maxConfigurableBudgetUsd, 2500);
  assert.equal(DEFAULT_BUDGET_POLICY.dailyLossStopUsd, 50);
  assert.equal(DEFAULT_BUDGET_POLICY.weeklyLossStopUsd, 150);
  assert.equal(DEFAULT_BUDGET_POLICY.leverage, 1);
});

test("invalid budget policy is rejected before any simulated decision", () => {
  const result = validateBudgetPolicy({
    ...DEFAULT_BUDGET_POLICY,
    baseBudgetUsd: 5000,
    selectableBudgetsUsd: [5000],
  });

  assert.equal(result.ok, false);
  assert.match(result.errors.join(" "), /maximum configurable budget/);
});

test("schedule policy blocks high-frequency trading", () => {
  assert.equal(validateSchedulePolicy(DEFAULT_SCHEDULE_POLICY).ok, true);

  const result = validateSchedulePolicy({
    ...DEFAULT_SCHEDULE_POLICY,
    minimumEvaluationIntervalMinutes: 5,
    highFrequencyTrading: "enabled",
  });

  assert.equal(result.ok, false);
  assert.match(result.errors.join(" "), /high-frequency trading/);
  assert.match(result.errors.join(" "), /240 minutes/);
});

test("simulation run logs why no trade and redacts private state", () => {
  const run = buildSimulationRun();
  const serialized = JSON.stringify(run);

  assert.equal(run.mode, "simulation");
  assert.equal(run.decision, "skip");
  assert.equal(run.riskResult, "blocked");
  assert.equal(run.vetoes.includes("execution-route-absent"), true);
  assert.equal(run.tradeLogEntry.action, "simulated-skip");
  assert.equal(run.tradeLogEntry.accountIdentifiers, "redacted");
  assert.equal(run.positionContext[0].newsContext[0].summary.includes("cannot create an order"), true);
  assert.equal(serialized.includes("apiKey"), false);
  assert.equal(serialized.includes("userKey"), false);
  assert.equal(serialized.includes('"accountId"'), false);
});
