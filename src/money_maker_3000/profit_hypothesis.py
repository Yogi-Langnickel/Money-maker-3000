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

VERSION = "instrument-profit-hypothesis.v1"
ALLOWED_INTERVALS = ("1h", "4h", "1d", "1w")
INSTRUMENTS = {
    "SPY": {"product": "ETF", "currency": "USD", "session": "US-equities-regular"},
    "QQQ": {"product": "ETF", "currency": "USD", "session": "US-equities-regular"},
    "VAS": {"product": "ETF", "currency": "AUD", "session": "AU-equities-regular"},
}
MAX_CANDLES = 1000
GRADE_RUBRIC = {"version": "profit-hypothesis-grade.v1", "missingEvidence": "unclear", "unlikely": "nonpositive net simulated return or no benchmark improvement", "weak": "positive simulated edge but cost or chronological stability fails", "strong": "positive against benchmark, positive double-cost sensitivity and every frozen stability slice; monitoring candidate only"}


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
    return type(value) in (int, float) and math.isfinite(value) and (value > 0 if positive else value >= 0)


def _bar(value: Any, interval: str, index: int) -> dict[str, Any]:
    _require(type(value) is dict and set(value) == {"start", "end", "availableAt", "open", "high", "low", "close", "volume", "provenance"}, "hypothesis-invalid-candle")
    start, end, available = _time(value["start"]), _time(value["end"]), _time(value["availableAt"])
    _require(start < end <= available, "hypothesis-candle-time-order")
    _require(all(_finite(value[name], positive=True) for name in ("open", "high", "low", "close")) and _finite(value["volume"]), "hypothesis-invalid-ohlcv")
    _require(value["high"] >= max(value["open"], value["close"]) and value["low"] <= min(value["open"], value["close"]), "hypothesis-inconsistent-ohlc")
    _require(type(value["provenance"]) is dict and set(value["provenance"]) == {"source", "adjustmentBasis", "retrievedAt", "rights"}, "hypothesis-invalid-provenance")
    _require(type(value["provenance"]["source"]) is str and type(value["provenance"]["adjustmentBasis"]) is str and type(value["provenance"]["rights"]) is str, "hypothesis-invalid-provenance")
    _time(value["provenance"]["retrievedAt"])
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
        previous = None
        for row in normalized:
            current = _time(row["start"])
            _require(previous is None or current > previous, "hypothesis-unordered-candles")
            _require(row["provenance"]["source"] == instrument["source"] and row["provenance"]["rights"] == instrument["rights"] and row["provenance"]["adjustmentBasis"] == instrument["adjustmentBasis"], "hypothesis-provenance-mismatch")
            previous = current
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


def backtest(hypothesis: dict[str, Any], rows: list[dict[str, Any]], *, initial_cash: float = 10000.0,
             cost_bps: float | None = None, start_index: int = 0, end_index: int | None = None,
             aligned_signals: set[int] | None = None) -> dict[str, Any]:
    """Cash/long-only deterministic simulator; signals fill no earlier than the next bar."""
    _require(initial_cash > 0 and math.isfinite(initial_cash), "hypothesis-invalid-cash")
    stop = hypothesis["stopLossPct"]; max_hold = hypothesis["maxHoldingBars"]; cost = (hypothesis["costBps"] if cost_bps is None else cost_bps) / 10000
    end_index = len(rows) - 1 if end_index is None else end_index
    cash, quantity, entry, entry_index, trades, equity = initial_cash, 0.0, None, None, [], []
    pending = False; exposure_bars = 0; transaction_costs = 0.0; gross_turnover = 0.0
    for index in range(max(0, start_index), end_index + 1):
        row = rows[index]
        # Only a signal previously available before this bar's start may execute here.
        if pending and quantity == 0:
            gross = cash / (1 + cost); quantity, cash, entry, entry_index = gross / row["open"], 0.0, row["open"], index
            transaction_costs += gross * cost; gross_turnover += gross
            trades.append({"type": "entry", "signalBar": index - 1, "fillBar": index, "fill": row["open"], "cost": gross * cost, "time": row["start"]})
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
                transaction_costs += gross * cost; gross_turnover += gross
                trades.append({"type": "exit", "entryBar": entry_index, "fillBar": index, "fill": fill, "cost": gross * cost, "reason": reason,
                               "simulatedNetPnl": proceeds - quantity * entry, "time": row["end"]})
                cash, quantity, entry, entry_index = proceeds, 0.0, None, None
        if quantity:
            exposure_bars += 1
        equity.append({"time": row["end"], "simulatedEquity": cash + quantity * row["close"]})
        if quantity == 0 and index < end_index and index >= 14:
            next_row = rows[index + 1]
            pending = ((aligned_signals is None or index in aligned_signals) and _signal(hypothesis, rows, index)
                       and _time(row["availableAt"]) <= _time(next_row["start"]))
    final = equity[-1]["simulatedEquity"] if equity else initial_cash
    exits = [item for item in trades if item["type"] == "exit"]
    peak, maximum_drawdown = initial_cash, 0.0
    for point in equity:
        peak = max(peak, point["simulatedEquity"]); maximum_drawdown = max(maximum_drawdown, (peak - point["simulatedEquity"]) / peak)
    return {"simulated": True, "initialCash": initial_cash, "finalEquity": final, "netSimulatedReturn": final / initial_cash - 1,
            "tradeLedger": trades, "equityCurve": equity, "tradeCount": len(exits), "expectancy": (sum(item["simulatedNetPnl"] for item in exits) / len(exits) if exits else None),
            "maxDrawdown": maximum_drawdown, "exposure": exposure_bars / len(equity) if equity else 0,
            "grossTurnover": gross_turnover / initial_cash, "transactionCosts": transaction_costs, "costBps": cost * 10000, "openPositionAtEnd": quantity > 0,
            "policy": "next-executable-bar; conservative intrabar stop; final open position marked-to-market"}


def _benchmark(rows: list[dict[str, Any]], start: int, end: int, cost_bps: float) -> float:
    if end <= start: return 0.0
    entry, final = rows[start + 1]["open"], rows[end]["close"]
    return (final / entry) * ((1 - cost_bps / 10000) / (1 + cost_bps / 10000)) - 1


def _grade(result: dict[str, Any], benchmark: float, stability: list[float], sensitivity: float) -> tuple[str, str]:
    if result["tradeCount"] < 3 or result["expectancy"] is None:
        return "unclear", "missing-required-trade-evidence"
    if result["netSimulatedReturn"] <= 0 or result["netSimulatedReturn"] <= benchmark:
        return "unlikely", "frozen-net-and-benchmark-criterion-not-met"
    if sensitivity <= 0 or any(value <= 0 for value in stability):
        return "weak", "positive-simulated-edge-not-robust-to-frozen-stability-or-cost-check"
    return "strong", "research-monitoring-candidate-only-never-a-profit-promise"


class ArtifactStore:
    """Append-only content-addressed JSON artifacts with idempotent recovery."""
    def __init__(self, root: Path):
        self.root = root.resolve(strict=False)
        for parent in (self.root, *self.root.parents):
            _require(not parent.is_symlink(), "hypothesis-unsafe-evidence-directory")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
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


def _aligned_signal_indices(dataset: dict[str, Any], primary: str) -> tuple[set[int], dict[str, int]]:
    """Allow a primary signal only when every higher timeframe was available then."""
    primary_rows = dataset["intervals"][primary]["rows"]
    others = {name: detail["rows"] for name, detail in dataset["intervals"].items() if name != primary}
    allowed, evidence = set(), {name: 0 for name in others}
    for index, row in enumerate(primary_rows):
        usable = True
        for name, high_rows in others.items():
            matches = [high for high in high_rows if _time(high["end"]) <= _time(row["end"]) and _time(high["availableAt"]) <= _time(row["availableAt"])]
            if matches: evidence[name] += 1
            else: usable = False
        if usable: allowed.add(index)
    return allowed, evidence


def _failure_artifact(root: Path | None, config: Any, code: str) -> None:
    if root is not None:
        ArtifactStore(root / ("failed-" + _digest({"config": _digest(config), "code": code}))).put("failed-run", {"version": VERSION, "status": "failed", "errorCode": code, "configSha256": _digest(config)})


def run(config: dict[str, Any], *, evidence_root: Path | None = None, allow_synthetic_smoke: bool = False,
        frozen_rules: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    try:
        _require(config.get("classification") in ("synthetic", "observed-attested"), "hypothesis-invalid-classification")
        _require(config.get("classification") != "synthetic" or allow_synthetic_smoke, "hypothesis-synthetic-smoke-opt-in-required")
        _require(config.get("refinementRequested") is not True, "hypothesis-holdout-refinement-requires-fresh-evidence")
        dataset = validate_dataset(config); primary = config.get("primaryInterval")
    except HypothesisError as exc:
        _failure_artifact(evidence_root, config, str(exc)); raise
    _require(primary in dataset["intervals"], "hypothesis-primary-interval-missing")
    _require(dataset["overlap"]["usable"], "hypothesis-no-overlapping-usable-history")
    rows = dataset["intervals"][primary]["rows"]
    _require(len(rows) >= 25, "hypothesis-insufficient-evidence")
    cost = config.get("costBps", 10.0); candidates = frozen_hypotheses(cost) if frozen_rules is None else frozen_rules
    _require(type(candidates) is list and 1 <= len(candidates) <= 2 and all(candidate in frozen_hypotheses(cost) for candidate in candidates), "hypothesis-frozen-rule-drift")
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
    frozen = {"version": VERSION, "instrument": config["instrument"], "primaryInterval": primary, "dataset": {key: {field: value[field] for field in ("sha256", "count", "coverage")} for key, value in dataset["intervals"].items()},
              "overlap": dataset["overlap"], "timeframeAlignment": {"eligiblePrimarySignals": len(aligned_signals), "higherTimeframeAvailableEndpoints": alignment_evidence}, "hypotheses": candidates, "gradeRubric": GRADE_RUBRIC, "selectionAccounting": {"attemptedTrials": len(trials), "maxTrials": len(candidates), "multipleComparisonCount": len(candidates), "holdoutRefinement": "rejected-without-fresh-dataset"}}
    report = {"version": VERSION, "simulated": True, "operationalStatus": "offline-research-complete", "validationStage": "untouched-chronological-evaluation", "frozen": frozen,
              "trials": trials, "limitation": "Synthetic fixtures prove mechanics only; no empirical profitability has been demonstrated." if config["classification"] == "synthetic" else "Observed-attested local data still does not establish future profitability.",
              "boundary": {"providerCalls": "blocked", "executionRoutes": "absent", "longOnly": True, "leverage": 1, "profitPromise": "absent"}}
    report["sha256"] = _digest(report)
    if evidence_root is not None:
        store = ArtifactStore(evidence_root / report["sha256"])
        artifacts = [store.put("frozen-hypothesis", frozen)]
        for index, trial in enumerate(trials):
            artifacts.extend([store.put("trial-%d-ledger" % index, {"simulated": True, "trades": trial["evaluation"]["tradeLedger"]}), store.put("trial-%d-equity" % index, {"simulated": True, "equity": trial["evaluation"]["equityCurve"]})])
        # The stored report is self-hashed.  Artifact locations are deliberately
        # not injected into it, otherwise a retry would change its identity.
        artifacts.append(store.put("report", report))
    return report


def frozen_retest(report: dict[str, Any], config: dict[str, Any], *, allow_synthetic_smoke: bool = False) -> dict[str, Any]:
    _require(type(report) is dict and report.get("version") == VERSION, "hypothesis-invalid-report")
    _require(report.get("sha256") == _digest({key: value for key, value in report.items() if key != "sha256"}), "hypothesis-report-identity-mismatch")
    _require(config.get("retune") is not True, "hypothesis-retuning-requires-new-hypothesis")
    permitted = [trial["hypothesis"] for trial in report.get("trials", []) if trial.get("grade") in ("weak", "strong")]
    _require(permitted, "hypothesis-no-weak-or-strong-candidate")
    copied = dict(config); copied["costBps"] = permitted[0]["costBps"]
    outcome = run(copied, allow_synthetic_smoke=allow_synthetic_smoke, frozen_rules=permitted)
    return {"version": VERSION, "frozenRuleRetest": True, "originalReportSha256": report["sha256"], "instrument": copied["instrument"], "results": outcome["trials"], "simulated": True}
