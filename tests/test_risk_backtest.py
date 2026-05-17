import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from money_maker_3000.backtest import (
    build_historical_fixture_backtest,
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
SELECTED_SPY = {"symbol": "SPY", "market": "US_EQUITIES", "instrumentClass": "ETF"}


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

    def test_historical_fixture_backtest_metadata_is_deterministic_and_diagnostics_only(self):
        input_sha = sha256_file(FIXTURE_PATH)
        with FIXTURE_PATH.open("r", encoding="utf-8", newline="") as source:
            report = build_historical_fixture_backtest(
                bars=iter_market_history_bars(source, selected_symbol="SPY"),
                selected_instrument=SELECTED_SPY,
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
        self.assertEqual(report["metadata"]["inputSha256"], input_sha)
        for forbidden in ("apiKey", "accountId", "positionId", "orderId", "rawProvider", "winRate", "sharpe"):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(report["metadata"]["performanceClaims"], "diagnostics-only-no-return-or-execution-quality-metrics")

    def test_synthetic_backtest_includes_coverage_and_veto_diagnostics(self):
        report = build_synthetic_backtest()

        self.assertEqual(report["mode"], "synthetic-backtest")
        self.assertEqual(report["summary"]["eventCount"], 4)
        self.assertEqual(report["summary"]["blockedCount"], 4)
        self.assertEqual(report["summary"]["vetoHistogram"]["execution-route-absent"], 4)
        self.assertEqual(report["scenarioSummaries"][0]["providerCalls"], "blocked")

    def test_ledger_records_are_append_only_redacted_and_reportable(self):
        report = build_synthetic_backtest(include_ledger_records=True)
        record = report["ledgerRecords"][0]
        unsafe = build_ledger_record(
            run={**record, "tradeLogEntry": {"accountId": "acct-real-123", "apiKey": "secret"}},
        )

        self.assertEqual(record["allocation"]["providerDemoBalance"], "redacted")
        self.assertEqual(unsafe["tradeLogEntry"]["accountId"], "redacted")
        self.assertEqual(unsafe["providerCallStatus"], "not-attempted")

        with tempfile.TemporaryDirectory() as temp_dir:
            ledger_path = Path(temp_dir) / "ledger.jsonl"
            append_ledger_record(ledger_path, record)
            append_ledger_record(ledger_path, {**record, "runId": "sim-second"})
            records = read_ledger_records(ledger_path)
            ledger_report = build_ledger_report(records=records)
            serialized = json.dumps(ledger_report)

        self.assertEqual(ledger_report["summary"]["recordCount"], 2)
        self.assertEqual(ledger_report["providerCalls"], "blocked")
        self.assertEqual(ledger_report["demoExecution"], "blocked")
        self.assertEqual(ledger_report["summary"]["redaction"]["providerDemoBalance"], "redacted")
        self.assertNotIn("acct-real-123", serialized)
        self.assertNotIn("secret", serialized)

    def test_redaction_preserves_safe_fields(self):
        redacted = redact_trade_log_entry(
            {
                "action": "simulated-skip",
                "reasonCode": "provider-not-connected",
                "portfolioBalance": 12345,
                "OAuthToken": "token",
            }
        )

        self.assertEqual(redacted["action"], "simulated-skip")
        self.assertEqual(redacted["reasonCode"], "provider-not-connected")
        self.assertEqual(redacted["portfolioBalance"], "redacted")
        self.assertEqual(redacted["OAuthToken"], "redacted")
        self.assertEqual(redacted["providerCall"], "not-attempted")


if __name__ == "__main__":
    unittest.main()
