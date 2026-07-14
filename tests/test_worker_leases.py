from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from money_maker_3000.worker_leases import (
    MAX_STORE_BYTES,
    MAX_TTL_SECONDS,
    WorkerLeaseStore,
    WorkerLeaseStoreError,
    build_worker_lease_report,
)


NOW = datetime(2026, 7, 15, 0, 0, 0, tzinfo=timezone.utc)


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
        with self.assertRaisesRegex(WorkerLeaseStoreError, "not initialized"):
            self.acquire()
        self.assertFalse(self.path.exists())
        self.assertFalse(self.store.lock_path.exists())

        self.initialize()
        original = self.path.read_bytes()
        self.assertEqual(self.store.initialize(now=NOW + timedelta(seconds=1))["status"], "already-initialized")
        self.assertEqual(self.path.read_bytes(), original)

        self.path.unlink()
        with self.assertRaises(FileNotFoundError):
            self.acquire(now=NOW + timedelta(seconds=2))
        self.assertFalse(self.path.exists())

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
        with self.assertRaises(WorkerLeaseStoreError):
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
        with self.assertRaisesRegex(WorkerLeaseStoreError, "not initialized"):
            store.initialize(now=NOW)
        self.assertFalse(store.lock_path.exists())
        self.assertEqual(path.read_text(encoding="utf-8"), "{bad-json}\n")

    def test_corrupt_duplicate_unknown_invalid_numbers_and_oversize_fail_closed(self):
        cases = (
            b"{bad-json}\n",
            b'{"version":1,"version":1}\n',
            json.dumps({"version": 1, "unknown": True}).encode(),
            b'{"version":NaN}\n',
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
                self.assertEqual(path.read_bytes(), original)

    def test_state_and_lock_reject_symlink_fifo_and_hardlink_without_mutation(self):
        self.initialize()
        original = self.path.read_bytes()

        state_link = Path(self.temporary.name) / "state-link.json"
        state_link.symlink_to(self.path)
        linked_store = WorkerLeaseStore(state_link)
        linked_lock = linked_store.lock_path
        linked_lock.write_bytes(b"")
        with self.assertRaises(WorkerLeaseStoreError):
            linked_store.acquire(holder="w", idempotency_key="k", ttl_seconds=1, now=NOW)

        hardlink = Path(self.temporary.name) / "state-hardlink.json"
        os.link(self.path, hardlink)
        hard_store = WorkerLeaseStore(hardlink)
        hard_store.lock_path.write_bytes(b"")
        with self.assertRaises(WorkerLeaseStoreError):
            hard_store.acquire(holder="w", idempotency_key="k", ttl_seconds=1, now=NOW)
        self.assertEqual(self.path.read_bytes(), original)

        if hasattr(os, "mkfifo"):
            fifo = Path(self.temporary.name) / "state-fifo"
            os.mkfifo(fifo)
            fifo_store = WorkerLeaseStore(fifo)
            fifo_store.lock_path.write_bytes(b"")
            with self.assertRaises(WorkerLeaseStoreError):
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
        self.acquire(holder="private-owner", key="private-operation")
        report = self.store.report(now=NOW)
        serialized = json.dumps(report, sort_keys=True)
        self.assertEqual(report["workerGate"]["state"], "busy")
        for secret in ("private-owner", "private-operation", os.fspath(self.path), "holderHash"):
            self.assertNotIn(secret, serialized)
        self.assertEqual(report["redaction"]["fence"], "absent")

    def test_report_rejects_time_reversal_and_broad_file_modes(self):
        self.initialize()
        self.acquire(now=NOW + timedelta(seconds=1))
        with self.assertRaisesRegex(WorkerLeaseStoreError, "precedes"):
            self.store.report(now=NOW)
        os.chmod(self.path, 0o644)
        self.assertEqual(self.store.report(now=NOW + timedelta(seconds=1))["integrity"]["state"], "corrupted")

    @unittest.skipUnless("fork" in multiprocessing.get_all_start_methods(), "requires fork multiprocessing")
    def test_lock_wait_is_bounded(self):
        import fcntl

        self.initialize()
        fd = os.open(self.store.lock_path, os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            contender = WorkerLeaseStore(self.path, lock_wait_seconds=0.02)
            started = datetime.now(timezone.utc)
            with self.assertRaisesRegex(TimeoutError, "timed out"):
                contender.acquire(holder="worker", idempotency_key="job", ttl_seconds=1, now=NOW)
            elapsed = (datetime.now(timezone.utc) - started).total_seconds()
            self.assertLess(elapsed, 1.0)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

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
                processes = [
                    context.Process(target=_acquire_worker, args=(os.fspath(path), holder, key, at, queue))
                    for holder, key in zip(holders, keys, strict=True)
                ]
                for process in processes:
                    process.start()
                for process in processes:
                    process.join(timeout=10)
                self.assertTrue(all(process.exitcode == 0 for process in processes))
                results = [queue.get(timeout=2) for _ in processes]
                self.assertTrue(all(result[0] == "ok" for result in results), results)
                histogram: dict[str, int] = {}
                for _, result in results:
                    histogram[result["status"]] = histogram.get(result["status"], 0) + 1
                self.assertEqual(histogram, expected_statuses)
                if label == "same-owner":
                    self.assertEqual(len({result[1]["fence"] for result in results}), 1)

        expiry_path = Path(self.temporary.name) / "race-expiry.json"
        expiry_store = WorkerLeaseStore(expiry_path)
        expiry_store.initialize(now=NOW)
        old = expiry_store.acquire(holder="old", idempotency_key="old", ttl_seconds=1, now=NOW)
        queue = context.Queue()
        processes = [
            context.Process(
                target=_acquire_worker,
                args=(os.fspath(expiry_path), f"new-{index}", f"new-{index}", NOW + timedelta(seconds=1), queue),
            )
            for index in range(8)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=10)
        results = [queue.get(timeout=2) for _ in processes]
        acquired = [result[1] for result in results if result[0] == "ok" and result[1]["status"] == "acquired"]
        self.assertEqual(len(acquired), 1)
        self.assertGreater(acquired[0]["fence"], old["fence"])

    @unittest.skipUnless("fork" in multiprocessing.get_all_start_methods(), "requires fork multiprocessing")
    def test_multiprocessing_kill_race_ends_revoked_and_fenced(self):
        self.initialize()
        context = multiprocessing.get_context("fork")
        queue = context.Queue()
        at = NOW + timedelta(seconds=1)
        processes = [
            context.Process(target=_acquire_worker, args=(os.fspath(self.path), "worker", "job", at, queue)),
            context.Process(target=_kill_worker, args=(os.fspath(self.path), at, queue)),
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=10)
        results = [queue.get(timeout=2) for _ in processes]
        self.assertTrue(all(result[0] == "ok" for result in results), results)
        self.assertEqual(self.store.report(now=at)["workerGate"]["state"], "kill-switch-blocked")
        state = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIsNone(state["lease"])
        self.assertTrue(state["killSwitch"]["engaged"])

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
