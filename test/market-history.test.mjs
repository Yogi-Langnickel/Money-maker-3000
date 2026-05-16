import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { buildHistoricalFixtureBacktest } from "../src/backtest.mjs";
import { parseMarketHistoryCsv, summarizeMarketHistoryBars } from "../src/market-history.mjs";

const selectedSpy = Object.freeze({
  symbol: "SPY",
  market: "US_EQUITIES",
  instrumentClass: "ETF",
});

async function readSpyFixture() {
  return readFile(new URL("./fixtures/market-history/spy-daily.csv", import.meta.url), "utf8");
}

test("market history parser normalizes a deterministic offline fixture", async () => {
  const bars = parseMarketHistoryCsv(await readSpyFixture(), { selectedInstrument: selectedSpy });
  const summary = summarizeMarketHistoryBars(bars);

  assert.equal(bars.length, 3);
  assert.deepEqual(bars[0], {
    symbol: "SPY",
    date: "2026-05-11",
    open: 520.1,
    high: 523.2,
    low: 519.4,
    close: 522.5,
    volume: 71000000,
    source: "public-test-fixture",
  });
  assert.deepEqual(summary, {
    symbol: "SPY",
    source: "public-test-fixture",
    barCount: 3,
    firstDate: "2026-05-11",
    lastDate: "2026-05-13",
    closeMin: 522.5,
    closeMax: 525.7,
    volumeTotal: 212500000,
    providerCalls: "blocked",
    accountData: "absent",
    execution: "blocked",
  });
});

test("market history parser rejects invalid schemas and account-linked columns", () => {
  assert.throws(
    () =>
      parseMarketHistoryCsv("symbol,date,open,high,low,close,volume,source,accountId\nSPY,2026-05-11,1,2,1,2,3,x", {
        selectedInstrument: selectedSpy,
      }),
    /account-linked columns/,
  );
  assert.throws(
    () =>
      parseMarketHistoryCsv("symbol,date,open,high,low,close,source\nSPY,2026-05-11,1,2,1,2,x", {
        selectedInstrument: selectedSpy,
      }),
    /header must be exactly/,
  );
});

test("market history parser rejects bad rows and selected-instrument mismatches", () => {
  const header = "symbol,date,open,high,low,close,volume,source";

  assert.throws(
    () => parseMarketHistoryCsv(`${header}\nSPY,2026-02-31,1,2,1,2,3,x`, { selectedInstrument: selectedSpy }),
    /valid calendar date/,
  );
  assert.throws(
    () =>
      parseMarketHistoryCsv(
        `${header}\nSPY,2026-05-12,1,2,1,2,3,x\nSPY,2026-05-11,1,2,1,2,3,x`,
        { selectedInstrument: selectedSpy },
      ),
    /after the previous row/,
  );
  assert.throws(
    () => parseMarketHistoryCsv(`${header}\nSPY,2026-05-11,1,2,1,NaN,3,x`, { selectedInstrument: selectedSpy }),
    /close must be a finite number/,
  );
  assert.throws(
    () => parseMarketHistoryCsv(`${header}\nSPY,2026-05-11,4,3,1,2,3,x`, { selectedInstrument: selectedSpy }),
    /high must be at least/,
  );
  assert.throws(
    () => parseMarketHistoryCsv(`${header}\nSPY,2026-05-11,1,4,3,2,3,x`, { selectedInstrument: selectedSpy }),
    /low must be no greater/,
  );
  assert.throws(
    () => parseMarketHistoryCsv(`${header}\nGLD,2026-05-11,1,2,1,2,3,x`, { selectedInstrument: selectedSpy }),
    /does not match SPY/,
  );
});

test("historical fixture backtest keeps providers, execution, and account data blocked", async () => {
  const bars = parseMarketHistoryCsv(await readSpyFixture(), { selectedInstrument: selectedSpy });
  const report = buildHistoricalFixtureBacktest({
    bars,
    selectedInstrument: selectedSpy,
    startedAt: new Date("2026-05-15T00:00:00.000Z"),
  });
  const serialized = JSON.stringify(report);

  assert.equal(report.mode, "historical-fixture-backtest");
  assert.equal(report.environment, "offline-fixture");
  assert.equal(report.providerCalls, "blocked");
  assert.equal(report.executionRoutes, "absent");
  assert.equal(report.accountData, "absent");
  assert.equal(report.history.symbol, "SPY");
  assert.equal(report.history.providerCalls, "blocked");
  assert.equal(report.summary.runCount, 1);
  assert.equal(report.summary.vetoHistogram["provider-not-connected"], 1);
  assert.equal(report.summary.vetoHistogram["execution-route-absent"], 1);
  assert.equal(report.scenarioSummaries[0].selectedInstrument.symbol, "SPY");
  assert.equal(report.scenarioSummaries[0].providerCalls, "blocked");
  assert.equal(report.scenarioSummaries[0].executionRoute, "absent");
  assert.equal(serialized.includes("apiKey"), false);
  assert.equal(serialized.includes("userKey"), false);
  assert.equal(serialized.includes("accountId"), false);
  assert.equal(serialized.includes("positionId"), false);
  assert.equal(serialized.includes("orderId"), false);
  assert.equal(serialized.includes("pnl"), false);
  assert.equal(serialized.includes("winRate"), false);
  assert.equal(serialized.includes("drawdown"), false);
  assert.equal(serialized.includes("sharpe"), false);
});
