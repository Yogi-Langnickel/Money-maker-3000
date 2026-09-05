from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from money_maker_3000.offline_runner import run_once
from money_maker_3000.operations import (
    OperationsError,
    build_operations_status,
    create_snapshot,
    install_launchd_agent,
    run_recovery_drill,
    run_soak_evidence,
    verify_restore,
)
from money_maker_3000.worker_leases import WorkerLeaseStore


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "contracts" / "offline-simulation-runner-v1.json"
NOW = datetime(2026, 9, 5, tzinfo=timezone.utc)


class OperationsTests(unittest.TestCase):
    def appliance(self, root: Path) -> tuple[Path, Path]:
        state = root / "state" / "lease.json"
        ledger = root / "ledger" / "audit.jsonl"
        result = run_once(
            manifest_path=MANIFEST, state_path=state, ledger_path=ledger,
            holder="operations-test", idempotency_key="operations-2026-09-05",
            started_at=NOW, clock=lambda: NOW,
        )
        self.assertEqual(result["status"], "completed")
        return state, ledger

    def test_status_snapshot_restore_and_retention_are_clean_and_redacted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state, ledger = self.appliance(root)
            status = build_operations_status(state_path=state, ledger_path=ledger, observed_at=NOW, max_age_hours=24)
            first = create_snapshot(state_path=state, ledger_path=ledger, snapshot_root=root / "snapshots", retain=1, created_at=NOW)
            second = create_snapshot(state_path=state, ledger_path=ledger, snapshot_root=root / "snapshots", retain=1, created_at=NOW + timedelta(seconds=1))
            restored = verify_restore(snapshot_root=root / "snapshots", snapshot_id=second["snapshot"]["id"], verification_root=root / "restore", observed_at=NOW + timedelta(seconds=1))

        self.assertEqual(status["status"], "ready")
        self.assertEqual(first["status"], "created")
        self.assertEqual(second["retention"]["removed"], 1)
        self.assertEqual(restored["status"], "verified")
        self.assertEqual(restored["accountData"], "absent")

    def test_stale_and_corrupt_state_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state, ledger = self.appliance(root)
            stale = build_operations_status(state_path=state, ledger_path=ledger, observed_at=NOW + timedelta(hours=49), max_age_hours=48)
            ledger.write_text("{corrupt}\n", encoding="utf-8")
            blocked = build_operations_status(state_path=state, ledger_path=ledger, observed_at=NOW, max_age_hours=48)
            with self.assertRaisesRegex(OperationsError, "clean v2"):
                create_snapshot(state_path=state, ledger_path=ledger, snapshot_root=root / "snapshots", retain=1, created_at=NOW)

        self.assertEqual(stale["status"], "stale")
        self.assertEqual(blocked["status"], "blocked")

    def test_kill_switch_and_active_lease_are_never_ready_or_snapshotable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state, ledger = self.appliance(root)
            store = WorkerLeaseStore(state, clock=lambda: NOW)
            store.engage_kill_switch(reason="operator-stop")
            killed = build_operations_status(state_path=state, ledger_path=ledger, observed_at=NOW)
            with self.assertRaisesRegex(OperationsError, "safely idle"):
                create_snapshot(state_path=state, ledger_path=ledger, snapshot_root=root / "snapshots", retain=1, created_at=NOW)

        self.assertEqual(killed["status"], "blocked")

    def test_recovery_drill_and_bounded_soak_prove_repeatable_operations(self):
        recovery = run_recovery_drill(manifest_path=MANIFEST, started_at=NOW)
        soak = run_soak_evidence(manifest_path=MANIFEST, days=3, started_at=NOW)
        self.assertEqual(recovery["status"], "completed")
        self.assertEqual(soak["evidence"]["completedOccurrences"], 3)
        self.assertEqual(soak["wallClockClaim"], "deterministic-simulation-only-not-a-real-time-soak")

    def test_restore_refuses_tampering_and_existing_target(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state, ledger = self.appliance(root)
            snapshot = create_snapshot(state_path=state, ledger_path=ledger, snapshot_root=root / "snapshots", retain=1, created_at=NOW)
            source = root / "snapshots" / snapshot["snapshot"]["id"]
            metadata_path = source / "snapshot.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["snapshotId"] = "snapshot-20260905T000001Z-000000000000"
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            with self.assertRaisesRegex(OperationsError, "identity verification"):
                verify_restore(snapshot_root=root / "snapshots", snapshot_id=snapshot["snapshot"]["id"], verification_root=root / "restore-id", observed_at=NOW)
            metadata["ledgerRecordCount"] = 999
            metadata["snapshotId"] = snapshot["snapshot"]["id"]
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            with self.assertRaisesRegex(OperationsError, "verification failed"):
                verify_restore(snapshot_root=root / "snapshots", snapshot_id=snapshot["snapshot"]["id"], verification_root=root / "restore-count", observed_at=NOW)
            (source / "ledger.jsonl").write_text("tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(OperationsError, "identity verification"):
                verify_restore(snapshot_root=root / "snapshots", snapshot_id=snapshot["snapshot"]["id"], verification_root=root / "restore", observed_at=NOW)

    def test_launchd_install_is_explicit_and_rolls_back_on_bootstrap_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaisesRegex(OperationsError, "explicit confirmation"):
                install_launchd_agent(repository_root=ROOT, launch_agents_dir=root / "agents", confirm_install=False)
            with patch("money_maker_3000.operations.subprocess.run", side_effect=OSError("no launchctl")):
                with self.assertRaisesRegex(OperationsError, "rolled back"):
                    install_launchd_agent(repository_root=ROOT, launch_agents_dir=root / "agents", confirm_install=True)
            self.assertFalse((root / "agents" / "local.money-maker-3000.offline-runner.plist").exists())


if __name__ == "__main__":
    unittest.main()
