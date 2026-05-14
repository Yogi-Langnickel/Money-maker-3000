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
  assert.equal(report.summary.minRemainingBudgetUsd, 500);
  assert.equal(report.summary.maxRemainingBudgetUsd, 1500);
  assert.equal(report.summary.vetoHistogram["provider-not-connected"], 4);
  assert.equal(report.summary.vetoHistogram["execution-route-absent"], 4);
  assert.equal(report.summary.warningHistogram["strategy is context-only and cannot create simulated trade intent"], 1);
  assert.equal(report.runs.every((run) => run.decision === "skip"), true);
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
