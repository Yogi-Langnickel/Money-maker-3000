"""Fenced, deterministic orchestration for approved offline diagnostics."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Callable

from money_maker_3000.backtest import build_historical_fixture_backtest, iter_decision_events
from money_maker_3000.contract_manifest import check_dashboard_contract_manifest
from money_maker_3000.contracts import build_allocation_policy, utc_iso
from money_maker_3000.fixture_provenance import (
    FIXTURE_PROVENANCE_ENTRIES,
    check_fixture_provenance_manifest,
)
from money_maker_3000.ledger import (
    append_ledger_record,
    build_ledger_record,
    read_ledger_records_with_integrity,
)
from money_maker_3000.market_history import iter_market_history_bars, sha256_file
from money_maker_3000.readiness import FixtureReadinessSpec, build_backtest_readiness_report
from money_maker_3000.worker_leases import WorkerLeaseStore, WorkerLeaseStoreError


RUNNER_VERSION = "deterministic-offline-simulation-runner.v1"
DEFAULT_MANIFEST_NAME = "offline-simulation-runner-v1.json"
MANIFEST_ID = "approved-slow-trend-fixture-diagnostics"
APPROVED_MANIFEST = {
    "schemaVersion": RUNNER_VERSION,
    "manifestId": MANIFEST_ID,
    "contractManifest": "dashboard-simulation-contract.json",
    "contractManifestSha256": "75571c32a3a04a28ebba29c54edc4dadabfc3101bf101f005a2774e113c060f0",
    "fixtureProvenanceManifest": "market-history-fixture-provenance.json",
    "fixtureProvenanceManifestSha256": "671d36f6c8f02b2ab0b23e4338d2a5095ba801b3123a95d507b84094fcdcaa5e",
    "fixtureFile": "spy-slow-trend-202-daily.csv",
    "fixtureSha256": "ecaec707c5bc6dccc05f0ed5b52f1110ba08a0e0431c59e0d9223baf9ae546d9",
    "strategyId": "slow-trend-allocation",
    "market": "US_EQUITIES",
    "instrumentClass": "ETF",
    "budgetUsd": 1000.0,
    "botAllocationUsd": 1000.0,
    "reservedUsd": 100.0,
    "maxOrderUsd": 250.0,
    "maxFixtureRows": 10000,
    "leaseTtlSeconds": 300,
}
MANIFEST_KEYS = frozenset(APPROVED_MANIFEST)
IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class OfflineRunnerError(ValueError):
    """Controlled, redaction-safe runner failure."""


def run_once(
    *,
    manifest_path: str | Path,
    state_path: str | Path,
    ledger_path: str | Path,
    holder: str,
    idempotency_key: str,
    started_at: datetime,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Run the one approved manifest once, safely recovering the same key.

    No strategy, fixture, allocation, or provider argument is accepted here.
    Those choices are frozen in the committed, allowlisted manifest.
    """
    _validate_identity(holder, "holder")
    _validate_identity(idempotency_key, "idempotency key")
    instant = _validated_instant(started_at)
    project_root, manifest = _load_approved_manifest(manifest_path)
    preflight = _preflight(project_root, manifest, instant)
    if not preflight["structurallyValid"]:
        return _result("preflight-blocked", manifest, preflight)

    _prepare_state_parent(Path(state_path))
    store = WorkerLeaseStore(state_path, clock=clock)
    initialized = store.initialize()
    acquired = store.acquire(
        holder=holder,
        idempotency_key=idempotency_key,
        ttl_seconds=manifest["leaseTtlSeconds"],
    )
    status = acquired["status"]
    if status == "already-completed":
        report, record = _build_diagnostics_and_record(project_root, manifest, instant)
        _confirm_existing_record(Path(ledger_path), record)
        return _result(
            "already-completed",
            manifest,
            preflight,
            lease_status=status,
            diagnostics=_diagnostic_summary(report),
            ledger_status="confirmed-existing",
        )
    if status != "acquired":
        return _result("lease-blocked", manifest, preflight, lease_status=status)

    try:
        report, record = _build_diagnostics_and_record(project_root, manifest, instant)
        completion = store.complete_fenced(
            holder=holder,
            idempotency_key=idempotency_key,
            epoch=acquired["epoch"],
            fence=acquired["fence"],
            operation=lambda: _append_or_confirm_record(Path(ledger_path), record),
        )
    except Exception:
        # A process crash intentionally leaves the lease to expire; ordinary
        # failures release it so the same deterministic key can be recovered.
        try:
            store.release(
                holder=holder,
                idempotency_key=idempotency_key,
                epoch=acquired["epoch"],
                fence=acquired["fence"],
            )
        except WorkerLeaseStoreError:
            pass
        raise

    if not completion["completed"]:
        return _result(
            "completion-blocked",
            manifest,
            preflight,
            lease_status=completion["status"],
            diagnostics=_diagnostic_summary(report),
        )
    return _result(
        "completed",
        manifest,
        preflight,
        lease_status="initialized-and-completed" if initialized["initialized"] else "completed",
        diagnostics=_diagnostic_summary(report),
        ledger_status=completion["operationResult"],
    )


def _load_approved_manifest(manifest_path: str | Path) -> tuple[Path, dict[str, Any]]:
    path = Path(manifest_path).resolve()
    project_root = path.parent.parent
    approved_path = (project_root / "contracts" / DEFAULT_MANIFEST_NAME).resolve()
    if path != approved_path:
        raise OfflineRunnerError("runner manifest path is not allowlisted")
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OfflineRunnerError("runner manifest cannot be read as strict JSON") from exc
    if not isinstance(parsed, dict) or set(parsed) != MANIFEST_KEYS:
        raise OfflineRunnerError("runner manifest fields are invalid")
    if parsed != APPROVED_MANIFEST:
        raise OfflineRunnerError("runner manifest content is not allowlisted")
    _validate_manifest_scalars(parsed)
    provenance = {entry["file"]: entry for entry in FIXTURE_PROVENANCE_ENTRIES}
    fixture = provenance.get(parsed["fixtureFile"])
    if fixture is None or fixture["symbol"] != "SPY":
        raise OfflineRunnerError("runner fixture is not allowlisted")
    return project_root, parsed


def _validate_manifest_scalars(manifest: dict[str, Any]) -> None:
    required_strings = ("fixtureFile", "strategyId", "market", "instrumentClass")
    if any(not isinstance(manifest[name], str) or not manifest[name] for name in required_strings):
        raise OfflineRunnerError("runner manifest string fields are invalid")
    for name in ("budgetUsd", "botAllocationUsd", "reservedUsd", "maxOrderUsd"):
        if not isinstance(manifest[name], (int, float)) or isinstance(manifest[name], bool):
            raise OfflineRunnerError("runner manifest allocation fields are invalid")
    if (
        not isinstance(manifest["maxFixtureRows"], int)
        or isinstance(manifest["maxFixtureRows"], bool)
        or manifest["maxFixtureRows"] < 1
        or not isinstance(manifest["leaseTtlSeconds"], int)
        or isinstance(manifest["leaseTtlSeconds"], bool)
    ):
        raise OfflineRunnerError("runner manifest integer fields are invalid")


def _preflight(project_root: Path, manifest: dict[str, Any], instant: datetime) -> dict[str, Any]:
    contract_ok = check_dashboard_contract_manifest(project_root / "contracts" / manifest["contractManifest"])
    provenance_ok = check_fixture_provenance_manifest(
        project_root / "contracts" / manifest["fixtureProvenanceManifest"],
        project_root / "tests" / "fixtures" / "market_history",
    )
    fixture_path = project_root / "tests" / "fixtures" / "market_history" / manifest["fixtureFile"]
    contract_pinned = _matches_pinned_digest(
        project_root / "contracts" / manifest["contractManifest"], manifest["contractManifestSha256"]
    )
    provenance_pinned = _matches_pinned_digest(
        project_root / "contracts" / manifest["fixtureProvenanceManifest"],
        manifest["fixtureProvenanceManifestSha256"],
    )
    fixture_pinned = _matches_pinned_digest(fixture_path, manifest["fixtureSha256"])
    allocation = _allocation(manifest)
    readiness = build_backtest_readiness_report(
        fixture_specs=[
            FixtureReadinessSpec(
                symbol="SPY",
                path=fixture_path,
                market=manifest["market"],
                instrument_class=manifest["instrumentClass"],
                strategy_id=manifest["strategyId"],
                budget_usd=float(manifest["budgetUsd"]),
                max_fixture_rows=manifest["maxFixtureRows"],
            )
        ],
        started_at=instant,
        allocation_policy=allocation,
    )
    fixture = readiness["fixtureDiagnostics"][0] if readiness["fixtureDiagnostics"] else {"ok": False}
    structural = (
        contract_ok
        and provenance_ok
        and contract_pinned
        and provenance_pinned
        and fixture_pinned
        and bool(readiness["ready"])
        and bool(fixture.get("ok"))
    )
    history = fixture.get("strategyHistoryDiagnostics", {})
    sampling = fixture.get("samplingQuality", {})
    analysis_sufficient = (
        structural
        and history.get("state") not in {"insufficient-history", "invalid-history", None}
        and history.get("walkForward", {}).get("state") not in {"insufficient-history", "invalid-history", None}
        and sampling.get("state") not in {"insufficient-history", None}
    )
    return {
        "structurallyValid": structural,
        "strategyAnalysisSufficient": analysis_sufficient,
        "contractManifest": "pinned-and-current" if contract_ok and contract_pinned else "drifted-or-invalid",
        "fixtureProvenance": "pinned-and-current" if provenance_ok and provenance_pinned else "drifted-or-invalid",
        "fixture": {
            "structurallyValid": bool(fixture.get("ok")) and fixture_pinned,
            "strategyHistoryState": history.get("state"),
            "walkForwardState": history.get("walkForward", {}).get("state"),
            "samplingState": sampling.get("state"),
        },
    }


def _build_diagnostics_and_record(
    project_root: Path, manifest: dict[str, Any], instant: datetime
) -> tuple[dict[str, Any], dict[str, Any]]:
    fixture_path = project_root / "tests" / "fixtures" / "market_history" / manifest["fixtureFile"]
    selected = {"symbol": "SPY", "market": manifest["market"], "instrumentClass": manifest["instrumentClass"]}
    allocation = _allocation(manifest)
    input_sha256 = sha256_file(fixture_path)
    with fixture_path.open("r", encoding="utf-8", newline="") as source:
        report = build_historical_fixture_backtest(
            bars=iter_market_history_bars(source, selected_symbol="SPY"),
            strategy_id=manifest["strategyId"],
            selected_instrument=selected,
            budget_usd=float(manifest["budgetUsd"]),
            allocation_policy=allocation,
            started_at=instant,
            input_sha256=input_sha256,
            max_fixture_rows=manifest["maxFixtureRows"],
        )
    with fixture_path.open("r", encoding="utf-8", newline="") as source:
        events = list(
            iter_decision_events(
                iter_market_history_bars(source, selected_symbol="SPY"),
                strategy_id=manifest["strategyId"],
                selected_instrument=selected,
                budget_usd=float(manifest["budgetUsd"]),
                allocation_policy=allocation,
                started_at=instant,
            )
        )
    if not events:
        raise OfflineRunnerError("approved fixture contains no deterministic diagnostic event")
    return report, build_ledger_record(run=events[-1].run, recorded_at=instant)


def _allocation(manifest: dict[str, Any]) -> dict[str, Any]:
    return build_allocation_policy(
        bot_allocation_usd=float(manifest["botAllocationUsd"]),
        reserved_usd=float(manifest["reservedUsd"]),
        max_order_usd=float(manifest["maxOrderUsd"]),
    )


def _matches_pinned_digest(path: Path, expected: str) -> bool:
    try:
        return sha256_file(path) == expected
    except OSError:
        return False


def _prepare_state_parent(state_path: Path) -> None:
    try:
        state_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    except OSError as exc:
        raise OfflineRunnerError("lease state parent cannot be prepared") from exc


def _append_or_confirm_record(ledger_path: Path, record: dict[str, Any]) -> str:
    if ledger_path.exists():
        recovered = read_ledger_records_with_integrity(ledger_path)
        if recovered["integrity"]["state"] != "clean":
            raise OfflineRunnerError("existing ledger is not a clean v2 ledger")
        if record in recovered["records"]:
            return "already-appended"
    append_ledger_record(ledger_path, record)
    return "appended"


def _confirm_existing_record(ledger_path: Path, record: dict[str, Any]) -> None:
    if not ledger_path.exists():
        raise OfflineRunnerError("completed lease has no ledger record")
    recovered = read_ledger_records_with_integrity(ledger_path)
    if recovered["integrity"]["state"] != "clean":
        raise OfflineRunnerError("existing ledger is not a clean v2 ledger")
    if record not in recovered["records"]:
        raise OfflineRunnerError("completed lease has no matching ledger record")


def _result(
    status: str,
    manifest: dict[str, Any],
    preflight: dict[str, Any],
    *,
    lease_status: str | None = None,
    diagnostics: dict[str, Any] | None = None,
    ledger_status: str | None = None,
) -> dict[str, Any]:
    return {
        "dtoVersion": RUNNER_VERSION,
        "status": status,
        "mode": "offline-simulation-only",
        "manifest": {"schemaVersion": manifest["schemaVersion"], "manifestId": manifest["manifestId"]},
        "preflight": preflight,
        "lease": {"status": lease_status or "not-attempted", "identity": "redacted"},
        "diagnostics": diagnostics or {"status": "not-run"},
        "ledger": {"status": ledger_status or "not-attempted", "content": "redacted"},
        "providerCalls": "blocked",
        "credentials": "absent",
        "accountData": "absent",
        "executionRoutes": "absent",
        "demoExecution": "blocked",
        "liveExecution": "blocked",
    }


def _diagnostic_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "completed",
        "fixtureRows": report["metadata"]["rowCount"],
        "strategyHistoryState": report["strategyHistoryDiagnostics"]["state"],
        "walkForwardState": report["strategyHistoryDiagnostics"]["walkForward"]["state"],
        "samplingState": report["samplingQuality"]["state"],
        "eventCount": report["summary"]["eventCount"],
        "blockedEventCount": report["summary"]["blockedCount"],
        "performanceClaims": "diagnostics-only-no-pnl-or-profitability-claim",
    }


def _validate_identity(value: str, label: str) -> None:
    if not isinstance(value, str) or not IDEMPOTENCY_PATTERN.fullmatch(value):
        raise OfflineRunnerError(f"{label} must be a short opaque identifier")


def _validated_instant(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise OfflineRunnerError("started-at must include an explicit timezone")
    return value.astimezone(timezone.utc)
