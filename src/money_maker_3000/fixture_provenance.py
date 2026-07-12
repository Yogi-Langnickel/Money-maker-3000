from __future__ import annotations

import argparse
import json
import re
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any, Sequence
from urllib.parse import urlsplit

from money_maker_3000.market_history import iter_market_history_bars, sha256_file

MANIFEST_SCHEMA_VERSION = "market-history-fixture-provenance.v1"
DEFAULT_FIXTURE_ROOT = Path("tests/fixtures/market_history")
DEFAULT_MANIFEST_PATH = Path("contracts/market-history-fixture-provenance.json")
CLASSIFICATIONS = {"synthetic-contract", "observed-licensed"}
ENTRY_KEYS = {
    "file",
    "sha256",
    "classification",
    "dataOrigin",
    "observedMarketData",
    "redistributionApproved",
    "symbol",
    "rowCount",
    "source",
    "sourceUrl",
    "license",
    "licenseUrl",
    "attribution",
}
SENSITIVE_METADATA_VALUE_PATTERNS = (
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"\b(?:api|oauth|jwt|token|secret)[_-][A-Za-z0-9][A-Za-z0-9_-]{7,}\b", re.I),
    re.compile(r"[?&](?:api[-_]?key|oauth|token|secret)=", re.I),
    re.compile(r"https://[^/\s]+@", re.I),
)


def _synthetic_entry(file: str, sha256: str, symbol: str, row_count: int, source: str) -> Mapping[str, Any]:
    return MappingProxyType({
        "file": file,
        "sha256": sha256,
        "classification": "synthetic-contract",
        "dataOrigin": "deterministic-synthetic",
        "observedMarketData": False,
        "redistributionApproved": True,
        "symbol": symbol,
        "rowCount": row_count,
        "source": source,
        "sourceUrl": None,
        "license": "project-test-data",
        "licenseUrl": None,
        "attribution": "not-required",
    })


FIXTURE_PROVENANCE_ENTRIES = (
    _synthetic_entry(
        "gld-daily.csv",
        "23ae1e1422b894e523196f08dc683c1738aa126e6d3139d17de9a8af3a5fd5fd",
        "GLD",
        3,
        "synthetic-short-fixture",
    ),
    _synthetic_entry(
        "qqq-daily.csv",
        "cb7c0a71975d8fda165365d1795b1dd2846ad23b13328c318e4a512d49639347",
        "QQQ",
        3,
        "synthetic-short-fixture",
    ),
    _synthetic_entry(
        "spy-daily.csv",
        "32402aa5b309633b3679df230c5b52218b83e30d20a18295a6b8e08d3c691214",
        "SPY",
        3,
        "synthetic-short-fixture",
    ),
    _synthetic_entry(
        "spy-slow-trend-202-daily.csv",
        "ecaec707c5bc6dccc05f0ed5b52f1110ba08a0e0431c59e0d9223baf9ae546d9",
        "SPY",
        202,
        "synthetic-test-fixture",
    ),
    _synthetic_entry(
        "spy-volatility-decline-20-daily.csv",
        "5621200eb3e8b3d0b87ea7479812c38ad1fa46aeca308ea654043bf0f76c8d5c",
        "SPY",
        20,
        "synthetic-volatility-fixture",
    ),
    _synthetic_entry(
        "spy-volatility-recovery-20-daily.csv",
        "8566f55c59ffd4f0f29c0b228422079d70dc90707345bbf922a87772ffdb076d",
        "SPY",
        20,
        "synthetic-volatility-fixture",
    ),
    _synthetic_entry(
        "spy-volatility-stable-20-daily.csv",
        "7e7aa8344d04b62a09d0f1dfb87ba218bad982ec8476e5bcd40666c0859fa56c",
        "SPY",
        20,
        "synthetic-volatility-fixture",
    ),
    _synthetic_entry(
        "vas-au-etf-synthetic-20-daily.csv",
        "6e7c464580d94a33f2f14613e7301af99af7a513e82ee8a71d92c782caf82c4b",
        "VAS",
        20,
        "synthetic-au-etf-fixture",
    ),
)


def build_fixture_provenance_manifest() -> dict[str, Any]:
    return {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "scope": "offline-test-fixtures-only",
        "providerCalls": "blocked",
        "credentials": "absent",
        "accountData": "absent",
        "executionRoutes": "absent",
        "entries": [dict(entry) for entry in FIXTURE_PROVENANCE_ENTRIES],
    }


def render_fixture_provenance_manifest() -> str:
    return json.dumps(build_fixture_provenance_manifest(), indent=2, sort_keys=True) + "\n"


def validate_fixture_provenance_inventory(
    fixture_root: Path = DEFAULT_FIXTURE_ROOT,
    entries: Sequence[Mapping[str, Any]] = FIXTURE_PROVENANCE_ENTRIES,
) -> list[str]:
    errors: list[str] = []
    listed_files: list[str] = []

    for index, entry in enumerate(entries):
        label = f"fixture provenance entry {index + 1}"
        if not isinstance(entry, Mapping) or set(entry) != ENTRY_KEYS:
            errors.append(f"{label} has invalid fields")
            continue
        file = entry["file"]
        if not isinstance(file, str) or not file or Path(file).name != file or not file.endswith(".csv"):
            errors.append(f"{label} file must be a local CSV filename")
            continue
        listed_files.append(file)
        errors.extend(_validate_entry_metadata(entry, label))
        path = fixture_root / file
        if path.is_symlink():
            errors.append(f"{file}: fixture file must not be a symbolic link")
            continue
        if not path.is_file():
            errors.append(f"{file}: fixture file is missing")
            continue
        try:
            actual_sha256 = sha256_file(path)
        except OSError:
            errors.append(f"{file}: fixture file cannot be read")
            continue
        if actual_sha256 != entry["sha256"]:
            errors.append(f"{file}: SHA-256 drift detected")
        try:
            row_count = 0
            source_drift = False
            with path.open("r", encoding="utf-8", newline="") as source:
                for bar in iter_market_history_bars(source, selected_symbol=entry["symbol"]):
                    row_count += 1
                    if bar.source != entry["source"]:
                        source_drift = True
        except (OSError, TypeError, ValueError):
            errors.append(f"{file}: invalid market-history CSV")
            continue
        if row_count != entry["rowCount"]:
            errors.append(f"{file}: row count drift detected")
        if source_drift:
            errors.append(f"{file}: source drift detected")

    if len(set(listed_files)) != len(listed_files):
        errors.append("fixture provenance inventory contains duplicate filenames")
    try:
        committed_files = sorted(path.name for path in fixture_root.glob("*.csv") if path.is_file())
    except OSError as exc:
        errors.append(f"fixture root cannot be read ({exc})")
    else:
        if sorted(listed_files) != committed_files:
            errors.append("fixture provenance inventory does not exactly cover committed CSV files")
    return errors


def _validate_entry_metadata(entry: Mapping[str, Any], label: str) -> list[str]:
    errors: list[str] = []
    classification = entry["classification"]
    if any(
        pattern.search(value)
        for value in entry.values()
        if isinstance(value, str)
        for pattern in SENSITIVE_METADATA_VALUE_PATTERNS
    ):
        errors.append(f"{label} contains sensitive metadata")
    if (
        not isinstance(entry["sha256"], str)
        or len(entry["sha256"]) != 64
        or any(character not in "0123456789abcdef" for character in entry["sha256"])
        or not isinstance(entry["symbol"], str)
        or not entry["symbol"]
        or not isinstance(entry["rowCount"], int)
        or isinstance(entry["rowCount"], bool)
        or entry["rowCount"] < 1
        or not isinstance(entry["source"], str)
        or not entry["source"]
        or not isinstance(classification, str)
        or not isinstance(entry["dataOrigin"], str)
        or not isinstance(entry["license"], str)
        or not isinstance(entry["attribution"], str)
        or not (entry["sourceUrl"] is None or isinstance(entry["sourceUrl"], str))
        or not (entry["licenseUrl"] is None or isinstance(entry["licenseUrl"], str))
        or not isinstance(entry["observedMarketData"], bool)
        or not isinstance(entry["redistributionApproved"], bool)
    ):
        errors.append(f"{label} metadata types are invalid")
        return errors
    if classification not in CLASSIFICATIONS:
        return [*errors, f"{label} classification is invalid"]

    if classification == "synthetic-contract" and (
        entry["dataOrigin"] != "deterministic-synthetic"
        or entry["observedMarketData"]
        or not entry["redistributionApproved"]
        or not entry["source"].startswith("synthetic-")
        or entry["sourceUrl"] is not None
        or entry["license"] != "project-test-data"
        or entry["licenseUrl"] is not None
        or entry["attribution"] != "not-required"
    ):
        errors.append(f"{label} synthetic classification metadata is invalid")
    elif classification == "observed-licensed" and (
        entry["dataOrigin"] != "observed-market"
        or not entry["observedMarketData"]
        or not entry["redistributionApproved"]
        or not _is_public_https_url(entry["sourceUrl"])
        or entry["license"] in {"", "not-established", None}
        or not _is_public_https_url(entry["licenseUrl"])
        or entry["attribution"] in {"", "absent", None}
    ):
        errors.append(f"{label} observed classification metadata is invalid")
    return errors


def _is_public_https_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    )


def write_fixture_provenance_manifest(
    path: Path = DEFAULT_MANIFEST_PATH,
    fixture_root: Path = DEFAULT_FIXTURE_ROOT,
) -> None:
    errors = validate_fixture_provenance_inventory(fixture_root)
    if errors:
        raise ValueError("; ".join(errors))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_fixture_provenance_manifest(), encoding="utf-8")


def check_fixture_provenance_manifest(
    path: Path = DEFAULT_MANIFEST_PATH,
    fixture_root: Path = DEFAULT_FIXTURE_ROOT,
) -> bool:
    if validate_fixture_provenance_inventory(fixture_root):
        return False
    try:
        existing = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return existing == render_fixture_provenance_manifest()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate or validate the offline fixture provenance manifest.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="write the canonical provenance manifest")
    mode.add_argument("--check", action="store_true", help="fail on fixture or provenance drift")
    parser.add_argument("--path", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--fixture-root", type=Path, default=DEFAULT_FIXTURE_ROOT)
    args = parser.parse_args(argv)

    if args.write:
        try:
            write_fixture_provenance_manifest(args.path, args.fixture_root)
        except ValueError as exc:
            parser.error(str(exc))
        return 0
    if check_fixture_provenance_manifest(args.path, args.fixture_root):
        return 0
    parser.error("fixture provenance drift detected; validate fixture metadata before running with --write")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
