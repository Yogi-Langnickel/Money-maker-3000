"""Bounded offline supervised probability learning; no execution or recommendations."""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import stat
from datetime import date
from pathlib import Path
from typing import Any

from money_maker_3000.contracts import validate_strategy_parameters
from money_maker_3000.fixture_provenance import FIXTURE_PROVENANCE_ENTRIES
from money_maker_3000.history_signals import build_strategy_history_diagnostics
from money_maker_3000.market_history import Bar, iter_market_history_bars

VERSION = "offline-learning.v1"
MAX_ROWS = 10000
MAX_BYTES = 8 * 1024 * 1024
MAX_JSON_BYTES = 128 * 1024
MIN_STATE_SUPPORT = 5
BOUNDARY = {
    "providerCalls": "blocked", "accountData": "absent", "executionRoutes": "absent",
    "demoExecution": "blocked", "liveExecution": "blocked", "candidateIntent": "skip",
    "performanceClaims": "classification-diagnostics-only-no-profitability-claim",
    "researchReady": False,
}
LIMITATIONS = [
    "operator-provenance-attestation-unverified", "single-chronological-holdout",
    "overlapping-labels-are-dependent", "no-exchange-calendar-validation",
    "no-corporate-action-verification", "no-trading-or-profitability-evidence",
    "repeated-holdout-inspection-can-bias-future-research",
]
STATES = {
    "volatility-band-accumulator": ("no-trigger-observed", "recovery-observed", "trigger-observed"),
    "slow-trend-allocation": ("trend-confirmed", "trend-not-confirmed"),
}
MANIFEST_KEYS = {
    "version", "symbol", "classification", "source", "sourceEvidence", "licenseEvidence",
    "attribution", "rightsEvidence", "priceBasis", "sha256", "rowCount",
}


class LearningError(ValueError):
    """Only fixed, value-blind messages may cross the CLI boundary."""


def _require(condition: bool, code: str = "invalid-learning-input") -> None:
    if not condition:
        raise LearningError(code)


def _keys(value: Any, keys: set[str]) -> None:
    _require(type(value) is dict and set(value) == keys, "invalid-learning-schema")


def _integer(value: Any, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


def _number(value: Any, low: float = 0, high: float = 1) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def _iso(value: Any) -> str:
    _require(type(value) is str, "invalid-split-date")
    try:
        _require(date.fromisoformat(value).isoformat() == value, "invalid-split-date")
    except ValueError:
        raise LearningError("invalid-split-date") from None
    return value


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _hash(value: Any) -> bool:
    return type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _read(path: str | Path, limit: int) -> bytes:
    # O_NONBLOCK prevents FIFO hangs; no-follow refuses a symlink leaf.
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            _require(stat.S_ISREG(info.st_mode) and info.st_size <= limit, "unsafe-or-oversized-input")
            raw = handle.read(limit + 1)
            _require(len(raw) <= limit, "unsafe-or-oversized-input")
            return raw
    except OSError:
        raise LearningError("learning-input-unavailable") from None


def _json(raw: bytes) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            _require(key not in result, "duplicate-json-key")
            result[key] = value
        return result

    def reject(_: str) -> None:
        raise LearningError("nonfinite-json-number")

    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=reject)
        _require(type(value) is dict, "invalid-learning-schema")
        return value
    except (UnicodeError, ValueError, RecursionError):
        raise LearningError("invalid-learning-json") from None


def _manifest(value: Any) -> None:
    _keys(value, MANIFEST_KEYS)
    _require(value["version"] == "learning-dataset.v1")
    # A narrow initial ETF universe avoids accidentally accepting prohibited instruments.
    _require(value["symbol"] in ("SPY", "QQQ", "VAS"), "unsupported-learning-symbol")
    _require(value["classification"] in ("synthetic", "observed-attested"))
    _require(value["priceBasis"] in ("unadjusted", "split-adjusted", "total-return-adjusted"))
    _require(_hash(value["sha256"]) and _integer(value["rowCount"], 1, MAX_ROWS))
    _require(type(value["source"]) is str and re.fullmatch(r"[a-z][a-z0-9-]{0,63}", value["source"]) is not None)
    # Evidence stays in the caller's private manifest; artifacts expose its digest only.
    for key in ("sourceEvidence", "licenseEvidence", "attribution", "rightsEvidence"):
        item = value[key]
        _require(type(item) is str and 1 <= len(item.strip()) <= 512 and all(ord(c) >= 32 for c in item))


def load_dataset(csv_path: str | Path, manifest_path: str | Path, *, allow_synthetic_smoke: bool = False) -> tuple[list[Bar], dict[str, Any]]:
    manifest = _json(_read(manifest_path, MAX_JSON_BYTES))
    _manifest(manifest)
    raw = _read(csv_path, MAX_BYTES)
    sha = hashlib.sha256(raw).hexdigest()
    _require(sha == manifest["sha256"], "dataset-checksum-mismatch")
    try:
        bars = []
        for bar in iter_market_history_bars(io.StringIO(raw.decode("utf-8-sig")), selected_symbol=manifest["symbol"]):
            _require(len(bars) < MAX_ROWS, "dataset-row-limit")
            _require(bar.source == manifest["source"], "dataset-source-mismatch")
            # Conservative numerical bounds prevent overflow/underflow in existing feature reducers.
            _require(all(1e-8 <= price <= 1e12 for price in (bar.open, bar.high, bar.low, bar.close)), "unsupported-price-range")
            _require(bar.volume <= 1e15, "unsupported-volume-range")
            bars.append(bar)
    except LearningError:
        raise
    except (ValueError, TypeError, UnicodeError, OverflowError):
        raise LearningError("invalid-market-history") from None
    _require(len(bars) == manifest["rowCount"], "dataset-row-count-mismatch")
    known_synthetic = {entry["sha256"] for entry in FIXTURE_PROVENANCE_ENTRIES if entry["classification"] == "synthetic-contract"}
    synthetic_marker = any(marker in manifest["source"].lower() for marker in ("synthetic", "fixture", "mock", "generated", "test"))
    _require(not (sha in known_synthetic or synthetic_marker) or manifest["classification"] == "synthetic", "synthetic-relabel-rejected")
    _require(manifest["classification"] != "synthetic" or allow_synthetic_smoke, "synthetic-smoke-opt-in-required")
    return bars, manifest


def candidate_grid(strategy: str) -> list[dict[str, Any]]:
    if strategy == "volatility-band-accumulator":
        grid = [{"lookbackDays": window, "dropTriggerPct": drop} for window in (10, 20, 40) for drop in (2.0, 3.0, 5.0)]
    elif strategy == "slow-trend-allocation":
        grid = [{"shortLookbackDays": short, "longLookbackDays": long, "confirmationBars": confirm} for short, long, confirm in ((10, 60, 1), (20, 100, 3), (50, 200, 3))]
    else:
        raise LearningError("unsupported-learning-strategy")
    _require(all(validate_strategy_parameters(strategy, item).ok for item in grid), "learning-grid-contract-drift")
    return grid


def _warmup(parameters: dict[str, Any]) -> int:
    return parameters["lookbackDays"] if "lookbackDays" in parameters else parameters["longLookbackDays"] + parameters["confirmationBars"] - 1


def _state(bars: list[Bar], endpoint: int, strategy: str, parameters: dict[str, Any]) -> str:
    window = bars[endpoint + 1 - _warmup(parameters):endpoint + 1]
    report = build_strategy_history_diagnostics(window, strategy_id=strategy, strategy_parameters=parameters)
    state = report["state"]
    _require(report["parameterState"] == "valid" and state in STATES[strategy], "invalid-learning-feature")
    return state


def _fit(states: list[str], labels: list[int], strategy: str) -> dict[str, Any]:
    count, positive = len(labels), sum(labels)
    prior = (positive + 1) / (count + 2)
    table = {}
    for state in STATES[strategy]:
        selected = [label for observed, label in zip(states, labels) if observed == state]
        support, positives = len(selected), sum(selected)
        fallback = support < MIN_STATE_SUPPORT
        table[state] = {"support": support, "positives": positives, "usesPrior": fallback,
                        "probabilityUp": prior if fallback else (positives + 1) / (support + 2)}
    return {"support": count, "positives": positive, "priorProbabilityUp": prior, "states": table}


def _score(probabilities: list[float], labels: list[int]) -> float:
    result = math.fsum((p - y) ** 2 for p, y in zip(probabilities, labels)) / len(labels)
    _require(_number(result), "invalid-learning-metric")
    return result


def _split_summary(bars: list[Bar], indices: list[int], labels: list[int], horizon: int) -> dict[str, Any]:
    return {"count": len(indices), "positives": sum(labels), "firstFeatureDate": bars[indices[0]].date,
            "lastFeatureDate": bars[indices[-1]].date, "lastLabelDate": bars[indices[-1] + horizon].date}


def train(csv_path: str | Path, manifest_path: str | Path, *, strategy: str, horizon_bars: int,
          train_end: str, validation_end: str, allow_synthetic_smoke: bool = False) -> dict[str, Any]:
    _require(_integer(horizon_bars, 1, 20), "invalid-horizon-bars")
    _require(_iso(train_end) < _iso(validation_end), "invalid-split-order")
    grid = candidate_grid(strategy)
    warmup = max(_warmup(item) for item in grid)
    bars, manifest = load_dataset(csv_path, manifest_path, allow_synthetic_smoke=allow_synthetic_smoke)
    dates = {bar.date for bar in bars}
    _require(train_end in dates and validation_end in dates, "split-cutoff-not-observed")
    splits: dict[str, list[int]] = {"train": [], "validation": [], "holdout": []}
    purged = 0
    for i in range(warmup - 1, len(bars) - horizon_bars):
        feature_date, label_date = bars[i].date, bars[i + horizon_bars].date
        if feature_date <= train_end:
            if label_date <= train_end:
                splits["train"].append(i)
            else:
                purged += 1
        elif feature_date <= validation_end:
            if label_date <= validation_end:
                splits["validation"].append(i)
            else:
                purged += 1
        else:
            splits["holdout"].append(i)
    _require(len(splits["train"]) >= 20 and len(splits["validation"]) >= 10 and len(splits["holdout"]) >= 10, "insufficient-learning-splits")
    labels = {name: [int(bars[i + horizon_bars].close > bars[i].close) for i in indices] for name, indices in splits.items()}
    candidates = []
    fits = []
    # Candidate fitting and selection consume only train and validation labels.
    for index, parameters in enumerate(grid):
        states = [_state(bars, i, strategy, parameters) for i in splits["train"]]
        fit = _fit(states, labels["train"], strategy)
        validation_states = [_state(bars, i, strategy, parameters) for i in splits["validation"]]
        score = _score([fit["states"][state]["probabilityUp"] for state in validation_states], labels["validation"])
        candidates.append({"index": index, "parameters": parameters, "validationBrier": score})
        fits.append(fit)
    winner = min(range(len(grid)), key=lambda i: (candidates[i]["validationBrier"], i))
    fit = fits[winner]
    selected = grid[winner]
    holdout_states = [_state(bars, i, strategy, selected) for i in splits["holdout"]]
    validation_states = [_state(bars, i, strategy, selected) for i in splits["validation"]]
    fallback_counts = {
        name: {"priorFallbackCount": sum(fit["states"][state]["usesPrior"] for state in states),
               "unseenStateCount": sum(fit["states"][state]["support"] == 0 for state in states)}
        for name, states in (("validation", validation_states), ("holdout", holdout_states))
    }
    holdout_brier = _score([fit["states"][state]["probabilityUp"] for state in holdout_states], labels["holdout"])
    baseline = {name: _score([fit["priorProbabilityUp"]] * len(labels[name]), labels[name]) for name in ("validation", "holdout")}
    summaries = {name: _split_summary(bars, indices, labels[name], horizon_bars) for name, indices in splits.items()}
    model = {"strategy": strategy, "parameters": selected, "horizonBars": horizon_bars,
             "symbol": manifest["symbol"], "priceBasis": manifest["priceBasis"],
             "classification": manifest["classification"], "sourceSha256": _digest(manifest["source"]), "fit": fit,
             "lastTrainingLabelDate": summaries["train"]["lastLabelDate"],
             "trainingRowsSha256": _digest([bar.to_dict() for bar in bars if bar.date <= train_end])}
    report = {
        "status": "synthetic-smoke-only" if manifest["classification"] == "synthetic" else "offline-fit-provenance-unverified",
        "datasetSha256": manifest["sha256"], "manifestSha256": _digest(manifest),
        "datasetRows": len(bars), "datasetFirstDate": bars[0].date, "datasetLastDate": bars[-1].date,
        "provenanceVerification": "operator-attestation-unverified",
        "trainEnd": train_end, "validationEnd": validation_end, "warmupBars": warmup,
        "purgedEndpointCount": purged, "unlabeledTailCount": horizon_bars,
        "splits": summaries, "fallbackCounts": fallback_counts, "candidates": candidates, "selectedCandidate": winner,
        "holdoutBrier": holdout_brier, "baselineValidationBrier": baseline["validation"],
        "baselineHoldoutBrier": baseline["holdout"],
        "oneClassTraining": fit["positives"] in (0, fit["support"]),
        "constantTrainingFeature": sum(item["support"] > 0 for item in fit["states"].values()) == 1,
        "validationBeatsPrior": candidates[winner]["validationBrier"] < baseline["validation"],
        "limitations": list(LIMITATIONS),
    }
    payload = {"version": VERSION, "boundary": dict(BOUNDARY), "model": model, "report": report}
    artifact = {**payload, "sha256": _digest(payload)}
    validate_artifact(artifact)
    return artifact


def _validate_fit(fit: Any, strategy: str) -> None:
    _keys(fit, {"support", "positives", "priorProbabilityUp", "states"})
    _require(_integer(fit["support"], 20, MAX_ROWS) and _integer(fit["positives"], 0, fit["support"]))
    prior = (fit["positives"] + 1) / (fit["support"] + 2)
    _require(_number(fit["priorProbabilityUp"]) and fit["priorProbabilityUp"] == prior)
    _keys(fit["states"], set(STATES[strategy]))
    for item in fit["states"].values():
        _keys(item, {"support", "positives", "usesPrior", "probabilityUp"})
        _require(_integer(item["support"], 0, fit["support"]) and _integer(item["positives"], 0, item["support"]))
        fallback = item["support"] < MIN_STATE_SUPPORT
        _require(type(item["usesPrior"]) is bool and item["usesPrior"] == fallback)
        expected = prior if fallback else (item["positives"] + 1) / (item["support"] + 2)
        _require(_number(item["probabilityUp"]) and item["probabilityUp"] == expected)
    _require(sum(item["support"] for item in fit["states"].values()) == fit["support"])
    _require(sum(item["positives"] for item in fit["states"].values()) == fit["positives"])


def validate_artifact(artifact: Any) -> None:
    _keys(artifact, {"version", "boundary", "model", "report", "sha256"})
    _require(artifact["version"] == VERSION and _canonical(artifact["boundary"]) == _canonical(BOUNDARY))
    _require(_hash(artifact["sha256"]) and artifact["sha256"] == _digest({key: value for key, value in artifact.items() if key != "sha256"}), "artifact-checksum-mismatch")
    model, report = artifact["model"], artifact["report"]
    _keys(model, {"strategy", "parameters", "horizonBars", "symbol", "priceBasis", "classification", "sourceSha256", "fit", "lastTrainingLabelDate", "trainingRowsSha256"})
    grid = candidate_grid(model["strategy"])
    _require(_integer(model["horizonBars"], 1, 20) and model["symbol"] in ("SPY", "QQQ", "VAS"))
    _require(model["priceBasis"] in ("unadjusted", "split-adjusted", "total-return-adjusted"))
    _require(model["classification"] in ("synthetic", "observed-attested") and _hash(model["trainingRowsSha256"]) and _hash(model["sourceSha256"]))
    _require(any(_canonical(model["parameters"]) == _canonical(item) for item in grid))
    _iso(model["lastTrainingLabelDate"])
    _validate_fit(model["fit"], model["strategy"])
    _keys(report, {"status", "datasetSha256", "manifestSha256", "datasetRows", "datasetFirstDate", "datasetLastDate", "provenanceVerification", "trainEnd", "validationEnd", "warmupBars", "purgedEndpointCount", "unlabeledTailCount", "splits", "fallbackCounts", "candidates", "selectedCandidate", "holdoutBrier", "baselineValidationBrier", "baselineHoldoutBrier", "oneClassTraining", "constantTrainingFeature", "validationBeatsPrior", "limitations"})
    _require(report["status"] == ("synthetic-smoke-only" if model["classification"] == "synthetic" else "offline-fit-provenance-unverified"))
    _require(report["provenanceVerification"] == "operator-attestation-unverified" and report["limitations"] == LIMITATIONS)
    _require(_hash(report["datasetSha256"]) and _hash(report["manifestSha256"]))
    synthetic_entries = [entry for entry in FIXTURE_PROVENANCE_ENTRIES if entry["classification"] == "synthetic-contract"]
    known_synthetic = report["datasetSha256"] in {entry["sha256"] for entry in synthetic_entries}
    known_synthetic_source = model["sourceSha256"] in {_digest(entry["source"]) for entry in synthetic_entries}
    _require(not (known_synthetic or known_synthetic_source) or model["classification"] == "synthetic", "synthetic-relabel-rejected")
    _require(_integer(report["datasetRows"], 1, MAX_ROWS))
    _require(_iso(report["datasetFirstDate"]) < _iso(report["trainEnd"]) < _iso(report["validationEnd"]) < _iso(report["datasetLastDate"]))
    warmup = max(_warmup(item) for item in grid)
    _require(type(report["warmupBars"]) is int and report["warmupBars"] == warmup)
    _require(_integer(report["purgedEndpointCount"], 0, 2 * model["horizonBars"]))
    _require(type(report["unlabeledTailCount"]) is int and report["unlabeledTailCount"] == model["horizonBars"])
    _keys(report["splits"], {"train", "validation", "holdout"})
    previous_label = report["datasetFirstDate"]
    for name in ("train", "validation", "holdout"):
        split = report["splits"][name]
        _keys(split, {"count", "positives", "firstFeatureDate", "lastFeatureDate", "lastLabelDate"})
        _require(_integer(split["count"], 20 if name == "train" else 10, MAX_ROWS) and _integer(split["positives"], 0, split["count"]))
        _require(previous_label < _iso(split["firstFeatureDate"]) <= _iso(split["lastFeatureDate"]) < _iso(split["lastLabelDate"]) <= report["datasetLastDate"])
        previous_label = split["lastLabelDate"]
    _keys(report["fallbackCounts"], {"validation", "holdout"})
    for name, counts in report["fallbackCounts"].items():
        _keys(counts, {"priorFallbackCount", "unseenStateCount"})
        _require(_integer(counts["priorFallbackCount"], 0, report["splits"][name]["count"]))
        _require(_integer(counts["unseenStateCount"], 0, counts["priorFallbackCount"]))
    train_split, validation, holdout = (report["splits"][name] for name in ("train", "validation", "holdout"))
    _require(train_split["lastLabelDate"] <= report["trainEnd"] < validation["firstFeatureDate"])
    _require(validation["lastLabelDate"] <= report["validationEnd"] < holdout["firstFeatureDate"])
    _require(model["lastTrainingLabelDate"] == train_split["lastLabelDate"])
    _require(train_split["count"] == model["fit"]["support"] and train_split["positives"] == model["fit"]["positives"])
    _require(sum(item["count"] for item in report["splits"].values()) + warmup - 1 + report["purgedEndpointCount"] + model["horizonBars"] == report["datasetRows"])
    _require(type(report["candidates"]) is list and len(report["candidates"]) == len(grid))
    for index, (candidate, parameters) in enumerate(zip(report["candidates"], grid)):
        _keys(candidate, {"index", "parameters", "validationBrier"})
        _require(type(candidate["index"]) is int and candidate["index"] == index and _canonical(candidate["parameters"]) == _canonical(parameters))
        _require(_number(candidate["validationBrier"]))
    winner = min(range(len(grid)), key=lambda i: (report["candidates"][i]["validationBrier"], i))
    _require(type(report["selectedCandidate"]) is int and report["selectedCandidate"] == winner)
    _require(_canonical(model["parameters"]) == _canonical(grid[winner]))
    for key in ("holdoutBrier", "baselineValidationBrier", "baselineHoldoutBrier"):
        _require(_number(report[key]))
    prior = model["fit"]["priorProbabilityUp"]
    for name, key in (("validation", "baselineValidationBrier"), ("holdout", "baselineHoldoutBrier")):
        split = report["splits"][name]
        expected = ((1 - prior) ** 2 * split["positives"] + prior ** 2 * (split["count"] - split["positives"])) / split["count"]
        _require(math.isclose(report[key], expected, rel_tol=1e-14, abs_tol=1e-14))
    flags = {"oneClassTraining": model["fit"]["positives"] in (0, model["fit"]["support"]),
             "constantTrainingFeature": sum(item["support"] > 0 for item in model["fit"]["states"].values()) == 1,
             "validationBeatsPrior": report["candidates"][winner]["validationBrier"] < report["baselineValidationBrier"]}
    for key, expected in flags.items():
        _require(type(report[key]) is bool and report[key] == expected)


def write_artifact(artifact: dict[str, Any], path: str | Path) -> None:
    validate_artifact(artifact)
    raw = _canonical(artifact) + b"\n"
    _require(len(raw) <= MAX_JSON_BYTES, "artifact-size-limit")
    # Exclusive creation never replaces existing files, hardlinks, or symlinks.
    # A crash may leave an incomplete file: the strict reader rejects it.
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        raise LearningError("artifact-output-unavailable-or-exists") from None


def load_artifact(path: str | Path) -> dict[str, Any]:
    artifact = _json(_read(path, MAX_JSON_BYTES))
    try:
        validate_artifact(artifact)
    except (TypeError, ValueError, OverflowError, RecursionError, KeyError):
        raise LearningError("invalid-learning-artifact") from None
    return artifact


def predict(model_path: str | Path, csv_path: str | Path, manifest_path: str | Path, *, allow_synthetic_smoke: bool = False) -> dict[str, Any]:
    artifact = load_artifact(model_path)
    model = artifact["model"]
    bars, manifest = load_dataset(csv_path, manifest_path, allow_synthetic_smoke=allow_synthetic_smoke)
    _require(all(manifest[key] == model[key] for key in ("symbol", "priceBasis", "classification")), "prediction-dataset-incompatible")
    _require(_digest(manifest["source"]) == model["sourceSha256"], "prediction-source-incompatible")
    _require(model["classification"] != "synthetic" or allow_synthetic_smoke, "synthetic-smoke-opt-in-required")
    _require(len(bars) >= _warmup(model["parameters"]), "insufficient-prediction-history")
    _require(bars[-1].date > artifact["report"]["splits"]["validation"]["lastLabelDate"], "prediction-before-selection-cutoff")
    state = _state(bars, len(bars) - 1, model["strategy"], model["parameters"])
    support = model["fit"]["states"][state]
    return {"version": "offline-learning-prediction.v1", "boundary": dict(BOUNDARY),
            "status": artifact["report"]["status"], "modelSha256": artifact["sha256"],
            "datasetSha256": manifest["sha256"], "symbol": model["symbol"], "asOfDate": bars[-1].date,
            "horizonBars": model["horizonBars"], "label": "future-close-strictly-greater-than-current-close",
            "state": state, **support,
            "evaluationContext": "historical-overlap" if bars[-1].date <= artifact["report"]["datasetLastDate"] else "post-training-dataset",
            "provenanceVerification": "operator-attestation-unverified"}
