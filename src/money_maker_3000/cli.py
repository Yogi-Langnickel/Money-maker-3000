from __future__ import annotations

import argparse
import cProfile
import json
import pstats
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from money_maker_3000.backtest import (
    build_historical_fixture_backtest,
    build_offline_fixture_batch_diagnostics,
    build_synthetic_backtest,
)
from money_maker_3000.contracts import build_allocation_policy, validate_run_mode
from money_maker_3000.ledger import export_ledger_report
from money_maker_3000.market_history import iter_market_history_bars, sha256_file


def _parse_started_at(raw: str | None) -> datetime:
    if not raw:
        return datetime.fromisoformat("2026-05-15T00:00:00+00:00")
    normalized = raw.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def _reject_execution_mode(run_mode: str) -> None:
    result = validate_run_mode(run_mode)
    if not result.ok:
        raise ValueError(result.errors[0])


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="money-maker-3000", description="Python-first simulation worker CLI")
    parser.add_argument("--profile", metavar="PATH", help="write cProfile stats for this CLI run")
    subparsers = parser.add_subparsers(dest="command", required=True)

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
    backtest.add_argument("--history-csv", type=Path, help="offline market-history CSV fixture")
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
    fixture_batch.add_argument("--started-at", help="explicit ISO timestamp for deterministic metadata")

    ledger_report = subparsers.add_parser("ledger-report", help="export a redacted JSONL ledger report")
    ledger_report.add_argument("ledger_path", type=Path)
    ledger_report.add_argument("--mode", default="backtest", help="must be backtest; execute/trade are rejected")
    return parser


def run_backtest(args: argparse.Namespace) -> dict[str, Any]:
    _reject_execution_mode(args.mode)
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
    if args.history_csv:
        input_sha256 = sha256_file(args.history_csv)
        with args.history_csv.open("r", encoding="utf-8", newline="") as source:
            bars = iter_market_history_bars(source, selected_symbol=args.symbol)
            return build_historical_fixture_backtest(
                bars=bars,
                strategy_id=args.strategy_id,
                selected_instrument=selected_instrument,
                budget_usd=args.budget_usd,
                allocation_policy=allocation,
                started_at=started_at,
                input_sha256=input_sha256,
            )
    return build_synthetic_backtest(started_at=started_at)


def run_fixture_batch(args: argparse.Namespace) -> dict[str, Any]:
    _reject_execution_mode(args.mode)
    started_at = _parse_started_at(args.started_at)
    allocation = build_allocation_policy(
        bot_allocation_usd=args.bot_allocation_usd,
        reserved_usd=args.reserved_usd,
        max_order_usd=args.max_order_usd,
        provider_demo_balance_usd=args.provider_demo_balance_usd,
    )
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
        input_sha256 = sha256_file(fixture_path)
        with fixture_path.open("r", encoding="utf-8", newline="") as source:
            bars = iter_market_history_bars(source, selected_symbol=symbol)
            reports.append(
                build_historical_fixture_backtest(
                    bars=bars,
                    strategy_id=str(entry.get("strategyId", args.strategy_id)),
                    selected_instrument=selected_instrument,
                    budget_usd=float(entry.get("budgetUsd", args.budget_usd)),
                    allocation_policy=allocation,
                    started_at=started_at,
                    input_sha256=input_sha256,
                )
            )
    return build_offline_fixture_batch_diagnostics(reports=reports, started_at=started_at)


def run_ledger_report(args: argparse.Namespace) -> dict[str, Any]:
    _reject_execution_mode(args.mode)
    return export_ledger_report(args.ledger_path)


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


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "backtest":
            result = _run_with_optional_profile(args.profile, lambda: run_backtest(args))
        elif args.command == "fixture-batch":
            result = _run_with_optional_profile(args.profile, lambda: run_fixture_batch(args))
        elif args.command == "ledger-report":
            result = _run_with_optional_profile(args.profile, lambda: run_ledger_report(args))
        else:
            parser.error(f"unsupported command: {args.command}")
            return 2
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
