from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import stat
import time
from typing import Any, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - the store deliberately has no non-POSIX fallback.
    fcntl = None


STORE_VERSION = 1
DTO_VERSION = "simulation-worker-lease-report.v1"
MAX_STORE_BYTES = 2 * 1024 * 1024
MAX_OPAQUE_VALUE_BYTES = 512
MAX_TTL_SECONDS = 24 * 60 * 60
MAX_COMPLETION_MARKERS = 4096
MAX_COUNTER = (1 << 63) - 1
MAX_LOCK_WAIT_SECONDS = 30.0
KILL_SWITCH_ENGAGE_REASONS = frozenset({"operator-stop", "risk-stop", "maintenance"})
KILL_SWITCH_REENABLE_REASON = "operator-reenable"
_HASH_PREFIX = b"money-maker-3000:simulation-worker-lease:v1\x00"
_STATE_KEYS = frozenset(
    {
        "version",
        "revision",
        "fenceGeneration",
        "lastMutationAt",
        "killSwitch",
        "lease",
        "lastRelease",
        "completions",
    }
)
_KILL_SWITCH_KEYS = frozenset({"engaged", "reason", "changedAt"})
_LEASE_KEYS = frozenset(
    {"holderHash", "idempotencyHash", "fence", "acquiredAt", "updatedAt", "expiresAt"}
)
_COMPLETION_KEYS = frozenset({"holderHash", "idempotencyHash", "fence", "completedAt"})
_RELEASE_KEYS = frozenset({"holderHash", "idempotencyHash", "fence", "releasedAt"})


class WorkerLeaseStoreError(ValueError):
    """A controlled fail-closed lease-store error."""


class WorkerLeaseStore:
    """Strict local state for one simulation worker lease.

    Authorization results are snapshots. A future side effect must hold this
    store's lock and atomically recheck holder, idempotency key, fence, expiry,
    and kill-switch state immediately before the side effect.
    """

    def __init__(self, state_path: str | Path, *, lock_wait_seconds: float = 1.0) -> None:
        self.path = _validated_path(state_path)
        self.lock_path = self.path.with_name(f".{self.path.name}.lock")
        self.lock_wait_seconds = _validated_lock_wait(lock_wait_seconds)

    def initialize(self, *, now: datetime) -> dict[str, Any]:
        instant = _validated_now(now)
        _validate_parent_directory(self.path)
        try:
            os.lstat(self.path)
            state_exists = True
        except FileNotFoundError:
            state_exists = False
        with self._lock(create=not state_exists) as lock_fd:
            try:
                state, _ = _read_state(self.path)
            except FileNotFoundError:
                state = {
                    "version": STORE_VERSION,
                    "revision": 0,
                    "fenceGeneration": 0,
                    "lastMutationAt": _format_time(instant),
                    "killSwitch": {"engaged": False, "reason": None, "changedAt": None},
                    "lease": None,
                    "lastRelease": None,
                    "completions": [],
                }
                _write_state(
                    self.path,
                    state,
                    self.lock_path,
                    lock_identity=_fd_identity(lock_fd),
                    expect_missing=True,
                )
                return {"status": "initialized", "initialized": True}
            _reject_time_reversal(state, instant)
            return {"status": "already-initialized", "initialized": True}

    def acquire(
        self,
        *,
        holder: str,
        idempotency_key: str,
        ttl_seconds: int,
        now: datetime,
    ) -> dict[str, Any]:
        holder_hash = _opaque_hash("holder", holder)
        idempotency_hash = _opaque_hash("idempotency", idempotency_key)
        ttl = _validated_ttl(ttl_seconds)
        instant = _validated_now(now)
        with self._lock() as lock_fd:
            state, identity = _read_state(self.path)
            _reject_time_reversal(state, instant)
            if state["killSwitch"]["engaged"]:
                return _acquire_result("kill-switch-blocked")

            if any(marker["idempotencyHash"] == idempotency_hash for marker in state["completions"]):
                return _acquire_result("already-completed")

            lease = state["lease"]
            if lease is not None and instant < _parse_time(lease["expiresAt"]):
                if _same_identity(lease, holder_hash, idempotency_hash):
                    return _acquire_result("held", fence=lease["fence"], expires_at=lease["expiresAt"])
                return _acquire_result("busy")
            if len(state["completions"]) >= MAX_COMPLETION_MARKERS:
                return _acquire_result("completion-capacity-exhausted")

            generation = _next_counter(state["fenceGeneration"], "fence generation")
            timestamp = _format_time(instant)
            expires_at = _format_time(_lease_expiry(instant, ttl))
            state["fenceGeneration"] = generation
            state["revision"] = _next_counter(state["revision"], "revision")
            state["lastMutationAt"] = timestamp
            state["lastRelease"] = None
            state["lease"] = {
                "holderHash": holder_hash,
                "idempotencyHash": idempotency_hash,
                "fence": generation,
                "acquiredAt": timestamp,
                "updatedAt": timestamp,
                "expiresAt": expires_at,
            }
            _write_state(
                self.path,
                state,
                self.lock_path,
                lock_identity=_fd_identity(lock_fd),
                expected_identity=identity,
            )
            return _acquire_result("acquired", fence=generation, expires_at=expires_at)

    def authorize(
        self,
        *,
        holder: str,
        idempotency_key: str,
        fence: int,
        now: datetime,
    ) -> dict[str, Any]:
        holder_hash, idempotency_hash, generation, instant = _validated_credentials(
            holder, idempotency_key, fence, now
        )
        with self._lock():
            state, _ = _read_state(self.path)
            _reject_time_reversal(state, instant)
            reason = _authorization_reason(state, holder_hash, idempotency_hash, generation, instant)
            return {
                "authorized": reason == "authorized",
                "reason": reason,
                "snapshotOnly": True,
                "sideEffectRule": "atomically-recheck-holder-idempotency-fence-before-side-effect",
            }

    def renew(
        self,
        *,
        holder: str,
        idempotency_key: str,
        fence: int,
        ttl_seconds: int,
        now: datetime,
    ) -> dict[str, Any]:
        holder_hash, idempotency_hash, generation, instant = _validated_credentials(
            holder, idempotency_key, fence, now
        )
        ttl = _validated_ttl(ttl_seconds)
        with self._lock() as lock_fd:
            state, identity = _read_state(self.path)
            _reject_time_reversal(state, instant)
            reason = _authorization_reason(state, holder_hash, idempotency_hash, generation, instant)
            if reason != "authorized":
                return {"status": reason, "renewed": False}
            lease = state["lease"]
            assert lease is not None
            timestamp = _format_time(instant)
            expires_at = _format_time(_lease_expiry(instant, ttl))
            if lease["updatedAt"] == timestamp and lease["expiresAt"] == expires_at:
                return {"status": "renewed", "renewed": True, "expiresAt": expires_at}
            state["revision"] = _next_counter(state["revision"], "revision")
            state["lastMutationAt"] = timestamp
            lease["updatedAt"] = timestamp
            lease["expiresAt"] = expires_at
            _write_state(
                self.path,
                state,
                self.lock_path,
                lock_identity=_fd_identity(lock_fd),
                expected_identity=identity,
            )
            return {"status": "renewed", "renewed": True, "expiresAt": expires_at}

    def release(
        self,
        *,
        holder: str,
        idempotency_key: str,
        fence: int,
        now: datetime,
    ) -> dict[str, Any]:
        return self._finish_active(
            action="release",
            holder=holder,
            idempotency_key=idempotency_key,
            fence=fence,
            now=now,
        )

    def complete(
        self,
        *,
        holder: str,
        idempotency_key: str,
        fence: int,
        now: datetime,
    ) -> dict[str, Any]:
        return self._finish_active(
            action="complete",
            holder=holder,
            idempotency_key=idempotency_key,
            fence=fence,
            now=now,
        )

    def _finish_active(
        self,
        *,
        action: str,
        holder: str,
        idempotency_key: str,
        fence: int,
        now: datetime,
    ) -> dict[str, Any]:
        holder_hash, idempotency_hash, generation, instant = _validated_credentials(
            holder, idempotency_key, fence, now
        )
        with self._lock() as lock_fd:
            state, identity = _read_state(self.path)
            _reject_time_reversal(state, instant)
            if action == "release":
                released = state["lastRelease"]
                if (
                    released
                    and _same_identity(released, holder_hash, idempotency_hash)
                    and released["fence"] == generation
                ):
                    return {"status": "already-released", "released": True}
            else:
                completion = next(
                    (
                        marker
                        for marker in state["completions"]
                        if marker["idempotencyHash"] == idempotency_hash
                    ),
                    None,
                )
                if (
                    completion
                    and completion["holderHash"] == holder_hash
                    and completion["fence"] == generation
                ):
                    return {"status": "already-completed", "completed": True}
                if completion:
                    return {"status": "already-completed", "completed": False}

            reason = _authorization_reason(state, holder_hash, idempotency_hash, generation, instant)
            if reason != "authorized":
                return {"status": reason, f"{action}d": False}
            if action == "complete" and len(state["completions"]) >= MAX_COMPLETION_MARKERS:
                return {"status": "completion-capacity-exhausted", "completed": False}
            timestamp = _format_time(instant)
            state["fenceGeneration"] = _next_counter(state["fenceGeneration"], "fence generation")
            state["revision"] = _next_counter(state["revision"], "revision")
            state["lastMutationAt"] = timestamp
            state["lease"] = None
            if action == "complete":
                state["completions"].append(
                    {
                        "holderHash": holder_hash,
                        "idempotencyHash": idempotency_hash,
                        "fence": generation,
                        "completedAt": timestamp,
                    }
                )
                state["lastRelease"] = None
            else:
                state["lastRelease"] = {
                    "holderHash": holder_hash,
                    "idempotencyHash": idempotency_hash,
                    "fence": generation,
                    "releasedAt": timestamp,
                }
            _write_state(
                self.path,
                state,
                self.lock_path,
                lock_identity=_fd_identity(lock_fd),
                expected_identity=identity,
            )
            return {"status": f"{action}d", f"{action}d": True}

    def engage_kill_switch(self, *, now: datetime, reason: str = "operator-stop") -> dict[str, Any]:
        if reason not in KILL_SWITCH_ENGAGE_REASONS:
            raise WorkerLeaseStoreError("kill-switch reason is not allowlisted")
        return self._set_kill_switch(engaged=True, reason=reason, now=now)

    def reenable(self, *, now: datetime) -> dict[str, Any]:
        return self._set_kill_switch(engaged=False, reason=KILL_SWITCH_REENABLE_REASON, now=now)

    def _set_kill_switch(self, *, engaged: bool, reason: str, now: datetime) -> dict[str, Any]:
        instant = _validated_now(now)
        with self._lock() as lock_fd:
            state, identity = _read_state(self.path)
            _reject_time_reversal(state, instant)
            if state["killSwitch"]["engaged"] is engaged:
                status = "already-engaged" if engaged else "already-enabled"
                return {
                    "status": status,
                    "killSwitchEngaged": engaged,
                    "reason": state["killSwitch"]["reason"],
                }
            timestamp = _format_time(instant)
            state["fenceGeneration"] = _next_counter(state["fenceGeneration"], "fence generation")
            state["revision"] = _next_counter(state["revision"], "revision")
            state["lastMutationAt"] = timestamp
            state["killSwitch"] = {"engaged": engaged, "reason": reason, "changedAt": timestamp}
            state["lease"] = None
            _write_state(
                self.path,
                state,
                self.lock_path,
                lock_identity=_fd_identity(lock_fd),
                expected_identity=identity,
            )
            return {
                "status": "engaged" if engaged else "re-enabled",
                "killSwitchEngaged": engaged,
                "reason": reason,
            }

    def report(self, *, now: datetime) -> dict[str, Any]:
        instant = _validated_now(now)
        try:
            os.lstat(self.path)
        except FileNotFoundError:
            return _lease_report(
                initialized=False,
                integrity_state="uninitialized",
                worker_state="blocked",
                completion_count=0,
            )
        try:
            with self._lock():
                state, _ = _read_state(self.path)
        except (OSError, WorkerLeaseStoreError, TimeoutError):
            return _lease_report(initialized=True, integrity_state="corrupted", worker_state="blocked")
        _reject_time_reversal(state, instant)

        if state["killSwitch"]["engaged"]:
            worker_state = "kill-switch-blocked"
        elif len(state["completions"]) >= MAX_COMPLETION_MARKERS and (
            state["lease"] is None or instant >= _parse_time(state["lease"]["expiresAt"])
        ):
            worker_state = "completion-capacity-blocked"
        elif state["lease"] is None:
            worker_state = "available"
        elif instant >= _parse_time(state["lease"]["expiresAt"]):
            worker_state = "expired-takeover-available"
        else:
            worker_state = "busy"
        return _lease_report(
            initialized=True,
            integrity_state="clean",
            worker_state=worker_state,
            completion_recorded=bool(state["completions"]),
            completion_count=len(state["completions"]),
            kill_switch_reason=state["killSwitch"]["reason"],
        )

    @contextmanager
    def _lock(self, *, create: bool = False) -> Iterator[int]:
        with _locked_file(self.lock_path, self.lock_wait_seconds, create=create) as lock_fd:
            yield lock_fd
            _verify_open_identity(self.lock_path, lock_fd, "lease lock")


def build_worker_lease_report(
    state_path: str | Path,
    *,
    now: datetime,
    lock_wait_seconds: float = 1.0,
) -> dict[str, Any]:
    return WorkerLeaseStore(state_path, lock_wait_seconds=lock_wait_seconds).report(now=now)


def _lease_report(
    *,
    initialized: bool,
    integrity_state: str,
    worker_state: str,
    completion_recorded: bool = False,
    completion_count: int | None = None,
    kill_switch_reason: str | None = None,
) -> dict[str, Any]:
    completion_remaining = None if completion_count is None else MAX_COMPLETION_MARKERS - completion_count
    return {
        "dtoVersion": DTO_VERSION,
        "mode": "simulation-worker-lease-report",
        "environment": "synthetic",
        "initialized": initialized,
        "integrity": {
            "state": integrity_state,
            "complete": integrity_state == "clean",
            "sourceMutation": "not-attempted",
            "rawContent": "absent",
        },
        "workerGate": {
            "state": worker_state,
            "completionRecorded": completion_recorded,
            "completionCount": completion_count,
            "completionCapacity": MAX_COMPLETION_MARKERS,
            "completionRemaining": completion_remaining,
            "killSwitchReason": kill_switch_reason,
            "authorization": "snapshot-only",
            "sideEffectRule": "atomically-recheck-holder-idempotency-fence-before-side-effect",
        },
        "providerCalls": "blocked",
        "accountData": "absent",
        "executionRoutes": "absent",
        "demoExecution": "blocked",
        "liveExecution": "blocked",
        "candidateIntent": "skip",
        "redaction": {
            "owner": "absent",
            "idempotency": "absent",
            "hashes": "absent",
            "fence": "absent",
            "path": "absent",
            "rawContent": "absent",
        },
    }


def _authorization_reason(
    state: dict[str, Any],
    holder_hash: str,
    idempotency_hash: str,
    fence: int,
    now: datetime,
) -> str:
    if state["killSwitch"]["engaged"]:
        return "kill-switch-blocked"
    lease = state["lease"]
    if lease is None:
        return "not-held"
    if now >= _parse_time(lease["expiresAt"]):
        return "expired"
    if not _same_identity(lease, holder_hash, idempotency_hash) or lease["fence"] != fence:
        return "stale-or-unauthorized"
    return "authorized"


def _acquire_result(status: str, *, fence: int | None = None, expires_at: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"status": status, "acquired": status in {"acquired", "held"}}
    if fence is not None:
        result["fence"] = fence
    if expires_at is not None:
        result["expiresAt"] = expires_at
    return result


def _same_identity(record: dict[str, Any], holder_hash: str, idempotency_hash: str) -> bool:
    return record["holderHash"] == holder_hash and record["idempotencyHash"] == idempotency_hash


def _validated_credentials(
    holder: str,
    idempotency_key: str,
    fence: int,
    now: datetime,
) -> tuple[str, str, int, datetime]:
    holder_hash = _opaque_hash("holder", holder)
    idempotency_hash = _opaque_hash("idempotency", idempotency_key)
    if not isinstance(fence, int) or isinstance(fence, bool) or fence < 1 or fence > MAX_COUNTER:
        raise WorkerLeaseStoreError("fence must be a positive bounded integer")
    return holder_hash, idempotency_hash, fence, _validated_now(now)


def _opaque_hash(kind: str, value: str) -> str:
    if not isinstance(value, str):
        raise WorkerLeaseStoreError(f"{kind} must be an opaque string")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise WorkerLeaseStoreError(f"{kind} must be valid UTF-8") from exc
    if not encoded or len(encoded) > MAX_OPAQUE_VALUE_BYTES:
        raise WorkerLeaseStoreError(f"{kind} must contain 1-{MAX_OPAQUE_VALUE_BYTES} UTF-8 bytes")
    return hashlib.sha256(_HASH_PREFIX + kind.encode("ascii") + b"\x00" + encoded).hexdigest()


def _validated_ttl(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= MAX_TTL_SECONDS:
        raise WorkerLeaseStoreError(f"ttl seconds must be an integer from 1 to {MAX_TTL_SECONDS}")
    return value


def _lease_expiry(now: datetime, ttl_seconds: int) -> datetime:
    try:
        return now + timedelta(seconds=ttl_seconds)
    except OverflowError as exc:
        raise WorkerLeaseStoreError("lease expiry exceeds the supported datetime range") from exc


def _validated_lock_wait(value: float) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value < 0
        or value > MAX_LOCK_WAIT_SECONDS
    ):
        raise WorkerLeaseStoreError(f"lock wait must be a finite number from 0 to {MAX_LOCK_WAIT_SECONDS}")
    return float(value)


def _validated_now(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise WorkerLeaseStoreError("now must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_time(value: Any) -> datetime:
    if not isinstance(value, str) or len(value) != 27 or not value.endswith("Z"):
        raise WorkerLeaseStoreError("stored timestamp must be canonical UTC microseconds")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise WorkerLeaseStoreError("stored timestamp is invalid") from exc
    if _format_time(parsed) != value:
        raise WorkerLeaseStoreError("stored timestamp must be canonical UTC microseconds")
    return parsed


def _reject_time_reversal(state: dict[str, Any], now: datetime) -> None:
    if now < _parse_time(state["lastMutationAt"]):
        raise WorkerLeaseStoreError("caller time precedes the last persisted mutation")


def _next_counter(value: int, label: str) -> int:
    if value >= MAX_COUNTER:
        raise WorkerLeaseStoreError(f"{label} is exhausted")
    return value + 1


def _validated_path(value: str | Path) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        raise WorkerLeaseStoreError("state path is required")
    try:
        path = Path(value)
    except (TypeError, ValueError) as exc:
        raise WorkerLeaseStoreError("state path is invalid") from exc
    if not os.fspath(path) or path.name in {"", ".", ".."}:
        raise WorkerLeaseStoreError("state path must name a file")
    return path


def _validate_parent_directory(path: Path) -> None:
    try:
        info = os.stat(path.parent, follow_symlinks=False)
    except OSError as exc:
        raise WorkerLeaseStoreError("state parent directory is unavailable") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise WorkerLeaseStoreError("state parent must be a directory")


@contextmanager
def _locked_file(lock_path: Path, wait_seconds: float, *, create: bool) -> Iterator[int]:
    if fcntl is None:
        raise OSError("simulation worker leases require POSIX fcntl locking")
    flags = os.O_RDWR | (os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0)
    created = False
    try:
        fd = os.open(lock_path, flags, 0o600)
    except FileNotFoundError as exc:
        if not create:
            raise WorkerLeaseStoreError("lease store is not initialized") from exc
        try:
            fd = os.open(lock_path, flags | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
        except FileExistsError:
            try:
                fd = os.open(lock_path, flags, 0o600)
            except OSError as race_exc:
                raise WorkerLeaseStoreError("lease lock cannot be opened safely") from race_exc
        except OSError as create_exc:
            raise WorkerLeaseStoreError("lease lock cannot be created safely") from create_exc
    except OSError as exc:
        raise WorkerLeaseStoreError("lease lock cannot be opened safely") from exc
    locked = False
    try:
        if created:
            os.fchmod(fd, 0o600)
        _validate_open_file(fd, "lease lock")
        _acquire_flock(fd, wait_seconds)
        locked = True
        _verify_open_identity(lock_path, fd, "lease lock")
        yield fd
    finally:
        try:
            if locked:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _acquire_flock(fd: int, wait_seconds: float) -> None:
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise TimeoutError("timed out waiting for lease lock")
            time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))


def _validate_open_file(fd: int, label: str) -> os.stat_result:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise WorkerLeaseStoreError(f"{label} must be a regular single-link file")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise WorkerLeaseStoreError(f"{label} must use mode 0600")
    return info


def _fd_identity(fd: int) -> tuple[int, int]:
    info = _validate_open_file(fd, "lease lock")
    return info.st_dev, info.st_ino


def _verify_open_identity(path: Path, fd: int, label: str) -> None:
    opened = _validate_open_file(fd, label)
    try:
        current = os.lstat(path)
    except OSError as exc:
        raise WorkerLeaseStoreError(f"{label} changed while open") from exc
    if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1:
        raise WorkerLeaseStoreError(f"{label} must be a regular single-link file")
    if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
        raise WorkerLeaseStoreError(f"{label} changed while open")


def _read_state(path: Path) -> tuple[dict[str, Any], tuple[int, int]]:
    try:
        preflight = os.lstat(path)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise WorkerLeaseStoreError("lease state cannot be inspected safely") from exc
    if not stat.S_ISREG(preflight.st_mode) or preflight.st_nlink != 1:
        raise WorkerLeaseStoreError("lease state must be a regular single-link file")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise WorkerLeaseStoreError("lease state cannot be opened safely") from exc
    try:
        info = _validate_open_file(fd, "lease state")
        if info.st_size <= 0 or info.st_size > MAX_STORE_BYTES:
            raise WorkerLeaseStoreError("lease state size is invalid")
        chunks: list[bytes] = []
        remaining = MAX_STORE_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(4096, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > MAX_STORE_BYTES:
            raise WorkerLeaseStoreError("lease state is oversized")
        _verify_open_identity(path, fd, "lease state")
    finally:
        os.close(fd)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkerLeaseStoreError("lease state is not UTF-8") from exc
    try:
        parsed = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, WorkerLeaseStoreError, RecursionError) as exc:
        raise WorkerLeaseStoreError("lease state is invalid JSON") from exc
    state = _validate_state(parsed)
    return state, (info.st_dev, info.st_ino)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WorkerLeaseStoreError("lease state contains duplicate JSON keys")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> Any:
    raise WorkerLeaseStoreError(f"lease state contains invalid number: {value}")


def _validate_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _STATE_KEYS:
        raise WorkerLeaseStoreError("lease state has an unsupported shape")
    if (
        not isinstance(value["version"], int)
        or isinstance(value["version"], bool)
        or value["version"] != STORE_VERSION
    ):
        raise WorkerLeaseStoreError("lease state version is unsupported")
    for field in ("revision", "fenceGeneration"):
        item = value[field]
        if not isinstance(item, int) or isinstance(item, bool) or not 0 <= item <= MAX_COUNTER:
            raise WorkerLeaseStoreError(f"lease state {field} is invalid")
    if value["revision"] < value["fenceGeneration"]:
        raise WorkerLeaseStoreError("lease state revision cannot trail its fence generation")
    last_mutation = _parse_time(value["lastMutationAt"])

    kill_switch = value["killSwitch"]
    if not isinstance(kill_switch, dict) or set(kill_switch) != _KILL_SWITCH_KEYS:
        raise WorkerLeaseStoreError("lease kill switch has an unsupported shape")
    if not isinstance(kill_switch["engaged"], bool):
        raise WorkerLeaseStoreError("lease kill switch flag is invalid")
    changed_at = kill_switch["changedAt"]
    kill_reason = kill_switch["reason"]
    if changed_at is not None and _parse_time(changed_at) > last_mutation:
        raise WorkerLeaseStoreError("lease kill switch timestamp is invalid")
    if kill_switch["engaged"]:
        if changed_at is None or kill_reason not in KILL_SWITCH_ENGAGE_REASONS:
            raise WorkerLeaseStoreError("engaged kill switch requires an allowlisted reason and timestamp")
    elif changed_at is None:
        if kill_reason is not None:
            raise WorkerLeaseStoreError("initial kill switch state cannot have a reason")
    elif kill_reason != KILL_SWITCH_REENABLE_REASON:
        raise WorkerLeaseStoreError("re-enabled kill switch reason is invalid")

    lease = value["lease"]
    if lease is not None:
        _validate_identity_record(lease, _LEASE_KEYS)
        acquired = _parse_time(lease["acquiredAt"])
        updated = _parse_time(lease["updatedAt"])
        expires = _parse_time(lease["expiresAt"])
        if not acquired <= updated == last_mutation or not updated < expires:
            raise WorkerLeaseStoreError("lease timestamps are inconsistent")
        if expires - updated > timedelta(seconds=MAX_TTL_SECONDS):
            raise WorkerLeaseStoreError("stored lease TTL exceeds the cap")
        if lease["fence"] != value["fenceGeneration"]:
            raise WorkerLeaseStoreError("active lease fence is not current")
    if kill_switch["engaged"] and lease is not None:
        raise WorkerLeaseStoreError("kill switch cannot retain an active lease")

    last_release = value["lastRelease"]
    if last_release is not None:
        _validate_identity_record(last_release, _RELEASE_KEYS)
        if _parse_time(last_release["releasedAt"]) > last_mutation:
            raise WorkerLeaseStoreError("release timestamp is invalid")
        if last_release["fence"] >= value["fenceGeneration"]:
            raise WorkerLeaseStoreError("release marker must be fenced")
        if lease is not None:
            raise WorkerLeaseStoreError("active lease cannot retain a release marker")

    completions = value["completions"]
    if not isinstance(completions, list) or len(completions) > MAX_COMPLETION_MARKERS:
        raise WorkerLeaseStoreError("completion marker list is invalid")
    seen_idempotency: set[str] = set()
    previous_fence = 0
    previous_completed_at: datetime | None = None
    for completion in completions:
        _validate_identity_record(completion, _COMPLETION_KEYS)
        if completion["idempotencyHash"] in seen_idempotency:
            raise WorkerLeaseStoreError("completion idempotency markers must be unique")
        seen_idempotency.add(completion["idempotencyHash"])
        completed_at = _parse_time(completion["completedAt"])
        if completed_at > last_mutation:
            raise WorkerLeaseStoreError("completion timestamp is invalid")
        if completion["fence"] >= value["fenceGeneration"]:
            raise WorkerLeaseStoreError("completion marker must be fenced")
        if completion["fence"] <= previous_fence or (
            previous_completed_at is not None and completed_at < previous_completed_at
        ):
            raise WorkerLeaseStoreError("completion markers are not in persisted order")
        previous_fence = completion["fence"]
        previous_completed_at = completed_at
    if lease is not None and lease["idempotencyHash"] in seen_idempotency:
        raise WorkerLeaseStoreError("active lease cannot repeat a completed idempotency marker")
    if lease is not None and len(completions) >= MAX_COMPLETION_MARKERS:
        raise WorkerLeaseStoreError("active lease cannot coexist with exhausted completion capacity")
    return value


def _validate_identity_record(value: Any, expected_keys: frozenset[str]) -> None:
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise WorkerLeaseStoreError("lease identity record has an unsupported shape")
    for field in ("holderHash", "idempotencyHash"):
        item = value[field]
        if (
            not isinstance(item, str)
            or len(item) != 64
            or any(character not in "0123456789abcdef" for character in item)
        ):
            raise WorkerLeaseStoreError("lease identity hash is invalid")
    fence = value["fence"]
    if not isinstance(fence, int) or isinstance(fence, bool) or not 1 <= fence <= MAX_COUNTER:
        raise WorkerLeaseStoreError("lease identity fence is invalid")


def _write_state(
    path: Path,
    state: dict[str, Any],
    lock_path: Path,
    *,
    lock_identity: tuple[int, int],
    expected_identity: tuple[int, int] | None = None,
    expect_missing: bool = False,
) -> None:
    validated = _validate_state(state)
    raw = (json.dumps(validated, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    if len(raw) > MAX_STORE_BYTES:
        raise WorkerLeaseStoreError("lease state exceeds the storage cap")
    if expected_identity is not None:
        _verify_path_identity(path, expected_identity, "lease state")
    elif expect_missing:
        _verify_path_missing(path, "lease state")
    _verify_path_identity(lock_path, lock_identity, "lease lock")
    _validate_parent_directory(path)

    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{secrets.token_hex(12)}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = -1
    try:
        fd = os.open(temporary, flags, 0o600)
        os.fchmod(fd, 0o600)
        _validate_open_file(fd, "lease temporary state")
        view = memoryview(raw)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError(errno.EIO, "short write")
            view = view[written:]
        os.fsync(fd)
        _verify_open_identity(temporary, fd, "lease temporary state")
        os.close(fd)
        fd = -1
        if expected_identity is not None:
            _verify_path_identity(path, expected_identity, "lease state")
        elif expect_missing:
            _verify_path_missing(path, "lease state")
        _verify_path_identity(lock_path, lock_identity, "lease lock")
        os.replace(temporary, path)
        directory_flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            directory_flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            directory_flags |= os.O_NOFOLLOW
        directory_fd = os.open(path.parent, directory_flags)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _verify_path_identity(path: Path, expected: tuple[int, int], label: str) -> None:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise WorkerLeaseStoreError(f"{label} changed before commit") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise WorkerLeaseStoreError(f"{label} must be a regular single-link file")
    if (info.st_dev, info.st_ino) != expected:
        raise WorkerLeaseStoreError(f"{label} changed before commit")


def _verify_path_missing(path: Path, label: str) -> None:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise WorkerLeaseStoreError(f"{label} cannot be inspected before commit") from exc
    raise WorkerLeaseStoreError(f"{label} appeared before commit")
