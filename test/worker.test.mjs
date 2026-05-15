import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { join } from "node:path";
import test from "node:test";
import { tmpdir } from "node:os";
import {
  DEFAULT_BUDGET_POLICY,
  DEFAULT_SCHEDULE_POLICY,
  DEFAULT_SIMULATION_CONFIG,
  STRATEGY_REGISTRY,
  buildSimulationRun,
  validateBudgetPolicy,
  validateSchedulePolicy,
  validateSimulationConfig,
} from "../src/contracts.mjs";
import {
  appendSimulationLedgerRecord,
  buildLedgerRecord,
  readSimulationLedgerRecords,
  redactTradeLogEntry,
} from "../src/ledger.mjs";
import {
  PROVIDER_REGISTRY,
  buildProviderMetadataSnapshot,
  validateProviderMetadata,
} from "../src/providers.mjs";
import { runOnceAndAppendLedger } from "../src/worker.mjs";

test("strategy registry is predefined and simulation safe", () => {
  assert.equal(STRATEGY_REGISTRY.length, 3);
  assert.equal(STRATEGY_REGISTRY.some((strategy) => strategy.strategyId === "news-aware-watchlist"), true);
  assert.equal(STRATEGY_REGISTRY.every((strategy) => strategy.status !== "live"), true);
});

test("provider registry exposes metadata only and blocks all provider capabilities", () => {
  const snapshot = buildProviderMetadataSnapshot();
  const validation = validateProviderMetadata(PROVIDER_REGISTRY[0]);
  const provider = snapshot.providers[0];
  const serialized = JSON.stringify(snapshot);

  assert.equal(validation.ok, true);
  assert.equal(snapshot.mode, "metadata-only");
  assert.equal(snapshot.providerCalls, "blocked");
  assert.equal(snapshot.credentials, "not-loaded");
  assert.equal(snapshot.executionRoutes, "absent");
  assert.equal(provider.providerId, "etoro");
  assert.equal(provider.status, "metadata-only");
  assert.equal(provider.demoExecution, "blocked");
  assert.equal(provider.liveExecution, "blocked");
  assert.deepEqual(provider.supportedModes, ["simulation"]);
  assert.equal(Object.values(provider.capabilities).every((value) => value !== "enabled"), true);
  assert.equal(snapshot.validation.ok, true);
  assert.equal(serialized.includes("apiKey"), false);
  assert.equal(serialized.includes("accessToken"), false);
  assert.equal(serialized.includes('"accountId"'), false);
});

test("provider metadata validator rejects enabled calls, credentials, and execution", () => {
  const result = validateProviderMetadata({
    ...PROVIDER_REGISTRY[0],
    providerCalls: "enabled",
    credentials: "loaded",
    accountData: "loaded",
    demoExecution: "enabled",
    liveExecution: "enabled",
    supportedModes: ["simulation", "demo"],
    capabilities: {
      ...PROVIDER_REGISTRY[0].capabilities,
      portfolioRead: "enabled",
    },
  });

  assert.equal(result.ok, false);
  assert.match(result.errors.join(" "), /provider calls/);
  assert.match(result.errors.join(" "), /credentials/);
  assert.match(result.errors.join(" "), /account data/);
  assert.match(result.errors.join(" "), /demo execution/);
  assert.match(result.errors.join(" "), /live execution/);
  assert.match(result.errors.join(" "), /supported modes/);
  assert.match(result.errors.join(" "), /portfolioRead/);
});

test("provider metadata snapshot reports malformed providers without throwing", () => {
  const snapshot = buildProviderMetadataSnapshot({ providers: [null] });

  assert.equal(snapshot.validation.ok, false);
  assert.equal(snapshot.providers[0].providerId, "unknown");
  assert.equal(snapshot.providers[0].status, "invalid");
  assert.match(snapshot.validation.providers[0].errors.join(" "), /provider metadata must be an object/);
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
  assert.equal(run.providerMetadata.mode, "metadata-only");
  assert.equal(run.providerMetadata.providerCalls, "blocked");
  assert.equal(run.providerMetadata.validation.ok, true);
  assert.equal(run.positionContext[0].newsContext[0].summary.includes("cannot create an order"), true);
  assert.equal(serialized.includes("apiKey"), false);
  assert.equal(serialized.includes("userKey"), false);
  assert.equal(serialized.includes('"accountId"'), false);
});

test("simulation config validates predefined strategy, budget, markets, instruments, and cadence", () => {
  const result = validateSimulationConfig(DEFAULT_SIMULATION_CONFIG, {
    strategyRegistry: STRATEGY_REGISTRY,
    budgetPolicy: DEFAULT_BUDGET_POLICY,
    schedulePolicy: DEFAULT_SCHEDULE_POLICY,
  });

  assert.equal(result.ok, true);
  assert.deepEqual(DEFAULT_SIMULATION_CONFIG.allowedMarkets, ["US", "AU"]);
  assert.deepEqual(DEFAULT_SIMULATION_CONFIG.allowedInstrumentClasses, ["EQUITY", "ETF"]);
  assert.equal(DEFAULT_SIMULATION_CONFIG.cadence.minimumEvaluationIntervalMinutes, 240);
  assert.equal(DEFAULT_SIMULATION_CONFIG.execution.providerCalls, "blocked");
});

test("simulation config rejects unregistered strategies, blocked instruments, HFT cadence, and provider calls", () => {
  const result = validateSimulationConfig(
    {
      ...DEFAULT_SIMULATION_CONFIG,
      strategyId: "operator-uploaded-code",
      budgetUsd: 5000,
      allowedMarkets: ["US", "BINANCE"],
      allowedInstrumentClasses: ["EQUITY", "CRYPTO"],
      cadence: {
        mode: "high-frequency",
        minimumEvaluationIntervalMinutes: 5,
        maxDecisionsPerDay: 100,
      },
      execution: {
        ...DEFAULT_SIMULATION_CONFIG.execution,
        providerCalls: "enabled",
        liveTrading: "enabled",
      },
    },
    {
      strategyRegistry: STRATEGY_REGISTRY,
      budgetPolicy: DEFAULT_BUDGET_POLICY,
      schedulePolicy: DEFAULT_SCHEDULE_POLICY,
    },
  );

  assert.equal(result.ok, false);
  assert.match(result.errors.join(" "), /predefined registry/);
  assert.match(result.errors.join(" "), /maximum configurable budget/);
  assert.match(result.errors.join(" "), /unsupported markets/);
  assert.match(result.errors.join(" "), /unsupported instrument classes/);
  assert.match(result.errors.join(" "), /minimum evaluation interval/);
  assert.match(result.errors.join(" "), /provider calls/);
  assert.match(result.errors.join(" "), /live trading/);
});

test("simulation run surfaces invalid config as a risk veto", () => {
  const run = buildSimulationRun({
    simulationConfig: {
      ...DEFAULT_SIMULATION_CONFIG,
      cadence: {
        ...DEFAULT_SIMULATION_CONFIG.cadence,
        minimumEvaluationIntervalMinutes: 1,
      },
    },
  });

  assert.equal(run.configValidation.ok, false);
  assert.equal(run.vetoes.includes("invalid-simulation-config"), true);
  assert.equal(run.vetoes.includes("execution-route-absent"), true);
});

test("simulation run preserves the existing strategyId option without a full config", () => {
  const run = buildSimulationRun({ strategyId: "threshold-rebalance" });

  assert.equal(run.strategyId, "threshold-rebalance");
  assert.equal(run.simulationConfig.strategyId, "threshold-rebalance");
  assert.equal(run.configValidation.ok, true);
});

test("ledger records are append-only JSONL and redact sensitive trade log fields", async () => {
  const tempDir = await mkdtemp(join(tmpdir(), "money-maker-ledger-"));
  const ledgerPath = join(tempDir, "simulation-ledger.jsonl");

  try {
    const run = buildSimulationRun();
    const record = buildLedgerRecord({
      run,
      entry: {
        ...run.tradeLogEntry,
        accountId: "acct-real-123",
        apiKey: "secret",
        nested: {
          userKey: "user-secret",
          safeReason: "synthetic-test",
        },
      },
    });

    await appendSimulationLedgerRecord(ledgerPath, record);
    await appendSimulationLedgerRecord(ledgerPath, { ...record, runId: "sim-second" });

    const records = await readSimulationLedgerRecords(ledgerPath);
    const serialized = JSON.stringify(records);

    assert.equal(records.length, 2);
    assert.equal(records[0].tradeLogEntry.accountId, "redacted");
    assert.equal(records[0].tradeLogEntry.apiKey, "redacted");
    assert.equal(records[0].tradeLogEntry.nested.userKey, "redacted");
    assert.equal(records[0].tradeLogEntry.nested.safeReason, "synthetic-test");
    assert.equal(records[0].tradeLogEntry.rawProviderPayloads, "absent");
    assert.equal(records[1].runId, "sim-second");
    assert.equal(serialized.includes("acct-real-123"), false);
    assert.equal(serialized.includes("secret"), false);
  } finally {
    await rm(tempDir, { recursive: true, force: true });
  }
});

test("worker can append a redacted simulation ledger record when a path is provided", async () => {
  const tempDir = await mkdtemp(join(tmpdir(), "money-maker-worker-ledger-"));
  const ledgerPath = join(tempDir, "simulation-ledger.jsonl");

  try {
    const { run, ledgerRecord } = await runOnceAndAppendLedger({ ledgerPath });
    const records = await readSimulationLedgerRecords(ledgerPath);

    assert.equal(run.mode, "simulation");
    assert.equal(ledgerRecord.runId, run.runId);
    assert.equal(records.length, 1);
    assert.equal(records[0].tradeLogEntry.accountIdentifiers, "redacted");
    assert.equal(records[0].tradeLogEntry.providerCall, "not-attempted");
  } finally {
    await rm(tempDir, { recursive: true, force: true });
  }
});

test("trade log redaction preserves safe synthetic fields", () => {
  const redacted = redactTradeLogEntry({
    action: "simulated-skip",
    reasonCode: "provider-not-connected",
    portfolioBalance: 12345,
    OAuthToken: "token",
  });

  assert.equal(redacted.action, "simulated-skip");
  assert.equal(redacted.reasonCode, "provider-not-connected");
  assert.equal(redacted.portfolioBalance, "redacted");
  assert.equal(redacted.OAuthToken, "redacted");
  assert.equal(redacted.providerCall, "not-attempted");
});
