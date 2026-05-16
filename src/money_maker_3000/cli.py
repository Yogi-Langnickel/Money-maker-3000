from __future__ import annotations

import argparse
import cProfile
import json
import pstats
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from money_maker_3000.backtest import build_historical_fixture_backtest, build_synthetic_backtest
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


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "backtest":
            result = _run_with_optional_profile(args.profile, lambda: run_backtest(args))
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
