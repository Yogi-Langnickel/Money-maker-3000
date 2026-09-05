from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from money_maker_3000.offline_runner import (
    DEFAULT_MANIFEST_NAME,
    OfflineRunnerError,
    _build_diagnostics_and_record,
    _load_approved_manifest,
    _preflight,
    run_once,
)
from money_maker_3000.ledger import append_ledger_record, read_ledger_records
from money_maker_3000.worker_leases import WorkerLeaseStore


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "contracts" / DEFAULT_MANIFEST_NAME
NOW = datetime(2026, 9, 5, tzinfo=timezone.utc)


class OfflineRunnerTests(unittest.TestCase):
    def run_once(self, directory: Path, key: str = "daily-2026-09-05", *, clock=None) -> dict:
        return run_once(
            manifest_path=MANIFEST,
            state_path=directory / "lease.json",
            ledger_path=directory / "ledger.jsonl",
            holder="test-scheduler",
            idempotency_key=key,
            started_at=NOW,
            clock=clock or (lambda: NOW),
        )

    def test_completed_run_is_offline_redacted_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            completed = self.run_once(directory)
            repeated = self.run_once(directory)
            records = read_ledger_records(directory / "ledger.jsonl")

        self.assertEqual(completed["status"], "completed")
        self.assertTrue(completed["preflight"]["structurallyValid"])
        self.assertTrue(completed["preflight"]["strategyAnalysisSufficient"])
        self.assertEqual(completed["ledger"]["status"], "appended")
        self.assertEqual(completed["providerCalls"], "blocked")
        self.assertEqual(completed["executionRoutes"], "absent")
        self.assertEqual(repeated["status"], "already-completed")
        self.assertEqual(len(records), 1)

    def test_recovery_completes_exact_record_left_by_interrupted_worker(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            project_root, manifest = _load_approved_manifest(MANIFEST)
            _, record = _build_diagnostics_and_record(project_root, manifest, NOW)
            append_ledger_record(directory / "ledger.jsonl", record)
            store = WorkerLeaseStore(directory / "lease.json", clock=lambda: NOW)
            store.initialize()
            acquired = store.acquire(holder="test-scheduler", idempotency_key="recovery-2026-09-05", ttl_seconds=300)
            self.assertEqual(acquired["status"], "acquired")

            recovered = self.run_once(
                directory,
                "recovery-2026-09-05",
                clock=lambda: NOW + timedelta(seconds=301),
            )
            records = read_ledger_records(directory / "ledger.jsonl")

        self.assertEqual(recovered["status"], "completed")
        self.assertEqual(recovered["ledger"]["status"], "already-appended")
        self.assertEqual(len(records), 1)

    def test_busy_kill_switch_and_corrupt_ledger_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            store = WorkerLeaseStore(directory / "lease.json", clock=lambda: NOW)
            store.initialize()
            store.acquire(holder="other-scheduler", idempotency_key="other-2026-09-05", ttl_seconds=300)
            busy = self.run_once(directory)

        self.assertEqual(busy["status"], "lease-blocked")
        self.assertEqual(busy["lease"]["status"], "busy")

        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            store = WorkerLeaseStore(directory / "lease.json", clock=lambda: NOW)
            store.initialize()
            store.engage_kill_switch(reason="operator-stop")
            stopped = self.run_once(directory)

        self.assertEqual(stopped["status"], "lease-blocked")
        self.assertEqual(stopped["lease"]["status"], "kill-switch-blocked")

        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            (directory / "ledger.jsonl").write_text("{corrupt}\n", encoding="utf-8")
            with self.assertRaisesRegex(OfflineRunnerError, "clean v2"):
                self.run_once(directory)
            report = WorkerLeaseStore(directory / "lease.json", clock=lambda: NOW).report(observed_at=NOW)

        self.assertEqual(report["workerGate"]["state"], "available")

    def test_completed_marker_requires_clean_matching_ledger_and_held_is_not_a_second_owner(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            self.run_once(directory)
            (directory / "ledger.jsonl").write_text("{corrupt}\n", encoding="utf-8")
            with self.assertRaisesRegex(OfflineRunnerError, "clean v2"):
                self.run_once(directory)

        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            store = WorkerLeaseStore(directory / "lease.json", clock=lambda: NOW)
            store.initialize()
            store.acquire(holder="test-scheduler", idempotency_key="daily-2026-09-05", ttl_seconds=300)
            in_progress = self.run_once(directory)

        self.assertEqual(in_progress["status"], "lease-blocked")
        self.assertEqual(in_progress["lease"]["status"], "held")

    def test_fresh_selected_state_parent_is_created_privately(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            local = directory / ".local"
            result = run_once(
                manifest_path=MANIFEST,
                state_path=local / "lease.json",
                ledger_path=local / "ledger.jsonl",
                holder="test-scheduler",
                idempotency_key="fresh-parent-2026-09-05",
                started_at=NOW,
                clock=lambda: NOW,
            )
            self.assertTrue(local.exists())

        self.assertEqual(result["status"], "completed")

    def test_only_the_committed_allowlisted_manifest_is_accepted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            copy = Path(temp_dir) / "manifest.json"
            copy.write_text(MANIFEST.read_text(encoding="utf-8"), encoding="utf-8")
            with self.assertRaisesRegex(OfflineRunnerError, "not allowlisted"):
                run_once(
                    manifest_path=copy,
                    state_path=Path(temp_dir) / "lease.json",
                    ledger_path=Path(temp_dir) / "ledger.jsonl",
                    holder="test-scheduler",
                    idempotency_key="copy-2026-09-05",
                    started_at=NOW,
                    clock=lambda: NOW,
                )

    def test_manifest_rejects_unknown_fields_without_echoing_contents(self):
        parsed = json.loads(MANIFEST.read_text(encoding="utf-8"))
        parsed["accountId"] = "forbidden"
        with patch("money_maker_3000.offline_runner.json.loads", return_value=parsed):
            with self.assertRaisesRegex(OfflineRunnerError, "fields are invalid"):
                _load_approved_manifest(MANIFEST)

    def test_manifest_content_and_reversed_clock_fail_closed(self):
        altered = json.loads(MANIFEST.read_text(encoding="utf-8"))
        altered["strategyId"] = "dca-cash-reserve"
        with patch("money_maker_3000.offline_runner.json.loads", return_value=altered):
            with self.assertRaisesRegex(OfflineRunnerError, "content is not allowlisted"):
                _load_approved_manifest(MANIFEST)

        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            self.run_once(directory, "future-2026-09-05")
            with self.assertRaisesRegex(Exception, "precedes the last persisted mutation"):
                run_once(
                    manifest_path=MANIFEST,
                    state_path=directory / "lease.json",
                    ledger_path=directory / "ledger.jsonl",
                    holder="test-scheduler",
                    idempotency_key="past-2026-09-05",
                    started_at=datetime(2026, 9, 4, 23, 59, 59, tzinfo=timezone.utc),
                    clock=lambda: datetime(2026, 9, 4, 23, 59, 59, tzinfo=timezone.utc),
                )

    def test_structural_fixture_validity_is_reported_separately_from_sufficiency(self):
        project_root, manifest = _load_approved_manifest(MANIFEST)
        readiness = {
            "ready": True,
            "fixtureDiagnostics": [
                {
                    "ok": True,
                    "strategyHistoryDiagnostics": {
                        "state": "insufficient-history",
                        "walkForward": {"state": "insufficient-history"},
                    },
                    "samplingQuality": {"state": "insufficient-history"},
                }
            ],
        }
        with patch("money_maker_3000.offline_runner.build_backtest_readiness_report", return_value=readiness):
            preflight = _preflight(project_root, manifest, NOW)

        self.assertTrue(preflight["structurallyValid"])
        self.assertFalse(preflight["strategyAnalysisSufficient"])

    def test_coherently_regenerated_dependencies_without_approved_pins_are_blocked(self):
        project_root, manifest = _load_approved_manifest(MANIFEST)
        readiness = {
            "ready": True,
            "fixtureDiagnostics": [
                {
                    "ok": True,
                    "strategyHistoryDiagnostics": {
                        "state": "trend-confirmed",
                        "walkForward": {"state": "available"},
                    },
                    "samplingQuality": {"state": "weekday-grid-covered"},
                }
            ],
        }
        with (
            patch("money_maker_3000.offline_runner.check_dashboard_contract_manifest", return_value=True),
            patch("money_maker_3000.offline_runner.check_fixture_provenance_manifest", return_value=True),
            patch("money_maker_3000.offline_runner.build_backtest_readiness_report", return_value=readiness),
            patch("money_maker_3000.offline_runner._matches_pinned_digest", return_value=False),
        ):
            preflight = _preflight(project_root, manifest, NOW)

        self.assertFalse(preflight["structurallyValid"])
        self.assertFalse(preflight["strategyAnalysisSufficient"])
        self.assertEqual(preflight["contractManifest"], "drifted-or-invalid")
        self.assertEqual(preflight["fixtureProvenance"], "drifted-or-invalid")
        self.assertFalse(preflight["fixture"]["structurallyValid"])


if __name__ == "__main__":
    unittest.main()
