import { appendFile, mkdir, readFile } from "node:fs/promises";
import { dirname } from "node:path";

const REDACTED = "redacted";
const ABSENT = "absent";

const SENSITIVE_KEY_PATTERN =
  /(account|api[-_]?key|auth|balance|credential|email|jwt|name|oauth|portfolio|secret|statement|token|user[-_]?key)/i;

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
