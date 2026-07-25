from __future__ import annotations

from copy import deepcopy
import json
from math import inf, nan
from pathlib import Path
import tempfile
import unittest

from money_maker_3000.backtest import build_synthetic_backtest
from money_maker_3000.contracts import DEFAULT_SIMULATION_CONFIG
from money_maker_3000.ledger import (
    SIMULATION_AUDIT_ALLOCATION_KEYS,
    SIMULATION_AUDIT_RECORD_KEYS,
    SIMULATION_AUDIT_TRADE_LOG_KEYS,
    append_ledger_record,
    build_ledger_record,
    build_ledger_report,
    read_ledger_records,
    read_ledger_records_with_integrity,
)
from money_maker_3000.risk import RISK_VETO_CODES, evaluate_risk_gate


def valid_record() -> dict:
    return deepcopy(build_synthetic_backtest(include_ledger_records=True)["ledgerRecords"][0])


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

    def test_report_does_not_default_malformed_v2(self):
        record = valid_record()
        del record["runId"]

        with self.assertRaisesRegex(ValueError, "fields do not match"):
            build_ledger_report(records=[record])

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
        self.assertTrue(set(risk["vetoes"]).issubset(RISK_VETO_CODES))


if __name__ == "__main__":
    unittest.main()
