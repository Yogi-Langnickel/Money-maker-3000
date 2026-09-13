"""Audited local cross-feed robustness diagnostics; never a merged training series.

All reports (including their digests) inherit the strictest source retention.
This module deliberately does not change learning.predict's source identity check.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from money_maker_3000 import learning
from money_maker_3000.market_history import Bar
from money_maker_3000.research_cycle import retention_check

VERSION = "strategy-portability.v1"
FIELDS = ("instrument", "currency", "session", "timestamps", "priceType", "adjustments", "costs")
TOLERANCES = {
    "volatility-band-accumulator": {"stateAgreement": .99, "triggerJaccard": .95,
        "unpairedFraction": .01, "labelAgreement": .98, "probabilityMeanAbsoluteDifference": .01,
        "directionAgreement": .98, "returnDifferenceP95": .001, "stressStateAgreement": .95},
    "slow-trend-allocation": {"stateAgreement": .97, "triggerJaccard": .90,
        "unpairedFraction": .03, "labelAgreement": .95, "probabilityMeanAbsoluteDifference": .02,
        "directionAgreement": .95, "returnDifferenceP95": .003, "stressStateAgreement": .90},
}


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise learning.LearningError(code)


def _timestamp(value: str) -> datetime:
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        _require(stamp.utcoffset() is not None, "portability-invalid-timestamp")
        return stamp
    except (TypeError, ValueError, AttributeError):
        raise learning.LearningError("portability-invalid-timestamp") from None


def freeze_protocol(path: str | Path, *, train_end: str, selection_end: str,
                    evaluation_start: str, evaluation_end: str, created_at: str,
                    expected_last_observation: str | None = None, end_session_evidence: str | None = None,
                    evidence_kind: str = "retrospective-historical-robustness") -> dict[str, Any]:
    """Freeze dates, candidate lists, decision thresholds and diagnostics before reading reserve.

    Prospective reservation requires a creation time before the first reserved date.
    Older, previously inspected data can only be labelled retrospective.
    """
    _require(learning._iso(train_end) < learning._iso(selection_end) <
             learning._iso(evaluation_start) <= learning._iso(evaluation_end), "portability-invalid-splits")
    expected_end = evaluation_end if expected_last_observation is None else learning._iso(expected_last_observation)
    _require(evaluation_start <= expected_end <= evaluation_end, "portability-invalid-expected-end")
    _require(expected_end == evaluation_end or (type(end_session_evidence) is str and bool(end_session_evidence.strip())),
             "portability-non-session-end-evidence-required")
    end_policy = {"kind": "evaluation-end-is-required-observation" if expected_end == evaluation_end else "documented-last-session-on-or-before-end",
                  "evidence": None if expected_end == evaluation_end else end_session_evidence}
    _timestamp(created_at)
    _require(evidence_kind in ("retrospective-historical-robustness", "prospectively-reserved-historical-robustness"),
             "portability-invalid-evidence-kind")
    if evidence_kind.startswith("prospectively"):
        _require(max(_timestamp(created_at).astimezone(timezone.utc).date().isoformat(),
                     datetime.now(timezone.utc).date().isoformat()) < evaluation_start, "portability-reserve-already-observed")
    payload = {"version": VERSION, "createdAt": created_at, "frozenAt": datetime.now(timezone.utc).isoformat(), "trainEnd": train_end,
        "selectionEnd": selection_end, "evaluationStart": evaluation_start, "evaluationEnd": evaluation_end,
        "evidenceKind": evidence_kind, "expectedLastObservation": expected_end, "endSessionPolicy": end_policy, "horizonObservations": 5,
        "candidates": {s: learning.candidate_grid(s) for s in TOLERANCES},
        "tolerances": TOLERANCES, "minimumSamples": 100, "minimumStressSamples": 10,
        "minimumTriggerUnion": 5, "stressAbsoluteDailyReturn": .02,
        "normalization": "none-original-observations-preserved", "maximumRowsPerSource": 10000,
        "selectionRule": "source-only-minimum-validation-brier-then-candidate-index"}
    document = {**payload, "sha256": learning._digest(payload)}
    write_report(document, path)
    return document


def write_report(report: dict[str, Any], path: str | Path) -> None:
    """Exclusive private artifact creation; callers must retain/delete all mixed derivatives."""
    if "descriptors" in report:
        for descriptor in report["descriptors"]:
            retention_check(descriptor["source"], descriptor["retention"])
    raw = learning._canonical(report)
    _require(len(raw) <= 8 * 1024 * 1024, "portability-artifact-size-limit")
    destination = Path(path)
    for parent in (destination.parent, *destination.parent.parents):
        _require(not parent.is_symlink(), "portability-unsafe-artifact-directory")
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".portability-pending-", dir=destination.parent)
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, destination, follow_symlinks=False)
        directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError:
        raise learning.LearningError("portability-artifact-already-exists-or-unavailable") from None
    finally:
        if temporary is not None:
            os.unlink(temporary)


def load_report(path: str | Path, *, current_retentions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Read private derived evidence only after checking current operator retention state."""
    _require(type(current_retentions) is dict and bool(current_retentions), "portability-retention-required")
    for source, retention in current_retentions.items():
        retention_check(source, retention)
    report = learning._json(learning._read(path, 8 * 1024 * 1024))
    _require(report.get("version") == VERSION and type(report.get("descriptors")) is list,
             "portability-invalid-report")
    _require({item["source"] for item in report["descriptors"]} == set(current_retentions),
             "portability-retention-source-mismatch")
    return report


def load_protocol(path: str | Path) -> dict[str, Any]:
    value = learning._json(learning._read(path, learning.MAX_JSON_BYTES))
    expected = {"version", "createdAt", "frozenAt", "trainEnd", "selectionEnd", "evaluationStart", "evaluationEnd", "evidenceKind",
                "horizonObservations", "candidates", "tolerances", "minimumSamples", "minimumStressSamples",
                "minimumTriggerUnion", "stressAbsoluteDailyReturn", "normalization", "maximumRowsPerSource", "selectionRule", "expectedLastObservation", "endSessionPolicy", "sha256"}
    _require(set(value) == expected, "portability-invalid-protocol-schema")
    _timestamp(value["createdAt"])
    _timestamp(value["frozenAt"])
    _require(value["evidenceKind"] in ("retrospective-historical-robustness", "prospectively-reserved-historical-robustness"), "portability-invalid-evidence-kind")
    if value["evidenceKind"].startswith("prospectively"):
        _require(max(_timestamp(value["createdAt"]).astimezone(timezone.utc).date().isoformat(),
                     _timestamp(value["frozenAt"]).astimezone(timezone.utc).date().isoformat()) < value["evaluationStart"], "portability-reserve-already-observed")
    payload = {k: v for k, v in value.items() if k != "sha256"}
    _require(value.get("sha256") == learning._digest(payload), "portability-protocol-checksum-mismatch")
    _require(value.get("version") == VERSION and value.get("tolerances") == TOLERANCES and
             value.get("candidates") == {s: learning.candidate_grid(s) for s in TOLERANCES} and
             value.get("horizonObservations") == 5 and value.get("minimumSamples") == 100 and
             value.get("minimumStressSamples") == 10 and value.get("minimumTriggerUnion") == 5 and
             value.get("maximumRowsPerSource") == 10000 and value.get("selectionRule") == "source-only-minimum-validation-brier-then-candidate-index" and
             value.get("stressAbsoluteDailyReturn") == .02 and value.get("normalization") == "none-original-observations-preserved",
             "portability-protocol-contract-drift")
    _require(learning._iso(value["trainEnd"]) < learning._iso(value["selectionEnd"]) <
             learning._iso(value["evaluationStart"]) <= learning._iso(value["evaluationEnd"]), "portability-invalid-splits")
    _require(value["evaluationStart"] <= learning._iso(value["expectedLastObservation"]) <= value["evaluationEnd"], "portability-invalid-expected-end")
    policy = value["endSessionPolicy"]
    _require(type(policy) is dict and set(policy) == {"kind", "evidence"}, "portability-invalid-end-session-policy")
    if value["expectedLastObservation"] == value["evaluationEnd"]:
        _require(policy == {"kind": "evaluation-end-is-required-observation", "evidence": None}, "portability-invalid-end-session-policy")
    else:
        _require(policy["kind"] == "documented-last-session-on-or-before-end" and type(policy["evidence"]) is str
                 and bool(policy["evidence"].strip()), "portability-non-session-end-evidence-required")
    return value


def _descriptor(value: dict[str, Any], bars: list[Bar], as_of: str) -> list[str]:
    _require(type(value) is dict and set(value) == {"version", "source", "symbol", "fields", "retention", "findings"},
             "portability-invalid-descriptor")
    _require(value["version"] == "feed-description.v1" and value["symbol"] in ("SPY", "QQQ", "VAS"),
             "portability-invalid-descriptor")
    _require(type(value["source"]) is str and value["source"] and type(value["fields"]) is dict and set(value["fields"]) == set(FIELDS),
             "portability-invalid-descriptor")
    unresolved = []
    for key in FIELDS:
        item = value["fields"][key]
        _require(type(item) is dict and set(item) == {"value", "status", "evidence"} and
                 item["status"] in ("documented", "verified", "unresolved") and
                 type(item["value"]) is str and bool(item["value"]) and
                 type(item["evidence"]) is str, "portability-invalid-meaning")
        _require(item["status"] == "unresolved" or bool(item["evidence"]), "portability-missing-meaning-evidence")
        if item["status"] == "unresolved" and key != "costs":
            unresolved.append(key)
    _require(type(value["findings"]) is list, "portability-invalid-findings")
    for finding in value["findings"]:
        _require(type(finding) is dict and set(finding) == {"classification", "explanation", "evidence"} and
                 finding["classification"] in ("documented", "statistical-association", "unresolved") and
                 all(type(finding[k]) is str and bool(finding[k]) for k in finding), "portability-invalid-findings")
    retention_check(value["source"], value["retention"])
    _require(type(bars) is list and len(bars) <= 10000, "portability-row-limit")
    previous = ""
    for bar in bars:
        _require(isinstance(bar, Bar), "portability-invalid-observation-contract")
        _require(bar.symbol == value["symbol"] and bar.source == value["source"], "portability-source-identity-mismatch")
        _require(previous < learning._iso(bar.date) <= as_of, "portability-duplicate-unordered-or-future-date")
        previous = bar.date
        _require(type(bar.close) in (int, float) and math.isfinite(bar.close) and 1e-8 <= bar.close <= 1e12,
                 "portability-invalid-price")
    return unresolved


def _percentile(values: list[float], fraction: float = .95) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(fraction * len(ordered)) - 1]


def _correlation(left: list[float], right: list[float]) -> float | None:
    if len(left) < 3:
        return None
    a, b = mean(left), mean(right)
    numerator = math.fsum((x-a)*(y-b) for x, y in zip(left, right))
    denominator = math.sqrt(math.fsum((x-a)**2 for x in left) * math.fsum((y-b)**2 for y in right))
    return numerator / denominator if denominator else None


def _sign(value: float) -> int:
    return (value > 0) - (value < 0)


def _returns(bars: list[Bar], horizon: int) -> dict[str, tuple[str, float]]:
    return {bars[i].date: (bars[i-horizon].date, bars[i].close / bars[i-horizon].close - 1)
            for i in range(horizon, len(bars))}


def _movement(left: list[Bar], right: list[Bar], dates: set[str], horizon: int) -> dict[str, Any]:
    a, b = _returns(left, horizon), _returns(right, horizon)
    common = sorted(dates & a.keys() & b.keys())
    aligned = [d for d in common if a[d][0] == b[d][0]]
    xs, ys = [a[d][1] for d in aligned], [b[d][1] for d in aligned]
    differences = [abs(x-y) for x, y in zip(xs, ys)]
    return {"sampleCount": len(aligned), "excludedMismatchedStartDates": len(common)-len(aligned),
        "directionAgreement": mean([_sign(x) == _sign(y) for x, y in zip(xs, ys)]) if xs else None,
        "returnDifferenceMean": mean(differences) if differences else None,
        "returnDifferenceP95": _percentile(differences), "returnCorrelation": _correlation(xs, ys)}


def _select(bars: list[Bar], strategy: str, protocol: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]] | None:
    warmup = max(learning._warmup(p) for p in protocol["candidates"][strategy])
    train = _training_indices(bars, strategy, protocol)
    validation = [i for i in range(warmup-1, len(bars)-5)
                  if bars[i].date > protocol["trainEnd"] and bars[i+5].date <= protocol["selectionEnd"]]
    if len(train) < 20 or len(validation) < 10:
        return None
    fits, attempts = [], []
    for parameters in protocol["candidates"][strategy]:
        fit = _native_fit(bars, train, strategy, parameters)
        fits.append(fit)
        probabilities = [fit["states"][learning._state(bars, i, strategy, parameters)]["probabilityUp"] for i in validation]
        score = learning._score(probabilities, [int(bars[i+5].close > bars[i].close) for i in validation])
        attempts.append({"parameters": parameters, "selectionBrier": score})
    winner = min(range(len(attempts)), key=lambda i: (attempts[i]["selectionBrier"], i))
    return attempts[winner]["parameters"], fits[winner], attempts


def _training_indices(bars: list[Bar], strategy: str, protocol: dict[str, Any]) -> list[int]:
    # Both native fits use the same maximum-grid warmup and train cutoff.
    warmup = max(learning._warmup(p) for p in protocol["candidates"][strategy])
    return [i for i in range(warmup-1, len(bars)-5) if bars[i+5].date <= protocol["trainEnd"]]


def _cohort(bars: list[Bar], indices: list[int], strategy: str, protocol: dict[str, Any]) -> dict[str, Any]:
    return {"sampleCount": len(indices), "warmupObservations": max(learning._warmup(p) for p in protocol["candidates"][strategy]),
            "trainingCutoff": protocol["trainEnd"], "firstFeatureDate": bars[indices[0]].date if indices else None,
            "lastFeatureDate": bars[indices[-1]].date if indices else None,
            "lastOutcomeDate": bars[indices[-1]+5].date if indices else None}


def _native_fit(bars: list[Bar], indices: list[int], strategy: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return learning._fit([learning._state(bars, i, strategy, parameters) for i in indices],
                         [int(bars[i+5].close > bars[i].close) for i in indices], strategy)


def _observations(bars: list[Bar], strategy: str, parameters: dict[str, Any], fit: dict[str, Any], protocol: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = {}
    for i in range(learning._warmup(parameters)-1, len(bars)-5):
        if protocol["evaluationStart"] <= bars[i].date and bars[i+5].date <= protocol["evaluationEnd"]:
            state = learning._state(bars, i, strategy, parameters)
            rows[bars[i].date] = {"state": state,
                "previousState": learning._state(bars, i-1, strategy, parameters) if i >= learning._warmup(parameters) else None, "probabilityUp": fit["states"][state]["probabilityUp"],
                "label": int(bars[i+5].close > bars[i].close), "labelDate": bars[i+5].date}
    return rows


def _behaviour(left: dict[str, Any], right: dict[str, Any], strategy: str, stressed: set[str]) -> dict[str, Any]:
    common = sorted(left.keys() & right.keys())
    aligned = [d for d in common if left[d]["labelDate"] == right[d]["labelDate"]]
    trigger = "trigger-observed" if strategy == "volatility-band-accumulator" else "trend-confirmed"
    # Dates on which the strategy enters its active state, not every active day.
    def triggers(rows: dict[str, Any]) -> set[str]:
        result = set()
        for d in sorted(rows):
            if rows[d]["state"] == trigger and rows[d]["previousState"] != trigger:
                result.add(d)
        return result
    a, b = triggers(left), triggers(right)
    union = a | b
    differences = [abs(left[d]["probabilityUp"]-right[d]["probabilityUp"]) for d in aligned]
    stress = [d for d in aligned if d in stressed]
    return {"sampleCount": len(aligned), "excludedOutcomeDateMismatch": len(common)-len(aligned),
        "unpairedFraction": (len(left.keys() | right.keys())-len(aligned))/len(left.keys() | right.keys()) if left or right else None,
        "unpairedPredictionDates": len(left.keys() ^ right.keys()),
        "stateAgreement": mean([left[d]["state"] == right[d]["state"] for d in aligned]) if aligned else None,
        "labelAgreement": mean([left[d]["label"] == right[d]["label"] for d in aligned]) if aligned else None,
        "probabilityMeanAbsoluteDifference": mean(differences) if differences else None,
        "probabilityMaxAbsoluteDifference": max(differences) if differences else None,
        "triggerUnionCount": len(union), "triggerJaccard": len(a & b)/len(union) if union else None,
        "leftTriggerDates": sorted(a), "rightTriggerDates": sorted(b),
        "disagreementDates": [d for d in aligned if left[d] != right[d]],
        "stressSampleCount": len(stress), "stressStateAgreement": mean([left[d]["state"] == right[d]["state"] for d in stress]) if stress else None,
        "leftBrier": learning._score([left[d]["probabilityUp"] for d in aligned], [left[d]["label"] for d in aligned]) if aligned else None,
        "rightBrier": learning._score([right[d]["probabilityUp"] for d in aligned], [right[d]["label"] for d in aligned]) if aligned else None}


def evaluate_pair(left: list[Bar], right: list[Bar], left_descriptor: dict[str, Any], right_descriptor: dict[str, Any],
                  protocol_path: str | Path, *, as_of: str, case_dates: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """Evaluate both transfer directions without fitting on the reserved period.

    case_dates supplies reviewed earlyClose/corporateAction dates. Empty categories
    remain explicit unavailable evidence. Statistical associations never prove causes.
    """
    learning._iso(as_of)
    protocol = load_protocol(protocol_path)
    unresolved = _descriptor(left_descriptor, left, as_of) + _descriptor(right_descriptor, right, as_of)
    cases = case_dates or {"earlyClose": [], "corporateAction": []}
    _require(set(cases) == {"earlyClose", "corporateAction"} and all(type(v) is list for v in cases.values()), "portability-invalid-case-dates")
    for values in cases.values():
        for d in values:
            learning._iso(d)
    mismatch = left_descriptor["symbol"] != right_descriptor["symbol"]
    for field in ("instrument", "currency"):
        a, b = left_descriptor["fields"][field], right_descriptor["fields"][field]
        if a["status"] != "unresolved" and b["status"] != "unresolved" and a["value"] != b["value"]:
            mismatch = True
    start, end = protocol["evaluationStart"], protocol["evaluationEnd"]
    by_a, by_b = {bar.date: bar for bar in left}, {bar.date: bar for bar in right}
    common = sorted(d for d in by_a.keys() & by_b.keys() if start <= d <= end)
    dates = set(common)
    expected_end = protocol["expectedLastObservation"]
    reserve_coverage = {"expectedLastObservation": expected_end, "evaluationEnd": end, "asOf": as_of,
        "leftLastReservedObservation": max((d for d in by_a if start <= d <= end), default=None),
        "rightLastReservedObservation": max((d for d in by_b if start <= d <= end), default=None),
        "leftExpectedEndpointObserved": expected_end in by_a, "rightExpectedEndpointObserved": expected_end in by_b,
        "evaluationPeriodElapsed": as_of >= end, "endSessionPolicy": protocol["endSessionPolicy"]}
    reserve_complete = as_of >= end and expected_end in by_a and expected_end in by_b
    reserve_coverage["status"] = "complete" if reserve_complete else "incomplete"
    daily = _movement(left, right, dates, 1)
    five = _movement(left, right, dates, 5)
    ra, rb = _returns(left, 1), _returns(right, 1)
    stressed = {d for d in common if d in ra and d in rb and max(abs(ra[d][1]), abs(rb[d][1])) >= protocol["stressAbsoluteDailyReturn"]}
    def case(d: str) -> dict[str, Any]:
        return {"date": d, "leftClose": by_a[d].close, "rightClose": by_b[d].close,
                "absolutePriceDifference": by_b[d].close-by_a[d].close,
                "relativePriceDifference": by_b[d].close/by_a[d].close-1,
                "leftDailyReturn": ra[d][1] if d in ra else None, "rightDailyReturn": rb[d][1] if d in rb else None,
                "causalStatus": "unresolved-unless-separately-documented"}
    largest = sorted(common, key=lambda d: (-abs(by_b[d].close/by_a[d].close-1), d))[:10]
    largest_absolute = sorted(common, key=lambda d: (-abs(by_b[d].close-by_a[d].close), d))[:10]
    ordinary = [d for d in common if d not in stressed and not any(d in values for values in cases.values())]
    selected_ordinary = ordinary[::max(1, len(ordinary)//10)][:10]
    evidence_cases = {"ordinary": [case(d) for d in selected_ordinary], "largestRelative": [case(d) for d in largest], "largestAbsolute": [case(d) for d in largest_absolute],
                      "stressed": [case(d) for d in sorted(stressed)]}
    for category, values in cases.items():
        evidence_cases[category] = [case(d) for d in values if d in dates]
    conditional_discrepancies = {}
    for category, items in evidence_cases.items():
        differences = [abs(item["relativePriceDifference"]) for item in items]
        conditional_discrepancies[category] = {"classification": "statistical-association",
            "sampleCount": len(differences), "meanAbsoluteRelativePriceDifference": mean(differences) if differences else None,
            "p95AbsoluteRelativePriceDifference": _percentile(differences),
            "interpretation": "descriptive-selected-case-association-not-causal-attribution"}
    lag_rows = []
    # Observation lag is diagnostic only; never used to realign or tune strategies.
    return_dates = [d for d in common if d in ra and d in rb and ra[d][0] == rb[d][0]]
    for lag in (-2, -1, 0, 1, 2):
        pairs = [(ra[return_dates[i]][1], rb[return_dates[i+lag]][1]) for i in range(len(return_dates)) if 0 <= i+lag < len(return_dates)]
        lag_rows.append({"rightLagObservations": lag, "sampleCount": len(pairs),
                         "returnCorrelation": _correlation([x for x,y in pairs], [y for x,y in pairs])})
    strategies = []
    for strategy, tolerance in protocol["tolerances"].items():
        directions = []
        for train_bars, target_bars, source, target in ((left,right,left_descriptor,right_descriptor),(right,left,right_descriptor,left_descriptor)):
            selected = _select(train_bars, strategy, protocol)
            if selected is None:
                directions.append({"from": source["source"], "to": target["source"], "status": "insufficient-source-development-history"})
                continue
            parameters, fit, attempts = selected
            target_indices = _training_indices(target_bars, strategy, protocol)
            source_indices = _training_indices(train_bars, strategy, protocol)
            reference = _observations(train_bars, strategy, parameters, fit, protocol)
            transfer = _observations(target_bars, strategy, parameters, fit, protocol)
            transfer_metrics = _behaviour(reference, transfer, strategy, stressed)
            native_metrics = None
            if len(target_indices) >= 20:
                native_fit = _native_fit(target_bars, target_indices, strategy, parameters)
                native = _observations(target_bars, strategy, parameters, native_fit, protocol)
                native_metrics = _behaviour(reference, native, strategy, stressed)
            directions.append({"from": source["source"], "to": target["source"], "status": "evaluated",
                "parameters": parameters, "sourceFit": fit, "sourceFitSha256": learning._digest(fit),
                "transferFitSha256": learning._digest(fit), "candidateAttempts": attempts,
                "retuning": "none", "transfer": transfer_metrics,
                "nativeTrainingCohorts": {"source": _cohort(train_bars, source_indices, strategy, protocol),
                                          "target": _cohort(target_bars, target_indices, strategy, protocol)},
                "caseBehaviour": {category: [{"date": item["date"], "source": reference.get(item["date"]), "transfer": transfer.get(item["date"])} for item in items] for category,items in evidence_cases.items()}, "independentNativeFitSameParameters": native_metrics})
        reasons = [] if reserve_complete else ["reserved-comparison-period-incomplete"]
        breaches = []
        for direction in directions:
            if direction["status"] != "evaluated":
                reasons.append(direction["status"])
                continue
            metrics = direction["transfer"]
            if metrics["sampleCount"] < protocol["minimumSamples"]:
                reasons.append("insufficient-comparable-outcomes")
            if metrics["stressSampleCount"] < protocol["minimumStressSamples"]:
                reasons.append("insufficient-stress-observations")
            if metrics["triggerUnionCount"] < protocol["minimumTriggerUnion"]:
                reasons.append("insufficient-trigger-transitions")
            combined = {**metrics, "directionAgreement": five["directionAgreement"], "returnDifferenceP95": five["returnDifferenceP95"]}
            for key, limit in tolerance.items():
                actual = combined[key]
                if actual is None:
                    reasons.append("unavailable-"+key)
                elif (actual > limit if key in ("probabilityMeanAbsoluteDifference", "returnDifferenceP95", "unpairedFraction") else actual < limit):
                    breaches.append(key)
        if unresolved:
            reasons.append("unresolved-data-meaning:"+",".join(sorted(set(unresolved))))
        verdict = "rejected" if mismatch else "inconclusive" if reasons else "rejected" if breaches else "supported"
        strategies.append({"verdictScope": "same-source-fitted-model-transfer-without-retuning", "nativeFitScope": "descriptive-independent-calibration-no-compatibility-verdict", "strategy": strategy, "verdict": verdict, "reasons": sorted(set(reasons)) + (["verified-instrument-or-currency-mismatch"] if mismatch else []),
                           "toleranceBreaches": sorted(set(breaches)), "tolerances": tolerance, "directions": directions})
    return {"version": VERSION, "protocolSha256": protocol["sha256"], "evidenceKind": protocol["evidenceKind"],
        "inputSeriesSha256": [learning._digest([{k: getattr(bar,k) for k in ("date","symbol","source","close")} for bar in series]) for series in (left,right)],
        "descriptors": [left_descriptor, right_descriptor], "retention": [left_descriptor["retention"],right_descriptor["retention"]],
        "retentionScope": "all-input-copies-hashes-models-and-mixed-reports", "normalization": protocol["normalization"],
        "reserveCoverage": reserve_coverage, "alignedDateCount": len(common), "missingFromLeft": sorted(d for d in by_b.keys()-by_a.keys() if start <= d <= end),
        "missingFromRight": sorted(d for d in by_a.keys()-by_b.keys() if start <= d <= end),
        "dailyMovements": daily, "fiveObservationMovements": five, "leadLagAssociations": lag_rows,
        "stressMovements": _movement(left,right,stressed,1), "cases": evidence_cases,
        "conditionalDiscrepancies": conditional_discrepancies,
        "caseCoverage": {k: "available" if v else "unavailable" for k,v in evidence_cases.items()},
        "strategies": strategies, "limitations": ["same-market-events-not-independent-predictive-confirmation",
            "historical-robustness-only-not-forward-predictive-evidence", "overlapping-five-observation-outcomes-dependent",
            "case-dates-supplied-not-complete-exchange-calendar", "price-returns-exclude-trading-costs-and-distributions",
            "unknown-causes-preserved-not-automatic-rejection", "subscription-status-operator-attested-not-automatically-detected", "no-combined-provider-training-series"],
        "boundary": dict(learning.BOUNDARY)}
