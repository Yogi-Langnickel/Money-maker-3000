import { buildSimulationRun } from "./contracts.mjs";
import { appendSimulationLedgerRecord, buildLedgerRecord } from "./ledger.mjs";

export function runOnce(options = {}) {
  return buildSimulationRun(options);
}

export async function runOnceAndAppendLedger({ ledgerPath, ...options } = {}) {
  const run = runOnce(options);
  const ledgerRecord = buildLedgerRecord({ run });

  if (ledgerPath) {
    await appendSimulationLedgerRecord(ledgerPath, ledgerRecord);
  }

  return {
    run,
    ledgerRecord,
  };
}

if (process.argv[1] === new URL(import.meta.url).pathname) {
  const result = runOnce();
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
}
