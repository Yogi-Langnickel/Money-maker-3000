from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import money_maker_3000.worker_leases as worker_leases_module

from money_maker_3000.worker_leases import (
    MAX_STORE_BYTES,
    MAX_TTL_SECONDS,
    WorkerLeaseStore as ProductionWorkerLeaseStore,
    WorkerLeaseStoreError,
    build_worker_lease_report as production_build_worker_lease_report,
)


NOW = datetime(2026, 7, 15, 0, 0, 0, tzinfo=timezone.utc)


class _ManualClock:
    def __init__(self, current: datetime = NOW) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current


class WorkerLeaseStore(ProductionWorkerLeaseStore):
    """Test adapter that drives the production store-owned fake clock."""

    def __init__(self, state_path, *, lock_wait_seconds=1.0, clock=None) -> None:
        self.test_clock = clock or _ManualClock()
        self.test_epoch: str | None = None
        super().__init__(state_path, lock_wait_seconds=lock_wait_seconds, clock=self.test_clock)

    def _at(self, now: datetime) -> None:
        self.test_clock.current = now

    def initialize(self, *, now: datetime = NOW):
        self._at(now)
        result = super().initialize()
        self.test_epoch = result["epoch"]
        return result

    def acquire(self, *, holder, idempotency_key, ttl_seconds, now: datetime = NOW):
        self._at(now)
        result = super().acquire(holder=holder, idempotency_key=idempotency_key, ttl_seconds=ttl_seconds)
        if "epoch" in result:
            self.test_epoch = result["epoch"]
        return result

    def authorize(self, *, holder, idempotency_key, fence, epoch=None, now: datetime = NOW):
        self._at(now)
        return super().authorize(
            holder=holder,
            idempotency_key=idempotency_key,
            epoch=epoch or self.test_epoch,
            fence=fence,
        )

    def renew(self, *, holder, idempotency_key, fence, ttl_seconds, epoch=None, now: datetime = NOW):
        self._at(now)
        return super().renew(
            holder=holder,
            idempotency_key=idempotency_key,
            epoch=epoch or self.test_epoch,
            fence=fence,
            ttl_seconds=ttl_seconds,
        )

    def release(self, *, holder, idempotency_key, fence, epoch=None, now: datetime = NOW):
        self._at(now)
        return super().release(
            holder=holder,
            idempotency_key=idempotency_key,
            epoch=epoch or self.test_epoch,
            fence=fence,
        )

    def complete(self, *, holder, idempotency_key, fence, epoch=None, now: datetime = NOW):
        self._at(now)
        return super().complete(
            holder=holder,
            idempotency_key=idempotency_key,
            epoch=epoch or self.test_epoch,
            fence=fence,
        )

    def engage_kill_switch(self, *, reason="operator-stop", now: datetime = NOW):
        self._at(now)
        return super().engage_kill_switch(reason=reason)

    def reenable(self, *, now: datetime = NOW):
        self._at(now)
        return super().reenable()

    def report(self, *, now: datetime = NOW):
        return super().report(observed_at=now)


def build_worker_lease_report(state_path, *, now: datetime, lock_wait_seconds: float = 1.0):
    return production_build_worker_lease_report(
        state_path,
        observed_at=now,
        lock_wait_seconds=lock_wait_seconds,
    )


def _acquire_worker(path: str, holder: str, key: str, now: datetime, queue: multiprocessing.Queue) -> None:
    try:
        result = WorkerLeaseStore(path, lock_wait_seconds=5).acquire(
            holder=holder,
            idempotency_key=key,
            ttl_seconds=60,
            now=now,
        )
        queue.put(("ok", result))
    except Exception as exc:  # pragma: no cover - asserted in the parent process.
        queue.put(("error", type(exc).__name__, str(exc)))


def _kill_worker(path: str, now: datetime, queue: multiprocessing.Queue) -> None:
    try:
        queue.put(("ok", WorkerLeaseStore(path, lock_wait_seconds=5).engage_kill_switch(now=now)))
    except Exception as exc:  # pragma: no cover - asserted in the parent process.
        queue.put(("error", type(exc).__name__, str(exc)))


def _initialize_worker(path: str, now: datetime, start, queue: multiprocessing.Queue) -> None:
    try:
        start.wait()
        result = ProductionWorkerLeaseStore(path, lock_wait_seconds=5, clock=lambda: now).initialize()
        queue.put(("ok", result))
    except Exception as exc:  # pragma: no cover - asserted in the parent process.
        queue.put(("error", type(exc).__name__, str(exc)))


def _barrier_acquire_worker(
    path: str,
    holder: str,
    key: str,
    now: datetime,
    start,
    queue: multiprocessing.Queue,
) -> None:
    try:
        start.wait()
        result = ProductionWorkerLeaseStore(path, lock_wait_seconds=5, clock=lambda: now).acquire(
            holder=holder,
            idempotency_key=key,
            ttl_seconds=60,
        )
        queue.put(("ok", holder, result))
    except Exception as exc:  # pragma: no cover - asserted in the parent process.
        queue.put(("error", type(exc).__name__, str(exc)))


def _barrier_kill_worker(path: str, now: datetime, start, queue: multiprocessing.Queue) -> None:
    try:
        start.wait()
        result = ProductionWorkerLeaseStore(path, lock_wait_seconds=5, clock=lambda: now).engage_kill_switch(
            reason="risk-stop"
        )
        queue.put(("ok", "kill", result))
    except Exception as exc:  # pragma: no cover - asserted in the parent process.
        queue.put(("error", type(exc).__name__, str(exc)))


def _paused_lock_replacement_worker(path: str, entered, proceed, queue: multiprocessing.Queue) -> None:
    original = worker_leases_module._read_state
    paused = False

    def pause_after_locks(state_path, *, directory_fd):
        nonlocal paused
        if not paused:
            paused = True
            entered.set()
            if not proceed.wait(timeout=10):
                raise TimeoutError("test barrier timed out")
        return original(state_path, directory_fd=directory_fd)

    try:
        with patch("money_maker_3000.worker_leases._read_state", side_effect=pause_after_locks):
            result = ProductionWorkerLeaseStore(path, lock_wait_seconds=5, clock=lambda: NOW).acquire(
                holder="writer-a",
                idempotency_key="job-a",
                ttl_seconds=60,
            )
        queue.put(("ok", result))
    except Exception as exc:  # pragma: no cover - asserted in the parent process.
        queue.put(("error", type(exc).__name__, str(exc)))


def _credential_operation_worker(
    path: str,
    operation: str,
    holder: str,
    key: str,
    epoch: str,
    fence: int,
    now: datetime,
    start,
    queue: multiprocessing.Queue,
) -> None:
    store = ProductionWorkerLeaseStore(path, lock_wait_seconds=5, clock=lambda: now)
    try:
        start.wait()
        if operation == "renew":
            result = store.renew(
                holder=holder,
                idempotency_key=key,
                epoch=epoch,
                fence=fence,
                ttl_seconds=60,
            )
        elif operation == "release":
            result = store.release(holder=holder, idempotency_key=key, epoch=epoch, fence=fence)
        elif operation == "complete":
            result = store.complete(holder=holder, idempotency_key=key, epoch=epoch, fence=fence)
        elif operation == "kill":
            result = store.engage_kill_switch(reason="risk-stop")
        else:  # pragma: no cover - test helper contract.
            raise AssertionError(f"unknown operation: {operation}")
        queue.put(("ok", operation, result))
    except Exception as exc:  # pragma: no cover - asserted in the parent process.
        queue.put(("error", operation, type(exc).__name__, str(exc)))


def _delayed_clock_operation_worker(
    path: str,
    operation: str,
    epoch: str | None,
    fence: int | None,
    clock_seconds,
    attempting,
    clock_called,
    queue: multiprocessing.Queue,
) -> None:
    original_acquire_flock = worker_leases_module._acquire_flock

    def instrumented_acquire_flock(fd: int, wait_seconds: float, *, label: str) -> None:
        if label == "lease directory":
            attempting.set()
        original_acquire_flock(fd, wait_seconds, label=label)

    def clock() -> datetime:
        clock_called.set()
        return datetime.fromtimestamp(clock_seconds.value, tz=timezone.utc)

    try:
        with patch(
            "money_maker_3000.worker_leases._acquire_flock",
            side_effect=instrumented_acquire_flock,
        ):
            store = ProductionWorkerLeaseStore(path, lock_wait_seconds=5, clock=clock)
            if operation == "initialize":
                result = store.initialize()
            elif operation == "acquire":
                result = store.acquire(holder="worker", idempotency_key="job", ttl_seconds=60)
            elif operation == "authorize":
                result = store.authorize(
                    holder="worker",
                    idempotency_key="job",
                    epoch=epoch,
                    fence=fence,
                )
            elif operation == "renew":
                result = store.renew(
                    holder="worker",
                    idempotency_key="job",
                    epoch=epoch,
                    fence=fence,
                    ttl_seconds=30,
                )
            elif operation == "release":
                result = store.release(
                    holder="worker",
                    idempotency_key="job",
                    epoch=epoch,
                    fence=fence,
                )
            elif operation == "complete":
                result = store.complete(
                    holder="worker",
                    idempotency_key="job",
                    epoch=epoch,
                    fence=fence,
                )
            elif operation == "kill":
                result = store.engage_kill_switch(reason="risk-stop")
            else:  # pragma: no cover - helper contract.
                raise AssertionError(f"unknown operation: {operation}")
        queue.put(("ok", result))
    except Exception as exc:  # pragma: no cover - asserted in the parent process.
        queue.put(("error", type(exc).__name__, str(exc)))


def _crash_with_lock(lock_path: str, ready) -> None:
    import fcntl

    fd = os.open(lock_path, os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    ready.set()
    os._exit(23)


class WorkerLeaseStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "worker-leases.json"
        self.store = WorkerLeaseStore(self.path)

    def initialize(self) -> None:
        self.assertEqual(self.store.initialize(now=NOW)["status"], "initialized")

    def acquire(self, *, holder: str = "worker-a", key: str = "job-a", now: datetime = NOW, ttl: int = 60):
        return self.store.acquire(holder=holder, idempotency_key=key, ttl_seconds=ttl, now=now)

    def test_initialize_is_explicit_and_idempotent_without_silent_missing_reset(self):
        with self.assertRaisesRegex(WorkerLeaseStoreError, "lock is missing"):
            self.acquire()
        self.assertFalse(self.path.exists())
        self.assertFalse(self.store.lock_path.exists())

        self.initialize()
        original = self.path.read_bytes()
        self.assertEqual(self.store.initialize(now=NOW + timedelta(seconds=1))["status"], "already-initialized")
        self.assertEqual(self.path.read_bytes(), original)

        self.path.unlink()
        with self.assertRaisesRegex(WorkerLeaseStoreError, "deleted"):
            self.acquire(now=NOW + timedelta(seconds=2))
        with self.assertRaisesRegex(WorkerLeaseStoreError, "deleted"):
            self.store.initialize(now=NOW + timedelta(seconds=2))
        self.assertFalse(self.path.exists())

    def test_epoch_prevents_deleted_state_and_full_store_recreation_aba(self):
        self.initialize()
        first = self.acquire()
        old_epoch = first["epoch"]
        old_fence = first["fence"]
        lock_anchor = self.store.lock_path.read_bytes()

        self.path.unlink()
        with self.assertRaisesRegex(WorkerLeaseStoreError, "deleted"):
            self.store.initialize(now=NOW + timedelta(seconds=1))
        self.assertFalse(self.path.exists())
        self.assertEqual(self.store.lock_path.read_bytes(), lock_anchor)

        self.store.lock_path.unlink()
        recreated = self.store.initialize(now=NOW + timedelta(seconds=2))
        self.assertNotEqual(recreated["epoch"], old_epoch)
        replacement = self.acquire(now=NOW + timedelta(seconds=2))
        self.assertEqual(replacement["fence"], 1)
        self.assertNotEqual(replacement["epoch"], old_epoch)
        with self.assertRaisesRegex(WorkerLeaseStoreError, "epoch"):
            self.store.authorize(
                holder="worker-a",
                idempotency_key="job-a",
                epoch=old_epoch,
                fence=old_fence,
                now=NOW + timedelta(seconds=2),
            )

    def test_state_deletion_cannot_erase_kill_switch_or_completion_under_surviving_lock(self):
        self.initialize()
        lease = self.acquire()
        self.store.complete(
            holder="worker-a",
            idempotency_key="job-a",
            epoch=lease["epoch"],
            fence=lease["fence"],
            now=NOW + timedelta(seconds=1),
        )
        self.store.engage_kill_switch(reason="risk-stop", now=NOW + timedelta(seconds=2))
        anchor = self.store.lock_path.read_bytes()
        self.path.unlink()
        for action in (
            lambda: self.store.initialize(now=NOW + timedelta(seconds=3)),
            lambda: self.acquire(holder="worker-b", key="job-b", now=NOW + timedelta(seconds=3)),
        ):
            with self.assertRaisesRegex(WorkerLeaseStoreError, "deleted"):
                action()
        self.assertFalse(self.path.exists())
        self.assertEqual(self.store.lock_path.read_bytes(), anchor)

    @unittest.skipUnless("spawn" in multiprocessing.get_all_start_methods(), "requires spawn multiprocessing")
    def test_concurrent_initializers_share_one_epoch_without_reset(self):
        context = multiprocessing.get_context("spawn")
        start = context.Event()
        queue = context.Queue()
        processes = [
            context.Process(target=_initialize_worker, args=(os.fspath(self.path), NOW, start, queue))
            for _ in range(6)
        ]
        for process in processes:
            process.start()
        start.set()
        for process in processes:
            process.join(timeout=15)
        self.assertTrue(all(process.exitcode == 0 for process in processes))
        results = [queue.get(timeout=2) for _ in processes]
        self.assertTrue(all(result[0] == "ok" for result in results), results)
        statuses = [result[1]["status"] for result in results]
        epochs = {result[1]["epoch"] for result in results}
        self.assertEqual(statuses.count("initialized"), 1)
        self.assertEqual(statuses.count("already-initialized"), 5)
        self.assertEqual(len(epochs), 1)
        state = json.loads(self.path.read_text(encoding="utf-8"))
        anchor = json.loads(self.store.lock_path.read_text(encoding="utf-8"))
        self.assertEqual(state["epoch"], anchor["epoch"])
        self.assertEqual(state["epoch"], next(iter(epochs)))

    def test_acquire_busy_same_holder_retry_and_exact_expiry_takeover(self):
        self.initialize()
        acquired = self.acquire(ttl=10)
        persisted = self.path.read_bytes()
        retry = self.acquire(ttl=1, now=NOW + timedelta(seconds=9))
        self.assertEqual(retry, {**acquired, "status": "held"})
        self.assertEqual(self.path.read_bytes(), persisted)

        busy = self.acquire(holder="worker-b", key="job-b", now=NOW + timedelta(seconds=9))
        self.assertEqual(busy, {"status": "busy", "acquired": False})
        self.assertEqual(self.path.read_bytes(), persisted)

        takeover = self.acquire(holder="worker-b", key="job-b", now=NOW + timedelta(seconds=10))
        self.assertEqual(takeover["status"], "acquired")
        self.assertGreater(takeover["fence"], acquired["fence"])
        stale = self.store.authorize(
            holder="worker-a", idempotency_key="job-a", fence=acquired["fence"], now=NOW + timedelta(seconds=10)
        )
        self.assertFalse(stale["authorized"])
        self.assertEqual(stale["reason"], "stale-or-unauthorized")

    def test_authorize_renew_release_require_exact_unexpired_credentials(self):
        self.initialize()
        lease = self.acquire(ttl=20)
        fence = lease["fence"]
        authorized = self.store.authorize(holder="worker-a", idempotency_key="job-a", fence=fence, now=NOW)
        self.assertTrue(authorized["authorized"])
        self.assertTrue(authorized["snapshotOnly"])
        for holder, key, candidate_fence in (
            ("worker-x", "job-a", fence),
            ("worker-a", "job-x", fence),
            ("worker-a", "job-a", fence + 1),
        ):
            denied = self.store.renew(
                holder=holder,
                idempotency_key=key,
                fence=candidate_fence,
                ttl_seconds=30,
                now=NOW + timedelta(seconds=1),
            )
            self.assertFalse(denied["renewed"])

        renewed = self.store.renew(
            holder="worker-a",
            idempotency_key="job-a",
            fence=fence,
            ttl_seconds=30,
            now=NOW + timedelta(seconds=10),
        )
        self.assertEqual(renewed["expiresAt"], "2026-07-15T00:00:40.000000Z")
        expired = self.store.release(
            holder="worker-a",
            idempotency_key="job-a",
            fence=fence,
            now=NOW + timedelta(seconds=40),
        )
        self.assertEqual(expired, {"status": "expired", "released": False})

        release = self.store.release(
            holder="worker-a",
            idempotency_key="job-a",
            fence=fence,
            now=NOW + timedelta(seconds=39),
        )
        self.assertTrue(release["released"])
        release_bytes = self.path.read_bytes()
        replay = self.store.release(
            holder="worker-a",
            idempotency_key="job-a",
            fence=fence,
            now=NOW + timedelta(seconds=39),
        )
        self.assertEqual(replay, {"status": "already-released", "released": True})
        self.assertEqual(self.path.read_bytes(), release_bytes)
        retried = self.acquire(now=NOW + timedelta(seconds=39))
        self.assertGreater(retried["fence"], fence)
        stale_release = self.store.release(
            holder="worker-a",
            idempotency_key="job-a",
            fence=fence,
            now=NOW + timedelta(seconds=39),
        )
        self.assertEqual(stale_release, {"status": "stale-or-unauthorized", "released": False})

    def test_completion_is_atomic_bounded_and_exact_replay_is_byte_stable(self):
        self.initialize()
        lease = self.acquire()
        completed = self.store.complete(
            holder="worker-a", idempotency_key="job-a", fence=lease["fence"], now=NOW + timedelta(seconds=1)
        )
        self.assertEqual(completed, {"status": "completed", "completed": True})
        stored = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIsNone(stored["lease"])
        self.assertEqual(len(stored["completions"]), 1)
        self.assertEqual(
            set(stored["completions"][0]),
            {"holderHash", "idempotencyHash", "fence", "completedAt"},
        )
        serialized = json.dumps(stored)
        self.assertNotIn("worker-a", serialized)
        self.assertNotIn("job-a", serialized)

        original = self.path.read_bytes()
        replay = self.store.complete(
            holder="worker-a", idempotency_key="job-a", fence=lease["fence"], now=NOW + timedelta(seconds=2)
        )
        self.assertEqual(replay, {"status": "already-completed", "completed": True})
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.acquire(now=NOW + timedelta(seconds=3))["status"], "already-completed")

    def test_completed_idempotency_is_global_and_durable_across_later_completions(self):
        self.initialize()
        first = self.acquire(holder="worker-a", key="job-a")
        self.store.complete(
            holder="worker-a", idempotency_key="job-a", fence=first["fence"], now=NOW + timedelta(seconds=1)
        )
        second = self.acquire(holder="worker-b", key="job-b", now=NOW + timedelta(seconds=2))
        self.store.complete(
            holder="worker-b", idempotency_key="job-b", fence=second["fence"], now=NOW + timedelta(seconds=3)
        )

        self.assertEqual(
            self.acquire(holder="worker-a", key="job-a", now=NOW + timedelta(seconds=4))["status"],
            "already-completed",
        )
        self.assertEqual(
            self.acquire(holder="different-worker", key="job-a", now=NOW + timedelta(seconds=4))["status"],
            "already-completed",
        )
        state = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(len(state["completions"]), 2)

        state["lease"] = {
            "holderHash": state["completions"][0]["holderHash"],
            "idempotencyHash": state["completions"][0]["idempotencyHash"],
            "fence": state["fenceGeneration"],
            "acquiredAt": state["lastMutationAt"],
            "updatedAt": state["lastMutationAt"],
            "expiresAt": "2026-07-15T00:01:04.000000Z",
        }
        self.path.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaisesRegex(WorkerLeaseStoreError, "completed idempotency"):
            self.store.authorize(
                holder="worker-a",
                idempotency_key="job-a",
                fence=state["fenceGeneration"],
                now=NOW + timedelta(seconds=4),
            )

    def test_exact_old_completion_replay_bypasses_only_clock_reversal(self):
        self.initialize()
        first = self.acquire(holder="worker-a", key="job-a", now=NOW)
        self.store.complete(
            holder="worker-a",
            idempotency_key="job-a",
            fence=first["fence"],
            now=NOW + timedelta(seconds=1),
        )
        second = self.acquire(holder="worker-b", key="job-b", now=NOW + timedelta(seconds=2))
        self.store.complete(
            holder="worker-b",
            idempotency_key="job-b",
            fence=second["fence"],
            now=NOW + timedelta(seconds=3),
        )
        persisted = self.path.read_bytes()
        replay = self.store.complete(
            holder="worker-a",
            idempotency_key="job-a",
            epoch=first["epoch"],
            fence=first["fence"],
            now=NOW + timedelta(seconds=1),
        )
        self.assertEqual(replay, {"status": "already-completed", "completed": True})
        self.assertEqual(self.path.read_bytes(), persisted)
        with self.assertRaisesRegex(WorkerLeaseStoreError, "precedes"):
            self.store.complete(
                holder="worker-x",
                idempotency_key="job-a",
                epoch=first["epoch"],
                fence=first["fence"],
                now=NOW + timedelta(seconds=1),
            )

    def test_completion_capacity_fails_closed_without_evicting_markers(self):
        with patch("money_maker_3000.worker_leases.MAX_COMPLETION_MARKERS", 2):
            self.initialize()
            now = NOW
            for index in range(2):
                lease = self.acquire(holder=f"worker-{index}", key=f"job-{index}", now=now)
                now += timedelta(seconds=1)
                result = self.store.complete(
                    holder=f"worker-{index}",
                    idempotency_key=f"job-{index}",
                    fence=lease["fence"],
                    now=now,
                )
                self.assertTrue(result["completed"])
                now += timedelta(seconds=1)

            before = self.path.read_bytes()
            refused = self.acquire(holder="worker-extra", key="job-extra", now=now)
            self.assertEqual(refused, {"status": "completion-capacity-exhausted", "acquired": False})
            self.assertEqual(self.path.read_bytes(), before)
            state = json.loads(before)
            self.assertEqual(len(state["completions"]), 2)
            self.assertIsNone(state["lease"])
            self.assertEqual(
                self.acquire(holder="other", key="job-0", now=now)["status"],
                "already-completed",
            )
            report = self.store.report(now=now)
            self.assertEqual(report["workerGate"]["state"], "completion-capacity-blocked")
            self.assertEqual(report["workerGate"]["completionCount"], 2)
            self.assertEqual(report["workerGate"]["completionCapacity"], 2)
            self.assertEqual(report["workerGate"]["completionRemaining"], 0)

    def test_stale_same_owner_aba_replay_fails(self):
        self.initialize()
        first = self.acquire()
        self.store.release(
            holder="worker-a", idempotency_key="job-a", fence=first["fence"], now=NOW + timedelta(seconds=1)
        )
        second = self.acquire(now=NOW + timedelta(seconds=1))
        self.assertGreater(second["fence"], first["fence"])
        stale = self.store.complete(
            holder="worker-a", idempotency_key="job-a", fence=first["fence"], now=NOW + timedelta(seconds=2)
        )
        self.assertEqual(stale, {"status": "stale-or-unauthorized", "completed": False})
        self.assertTrue(
            self.store.authorize(
                holder="worker-a", idempotency_key="job-a", fence=second["fence"], now=NOW + timedelta(seconds=2)
            )["authorized"]
        )

    def test_kill_switch_revokes_fences_and_reenable_never_resurrects(self):
        self.initialize()
        lease = self.acquire()
        engaged = self.store.engage_kill_switch(now=NOW + timedelta(seconds=1))
        self.assertEqual(
            engaged,
            {"status": "engaged", "killSwitchEngaged": True, "reason": "operator-stop"},
        )
        original = self.path.read_bytes()
        self.assertEqual(
            self.store.engage_kill_switch(now=NOW + timedelta(seconds=2)),
            {"status": "already-engaged", "killSwitchEngaged": True, "reason": "operator-stop"},
        )
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.acquire(now=NOW + timedelta(seconds=2))["status"], "kill-switch-blocked")
        self.assertEqual(
            self.store.renew(
                holder="worker-a",
                idempotency_key="job-a",
                fence=lease["fence"],
                ttl_seconds=60,
                now=NOW + timedelta(seconds=2),
            )["status"],
            "kill-switch-blocked",
        )

        before = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(self.store.reenable(now=NOW + timedelta(seconds=3))["status"], "re-enabled")
        after = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIsNone(after["lease"])
        self.assertGreater(after["revision"], before["revision"])
        self.assertGreater(after["fenceGeneration"], before["fenceGeneration"])
        stale = self.store.authorize(
            holder="worker-a", idempotency_key="job-a", fence=lease["fence"], now=NOW + timedelta(seconds=3)
        )
        self.assertEqual(stale["reason"], "not-held")

    def test_kill_switch_reasons_are_allowlisted_and_persisted(self):
        self.initialize()
        original = self.path.read_bytes()
        with self.assertRaisesRegex(WorkerLeaseStoreError, "allowlisted"):
            self.store.engage_kill_switch(now=NOW, reason="free-form-secret")
        self.assertEqual(self.path.read_bytes(), original)
        engaged = self.store.engage_kill_switch(now=NOW, reason="maintenance")
        self.assertEqual(engaged["reason"], "maintenance")
        state = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(state["killSwitch"]["reason"], "maintenance")
        enabled = self.store.reenable(now=NOW + timedelta(seconds=1))
        self.assertEqual(enabled["reason"], "operator-reenable")

    def test_time_reversal_and_input_caps_fail_without_state_mutation(self):
        self.initialize()
        self.acquire(now=NOW + timedelta(seconds=10))
        original = self.path.read_bytes()
        invalid_calls = (
            lambda: self.acquire(now=NOW + timedelta(seconds=9)),
            lambda: self.store.engage_kill_switch(now=NOW + timedelta(seconds=9)),
            lambda: self.acquire(ttl=0, now=NOW + timedelta(seconds=10)),
            lambda: self.acquire(ttl=True, now=NOW + timedelta(seconds=10)),
            lambda: self.acquire(ttl=MAX_TTL_SECONDS + 1, now=NOW + timedelta(seconds=10)),
            lambda: self.store.authorize(
                holder="worker-a", idempotency_key="job-a", fence=True, now=NOW + timedelta(seconds=10)
            ),
            lambda: self.store.acquire(
                holder="x" * (513), idempotency_key="job", ttl_seconds=1, now=NOW + timedelta(seconds=10)
            ),
        )
        for invalid_call in invalid_calls:
            with self.assertRaises(WorkerLeaseStoreError):
                invalid_call()
            self.assertEqual(self.path.read_bytes(), original)
        with self.assertRaisesRegex(WorkerLeaseStoreError, "lock wait must"):
            WorkerLeaseStore(self.path, lock_wait_seconds=float("nan"))

        precise_path = Path(self.temporary.name) / "precise-time.json"
        precise = WorkerLeaseStore(precise_path)
        precise.initialize(now=NOW + timedelta(microseconds=900))
        with self.assertRaisesRegex(WorkerLeaseStoreError, "precedes"):
            precise.acquire(
                holder="worker",
                idempotency_key="job",
                ttl_seconds=1,
                now=NOW + timedelta(microseconds=500),
            )

    def test_mutation_time_is_store_owned_and_cannot_be_poisoned_by_caller(self):
        clock = _ManualClock(NOW)
        store = ProductionWorkerLeaseStore(self.path, clock=clock)
        store.initialize()
        before = self.path.read_bytes()
        with self.assertRaises(TypeError):
            store.acquire(
                holder="worker",
                idempotency_key="job",
                ttl_seconds=60,
                now=NOW + timedelta(days=3650),
            )
        self.assertEqual(self.path.read_bytes(), before)
        acquired = store.acquire(holder="worker", idempotency_key="job", ttl_seconds=60)
        self.assertEqual(acquired["expiresAt"], "2026-07-15T00:01:00.000000Z")

    def test_strict_loaded_state_scalar_nested_and_ttl_invariants(self):
        self.initialize()
        valid = json.loads(self.path.read_text(encoding="utf-8"))
        invalid_states = []
        for field, value in (("version", 1.0), ("revision", True), ("fenceGeneration", -1)):
            candidate = json.loads(json.dumps(valid))
            candidate[field] = value
            invalid_states.append(json.dumps(candidate).encode())
        nested_unknown = json.loads(json.dumps(valid))
        nested_unknown["killSwitch"]["unknown"] = False
        invalid_states.append(json.dumps(nested_unknown).encode())
        duplicate_nested = json.dumps(valid, sort_keys=True, separators=(",", ":")).replace(
            '"engaged":false', '"engaged":false,"engaged":false'
        )
        invalid_states.append(duplicate_nested.encode())

        lease = self.acquire()
        active = json.loads(self.path.read_text(encoding="utf-8"))
        active["lease"]["expiresAt"] = "2026-07-16T00:00:01.000000Z"
        invalid_states.append(json.dumps(active).encode())
        self.assertGreater(lease["fence"], 0)

        for index, raw in enumerate(invalid_states):
            with self.subTest(index=index):
                self.path.write_bytes(raw)
                original = self.path.read_bytes()
                with self.assertRaises(WorkerLeaseStoreError):
                    self.store.acquire(holder="worker", idempotency_key="job", ttl_seconds=1, now=NOW)
                self.assertEqual(self.path.read_bytes(), original)

    def test_initialize_does_not_create_lock_to_repair_existing_invalid_state(self):
        path = Path(self.temporary.name) / "invalid-before-initialize.json"
        path.write_text("{bad-json}\n", encoding="utf-8")
        os.chmod(path, 0o600)
        store = WorkerLeaseStore(path)
        with self.assertRaisesRegex(WorkerLeaseStoreError, "lock is missing"):
            store.initialize(now=NOW)
        self.assertFalse(store.lock_path.exists())
        self.assertEqual(path.read_text(encoding="utf-8"), "{bad-json}\n")

    def test_corrupt_duplicate_unknown_invalid_numbers_and_oversize_fail_closed(self):
        cases = (
            b"{bad-json}\n",
            b'{"version":1,"version":1}\n',
            json.dumps({"version": 1, "unknown": True}).encode(),
            b'{"version":NaN}\n',
            b'{"version":' + (b"9" * 5000) + b"}\n",
            b"\xff\xfe\n",
            (b"[" * 2000) + (b"]" * 2000),
            b"x" * (MAX_STORE_BYTES + 1),
        )
        for index, content in enumerate(cases):
            with self.subTest(index=index):
                path = Path(self.temporary.name) / f"corrupt-{index}.json"
                store = WorkerLeaseStore(path)
                store.initialize(now=NOW)
                path.write_bytes(content)
                original = path.read_bytes()
                with self.assertRaises((WorkerLeaseStoreError, FileNotFoundError)):
                    store.acquire(holder="worker", idempotency_key="job", ttl_seconds=10, now=NOW)
                self.assertEqual(path.read_bytes(), original)
                report = store.report(now=NOW)
                self.assertEqual(report["integrity"]["state"], "corrupted")
                self.assertEqual(report["integrity"]["issueCode"], "state-invalid")
                self.assertEqual(path.read_bytes(), original)

    def test_state_and_lock_reject_symlink_fifo_and_hardlink_without_mutation(self):
        self.initialize()
        original = self.path.read_bytes()

        state_link = Path(self.temporary.name) / "state-link.json"
        state_link.symlink_to(self.path)
        linked_store = WorkerLeaseStore(state_link)
        linked_lock = linked_store.lock_path
        linked_lock.write_bytes(self.store.lock_path.read_bytes())
        os.chmod(linked_lock, 0o600)
        with self.assertRaisesRegex(WorkerLeaseStoreError, "lease state must"):
            linked_store.acquire(holder="w", idempotency_key="k", ttl_seconds=1, now=NOW)

        hardlink = Path(self.temporary.name) / "state-hardlink.json"
        os.link(self.path, hardlink)
        hard_store = WorkerLeaseStore(hardlink)
        hard_store.lock_path.write_bytes(self.store.lock_path.read_bytes())
        os.chmod(hard_store.lock_path, 0o600)
        with self.assertRaisesRegex(WorkerLeaseStoreError, "lease state must"):
            hard_store.acquire(holder="w", idempotency_key="k", ttl_seconds=1, now=NOW)
        self.assertEqual(self.path.read_bytes(), original)

        if hasattr(os, "mkfifo"):
            fifo = Path(self.temporary.name) / "state-fifo"
            os.mkfifo(fifo)
            fifo_store = WorkerLeaseStore(fifo)
            fifo_store.lock_path.write_bytes(self.store.lock_path.read_bytes())
            os.chmod(fifo_store.lock_path, 0o600)
            with self.assertRaisesRegex(WorkerLeaseStoreError, "lease state must"):
                fifo_store.acquire(holder="w", idempotency_key="k", ttl_seconds=1, now=NOW)

        separate = Path(self.temporary.name) / "separate.json"
        separate_store = WorkerLeaseStore(separate)
        separate_store.initialize(now=NOW)
        separate_store.lock_path.unlink()
        separate_store.lock_path.symlink_to(self.store.lock_path)
        before = separate.read_bytes()
        with self.assertRaises(WorkerLeaseStoreError):
            separate_store.acquire(holder="w", idempotency_key="k", ttl_seconds=1, now=NOW)
        self.assertEqual(separate.read_bytes(), before)

        lock_alias = Path(self.temporary.name) / "lock-alias"
        separate_store.lock_path.unlink()
        os.link(self.store.lock_path, separate_store.lock_path)
        os.link(separate_store.lock_path, lock_alias)
        with self.assertRaises(WorkerLeaseStoreError):
            separate_store.acquire(holder="w", idempotency_key="k", ttl_seconds=1, now=NOW)

        if hasattr(os, "mkfifo"):
            fifo_lock_store = WorkerLeaseStore(Path(self.temporary.name) / "fifo-lock-state.json")
            fifo_lock_store.path.write_bytes(original)
            os.chmod(fifo_lock_store.path, 0o600)
            os.mkfifo(fifo_lock_store.lock_path, 0o600)
            with self.assertRaises(WorkerLeaseStoreError):
                fifo_lock_store.acquire(holder="w", idempotency_key="k", ttl_seconds=1, now=NOW)

    def test_missing_report_is_canonical_redacted_and_creates_nothing(self):
        report = build_worker_lease_report(self.path, now=NOW)
        self.assertEqual(report["integrity"]["state"], "uninitialized")
        self.assertFalse(report["initialized"])
        self.assertFalse(self.path.exists())
        self.assertFalse(self.store.lock_path.exists())
        serialized = json.dumps(report).lower()
        for secret in ("worker-a", "job-a", os.fspath(self.path)):
            self.assertNotIn(secret.lower(), serialized)
        self.assertEqual(report["providerCalls"], "blocked")
        self.assertEqual(report["accountData"], "absent")
        self.assertEqual(report["candidateIntent"], "skip")

    def test_existing_reports_redact_identity_fence_path_and_content(self):
        self.initialize()
        acquired = self.acquire(holder="private-owner", key="private-operation")
        report = self.store.report(now=NOW)
        serialized = json.dumps(report, sort_keys=True)
        self.assertEqual(report["workerGate"]["state"], "busy")
        for secret in (
            "private-owner",
            "private-operation",
            acquired["epoch"],
            os.fspath(self.path),
            "holderHash",
        ):
            self.assertNotIn(secret, serialized)
        self.assertEqual(report["redaction"]["fence"], "absent")
        self.assertEqual(report["redaction"]["epoch"], "absent")

    def test_report_rejects_time_reversal_and_broad_file_modes(self):
        self.initialize()
        self.acquire(now=NOW + timedelta(seconds=1))
        reversed_report = self.store.report(now=NOW)
        self.assertEqual(reversed_report["integrity"]["state"], "unavailable")
        self.assertEqual(reversed_report["integrity"]["issueCode"], "observed-time-reversed")
        os.chmod(self.path, 0o644)
        self.assertEqual(self.store.report(now=NOW + timedelta(seconds=1))["integrity"]["state"], "corrupted")

    def test_report_classifies_locking_failures_as_unavailable(self):
        import fcntl

        self.initialize()
        fd = os.open(self.store.lock_path, os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            report = WorkerLeaseStore(self.path, lock_wait_seconds=0.02).report(now=NOW)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        self.assertEqual(report["integrity"]["state"], "unavailable")
        self.assertEqual(report["integrity"]["issueCode"], "lock-timeout")

        with patch("money_maker_3000.worker_leases.fcntl", None):
            no_locking = self.store.report(now=NOW)
        self.assertEqual(no_locking["integrity"]["state"], "unavailable")
        self.assertEqual(no_locking["integrity"]["issueCode"], "locking-unavailable")

        self.store.lock_path.unlink()
        self.store.lock_path.symlink_to(self.path)
        unsafe = self.store.report(now=NOW)
        self.assertEqual(unsafe["integrity"]["state"], "unavailable")
        self.assertEqual(unsafe["integrity"]["issueCode"], "unsafe-lock")
        self.assertEqual(unsafe["integrity"]["rawContent"], "absent")

    def test_report_normalizes_path_probe_and_state_io_failures(self):
        private_detail = "private-filesystem-detail"
        unsafe_paths = (
            Path(self.temporary.name) / ("x" * 300),
            Path(self.temporary.name) / f"embedded-{private_detail}\x00-name",
        )
        for path in unsafe_paths:
            with self.subTest(path_kind="nul" if "\x00" in os.fspath(path) else "overlong"):
                report = ProductionWorkerLeaseStore(path).report(observed_at=NOW)
                serialized = json.dumps(report, sort_keys=True)
                self.assertEqual(report["integrity"]["state"], "unavailable")
                self.assertEqual(report["integrity"]["issueCode"], "filesystem-unavailable")
                self.assertEqual(report["workerGate"]["state"], "blocked")
                self.assertNotIn(private_detail, serialized)
                self.assertNotIn(os.fspath(path), serialized)

        self.initialize()
        state_identity = os.stat(self.path).st_ino
        real_read = os.read

        def fail_state_read(fd: int, size: int) -> bytes:
            if os.fstat(fd).st_ino == state_identity:
                raise OSError(f"{private_detail}:{self.path}")
            return real_read(fd, size)

        with patch("money_maker_3000.worker_leases.os.read", side_effect=fail_state_read):
            read_report = self.store.report(now=NOW)
        self.assertEqual(read_report["integrity"]["state"], "unavailable")
        self.assertEqual(read_report["integrity"]["issueCode"], "filesystem-unavailable")
        self.assertNotIn(private_detail, json.dumps(read_report))
        self.assertNotIn(os.fspath(self.path), json.dumps(read_report))

        real_open = os.open

        def fail_state_open(path, flags, *args, **kwargs):
            if path == self.path.name and kwargs.get("dir_fd") is not None:
                raise OSError(f"{private_detail}:{self.path}")
            return real_open(path, flags, *args, **kwargs)

        with patch("money_maker_3000.worker_leases.os.open", side_effect=fail_state_open):
            open_report = self.store.report(now=NOW)
        self.assertEqual(open_report["integrity"]["state"], "unavailable")
        self.assertEqual(open_report["integrity"]["issueCode"], "filesystem-unavailable")
        self.assertNotIn(private_detail, json.dumps(open_report))

        real_stat = os.stat
        state_stat_calls = 0

        def fail_state_read_stat(path, *args, **kwargs):
            nonlocal state_stat_calls
            if path == self.path.name and kwargs.get("dir_fd") is not None:
                state_stat_calls += 1
                if state_stat_calls == 3:
                    raise OSError(f"{private_detail}:{self.path}")
            return real_stat(path, *args, **kwargs)

        with patch("money_maker_3000.worker_leases.os.stat", side_effect=fail_state_read_stat):
            stat_report = self.store.report(now=NOW)
        self.assertEqual(stat_report["integrity"]["state"], "unavailable")
        self.assertEqual(stat_report["integrity"]["issueCode"], "filesystem-unavailable")
        self.assertNotIn(private_detail, json.dumps(stat_report))
        self.assertGreaterEqual(state_stat_calls, 3)

    def test_report_normalizes_lock_anchor_read_and_seek_failures(self):
        self.initialize()
        private_detail = "private-lock-io-detail"
        lock_identity = os.stat(self.store.lock_path).st_ino
        real_read = os.read
        real_lseek = os.lseek

        def fail_lock_read(fd: int, size: int) -> bytes:
            if os.fstat(fd).st_ino == lock_identity:
                raise OSError(f"{private_detail}:{self.store.lock_path}")
            return real_read(fd, size)

        def fail_lock_seek(fd: int, offset: int, whence: int) -> int:
            if os.fstat(fd).st_ino == lock_identity:
                raise OSError(f"{private_detail}:{self.store.lock_path}")
            return real_lseek(fd, offset, whence)

        for target, side_effect in (("os.read", fail_lock_read), ("os.lseek", fail_lock_seek)):
            with self.subTest(operation=target):
                with patch(f"money_maker_3000.worker_leases.{target}", side_effect=side_effect):
                    report = self.store.report(now=NOW)
                serialized = json.dumps(report, sort_keys=True)
                self.assertEqual(report["integrity"]["state"], "unavailable")
                self.assertEqual(report["integrity"]["issueCode"], "unsafe-lock")
                self.assertEqual(report["workerGate"]["state"], "blocked")
                self.assertNotIn(private_detail, serialized)
                self.assertNotIn(os.fspath(self.store.lock_path), serialized)

    def test_untrusted_parent_is_unavailable_and_never_mutated(self):
        for mode in (0o755, 0o777):
            with self.subTest(mode=oct(mode)):
                os.chmod(self.path.parent, mode)
                report = self.store.report(now=NOW)
                self.assertEqual(report["integrity"]["state"], "unavailable")
                self.assertEqual(report["integrity"]["issueCode"], "unsafe-parent")
                with self.assertRaisesRegex(WorkerLeaseStoreError, "euid-owned"):
                    self.store.initialize(now=NOW)
                self.assertFalse(self.path.exists())
                self.assertFalse(self.store.lock_path.exists())

    @unittest.skipUnless("fork" in multiprocessing.get_all_start_methods(), "requires fork multiprocessing")
    def test_lock_wait_is_bounded(self):
        import fcntl

        self.initialize()
        fd = os.open(self.store.lock_path, os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            contender = WorkerLeaseStore(self.path, lock_wait_seconds=0.02)
            started = datetime.now(timezone.utc)
            with self.assertRaisesRegex(WorkerLeaseStoreError, "timed out"):
                contender.acquire(holder="worker", idempotency_key="job", ttl_seconds=1, now=NOW)
            elapsed = (datetime.now(timezone.utc) - started).total_seconds()
            self.assertLess(elapsed, 1.0)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    @unittest.skipUnless("fork" in multiprocessing.get_all_start_methods(), "requires fork multiprocessing")
    def test_contended_transitions_sample_clock_only_after_both_locks(self):
        import fcntl

        context = multiprocessing.get_context("fork")

        def run_delayed(
            path: Path,
            operation: str,
            target: datetime,
            *,
            epoch: str | None = None,
            fence: int | None = None,
        ) -> dict:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            fcntl.flock(directory_fd, fcntl.LOCK_EX)
            clock_seconds = context.Value("d", NOW.timestamp())
            attempting = context.Event()
            clock_called = context.Event()
            queue = context.Queue()
            process = context.Process(
                target=_delayed_clock_operation_worker,
                args=(
                    os.fspath(path),
                    operation,
                    epoch,
                    fence,
                    clock_seconds,
                    attempting,
                    clock_called,
                    queue,
                ),
            )
            try:
                process.start()
                self.assertTrue(attempting.wait(timeout=5))
                self.assertFalse(clock_called.is_set(), f"{operation} sampled time before the directory lock")
                clock_seconds.value = target.timestamp()
            finally:
                fcntl.flock(directory_fd, fcntl.LOCK_UN)
                os.close(directory_fd)
            process.join(timeout=10)
            self.assertEqual(process.exitcode, 0)
            result = queue.get(timeout=2)
            self.assertEqual(result[0], "ok", result)
            self.assertTrue(clock_called.is_set())
            return result[1]

        initialize_path = Path(self.temporary.name) / "delayed-initialize.json"
        initialized = run_delayed(initialize_path, "initialize", NOW + timedelta(seconds=5))
        self.assertEqual(initialized["status"], "initialized")
        self.assertEqual(
            json.loads(initialize_path.read_text(encoding="utf-8"))["lastMutationAt"],
            "2026-07-15T00:00:05.000000Z",
        )

        acquire_path = Path(self.temporary.name) / "delayed-acquire.json"
        ProductionWorkerLeaseStore(acquire_path, clock=lambda: NOW).initialize()
        acquired = run_delayed(acquire_path, "acquire", NOW + timedelta(seconds=10))
        self.assertEqual(acquired["expiresAt"], "2026-07-15T00:01:10.000000Z")

        authorize_path = Path(self.temporary.name) / "delayed-authorize.json"
        authorize_store = ProductionWorkerLeaseStore(authorize_path, clock=lambda: NOW)
        authorize_store.initialize()
        authorize_lease = authorize_store.acquire(holder="worker", idempotency_key="job", ttl_seconds=1)
        authorized = run_delayed(
            authorize_path,
            "authorize",
            NOW + timedelta(seconds=2),
            epoch=authorize_lease["epoch"],
            fence=authorize_lease["fence"],
        )
        self.assertEqual(authorized["reason"], "expired")

        renew_path = Path(self.temporary.name) / "delayed-renew.json"
        renew_store = ProductionWorkerLeaseStore(renew_path, clock=lambda: NOW)
        renew_store.initialize()
        renew_lease = renew_store.acquire(holder="worker", idempotency_key="job", ttl_seconds=60)
        renewed = run_delayed(
            renew_path,
            "renew",
            NOW + timedelta(seconds=10),
            epoch=renew_lease["epoch"],
            fence=renew_lease["fence"],
        )
        self.assertEqual(renewed["expiresAt"], "2026-07-15T00:00:40.000000Z")

        for operation in ("release", "complete"):
            with self.subTest(operation=operation):
                path = Path(self.temporary.name) / f"delayed-{operation}.json"
                store = ProductionWorkerLeaseStore(path, clock=lambda: NOW)
                store.initialize()
                lease = store.acquire(holder="worker", idempotency_key="job", ttl_seconds=1)
                result = run_delayed(
                    path,
                    operation,
                    NOW + timedelta(seconds=2),
                    epoch=lease["epoch"],
                    fence=lease["fence"],
                )
                self.assertEqual(result, {"status": "expired", f"{operation}d": False})

        kill_path = Path(self.temporary.name) / "delayed-kill.json"
        ProductionWorkerLeaseStore(kill_path, clock=lambda: NOW).initialize()
        killed = run_delayed(kill_path, "kill", NOW + timedelta(seconds=10))
        self.assertEqual(killed["status"], "engaged")
        kill_state = json.loads(kill_path.read_text(encoding="utf-8"))
        self.assertEqual(kill_state["killSwitch"]["changedAt"], "2026-07-15T00:00:10.000000Z")

    @unittest.skipUnless("fork" in multiprocessing.get_all_start_methods(), "requires fork multiprocessing")
    def test_multiprocessing_fresh_same_owner_and_expiry_races(self):
        context = multiprocessing.get_context("fork")
        for label, holders, keys, at, expected_statuses in (
            (
                "fresh",
                [f"worker-{index}" for index in range(8)],
                [f"job-{index}" for index in range(8)],
                NOW,
                {"acquired": 1, "busy": 7},
            ),
            (
                "same-owner",
                ["worker-same"] * 8,
                ["job-same"] * 8,
                NOW,
                {"acquired": 1, "held": 7},
            ),
        ):
            with self.subTest(label=label):
                path = Path(self.temporary.name) / f"race-{label}.json"
                WorkerLeaseStore(path).initialize(now=NOW)
                queue = context.Queue()
                start = context.Event()
                processes = [
                    context.Process(
                        target=_barrier_acquire_worker,
                        args=(os.fspath(path), holder, key, at, start, queue),
                    )
                    for holder, key in zip(holders, keys, strict=True)
                ]
                for process in processes:
                    process.start()
                start.set()
                for process in processes:
                    process.join(timeout=10)
                self.assertTrue(all(process.exitcode == 0 for process in processes))
                results = [queue.get(timeout=2) for _ in processes]
                self.assertTrue(all(result[0] == "ok" for result in results), results)
                histogram: dict[str, int] = {}
                for _, _, result in results:
                    histogram[result["status"]] = histogram.get(result["status"], 0) + 1
                self.assertEqual(histogram, expected_statuses)
                persisted = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(persisted["fenceGeneration"], 1)
                self.assertEqual(persisted["lease"]["fence"], 1)
                if label == "same-owner":
                    self.assertEqual(len({result[2]["fence"] for result in results}), 1)
                    self.assertEqual(
                        persisted["lease"]["holderHash"],
                        worker_leases_module._opaque_hash("holder", "worker-same"),
                    )
                else:
                    self.assertIn(persisted["lease"]["holderHash"], {
                        worker_leases_module._opaque_hash("holder", holder) for holder in holders
                    })

        expiry_path = Path(self.temporary.name) / "race-expiry.json"
        expiry_store = WorkerLeaseStore(expiry_path)
        expiry_store.initialize(now=NOW)
        old = expiry_store.acquire(holder="old", idempotency_key="old", ttl_seconds=1, now=NOW)
        queue = context.Queue()
        start = context.Event()
        processes = [
            context.Process(
                target=_barrier_acquire_worker,
                args=(
                    os.fspath(expiry_path),
                    f"new-{index}",
                    f"new-{index}",
                    NOW + timedelta(seconds=1),
                    start,
                    queue,
                ),
            )
            for index in range(8)
        ]
        for process in processes:
            process.start()
        start.set()
        for process in processes:
            process.join(timeout=10)
        results = [queue.get(timeout=2) for _ in processes]
        self.assertTrue(all(process.exitcode == 0 for process in processes))
        self.assertTrue(all(result[0] == "ok" for result in results), results)
        histogram: dict[str, int] = {}
        for _, _, result in results:
            histogram[result["status"]] = histogram.get(result["status"], 0) + 1
        self.assertEqual(histogram, {"acquired": 1, "busy": 7})
        acquired = [result[2] for result in results if result[2]["status"] == "acquired"]
        self.assertEqual(len(acquired), 1)
        self.assertGreater(acquired[0]["fence"], old["fence"])
        persisted = json.loads(expiry_path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["fenceGeneration"], acquired[0]["fence"])
        self.assertEqual(persisted["lease"]["fence"], acquired[0]["fence"])

    @unittest.skipUnless("fork" in multiprocessing.get_all_start_methods(), "requires fork multiprocessing")
    def test_multiprocessing_kill_race_ends_revoked_and_fenced(self):
        self.initialize()
        context = multiprocessing.get_context("fork")
        queue = context.Queue()
        start = context.Event()
        at = NOW + timedelta(seconds=1)
        processes = [
            context.Process(
                target=_barrier_acquire_worker,
                args=(os.fspath(self.path), "worker", "job", at, start, queue),
            ),
            context.Process(target=_barrier_kill_worker, args=(os.fspath(self.path), at, start, queue)),
        ]
        for process in processes:
            process.start()
        start.set()
        for process in processes:
            process.join(timeout=10)
        results = [queue.get(timeout=2) for _ in processes]
        self.assertTrue(all(process.exitcode == 0 for process in processes))
        self.assertTrue(all(result[0] == "ok" for result in results), results)
        statuses = {result[1]: result[2]["status"] for result in results}
        self.assertEqual(statuses["kill"], "engaged")
        self.assertIn(statuses["worker"], {"acquired", "kill-switch-blocked"})
        self.assertEqual(self.store.report(now=at)["workerGate"]["state"], "kill-switch-blocked")
        state = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIsNone(state["lease"])
        self.assertTrue(state["killSwitch"]["engaged"])
        self.assertGreaterEqual(state["fenceGeneration"], 1)

    @unittest.skipUnless("fork" in multiprocessing.get_all_start_methods(), "requires fork multiprocessing")
    def test_lock_replacement_cannot_acknowledge_a_lost_mutation(self):
        self.initialize()
        context = multiprocessing.get_context("fork")
        entered = context.Event()
        proceed = context.Event()
        queue = context.Queue()
        writer_a = context.Process(
            target=_paused_lock_replacement_worker,
            args=(os.fspath(self.path), entered, proceed, queue),
        )
        writer_a.start()
        self.assertTrue(entered.wait(timeout=5))

        replacement = self.store.lock_path.with_name("replacement.lock")
        replacement.write_bytes(self.store.lock_path.read_bytes())
        os.chmod(replacement, 0o600)
        os.replace(replacement, self.store.lock_path)

        start_b = context.Event()
        writer_b = context.Process(
            target=_barrier_acquire_worker,
            args=(os.fspath(self.path), "writer-b", "job-b", NOW, start_b, queue),
        )
        writer_b.start()
        start_b.set()
        proceed.set()
        writer_a.join(timeout=10)
        writer_b.join(timeout=10)
        self.assertEqual(writer_a.exitcode, 0)
        self.assertEqual(writer_b.exitcode, 0)
        results = [queue.get(timeout=2) for _ in range(2)]
        errors = [result for result in results if result[0] == "error"]
        successes = [result for result in results if result[0] == "ok"]
        self.assertEqual(len(errors), 1, results)
        self.assertRegex(errors[0][2], r"lease lock (changed|must be a regular single-link file)")
        self.assertEqual(len(successes), 1, results)
        self.assertEqual(successes[0][2]["status"], "acquired")

        state = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(state["lease"]["holderHash"], worker_leases_module._opaque_hash("holder", "writer-b"))
        self.assertEqual(state["lease"]["fence"], successes[0][2]["fence"])
        self.assertEqual(state["fenceGeneration"], successes[0][2]["fence"])

    @unittest.skipUnless("fork" in multiprocessing.get_all_start_methods(), "requires fork multiprocessing")
    def test_credential_transition_races_are_serializable(self):
        context = multiprocessing.get_context("fork")
        for left, right in (
            ("renew", "release"),
            ("complete", "release"),
            ("renew", "kill"),
            ("release", "kill"),
        ):
            with self.subTest(left=left, right=right):
                path = Path(self.temporary.name) / f"race-{left}-{right}.json"
                store = WorkerLeaseStore(path)
                store.initialize(now=NOW)
                lease = store.acquire(holder="worker", idempotency_key="job", ttl_seconds=60, now=NOW)
                start = context.Event()
                queue = context.Queue()
                processes = [
                    context.Process(
                        target=_credential_operation_worker,
                        args=(
                            os.fspath(path),
                            operation,
                            "worker",
                            "job",
                            lease["epoch"],
                            lease["fence"],
                            NOW + timedelta(seconds=1),
                            start,
                            queue,
                        ),
                    )
                    for operation in (left, right)
                ]
                for process in processes:
                    process.start()
                start.set()
                for process in processes:
                    process.join(timeout=10)
                self.assertTrue(all(process.exitcode == 0 for process in processes))
                results = [queue.get(timeout=2) for _ in processes]
                self.assertTrue(all(result[0] == "ok" for result in results), results)
                by_operation = {result[1]: result[2] for result in results}
                state = json.loads(path.read_text(encoding="utf-8"))
                self.assertIsNone(state["lease"])

                if "kill" in by_operation:
                    self.assertEqual(by_operation["kill"]["status"], "engaged")
                    self.assertTrue(state["killSwitch"]["engaged"])
                    other = left if right == "kill" else right
                    if other == "renew":
                        self.assertIn(by_operation[other]["status"], {"renewed", "kill-switch-blocked"})
                    else:
                        self.assertIn(by_operation[other]["status"], {"released", "kill-switch-blocked"})
                elif left == "renew":
                    self.assertEqual(by_operation["release"]["status"], "released")
                    self.assertIn(by_operation["renew"]["status"], {"renewed", "not-held"})
                    self.assertIsNotNone(state["lastRelease"])
                else:
                    successful_terminal = sum(
                        bool(by_operation[operation].get(f"{operation}d"))
                        for operation in ("complete", "release")
                    )
                    self.assertEqual(successful_terminal, 1)
                    self.assertEqual(bool(state["completions"]), by_operation["complete"]["completed"])

    @unittest.skipUnless("fork" in multiprocessing.get_all_start_methods(), "requires fork multiprocessing")
    def test_process_crash_releases_flock(self):
        self.initialize()
        context = multiprocessing.get_context("fork")
        ready = context.Event()
        process = context.Process(target=_crash_with_lock, args=(os.fspath(self.store.lock_path), ready))
        process.start()
        self.assertTrue(ready.wait(timeout=2))
        process.join(timeout=5)
        self.assertEqual(process.exitcode, 23)
        result = self.acquire(now=NOW + timedelta(seconds=1))
        self.assertEqual(result["status"], "acquired")

    @unittest.skipUnless("spawn" in multiprocessing.get_all_start_methods(), "requires spawn multiprocessing")
    def test_spawn_contention_uses_the_same_single_winner_contract(self):
        self.initialize()
        context = multiprocessing.get_context("spawn")
        queue = context.Queue()
        processes = [
            context.Process(
                target=_acquire_worker,
                args=(os.fspath(self.path), f"spawn-{index}", f"spawn-job-{index}", NOW, queue),
            )
            for index in range(4)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=15)
        self.assertTrue(all(process.exitcode == 0 for process in processes))
        results = [queue.get(timeout=2) for _ in processes]
        statuses = [result[1]["status"] for result in results if result[0] == "ok"]
        self.assertEqual(statuses.count("acquired"), 1)
        self.assertEqual(statuses.count("busy"), 3)

    def test_atomic_replace_failure_preserves_prior_canonical_bytes(self):
        self.initialize()
        before = self.path.read_bytes()
        with patch("money_maker_3000.worker_leases.os.replace", side_effect=OSError("injected replace failure")):
            with self.assertRaisesRegex(OSError, "injected replace failure"):
                self.acquire()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob(f".{self.path.name}.tmp-*")), [])

    def test_pre_replace_temporary_fsync_failure_preserves_prior_canonical_bytes(self):
        self.initialize()
        before = self.path.read_bytes()
        with patch(
            "money_maker_3000.worker_leases.os.fsync",
            side_effect=OSError("injected temporary fsync failure"),
        ):
            with self.assertRaisesRegex(OSError, "injected temporary fsync failure"):
                self.acquire()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob(f".{self.path.name}.tmp-*")), [])

    def test_post_replace_directory_fsync_failure_leaves_valid_retry_safe_state(self):
        self.initialize()
        real_fsync = os.fsync
        calls = 0

        def fail_directory_fsync(fd: int) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected directory fsync failure")
            real_fsync(fd)

        with patch("money_maker_3000.worker_leases.os.fsync", side_effect=fail_directory_fsync):
            with self.assertRaisesRegex(OSError, "injected directory fsync failure"):
                self.acquire()

        state = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIsNotNone(state["lease"])
        before_retry = self.path.read_bytes()
        retry = self.acquire()
        self.assertEqual(retry["status"], "held")
        self.assertEqual(self.path.read_bytes(), before_retry)
        self.assertEqual(self.store.report(now=NOW)["integrity"]["state"], "clean")


if __name__ == "__main__":
    unittest.main()
