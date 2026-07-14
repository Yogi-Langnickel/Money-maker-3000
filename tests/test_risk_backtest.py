import json
import multiprocessing
import os
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path

from money_maker_3000.backtest import (
    build_historical_fixture_backtest,
    build_offline_fixture_batch_diagnostics,
    build_synthetic_backtest,
    iter_decision_events,
    summarize_decision_events,
)
from money_maker_3000.contracts import (
    DEFAULT_ALLOCATION_POLICY,
    build_allocation_policy,
    default_simulation_config_for_strategy,
)
from money_maker_3000.engine import build_simulation_run
from money_maker_3000.ledger import (
    MAX_LEDGER_LINE_BYTES,
    append_ledger_record,
    build_ledger_record,
    build_ledger_report,
    export_ledger_report,
    read_ledger_records,
    read_ledger_records_with_integrity,
    redact_trade_log_entry,
)
from money_maker_3000.market_history import (
    Bar,
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
QQQ_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "market_history" / "qqq-daily.csv"
SELECTED_SPY = {"symbol": "SPY", "market": "US_EQUITIES", "instrumentClass": "ETF"}
SELECTED_GLD = {"symbol": "GLD", "market": "US_EQUITIES", "instrumentClass": "ETF"}
SELECTED_QQQ = {"symbol": "QQQ", "market": "US_EQUITIES", "instrumentClass": "ETF"}


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

    def test_risk_policy_malformed_values_fail_closed_without_exceptions(self):
        malformed_cases = (
            ("maxOrderUsd", "bad", "risk max order must be a positive finite number"),
            ("maxOrderUsd", float("nan"), "risk max order must be a positive finite number"),
            ("perInstrumentExposureCapUsd", float("inf"), "per-instrument exposure cap"),
            ("cashReserveFloorUsd", -1, "cash reserve floor"),
            ("maxOpenPositions", True, "max open positions must be a positive integer"),
            ("maxDataAgeDays", 0, "max data age days must be a positive integer"),
            ("blockedInstrumentClasses", "CFD,OPTION", "blocked instrument classes must be a list"),
            ("leverage", True, "leverage must remain 1"),
        )

        for field, value, expected_error in malformed_cases:
            with self.subTest(field=field, value=value):
                policy = {**DEFAULT_RISK_POLICY, field: value}
                result = validate_risk_policy(policy)
                self.assertFalse(result.ok)
                self.assertIn(expected_error, " ".join(result.errors))

                decision = evaluate_risk_gate(
                    simulation_config=default_simulation_config_for_strategy("dca-cash-reserve"),
                    risk_policy=policy,
                    proposed_order_usd=100,
                )
                self.assertEqual(decision["decision"], "blocked")
                self.assertIn("invalid-risk-policy", decision["vetoes"])

        missing = dict(DEFAULT_RISK_POLICY)
        missing.pop("maxOrderUsd")
        self.assertFalse(validate_risk_policy(missing).ok)
        self.assertFalse(validate_risk_policy("not-an-object").ok)

    def test_risk_policy_cross_checks_zero_allocation_boundaries(self):
        allocation = {
            **DEFAULT_ALLOCATION_POLICY,
            "reservedUsd": 0,
            "availableUsd": DEFAULT_ALLOCATION_POLICY["botAllocationUsd"],
        }

        result = validate_risk_policy(DEFAULT_RISK_POLICY, allocation_policy=allocation)

        self.assertFalse(result.ok)
        self.assertIn(
            "cash reserve floor cannot exceed reserved allocation",
            result.errors,
        )

    def test_risk_gate_malformed_allocation_order_and_state_fail_closed(self):
        config = default_simulation_config_for_strategy("dca-cash-reserve")
        cases = (
            (
                {"allocation_policy": {"botAllocationUsd": 1000}, "proposed_order_usd": 100},
                "invalid-allocation-policy",
            ),
            ({"allocation_policy": {}, "proposed_order_usd": 100}, "invalid-allocation-policy"),
            ({"budget_policy": {"baseBudgetUsd": "bad"}, "proposed_order_usd": 100}, "invalid-budget-policy"),
            ({"schedule_policy": {"maxDecisionsPerDay": "bad"}, "proposed_order_usd": 100}, "invalid-schedule-policy"),
            ({"proposed_order_usd": "100"}, "invalid-order-intent"),
            ({"proposed_order_usd": float("nan")}, "invalid-order-intent"),
            ({"risk_state": "bad", "proposed_order_usd": 100}, "invalid-risk-state"),
            (
                {
                    "risk_state": RiskInputState(
                        daily_loss_usd="bad",
                        weekly_loss_usd=0,
                        allocation_drawdown_usd=0,
                    ),
                    "proposed_order_usd": 100,
                },
                "invalid-risk-state",
            ),
            (
                {
                    "risk_state": RiskInputState(
                        daily_loss_usd=float("nan"),
                        weekly_loss_usd=0,
                        allocation_drawdown_usd=0,
                    ),
                    "proposed_order_usd": 100,
                },
                "invalid-risk-state",
            ),
        )

        for inputs, expected_veto in cases:
            with self.subTest(expected_veto=expected_veto):
                decision = evaluate_risk_gate(simulation_config=config, **inputs)
                self.assertEqual(decision["decision"], "blocked")
                self.assertIn(expected_veto, decision["vetoes"])
                json.dumps(decision, allow_nan=False)

        malformed_config = evaluate_risk_gate(
            simulation_config="not-an-object",
            proposed_order_usd=100,
        )
        self.assertEqual(malformed_config["decision"], "blocked")
        self.assertIn("invalid-simulation-config", malformed_config["vetoes"])

        empty_config = evaluate_risk_gate(
            simulation_config={},
            proposed_order_usd=100,
        )
        self.assertEqual(empty_config["decision"], "blocked")
        self.assertIn("invalid-simulation-config", empty_config["vetoes"])

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

    def test_reconciliation_malformed_dates_and_numbers_fail_closed(self):
        provider_snapshot = {
            "providerState": "known-read-only",
            "source": "synthetic",
            "providerCallStatus": "not-attempted",
        }
        invalid_date_cases = (
            ("not-a-date", "2026-05-15"),
            ("2026-02-30", "2026-05-15"),
            ("2026-05-01", "invalid-start"),
        )

        for last_seen, started_at in invalid_date_cases:
            with self.subTest(last_seen=last_seen, started_at=started_at):
                record = build_simulation_reconciliation_record(
                    provider_snapshot=provider_snapshot,
                    data_last_seen_date=last_seen,
                    started_at_date=started_at,
                    daily_loss_usd=0,
                    weekly_loss_usd=0,
                    allocation_drawdown_usd=0,
                )
                self.assertFalse(record["validation"]["ok"])
                self.assertEqual(record["reconciliationState"], "missing")
                self.assertEqual(record["riskInputState"]["data_freshness"], "unknown")
                self.assertIn(
                    "freshness dates must use valid ISO calendar dates",
                    record["validation"]["problems"],
                )

        malformed_number = build_simulation_reconciliation_record(
            provider_snapshot=provider_snapshot,
            data_freshness="fresh",
            max_data_age_days=True,
            daily_loss_usd=float("nan"),
            weekly_loss_usd=float("inf"),
            allocation_drawdown_usd=-1,
        )
        self.assertFalse(malformed_number["validation"]["ok"])
        self.assertEqual(malformed_number["reconciliationState"], "missing")
        self.assertIsNone(malformed_number["lossReconciliation"]["dailyLossUsd"])
        self.assertIsNone(malformed_number["lossReconciliation"]["weeklyLossUsd"])
        self.assertIsNone(malformed_number["lossReconciliation"]["allocationDrawdownUsd"])
        self.assertNotIn("NaN", json.dumps(malformed_number))
        self.assertNotIn("Infinity", json.dumps(malformed_number))

    def test_reconciliation_malformed_allocation_and_provider_values_fail_closed(self):
        provider_snapshot = {
            "providerState": "known-read-only",
            "source": "synthetic",
            "providerCallStatus": "not-attempted",
            "nested": {"invalidMetric": float("nan")},
        }

        for allocation_policy in ({}, "not-an-object"):
            with self.subTest(allocation_policy=allocation_policy):
                record = build_simulation_reconciliation_record(
                    allocation_policy=allocation_policy,
                    provider_snapshot=provider_snapshot,
                    data_freshness="fresh",
                    daily_loss_usd=0,
                    weekly_loss_usd=0,
                    allocation_drawdown_usd=0,
                )

                self.assertFalse(record["validation"]["ok"])
                self.assertEqual(record["reconciliationState"], "missing")
                self.assertEqual(
                    record["providerSnapshot"]["nested"]["invalidMetric"],
                    "redacted",
                )
                json.dumps(record, allow_nan=False)

        tuple_record = build_simulation_reconciliation_record(
            provider_snapshot={
                **provider_snapshot,
                "nested": ("safe", {"invalidMetric": float("-inf")}),
            },
            data_freshness="fresh",
            daily_loss_usd=0,
            weekly_loss_usd=0,
            allocation_drawdown_usd=0,
        )
        self.assertFalse(tuple_record["validation"]["ok"])
        self.assertEqual(tuple_record["reconciliationState"], "missing")
        self.assertEqual(
            tuple_record["providerSnapshot"]["nested"][1]["invalidMetric"],
            "redacted",
        )
        json.dumps(tuple_record, allow_nan=False)

    def test_streaming_market_history_parser_and_single_pass_summary(self):
        with FIXTURE_PATH.open("r", encoding="utf-8", newline="") as source:
            bars = iter_market_history_bars(source, selected_symbol="SPY")
            summary = summarize_market_history_bars(bars)

        self.assertEqual(summary["symbol"], "SPY")
        self.assertEqual(summary["source"], "synthetic-short-fixture")
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

        self.assertEqual(report["metadata"]["dataSource"], "synthetic-short-fixture")
        self.assertEqual(report["metadata"]["rowCount"], 3)
        self.assertEqual(report["periodDiagnostics"]["symbol"], "GLD")
        self.assertEqual(report["periodDiagnostics"]["providerCalls"], "blocked")

    def test_qqq_fixture_expands_offline_instrument_coverage(self):
        with QQQ_FIXTURE_PATH.open("r", encoding="utf-8", newline="") as source:
            report = build_historical_fixture_backtest(
                bars=iter_market_history_bars(source, selected_symbol="QQQ"),
                selected_instrument=SELECTED_QQQ,
                started_at=datetime.fromisoformat("2026-05-15T00:00:00+00:00"),
                input_sha256=sha256_file(QQQ_FIXTURE_PATH),
            )
        serialized = json.dumps(report)

        self.assertEqual(report["metadata"]["dataSource"], "synthetic-short-fixture")
        self.assertEqual(report["metadata"]["rowCount"], 3)
        self.assertEqual(report["metadata"]["firstDate"], "2026-05-11")
        self.assertEqual(report["metadata"]["lastDate"], "2026-05-13")
        self.assertEqual(report["metadata"]["maxFixtureRows"], 10000)
        self.assertEqual(report["metadata"]["inputSha256"], sha256_file(QQQ_FIXTURE_PATH))
        self.assertEqual(report["periodDiagnostics"]["symbol"], "QQQ")
        self.assertEqual(report["periodDiagnostics"]["providerCalls"], "blocked")
        self.assertEqual(report["periodDiagnostics"]["accountData"], "absent")
        self.assertEqual(report["periodDiagnostics"]["execution"], "blocked")
        for forbidden in ("apiKey", "accountId", "positionId", "orderId", "rawProvider", "winRate", "sharpe"):
            self.assertNotIn(forbidden, serialized)

    def test_offline_fixture_batch_diagnostics_aggregate_per_symbol_reports(self):
        reports = []
        for symbol, fixture_path, selected in (
            ("SPY", FIXTURE_PATH, SELECTED_SPY),
            ("GLD", GLD_FIXTURE_PATH, SELECTED_GLD),
            ("QQQ", QQQ_FIXTURE_PATH, SELECTED_QQQ),
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
        self.assertEqual(batch["coverage"]["fixtureCount"], 3)
        self.assertEqual(batch["coverage"]["totalRows"], 9)
        self.assertEqual(batch["summary"]["eventCount"], 9)
        self.assertEqual(batch["summary"]["blockedCount"], 9)
        self.assertEqual(batch["metadata"]["symbols"], ["GLD", "QQQ", "SPY"])
        self.assertEqual([item["symbol"] for item in batch["perSymbolDiagnostics"]], ["SPY", "GLD", "QQQ"])
        self.assertEqual(batch["perSymbolDiagnostics"][0]["inputSha256"], sha256_file(FIXTURE_PATH))
        self.assertEqual(batch["perSymbolDiagnostics"][0]["parserVersion"], "0.1.0-streaming-stdlib")
        self.assertEqual(batch["perSymbolDiagnostics"][0]["coverage"]["rowCount"], 3)
        self.assertEqual(batch["perSymbolDiagnostics"][0]["periodDiagnostics"]["providerCalls"], "blocked")
        self.assertEqual(
            batch["perSymbolDiagnostics"][0]["strategyHistoryDiagnostics"]["dtoVersion"],
            "strategy-history-diagnostics.v3",
        )
        self.assertEqual(batch["perSymbolDiagnostics"][0]["strategyHistoryDiagnostics"]["candidateIntent"], "skip")
        self.assertEqual(
            batch["perSymbolDiagnostics"][0]["strategyHistoryDiagnostics"]["walkForward"]["state"],
            "not-applicable",
        )
        self.assertEqual(batch["summary"]["strategyHistoryStateHistogram"], {"not-applicable": 3})
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

    def test_offline_fixture_batch_rejects_unsafe_strategy_history_diagnostics(self):
        with FIXTURE_PATH.open("r", encoding="utf-8", newline="") as source:
            report = build_historical_fixture_backtest(
                bars=iter_market_history_bars(source, selected_symbol="SPY"),
                selected_instrument=SELECTED_SPY,
                started_at=datetime.fromisoformat("2026-05-15T00:00:00+00:00"),
                input_sha256=sha256_file(FIXTURE_PATH),
            )

        unsafe = deepcopy(report)
        unsafe["strategyHistoryDiagnostics"]["providerCalls"] = "allowed"
        with self.assertRaisesRegex(ValueError, "strategy history diagnostics are invalid"):
            build_offline_fixture_batch_diagnostics(reports=[unsafe])

        unsafe = deepcopy(report)
        unsafe["strategyHistoryDiagnostics"]["metrics"] = {"accountId": 123}
        with self.assertRaisesRegex(ValueError, "strategy history metrics are invalid"):
            build_offline_fixture_batch_diagnostics(reports=[unsafe])

        unsafe = deepcopy(report)
        unsafe["strategyHistoryDiagnostics"]["walkForward"]["providerCalls"] = "allowed"
        with self.assertRaisesRegex(ValueError, "walk-forward diagnostics are invalid"):
            build_offline_fixture_batch_diagnostics(reports=[unsafe])

        unsafe = deepcopy(report)
        unsafe["strategyHistoryDiagnostics"]["walkForward"]["eligibleObservationCount"] = 1
        with self.assertRaisesRegex(ValueError, "walk-forward diagnostics are invalid"):
            build_offline_fixture_batch_diagnostics(reports=[unsafe])

        volatility_report = build_historical_fixture_backtest(
            bars=[
                Bar("SPY", f"2026-05-{11 + index:02d}", close, close, close, close, 1000.0, "synthetic-test-fixture")
                for index, close in enumerate([100.0, 102.0, 101.0, 99.0, 96.0])
            ],
            strategy_id="volatility-band-accumulator",
            selected_instrument=SELECTED_SPY,
            strategy_parameters={"lookbackDays": 5, "dropTriggerPct": 3.0},
            started_at=datetime.fromisoformat("2026-05-15T00:00:00+00:00"),
        )
        unsafe = deepcopy(volatility_report)
        unsafe["strategyHistoryDiagnostics"]["walkForward"]["stateCounts"] = {"no-trigger-observed": 1}
        with self.assertRaisesRegex(ValueError, "walk-forward diagnostics are invalid"):
            build_offline_fixture_batch_diagnostics(reports=[unsafe])

        unsafe = deepcopy(volatility_report)
        unsafe["strategyHistoryDiagnostics"]["metrics"]["dropTriggerPct"] = 2.5
        with self.assertRaisesRegex(ValueError, "strategy history diagnostics are invalid"):
            build_offline_fixture_batch_diagnostics(reports=[unsafe])

        unsafe = deepcopy(volatility_report)
        unsafe["strategyHistoryDiagnostics"]["walkForward"]["foldCount"] = 0
        unsafe["strategyHistoryDiagnostics"]["walkForward"]["folds"] = []
        with self.assertRaisesRegex(ValueError, "walk-forward diagnostics are invalid"):
            build_offline_fixture_batch_diagnostics(reports=[unsafe])

        unsafe = deepcopy(volatility_report)
        unsafe["strategyHistoryDiagnostics"]["walkForward"]["folds"][0]["foldIndex"] = 1
        with self.assertRaisesRegex(ValueError, "walk-forward diagnostics are invalid"):
            build_offline_fixture_batch_diagnostics(reports=[unsafe])

        unsafe = deepcopy(volatility_report)
        unsafe["strategyHistoryDiagnostics"]["walkForward"]["folds"][0]["foldIndex"] = False
        with self.assertRaisesRegex(ValueError, "walk-forward diagnostics are invalid"):
            build_offline_fixture_batch_diagnostics(reports=[unsafe])

        unsafe = deepcopy(volatility_report)
        unsafe["strategyHistoryDiagnostics"]["walkForward"]["folds"][0]["firstObservationDate"] = (
            "2026-05-14"
        )
        with self.assertRaisesRegex(ValueError, "walk-forward diagnostics are invalid"):
            build_offline_fixture_batch_diagnostics(reports=[unsafe])

    def test_offline_fixture_batch_rejects_tampered_multi_fold_walk_forward_diagnostics(self):
        report = build_historical_fixture_backtest(
            bars=[
                Bar(
                    "SPY",
                    (datetime(2026, 5, 7) + timedelta(days=index * 2)).date().isoformat(),
                    close,
                    close,
                    close,
                    close,
                    1000.0,
                    "synthetic-test-fixture",
                )
                for index, close in enumerate(
                    [100.0, 100.0, 100.0, 100.0, 100.0, 95.0, 92.0, 97.0, 100.0, 101.0, 102.0]
                )
            ],
            strategy_id="volatility-band-accumulator",
            selected_instrument=SELECTED_SPY,
            strategy_parameters={"lookbackDays": 5, "dropTriggerPct": 3.0},
            started_at=datetime.fromisoformat("2026-05-15T00:00:00+00:00"),
        )
        walk_forward = report["strategyHistoryDiagnostics"]["walkForward"]
        self.assertEqual(walk_forward["foldCount"], 5)

        unsafe = deepcopy(report)
        unsafe["strategyHistoryDiagnostics"]["walkForward"]["folds"][0]["stateCounts"] = {
            "trigger-observed": 1
        }
        with self.assertRaisesRegex(ValueError, "walk-forward diagnostics are invalid"):
            build_offline_fixture_batch_diagnostics(reports=[unsafe])

        unsafe = deepcopy(report)
        unsafe["strategyHistoryDiagnostics"]["walkForward"]["folds"][0]["lastObservationDate"] = (
            walk_forward["folds"][0]["firstObservationDate"]
        )
        with self.assertRaisesRegex(ValueError, "walk-forward diagnostics are invalid"):
            build_offline_fixture_batch_diagnostics(reports=[unsafe])

        unsafe = deepcopy(report)
        unsafe["strategyHistoryDiagnostics"]["walkForward"]["folds"][1]["firstObservationDate"] = (
            walk_forward["folds"][0]["lastObservationDate"]
        )
        with self.assertRaisesRegex(ValueError, "walk-forward diagnostics are invalid"):
            build_offline_fixture_batch_diagnostics(reports=[unsafe])

        unsafe = deepcopy(report)
        unsafe["strategyHistoryDiagnostics"]["walkForward"]["folds"][1]["firstObservationDate"] = (
            datetime.fromisoformat(walk_forward["folds"][1]["firstObservationDate"])
            - timedelta(days=1)
        ).date().isoformat()
        with self.assertRaisesRegex(ValueError, "walk-forward diagnostics are invalid"):
            build_offline_fixture_batch_diagnostics(reports=[unsafe])

        unsafe = deepcopy(report)
        unsafe["strategyHistoryDiagnostics"]["walkForward"]["transitionCount"] += 1
        with self.assertRaisesRegex(ValueError, "walk-forward diagnostics are invalid"):
            build_offline_fixture_batch_diagnostics(reports=[unsafe])

        unsafe = deepcopy(report)
        unsafe_walk_forward = unsafe["strategyHistoryDiagnostics"]["walkForward"]
        unsafe_walk_forward["folds"][-1]["stateCounts"] = {"recovery-observed": 1}
        unsafe_walk_forward["stateCounts"] = {
            "no-trigger-observed": 1,
            "recovery-observed": 3,
            "trigger-observed": 3,
        }
        with self.assertRaisesRegex(ValueError, "walk-forward diagnostics are invalid"):
            build_offline_fixture_batch_diagnostics(reports=[unsafe])

        unsafe = deepcopy(report)
        unsafe_walk_forward = unsafe["strategyHistoryDiagnostics"]["walkForward"]
        self.assertEqual(unsafe_walk_forward["folds"][1]["stateCounts"], {"trigger-observed": 2})
        unsafe_walk_forward["folds"][1]["transitionCount"] = 1
        unsafe_walk_forward["transitionCount"] += 1
        with self.assertRaisesRegex(ValueError, "walk-forward diagnostics are invalid"):
            build_offline_fixture_batch_diagnostics(reports=[unsafe])

    def test_offline_fixture_batch_preserves_invalid_defaulted_parameter_boundary(self):
        report = build_historical_fixture_backtest(
            bars=[
                Bar(
                    "SPY",
                    f"2026-05-{index + 1:02d}",
                    100.0,
                    100.0,
                    100.0,
                    100.0,
                    1000.0,
                    "synthetic-test-fixture",
                )
                for index in range(20)
            ],
            strategy_id="volatility-band-accumulator",
            selected_instrument=SELECTED_SPY,
            strategy_parameters={"lookbackDays": "bad", "dropTriggerPct": float("nan")},
            started_at=datetime.fromisoformat("2026-05-21T00:00:00+00:00"),
        )

        batch = build_offline_fixture_batch_diagnostics(reports=[report])

        diagnostics = batch["perSymbolDiagnostics"][0]["strategyHistoryDiagnostics"]
        self.assertEqual(diagnostics["parameterState"], "invalid-defaulted")
        self.assertEqual(diagnostics["requiredBarCount"], 20)
        self.assertEqual(diagnostics["walkForward"]["foldCount"], 1)

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
        self.assertEqual(report["metadata"]["dataSource"], "synthetic-short-fixture")
        self.assertEqual(report["metadata"]["firstDate"], "2026-05-11")
        self.assertEqual(report["metadata"]["lastDate"], "2026-05-13")
        self.assertEqual(report["metadata"]["rowCount"], 3)
        self.assertEqual(report["metadata"]["maxFixtureRows"], 10000)
        self.assertEqual(report["metadata"]["inputSha256"], input_sha)
        self.assertEqual(report["periodDiagnostics"]["dtoVersion"], "market-period-diagnostics.v1")
        self.assertEqual(report["strategyHistoryDiagnostics"]["dtoVersion"], "strategy-history-diagnostics.v3")
        self.assertEqual(report["strategyHistoryDiagnostics"]["state"], "not-applicable")
        self.assertEqual(report["strategyHistoryDiagnostics"]["candidateIntent"], "skip")
        self.assertEqual(report["strategyHistoryDiagnostics"]["providerCalls"], "blocked")
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

    def test_invalid_strategy_parameters_are_not_echoed_in_run_or_backtest_output(self):
        run = build_simulation_run(
            strategy_id="dca-cash-reserve",
            simulation_config={
                "strategyParameters": {
                    "fixedOrderUsd": 125.0,
                    "apiKey": "api-secret-abcdef12",
                }
            },
        )
        report = build_synthetic_backtest(
            scenarios=[
                {
                    "scenarioId": "unsafe-params",
                    "strategyId": "dca-cash-reserve",
                    "simulationConfig": {
                        "strategyParameters": {
                            "fixedOrderUsd": 125.0,
                            "apiKey": "api-secret-abcdef12",
                        }
                    },
                }
            ]
        )
        serialized_run = json.dumps(run, allow_nan=False)
        serialized_report = json.dumps(report, allow_nan=False)

        self.assertIn("unsupported strategy parameters", " ".join(run["configValidation"]["errors"]))
        self.assertEqual(run["simulationConfig"]["strategyParameters"]["fixedOrderUsd"], 100.0)
        self.assertEqual(report["runs"][0]["intentDiagnostics"]["strategyParameters"]["fixedOrderUsd"], 100.0)
        self.assertEqual(report["runs"][0]["intentDiagnostics"]["candidateOrderUsd"], 100.0)
        for serialized in (serialized_run, serialized_report):
            self.assertNotIn("apiKey", serialized)
            self.assertNotIn("api-secret-abcdef12", serialized)

    def test_invalid_budget_and_allocation_numbers_are_not_echoed_in_run_output(self):
        run = build_simulation_run(
            strategy_id="dca-cash-reserve",
            simulation_config={"budgetUsd": float("nan")},
            allocation_policy=build_allocation_policy(
                bot_allocation_usd=float("nan"),
                reserved_usd=100.0,
                max_order_usd=float("inf"),
            ),
        )
        serialized = json.dumps(run, allow_nan=False)

        self.assertIn("budget must be a positive finite USD amount", " ".join(run["configValidation"]["errors"]))
        self.assertEqual(run["simulationConfig"]["budgetUsd"], 1000.0)
        self.assertEqual(run["budget"]["remainingUsd"], 1000.0)
        self.assertEqual(run["allocation"]["botAllocationUsd"], 1000.0)
        self.assertEqual(run["allocation"]["maxOrderUsd"], 250.0)
        self.assertNotIn("NaN", serialized)
        self.assertNotIn("Infinity", serialized)

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

    def test_ledger_report_redacts_manually_edited_legacy_records(self):
        legacy_record = {
            "ledgerVersion": 1,
            "recordedAt": "2026-05-15T00:00:00.000Z",
            "correlationId": "corr-manual-1",
            "runId": "run-manual-1",
            "mode": "live",
            "environment": "provider",
            "strategyId": "dca-cash-reserve",
            "strategyVersion": "0.1.0-sim",
            "configVersion": "cfg",
            "configHash": "hash",
            "allocationId": "alloc-sim-default",
            "strategyAllocationId": "alloc-sim-dca",
            "decision": "skip",
            "riskResult": "blocked",
            "riskDecision": "blocked",
            "vetoes": ["execution-route-absent", "manual note for acct-real-123"],
            "dataFreshness": "fresh",
            "providerCallStatus": "attempted",
            "executionRoute": "demo",
            "tradeLogEntry": {
                "action": "simulated-skip",
                "reasonCode": "provider-not-connected",
                "accountId": "acct-real-123",
                "apiKey": "api-secret-abcdef12",
                "note": "operator@example.test",
                "providerCall": "attempted",
                "executionRoute": "demo",
            },
            "rawProviderPayload": {"token": "token-secret-abcdef12"},
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            ledger_path = Path(temp_dir) / "legacy-ledger.jsonl"
            ledger_path.write_text(json.dumps(legacy_record) + "\n", encoding="utf-8")
            ledger_report = export_ledger_report(ledger_path)
        serialized = json.dumps(ledger_report, sort_keys=True)

        self.assertEqual(ledger_report["environment"], "synthetic")
        self.assertEqual(ledger_report["providerCalls"], "blocked")
        self.assertEqual(ledger_report["executionRoutes"], "absent")
        self.assertEqual(ledger_report["records"][0]["mode"], "simulation")
        self.assertEqual(ledger_report["records"][0]["environment"], "synthetic")
        self.assertEqual(ledger_report["records"][0]["providerCallStatus"], "not-attempted")
        self.assertEqual(ledger_report["records"][0]["executionRoute"], "absent")
        self.assertEqual(ledger_report["records"][0]["tradeLog"]["accountIdentifiers"], "redacted")
        self.assertEqual(ledger_report["records"][0]["tradeLog"]["rawProviderPayloads"], "absent")
        self.assertEqual(ledger_report["records"][0]["tradeLog"]["providerCall"], "not-attempted")
        self.assertEqual(ledger_report["records"][0]["tradeLog"]["executionRoute"], "absent")
        self.assertNotIn("acct-real-123", serialized)
        self.assertNotIn("operator@example.test", serialized)
        self.assertNotIn("api-secret-abcdef12", serialized)
        self.assertNotIn("token-secret-abcdef12", serialized)

    def test_ledger_integrity_recovery_reports_corruption_without_mutating_source(self):
        valid_record = build_synthetic_backtest(include_ledger_records=True)["ledgerRecords"][0]
        unsafe_legacy = {
            "ledgerVersion": 1,
            "strategyId": "dca-cash-reserve",
            "decision": "skip",
            "riskResult": "blocked",
            "accountEmail": "private@example.com",
            "tradeLogEntry": {"action": "simulated-skip", "reasonCode": "blocked"},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger_path = Path(temp_dir) / "mixed-ledger.jsonl"
            contents = (
                json.dumps(valid_record) + "\n" +
                "{not-json}\n" +
                "[1,2,3]\n" +
                json.dumps(unsafe_legacy) + "\n"
            )
            ledger_path.write_text(contents, encoding="utf-8")
            recovered = read_ledger_records_with_integrity(ledger_path)
            ledger_report = export_ledger_report(ledger_path)
            unchanged = ledger_path.read_text(encoding="utf-8")

        self.assertEqual(unchanged, contents)
        self.assertEqual(recovered["integrity"]["state"], "corrupted")
        self.assertFalse(recovered["integrity"]["complete"])
        self.assertEqual(recovered["integrity"]["nonemptyLineCount"], 4)
        self.assertEqual(recovered["integrity"]["acceptedRecordCount"], 2)
        self.assertEqual(recovered["integrity"]["rejectedRecordCount"], 2)
        self.assertEqual(recovered["integrity"]["warningCount"], 2)
        self.assertEqual(recovered["integrity"]["errorCount"], 2)
        self.assertEqual(recovered["integrity"]["sourceMutation"], "not-attempted")
        self.assertEqual(
            [issue["code"] for issue in recovered["integrity"]["issues"]],
            ["invalid-json", "invalid-record-shape", "legacy-record-normalized", "sensitive-record-redacted"],
        )
        self.assertTrue(all(issue["rawContent"] == "absent" for issue in recovered["integrity"]["issues"]))
        self.assertEqual(ledger_report["integrity"], recovered["integrity"])
        recovered_serialized = json.dumps(recovered).lower()
        serialized = json.dumps(ledger_report).lower()
        self.assertNotIn("private@example.com", recovered_serialized)
        self.assertNotIn("private@example.com", serialized)
        self.assertNotIn("not-json", serialized)

    def test_ledger_integrity_rejects_invalid_v2_record(self):
        invalid_record = {
            **build_synthetic_backtest(include_ledger_records=True)["ledgerRecords"][0],
            "unexpectedField": "unsafe",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger_path = Path(temp_dir) / "invalid-v2.jsonl"
            ledger_path.write_text(json.dumps(invalid_record) + "\n", encoding="utf-8")
            recovered = read_ledger_records_with_integrity(ledger_path)

        self.assertEqual(recovered["records"], [])
        self.assertEqual(recovered["integrity"]["state"], "corrupted")
        self.assertEqual(recovered["integrity"]["issues"][0]["code"], "invalid-audit-record")

    def test_ledger_integrity_rejects_oversized_and_invalid_utf8_records(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger_path = Path(temp_dir) / "binary-corruption.jsonl"
            contents = b"x" * (MAX_LEDGER_LINE_BYTES + 1) + b"\n\xff\n"
            ledger_path.write_bytes(contents)
            recovered = read_ledger_records_with_integrity(ledger_path)
            unchanged = ledger_path.read_bytes()

        self.assertEqual(unchanged, contents)
        self.assertEqual(recovered["records"], [])
        self.assertEqual(recovered["integrity"]["nonemptyLineCount"], 2)
        self.assertEqual(recovered["integrity"]["rejectedRecordCount"], 2)
        self.assertEqual(
            [issue["code"] for issue in recovered["integrity"]["issues"]],
            ["oversized-record", "invalid-utf8"],
        )

    def test_ledger_report_rejects_caller_supplied_integrity_metadata(self):
        malicious_integrity = {
            "state": "clean",
            "complete": True,
            "nonemptyLineCount": 0,
            "acceptedRecordCount": 0,
            "rejectedRecordCount": 0,
            "warningCount": 0,
            "errorCount": 0,
            "issues": [],
            "sourceMutation": "not-attempted",
            "operatorSecret": "api-secret-abcdef12",
        }

        with self.assertRaisesRegex(ValueError, "ledger integrity metadata is invalid"):
            build_ledger_report(records=[], integrity=malicious_integrity)

    def test_ledger_report_normalizes_unsafe_legacy_state_fields(self):
        report = build_ledger_report(
            records=[
                {
                    "ledgerVersion": 1,
                    "recordedAt": "2026-05-15T00:00:00.000Z",
                    "correlationId": "corr-manual-unsafe-state",
                    "runId": "run-manual-unsafe-state",
                    "strategyId": "operator-alpha",
                    "strategyVersion": "0.1.0-sim",
                    "configVersion": "cfg",
                    "configHash": "hash",
                    "allocationId": "alloc-sim-default",
                    "strategyAllocationId": "alloc-sim-dca",
                    "decision": "buy",
                    "riskResult": "approved",
                    "riskDecision": "allowed",
                    "vetoes": ["execution-route-absent", "guaranteed-profit", "win-rate-100"],
                    "dataFreshness": "guaranteed-profit",
                    "tradeLogEntry": {
                        "action": "buy",
                        "reasonCode": "guaranteed-profit",
                        "budgetRemainingUsd": "win-rate-100",
                        "riskDecision": "allowed",
                        "providerCall": "attempted",
                        "executionRoute": "demo",
                    },
                }
            ]
        )
        serialized = json.dumps(report, sort_keys=True)
        record = report["records"][0]
        summary = report["summary"]

        self.assertEqual(record["decision"], "skip")
        self.assertEqual(record["riskResult"], "blocked")
        self.assertEqual(record["riskDecision"], "blocked")
        self.assertEqual(record["vetoes"], ["execution-route-absent"])
        self.assertEqual(record["dataFreshness"], "unknown")
        self.assertEqual(record["strategyId"], None)
        self.assertEqual(record["tradeLog"]["action"], "simulated-skip")
        self.assertEqual(record["tradeLog"]["reasonCode"], "blocked")
        self.assertEqual(record["tradeLog"]["budgetRemainingUsd"], None)
        self.assertEqual(record["tradeLog"]["riskDecision"], "blocked")
        self.assertEqual(summary["skipCount"], 1)
        self.assertEqual(summary["blockedCount"], 1)
        self.assertEqual(summary["strategyIds"], [])
        self.assertEqual(summary["decisionHistogram"], {"skip": 1})
        self.assertEqual(summary["riskResultHistogram"], {"blocked": 1})
        self.assertEqual(summary["vetoHistogram"], {"execution-route-absent": 1})
        for unsafe in ("buy", "approved", "allowed", "guaranteed-profit", "win-rate-100", "operator-alpha"):
            self.assertNotIn(unsafe, serialized)

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
