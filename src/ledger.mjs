import { appendFile, mkdir, readFile } from "node:fs/promises";
import { dirname } from "node:path";

const REDACTED = "redacted";
const ABSENT = "absent";

const SENSITIVE_KEY_PATTERN =
  /(account|api[-_]?key|auth|balance|credential|email|jwt|name|oauth|portfolio|secret|statement|token|user[-_]?key)/i;

function incrementHistogram(histogram, values) {
  for (const value of values) {
    histogram[value] = (histogram[value] ?? 0) + 1;
  }
}

function redactValue(value) {
  if (Array.isArray(value)) {
    return value.map((item) => redactValue(item));
  }

  if (value !== null && typeof value === "object") {
    return redactTradeLogEntry(value);
  }

  return value;
}

export function redactTradeLogEntry(entry) {
  const redacted = {};

  for (const [key, value] of Object.entries(entry ?? {})) {
    if (SENSITIVE_KEY_PATTERN.test(key)) {
      redacted[key] = REDACTED;
      continue;
    }

    redacted[key] = redactValue(value);
  }

  return {
    ...redacted,
    accountIdentifiers: REDACTED,
    rawProviderPayloads: ABSENT,
    providerCall: "not-attempted",
    executionRoute: "absent",
  };
}

export function buildLedgerRecord({
  run,
  entry = run?.tradeLogEntry,
  recordedAt = run?.evaluatedAt ?? new Date().toISOString(),
} = {}) {
  if (!run) {
    throw new TypeError("simulation run is required");
  }

  return {
    ledgerVersion: 1,
    recordedAt,
    runId: run.runId,
    mode: "simulation",
    environment: "synthetic",
    strategyId: run.strategyId,
    strategyVersion: run.strategyVersion,
    decision: run.decision,
    riskResult: run.riskResult,
    vetoes: [...run.vetoes],
    tradeLogEntry: redactTradeLogEntry(entry),
  };
}

function buildLedgerRecordDTO(record) {
  const tradeLogEntry = redactTradeLogEntry(record?.tradeLogEntry);

  return {
    ledgerVersion: record?.ledgerVersion ?? 1,
    recordedAt: record?.recordedAt ?? null,
    runId: record?.runId ?? null,
    mode: "simulation",
    environment: "synthetic",
    strategyId: record?.strategyId ?? null,
    strategyVersion: record?.strategyVersion ?? null,
    decision: record?.decision ?? null,
    riskResult: record?.riskResult ?? null,
    vetoes: Array.isArray(record?.vetoes) ? [...record.vetoes] : [],
    tradeLog: {
      action: tradeLogEntry.action ?? null,
      reasonCode: tradeLogEntry.reasonCode ?? null,
      budgetRemainingUsd: Number.isFinite(tradeLogEntry.budgetRemainingUsd)
        ? tradeLogEntry.budgetRemainingUsd
        : null,
      providerCall: tradeLogEntry.providerCall,
      executionRoute: tradeLogEntry.executionRoute,
      accountIdentifiers: tradeLogEntry.accountIdentifiers,
      rawProviderPayloads: tradeLogEntry.rawProviderPayloads,
    },
  };
}

export function buildSimulationLedgerReport({
  records = [],
  generatedAt = new Date("2026-05-15T00:00:00.000Z"),
} = {}) {
  const recordDTOs = records.map((record) => buildLedgerRecordDTO(record));
  const decisionHistogram = {};
  const riskResultHistogram = {};
  const vetoHistogram = {};
  const strategyIds = new Set();
  const recordedAtValues = [];

  for (const record of recordDTOs) {
    incrementHistogram(decisionHistogram, record.decision ? [record.decision] : []);
    incrementHistogram(riskResultHistogram, record.riskResult ? [record.riskResult] : []);
    incrementHistogram(vetoHistogram, record.vetoes);

    if (record.strategyId) {
      strategyIds.add(record.strategyId);
    }

    if (record.recordedAt) {
      recordedAtValues.push(record.recordedAt);
    }
  }

  const sortedRecordedAtValues = [...recordedAtValues].sort();

  return {
    ledgerReportVersion: 1,
    mode: "simulation-ledger-report",
    environment: "synthetic",
    providerCalls: "blocked",
    executionRoutes: "absent",
    demoExecution: "blocked",
    liveExecution: "blocked",
    generatedAt: generatedAt.toISOString(),
    summary: {
      recordCount: recordDTOs.length,
      skipCount: recordDTOs.filter((record) => record.decision === "skip").length,
      blockedCount: recordDTOs.filter((record) => record.riskResult === "blocked").length,
      uniqueRunCount: new Set(recordDTOs.map((record) => record.runId).filter(Boolean)).size,
      strategyIds: [...strategyIds].sort(),
      firstRecordedAt: sortedRecordedAtValues[0] ?? null,
      lastRecordedAt: sortedRecordedAtValues.at(-1) ?? null,
      decisionHistogram,
      riskResultHistogram,
      vetoHistogram,
      redaction: {
        accountIdentifiers: REDACTED,
        rawProviderPayloads: ABSENT,
        providerCall: "not-attempted",
        executionRoute: ABSENT,
      },
    },
    records: recordDTOs,
  };
}

export async function appendSimulationLedgerRecord(ledgerPath, record) {
  if (!ledgerPath) {
    throw new TypeError("ledger path is required");
  }

  await mkdir(dirname(ledgerPath), { recursive: true });
  await appendFile(ledgerPath, `${JSON.stringify(record)}\n`, { encoding: "utf8", flag: "a" });

  return record;
}

export async function readSimulationLedgerRecords(ledgerPath) {
  const content = await readFile(ledgerPath, "utf8");

  return content
    .split("\n")
    .filter(Boolean)
    .map((line) => JSON.parse(line));
}

export async function exportSimulationLedgerReport(ledgerPath, options = {}) {
  const records = await readSimulationLedgerRecords(ledgerPath);

  return buildSimulationLedgerReport({
    records,
    ...options,
  });
}
