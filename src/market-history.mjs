const REQUIRED_MARKET_HISTORY_COLUMNS = Object.freeze([
  "symbol",
  "date",
  "open",
  "high",
  "low",
  "close",
  "volume",
  "source",
]);

const SENSITIVE_COLUMN_PATTERN =
  /(account|balance|credential|holding|order|password|portfolio|position|secret|token|transaction|user)/i;
const ISO_DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
const SYMBOL_PATTERN = /^[A-Z0-9][A-Z0-9.-]{0,14}$/;

function parseCsvLine(line) {
  return line.split(",").map((value) => value.trim());
}

function parseFiniteNumber(value, { field, rowNumber, allowZero = true }) {
  const parsed = Number(value);

  if (!Number.isFinite(parsed)) {
    throw new Error(`market history row ${rowNumber} ${field} must be a finite number`);
  }

  if (parsed < 0 || (!allowZero && parsed === 0)) {
    throw new Error(`market history row ${rowNumber} ${field} must be positive`);
  }

  return parsed;
}

function assertValidIsoDate(date, rowNumber) {
  if (!ISO_DATE_PATTERN.test(date)) {
    throw new Error(`market history row ${rowNumber} date must be YYYY-MM-DD`);
  }

  const parsed = new Date(`${date}T00:00:00.000Z`);
  if (Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== date) {
    throw new Error(`market history row ${rowNumber} date must be a valid calendar date`);
  }
}

function assertExpectedHeader(header) {
  const sensitiveColumns = header.filter((column) => SENSITIVE_COLUMN_PATTERN.test(column));
  if (sensitiveColumns.length > 0) {
    throw new Error(`market history CSV includes account-linked columns: ${sensitiveColumns.join(", ")}`);
  }

  if (
    header.length !== REQUIRED_MARKET_HISTORY_COLUMNS.length ||
    header.some((column, index) => column !== REQUIRED_MARKET_HISTORY_COLUMNS[index])
  ) {
    throw new Error(
      `market history CSV header must be exactly: ${REQUIRED_MARKET_HISTORY_COLUMNS.join(",")}`,
    );
  }
}

export function parseMarketHistoryCsv(csvText, { selectedInstrument } = {}) {
  if (!selectedInstrument?.symbol || typeof selectedInstrument.symbol !== "string") {
    throw new Error("selected instrument symbol is required for market history replay");
  }

  const expectedSymbol = selectedInstrument.symbol.trim().toUpperCase();
  if (!SYMBOL_PATTERN.test(expectedSymbol)) {
    throw new Error("selected instrument symbol must be an uppercase market symbol");
  }

  const lines = String(csvText ?? "")
    .replace(/^\uFEFF/, "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);

  if (lines.length < 2) {
    throw new Error("market history CSV must include a header and at least one row");
  }

  const header = parseCsvLine(lines[0]);
  assertExpectedHeader(header);

  const bars = [];
  let previousDate = null;

  for (let index = 1; index < lines.length; index += 1) {
    const rowNumber = index + 1;
    const values = parseCsvLine(lines[index]);
    if (values.length !== header.length) {
      throw new Error(`market history row ${rowNumber} must have ${header.length} columns`);
    }

    const row = Object.fromEntries(header.map((column, columnIndex) => [column, values[columnIndex]]));
    const symbol = row.symbol.toUpperCase();

    if (!SYMBOL_PATTERN.test(symbol)) {
      throw new Error(`market history row ${rowNumber} symbol must be an uppercase market symbol`);
    }

    if (symbol !== expectedSymbol) {
      throw new Error(`market history row ${rowNumber} symbol ${symbol} does not match ${expectedSymbol}`);
    }

    assertValidIsoDate(row.date, rowNumber);
    if (previousDate !== null && row.date <= previousDate) {
      throw new Error(`market history row ${rowNumber} date must be after the previous row`);
    }
    previousDate = row.date;

    const open = parseFiniteNumber(row.open, { field: "open", rowNumber, allowZero: false });
    const high = parseFiniteNumber(row.high, { field: "high", rowNumber, allowZero: false });
    const low = parseFiniteNumber(row.low, { field: "low", rowNumber, allowZero: false });
    const close = parseFiniteNumber(row.close, { field: "close", rowNumber, allowZero: false });
    const volume = parseFiniteNumber(row.volume, { field: "volume", rowNumber });

    if (high < Math.max(open, low, close)) {
      throw new Error(`market history row ${rowNumber} high must be at least open, low, and close`);
    }

    if (low > Math.min(open, high, close)) {
      throw new Error(`market history row ${rowNumber} low must be no greater than open, high, and close`);
    }

    if (!row.source) {
      throw new Error(`market history row ${rowNumber} source is required`);
    }

    bars.push({
      symbol,
      date: row.date,
      open,
      high,
      low,
      close,
      volume,
      source: row.source,
    });
  }

  return bars;
}

export function summarizeMarketHistoryBars(bars) {
  if (!Array.isArray(bars) || bars.length === 0) {
    throw new Error("market history bars are required");
  }

  const symbols = [...new Set(bars.map((bar) => bar.symbol))];
  const sources = [...new Set(bars.map((bar) => bar.source))];
  const closes = bars.map((bar) => bar.close);
  const volumeTotal = bars.reduce((sum, bar) => sum + bar.volume, 0);

  return {
    symbol: symbols.length === 1 ? symbols[0] : "mixed",
    source: sources.length === 1 ? sources[0] : "mixed",
    barCount: bars.length,
    firstDate: bars[0].date,
    lastDate: bars.at(-1).date,
    closeMin: Math.min(...closes),
    closeMax: Math.max(...closes),
    volumeTotal,
    providerCalls: "blocked",
    accountData: "absent",
    execution: "blocked",
  };
}

export { REQUIRED_MARKET_HISTORY_COLUMNS };
