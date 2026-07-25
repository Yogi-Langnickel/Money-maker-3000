from __future__ import annotations

from contextlib import contextmanager
import json
from math import isfinite
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised only on non-POSIX hosts.
    fcntl = None

from money_maker_3000.contracts import utc_iso
from money_maker_3000.risk import RISK_VETO_CODES
from money_maker_3000.strategies import STRATEGY_REGISTRY

REDACTED = "redacted"
ABSENT = "absent"
REPORT_DECISIONS = {"skip"}
REPORT_RISK_RESULTS = {"blocked"}
REPORT_RISK_DECISIONS = {"blocked"}
REPORT_TRADE_LOG_ACTIONS = {"simulated-skip"}
REPORT_STRATEGY_IDS = {strategy["strategyId"] for strategy in STRATEGY_REGISTRY}
REPORT_DATA_FRESHNESS = {"fresh", "future-data", "missing", "stale", "unknown"}
REPORT_VETO_CODES = RISK_VETO_CODES
REPORT_REASON_CODES = REPORT_RISK_DECISIONS | REPORT_VETO_CODES
MAX_LEDGER_LINE_BYTES = 64 * 1024
LEDGER_INTEGRITY_STATES = {"clean", "recovered-with-warnings", "corrupted"}
LEDGER_INTEGRITY_ISSUE_CODES = {
    "oversized-record",
    "invalid-utf8",
    "invalid-json",
    "invalid-record-shape",
    "invalid-audit-record",
    "legacy-record-normalized",
    "sensitive-record-redacted",
}
LEDGER_INTEGRITY_ERROR_CODES = {
    "oversized-record",
    "invalid-utf8",
    "invalid-json",
    "invalid-record-shape",
    "invalid-audit-record",
}
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
SIMULATION_AUDIT_ALLOCATION_KEYS = frozenset(
    {
        "botAllocationUsd",
        "reservedUsd",
        "availableUsd",
        "maxOrderUsd",
        "providerDemoBalance",
        "providerBalanceUse",
    }
)
SIMULATION_AUDIT_TRADE_LOG_KEYS = frozenset(
    {
        "tradeLogId",
        "correlationId",
        "action",
        "strategyId",
        "strategyVersion",
        "configHash",
        "allocationId",
        "strategyAllocationId",
        "decision",
        "riskDecision",
        "reasonCode",
        "vetoes",
        "budgetRemainingUsd",
        "dataFreshness",
        "accountIdentifiers",
        "rawProviderPayloads",
        "providerCall",
        "executionRoute",
    }
)
CANONICAL_UTC_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}")


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
    allocation = run.get("allocation")
    raw_trade_log_entry = entry if entry is not None else run.get("tradeLogEntry")
    raw_risk_decision = run.get("riskDecision")
    if not isinstance(allocation, dict):
        raise ValueError("simulation run allocation is invalid")
    if not isinstance(raw_trade_log_entry, dict):
        raise ValueError("simulation run trade log is invalid")
    if not isinstance(raw_risk_decision, dict) or "decision" not in raw_risk_decision:
        raise ValueError("simulation run risk decision is invalid")
    if recorded_at is None and "evaluatedAt" not in run:
        raise ValueError("simulation run evaluated time is missing")
    trade_log_entry = redact_trade_log_entry(raw_trade_log_entry)
    record = {
        "ledgerVersion": 2,
        "dtoVersion": "simulation-audit-record.v2",
        "recordedAt": utc_iso(recorded_at) if recorded_at else run["evaluatedAt"],
        "correlationId": run.get("correlationId"),
        "runId": run.get("runId"),
        "mode": "simulation",
        "environment": "synthetic",
        "strategyId": run.get("strategyId"),
        "strategyVersion": run.get("strategyVersion"),
        "configVersion": run.get("configVersion"),
        "configHash": run.get("configHash"),
        "allocationId": allocation.get("allocationId"),
        "strategyAllocationId": allocation.get("strategyAllocationId"),
        "allocation": {
            "botAllocationUsd": allocation.get("botAllocationUsd"),
            "reservedUsd": allocation.get("reservedUsd"),
            "availableUsd": allocation.get("availableUsd"),
            "maxOrderUsd": allocation.get("maxOrderUsd"),
            "providerDemoBalance": REDACTED,
            "providerBalanceUse": "ignored-for-budget",
        },
        "decision": run.get("decision"),
        "riskResult": run.get("riskResult"),
        "riskDecision": raw_risk_decision["decision"],
        "vetoes": list(run.get("vetoes", [])),
        "dataFreshness": raw_trade_log_entry.get("dataFreshness"),
        "providerCallStatus": "not-attempted",
        "executionRoute": ABSENT,
        "tradeLogEntry": trade_log_entry,
    }
    return _validate_simulation_audit_record(record)


def append_ledger_record(ledger_path: str | Path, record: dict[str, Any]) -> dict[str, Any]:
    if not ledger_path:
        raise TypeError("ledger path is required")
    validated_record = _validate_simulation_audit_record(record)
    path = Path(ledger_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _exclusive_ledger_writer(path):
        if path.exists():
            recovered = read_ledger_records_with_integrity(path)
            if recovered["integrity"]["state"] != "clean":
                raise ValueError("existing ledger is not a clean v2 ledger; append refused")
            _reject_duplicate_ledger_identity(recovered["records"], validated_record)
        with path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(validated_record, sort_keys=True, separators=(",", ":")) + "\n")
            output.flush()
            os.fsync(output.fileno())
    return validated_record


def read_ledger_records(ledger_path: str | Path) -> list[dict[str, Any]]:
    recovered = read_ledger_records_with_integrity(ledger_path)
    if recovered["integrity"]["state"] == "corrupted":
        raise ValueError("ledger is corrupted; raw records are unavailable")
    return recovered["records"]


def read_ledger_records_with_integrity(ledger_path: str | Path) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    nonempty_line_count = 0
    path = Path(ledger_path)
    with path.open("rb") as source:
        line_number = 0
        while raw_line := source.readline(MAX_LEDGER_LINE_BYTES + 1):
            line_number += 1
            if len(raw_line) > MAX_LEDGER_LINE_BYTES:
                has_nonwhitespace = bool(raw_line.strip())
                while not raw_line.endswith(b"\n"):
                    raw_line = source.readline(MAX_LEDGER_LINE_BYTES + 1)
                    if not raw_line:
                        break
                    has_nonwhitespace = has_nonwhitespace or bool(raw_line.strip())
                if has_nonwhitespace:
                    nonempty_line_count += 1
                    issues.append(_ledger_integrity_issue(line_number, "oversized-record", "error"))
                continue
            if not raw_line.strip():
                continue
            nonempty_line_count += 1
            try:
                text = raw_line.decode("utf-8")
            except UnicodeDecodeError:
                issues.append(_ledger_integrity_issue(line_number, "invalid-utf8", "error"))
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                issues.append(_ledger_integrity_issue(line_number, "invalid-json", "error"))
                continue
            if not isinstance(parsed, dict):
                issues.append(_ledger_integrity_issue(line_number, "invalid-record-shape", "error"))
                continue

            if _is_v2_record_candidate(parsed):
                try:
                    parsed = _validate_simulation_audit_record(parsed)
                except (TypeError, ValueError):
                    issues.append(_ledger_integrity_issue(line_number, "invalid-audit-record", "error"))
                    continue
            elif _is_explicit_legacy_v1_record(parsed):
                issues.append(_ledger_integrity_issue(line_number, "legacy-record-normalized", "warning"))
                if _has_unredacted_sensitive_value(parsed):
                    issues.append(_ledger_integrity_issue(line_number, "sensitive-record-redacted", "warning"))
                parsed = redact_mapping(parsed)
            else:
                issues.append(_ledger_integrity_issue(line_number, "invalid-audit-record", "error"))
                continue
            records.append(parsed)

    error_count = len([issue for issue in issues if issue["severity"] == "error"])
    warning_count = len(issues) - error_count
    return {
        "records": records,
        "integrity": {
            "state": "corrupted" if error_count else ("recovered-with-warnings" if warning_count else "clean"),
            "complete": error_count == 0,
            "nonemptyLineCount": nonempty_line_count,
            "acceptedRecordCount": len(records),
            "rejectedRecordCount": error_count,
            "warningCount": warning_count,
            "errorCount": error_count,
            "issues": issues,
            "sourceMutation": "not-attempted",
        },
    }


def _ledger_integrity_issue(line_number: int, code: str, severity: str) -> dict[str, Any]:
    return {
        "lineNumber": line_number,
        "code": code,
        "severity": severity,
        "rawContent": "absent",
    }


def _validated_ledger_integrity(integrity: dict[str, Any] | None, record_count: int) -> dict[str, Any]:
    if integrity is None:
        return {
            "state": "not-assessed",
            "complete": False,
            "nonemptyLineCount": record_count,
            "acceptedRecordCount": record_count,
            "rejectedRecordCount": 0,
            "warningCount": 0,
            "errorCount": 0,
            "issues": [],
            "sourceMutation": "not-attempted",
        }
    expected_keys = {
        "state",
        "complete",
        "nonemptyLineCount",
        "acceptedRecordCount",
        "rejectedRecordCount",
        "warningCount",
        "errorCount",
        "issues",
        "sourceMutation",
    }
    if not isinstance(integrity, dict) or set(integrity) != expected_keys:
        raise ValueError("ledger integrity metadata is invalid")
    count_keys = (
        "nonemptyLineCount",
        "acceptedRecordCount",
        "rejectedRecordCount",
        "warningCount",
        "errorCount",
    )
    if (
        integrity["state"] not in LEDGER_INTEGRITY_STATES
        or not isinstance(integrity["complete"], bool)
        or integrity["sourceMutation"] != "not-attempted"
        or any(
            not isinstance(integrity[key], int) or isinstance(integrity[key], bool) or integrity[key] < 0
            for key in count_keys
        )
        or integrity["acceptedRecordCount"] != record_count
        or integrity["rejectedRecordCount"] != integrity["errorCount"]
        or integrity["nonemptyLineCount"] != record_count + integrity["rejectedRecordCount"]
        or not isinstance(integrity["issues"], list)
        or len(integrity["issues"]) != integrity["warningCount"] + integrity["errorCount"]
    ):
        raise ValueError("ledger integrity metadata is invalid")
    issues = []
    for issue in integrity["issues"]:
        if (
            not isinstance(issue, dict)
            or set(issue) != {"lineNumber", "code", "severity", "rawContent"}
            or not isinstance(issue["lineNumber"], int)
            or isinstance(issue["lineNumber"], bool)
            or issue["lineNumber"] < 1
            or issue["code"] not in LEDGER_INTEGRITY_ISSUE_CODES
            or issue["severity"] not in {"warning", "error"}
            or (issue["code"] in LEDGER_INTEGRITY_ERROR_CODES) != (issue["severity"] == "error")
            or issue["rawContent"] != "absent"
        ):
            raise ValueError("ledger integrity metadata is invalid")
        issues.append(dict(issue))
    error_count = len([issue for issue in issues if issue["severity"] == "error"])
    warning_count = len(issues) - error_count
    expected_state = "corrupted" if error_count else ("recovered-with-warnings" if warning_count else "clean")
    if (
        integrity["state"] != expected_state
        or integrity["complete"] != (error_count == 0)
        or integrity["errorCount"] != error_count
        or integrity["warningCount"] != warning_count
    ):
        raise ValueError("ledger integrity metadata is invalid")
    return {**integrity, "issues": issues}


def _validate_simulation_audit_record(record: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise TypeError("ledger record must be a JSON object")
    if set(record) != SIMULATION_AUDIT_RECORD_KEYS:
        raise ValueError("ledger record fields do not match simulation-audit-record.v2")
    if type(record["ledgerVersion"]) is not int or record["ledgerVersion"] != 2:
        raise ValueError("ledger record version is invalid")
    if record["dtoVersion"] != "simulation-audit-record.v2":
        raise ValueError("ledger record DTO version is invalid")
    _validate_canonical_utc(record["recordedAt"])
    for key in (
        "correlationId",
        "runId",
        "strategyVersion",
        "configVersion",
        "allocationId",
        "strategyAllocationId",
    ):
        _validate_identifier(record[key], key)
    if record["mode"] != "simulation" or record["environment"] != "synthetic":
        raise ValueError("ledger record must be simulation/synthetic only")
    if record["strategyId"] not in REPORT_STRATEGY_IDS:
        raise ValueError("ledger record strategy is invalid")
    if not isinstance(record["configHash"], str) or not SHA256_PATTERN.fullmatch(record["configHash"]):
        raise ValueError("ledger record config hash is invalid")
    if record["decision"] != "skip":
        raise ValueError("ledger record decision is invalid")
    if record["riskResult"] != "blocked" or record["riskDecision"] != "blocked":
        raise ValueError("ledger record risk result is invalid")
    vetoes = _validate_vetoes(record["vetoes"])
    if record["dataFreshness"] not in REPORT_DATA_FRESHNESS:
        raise ValueError("ledger record data freshness is invalid")
    if record["providerCallStatus"] != "not-attempted" or record["executionRoute"] != ABSENT:
        raise ValueError("ledger record provider boundary is invalid")
    _validate_allocation(record["allocation"])
    _validate_trade_log(record["tradeLogEntry"], record, vetoes)
    if _has_unredacted_sensitive_value(record):
        raise ValueError("ledger record contains unredacted sensitive data")
    return dict(record)


def _validate_identifier(value: Any, field: str) -> None:
    if not isinstance(value, str) or not IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(f"ledger record {field} is invalid")


def _validate_canonical_utc(value: Any) -> None:
    if not isinstance(value, str) or not CANONICAL_UTC_PATTERN.fullmatch(value):
        raise ValueError("ledger record timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("ledger record timestamp is invalid") from error
    if parsed.tzinfo != timezone.utc or utc_iso(parsed) != value:
        raise ValueError("ledger record timestamp is invalid")


def _validate_number(value: Any, field: str, *, positive: bool = False) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value < 0
        or (positive and value <= 0)
    ):
        raise ValueError(f"ledger record {field} is invalid")
    return float(value)


def _validate_vetoes(value: Any) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or item not in RISK_VETO_CODES for item in value)
        or len(value) != len(set(value))
    ):
        raise ValueError("ledger record vetoes are invalid")
    return list(value)


def _validate_allocation(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != SIMULATION_AUDIT_ALLOCATION_KEYS:
        raise ValueError("ledger record allocation is invalid")
    bot = _validate_number(value["botAllocationUsd"], "allocation", positive=True)
    reserved = _validate_number(value["reservedUsd"], "allocation")
    available = _validate_number(value["availableUsd"], "allocation")
    max_order = _validate_number(value["maxOrderUsd"], "allocation", positive=True)
    if (
        abs((bot - reserved) - available) > 1e-9
        or reserved > bot
        or max_order > available
        or value["providerDemoBalance"] != REDACTED
        or value["providerBalanceUse"] != "ignored-for-budget"
    ):
        raise ValueError("ledger record allocation is invalid")


def _validate_trade_log(value: Any, parent: dict[str, Any], vetoes: list[str]) -> None:
    if not isinstance(value, dict) or set(value) != SIMULATION_AUDIT_TRADE_LOG_KEYS:
        raise ValueError("ledger record trade log is invalid")
    _validate_identifier(value["tradeLogId"], "trade log ID")
    matching_fields = (
        "correlationId",
        "strategyId",
        "strategyVersion",
        "configHash",
        "allocationId",
        "strategyAllocationId",
        "riskDecision",
        "dataFreshness",
    )
    if any(value[field] != parent[field] for field in matching_fields):
        raise ValueError("ledger record trade log does not match its parent")
    nested_vetoes = _validate_vetoes(value["vetoes"])
    if nested_vetoes != vetoes:
        raise ValueError("ledger record trade log vetoes do not match its parent")
    if (
        value["action"] != "simulated-skip"
        or value["decision"] != "blocked"
        or value["reasonCode"] not in REPORT_REASON_CODES
        or (value["reasonCode"] != "blocked" and value["reasonCode"] not in vetoes)
        or value["accountIdentifiers"] != REDACTED
        or value["rawProviderPayloads"] != ABSENT
        or value["providerCall"] != "not-attempted"
        or value["executionRoute"] != ABSENT
    ):
        raise ValueError("ledger record trade log is invalid")
    _validate_number(value["budgetRemainingUsd"], "trade log budget")


def _is_v2_record_candidate(record: dict[str, Any]) -> bool:
    return record.get("ledgerVersion") == 2 or record.get("dtoVersion") == "simulation-audit-record.v2"


def _is_explicit_legacy_v1_record(record: dict[str, Any]) -> bool:
    return record.get("ledgerVersion") == 1 and record.get("dtoVersion") in (None, "simulation-audit-record.v1")


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


def _reject_duplicate_ledger_identity(records: list[dict[str, Any]], record: dict[str, Any]) -> None:
    run_id = record.get("runId")
    correlation_id = record.get("correlationId")
    for existing in records:
        if run_id and existing.get("runId") == run_id:
            raise ValueError("ledger already contains the run identity")
        if correlation_id and existing.get("correlationId") == correlation_id:
            raise ValueError("ledger already contains the correlation identity")


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
        if isinstance(value, str) and value:
            histogram[value] = histogram.get(value, 0) + 1


def _report_scalar(value: Any, default: Any = None) -> Any:
    redacted = _redact_value(value)
    if isinstance(redacted, (dict, list)):
        return default
    return redacted if redacted is not None else default


def _report_list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        redacted = _redact_value(item)
        if not isinstance(redacted, (dict, list)):
            result.append(redacted)
    return result


def _allowed_report_scalar(value: Any, allowed: set[str], default: str | None = None) -> str | None:
    scalar = _report_scalar(value)
    if isinstance(scalar, str) and scalar in allowed:
        return scalar
    return default


def _allowed_report_list(value: Any, allowed: set[str]) -> list[str]:
    return [item for item in _report_list(value) if isinstance(item, str) and item in allowed]


def _report_number(value: Any) -> int | float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _sanitize_report_record(record: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise TypeError("ledger report records must be JSON objects")
    redacted = redact_mapping(record)
    trade_log = redact_trade_log_entry(redacted.get("tradeLogEntry"))
    return {
        "ledgerVersion": _report_scalar(redacted.get("ledgerVersion"), 2),
        "recordedAt": _report_scalar(redacted.get("recordedAt")),
        "correlationId": _report_scalar(redacted.get("correlationId")),
        "runId": _report_scalar(redacted.get("runId")),
        "strategyId": _allowed_report_scalar(redacted.get("strategyId"), REPORT_STRATEGY_IDS),
        "strategyVersion": _report_scalar(redacted.get("strategyVersion")),
        "configVersion": _report_scalar(redacted.get("configVersion")),
        "configHash": _report_scalar(redacted.get("configHash")),
        "allocationId": _report_scalar(redacted.get("allocationId")),
        "strategyAllocationId": _report_scalar(redacted.get("strategyAllocationId")),
        "decision": _allowed_report_scalar(redacted.get("decision"), REPORT_DECISIONS, "skip"),
        "riskResult": _allowed_report_scalar(redacted.get("riskResult"), REPORT_RISK_RESULTS, "blocked"),
        "riskDecision": _allowed_report_scalar(redacted.get("riskDecision"), REPORT_RISK_DECISIONS, "blocked"),
        "vetoes": _allowed_report_list(redacted.get("vetoes"), REPORT_VETO_CODES),
        "dataFreshness": _allowed_report_scalar(redacted.get("dataFreshness"), REPORT_DATA_FRESHNESS, "unknown"),
        "tradeLogEntry": trade_log,
    }


def _record_dto(record: dict[str, Any]) -> dict[str, Any]:
    if _is_v2_record_candidate(record):
        record = _validate_simulation_audit_record(record)
    elif not _is_explicit_legacy_v1_record(record):
        raise ValueError("ledger report record version is unsupported")
    sanitized = _sanitize_report_record(record)
    trade_log = sanitized["tradeLogEntry"]
    return {
        "ledgerVersion": sanitized["ledgerVersion"],
        "recordedAt": sanitized["recordedAt"],
        "correlationId": sanitized["correlationId"],
        "runId": sanitized["runId"],
        "mode": "simulation",
        "environment": "synthetic",
        "strategyId": sanitized["strategyId"],
        "strategyVersion": sanitized["strategyVersion"],
        "configVersion": sanitized["configVersion"],
        "configHash": sanitized["configHash"],
        "allocationId": sanitized["allocationId"],
        "strategyAllocationId": sanitized["strategyAllocationId"],
        "decision": sanitized["decision"],
        "riskResult": sanitized["riskResult"],
        "riskDecision": sanitized["riskDecision"],
        "vetoes": sanitized["vetoes"],
        "dataFreshness": sanitized["dataFreshness"],
        "providerCallStatus": "not-attempted",
        "executionRoute": ABSENT,
        "tradeLog": {
            "action": _allowed_report_scalar(trade_log.get("action"), REPORT_TRADE_LOG_ACTIONS, "simulated-skip"),
            "reasonCode": _allowed_report_scalar(trade_log.get("reasonCode"), REPORT_REASON_CODES, "blocked"),
            "budgetRemainingUsd": _report_number(trade_log.get("budgetRemainingUsd")),
            "riskDecision": _allowed_report_scalar(trade_log.get("riskDecision"), REPORT_RISK_DECISIONS, "blocked"),
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
    integrity: dict[str, Any] | None = None,
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
        if isinstance(record.get("recordedAt"), str) and record.get("recordedAt"):
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
        "integrity": _validated_ledger_integrity(integrity, len(dto_records)),
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
    recovered = read_ledger_records_with_integrity(ledger_path)
    return build_ledger_report(records=recovered["records"], integrity=recovered["integrity"])
