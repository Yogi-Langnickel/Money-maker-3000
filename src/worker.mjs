import { buildSelectedBacktest } from "./backtest.mjs";
import { buildSimulationRun } from "./contracts.mjs";
import {
  appendSimulationLedgerRecord,
  buildLedgerRecord,
  exportSimulationLedgerReport,
} from "./ledger.mjs";

const CLI_OPTION_ALIASES = Object.freeze({
  "--mode": "runMode",
  "--run-mode": "runMode",
  "--strategy": "strategyId",
  "--strategy-id": "strategyId",
  "--symbol": "symbol",
  "--market": "market",
  "--instrument-class": "instrumentClass",
  "--ledger-report": "ledgerReportPath",
  "--export-ledger-report": "ledgerReportPath",
});

function readCliOptionValue(args, index, rawArg) {
  const equalsIndex = rawArg.indexOf("=");

  if (equalsIndex !== -1) {
    return {
      value: rawArg.slice(equalsIndex + 1),
      nextIndex: index,
    };
  }

  return {
    value: args[index + 1],
    nextIndex: index + 1,
  };
}

export function parseWorkerCliArgs(args = []) {
  const parsed = {};

  for (let index = 0; index < args.length; index += 1) {
    const rawArg = args[index];
    const optionName = rawArg.includes("=") ? rawArg.slice(0, rawArg.indexOf("=")) : rawArg;
    const optionKey = CLI_OPTION_ALIASES[optionName];

    if (!optionKey) {
      throw new Error(`unsupported CLI option: ${optionName}`);
    }

    const { value, nextIndex } = readCliOptionValue(args, index, rawArg);
    if (!value || value.startsWith("--")) {
      throw new Error(`missing value for ${optionName}`);
    }

    parsed[optionKey] = value;
    index = nextIndex;
  }

  const runMode = parsed.runMode ?? "backtest";
  if (["execute", "trade", "trading"].includes(runMode)) {
    throw new Error("execution mode is disabled; only backtest mode is currently allowed");
  }

  if (parsed.ledgerReportPath) {
    return {
      command: "ledger-report",
      ledgerPath: parsed.ledgerReportPath,
      runMode,
    };
  }

  const selectedInstrument =
    parsed.symbol || parsed.market || parsed.instrumentClass
      ? {
          ...(parsed.symbol ? { symbol: parsed.symbol } : {}),
          ...(parsed.market ? { market: parsed.market } : {}),
          ...(parsed.instrumentClass ? { instrumentClass: parsed.instrumentClass } : {}),
        }
      : undefined;

  return {
    runMode,
    ...(parsed.strategyId ? { strategyId: parsed.strategyId } : {}),
    simulationConfig: {
      runMode,
      ...(parsed.strategyId ? { strategyId: parsed.strategyId } : {}),
      ...(selectedInstrument ? { selectedInstrument } : {}),
    },
  };
}

export function runSelectedBacktest(options = {}) {
  if (options.runMode && options.runMode !== "backtest") {
    throw new Error("execution mode is disabled; only backtest mode is currently allowed");
  }

  return buildSelectedBacktest({
    strategyId: options.strategyId ?? options.simulationConfig?.strategyId,
    selectedInstrument: options.simulationConfig?.selectedInstrument,
    budgetUsd: options.simulationConfig?.budgetUsd,
  });
}

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

export async function exportLedgerReportCli({ ledgerPath, runMode } = {}) {
  if (runMode && runMode !== "backtest") {
    throw new Error("execution mode is disabled; only backtest mode is currently allowed");
  }

  if (!ledgerPath) {
    throw new Error("ledger path is required");
  }

  return exportSimulationLedgerReport(ledgerPath);
}

if (process.argv[1] === new URL(import.meta.url).pathname) {
  try {
    const options = parseWorkerCliArgs(process.argv.slice(2));
    const result =
      options.command === "ledger-report"
        ? await exportLedgerReportCli(options)
        : runSelectedBacktest(options);
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  } catch (error) {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  }
}
