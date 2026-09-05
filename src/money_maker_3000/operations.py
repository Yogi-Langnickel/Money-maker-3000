"""Offline-only operational controls for the deterministic simulation runner."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import stat
import subprocess
import tempfile
from typing import Any, Callable

from money_maker_3000.ledger import _read_ledger_records_with_integrity_locked, _shared_ledger_reader, append_ledger_record, read_ledger_records_with_integrity
from money_maker_3000.offline_runner import DEFAULT_MANIFEST_NAME, _build_diagnostics_and_record, _load_approved_manifest, run_once
from money_maker_3000.worker_leases import WorkerLeaseStore


OPERATIONS_VERSION = "offline-simulation-operations.v1"
SNAPSHOT_VERSION = "offline-simulation-snapshot.v1"
SNAPSHOT_PREFIX = "snapshot-"
SNAPSHOT_METADATA = "snapshot.json"
SNAPSHOT_STATE = "lease-state.json"
SNAPSHOT_LOCK = ".lease-state.json.lock"
SNAPSHOT_LEDGER = "ledger.jsonl"
MAX_SNAPSHOT_FILE_BYTES = 16 * 1024 * 1024
MAX_RETENTION = 365
LAUNCHD_LABEL = "local.money-maker-3000.offline-runner"
SAFE_SNAPSHOT_NAME = re.compile(r"^snapshot-[0-9T.-]+Z-[0-9a-f]{12}$")


class OperationsError(ValueError):
    """Controlled operations failure that never includes source contents."""


def build_operations_status(
    *,
    state_path: str | Path,
    ledger_path: str | Path,
    observed_at: datetime,
    max_age_hours: int = 48,
) -> dict[str, Any]:
    instant = _utc(observed_at)
    if not isinstance(max_age_hours, int) or isinstance(max_age_hours, bool) or not 1 <= max_age_hours <= 24 * 31:
        raise OperationsError("max age hours must be between 1 and 744")
    lease = WorkerLeaseStore(state_path).report(observed_at=instant)
    try:
        ledger, ledger_bytes = _read_locked_ledger(Path(ledger_path))
    except (OSError, ValueError):
        return _status("unavailable", lease, "unavailable", None, max_age_hours)
    if ledger["integrity"]["state"] != "clean":
        return _status("blocked", lease, ledger["integrity"]["state"], None, max_age_hours)
    latest = _latest_recorded_at(ledger["records"])
    if latest is None:
        return _status("stale", lease, "clean", None, max_age_hours)
    age = instant - latest
    if age.total_seconds() < 0:
        return _status("blocked", lease, "clean", latest, max_age_hours)
    gate = lease["workerGate"]["state"]
    if not lease["integrity"]["complete"] or gate in {"kill-switch-blocked", "completion-capacity-blocked", "blocked"}:
        state = "blocked"
    elif gate == "busy":
        state = "in-progress"
    elif gate != "available":
        state = "blocked"
    else:
        state = "ready" if age <= timedelta(hours=max_age_hours) else "stale"
    return _status(state, lease, "clean", latest, max_age_hours)


def create_snapshot(
    *,
    state_path: str | Path,
    ledger_path: str | Path,
    snapshot_root: str | Path,
    retain: int,
    created_at: datetime,
) -> dict[str, Any]:
    if not isinstance(retain, int) or isinstance(retain, bool) or not 1 <= retain <= MAX_RETENTION:
        raise OperationsError("snapshot retention must be between 1 and 365")
    instant = _utc(created_at)
    store = WorkerLeaseStore(state_path)
    lease_report = store.report(observed_at=instant)
    if not lease_report["integrity"]["complete"] or lease_report["workerGate"]["state"] != "available":
        raise OperationsError("lease state is not safely idle; snapshot refused")
    state_bytes = _read_regular_bytes(Path(state_path))
    lock_bytes = _read_regular_bytes(Path(state_path).with_name(f".{Path(state_path).name}.lock"))
    try:
        ledger, ledger_bytes = _read_locked_ledger(Path(ledger_path))
    except (OSError, ValueError) as exc:
        raise OperationsError("ledger cannot be read for snapshot") from exc
    if ledger["integrity"]["state"] != "clean":
        raise OperationsError("ledger is not a clean v2 ledger; snapshot refused")
    final_report = store.report(observed_at=instant)
    final_state_bytes = _read_regular_bytes(Path(state_path))
    final_lock_bytes = _read_regular_bytes(Path(state_path).with_name(f".{Path(state_path).name}.lock"))
    if (not final_report["integrity"]["complete"] or final_report["workerGate"]["state"] != "available" or state_bytes != final_state_bytes or lock_bytes != final_lock_bytes):
        raise OperationsError("lease state changed during snapshot; snapshot refused")
    root = _private_directory(Path(snapshot_root))
    _validate_retention_candidates(root)
    snapshot_id = f"{SNAPSHOT_PREFIX}{instant.strftime('%Y%m%dT%H%M%S')}Z-{_digest(state_bytes + lock_bytes + ledger_bytes)[:12]}"
    destination = root / snapshot_id
    if destination.exists():
        raise OperationsError("snapshot identity already exists")
    staging = Path(tempfile.mkdtemp(prefix=".snapshot-staging-", dir=root))
    try:
        _write_private(staging / SNAPSHOT_STATE, state_bytes)
        _write_private(staging / SNAPSHOT_LOCK, lock_bytes)
        _write_private(staging / SNAPSHOT_LEDGER, ledger_bytes)
        metadata = {
            "snapshotVersion": SNAPSHOT_VERSION,
            "snapshotId": snapshot_id,
            "createdAt": _iso(instant),
            "stateSha256": _digest(state_bytes),
            "lockSha256": _digest(lock_bytes),
            "ledgerSha256": _digest(ledger_bytes),
            "ledgerRecordCount": len(ledger["records"]),
            "redaction": "v2-ledger-and-hashed-lease-state-only",
            "providerCalls": "blocked",
            "accountData": "absent",
            "executionRoutes": "absent",
        }
        _write_private(staging / SNAPSHOT_METADATA, _strict_json(metadata))
        os.replace(staging, destination)
        _fsync_directory(root)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    removed = _apply_retention(root, retain=retain)
    return {
        "dtoVersion": OPERATIONS_VERSION,
        "status": "created",
        "snapshot": {"id": snapshot_id, "recordCount": len(ledger["records"]), "content": "redacted"},
        "retention": {"retained": retain, "removed": removed},
        "providerCalls": "blocked",
        "accountData": "absent",
        "executionRoutes": "absent",
    }


def verify_restore(
    *,
    snapshot_root: str | Path,
    snapshot_id: str,
    verification_root: str | Path,
    observed_at: datetime,
) -> dict[str, Any]:
    _utc(observed_at)
    if not SAFE_SNAPSHOT_NAME.fullmatch(snapshot_id):
        raise OperationsError("snapshot identity is invalid")
    source = Path(snapshot_root) / snapshot_id
    metadata, state_bytes, lock_bytes, ledger_bytes = _read_snapshot(source)
    target_root = Path(verification_root)
    if target_root.exists():
        raise OperationsError("restore verification target must not already exist")
    target_root.mkdir(parents=True, mode=0o700)
    try:
        _write_private(target_root / SNAPSHOT_STATE, state_bytes)
        _write_private(target_root / SNAPSHOT_LOCK, lock_bytes)
        _write_private(target_root / SNAPSHOT_LEDGER, ledger_bytes)
        lease = WorkerLeaseStore(target_root / SNAPSHOT_STATE).report(observed_at=_utc(observed_at))
        ledger = read_ledger_records_with_integrity(target_root / SNAPSHOT_LEDGER)
        if not lease["integrity"]["complete"] or ledger["integrity"]["state"] != "clean" or len(ledger["records"]) != metadata["ledgerRecordCount"]:
            raise OperationsError("restored snapshot integrity verification failed")
    except Exception:
        shutil.rmtree(target_root, ignore_errors=True)
        raise
    return {
        "dtoVersion": OPERATIONS_VERSION,
        "status": "verified",
        "snapshot": {"id": metadata["snapshotId"], "recordCount": metadata["ledgerRecordCount"], "content": "redacted"},
        "restore": {"target": "isolated-and-redacted", "leaseIntegrity": "clean", "ledgerIntegrity": "clean"},
        "providerCalls": "blocked",
        "accountData": "absent",
        "executionRoutes": "absent",
    }


def run_soak_evidence(
    *,
    manifest_path: str | Path,
    days: int = 30,
    started_at: datetime,
) -> dict[str, Any]:
    if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 30:
        raise OperationsError("soak evidence days must be between 1 and 30")
    initial = _utc(started_at)
    with tempfile.TemporaryDirectory(prefix="money-maker-soak-") as temp_dir:
        root = Path(temp_dir)
        state_path = root / "state" / "lease.json"
        ledger_path = root / "ledger" / "audit.jsonl"
        outcomes = []
        for offset in range(days):
            instant = initial + timedelta(days=offset)
            outcome = run_once(
                manifest_path=manifest_path,
                state_path=state_path,
                ledger_path=ledger_path,
                holder="offline-operations-soak",
                idempotency_key=f"soak-{instant.strftime('%Y%m%d')}",
                started_at=instant,
                clock=lambda instant=instant: instant,
            )
            if outcome["status"] != "completed":
                raise OperationsError("deterministic soak occurrence did not complete")
            outcomes.append(outcome)
        snapshot = create_snapshot(
            state_path=state_path,
            ledger_path=ledger_path,
            snapshot_root=root / "snapshots",
            retain=1,
            created_at=initial + timedelta(days=days),
        )
        snapshot_id = snapshot["snapshot"]["id"]
        restored = verify_restore(
            snapshot_root=root / "snapshots",
            snapshot_id=snapshot_id,
            verification_root=root / "restore-verification",
            observed_at=initial + timedelta(days=days),
        )
    return {
        "dtoVersion": OPERATIONS_VERSION,
        "status": "completed",
        "evidence": {"simulatedDays": days, "completedOccurrences": len(outcomes), "restore": restored["status"]},
        "wallClockClaim": "deterministic-simulation-only-not-a-real-time-soak",
        "providerCalls": "blocked",
        "accountData": "absent",
        "executionRoutes": "absent",
    }


def run_recovery_drill(*, manifest_path: str | Path, started_at: datetime) -> dict[str, Any]:
    """Prove crash-after-append recovery in an isolated, disposable appliance."""
    initial = _utc(started_at)
    with tempfile.TemporaryDirectory(prefix="money-maker-recovery-") as temp_dir:
        root = Path(temp_dir)
        state_path = root / "state" / "lease.json"
        state_path.parent.mkdir(mode=0o700)
        ledger_path = root / "ledger" / "audit.jsonl"
        ledger_path.parent.mkdir(mode=0o700)
        project_root, manifest = _load_approved_manifest(manifest_path)
        _, record = _build_diagnostics_and_record(project_root, manifest, initial)
        store = WorkerLeaseStore(state_path, clock=lambda: initial)
        store.initialize()
        acquired = store.acquire(holder="offline-operations-recovery", idempotency_key="recovery-drill", ttl_seconds=manifest["leaseTtlSeconds"])
        if acquired["status"] != "acquired":
            raise OperationsError("recovery drill could not acquire its isolated lease")
        append_ledger_record(ledger_path, record)
        recovered = run_once(
            manifest_path=manifest_path,
            state_path=state_path,
            ledger_path=ledger_path,
            holder="offline-operations-recovery",
            idempotency_key="recovery-drill",
            started_at=initial,
            clock=lambda: initial + timedelta(seconds=manifest["leaseTtlSeconds"] + 1),
        )
        if recovered["status"] != "completed" or recovered["ledger"]["status"] != "already-appended":
            raise OperationsError("recovery drill did not prove exact append recovery")
    return {
        "dtoVersion": OPERATIONS_VERSION,
        "status": "completed",
        "recovery": "simulated-crash-after-append-recovered-without-duplicate",
        "providerCalls": "blocked",
        "accountData": "absent",
        "executionRoutes": "absent",
    }


def install_launchd_agent(*, repository_root: str | Path, launch_agents_dir: str | Path, confirm_install: bool) -> dict[str, Any]:
    if not confirm_install:
        raise OperationsError("launchd installation requires explicit confirmation")
    repository = Path(repository_root).resolve()
    canonical_repository = Path(__file__).resolve().parents[2]
    if repository != canonical_repository:
        raise OperationsError("launchd installation is restricted to this checked-out project")
    manifest = repository / "contracts" / DEFAULT_MANIFEST_NAME
    try:
        _load_approved_manifest(manifest)
    except ValueError as exc:
        raise OperationsError("repository approved runner manifest is invalid") from exc
    agents = _launch_agents_directory(Path(launch_agents_dir))
    plist_path = agents / f"{LAUNCHD_LABEL}.plist"
    if plist_path.exists():
        raise OperationsError("launchd agent already exists; overwrite is refused")
    command = (
        "PYTHONPATH=src python3.13 -m money_maker_3000.cli run-once "
        f"--manifest contracts/{DEFAULT_MANIFEST_NAME} "
        "--state-path .local/offline-runner-lease.json --ledger-path .local/offline-runner-ledger.jsonl "
        "--idempotency-key daily-$(date -u +%F) --started-at $(date -u +%FT00:00:00Z)"
    )
    payload = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": ["/usr/bin/env", "zsh", "-lc", command],
        "WorkingDirectory": str(repository),
        "StartCalendarInterval": {"Hour": 9, "Minute": 0},
        "StandardOutPath": str(repository / ".local" / "offline-runner.out.log"),
        "StandardErrorPath": str(repository / ".local" / "offline-runner.err.log"),
    }
    _write_private(plist_path, plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True), mode=0o644)
    try:
        subprocess.run(
            ["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist_path)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        plist_path.unlink(missing_ok=True)
        raise OperationsError("launchd bootstrap failed; installation rolled back") from exc
    return {"dtoVersion": OPERATIONS_VERSION, "status": "installed", "label": LAUNCHD_LABEL, "providerCalls": "blocked"}


def _status(state: str, lease: dict[str, Any], ledger_state: str, latest: datetime | None, max_age_hours: int) -> dict[str, Any]:
    return {
        "dtoVersion": OPERATIONS_VERSION,
        "status": state,
        "lease": {"integrity": lease["integrity"]["state"], "workerState": lease["workerGate"]["state"]},
        "ledger": {"integrity": ledger_state, "latestRecordedAt": _iso(latest) if latest else None},
        "staleAfterHours": max_age_hours,
        "providerCalls": "blocked",
        "accountData": "absent",
        "executionRoutes": "absent",
    }


def _latest_recorded_at(records: list[dict[str, Any]]) -> datetime | None:
    if not records:
        return None
    try:
        return max(datetime.fromisoformat(record["recordedAt"].replace("Z", "+00:00")) for record in records)
    except (KeyError, TypeError, ValueError):
        return None


def _read_snapshot(source: Path) -> tuple[dict[str, Any], bytes, bytes, bytes]:
    if source.is_symlink() or not source.is_dir():
        raise OperationsError("snapshot source is invalid")
    try:
        metadata = json.loads(_read_regular_bytes(source / SNAPSHOT_METADATA).decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, OSError, ValueError) as exc:
        raise OperationsError("snapshot metadata is invalid") from exc
    if not isinstance(metadata, dict) or set(metadata) != {
        "snapshotVersion", "snapshotId", "createdAt", "stateSha256", "lockSha256", "ledgerSha256", "ledgerRecordCount",
        "redaction", "providerCalls", "accountData", "executionRoutes",
    } or metadata.get("snapshotVersion") != SNAPSHOT_VERSION or not SAFE_SNAPSHOT_NAME.fullmatch(str(metadata.get("snapshotId"))) or metadata.get("redaction") != "v2-ledger-and-hashed-lease-state-only" or metadata.get("providerCalls") != "blocked" or metadata.get("accountData") != "absent" or metadata.get("executionRoutes") != "absent" or not isinstance(metadata.get("ledgerRecordCount"), int) or isinstance(metadata.get("ledgerRecordCount"), bool) or metadata["ledgerRecordCount"] < 0 or not _valid_iso(metadata.get("createdAt")) or any(not isinstance(metadata.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", metadata[key]) for key in ("stateSha256", "lockSha256", "ledgerSha256")):
        raise OperationsError("snapshot metadata shape is invalid")
    state_bytes = _read_regular_bytes(source / SNAPSHOT_STATE)
    lock_bytes = _read_regular_bytes(source / SNAPSHOT_LOCK)
    ledger_bytes = _read_regular_bytes(source / SNAPSHOT_LEDGER)
    expected_id = f"{SNAPSHOT_PREFIX}{metadata['createdAt'][:19].replace('-', '').replace(':', '')}Z-{_digest(state_bytes + lock_bytes + ledger_bytes)[:12]}"
    if metadata["snapshotId"] != source.name or metadata["snapshotId"] != expected_id:
        raise OperationsError("snapshot identity verification failed")
    if _digest(state_bytes) != metadata["stateSha256"] or _digest(lock_bytes) != metadata["lockSha256"] or _digest(ledger_bytes) != metadata["ledgerSha256"]:
        raise OperationsError("snapshot digest verification failed")
    return metadata, state_bytes, lock_bytes, ledger_bytes


def _read_locked_ledger(path: Path) -> tuple[dict[str, Any], bytes]:
    with _shared_ledger_reader(path):
        ledger = _read_ledger_records_with_integrity_locked(path)
        return ledger, _read_regular_bytes(path)


def _apply_retention(root: Path, *, retain: int) -> int:
    snapshots = sorted(path for path in root.iterdir() if SAFE_SNAPSHOT_NAME.fullmatch(path.name) and path.is_dir() and not path.is_symlink())
    removed = 0
    for old in snapshots[:-retain]:
        _read_snapshot(old)
        shutil.rmtree(old)
        removed += 1
    _fsync_directory(root)
    return removed


def _validate_retention_candidates(root: Path) -> None:
    for snapshot in root.iterdir():
        if SAFE_SNAPSHOT_NAME.fullmatch(snapshot.name) and snapshot.is_dir() and not snapshot.is_symlink():
            _read_snapshot(snapshot)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _private_directory(path: Path) -> Path:
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise OperationsError("operations directory is invalid")
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise OperationsError("operations directory must not be group or world accessible")
    return path


def _launch_agents_directory(path: Path) -> Path:
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = path.stat()
    if path.is_symlink() or not path.is_dir() or info.st_uid != os.geteuid():
        raise OperationsError("launch agents directory is invalid")
    if info.st_mode & 0o022:
        raise OperationsError("launch agents directory must not be group or world writable")
    return path


def _read_regular_bytes(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise OperationsError("snapshot source file is not regular")
            data = bytearray()
            while chunk := os.read(descriptor, 1024 * 1024):
                data.extend(chunk)
                if len(data) > MAX_SNAPSHOT_FILE_BYTES:
                    raise OperationsError("snapshot source file exceeds size limit")
            return bytes(data)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise OperationsError("snapshot source file cannot be read") from exc


def _write_private(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _strict_json(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise OperationsError("timestamp must include an explicit timezone")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _valid_iso(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return _iso(datetime.fromisoformat(value.replace("Z", "+00:00"))) == value
    except ValueError:
        return False
