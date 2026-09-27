"""Frozen, diagnostic-only feature registry used by offline research.

The toolkit deliberately receives both completion and availability timestamps.
They are different facts: a completed bar is not usable until its source says
it became available.  No feature creates an order, forecast, or recommendation.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any

VERSION = "signal-toolkit.v1"


class SignalError(ValueError):
    pass


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise SignalError(code)


def utc_timestamp(value: Any) -> datetime:
    _require(type(value) is str, "signal-invalid-timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        _require(parsed.tzinfo is not None, "signal-invalid-timestamp")
        return parsed.astimezone(timezone.utc)
    except ValueError:
        raise SignalError("signal-invalid-timestamp") from None


def timestamp(value: Any) -> str:
    return utc_timestamp(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


REGISTRY = {
    "simple-return": {"version": "simple-return.v1", "fields": ["close"], "lookback": 2},
    "rsi": {"version": "rsi-wilder-14.v1", "fields": ["close"], "lookback": 15},
    "normalized-atr": {"version": "normalized-atr-wilder-14.v1", "fields": ["high", "low", "close"], "lookback": 15},
}


def feature_registry() -> list[dict[str, Any]]:
    return [{"id": name, **REGISTRY[name]} for name in sorted(REGISTRY)]


def _row(row: Any, field: str) -> Any:
    return row.get(field) if type(row) is dict else getattr(row, field, None)


def _number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _validate_rows(rows: list[Any], *, completed_at: Any, available_at: Any) -> tuple[datetime, datetime]:
    completed, available = utc_timestamp(completed_at), utc_timestamp(available_at)
    _require(completed <= available, "signal-completion-after-availability")
    _require(type(rows) is list and rows, "signal-empty-history")
    prior = None
    for row in rows:
        end = _row(row, "end") or _row(row, "timestamp") or _row(row, "date")
        current = utc_timestamp(end if "T" in str(end) else str(end) + "T00:00:00Z")
        _require(prior is None or current > prior, "signal-unordered-history")
        _require(current <= completed, "signal-incomplete-or-future-bar")
        _require(_number(_row(row, "close")), "signal-invalid-close")
        prior = current
    return completed, available


def evaluate_feature(name: str, rows: list[Any], *, completed_at: Any, available_at: Any,
                     ohlc_attested: bool = False, ohlc_basis: str | None = None) -> dict[str, Any]:
    """Evaluate one bounded indicator only from bars completed before availability."""
    _require(name in REGISTRY, "signal-unknown-feature")
    completed, available = _validate_rows(rows, completed_at=completed_at, available_at=available_at)
    need = REGISTRY[name]["lookback"]
    endpoint = _row(rows[-1], "end") or _row(rows[-1], "timestamp") or _row(rows[-1], "date")
    result = {"feature": {"id": name, "version": REGISTRY[name]["version"]},
              "completedAt": timestamp(completed_at), "availableAt": timestamp(available_at),
              "endpoint": endpoint, "inputRowsSha256": digest([dict(row) if type(row) is dict else row.__dict__ for row in rows])}
    if len(rows) < need:
        return {**result, "status": "unavailable", "reason": "insufficient-history", "value": None}
    closes = [float(_row(row, "close")) for row in rows]
    if name == "simple-return":
        value = closes[-1] / closes[-2] - 1.0
    elif name == "rsi":
        changes = [right - left for left, right in zip(closes[-15:], closes[-14:])]
        gains = sum(max(change, 0.0) for change in changes) / 14
        losses = sum(max(-change, 0.0) for change in changes) / 14
        if gains == losses == 0:
            return {**result, "status": "unavailable", "reason": "flat-history-rsi-undefined", "value": None}
        value = 100.0 if losses == 0 else 100.0 - 100.0 / (1.0 + gains / losses)
    else:
        if not ohlc_attested or not isinstance(ohlc_basis, str) or not ohlc_basis:
            return {**result, "status": "unavailable", "reason": "ohlc-unavailable-not-attested", "value": None}
        window = rows[-15:]
        if any(not _number(_row(row, field)) for row in window for field in ("high", "low", "close")):
            return {**result, "status": "unavailable", "reason": "ohlc-unavailable-not-derived", "value": None}
        ranges = [max(float(_row(row, "high")) - float(_row(row, "low")),
                      abs(float(_row(row, "high")) - float(_row(prev, "close"))),
                      abs(float(_row(row, "low")) - float(_row(prev, "close"))))
                  for prev, row in zip(window, window[1:])]
        value = sum(ranges) / len(ranges) / closes[-1]
    _require(math.isfinite(value), "signal-nonfinite-result")
    return {**result, "status": "available", "reason": None, "value": value}


def whole_cohort_coverage(rows: list[Any], *, completed_at: Any, available_at: Any,
                          ohlc_attested: bool = False, ohlc_basis: str | None = None) -> dict[str, Any]:
    """Coverage works for dataclass and dictionary rows and preserves availability evidence."""
    _, available = _validate_rows(rows, completed_at=completed_at, available_at=available_at)
    coverage = []
    for name in sorted(REGISTRY):
        missing: list[str] = []
        for index in range(len(rows)):
            # Earlier endpoints use the next row's declared availability when supplied;
            # never substitute completion time as availability time.
            row = rows[min(index + 1, len(rows) - 1)]
            endpoint_available = _row(row, "available_at") or _row(row, "availableAt") or timestamp(available)
            endpoint_completed = _row(rows[index], "end") or _row(rows[index], "timestamp") or _row(rows[index], "date")
            if "T" not in str(endpoint_completed):
                endpoint_completed = str(endpoint_completed) + "T00:00:00Z"
            outcome = evaluate_feature(name, rows[: index + 1], completed_at=endpoint_completed,
                                       available_at=endpoint_available, ohlc_attested=ohlc_attested, ohlc_basis=ohlc_basis)
            if outcome["status"] != "available":
                missing.append(str(outcome["endpoint"]))
        coverage.append({"feature": name, "endpoints": len(rows), "availableEndpoints": len(rows) - len(missing),
                         "unavailableEndpoints": len(missing), "unavailableEndpointsAt": missing})
    return {"version": VERSION, "availableAt": timestamp(available_at), "coverage": coverage}


def freeze_feature_bundle(rows: list[Any], *, completed_at: Any, available_at: Any,
                          ohlc_attested: bool = False, ohlc_basis: str | None = None) -> dict[str, Any]:
    _validate_rows(rows, completed_at=completed_at, available_at=available_at)
    payload = {"version": VERSION, "registry": feature_registry(), "completedAt": timestamp(completed_at),
               "availableAt": timestamp(available_at), "ohlcPolicy": {"attested": ohlc_attested, "basis": ohlc_basis},
               "rowsSha256": digest([dict(row) if type(row) is dict else row.__dict__ for row in rows])}
    return {**payload, "sha256": digest(payload)}


def validate_feature_bundle(value: Any) -> None:
    _require(type(value) is dict and set(value) == {"version", "registry", "completedAt", "availableAt", "ohlcPolicy", "rowsSha256", "sha256"}, "signal-invalid-feature-bundle")
    _require(value["version"] == VERSION and value["registry"] == feature_registry(), "signal-feature-definition-drift")
    _require(utc_timestamp(value["completedAt"]) <= utc_timestamp(value["availableAt"]), "signal-completion-after-availability")
    _require(value["sha256"] == digest({key: item for key, item in value.items() if key != "sha256"}), "signal-feature-bundle-digest-mismatch")
