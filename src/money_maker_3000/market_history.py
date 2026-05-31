from __future__ import annotations

import csv
import hashlib
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Iterator, TextIO

from money_maker_3000.contracts import SYMBOL_PATTERN

PARSER_VERSION = "0.1.0-streaming-stdlib"
REQUIRED_MARKET_HISTORY_COLUMNS = ("symbol", "date", "open", "high", "low", "close", "volume", "source")
PERFORMANCE_PERIODS = (
    ("24h", 1),
    ("1w", 7),
    ("1m", 31),
    ("1y", 366),
    ("5y", 366 * 5),
    ("max", None),
)
SENSITIVE_COLUMN_MARKERS = (
    "account",
    "balance",
    "credential",
    "holding",
    "order",
    "password",
    "portfolio",
    "position",
    "secret",
    "token",
    "transaction",
    "user",
)


@dataclass(frozen=True)
class Bar:
    symbol: str
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    source: str

    def to_dict(self) -> dict[str, str | float]:
        return asdict(self)


@dataclass
class MarketHistoryAccumulator:
    symbol: str | None = None
    source: str | None = None
    row_count: int = 0
    first_date: str | None = None
    last_date: str | None = None
    close_min: float | None = None
    close_max: float | None = None
    volume_total: float = 0.0

    def update(self, bar: Bar) -> None:
        if self.symbol is None:
            self.symbol = bar.symbol
        elif self.symbol != bar.symbol:
            self.symbol = "mixed"
        if self.source is None:
            self.source = bar.source
        elif self.source != bar.source:
            self.source = "mixed"
        if self.first_date is None:
            self.first_date = bar.date
        self.last_date = bar.date
        self.row_count += 1
        self.close_min = bar.close if self.close_min is None else min(self.close_min, bar.close)
        self.close_max = bar.close if self.close_max is None else max(self.close_max, bar.close)
        self.volume_total += bar.volume

    def to_summary(self) -> dict[str, object]:
        if self.row_count == 0:
            raise ValueError("market history bars are required")
        return {
            "symbol": self.symbol,
            "source": self.source,
            "barCount": self.row_count,
            "firstDate": self.first_date,
            "lastDate": self.last_date,
            "closeMin": self.close_min,
            "closeMax": self.close_max,
            "volumeTotal": self.volume_total,
            "providerCalls": "blocked",
            "accountData": "absent",
            "execution": "blocked",
            "parserVersion": PARSER_VERSION,
        }


def _validate_header(header: list[str]) -> None:
    sensitive_columns = [
        column for column in header if any(marker in column.lower() for marker in SENSITIVE_COLUMN_MARKERS)
    ]
    if sensitive_columns:
        raise ValueError(f"market history CSV includes account-linked columns: {', '.join(sensitive_columns)}")
    if tuple(header) != REQUIRED_MARKET_HISTORY_COLUMNS:
        raise ValueError(f"market history CSV header must be exactly: {','.join(REQUIRED_MARKET_HISTORY_COLUMNS)}")


def _parse_positive_float(raw: str, field: str, row_number: int, *, allow_zero: bool = True) -> float:
    try:
        parsed = float(raw)
    except ValueError as exc:
        raise ValueError(f"market history row {row_number} {field} must be a finite number") from exc
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        raise ValueError(f"market history row {row_number} {field} must be a finite number")
    if parsed < 0 or (parsed == 0 and not allow_zero):
        raise ValueError(f"market history row {row_number} {field} must be positive")
    return parsed


def _validate_iso_date(raw: str, row_number: int) -> None:
    try:
        parsed = date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"market history row {row_number} date must be a valid calendar date") from exc
    if parsed.isoformat() != raw:
        raise ValueError(f"market history row {row_number} date must be YYYY-MM-DD")


def iter_market_history_bars(csv_file: TextIO, *, selected_symbol: str) -> Iterator[Bar]:
    expected_symbol = selected_symbol.strip().upper()
    if not SYMBOL_PATTERN.fullmatch(expected_symbol):
        raise ValueError("selected instrument symbol must be an uppercase market symbol")

    reader = csv.DictReader(csv_file)
    header = list(reader.fieldnames or [])
    _validate_header(header)

    previous_date: str | None = None
    yielded = False
    for row_index, row in enumerate(reader, start=2):
        if set(row) != set(REQUIRED_MARKET_HISTORY_COLUMNS):
            raise ValueError(f"market history row {row_index} must have {len(REQUIRED_MARKET_HISTORY_COLUMNS)} columns")
        symbol = str(row["symbol"]).strip().upper()
        if not SYMBOL_PATTERN.fullmatch(symbol):
            raise ValueError(f"market history row {row_index} symbol must be an uppercase market symbol")
        if symbol != expected_symbol:
            raise ValueError(f"market history row {row_index} symbol {symbol} does not match {expected_symbol}")

        raw_date = str(row["date"]).strip()
        _validate_iso_date(raw_date, row_index)
        if previous_date is not None and raw_date <= previous_date:
            raise ValueError(f"market history row {row_index} date must be after the previous row")
        previous_date = raw_date

        open_price = _parse_positive_float(row["open"], "open", row_index, allow_zero=False)
        high = _parse_positive_float(row["high"], "high", row_index, allow_zero=False)
        low = _parse_positive_float(row["low"], "low", row_index, allow_zero=False)
        close = _parse_positive_float(row["close"], "close", row_index, allow_zero=False)
        volume = _parse_positive_float(row["volume"], "volume", row_index)
        if high < max(open_price, low, close):
            raise ValueError(f"market history row {row_index} high must be at least open, low, and close")
        if low > min(open_price, high, close):
            raise ValueError(f"market history row {row_index} low must be no greater than open, high, and close")
        source = str(row["source"]).strip()
        if not source:
            raise ValueError(f"market history row {row_index} source is required")

        yielded = True
        yield Bar(symbol=symbol, date=raw_date, open=open_price, high=high, low=low, close=close, volume=volume, source=source)

    if not yielded:
        raise ValueError("market history CSV must include at least one row")


def parse_market_history_csv(csv_text: str, *, selected_symbol: str) -> list[Bar]:
    from io import StringIO

    return list(iter_market_history_bars(StringIO(str(csv_text).lstrip("\ufeff")), selected_symbol=selected_symbol))


def summarize_market_history_bars(bars: Iterable[Bar]) -> dict[str, object]:
    accumulator = MarketHistoryAccumulator()
    for bar in bars:
        accumulator.update(bar)
    return accumulator.to_summary()


def build_period_performance_diagnostics(bars: Iterable[Bar]) -> dict[str, object]:
    """Build market-history change diagnostics without profitability claims."""
    collected: list[Bar] = []
    for bar in bars:
        collected.append(bar)
    if not collected:
        raise ValueError("market history bars are required")

    symbol = _summarize_identity(bar.symbol for bar in collected)
    source = _summarize_identity(bar.source for bar in collected)
    latest = collected[-1]
    latest_date = date.fromisoformat(latest.date)
    periods = [
        _build_period_diagnostic(period_id, days, collected, latest, latest_date)
        for period_id, days in PERFORMANCE_PERIODS
    ]
    return {
        "dtoVersion": "market-period-diagnostics.v1",
        "symbol": symbol,
        "source": source,
        "latestDate": latest.date,
        "latestClose": latest.close,
        "periods": periods,
        "providerCalls": "blocked",
        "accountData": "absent",
        "execution": "blocked",
        "parserVersion": PARSER_VERSION,
        "performanceClaims": "market-history-change-only-no-pnl-or-execution-quality-metrics",
    }


def _summarize_identity(values: Iterable[str]) -> str:
    seen: str | None = None
    for value in values:
        if seen is None:
            seen = value
        elif seen != value:
            return "mixed"
    if seen is None:
        raise ValueError("identity values are required")
    return seen


def _build_period_diagnostic(
    period_id: str,
    days: int | None,
    bars: list[Bar],
    latest: Bar,
    latest_date: date,
) -> dict[str, object]:
    if days is None:
        window_bars = bars
        requested_start = None
        coverage_state = "available" if len(window_bars) >= 2 else "insufficient-history"
    else:
        cutoff = latest_date - timedelta(days=days)
        requested_start = cutoff.isoformat()
        window_bars = [bar for bar in bars if date.fromisoformat(bar.date) >= cutoff]
        if len(window_bars) < 2:
            coverage_state = "insufficient-history"
        elif date.fromisoformat(window_bars[0].date) > cutoff:
            coverage_state = "partial-history"
        else:
            coverage_state = "available"

    start = window_bars[0]
    change_absolute = latest.close - start.close
    change_percent = (change_absolute / start.close) * 100
    return {
        "period": period_id,
        "requestedWindowDays": days,
        "requestedStartDate": requested_start,
        "startDate": start.date,
        "endDate": latest.date,
        "startClose": start.close,
        "endClose": latest.close,
        "changeAbsolute": round(change_absolute, 6),
        "changePercent": round(change_percent, 6),
        "barCount": len(window_bars),
        "coverageState": coverage_state,
        "source": _summarize_identity(bar.source for bar in window_bars),
        "performanceClaims": "market-history-change-only",
    }


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
