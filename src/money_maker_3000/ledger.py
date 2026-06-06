from __future__ import annotations

from contextlib import contextmanager
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised only on non-POSIX hosts.
    fcntl = None

from money_maker_3000.contracts import utc_iso

REDACTED = "redacted"
ABSENT = "absent"
SENSITIVE_KEY_PATTERN = re.compile(
    r"(account|api[-_]?key|auth|balance|credential|email|holding|jwt|name|oauth|order|portfolio|position|secret|statement|token|transaction|user[-_]?key)",
    re.I,
)
SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    re.compile(
        r"\b(?:acct|account|ord|order|pos|position|txn|transaction)[_-](?=[A-Za-z0-9_-]*\d)[A-Za-z0-9][A-Za-z0-9_-]{3,}\b",
        re.I,
    ),
    re.compile(r"\b(?:api|oauth|jwt|token|secret)[_-][A-Za-z0-9][A-Za-z0-9_-]{7,}\b", re.I),
)
SAFE_SENSITIVE_VALUES = {REDACTED, ABSENT, "not-attempted", "ignored-for-budget", None}
SAFE_SENSITIVE_KEYS = {"maxOrderUsd"}
SIMULATION_AUDIT_RECORD_KEYS = frozenset(
    {
        "ledgerVersion",
        "dtoVersion",
        "recordedAt",
        "correlationId",
        "runId",
        "mode",
        "environment",
        "strategyId",
        "strategyVersion",
        "configVersion",
        "configHash",
        "allocationId",
        "strategyAllocationId",
        "allocation",
        "decision",
        "riskResult",
        "riskDecision",
        "vetoes",
        "dataFreshness",
        "providerCallStatus",
        "executionRoute",
        "tradeLogEntry",
    }
)


def _redact_value(value: Any) -> Any:
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, dict):
        return redact_mapping(value)
    if _looks_sensitive_scalar(value):
        return REDACTED
    return value


def redact_mapping(entry: dict[str, Any] | None) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in (entry or {}).items():
        if SENSITIVE_KEY_PATTERN.search(key):
            redacted[key] = REDACTED
        else:
            redacted[key] = _redact_value(value)
    return redacted


def redact_trade_log_entry(entry: dict[str, Any] | None) -> dict[str, Any]:
    redacted = redact_mapping(entry)
    redacted.update(
        {
            "accountIdentifiers": REDACTED,
            "rawProviderPayloads": ABSENT,
            "providerCall": "not-attempted",
            "executionRoute": ABSENT,
        }
    )
    return redacted


def build_ledger_record(
    *,
    run: dict[str, Any],
    entry: dict[str, Any] | None = None,
    recorded_at: datetime | None = None,
) -> dict[str, Any]:
    if not run:
        raise TypeError("simulation run is required")
    trade_log_entry = redact_trade_log_entry(entry or run.get("tradeLogEntry"))
    raw_risk_decision = run.get("riskDecision", {})
    risk_decision = raw_risk_decision if isinstance(raw_risk_decision, dict) else {"decision": raw_risk_decision}
    return {
        "ledgerVersion": 2,
        "dtoVersion": "simulation-audit-record.v2",
        "recordedAt": utc_iso(recorded_at) if recorded_at else run.get("evaluatedAt", utc_iso()),
        "correlationId": run.get("correlationId"),
        "runId": run.get("runId"),
        "mode": "simulation",
        "environment": "synthetic",
        "strategyId": run.get("strategyId"),
        "strategyVersion": run.get("strategyVersion"),
        "configVersion": run.get("configVersion"),
        "configHash": run.get("configHash"),
        "allocationId": run.get("allocation", {}).get("allocationId"),
        "strategyAllocationId": run.get("allocation", {}).get("strategyAllocationId"),
        "allocation": {
            "botAllocationUsd": run.get("allocation", {}).get("botAllocationUsd"),
            "reservedUsd": run.get("allocation", {}).get("reservedUsd"),
            "availableUsd": run.get("allocation", {}).get("availableUsd"),
            "maxOrderUsd": run.get("allocation", {}).get("maxOrderUsd"),
            "providerDemoBalance": REDACTED,
            "providerBalanceUse": "ignored-for-budget",
        },
        "decision": run.get("decision"),
        "riskResult": run.get("riskResult"),
        "riskDecision": risk_decision.get("decision", "blocked"),
        "vetoes": list(run.get("vetoes", [])),
        "dataFreshness": run.get("tradeLogEntry", {}).get("dataFreshness", "unknown"),
        "providerCallStatus": "not-attempted",
        "executionRoute": ABSENT,
        "tradeLogEntry": trade_log_entry,
    }


def append_ledger_record(ledger_path: str | Path, record: dict[str, Any]) -> dict[str, Any]:
    if not ledger_path:
        raise TypeError("ledger path is required")
    validated_record = _validate_simulation_audit_record(record)
    path = Path(ledger_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _exclusive_ledger_writer(path):
        if path.exists():
            _reject_duplicate_ledger_identity(path, validated_record)
        with path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(validated_record, sort_keys=True, separators=(",", ":")) + "\n")
            output.flush()
            os.fsync(output.fileno())
    return validated_record


def read_ledger_records(ledger_path: str | Path) -> list[dict[str, Any]]:
    path = Path(ledger_path)
    with path.open("r", encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]


def _validate_simulation_audit_record(record: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise TypeError("ledger record must be a JSON object")
    unknown_keys = sorted(set(record) - SIMULATION_AUDIT_RECORD_KEYS)
    if unknown_keys:
        raise ValueError(f"ledger record has unsupported fields: {', '.join(unknown_keys)}")
    if record.get("dtoVersion") != "simulation-audit-record.v2":
        raise ValueError("ledger record must use simulation-audit-record.v2")
    if record.get("mode") != "simulation" or record.get("environment") != "synthetic":
        raise ValueError("ledger record must be simulation/synthetic only")
    if _has_unredacted_sensitive_value(record):
        raise ValueError("ledger record contains unredacted sensitive data")
    return dict(record)


def _has_unredacted_sensitive_value(value: Any) -> bool:
    if isinstance(value, list):
        return any(_has_unredacted_sensitive_value(item) for item in value)
    if not isinstance(value, dict):
        return _looks_sensitive_scalar(value)
    for key, item in value.items():
        if SENSITIVE_KEY_PATTERN.search(str(key)) and str(key) not in SAFE_SENSITIVE_KEYS:
            if isinstance(item, (dict, list)) or item not in SAFE_SENSITIVE_VALUES:
                return True
        if _has_unredacted_sensitive_value(item):
            return True
    return False


def _looks_sensitive_scalar(value: Any) -> bool:
    if value in SAFE_SENSITIVE_VALUES or not isinstance(value, str):
        return False
    return any(pattern.search(value) for pattern in SENSITIVE_VALUE_PATTERNS)


def _reject_duplicate_ledger_identity(path: Path, record: dict[str, Any]) -> None:
    run_id = record.get("runId")
    correlation_id = record.get("correlationId")
    for existing in read_ledger_records(path):
        if run_id and existing.get("runId") == run_id:
            raise ValueError(f"ledger already contains runId: {run_id}")
        if correlation_id and existing.get("correlationId") == correlation_id:
            raise ValueError(f"ledger already contains correlationId: {correlation_id}")


def _ledger_lock_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.lock")


@contextmanager
def _exclusive_ledger_writer(path: Path) -> Iterator[None]:
    if fcntl is None:
        raise OSError("exclusive ledger writer lock requires POSIX fcntl support")
    lock_path = _ledger_lock_path(path)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _increment(histogram: dict[str, int], values: Iterable[str]) -> None:
    for value in values:
        histogram[value] = histogram.get(value, 0) + 1


def _record_dto(record: dict[str, Any]) -> dict[str, Any]:
    trade_log = redact_trade_log_entry(record.get("tradeLogEntry"))
    return {
        "ledgerVersion": record.get("ledgerVersion", 2),
        "recordedAt": record.get("recordedAt"),
        "correlationId": record.get("correlationId"),
        "runId": record.get("runId"),
        "mode": "simulation",
        "environment": "synthetic",
        "strategyId": record.get("strategyId"),
        "strategyVersion": record.get("strategyVersion"),
        "configVersion": record.get("configVersion"),
        "configHash": record.get("configHash"),
        "allocationId": record.get("allocationId"),
        "strategyAllocationId": record.get("strategyAllocationId"),
        "decision": record.get("decision"),
        "riskResult": record.get("riskResult"),
        "riskDecision": record.get("riskDecision", "blocked"),
        "vetoes": list(record.get("vetoes", [])),
        "dataFreshness": record.get("dataFreshness", "unknown"),
        "providerCallStatus": "not-attempted",
        "executionRoute": ABSENT,
        "tradeLog": {
            "action": trade_log.get("action"),
            "reasonCode": trade_log.get("reasonCode"),
            "budgetRemainingUsd": trade_log.get("budgetRemainingUsd"),
            "riskDecision": trade_log.get("riskDecision", "blocked"),
            "providerCall": trade_log.get("providerCall"),
            "executionRoute": trade_log.get("executionRoute"),
            "accountIdentifiers": trade_log.get("accountIdentifiers"),
            "rawProviderPayloads": trade_log.get("rawProviderPayloads"),
        },
    }


def build_ledger_report(
    *,
    records: list[dict[str, Any]] | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    dto_records = [_record_dto(record) for record in (records or [])]
    decision_histogram: dict[str, int] = {}
    risk_result_histogram: dict[str, int] = {}
    veto_histogram: dict[str, int] = {}
    strategy_ids = set()
    recorded_values = []
    for record in dto_records:
        _increment(decision_histogram, [record["decision"]] if record.get("decision") else [])
        _increment(risk_result_histogram, [record["riskResult"]] if record.get("riskResult") else [])
        _increment(veto_histogram, record.get("vetoes", []))
        if record.get("strategyId"):
            strategy_ids.add(record["strategyId"])
        if record.get("recordedAt"):
            recorded_values.append(record["recordedAt"])
    recorded_values.sort()
    return {
        "ledgerReportVersion": 2,
        "dtoVersion": "simulation-ledger-report.v2",
        "mode": "simulation-ledger-report",
        "environment": "synthetic",
        "providerCalls": "blocked",
        "executionRoutes": "absent",
        "demoExecution": "blocked",
        "liveExecution": "blocked",
        "generatedAt": utc_iso(generated_at) if generated_at else utc_iso(datetime.fromisoformat("2026-05-15T00:00:00+00:00")),
        "summary": {
            "recordCount": len(dto_records),
            "skipCount": len([record for record in dto_records if record.get("decision") == "skip"]),
            "blockedCount": len([record for record in dto_records if record.get("riskResult") == "blocked"]),
            "uniqueRunCount": len({record["runId"] for record in dto_records if record.get("runId")}),
            "strategyIds": sorted(strategy_ids),
            "firstRecordedAt": recorded_values[0] if recorded_values else None,
            "lastRecordedAt": recorded_values[-1] if recorded_values else None,
            "decisionHistogram": decision_histogram,
            "riskResultHistogram": risk_result_histogram,
            "vetoHistogram": veto_histogram,
            "redaction": {
                "accountIdentifiers": REDACTED,
                "rawProviderPayloads": ABSENT,
                "providerCall": "not-attempted",
                "executionRoute": ABSENT,
                "providerDemoBalance": REDACTED,
            },
        },
        "records": dto_records,
    }


def export_ledger_report(ledger_path: str | Path) -> dict[str, Any]:
    return build_ledger_report(records=read_ledger_records(ledger_path))
