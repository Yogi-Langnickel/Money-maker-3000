from __future__ import annotations

import argparse
import cProfile
import json
import pstats
import sys
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
from typing import Any, Callable

from money_maker_3000.backtest import (
    DEFAULT_MAX_FIXTURE_ROWS,
    build_historical_fixture_backtest,
    build_offline_fixture_batch_diagnostics,
    build_synthetic_backtest,
)
from money_maker_3000.contracts import build_allocation_policy, validate_run_mode, validate_strategy_parameters
from money_maker_3000.ledger import export_ledger_report
from money_maker_3000.market_history import iter_market_history_bars, sha256_file
from money_maker_3000.offline_runner import run_once
from money_maker_3000.operations import (
    build_operations_status,
    create_snapshot,
    install_launchd_agent,
    run_recovery_drill,
    run_soak_evidence,
    verify_restore,
)
from money_maker_3000.readiness import (
    FixtureReadinessSpec,
    build_allocation_policy_for_readiness,
    build_backtest_readiness_report,
)
from money_maker_3000.worker_leases import build_worker_lease_report


def _parse_started_at(raw: str | None) -> datetime:
    if not raw:
        return datetime.fromisoformat("2026-05-15T00:00:00+00:00")
    normalized = raw.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def _parse_observed_at(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("observed-at must include an explicit timezone")
    return parsed


def _reject_execution_mode(run_mode: str) -> None:
    result = validate_run_mode(run_mode)
    if not result.ok:
        raise ValueError(result.errors[0])


class _CommandArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # Subparser conversion/required-argument failures happen before our handler.
        if self.prog in {"money-maker-3000 learning-train", "money-maker-3000 learning-predict"}:
            message = "invalid-learning-arguments"
        super().error(message)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="money-maker-3000", description="Python-first simulation worker CLI")
    parser.add_argument("--profile", metavar="PATH", help="write cProfile stats for this CLI run")
    subparsers = parser.add_subparsers(dest="command", required=True, parser_class=_CommandArgumentParser)

    backtest = subparsers.add_parser("backtest", help="run a deterministic diagnostics-only backtest")
    backtest.add_argument("--mode", default="backtest", help="must be backtest; execute/trade are rejected")
    backtest.add_argument("--strategy", "--strategy-id", dest="strategy_id", default="dca-cash-reserve")
    backtest.add_argument("--symbol", default="SPY")
    backtest.add_argument("--market", default="US_EQUITIES")
    backtest.add_argument("--instrument-class", default="ETF")
    backtest.add_argument("--budget-usd", type=float, default=1000.0)
    backtest.add_argument("--bot-allocation-usd", type=float, default=1000.0)
    backtest.add_argument("--reserved-usd", type=float, default=100.0)
    backtest.add_argument("--max-order-usd", type=float, default=250.0)
    backtest.add_argument("--provider-demo-balance-usd", type=float)
    backtest.add_argument("--strategy-params-json", help="JSON object of allowlisted parameters for the predefined strategy")
    backtest.add_argument("--history-csv", type=Path, help="offline market-history CSV fixture")
    backtest.add_argument("--max-fixture-rows", type=int, default=DEFAULT_MAX_FIXTURE_ROWS)
    backtest.add_argument("--started-at", help="explicit ISO timestamp for deterministic metadata")

    fixture_batch = subparsers.add_parser(
        "fixture-batch",
        help="run diagnostics across offline market-history fixtures",
    )
    fixture_batch.add_argument("--mode", default="backtest", help="must be backtest; execute/trade are rejected")
    fixture_batch.add_argument("--manifest", type=Path, help="JSON manifest with a fixtures array")
    fixture_batch.add_argument(
        "--fixture",
        action="append",
        default=[],
        metavar="SYMBOL=PATH",
        help="offline fixture entry; may be repeated",
    )
    fixture_batch.add_argument("--strategy", "--strategy-id", dest="strategy_id", default="dca-cash-reserve")
    fixture_batch.add_argument("--market", default="US_EQUITIES")
    fixture_batch.add_argument("--instrument-class", default="ETF")
    fixture_batch.add_argument("--budget-usd", type=float, default=1000.0)
    fixture_batch.add_argument("--bot-allocation-usd", type=float, default=1000.0)
    fixture_batch.add_argument("--reserved-usd", type=float, default=100.0)
    fixture_batch.add_argument("--max-order-usd", type=float, default=250.0)
    fixture_batch.add_argument("--provider-demo-balance-usd", type=float)
    fixture_batch.add_argument("--strategy-params-json", help="JSON object of allowlisted parameters for the predefined strategy")
    fixture_batch.add_argument("--max-fixture-rows", type=int, default=DEFAULT_MAX_FIXTURE_ROWS)
    fixture_batch.add_argument("--started-at", help="explicit ISO timestamp for deterministic metadata")

    readiness = subparsers.add_parser(
        "readiness",
        help="verify offline backtest readiness without provider calls or execution",
    )
    readiness.add_argument("--mode", default="backtest", help="must be backtest; execute/trade are rejected")
    readiness.add_argument("--manifest", type=Path, help="JSON manifest with a fixtures array")
    readiness.add_argument(
        "--fixture",
        action="append",
        default=[],
        metavar="SYMBOL=PATH",
        help="offline fixture entry; may be repeated",
    )
    readiness.add_argument("--strategy", "--strategy-id", dest="strategy_id", default="dca-cash-reserve")
    readiness.add_argument("--market", default="US_EQUITIES")
    readiness.add_argument("--instrument-class", default="ETF")
    readiness.add_argument("--budget-usd", type=float, default=1000.0)
    readiness.add_argument("--bot-allocation-usd", type=float, default=1000.0)
    readiness.add_argument("--reserved-usd", type=float, default=100.0)
    readiness.add_argument("--max-order-usd", type=float, default=250.0)
    readiness.add_argument("--provider-demo-balance-usd", type=float)
    readiness.add_argument("--strategy-params-json", help="JSON object of allowlisted parameters for the predefined strategy")
    readiness.add_argument("--max-fixture-rows", type=int, default=DEFAULT_MAX_FIXTURE_ROWS)
    readiness.add_argument("--started-at", help="explicit ISO timestamp for deterministic metadata")

    ledger_report = subparsers.add_parser("ledger-report", help="export a redacted JSONL ledger report")
    ledger_report.add_argument("ledger_path", type=Path)
    ledger_report.add_argument("--mode", default="backtest", help="must be backtest; execute/trade are rejected")

    lease_report = subparsers.add_parser(
        "lease-report",
        help="inspect a local simulation worker lease without changing it",
    )
    lease_report.add_argument("state_path", type=Path)
    lease_report.add_argument("--observed-at", help="timezone-aware ISO timestamp; defaults to current UTC time")
    lease_report.add_argument("--lock-wait-seconds", type=float, default=1.0)
    lease_report.add_argument("--mode", default="backtest", help="must be backtest; execute/trade are rejected")

    run_once = subparsers.add_parser(
        "run-once",
        help="run the one approved offline simulation manifest once with a fenced lease",
    )
    run_once.add_argument("--manifest", type=Path, required=True, help="the committed allowlisted runner manifest")
    run_once.add_argument("--state-path", type=Path, required=True, help="local fenced lease-state path")
    run_once.add_argument("--ledger-path", type=Path, required=True, help="local redacted v2 ledger path")
    run_once.add_argument("--holder", default="offline-simulation-scheduler", help="opaque local scheduler holder id")
    run_once.add_argument("--idempotency-key", required=True, help="stable opaque key for this scheduled occurrence")
    run_once.add_argument("--started-at", required=True, help="explicit ISO timestamp for deterministic replay evidence")
    run_once.add_argument("--mode", default="backtest", help="must be backtest; execute/trade are rejected")

    operations = subparsers.add_parser("operations-status", help="inspect offline appliance freshness without mutation")
    operations.add_argument("--state-path", type=Path, required=True)
    operations.add_argument("--ledger-path", type=Path, required=True)
    operations.add_argument("--max-age-hours", type=int, default=48)
    operations.add_argument("--observed-at", required=True)
    operations.add_argument("--mode", default="backtest")

    snapshot = subparsers.add_parser("operations-snapshot", help="atomically snapshot clean local offline state")
    snapshot.add_argument("--state-path", type=Path, required=True)
    snapshot.add_argument("--ledger-path", type=Path, required=True)
    snapshot.add_argument("--snapshot-root", type=Path, required=True)
    snapshot.add_argument("--retain", type=int, default=30)
    snapshot.add_argument("--created-at", required=True)
    snapshot.add_argument("--mode", default="backtest")

    soak = subparsers.add_parser("operations-soak-evidence", help="run bounded deterministic offline soak evidence")
    soak.add_argument("--manifest", type=Path, required=True)
    soak.add_argument("--days", type=int, default=30)
    soak.add_argument("--started-at", required=True)
    soak.add_argument("--mode", default="backtest")

    restore = subparsers.add_parser("operations-restore-verify", help="verify a snapshot only into a new isolated directory")
    restore.add_argument("--snapshot-root", type=Path, required=True)
    restore.add_argument("--snapshot-id", required=True)
    restore.add_argument("--verification-root", type=Path, required=True)
    restore.add_argument("--observed-at", required=True)
    restore.add_argument("--mode", default="backtest")

    recovery = subparsers.add_parser("operations-recovery-drill", help="prove isolated crash-after-append recovery")
    recovery.add_argument("--manifest", type=Path, required=True)
    recovery.add_argument("--started-at", required=True)
    recovery.add_argument("--mode", default="backtest")

    install = subparsers.add_parser("operations-install-launchd", help="explicitly install the offline-only launchd agent")
    install.add_argument("--repository-root", type=Path, required=True)
    install.add_argument("--launch-agents-dir", type=Path, required=True)
    install.add_argument("--confirm-install", action="store_true")
    install.add_argument("--mode", default="backtest")
    learning_train = subparsers.add_parser("learning-train", help="fit an offline diagnostic probability model")
    learning_train.add_argument("--history-csv", type=Path, required=True)
    learning_train.add_argument("--dataset-manifest", type=Path, required=True)
    learning_train.add_argument("--strategy", default="volatility-band-accumulator")
    learning_train.add_argument("--horizon-bars", type=int, default=5)
    learning_train.add_argument("--train-end", required=True)
    learning_train.add_argument("--validation-end", required=True)
    learning_train.add_argument("--output", type=Path, required=True)
    learning_train.add_argument("--allow-synthetic-smoke", action="store_true")
    learning_predict = subparsers.add_parser("learning-predict", help="inspect a frozen offline model probability")
    learning_predict.add_argument("--model", type=Path, required=True)
    learning_predict.add_argument("--history-csv", type=Path, required=True)
    learning_predict.add_argument("--dataset-manifest", type=Path, required=True)
    learning_predict.add_argument("--allow-synthetic-smoke", action="store_true")
    return parser


def run_backtest(args: argparse.Namespace) -> dict[str, Any]:
    _reject_execution_mode(args.mode)
    _validate_backtest_numeric_args(args)
    selected_instrument = {
        "symbol": args.symbol,
        "market": args.market,
        "instrumentClass": args.instrument_class,
    }
    allocation = build_allocation_policy(
        bot_allocation_usd=args.bot_allocation_usd,
        reserved_usd=args.reserved_usd,
        max_order_usd=args.max_order_usd,
        provider_demo_balance_usd=args.provider_demo_balance_usd,
    )
    started_at = _parse_started_at(args.started_at)
    strategy_parameters = _parse_strategy_params_json(args.strategy_params_json)
    _validate_strategy_parameters_for_cli(args.strategy_id, strategy_parameters)
    if args.history_csv:
        input_sha256 = sha256_file(args.history_csv)
        with args.history_csv.open("r", encoding="utf-8", newline="") as source:
            bars = iter_market_history_bars(source, selected_symbol=args.symbol)
            return build_historical_fixture_backtest(
                bars=bars,
                strategy_id=args.strategy_id,
                selected_instrument=selected_instrument,
                strategy_parameters=strategy_parameters,
                budget_usd=args.budget_usd,
                allocation_policy=allocation,
                started_at=started_at,
                input_sha256=input_sha256,
                max_fixture_rows=args.max_fixture_rows,
            )
    return build_synthetic_backtest(
        scenarios=[
            {
                "scenarioId": f"{args.strategy_id}-{args.symbol}-cli",
                "strategyId": args.strategy_id,
                "budgetUsd": args.budget_usd,
                "simulationConfig": {
                    "runMode": "backtest",
                    "selectedInstrument": selected_instrument,
                    **({"strategyParameters": strategy_parameters} if strategy_parameters else {}),
                },
                "allocationPolicy": allocation,
            }
        ],
        started_at=started_at,
    )


def run_fixture_batch(args: argparse.Namespace) -> dict[str, Any]:
    _reject_execution_mode(args.mode)
    _validate_backtest_numeric_args(args)
    started_at = _parse_started_at(args.started_at)
    allocation = build_allocation_policy(
        bot_allocation_usd=args.bot_allocation_usd,
        reserved_usd=args.reserved_usd,
        max_order_usd=args.max_order_usd,
        provider_demo_balance_usd=args.provider_demo_balance_usd,
    )
    strategy_parameters = _parse_strategy_params_json(args.strategy_params_json)
    entries = _load_fixture_batch_entries(args)
    reports = []
    for entry in entries:
        symbol = str(entry["symbol"]).strip().upper()
        fixture_path = Path(entry["path"])
        selected_instrument = {
            "symbol": symbol,
            "market": str(entry.get("market", args.market)),
            "instrumentClass": str(entry.get("instrumentClass", args.instrument_class)),
        }
        entry_strategy_id = str(entry.get("strategyId", args.strategy_id))
        entry_strategy_parameters = _entry_strategy_parameters(entry, strategy_parameters)
        _validate_strategy_parameters_for_cli(entry_strategy_id, entry_strategy_parameters)
        input_sha256 = sha256_file(fixture_path)
        with fixture_path.open("r", encoding="utf-8", newline="") as source:
            bars = iter_market_history_bars(source, selected_symbol=symbol)
            reports.append(
                build_historical_fixture_backtest(
                    bars=bars,
                    strategy_id=entry_strategy_id,
                    selected_instrument=selected_instrument,
                    strategy_parameters=entry_strategy_parameters,
                    budget_usd=float(entry.get("budgetUsd", args.budget_usd)),
                    allocation_policy=allocation,
                    started_at=started_at,
                    input_sha256=input_sha256,
                    max_fixture_rows=int(entry.get("maxFixtureRows", args.max_fixture_rows)),
                )
            )
    return build_offline_fixture_batch_diagnostics(reports=reports, started_at=started_at)


def run_readiness(args: argparse.Namespace) -> dict[str, Any]:
    _reject_execution_mode(args.mode)
    _validate_backtest_numeric_args(args)
    started_at = _parse_started_at(args.started_at)
    strategy_parameters = _parse_strategy_params_json(args.strategy_params_json)
    entries = _load_fixture_batch_entries(args)
    specs = []
    for entry in entries:
        entry_strategy_parameters = _entry_strategy_parameters(entry, strategy_parameters)
        specs.append(
            FixtureReadinessSpec(
                symbol=str(entry["symbol"]),
                path=Path(entry["path"]),
                market=str(entry.get("market", args.market)),
                instrument_class=str(entry.get("instrumentClass", args.instrument_class)),
                strategy_id=str(entry.get("strategyId", args.strategy_id)),
                budget_usd=float(entry.get("budgetUsd", args.budget_usd)),
                max_fixture_rows=int(entry.get("maxFixtureRows", args.max_fixture_rows)),
                strategy_parameters=entry_strategy_parameters,
            )
        )
    allocation = build_allocation_policy_for_readiness(
        bot_allocation_usd=args.bot_allocation_usd,
        reserved_usd=args.reserved_usd,
        max_order_usd=args.max_order_usd,
        provider_demo_balance_usd=args.provider_demo_balance_usd,
    )
    return build_backtest_readiness_report(
        fixture_specs=specs,
        started_at=started_at,
        allocation_policy=allocation,
    )


def run_ledger_report(args: argparse.Namespace) -> dict[str, Any]:
    _reject_execution_mode(args.mode)
    return export_ledger_report(args.ledger_path)


def run_lease_report(args: argparse.Namespace) -> dict[str, Any]:
    _reject_execution_mode(args.mode)
    return build_worker_lease_report(
        args.state_path,
        observed_at=_parse_observed_at(args.observed_at),
        lock_wait_seconds=args.lock_wait_seconds,
    )


def run_once_command(args: argparse.Namespace) -> dict[str, Any]:
    _reject_execution_mode(args.mode)
    return run_once(
        manifest_path=args.manifest,
        state_path=args.state_path,
        ledger_path=args.ledger_path,
        holder=args.holder,
        idempotency_key=args.idempotency_key,
        started_at=_parse_observed_at(args.started_at),
    )


def run_operations_command(args: argparse.Namespace) -> dict[str, Any]:
    _reject_execution_mode(args.mode)
    if args.command == "operations-status":
        return build_operations_status(state_path=args.state_path, ledger_path=args.ledger_path, observed_at=_parse_observed_at(args.observed_at), max_age_hours=args.max_age_hours)
    if args.command == "operations-snapshot":
        return create_snapshot(state_path=args.state_path, ledger_path=args.ledger_path, snapshot_root=args.snapshot_root, retain=args.retain, created_at=_parse_observed_at(args.created_at))
    if args.command == "operations-soak-evidence":
        return run_soak_evidence(manifest_path=args.manifest, days=args.days, started_at=_parse_observed_at(args.started_at))
    if args.command == "operations-restore-verify":
        return verify_restore(snapshot_root=args.snapshot_root, snapshot_id=args.snapshot_id, verification_root=args.verification_root, observed_at=_parse_observed_at(args.observed_at))
    if args.command == "operations-recovery-drill":
        return run_recovery_drill(manifest_path=args.manifest, started_at=_parse_observed_at(args.started_at))
    if args.command == "operations-install-launchd":
        return install_launchd_agent(repository_root=args.repository_root, launch_agents_dir=args.launch_agents_dir, confirm_install=args.confirm_install)
    raise ValueError("unsupported operations command")


def _run_with_optional_profile(profile_path: str | None, func: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    if not profile_path:
        return func()
    profiler = cProfile.Profile()
    try:
        return profiler.runcall(func)
    finally:
        with open(profile_path, "w", encoding="utf-8") as output:
            stats = pstats.Stats(profiler, stream=output).sort_stats("cumulative")
            stats.print_stats()


def _load_fixture_batch_entries(args: argparse.Namespace) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    if args.manifest:
        with args.manifest.open("r", encoding="utf-8") as source:
            manifest = json.load(source)
        if not isinstance(manifest, dict) or not isinstance(manifest.get("fixtures"), list):
            raise ValueError("fixture batch manifest must be a JSON object with a fixtures array")
        manifest_root = args.manifest.parent
        for index, raw_entry in enumerate(manifest["fixtures"], start=1):
            if not isinstance(raw_entry, dict):
                raise ValueError(f"fixture batch manifest entry {index} must be an object")
            entries.append(_normalize_manifest_entry(raw_entry, manifest_root=manifest_root, index=index))

    for raw_fixture in args.fixture:
        if "=" not in raw_fixture:
            raise ValueError("fixture entries must use SYMBOL=PATH")
        symbol, raw_path = raw_fixture.split("=", 1)
        entries.append({"symbol": symbol, "path": Path(raw_path)})

    if not entries:
        raise ValueError("fixture-batch requires --manifest or at least one --fixture SYMBOL=PATH")
    return entries


def _validate_backtest_numeric_args(args: argparse.Namespace) -> None:
    _require_finite_usd("budget-usd", args.budget_usd, positive=True)
    _require_finite_usd("bot-allocation-usd", args.bot_allocation_usd, positive=True)
    _require_finite_usd("reserved-usd", args.reserved_usd, positive=False)
    _require_finite_usd("max-order-usd", args.max_order_usd, positive=True)
    provider_balance = getattr(args, "provider_demo_balance_usd", None)
    if provider_balance is not None:
        _require_finite_usd("provider-demo-balance-usd", provider_balance, positive=True)


def _require_finite_usd(label: str, value: float, *, positive: bool) -> None:
    if not isinstance(value, (int, float)) or not isfinite(float(value)):
        raise ValueError(f"{label} must be a finite number")
    if positive and float(value) <= 0:
        raise ValueError(f"{label} must be positive")
    if not positive and float(value) < 0:
        raise ValueError(f"{label} must be non-negative")


def _normalize_manifest_entry(raw_entry: dict[str, Any], *, manifest_root: Path, index: int) -> dict[str, Any]:
    symbol = raw_entry.get("symbol")
    path = raw_entry.get("path")
    if not symbol or not path:
        raise ValueError(f"fixture batch manifest entry {index} requires symbol and path")
    fixture_path = Path(str(path))
    if not fixture_path.is_absolute():
        fixture_path = manifest_root / fixture_path
    entry = dict(raw_entry)
    entry["symbol"] = str(symbol)
    entry["path"] = fixture_path
    return entry


def _parse_strategy_params_json(raw_value: str | None) -> dict[str, Any] | None:
    if raw_value is None:
        return None
    parsed = json.loads(raw_value)
    if not isinstance(parsed, dict):
        raise ValueError("strategy params JSON must be an object")
    return parsed


def _entry_strategy_parameters(
    entry: dict[str, Any],
    fallback: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if "strategyParameters" not in entry:
        return fallback
    strategy_parameters = entry["strategyParameters"]
    if not isinstance(strategy_parameters, dict):
        raise ValueError("fixture batch manifest strategyParameters must be an object")
    return strategy_parameters


def _validate_strategy_parameters_for_cli(strategy_id: str, parameters: dict[str, Any] | None) -> None:
    if parameters is None:
        return
    result = validate_strategy_parameters(strategy_id, parameters)
    if not result.ok:
        raise ValueError("; ".join(result.errors))


def run_learning_command(args: argparse.Namespace) -> dict[str, Any]:
    from money_maker_3000.learning import LearningError, predict, train, write_artifact

    try:
        if args.profile:
            raise LearningError("learning-profile-disabled")
        if args.command == "learning-train":
            artifact = train(
                args.history_csv, args.dataset_manifest, strategy=args.strategy,
                horizon_bars=args.horizon_bars, train_end=args.train_end,
                validation_end=args.validation_end,
                allow_synthetic_smoke=args.allow_synthetic_smoke,
            )
            write_artifact(artifact, args.output)
            return artifact
        return predict(args.model, args.history_csv, args.dataset_manifest,
                       allow_synthetic_smoke=args.allow_synthetic_smoke)
    except LearningError:
        raise
    except Exception:
        raise LearningError("learning-command-failed") from None


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args, unknown = parser.parse_known_args(argv)
    if unknown:
        # Root argparse would otherwise echo arbitrary unknown values verbatim.
        if args.command in {"learning-train", "learning-predict"}:
            parser.error("invalid-learning-arguments")
        parser.error("unrecognized arguments: " + " ".join(unknown))
    try:
        if args.command == "backtest":
            result = _run_with_optional_profile(args.profile, lambda: run_backtest(args))
        elif args.command == "fixture-batch":
            result = _run_with_optional_profile(args.profile, lambda: run_fixture_batch(args))
        elif args.command == "readiness":
            result = _run_with_optional_profile(args.profile, lambda: run_readiness(args))
        elif args.command == "ledger-report":
            result = _run_with_optional_profile(args.profile, lambda: run_ledger_report(args))
        elif args.command == "lease-report":
            result = _run_with_optional_profile(args.profile, lambda: run_lease_report(args))
        elif args.command == "run-once":
            result = _run_with_optional_profile(args.profile, lambda: run_once_command(args))
        elif args.command.startswith("learning-"):
            result = run_learning_command(args)
        elif args.command.startswith("operations-"):
            result = _run_with_optional_profile(args.profile, lambda: run_operations_command(args))
        else:
            parser.error(f"unsupported command: {args.command}")
            return 2
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    try:
        serialized_result = json.dumps(result, indent=2, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError):
        print("command result is not strict JSON", file=sys.stderr)
        return 1
    print(serialized_result)
    if args.command == "readiness" and not result.get("ready", False):
        return 1
    if args.command in {"ledger-report", "lease-report"} and not result.get("integrity", {}).get("complete", False):
        return 1
    if args.command == "run-once" and result.get("status") not in {"completed", "already-completed"}:
        return 1
    if args.command == "operations-status" and result.get("status") != "ready":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
