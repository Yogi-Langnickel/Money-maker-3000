from __future__ import annotations

from copy import deepcopy
import inspect
import json
from math import inf, nan
import multiprocessing
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import patch

from money_maker_3000.backtest import build_synthetic_backtest
from money_maker_3000.contracts import DEFAULT_SIMULATION_CONFIG
from money_maker_3000.ledger import (
    SIMULATION_AUDIT_ALLOCATION_KEYS,
    SIMULATION_AUDIT_RECORD_KEYS,
    SIMULATION_AUDIT_TRADE_LOG_KEYS,
    _exclusive_ledger_writer,
    append_ledger_record,
    build_ledger_record,
    build_ledger_report,
    read_ledger_records,
    read_ledger_records_with_integrity,
)
from money_maker_3000.risk import (
    DATA_FRESHNESS_VETO_CODES,
    RISK_VETO_CODES,
    VALIDATION_VETO_CODES,
    evaluate_risk_gate,
)
from money_maker_3000.strategies import STRATEGY_REGISTRY, strategy_by_id


def valid_record() -> dict:
    return deepcopy(build_synthetic_backtest(include_ledger_records=True)["ledgerRecords"][0])


def hold_exclusive_ledger_lock(path: str, ready, release) -> None:
    with _exclusive_ledger_writer(Path(path)):
        ready.put("locked")
        release.get(timeout=5)


def read_locked_ledger(path: str, started, result) -> None:
    started.put("started")
    result.put(read_ledger_records_with_integrity(path))


class StrictLedgerV2SchemaTests(unittest.TestCase):
    def assert_invalid_append(self, record: dict) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises((TypeError, ValueError)):
                append_ledger_record(Path(temp_dir) / "ledger.jsonl", record)

    def test_generated_v2_record_has_exact_nested_shapes(self):
        record = valid_record()

        self.assertEqual(set(record), SIMULATION_AUDIT_RECORD_KEYS)
        self.assertEqual(set(record["allocation"]), SIMULATION_AUDIT_ALLOCATION_KEYS)
        self.assertEqual(set(record["tradeLogEntry"]), SIMULATION_AUDIT_TRADE_LOG_KEYS)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "ledger.jsonl"
            self.assertEqual(append_ledger_record(path, record), record)

    def test_append_serializer_rejects_unexpected_nan_after_validation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "serializer-guard.jsonl"
            with (
                patch(
                    "money_maker_3000.ledger._validate_simulation_audit_record",
                    return_value={"unexpected": float("nan")},
                ),
                self.assertRaisesRegex(ValueError, "JSON compliant"),
            ):
                append_ledger_record(path, valid_record())

            self.assertFalse(path.exists())

    def test_every_required_top_level_field_is_enforced(self):
        for field in SIMULATION_AUDIT_RECORD_KEYS:
            with self.subTest(field=field):
                record = valid_record()
                del record[field]
                self.assert_invalid_append(record)

    def test_unknown_top_level_and_nested_fields_are_rejected(self):
        variants = []
        record = valid_record()
        record["unexpected"] = "value"
        variants.append(record)
        record = valid_record()
        record["allocation"]["unexpected"] = "value"
        variants.append(record)
        record = valid_record()
        record["tradeLogEntry"]["unexpected"] = "value"
        variants.append(record)

        for record in variants:
            self.assert_invalid_append(record)

    def test_every_required_nested_field_is_enforced(self):
        for container, fields in (
            ("allocation", SIMULATION_AUDIT_ALLOCATION_KEYS),
            ("tradeLogEntry", SIMULATION_AUDIT_TRADE_LOG_KEYS),
        ):
            for field in fields:
                with self.subTest(container=container, field=field):
                    record = valid_record()
                    del record[container][field]
                    self.assert_invalid_append(record)

    def test_version_classification_rejects_mixed_unknown_and_future_records(self):
        variants = []
        for version, dto_version in (
            (1, "simulation-audit-record.v2"),
            (2.0, "simulation-audit-record.v2"),
            (2, "simulation-audit-record.v1"),
            (3, "simulation-audit-record.v3"),
            (99, None),
        ):
            record = valid_record()
            record["ledgerVersion"] = version
            if dto_version is None:
                del record["dtoVersion"]
            else:
                record["dtoVersion"] = dto_version
            variants.append(record)

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "ledger.jsonl"
            path.write_text("".join(json.dumps(record) + "\n" for record in variants), encoding="utf-8")
            recovered = read_ledger_records_with_integrity(path)

        self.assertEqual(recovered["records"], [])
        self.assertEqual(recovered["integrity"]["state"], "corrupted")
        self.assertEqual(recovered["integrity"]["errorCount"], len(variants))
        self.assertTrue(
            all(issue["code"] == "invalid-audit-record" for issue in recovered["integrity"]["issues"])
        )

    def test_duplicate_json_keys_are_invalid_at_top_and_nested_depths(self):
        encoded = json.dumps(valid_record(), separators=(",", ":"))
        duplicate_top = encoded.replace('{"ledgerVersion":2,', '{"ledgerVersion":2,"ledgerVersion":2,', 1)
        duplicate_nested = encoded.replace(
            '"botAllocationUsd":1000.0,',
            '"botAllocationUsd":1000.0,"botAllocationUsd":1000.0,',
            1,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "duplicate-keys.jsonl"
            path.write_text(f"{duplicate_top}\n{duplicate_nested}\n", encoding="utf-8")
            recovered = read_ledger_records_with_integrity(path)

        self.assertEqual(recovered["records"], [])
        self.assertEqual(recovered["integrity"]["state"], "corrupted")
        self.assertEqual(
            [issue["code"] for issue in recovered["integrity"]["issues"]],
            ["invalid-audit-record", "invalid-audit-record"],
        )

    def test_explicit_v1_is_read_report_redact_only_and_blocks_append(self):
        legacy = {
            "ledgerVersion": 1,
            "dtoVersion": "simulation-audit-record.v1",
            "accountEmail": "operator@example.test",
            "decision": "skip",
            "riskResult": "blocked",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "ledger.jsonl"
            original = json.dumps(legacy) + "\n"
            path.write_text(original, encoding="utf-8")
            recovered = read_ledger_records_with_integrity(path)
            with self.assertRaisesRegex(ValueError, "clean v2"):
                append_ledger_record(path, valid_record())
            unchanged = path.read_text(encoding="utf-8")

        self.assertEqual(unchanged, original)
        self.assertEqual(recovered["integrity"]["state"], "recovered-with-warnings")
        self.assertNotIn("operator@example.test", json.dumps(recovered))
        report = build_ledger_report(records=recovered["records"], integrity=recovered["integrity"])
        self.assertEqual(report["records"][0]["decision"], "skip")

    def test_bool_and_float_v1_versions_are_not_legacy_compatible(self):
        variants = [
            {"ledgerVersion": True, "dtoVersion": "simulation-audit-record.v1"},
            {"ledgerVersion": 1.0, "dtoVersion": "simulation-audit-record.v1"},
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "invalid-v1.jsonl"
            path.write_text("".join(json.dumps(record) + "\n" for record in variants), encoding="utf-8")
            recovered = read_ledger_records_with_integrity(path)

        self.assertEqual(recovered["records"], [])
        self.assertEqual(recovered["integrity"]["errorCount"], 2)
        self.assertTrue(
            all(issue["code"] == "invalid-audit-record" for issue in recovered["integrity"]["issues"])
        )

    def test_corrupt_existing_ledger_blocks_append_without_mutation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "ledger.jsonl"
            original = "{not-json}\n"
            path.write_text(original, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "clean v2"):
                append_ledger_record(path, valid_record())
            with self.assertRaisesRegex(ValueError, "corrupted"):
                read_ledger_records(path)
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_noncanonical_timestamps_and_hashes_are_rejected(self):
        variants = []
        for timestamp in (
            "2026-05-15T00:00:00Z",
            "2026-05-15T00:00:00.000+00:00",
            "2026-05-15T10:00:00.000+10:00",
            "not-a-time",
        ):
            record = valid_record()
            record["recordedAt"] = timestamp
            variants.append(record)
        for digest in ("hash", "A" * 64, "0" * 63, "0" * 65):
            record = valid_record()
            record["configHash"] = digest
            record["tradeLogEntry"]["configHash"] = digest
            variants.append(record)

        for record in variants:
            self.assert_invalid_append(record)

    def test_strategy_and_config_versions_must_match_canonical_contracts(self):
        record = valid_record()
        record["strategyVersion"] = "operator-version"
        record["tradeLogEntry"]["strategyVersion"] = "operator-version"
        self.assert_invalid_append(record)

        record = valid_record()
        record["configVersion"] = "operator-version"
        self.assert_invalid_append(record)

        strategy = strategy_by_id(valid_record()["strategyId"])
        self.assertIsNotNone(strategy)
        self.assertEqual(valid_record()["strategyVersion"], strategy["version"])

    def test_historical_v2_versions_survive_current_producer_version_advance(self):
        record = valid_record()
        advanced_registry = [
            {
                **strategy,
                "version": "9.9.9-sim",
            }
            for strategy in STRATEGY_REGISTRY
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "historical-v2.jsonl"
            path.write_text(json.dumps(record) + "\n", encoding="utf-8")
            with (
                patch("money_maker_3000.strategies.STRATEGY_REGISTRY", advanced_registry),
                patch("money_maker_3000.contracts.SIMULATION_CONTRACT_VERSION", "9.9.9-sim"),
            ):
                recovered = read_ledger_records_with_integrity(path)

        self.assertEqual(recovered["integrity"]["state"], "clean")
        self.assertEqual(recovered["records"], [record])

    def test_invalid_numeric_scalars_booleans_and_nonfinite_values_are_rejected(self):
        for field in ("botAllocationUsd", "reservedUsd", "availableUsd", "maxOrderUsd"):
            for value in (True, -1, nan, inf):
                with self.subTest(field=field, value=value):
                    record = valid_record()
                    record["allocation"][field] = value
                    self.assert_invalid_append(record)
        for value in (True, -1, nan, inf):
            with self.subTest(field="budgetRemainingUsd", value=value):
                record = valid_record()
                record["tradeLogEntry"]["budgetRemainingUsd"] = value
                self.assert_invalid_append(record)

    def test_huge_numbers_are_controlled_corruption_not_overflow(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "huge-numbers.jsonl"
            records = []
            for value in (10**1000, 1e308):
                record = valid_record()
                record["allocation"]["botAllocationUsd"] = value
                records.append(record)
            path.write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )
            recovered = read_ledger_records_with_integrity(path)

        self.assertEqual(recovered["records"], [])
        self.assertEqual(recovered["integrity"]["state"], "corrupted")
        self.assertEqual(recovered["integrity"]["errorCount"], 2)
        self.assertTrue(
            all(issue["code"] == "invalid-audit-record" for issue in recovered["integrity"]["issues"])
        )

    def test_frozen_diagnostic_and_provider_boundary_values_are_enforced(self):
        mutations = (
            ("mode", "live"),
            ("environment", "provider"),
            ("strategyId", "operator-strategy"),
            ("decision", "buy"),
            ("riskResult", "approved"),
            ("riskDecision", "allowed"),
            ("dataFreshness", "guaranteed-fresh"),
            ("providerCallStatus", "attempted"),
            ("executionRoute", "demo"),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                record = valid_record()
                record[field] = value
                self.assert_invalid_append(record)

        trade_mutations = (
            ("action", "buy"),
            ("decision", "allowed"),
            ("reasonCode", "guaranteed-profit"),
            ("accountIdentifiers", "present"),
            ("rawProviderPayloads", "present"),
            ("providerCall", "attempted"),
            ("executionRoute", "demo"),
        )
        for field, value in trade_mutations:
            with self.subTest(trade_field=field):
                record = valid_record()
                record["tradeLogEntry"][field] = value
                self.assert_invalid_append(record)

    def test_allocation_relationships_fail_closed(self):
        variants = []
        record = valid_record()
        record["allocation"]["availableUsd"] += 1
        variants.append(record)
        record = valid_record()
        record["allocation"]["reservedUsd"] = record["allocation"]["botAllocationUsd"] + 1
        variants.append(record)
        record = valid_record()
        record["allocation"]["maxOrderUsd"] = record["allocation"]["availableUsd"] + 1
        variants.append(record)

        for record in variants:
            self.assert_invalid_append(record)

    def test_duplicate_unknown_and_parent_mismatched_vetoes_are_rejected(self):
        record = valid_record()
        record["vetoes"].append(record["vetoes"][0])
        record["tradeLogEntry"]["vetoes"] = list(record["vetoes"])
        self.assert_invalid_append(record)

        record = valid_record()
        record["vetoes"] = ["not-a-veto"]
        record["tradeLogEntry"]["vetoes"] = ["not-a-veto"]
        self.assert_invalid_append(record)

        record = valid_record()
        record["tradeLogEntry"]["vetoes"] = list(reversed(record["vetoes"]))
        self.assert_invalid_append(record)

    def test_parent_and_trade_log_identity_fields_must_match(self):
        for field in (
            "correlationId",
            "strategyId",
            "strategyVersion",
            "configHash",
            "allocationId",
            "strategyAllocationId",
            "riskDecision",
            "dataFreshness",
        ):
            with self.subTest(field=field):
                record = valid_record()
                record["tradeLogEntry"][field] = "mismatch"
                self.assert_invalid_append(record)

        record = valid_record()
        record["tradeLogEntry"]["tradeLogId"] = "trade-log-unbound"
        self.assert_invalid_append(record)

    def test_multi_record_backtest_trade_log_ids_are_unique_and_bound_to_runs(self):
        records = build_synthetic_backtest(include_ledger_records=True)["ledgerRecords"]
        trade_log_ids = [record["tradeLogEntry"]["tradeLogId"] for record in records]

        self.assertGreater(len(records), 1)
        self.assertEqual(len(trade_log_ids), len(set(trade_log_ids)))
        self.assertEqual(
            trade_log_ids,
            [f"trade-log-{record['runId']}" for record in records],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "multi-run.jsonl"
            for record in records:
                append_ledger_record(path, record)
            self.assertEqual(len(read_ledger_records(path)), len(records))

    def test_public_reader_waits_for_exclusive_writer_lock(self):
        if "fork" not in multiprocessing.get_all_start_methods():
            self.skipTest("shared-lock test requires fork-capable multiprocessing")

        context = multiprocessing.get_context("fork")
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "ledger.jsonl"
            append_ledger_record(path, valid_record())
            ready = context.Queue()
            release = context.Queue()
            started = context.Queue()
            result = context.Queue()
            holder = context.Process(
                target=hold_exclusive_ledger_lock,
                args=(str(path), ready, release),
            )
            reader = context.Process(
                target=read_locked_ledger,
                args=(str(path), started, result),
            )
            holder.start()
            self.assertEqual(ready.get(timeout=2), "locked")
            reader.start()
            self.assertEqual(started.get(timeout=2), "started")
            with self.assertRaises(queue.Empty):
                result.get(timeout=0.2)
            release.put("release")
            recovered = result.get(timeout=2)
            holder.join(timeout=2)
            reader.join(timeout=2)

        self.assertEqual(holder.exitcode, 0)
        self.assertEqual(reader.exitcode, 0)
        self.assertEqual(recovered["integrity"]["state"], "clean")

    def test_report_does_not_default_malformed_v2(self):
        record = valid_record()
        del record["runId"]

        with self.assertRaisesRegex(ValueError, "fields do not match"):
            build_ledger_report(records=[record])

    def test_legacy_report_sanitizes_nonfinite_and_out_of_range_scalars(self):
        legacy = {
            "ledgerVersion": 1,
            "recordedAt": float("inf"),
            "correlationId": float("nan"),
            "runId": 10**1000,
            "allocationId": b"not-json-safe",
            "strategyId": "dca-cash-reserve",
            "decision": "skip",
            "riskResult": "blocked",
            "tradeLogEntry": {
                "action": "simulated-skip",
                "reasonCode": "blocked",
                "budgetRemainingUsd": float("nan"),
            },
        }

        report = build_ledger_report(records=[legacy])
        serialized = json.dumps(report, allow_nan=False)

        self.assertIsInstance(serialized, str)
        self.assertIsNone(report["records"][0]["recordedAt"])
        self.assertIsNone(report["records"][0]["correlationId"])
        self.assertIsNone(report["records"][0]["runId"])
        self.assertIsNone(report["records"][0]["allocationId"])
        self.assertIsNone(report["records"][0]["tradeLog"]["budgetRemainingUsd"])

    def test_record_builder_does_not_default_missing_current_facts(self):
        record = valid_record()

        with self.assertRaisesRegex(ValueError, "evaluated time is missing"):
            build_ledger_record(
                run={
                    **record,
                    "riskDecision": {"decision": "blocked"},
                    "allocation": {
                        **record["allocation"],
                        "allocationId": record["allocationId"],
                        "strategyAllocationId": record["strategyAllocationId"],
                    },
                }
            )

    def test_risk_veto_catalog_covers_every_emitted_veto(self):
        risk = evaluate_risk_gate(
            simulation_config=DEFAULT_SIMULATION_CONFIG,
            risk_state="invalid",
            proposed_order_usd=-1,
        )

        self.assertIn("invalid-risk-state", RISK_VETO_CODES)
        self.assertIn("invalid-order-intent", RISK_VETO_CODES)
        self.assertTrue(set(VALIDATION_VETO_CODES.values()).issubset(RISK_VETO_CODES))
        self.assertTrue(DATA_FRESHNESS_VETO_CODES.issubset(RISK_VETO_CODES))
        self.assertTrue(set(risk["vetoes"]).issubset(RISK_VETO_CODES))
        source = inspect.getsource(evaluate_risk_gate)
        self.assertNotIn("vetoes.append(", source)
        self.assertNotIn("vetoes.extend(", source)


if __name__ == "__main__":
    unittest.main()
