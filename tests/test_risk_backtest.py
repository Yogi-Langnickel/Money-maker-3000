import json
import multiprocessing
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from money_maker_3000.backtest import (
    build_historical_fixture_backtest,
    build_offline_fixture_batch_diagnostics,
    build_synthetic_backtest,
    iter_decision_events,
    summarize_decision_events,
)
from money_maker_3000.contracts import build_allocation_policy, default_simulation_config_for_strategy
from money_maker_3000.ledger import (
    append_ledger_record,
    build_ledger_record,
    build_ledger_report,
    read_ledger_records,
    redact_trade_log_entry,
)
from money_maker_3000.market_history import (
    build_period_performance_diagnostics,
    iter_market_history_bars,
    parse_market_history_csv,
    sha256_file,
    summarize_market_history_bars,
)
from money_maker_3000.reconciliation import (
    build_simulation_reconciliation_record,
    risk_input_state_from_reconciliation,
)
from money_maker_3000.risk import DEFAULT_RISK_POLICY, RiskInputState, evaluate_risk_gate, validate_risk_policy

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "market_history" / "spy-daily.csv"
GLD_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "market_history" / "gld-daily.csv"
SELECTED_SPY = {"symbol": "SPY", "market": "US_EQUITIES", "instrumentClass": "ETF"}
SELECTED_GLD = {"symbol": "GLD", "market": "US_EQUITIES", "instrumentClass": "ETF"}


def _append_duplicate_ledger_worker(ledger_path: str, record: dict, queue: multiprocessing.Queue) -> None:
    try:
        append_ledger_record(ledger_path, record)
        queue.put(("ok", record["runId"]))
    except Exception as exc:  # pragma: no cover - assertion happens in parent.
        queue.put(("error", type(exc).__name__, str(exc)))


class RiskAndBacktestTests(unittest.TestCase):
    def test_risk_policy_enforces_loss_order_exposure_and_safety_rules(self):
        allocation = build_allocation_policy(bot_allocation_usd=1000, reserved_usd=100, max_order_usd=250)
        result = validate_risk_policy(DEFAULT_RISK_POLICY, allocation_policy=allocation)

        self.assertTrue(result.ok)
        invalid = validate_risk_policy(
            {**DEFAULT_RISK_POLICY, "dailyLossStopUsd": 200, "weeklyLossStopUsd": 100, "leverage": 2},
            allocation_policy=allocation,
        )
        self.assertFalse(invalid.ok)
        joined = " ".join(invalid.errors)
        self.assertIn("daily loss stop must be <= weekly loss stop <= bot allocation", joined)
        self.assertIn("leverage must remain 1", joined)

    def test_risk_engine_fails_closed_on_missing_reconciliation_and_unknown_provider(self):
        config = default_simulation_config_for_strategy("dca-cash-reserve")
        decision = evaluate_risk_gate(
            simulation_config=config,
            allocation_policy=build_allocation_policy(bot_allocation_usd=1000, reserved_usd=100),
            risk_state=RiskInputState(data_freshness="fresh"),
            proposed_order_usd=100,
        )

        self.assertEqual(decision["decision"], "blocked")
        self.assertIn("unknown-provider-state", decision["vetoes"])
        self.assertIn("missing-reconciliation", decision["vetoes"])
        self.assertIn("missing-loss-reconciliation", decision["vetoes"])
        self.assertIn("execution-route-absent", decision["vetoes"])
        self.assertEqual(decision["allocation"]["providerDemoBalance"], "redacted")
        self.assertIn("not evaluated against real PnL", decision["diagnostics"][0])

    def test_risk_engine_enforces_per_order_exposure_cash_and_position_caps(self):
        config = default_simulation_config_for_strategy("dca-cash-reserve")
        state = RiskInputState(
            provider_state="known-read-only",
            reconciliation_state="available",
            data_freshness="fresh",
            daily_loss_usd=0,
            weekly_loss_usd=0,
            allocation_drawdown_usd=0,
            instrument_exposure_usd=400,
            open_positions=3,
        )
        decision = evaluate_risk_gate(
            simulation_config=config,
            allocation_policy=build_allocation_policy(bot_allocation_usd=1000, reserved_usd=100, max_order_usd=250),
            risk_state=state,
            proposed_order_usd=300,
        )

        self.assertIn("per-order-cap", decision["vetoes"])
        self.assertIn("per-instrument-exposure-cap", decision["vetoes"])
        self.assertIn("max-open-positions", decision["vetoes"])

    def test_reconciliation_record_redacts_provider_context_and_feeds_risk_gate(self):
        allocation = build_allocation_policy(
            bot_allocation_usd=1000.0,
            reserved_usd=100.0,
            provider_demo_balance_usd=1_000_000.0,
            max_order_usd=250.0,
        )
        record = build_simulation_reconciliation_record(
            allocation_policy=allocation,
            provider_snapshot={
                "providerState": "known-read-only",
                "source": "synthetic",
                "providerCallStatus": "not-attempted",
                "accountId": "acct-real-123",
                "providerDemoBalanceUsd": 1_000_000.0,
                "positionId": "pos-real-123",
            },
            data_freshness="fresh",
            daily_loss_usd=0.0,
            weekly_loss_usd=0.0,
            allocation_drawdown_usd=0.0,
            instrument_exposure_usd=100.0,
            open_positions=1,
        )
        serialized = json.dumps(record)

        self.assertTrue(record["validation"]["ok"])
        self.assertEqual(record["reconciliationState"], "available")
        self.assertEqual(record["providerSnapshot"]["accountId"], "redacted")
        self.assertEqual(record["providerSnapshot"]["providerDemoBalanceUsd"], "redacted")
        self.assertEqual(record["providerSnapshot"]["positionId"], "redacted")
        self.assertNotIn("acct-real-123", serialized)
        self.assertNotIn("1000000", serialized)

        decision = evaluate_risk_gate(
            simulation_config=default_simulation_config_for_strategy("dca-cash-reserve"),
            allocation_policy=allocation,
            risk_state=risk_input_state_from_reconciliation(record),
            proposed_order_usd=100.0,
        )

        self.assertNotIn("unknown-provider-state", decision["vetoes"])
        self.assertNotIn("missing-reconciliation", decision["vetoes"])
        self.assertNotIn("missing-loss-reconciliation", decision["vetoes"])
        self.assertIn("provider-not-connected", decision["vetoes"])
        self.assertIn("execution-route-absent", decision["vetoes"])

    def test_reconciliation_record_fails_closed_without_loss_context(self):
        record = build_simulation_reconciliation_record(
            provider_snapshot={
                "providerState": "known-read-only",
                "source": "synthetic",
                "providerCallStatus": "not-attempted",
            },
            data_freshness="fresh",
        )

        self.assertFalse(record["validation"]["ok"])
        self.assertEqual(record["reconciliationState"], "missing")
        self.assertIn("loss reconciliation metrics are required", record["validation"]["problems"])

        risk_state = risk_input_state_from_reconciliation(record)
        self.assertEqual(risk_state.provider_state, "known-read-only")
        self.assertEqual(risk_state.reconciliation_state, "missing")
        self.assertEqual(risk_state.data_freshness, "missing")

    def test_reconciliation_freshness_is_derived_from_dates_when_present(self):
        record = build_simulation_reconciliation_record(
            provider_snapshot={
                "providerState": "known-read-only",
                "source": "synthetic",
                "providerCallStatus": "not-attempted",
            },
            data_freshness="fresh",
            data_last_seen_date="2026-05-01",
            started_at_date="2026-05-15",
            max_data_age_days=7,
            daily_loss_usd=0.0,
            weekly_loss_usd=0.0,
            allocation_drawdown_usd=0.0,
        )

        self.assertFalse(record["validation"]["ok"])
        self.assertEqual(record["riskInputState"]["data_freshness"], "stale")
        self.assertIn("data freshness conflicts with provided dates", record["validation"]["problems"])

    def test_reconciliation_rejects_unknown_freshness_states(self):
        record = build_simulation_reconciliation_record(
            provider_snapshot={
                "providerState": "known-read-only",
                "source": "synthetic",
                "providerCallStatus": "not-attempted",
            },
            data_freshness="fresh-enough",
            daily_loss_usd=0.0,
            weekly_loss_usd=0.0,
            allocation_drawdown_usd=0.0,
        )

        self.assertFalse(record["validation"]["ok"])
        self.assertEqual(record["riskInputState"]["data_freshness"], "unknown")
        self.assertIn("data freshness state is invalid", record["validation"]["problems"])

    def test_streaming_market_history_parser_and_single_pass_summary(self):
        with FIXTURE_PATH.open("r", encoding="utf-8", newline="") as source:
            bars = iter_market_history_bars(source, selected_symbol="SPY")
            summary = summarize_market_history_bars(bars)

        self.assertEqual(summary["symbol"], "SPY")
        self.assertEqual(summary["source"], "public-test-fixture")
        self.assertEqual(summary["barCount"], 3)
        self.assertEqual(summary["firstDate"], "2026-05-11")
        self.assertEqual(summary["lastDate"], "2026-05-13")
        self.assertEqual(summary["closeMin"], 522.5)
        self.assertEqual(summary["closeMax"], 525.7)
        self.assertEqual(summary["providerCalls"], "blocked")

    def test_period_performance_diagnostics_are_context_only(self):
        with FIXTURE_PATH.open("r", encoding="utf-8", newline="") as source:
            diagnostics = build_period_performance_diagnostics(
                iter_market_history_bars(source, selected_symbol="SPY")
            )
        serialized = json.dumps(diagnostics)

        self.assertEqual(diagnostics["dtoVersion"], "market-period-diagnostics.v1")
        self.assertEqual(diagnostics["symbol"], "SPY")
        self.assertEqual(diagnostics["latestDate"], "2026-05-13")
        self.assertEqual(diagnostics["providerCalls"], "blocked")
        self.assertEqual(diagnostics["accountData"], "absent")
        self.assertEqual(diagnostics["execution"], "blocked")
        self.assertEqual([period["period"] for period in diagnostics["periods"]], ["24h", "1w", "1m", "1y", "5y", "max"])
        self.assertEqual(diagnostics["periods"][0]["startDate"], "2026-05-12")
        self.assertEqual(diagnostics["periods"][0]["endDate"], "2026-05-13")
        self.assertEqual(diagnostics["periods"][0]["barCount"], 2)
        self.assertEqual(diagnostics["periods"][0]["changeAbsolute"], 1.4)
        self.assertEqual(diagnostics["periods"][1]["coverageState"], "partial-history")
        self.assertIn("market-history-change-only", diagnostics["performanceClaims"])
        for forbidden in ("apikey", "accountid", "positionid", "orderid", "rawprovider", "winrate", "sharpe"):
            self.assertNotIn(forbidden, serialized.lower())

    def test_gld_fixture_expands_offline_instrument_coverage(self):
        with GLD_FIXTURE_PATH.open("r", encoding="utf-8", newline="") as source:
            report = build_historical_fixture_backtest(
                bars=iter_market_history_bars(source, selected_symbol="GLD"),
                selected_instrument=SELECTED_GLD,
                started_at=datetime.fromisoformat("2026-05-15T00:00:00+00:00"),
                input_sha256=sha256_file(GLD_FIXTURE_PATH),
            )

        self.assertEqual(report["metadata"]["dataSource"], "public-test-fixture")
        self.assertEqual(report["metadata"]["rowCount"], 3)
        self.assertEqual(report["periodDiagnostics"]["symbol"], "GLD")
        self.assertEqual(report["periodDiagnostics"]["providerCalls"], "blocked")

    def test_offline_fixture_batch_diagnostics_aggregate_per_symbol_reports(self):
        reports = []
        for symbol, fixture_path, selected in (
            ("SPY", FIXTURE_PATH, SELECTED_SPY),
            ("GLD", GLD_FIXTURE_PATH, SELECTED_GLD),
        ):
            with fixture_path.open("r", encoding="utf-8", newline="") as source:
                reports.append(
                    build_historical_fixture_backtest(
                        bars=iter_market_history_bars(source, selected_symbol=symbol),
                        selected_instrument=selected,
                        started_at=datetime.fromisoformat("2026-05-15T00:00:00+00:00"),
                        input_sha256=sha256_file(fixture_path),
                    )
                )

        batch = build_offline_fixture_batch_diagnostics(
            reports=reports,
            started_at=datetime.fromisoformat("2026-05-15T00:00:00+00:00"),
        )
        serialized = json.dumps(batch)

        self.assertEqual(batch["dtoVersion"], "offline-fixture-batch-diagnostics.v1")
        self.assertEqual(batch["mode"], "offline-fixture-batch-diagnostics")
        self.assertEqual(batch["providerCalls"], "blocked")
        self.assertEqual(batch["executionRoutes"], "absent")
        self.assertEqual(batch["coverage"]["fixtureCount"], 2)
        self.assertEqual(batch["coverage"]["totalRows"], 6)
        self.assertEqual(batch["summary"]["eventCount"], 6)
        self.assertEqual(batch["summary"]["blockedCount"], 6)
        self.assertEqual(batch["metadata"]["symbols"], ["GLD", "SPY"])
        self.assertEqual([item["symbol"] for item in batch["perSymbolDiagnostics"]], ["SPY", "GLD"])
        self.assertEqual(batch["perSymbolDiagnostics"][0]["inputSha256"], sha256_file(FIXTURE_PATH))
        self.assertEqual(batch["perSymbolDiagnostics"][0]["parserVersion"], "0.1.0-streaming-stdlib")
        self.assertEqual(batch["perSymbolDiagnostics"][0]["coverage"]["rowCount"], 3)
        self.assertEqual(batch["perSymbolDiagnostics"][0]["periodDiagnostics"]["providerCalls"], "blocked")
        self.assertIn("missing-reconciliation", batch["summary"]["vetoHistogram"])
        for forbidden in ("apiKey", "accountId", "positionId", "orderId", "rawProvider", "winRate", "sharpe"):
            self.assertNotIn(forbidden, serialized)

    def test_offline_fixture_batch_rejects_duplicate_symbols(self):
        with FIXTURE_PATH.open("r", encoding="utf-8", newline="") as source:
            report = build_historical_fixture_backtest(
                bars=iter_market_history_bars(source, selected_symbol="SPY"),
                selected_instrument=SELECTED_SPY,
                started_at=datetime.fromisoformat("2026-05-15T00:00:00+00:00"),
                input_sha256=sha256_file(FIXTURE_PATH),
            )

        with self.assertRaisesRegex(ValueError, "duplicate offline fixture symbol"):
            build_offline_fixture_batch_diagnostics(reports=[report, report])

    def test_market_history_parser_rejects_sensitive_columns_and_bad_rows(self):
        with self.assertRaisesRegex(ValueError, "account-linked columns"):
            parse_market_history_csv(
                "symbol,date,open,high,low,close,volume,source,accountId\nSPY,2026-05-11,1,2,1,2,3,x",
                selected_symbol="SPY",
            )
        with self.assertRaisesRegex(ValueError, "after the previous row"):
            parse_market_history_csv(
                "symbol,date,open,high,low,close,volume,source\n"
                "SPY,2026-05-12,1,2,1,2,3,x\n"
                "SPY,2026-05-11,1,2,1,2,3,x",
                selected_symbol="SPY",
            )

    def test_backtest_pipeline_is_iterable_to_events_to_summary(self):
        with FIXTURE_PATH.open("r", encoding="utf-8", newline="") as source:
            events = iter_decision_events(
                iter_market_history_bars(source, selected_symbol="SPY"),
                selected_instrument=SELECTED_SPY,
                started_at=datetime.fromisoformat("2026-05-15T00:00:00+00:00"),
            )
            summary = summarize_decision_events(events)

        self.assertEqual(summary["eventCount"], 3)
        self.assertEqual(summary["blockedCount"], 3)
        self.assertEqual(summary["vetoHistogram"]["missing-reconciliation"], 3)
        self.assertEqual(summary["vetoHistogram"]["execution-route-absent"], 3)
        self.assertIn("diagnostics-only", summary["performanceClaims"])

    def test_historical_fixture_backtest_run_ids_are_unique_per_bar(self):
        with FIXTURE_PATH.open("r", encoding="utf-8", newline="") as source:
            report = build_historical_fixture_backtest(
                bars=iter_market_history_bars(source, selected_symbol="SPY"),
                selected_instrument=SELECTED_SPY,
                started_at=datetime.fromisoformat("2026-05-15T00:00:00+00:00"),
                input_sha256=sha256_file(FIXTURE_PATH),
            )

        run_ids = [run["runId"] for run in report["runs"]]
        self.assertEqual(len(run_ids), 3)
        self.assertEqual(len(set(run_ids)), 3)
        self.assertTrue(all("SPY" in run_id for run_id in run_ids))

    def test_historical_fixture_backtest_metadata_is_deterministic_and_diagnostics_only(self):
        input_sha = sha256_file(FIXTURE_PATH)
        with FIXTURE_PATH.open("r", encoding="utf-8", newline="") as source:
            report = build_historical_fixture_backtest(
                bars=iter_market_history_bars(source, selected_symbol="SPY"),
                selected_instrument=SELECTED_SPY,
                strategy_parameters={
                    "fixedOrderUsd": 125.0,
                    "orderFractionPct": 0.1,
                    "cashReserveFloorUsd": 100.0,
                    "maxOrdersPerWeek": 2,
                    "cooldownDays": 1,
                },
                started_at=datetime.fromisoformat("2026-05-15T00:00:00+00:00"),
                input_sha256=input_sha,
            )
        serialized = json.dumps(report)

        self.assertEqual(report["mode"], "historical-fixture-backtest")
        self.assertEqual(report["environment"], "offline-fixture")
        self.assertEqual(report["providerCalls"], "blocked")
        self.assertEqual(report["executionRoutes"], "absent")
        self.assertEqual(report["metadata"]["strategyId"], "dca-cash-reserve")
        self.assertEqual(report["metadata"]["dataSource"], "public-test-fixture")
        self.assertEqual(report["metadata"]["firstDate"], "2026-05-11")
        self.assertEqual(report["metadata"]["lastDate"], "2026-05-13")
        self.assertEqual(report["metadata"]["rowCount"], 3)
        self.assertEqual(report["metadata"]["maxFixtureRows"], 10000)
        self.assertEqual(report["metadata"]["inputSha256"], input_sha)
        self.assertEqual(report["periodDiagnostics"]["dtoVersion"], "market-period-diagnostics.v1")
        self.assertEqual(report["periodDiagnostics"]["periods"][0]["period"], "24h")
        self.assertEqual(report["periodDiagnostics"]["periods"][-1]["period"], "max")
        self.assertEqual(report["runs"][0]["intentDiagnostics"]["dtoVersion"], "strategy-intent-diagnostics.v1")
        self.assertEqual(report["runs"][0]["intentDiagnostics"]["candidateIntent"], "skip")
        self.assertEqual(report["runs"][0]["intentDiagnostics"]["candidateOrderUsd"], 125.0)
        self.assertTrue(report["runs"][0]["intentDiagnostics"]["blockedByRiskGate"])
        self.assertEqual(report["runs"][0]["intentDiagnostics"]["providerCalls"], "blocked")
        self.assertEqual(report["runs"][0]["intentDiagnostics"]["executionRoute"], "absent")
        self.assertEqual(
            report["runs"][0]["intentDiagnostics"]["performanceClaims"],
            "diagnostics-only-no-order-or-profitability-claim",
        )
        for forbidden in ("apiKey", "accountId", "positionId", "orderId", "rawProvider", "winRate", "sharpe"):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(report["metadata"]["performanceClaims"], "diagnostics-only-no-return-or-execution-quality-metrics")

    def test_historical_fixture_backtest_rejects_large_fixture_inputs(self):
        with FIXTURE_PATH.open("r", encoding="utf-8", newline="") as source:
            with self.assertRaisesRegex(ValueError, "maxFixtureRows=2"):
                build_historical_fixture_backtest(
                    bars=iter_market_history_bars(source, selected_symbol="SPY"),
                    selected_instrument=SELECTED_SPY,
                    started_at=datetime.fromisoformat("2026-05-15T00:00:00+00:00"),
                    max_fixture_rows=2,
                )

        with FIXTURE_PATH.open("r", encoding="utf-8", newline="") as source:
            report = build_historical_fixture_backtest(
                bars=iter_market_history_bars(source, selected_symbol="SPY"),
                selected_instrument=SELECTED_SPY,
                started_at=datetime.fromisoformat("2026-05-15T00:00:00+00:00"),
                max_fixture_rows=3,
            )

        self.assertEqual(report["metadata"]["rowCount"], 3)
        self.assertEqual(report["metadata"]["maxFixtureRows"], 3)

    def test_synthetic_backtest_includes_coverage_and_veto_diagnostics(self):
        report = build_synthetic_backtest()

        self.assertEqual(report["mode"], "synthetic-backtest")
        self.assertEqual(report["summary"]["eventCount"], 4)
        self.assertEqual(report["summary"]["blockedCount"], 4)
        self.assertEqual(report["summary"]["vetoHistogram"]["execution-route-absent"], 4)
        self.assertEqual(report["scenarioSummaries"][0]["providerCalls"], "blocked")
        run_ids = [run["runId"] for run in report["runs"]]
        scenario_ids = [scenario["scenarioId"] for scenario in report["scenarioSummaries"]]
        self.assertEqual(len(run_ids), len(set(run_ids)))
        for scenario_id, run_id in zip(scenario_ids, run_ids, strict=True):
            self.assertIn(scenario_id, run_id)

    def test_ledger_records_are_append_only_redacted_and_reportable(self):
        report = build_synthetic_backtest(include_ledger_records=True)
        record = report["ledgerRecords"][0]
        unsafe = build_ledger_record(
            run={**record, "tradeLogEntry": {"accountId": "acct-real-123", "apiKey": "secret"}},
        )

        self.assertEqual(record["tradeLogEntry"]["correlationId"], record["correlationId"])
        self.assertEqual(record["allocation"]["providerDemoBalance"], "redacted")
        self.assertEqual(unsafe["tradeLogEntry"]["accountId"], "redacted")
        self.assertEqual(unsafe["providerCallStatus"], "not-attempted")

        with tempfile.TemporaryDirectory() as temp_dir:
            ledger_path = Path(temp_dir) / "ledger.jsonl"
            for ledger_record in report["ledgerRecords"]:
                append_ledger_record(ledger_path, ledger_record)
            records = read_ledger_records(ledger_path)
            ledger_report = build_ledger_report(records=records)
            serialized = json.dumps(ledger_report)

        self.assertEqual(ledger_report["summary"]["recordCount"], len(report["ledgerRecords"]))
        self.assertEqual(ledger_report["providerCalls"], "blocked")
        self.assertEqual(ledger_report["demoExecution"], "blocked")
        self.assertEqual(ledger_report["summary"]["redaction"]["providerDemoBalance"], "redacted")
        self.assertNotIn("acct-real-123", serialized)
        self.assertNotIn("secret", serialized)

    def test_ledger_append_rejects_unknown_sensitive_and_duplicate_records(self):
        report = build_synthetic_backtest(include_ledger_records=True)
        record = report["ledgerRecords"][0]

        with tempfile.TemporaryDirectory() as temp_dir:
            ledger_path = Path(temp_dir) / "ledger.jsonl"
            append_ledger_record(ledger_path, record)

            with self.assertRaisesRegex(ValueError, "runId"):
                append_ledger_record(ledger_path, {**record, "correlationId": "corr-other"})
            with self.assertRaisesRegex(ValueError, "correlationId"):
                append_ledger_record(ledger_path, {**record, "runId": "run-other"})
            with self.assertRaisesRegex(ValueError, "unsupported fields"):
                append_ledger_record(
                    ledger_path,
                    {**record, "runId": "run-third", "correlationId": "corr-third", "accountId": "acct-real-123"},
                )
            with self.assertRaisesRegex(ValueError, "unredacted sensitive data"):
                append_ledger_record(
                    ledger_path,
                    {
                        **record,
                        "runId": "run-fourth",
                        "correlationId": "corr-fourth",
                        "tradeLogEntry": {"accountId": "acct-real-123"},
                    },
                )
            with self.assertRaisesRegex(ValueError, "unredacted sensitive data"):
                append_ledger_record(
                    ledger_path,
                    {
                        **record,
                        "runId": "run-fifth",
                        "correlationId": "corr-fifth",
                        "tradeLogEntry": {"note": "manual check for acct-real-123"},
                    },
                )
            with self.assertRaisesRegex(ValueError, "unredacted sensitive data"):
                append_ledger_record(
                    ledger_path,
                    {
                        **record,
                        "runId": "run-sixth",
                        "correlationId": "corr-sixth",
                        "tradeLogEntry": {"memo": "operator@example.test"},
                    },
                )

    def test_ledger_append_is_single_writer_across_processes(self):
        if "fork" not in multiprocessing.get_all_start_methods():
            self.skipTest("single-writer ledger test requires fork-capable multiprocessing")

        report = build_synthetic_backtest(include_ledger_records=True)
        record = report["ledgerRecords"][0]

        with tempfile.TemporaryDirectory() as temp_dir:
            ledger_path = Path(temp_dir) / "ledger.jsonl"
            context = multiprocessing.get_context("fork")
            queue = context.Queue()
            processes = [
                context.Process(target=_append_duplicate_ledger_worker, args=(os.fspath(ledger_path), record, queue))
                for _ in range(8)
            ]

            for process in processes:
                process.start()
            for process in processes:
                process.join(timeout=10)

            self.assertTrue(all(process.exitcode == 0 for process in processes))
            results = [queue.get(timeout=1) for _ in processes]
            records = read_ledger_records(ledger_path)

        self.assertEqual(len([result for result in results if result[0] == "ok"]), 1)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["runId"], record["runId"])
        self.assertEqual(records[0]["correlationId"], record["correlationId"])
        self.assertEqual(
            len([result for result in results if result[0] == "error" and "already contains" in result[2]]),
            7,
        )

    def test_redaction_preserves_safe_fields(self):
        redacted = redact_trade_log_entry(
            {
                "action": "simulated-skip",
                "reasonCode": "provider-not-connected",
                "portfolioBalance": 12345,
                "OAuthToken": "token",
                "note": "manual check for order-real-123",
            }
        )

        self.assertEqual(redacted["action"], "simulated-skip")
        self.assertEqual(redacted["reasonCode"], "provider-not-connected")
        self.assertEqual(redacted["portfolioBalance"], "redacted")
        self.assertEqual(redacted["OAuthToken"], "redacted")
        self.assertEqual(redacted["note"], "redacted")
        self.assertEqual(redacted["providerCall"], "not-attempted")


if __name__ == "__main__":
    unittest.main()
