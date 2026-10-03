"""Instrument-centred, offline simulated-profit hypothesis research v1.

This module is deliberately a research diagnostic.  It accepts already-local
candle facts only, has no collector/client imports, and cannot create an order
intent.  Simulated P&L is permitted only in the labelled artifacts it creates.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import fcntl
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import signal_toolkit as S
from . import research_cycle as R
from . import learning as L

VERSION = "instrument-profit-hypothesis.v1"
ALLOWED_INTERVALS = ("1h", "4h", "1d", "1w")
INTERVAL_SECONDS = {"1h": 60 * 60, "4h": 4 * 60 * 60, "1d": 24 * 60 * 60, "1w": 7 * 24 * 60 * 60}
TIMEFRAME_RANK = {name: index for index, name in enumerate(ALLOWED_INTERVALS)}
INSTRUMENTS = {
    "SPY": {"product": "ETF", "currency": "USD", "session": "US-equities-regular"},
    "QQQ": {"product": "ETF", "currency": "USD", "session": "US-equities-regular"},
    "VAS": {"product": "ETF", "currency": "AUD", "session": "AU-equities-regular"},
}
MAX_CANDLES = 1000
GRADE_RUBRIC = {"version": "profit-hypothesis-grade.v2", "missingEvidence": "unclear; fewer than ten closed trades is insufficient; dependent retrospective samples never establish independent confirmation", "unlikely": "nonpositive net simulated return or no benchmark improvement", "weak": "positive simulated edge but cost or chronological stability fails", "strong": "at least ten closed trades, positive against benchmark, positive double-cost sensitivity and every frozen stability slice; retrospective monitoring candidate only, independence unproven"}


class HypothesisError(ValueError):
    """A controlled, value-blind rejected-research condition."""


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise HypothesisError(code)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _time(value: Any) -> datetime:
    try:
        _require(type(value) is str, "hypothesis-invalid-timestamp")
        item = datetime.fromisoformat(value.replace("Z", "+00:00"))
        _require(item.tzinfo is not None, "hypothesis-invalid-timestamp")
        return item.astimezone(timezone.utc)
    except ValueError:
        raise HypothesisError("hypothesis-invalid-timestamp") from None


def _stamp(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    return _time(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _finite(value: Any, *, positive: bool = False) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value) and (value > 0 if positive else value >= 0)
    except OverflowError:
        return False


def _bar(value: Any, interval: str, index: int) -> dict[str, Any]:
    _require(type(value) is dict and set(value) == {"start", "end", "availableAt", "open", "high", "low", "close", "volume", "provenance"}, "hypothesis-invalid-candle")
    start, end, available = _time(value["start"]), _time(value["end"]), _time(value["availableAt"])
    _require(start < end <= available, "hypothesis-candle-time-order")
    _require(all(_finite(value[name], positive=True) for name in ("open", "high", "low", "close")) and _finite(value["volume"]), "hypothesis-invalid-ohlcv")
    _require(value["high"] >= max(value["open"], value["close"]) and value["low"] <= min(value["open"], value["close"]), "hypothesis-inconsistent-ohlc")
    _require(type(value["provenance"]) is dict and set(value["provenance"]) == {"source", "adjustmentBasis", "retrievedAt", "rights"}, "hypothesis-invalid-provenance")
    _require(type(value["provenance"]["source"]) is str and type(value["provenance"]["adjustmentBasis"]) is str and type(value["provenance"]["rights"]) is str, "hypothesis-invalid-provenance")
    _require(available <= _time(value["provenance"]["retrievedAt"]), "hypothesis-retrieval-before-availability")
    return {**value, "start": _stamp(value["start"]), "end": _stamp(value["end"]), "availableAt": _stamp(value["availableAt"])}


def validate_dataset(config: dict[str, Any]) -> dict[str, Any]:
    """Validate exact local-candle facts and return coverage, never pagination guesses."""
    _require(type(config) is dict and config.get("version") == VERSION, "hypothesis-invalid-config-version")
    instrument = config.get("instrument")
    _require(type(instrument) is dict and set(instrument) == {"symbol", "product", "currency", "session", "source", "rights", "adjustmentBasis"}, "hypothesis-invalid-instrument-contract")
    allowed = INSTRUMENTS.get(instrument.get("symbol"))
    _require(allowed is not None, "hypothesis-unsupported-instrument")
    _require(all(instrument.get(key) == value for key, value in allowed.items()), "hypothesis-instrument-substitution-rejected")
    _require(type(instrument["source"]) is str and type(instrument["rights"]) is str and type(instrument["adjustmentBasis"]) is str, "hypothesis-invalid-instrument-contract")
    datasets = config.get("datasets")
    _require(type(datasets) is dict and 2 <= len(datasets) <= 4 and set(datasets).issubset(ALLOWED_INTERVALS), "hypothesis-interval-count-or-name")
    result, starts, ends = {}, [], []
    for interval in sorted(datasets):
        rows = datasets[interval]
        _require(type(rows) is list and 1 <= len(rows) <= MAX_CANDLES, "hypothesis-candle-count")
        normalized = [_bar(row, interval, index) for index, row in enumerate(rows)]
        previous_start = previous_end = previous_available = None
        for row in normalized:
            current, end, available = _time(row["start"]), _time(row["end"]), _time(row["availableAt"])
            _require((end - current).total_seconds() == INTERVAL_SECONDS[interval], "hypothesis-interval-duration-mismatch")
            _require(previous_start is None or current > previous_start, "hypothesis-unordered-candles")
            # The interval is a chronological fact, not retrieval order.  A
            # later bar cannot become available before an earlier bar, and bars
            # may not overlap; otherwise an old revised/late bar could leak.
            _require(previous_end is None or end > previous_end and current >= previous_end, "hypothesis-overlapping-or-unordered-candles")
            _require(previous_available is None or available >= previous_available, "hypothesis-nonmonotonic-availability")
            _require(row["provenance"]["source"] == instrument["source"] and row["provenance"]["rights"] == instrument["rights"] and row["provenance"]["adjustmentBasis"] == instrument["adjustmentBasis"], "hypothesis-provenance-mismatch")
            if config.get("classification") == "observed-attested" and instrument["session"] in ("US-equities-regular", "AU-equities-regular"):
                _require(current.weekday() < 5 and end.weekday() < 5, "hypothesis-regular-session-weekend-candle")
            previous_start, previous_end, previous_available = current, end, available
        starts.append(_time(normalized[0]["start"])); ends.append(_time(normalized[-1]["end"]))
        result[interval] = {"rows": normalized, "sha256": _digest(normalized), "count": len(normalized),
                            "coverage": {"start": normalized[0]["start"], "end": normalized[-1]["end"],
                                         "firstAvailableAt": normalized[0]["availableAt"], "lastAvailableAt": normalized[-1]["availableAt"]}}
    overlap_start, overlap_end = max(starts), min(ends)
    return {"intervals": result, "overlap": {"start": _stamp(overlap_start) if overlap_start <= overlap_end else None,
            "end": _stamp(overlap_end) if overlap_start <= overlap_end else None, "usable": overlap_start <= overlap_end}}


def frozen_hypotheses(cost_bps: float) -> list[dict[str, Any]]:
    _require(_finite(cost_bps) and cost_bps <= 1000, "hypothesis-invalid-cost")
    common = {"entry": "at next completed primary-bar open after signal availability", "exit": "conservative stop then maximum-holding exit", "stopLossPct": 0.04,
              "maxHoldingBars": 3, "positionSizing": "100-percent-cash, long-only, no-leverage", "costBps": float(cost_bps),
              "timeframeRoles": {"primary": "entry and execution", "higher": "availability-alignment gate"}}
    return [
        {"id": "trend-return-3.v1", "indicators": [{"id": "simple-return", "parameters": {"lookbackBars": 3}}],
         "rule": "close greater than close three primary bars ago", "incrementalFrom": None, **common},
        {"id": "trend-return-3-rsi-70.v1", "indicators": [{"id": "simple-return", "parameters": {"lookbackBars": 3}}, {"id": "rsi", "parameters": {"length": 14, "maximum": 70}}],
         "rule": "trend-return-3 and RSI-14 below 70", "incrementalFrom": "trend-return-3.v1", **common},
    ]


def _rsi(closes: list[float]) -> float | None:
    if len(closes) < 15:
        return None
    changes = [right - left for left, right in zip(closes[-15:], closes[-14:])]
    gains, losses = sum(max(change, 0) for change in changes) / 14, sum(max(-change, 0) for change in changes) / 14
    return 100.0 if losses == 0 else 100.0 - 100.0 / (1.0 + gains / losses)


def _signal(hypothesis: dict[str, Any], rows: list[dict[str, Any]], index: int) -> bool:
    if index < 14 or rows[index]["close"] <= rows[index - 3]["close"]:
        return False
    return hypothesis["id"] == "trend-return-3.v1" or (_rsi([row["close"] for row in rows[: index + 1]]) or 101) < 70


def _inputs_available_for_signal(rows: list[dict[str, Any]], index: int) -> bool:
    """Defence in depth for direct callers that skipped dataset validation.

    The longest current rule is RSI-14, so every candle it could read must
    have been available at the decision timestamp.  This prevents a late old
    candle from changing a signal or its subsequent fill.
    """
    decision_time = _time(rows[index]["availableAt"])
    return all(_time(row["availableAt"]) <= decision_time for row in rows[max(0, index - 14): index + 1])


def _checked(*values: float) -> None:
    _require(all(_finite(value) for value in values), "hypothesis-portfolio-arithmetic-overflow")


def backtest(hypothesis: dict[str, Any], rows: list[dict[str, Any]], *, initial_cash: float = 10000.0,
             cost_bps: float | None = None, start_index: int = 0, end_index: int | None = None,
             aligned_signals: set[int] | None = None) -> dict[str, Any]:
    """Cash/long-only deterministic simulator; signals fill no earlier than the next bar."""
    _require(_finite(initial_cash, positive=True), "hypothesis-invalid-cash")
    _require(all(_finite(row.get(field), positive=True) for row in rows for field in ("open", "high", "low", "close")), "hypothesis-invalid-ohlcv")
    stop = hypothesis["stopLossPct"]; max_hold = hypothesis["maxHoldingBars"]; cost = (hypothesis["costBps"] if cost_bps is None else cost_bps) / 10000
    end_index = len(rows) - 1 if end_index is None else end_index
    cash, quantity, entry, entry_index, trades, equity = initial_cash, 0.0, None, None, [], []
    entry_cash_spent = entry_cost = 0.0; entry_id = None
    pending = False; exposure_bars = 0; transaction_costs = 0.0; gross_turnover = 0.0
    for index in range(max(0, start_index), end_index + 1):
        row = rows[index]
        # Only a signal previously available before this bar's start may execute here.
        if pending and quantity == 0:
            gross = cash / (1 + cost); entry_cost = gross * cost; entry_cash_spent = gross + entry_cost
            quantity, cash, entry, entry_index, entry_id = gross / row["open"], 0.0, row["open"], index, "entry-%d" % index
            _checked(gross, quantity, entry_cost, entry_cash_spent)
            transaction_costs += gross * cost; gross_turnover += gross
            trades.append({"id": entry_id, "type": "entry", "signalBar": index - 1, "fillBar": index, "fill": row["open"], "notional": gross, "cost": entry_cost, "cashSpent": entry_cash_spent, "time": row["start"]})
            pending = False
        if quantity and entry is not None:
            stop_price = entry * (1 - stop)
            # Conservative policy: a gap fills at open; an intrabar stop fills at stop.
            reason = fill = None
            if row["open"] <= stop_price: reason, fill = "gap-stop", row["open"]
            elif row["low"] <= stop_price: reason, fill = "intrabar-stop-conservative", stop_price
            elif index - (entry_index or index) >= max_hold: reason, fill = "maximum-holding-close", row["close"]
            if fill is not None:
                gross = quantity * fill; proceeds = gross * (1 - cost)
                _checked(gross, proceeds)
                transaction_costs += gross * cost; gross_turnover += gross
                exit_cost = gross * cost
                trades.append({"entryId": entry_id, "type": "exit", "entryBar": entry_index, "fillBar": index, "fill": fill, "notional": gross, "cost": exit_cost, "entryCost": entry_cost, "entryCashSpent": entry_cash_spent, "reason": reason,
                               "simulatedNetPnl": proceeds - entry_cash_spent, "time": row["end"]})
                cash, quantity, entry, entry_index, entry_cash_spent, entry_cost, entry_id = proceeds, 0.0, None, None, 0.0, 0.0, None
        if quantity:
            exposure_bars += 1
        marked = cash + quantity * row["close"]
        _checked(marked, transaction_costs, gross_turnover)
        equity.append({"time": row["end"], "simulatedEquity": marked})
        if quantity == 0 and index < end_index and index >= 14:
            next_row = rows[index + 1]
            pending = ((aligned_signals is None or index in aligned_signals) and _inputs_available_for_signal(rows, index) and _signal(hypothesis, rows, index)
                       and _time(row["availableAt"]) <= _time(next_row["start"]))
    terminal_open = quantity > 0
    if quantity and equity:
        gross = quantity * rows[end_index]["close"]; proceeds = gross * (1 - cost)
        _checked(gross, proceeds)
        exit_cost = gross * cost; transaction_costs += exit_cost; gross_turnover += gross
        trades.append({"entryId": entry_id, "type": "exit", "entryBar": entry_index, "fillBar": end_index,
                       "fill": rows[end_index]["close"], "notional": gross, "cost": exit_cost, "entryCost": entry_cost,
                       "entryCashSpent": entry_cash_spent, "reason": "terminal-liquidation",
                       "simulatedNetPnl": proceeds - entry_cash_spent, "time": rows[end_index]["end"]})
        cash, quantity = proceeds, 0.0
        equity[-1]["simulatedEquity"] = cash
    final = equity[-1]["simulatedEquity"] if equity else initial_cash
    _checked(final, transaction_costs, gross_turnover, final / initial_cash, gross_turnover / initial_cash)
    exits = [item for item in trades if item["type"] == "exit"]
    peak, maximum_drawdown = initial_cash, 0.0
    for point in equity:
        peak = max(peak, point["simulatedEquity"]); maximum_drawdown = max(maximum_drawdown, (peak - point["simulatedEquity"]) / peak)
    return {"simulated": True, "initialCash": initial_cash, "finalEquity": final, "netSimulatedReturn": final / initial_cash - 1,
            "tradeLedger": trades, "equityCurve": equity, "tradeCount": len(exits), "closedSimulatedPnl": sum(item["simulatedNetPnl"] for item in exits), "expectancy": (sum(item["simulatedNetPnl"] for item in exits) / len(exits) if exits else None),
            "maxDrawdown": maximum_drawdown, "exposure": exposure_bars / len(equity) if equity else 0,
            "grossTurnover": gross_turnover / initial_cash, "transactionCosts": transaction_costs, "costBps": cost * 10000, "openPositionAtEnd": False, "terminalPositionLiquidated": terminal_open,
            "policy": "next-executable-bar; conservative intrabar stop; terminal liquidation with matching selling costs"}


def _benchmark(rows: list[dict[str, Any]], start: int, end: int, cost_bps: float) -> float:
    if end <= start: return 0.0
    entry, final = rows[start + 1]["open"], rows[end]["close"]
    ratio = (final / entry) * ((1 - cost_bps / 10000) / (1 + cost_bps / 10000))
    _checked(ratio)
    return ratio - 1


def _grade(result: dict[str, Any], benchmark: float, stability: list[float], sensitivity: float) -> tuple[str, str]:
    if result["tradeCount"] < 10 or result["expectancy"] is None:
        return "unclear", "missing-required-trade-evidence"
    if result["netSimulatedReturn"] <= 0 or result["netSimulatedReturn"] <= benchmark:
        return "unlikely", "frozen-net-and-benchmark-criterion-not-met"
    if sensitivity <= 0 or any(value <= 0 for value in stability):
        return "weak", "positive-simulated-edge-not-robust-to-frozen-stability-or-cost-check"
    return "strong", "research-monitoring-candidate-only-never-a-profit-promise"


class ArtifactStore:
    """Append-only content-addressed JSON artifacts with idempotent recovery."""
    def __init__(self, root: Path, *, create: bool = True):
        self.root = root.resolve(strict=False)
        for parent in (self.root, *self.root.parents):
            _require(not parent.is_symlink(), "hypothesis-unsafe-evidence-directory")
        if create:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        else:
            _require(self.root.exists(), "hypothesis-evidence-unavailable")
        info = self.root.stat()
        _require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700, "hypothesis-private-evidence-directory-required")

    def _read_exact(self, path: Path, limit: int) -> bytes:
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as handle:
                info = os.fstat(handle.fileno())
                _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= limit, "hypothesis-unsafe-artifact")
                return handle.read(limit + 1)
        except OSError:
            raise HypothesisError("hypothesis-unsafe-artifact") from None

    def put(self, name: str, value: dict[str, Any]) -> dict[str, str]:
        payload = _canonical(value); identity = _digest(value); path = self.root / (name + ".json")
        lock = os.open(self.root / ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(lock); _require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600, "hypothesis-unsafe-artifact-lock")
            fcntl.flock(lock, fcntl.LOCK_EX)
            fd, pending = tempfile.mkstemp(prefix=".pending-", dir=self.root)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(payload); handle.flush(); os.fsync(handle.fileno())
                try:
                    os.link(pending, path, follow_symlinks=False)
                except FileExistsError:
                    _require(self._read_exact(path, len(payload)) == payload, "hypothesis-artifact-identity-conflict")
                directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
                try: os.fsync(directory)
                finally: os.close(directory)
            finally:
                os.unlink(pending)
        finally:
            os.close(lock)
        return {"name": name, "sha256": identity}

    def get(self, name: str) -> dict[str, Any]:
        _require(type(name) is str and name and "/" not in name, "hypothesis-unsafe-artifact")
        lock = os.open(self.root / ".lock", os.O_RDONLY | os.O_NOFOLLOW)
        try:
            fcntl.flock(lock, fcntl.LOCK_SH)
            raw = self._read_exact(self.root / (name + ".json"), 8 * 1024 * 1024)
        finally:
            os.close(lock)
        try:
            value = json.loads(raw)
            _require(type(value) is dict and _canonical(value) == raw, "hypothesis-artifact-integrity-mismatch")
            return value
        except (UnicodeError, ValueError):
            raise HypothesisError("hypothesis-artifact-integrity-mismatch") from None


def _aligned_signal_indices(dataset: dict[str, Any], primary: str) -> tuple[set[int], dict[str, int]]:
    """Allow a primary signal only when every higher timeframe was available then."""
    primary_rows = dataset["intervals"][primary]["rows"]
    # Only a coarser interval is a higher-timeframe gate.  Finer bars are
    # retained for coverage only and cannot masquerade as confirmation.
    others = {name: detail["rows"] for name, detail in dataset["intervals"].items() if TIMEFRAME_RANK[name] > TIMEFRAME_RANK[primary]}
    allowed, evidence = set(), {name: 0 for name in others}
    for index, row in enumerate(primary_rows):
        usable = True
        for name, high_rows in others.items():
            matches = [high for high in high_rows if _time(high["end"]) <= _time(row["end"]) and _time(high["availableAt"]) <= _time(row["availableAt"])]
            if matches and (_time(row["availableAt"]) - max(_time(high["end"]) for high in matches)).total_seconds() <= INTERVAL_SECONDS[name] * 2: evidence[name] += 1
            else: usable = False
        if usable: allowed.add(index)
    return allowed, evidence


def _attempt_scope(config: Any, *, accepted: bool = False) -> dict[str, Any]:
    instrument = config.get("instrument") if type(config) is dict else None
    safe = None
    if type(instrument) is dict and set(instrument) == {"symbol", "product", "currency", "session", "source", "rights", "adjustmentBasis"}:
        symbol = instrument.get("symbol")
        expected = INSTRUMENTS.get(symbol) if type(symbol) is str else None
        if (expected is not None and all(instrument.get(k) == v for k,v in expected.items())
                and type(instrument.get("source")) is str and instrument["source"] in ("synthetic", "etoro", "fmp-eod")
                and type(instrument.get("adjustmentBasis")) is str and instrument["adjustmentBasis"] in ("unadjusted", "split-adjusted", "total-return-adjusted")):
            safe = {k:instrument[k] for k in ("symbol", "product", "currency", "session", "source", "adjustmentBasis")}
    # Rejected inputs never contribute dataset hashes, regardless of structure.
    return {"instrument": safe, "datasetInputSha256": _digest(config["datasets"]) if accepted else None}


def _failure_artifact(root: Path | None, config: Any, code: str) -> None:
    if root is not None:
        scope = _attempt_scope(config)
        # Invalid raw inputs may include forbidden fields. Never retain or hash
        # that payload; failure identity derives only from sanitized scope.
        identity = _digest({"scope": scope, "code": code})
        ArtifactStore(root / ("failed-" + identity)).put("failed-run", {"version": VERSION, "status": "failed", "errorCode": code, "configSha256": identity, "attemptScope": scope})


def _attempt_count(root: Path | None, config_sha256: str, scope: dict[str, Any]) -> int:
    if root is None or not root.exists():
        return 1
    identities = {config_sha256}
    for path in root.iterdir():
        if not path.is_dir():
            continue
        try:
            record = path / ("report.json" if (path / "report.json").exists() else "failed-run.json")
            payload = json.loads(record.read_bytes())
            accounting = payload.get("frozen", {}).get("selectionAccounting", {})
            identity = accounting.get("attemptConfigSha256", payload.get("configSha256"))
            retained_scope = accounting.get("attemptScope", payload.get("attemptScope"))
            blocked_match = (payload.get("status") == "failed" and type(retained_scope) is dict
                             and retained_scope.get("datasetInputSha256") is None
                             and scope.get("instrument") is not None
                             and retained_scope.get("instrument") == scope["instrument"])
            # Blocked data cannot be hashed. Include same-instrument blocked
            # identities conservatively even if their dataset might differ.
            if (retained_scope == scope or blocked_match) and type(identity) is str:
                identities.add(identity)
        except (OSError, ValueError, KeyError, TypeError):
            # A corrupt retained artifact cannot be evidence for an attempt.
            continue
    return len(identities)


def run(config: dict[str, Any], *, evidence_root: Path | None = None, allow_synthetic_smoke: bool = False,
        frozen_rules: list[dict[str, Any]] | None = None, retained_attempt_count: int | None = None,
        report_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        required = {"version", "classification", "primaryInterval", "instrument", "datasets"}
        optional = {"costBps", "retention", "interpretation", "refinementRequested", "retune"}
        _require(type(config) is dict and required <= set(config) and not set(config) - required - optional,
                 "hypothesis-invalid-config-fields")
        _require(config.get("classification") in ("synthetic", "observed-attested"), "hypothesis-invalid-classification")
        _require(config.get("classification") != "synthetic" or allow_synthetic_smoke, "hypothesis-synthetic-smoke-opt-in-required")
        _require(all(type(config[key]) is bool for key in ("refinementRequested", "retune") if key in config),
                 "hypothesis-invalid-control-flag")
        _require(config["classification"] != "synthetic" or not {"retention", "interpretation"}.intersection(config),
                 "hypothesis-synthetic-extra-metadata-rejected")
        if config.get("classification") == "observed-attested":
            _require(config.get("instrument", {}).get("source") in ("etoro", "fmp-eod"), "hypothesis-observed-source-unapproved")
            R.retention_check(config["instrument"]["source"], config.get("retention"))
            R.validate_interpretation(config.get("interpretation"))
            _require(config["interpretation"]["instrumentVerified"] and config["interpretation"]["trainingPermitted"]
                     and config["interpretation"]["currency"] == config["instrument"]["currency"]
                     and config["interpretation"]["session"] == config["instrument"]["session"]
                     and config["interpretation"]["adjustments"] == config["instrument"]["adjustmentBasis"], "hypothesis-observed-instrument-or-rights-unapproved")
        _require(config.get("refinementRequested") is not True, "hypothesis-holdout-refinement-requires-fresh-evidence")
        dataset = validate_dataset(config); primary = config.get("primaryInterval")
    except (HypothesisError, L.LearningError) as exc:
        code = str(exc) if isinstance(exc, HypothesisError) else "hypothesis-observed-gate-rejected"
        _failure_artifact(evidence_root, config, code)
        if isinstance(exc, HypothesisError):
            raise
        raise HypothesisError(code) from None
    except ValueError as exc:
        # The eToro retention gate keeps its controlled collector exception at
        # the collection boundary. Normalize only that value-blind type here.
        if type(exc).__name__ != "CollectionError" or type(exc).__module__ != "money_maker_3000.feed_collection":
            raise
        _failure_artifact(evidence_root, config, "hypothesis-observed-gate-rejected")
        raise HypothesisError("hypothesis-observed-gate-rejected") from None
    if primary not in dataset["intervals"]:
        _failure_artifact(evidence_root, config, "hypothesis-primary-interval-missing"); raise HypothesisError("hypothesis-primary-interval-missing")
    if not dataset["overlap"]["usable"]:
        _failure_artifact(evidence_root, config, "hypothesis-no-overlapping-usable-history"); raise HypothesisError("hypothesis-no-overlapping-usable-history")
    rows = dataset["intervals"][primary]["rows"]
    if len(rows) < 25:
        _failure_artifact(evidence_root, config, "hypothesis-insufficient-evidence"); raise HypothesisError("hypothesis-insufficient-evidence")
    cost = config.get("costBps", 10.0)
    try:
        candidates = frozen_hypotheses(cost) if frozen_rules is None else frozen_rules
    except HypothesisError as exc:
        _failure_artifact(evidence_root, config, str(exc)); raise
    if not (type(candidates) is list and 1 <= len(candidates) <= 2 and all(candidate in frozen_hypotheses(cost) for candidate in candidates)):
        _failure_artifact(evidence_root, config, "hypothesis-frozen-rule-drift"); raise HypothesisError("hypothesis-frozen-rule-drift")
    aligned_signals, alignment_evidence = _aligned_signal_indices(dataset, primary)
    split = max(15, int(len(rows) * .6)); evaluation_start = split + 1
    trials = []
    for candidate in candidates:
        dev = backtest(candidate, rows, cost_bps=cost, end_index=split, aligned_signals=aligned_signals)
        evaluation = backtest(candidate, rows, cost_bps=cost, start_index=evaluation_start, aligned_signals=aligned_signals)
        sensitivity = backtest(candidate, rows, cost_bps=cost * 2, start_index=evaluation_start, aligned_signals=aligned_signals)["netSimulatedReturn"]
        thirds = [backtest(candidate, rows, cost_bps=cost, start_index=begin, end_index=end, aligned_signals=aligned_signals)["netSimulatedReturn"] for begin, end in ((evaluation_start, (evaluation_start + len(rows)) // 2), ((evaluation_start + len(rows)) // 2 + 1, len(rows) - 1))]
        benchmark = _benchmark(rows, evaluation_start, len(rows) - 1, cost)
        grade, reason = _grade(evaluation, benchmark, thirds, sensitivity)
        trials.append({"hypothesis": candidate, "status": "evaluated", "development": dev, "evaluation": evaluation,
                       "benchmarkReturn": benchmark, "costSensitivityAtDoubleCost": sensitivity, "chronologicalStability": thirds,
                       "incrementalNetReturn": evaluation["netSimulatedReturn"] - (trials[0]["evaluation"]["netSimulatedReturn"] if trials else 0),
                       "uncertainty": {"tradeCount": evaluation["tradeCount"], "assessment": "insufficient-independent-trades" if evaluation["tradeCount"] < 10 else "dependent-chronological-sample"}, "grade": grade, "gradeReason": reason})
    config_sha256, scope = _digest(config), _attempt_scope(config, accepted=True)
    frozen = {"version": VERSION, "instrument": config["instrument"], "primaryInterval": primary, "dataset": {key: {field: value[field] for field in ("sha256", "count", "coverage")} for key, value in dataset["intervals"].items()},
              "overlap": dataset["overlap"], "timeframeAlignment": {"primary": primary, "roles": {name: ("primary" if name == primary else "higher-availability-freshness-gate" if TIMEFRAME_RANK[name] > TIMEFRAME_RANK[primary] else "lower-coverage-only") for name in dataset["intervals"]}, "eligiblePrimarySignals": len(aligned_signals), "higherTimeframeAvailableEndpoints": alignment_evidence}, "hypotheses": candidates, "gradeRubric": GRADE_RUBRIC, "selectionAccounting": {"attemptConfigSha256": config_sha256, "attemptScope": scope, "attemptedTrials": len(trials), "retainedAttemptCount": retained_attempt_count if retained_attempt_count is not None else _attempt_count(evidence_root, config_sha256, scope), "maxTrials": len(candidates), "multipleComparisonCount": len(candidates), "holdoutRefinement": "rejected-without-fresh-dataset"}}
    _require(report_metadata is None or type(report_metadata) is dict and set(report_metadata) == {"frozenRuleRetest", "originalReportSha256"}
             and report_metadata["frozenRuleRetest"] is True and type(report_metadata["originalReportSha256"]) is str
             and len(report_metadata["originalReportSha256"]) == 64 and all(char in "0123456789abcdef" for char in report_metadata["originalReportSha256"]),
             "hypothesis-invalid-report-metadata")
    artifacts = [{"name": "frozen-hypothesis", "sha256": _digest(frozen)}]
    for index, trial in enumerate(trials):
        artifacts.extend([{"name": "trial-%d-ledger" % index, "sha256": _digest({"simulated": True, "trades": trial["evaluation"]["tradeLedger"]})}, {"name": "trial-%d-equity" % index, "sha256": _digest({"simulated": True, "equity": trial["evaluation"]["equityCurve"]})}])
    artifacts.append({"name": "report", "sha256": "self"})
    observed = config["classification"] == "observed-attested"
    report = {"version": VERSION, "simulated": True, "operationalStatus": "offline-research-session-calendar-unverified" if observed else "offline-research-complete", "validationStage": "untouched-chronological-evaluation-session-calendar-unverified" if observed else "untouched-chronological-evaluation", "frozen": frozen, "artifactManifest": artifacts,
              "trials": trials, "limitation": "Synthetic fixtures prove mechanics only; no empirical profitability has been demonstrated; synthetic session dates are exempt from exchange-calendar validation." if config["classification"] == "synthetic" else "Observed-attested local data still does not establish future profitability; weekday compatibility is checked but exchange holidays require source calendar evidence.",
              "boundary": {"providerCalls": "blocked", "executionRoutes": "absent", "longOnly": True, "leverage": 1, "profitPromise": "absent"}}
    if report_metadata is not None:
        report.update(report_metadata)
    report["sha256"] = _digest(report)
    if evidence_root is not None:
        store = ArtifactStore(evidence_root / report["sha256"])
        _require(store.put("frozen-hypothesis", frozen) == artifacts[0], "hypothesis-artifact-manifest-mismatch")
        for index, trial in enumerate(trials):
            offset = 1 + index * 2
            _require(store.put("trial-%d-ledger" % index, {"simulated": True, "trades": trial["evaluation"]["tradeLedger"]}) == artifacts[offset] and store.put("trial-%d-equity" % index, {"simulated": True, "equity": trial["evaluation"]["equityCurve"]}) == artifacts[offset + 1], "hypothesis-artifact-manifest-mismatch")
        # The stored report is self-hashed.  Artifact locations are deliberately
        # not injected into it, otherwise a retry would change its identity.
        _require(store.put("report", report)["sha256"] == _digest(report), "hypothesis-artifact-manifest-mismatch")
    return report


def frozen_retest(report: dict[str, Any], config: dict[str, Any], *, evidence_root: Path | None = None, allow_synthetic_smoke: bool = False) -> dict[str, Any]:
    _require(type(report) is dict and report.get("version") == VERSION, "hypothesis-invalid-report")
    _require(report.get("sha256") == _digest({key: value for key, value in report.items() if key != "sha256"}), "hypothesis-report-identity-mismatch")
    _require(config.get("retune") is not True, "hypothesis-retuning-requires-new-hypothesis")
    _require(type(report.get("frozen")) is dict and config.get("instrument") != report["frozen"].get("instrument"), "hypothesis-retest-requires-different-instrument")
    permitted = [trial["hypothesis"] for trial in report.get("trials", []) if trial.get("grade") in ("weak", "strong")]
    _require(permitted, "hypothesis-no-weak-or-strong-candidate")
    copied = dict(config); copied["costBps"] = permitted[0]["costBps"]
    return run(copied, evidence_root=evidence_root, allow_synthetic_smoke=allow_synthetic_smoke, frozen_rules=permitted,
               report_metadata={"frozenRuleRetest": True, "originalReportSha256": report["sha256"]})


def replay(report: dict[str, Any], config: dict[str, Any], *, evidence_root: Path | None = None, allow_synthetic_smoke: bool = False) -> dict[str, Any]:
    """Read-only deterministic replay of frozen rules, dataset hashes, trades and equity."""
    _require(type(report) is dict and report.get("sha256") == _digest({key: value for key, value in report.items() if key != "sha256"}), "hypothesis-report-identity-mismatch")
    frozen = report.get("frozen")
    _require(type(frozen) is dict and type(frozen.get("hypotheses")) is list, "hypothesis-invalid-frozen-report")
    _require(evidence_root is not None, "hypothesis-replay-evidence-root-required")
    store = ArtifactStore(evidence_root / report["sha256"], create=False)
    _require(store.get("report") == report, "hypothesis-replay-stored-report-mismatch")
    manifest = report.get("artifactManifest")
    _require(type(manifest) is list and manifest and manifest[-1] == {"name": "report", "sha256": "self"}, "hypothesis-artifact-manifest-mismatch")
    for item in manifest[:-1]:
        _require(type(item) is dict and set(item) == {"name", "sha256"} and _digest(store.get(item["name"])) == item["sha256"], "hypothesis-replay-artifact-mismatch")
    replayed = run(config, allow_synthetic_smoke=allow_synthetic_smoke, frozen_rules=frozen["hypotheses"], retained_attempt_count=frozen["selectionAccounting"]["retainedAttemptCount"])
    _require(replayed["frozen"] == frozen and replayed["trials"] == report.get("trials"), "hypothesis-replay-mismatch")
    return {"version": VERSION, "replay": "verified", "reportSha256": report["sha256"], "dataset": frozen["dataset"], "trialCount": len(replayed["trials"]), "simulated": True}
