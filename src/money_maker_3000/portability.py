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
from typing import Any, Callable

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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _clock(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    _require(isinstance(value, datetime) and value.utcoffset() is not None,
             "portability-invalid-clock")
    return value.astimezone(timezone.utc)


def _series_digest(bars: list[Bar]) -> str:
    return learning._digest([{key: getattr(bar, key) for key in ("date", "symbol", "source", "close")}
                             for bar in bars])


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
    _require(type(report) is dict, "portability-invalid-report")
    if "descriptors" in report:
        _require(type(report["descriptors"]) is list
                 and len(report["descriptors"]) == 2
                 and all(type(descriptor) is dict and type(descriptor.get("source")) is str
                         and type(descriptor.get("retention")) is dict
                         for descriptor in report["descriptors"]), "portability-invalid-report")
        for descriptor in report["descriptors"]:
            retention_check(descriptor["source"], descriptor["retention"])
    # Protocol documents deliberately retain their own schema.  A completed
    # portability report, however, is a reusable mixed-source derivative and
    # must be sealed before it can be read by the coordinator.
    document = report
    if report.get("version") == VERSION and type(report.get("descriptors")) is list:
        _require("sha256" not in report, "portability-sealed-report-rewrite-forbidden")
        _validate_report(report)
        document = {**report, "sha256": learning._digest(report)}
    raw = learning._canonical(document)
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


def load_report(path: str | Path, *, protocol_path: str | Path,
                current_retentions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Read private derived evidence only after checking current operator retention state."""
    _require(type(current_retentions) is dict and bool(current_retentions), "portability-retention-required")
    for source, retention in current_retentions.items():
        retention_check(source, retention)
    protocol = load_protocol(protocol_path)
    report = learning._json(learning._read(path, 8 * 1024 * 1024))
    _validate_report(report, protocol=protocol, current_retentions=current_retentions)
    return report


def _validate_report(report: Any, *, protocol: dict[str, Any] | None = None,
                     current_retentions: dict[str, dict[str, Any]] | None = None) -> None:
    """Validate a sealed, final portability report before any verdict is reused."""
    _require(type(report) is dict, "portability-invalid-report")
    expected = {"version", "protocolSha256", "evidenceKind", "inputSeriesSha256", "descriptors", "retention",
                "retentionScope", "normalization", "reserveEndpointCoverage", "reserveIntervalCompleteness", "alignedDateCount", "missingFromLeft",
                "missingFromRight", "dailyMovements", "fiveObservationMovements", "leadLagAssociations",
                "stressMovements", "cases", "conditionalDiscrepancies", "caseCoverage", "strategies",
                "limitations", "boundary"}
    sealed = set(report) == expected | {"sha256"}
    unsealed = set(report) == expected
    _require(sealed or (current_retentions is None and unsealed), "portability-invalid-report")
    if sealed:
        payload = {key: value for key, value in report.items() if key != "sha256"}
        _require(learning._hash(report["sha256"]) and report["sha256"] == learning._digest(payload),
                 "portability-report-checksum-mismatch")
    _require(report["version"] == VERSION and learning._hash(report["protocolSha256"])
             and report["evidenceKind"] in ("retrospective-historical-robustness", "prospectively-reserved-historical-robustness")
             and report["boundary"] == learning.BOUNDARY, "portability-invalid-report")
    descriptors = report["descriptors"]
    _require(type(descriptors) is list and len(descriptors) == 2
             and all(type(item) is dict for item in descriptors), "portability-invalid-report")
    sources = [item.get("source") for item in descriptors]
    _require(all(type(source) is str and source for source in sources) and len(set(sources)) == len(sources),
             "portability-retention-source-mismatch")
    _require(type(report["retention"]) is list and report["retention"] == [item.get("retention") for item in descriptors],
             "portability-retention-source-mismatch")
    for descriptor in descriptors:
        _descriptor(descriptor, None, "9999-12-31", _utc_now())
    _require(type(report["inputSeriesSha256"]) is list and len(report["inputSeriesSha256"]) == 2
             and all(learning._hash(value) for value in report["inputSeriesSha256"])
             and report["inputSeriesSha256"] == [descriptor["retrieval"]["inputSeriesSha256"] for descriptor in descriptors],
             "portability-invalid-report")
    _require(report["retentionScope"] == "all-input-copies-hashes-models-and-mixed-reports"
             and report["normalization"] == "none-original-observations-preserved"
             and report["limitations"] == ["same-market-events-not-independent-predictive-confirmation",
                 "historical-robustness-only-not-forward-predictive-evidence", "overlapping-five-observation-outcomes-dependent",
                 "case-dates-supplied-not-complete-exchange-calendar", "reserved-interval-completeness-unproven-without-pinned-session-calendar", "price-returns-exclude-trading-costs-and-distributions",
                 "unknown-causes-preserved-not-automatic-rejection", "subscription-status-operator-attested-not-automatically-detected",
                 "no-combined-provider-training-series"], "portability-invalid-report")
    _require(learning._integer(report["alignedDateCount"], 0, 10000), "portability-invalid-report")
    for key in ("missingFromLeft", "missingFromRight"):
        _require(type(report[key]) is list and report[key] == sorted(set(report[key])) and all(learning._iso(value) for value in report[key]),
                 "portability-invalid-report")
    _validate_reserve_endpoint_coverage(report["reserveEndpointCoverage"], descriptors)
    _validate_reserve_interval_completeness(report["reserveIntervalCompleteness"])
    if protocol is not None:
        coverage = report["reserveEndpointCoverage"]
        _require(report["protocolSha256"] == protocol["sha256"]
                 and report["evidenceKind"] == protocol["evidenceKind"]
                 and report["normalization"] == protocol["normalization"]
                 and coverage["expectedLastObservation"] == protocol["expectedLastObservation"]
                 and coverage["evaluationEnd"] == protocol["evaluationEnd"]
                 and coverage["endSessionPolicy"] == protocol["endSessionPolicy"],
                 "portability-report-protocol-mismatch")
    _validate_movement(report["dailyMovements"])
    _validate_movement(report["fiveObservationMovements"])
    _validate_movement(report["stressMovements"])
    _validate_cases(report)
    _require(type(report["strategies"]) is list and {item.get("strategy") for item in report["strategies"]} == set(TOLERANCES)
             and len(report["strategies"]) == len(TOLERANCES), "portability-invalid-report")
    for strategy in report["strategies"]:
        _require(type(strategy) is dict and strategy.get("verdict") in ("supported", "inconclusive", "rejected")
                 and type(strategy.get("reasons")) is list and type(strategy.get("toleranceBreaches")) is list,
                 "portability-invalid-report")
        _require(set(strategy) == {"verdictScope", "nativeFitScope", "strategy", "verdict", "reasons", "toleranceBreaches", "tolerances", "directions"}
                 and strategy["verdictScope"] == "same-source-fitted-model-transfer-without-retuning"
                 and strategy["nativeFitScope"] == "descriptive-independent-calibration-no-compatibility-verdict"
                 and strategy["tolerances"] == TOLERANCES[strategy["strategy"]]
                 and all(type(reason) is str and reason for reason in strategy["reasons"])
                 and strategy["reasons"] == sorted(set(strategy["reasons"]))
                 and set(strategy["toleranceBreaches"]) <= set(strategy["tolerances"])
                 and strategy["toleranceBreaches"] == sorted(set(strategy["toleranceBreaches"]))
                 and type(strategy["directions"]) is list and len(strategy["directions"]) == 2,
                 "portability-invalid-report")
        breaches, reasons = _validate_directions(strategy, descriptors, report)
        _require(strategy["toleranceBreaches"] == breaches, "portability-verdict-contract-mismatch")
        if report["reserveEndpointCoverage"]["status"] != "complete":
            reasons.add("reserved-comparison-endpoint-incomplete")
        if report["reserveIntervalCompleteness"]["status"] != "proven":
            reasons.add("reserved-comparison-interval-completeness-unproven")
        unresolved = []
        for descriptor in descriptors:
            unresolved.extend(key for key, value in descriptor["fields"].items()
                              if value["status"] == "unresolved" and key != "costs")
        if unresolved:
            reasons.add("unresolved-data-meaning:" + ",".join(sorted(set(unresolved))))
        mismatch = descriptors[0]["symbol"] != descriptors[1]["symbol"] or any(
            left["status"] != "unresolved" and right["status"] != "unresolved" and left["value"] != right["value"]
            for left, right in ((descriptors[0]["fields"][field], descriptors[1]["fields"][field]) for field in ("instrument", "currency")))
        if mismatch:
            reasons.add("verified-instrument-or-currency-mismatch")
        _require(strategy["reasons"] == sorted(reasons), "portability-verdict-contract-mismatch")
        expected_verdict = "rejected" if mismatch else "inconclusive" if reasons else "rejected" if strategy["toleranceBreaches"] else "supported"
        _require(strategy["verdict"] == expected_verdict, "portability-verdict-contract-mismatch")
    if current_retentions is not None:
        _require(set(sources) == set(current_retentions), "portability-retention-source-mismatch")
        _require(all(descriptor["retention"] == current_retentions[descriptor["source"]] for descriptor in descriptors),
                 "portability-retention-source-mismatch")


def _finite(value: Any, *, low: float | None = None, high: float | None = None) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value) and (low is None or value >= low) and (high is None or value <= high)
    except OverflowError:
        return False


def _optional_number(value: Any, *, low: float | None = None, high: float | None = None) -> bool:
    return value is None or _finite(value, low=low, high=high)


def _validate_reserve_endpoint_coverage(value: Any, descriptors: list[dict[str, Any]]) -> None:
    _require(type(value) is dict and set(value) == {"expectedLastObservation", "evaluationEnd", "asOf",
             "leftLastReservedObservation", "rightLastReservedObservation", "leftExpectedEndpointObserved",
             "rightExpectedEndpointObserved", "evaluationPeriodElapsed", "asOfBoundedByRetrieval",
             "leftRetrievedAt", "rightRetrievedAt", "retrievalEvidenceCoversEvaluationEnd", "endSessionPolicy", "status"}, "portability-invalid-report")
    for key in ("expectedLastObservation", "evaluationEnd", "asOf"):
        learning._iso(value[key])
    _require(value["expectedLastObservation"] <= value["evaluationEnd"] <= value["asOf"]
             and all(item is None or learning._iso(item) for item in (value["leftLastReservedObservation"], value["rightLastReservedObservation"]))
             and all(item is None or item <= value["evaluationEnd"] for item in (value["leftLastReservedObservation"], value["rightLastReservedObservation"]))
             and all(type(value[key]) is bool for key in ("leftExpectedEndpointObserved", "rightExpectedEndpointObserved", "evaluationPeriodElapsed", "asOfBoundedByRetrieval", "retrievalEvidenceCoversEvaluationEnd"))
             and type(value["endSessionPolicy"]) is dict and set(value["endSessionPolicy"]) == {"kind", "evidence"}
             and value["status"] in ("complete", "incomplete"), "portability-invalid-report")
    policy = value["endSessionPolicy"]
    if value["expectedLastObservation"] == value["evaluationEnd"]:
        _require(policy == {"kind": "evaluation-end-is-required-observation", "evidence": None}, "portability-invalid-report")
    else:
        _require(policy["kind"] == "documented-last-session-on-or-before-end"
                 and type(policy["evidence"]) is str and bool(policy["evidence"].strip()), "portability-invalid-report")
    retrieved = [_timestamp(descriptor["retrieval"]["retrievedAt"]).astimezone(timezone.utc) for descriptor in descriptors]
    _require([value["leftRetrievedAt"], value["rightRetrievedAt"]] == [stamp.isoformat() for stamp in retrieved]
             and value["asOfBoundedByRetrieval"] is True
             and value["asOf"] <= min(stamp.date().isoformat() for stamp in retrieved)
             and value["retrievalEvidenceCoversEvaluationEnd"] == all(stamp.date().isoformat() > value["evaluationEnd"] for stamp in retrieved)
             and value["evaluationPeriodElapsed"] == (value["asOf"] >= value["evaluationEnd"])
             and (not value["leftExpectedEndpointObserved"] or value["leftLastReservedObservation"] == value["expectedLastObservation"])
             and (not value["rightExpectedEndpointObserved"] or value["rightLastReservedObservation"] == value["expectedLastObservation"]),
             "portability-reserve-coverage-mismatch")
    complete = (value["evaluationPeriodElapsed"] and value["retrievalEvidenceCoversEvaluationEnd"]
                and value["leftExpectedEndpointObserved"] and value["rightExpectedEndpointObserved"])
    _require(value["status"] == ("complete" if complete else "incomplete"), "portability-reserve-coverage-mismatch")


def _validate_reserve_interval_completeness(value: Any) -> None:
    """V1 intentionally proves endpoints only, never every reserved session."""
    _require(value == {"status": "unproven", "reason": "no-pinned-reserved-session-calendar-evidence",
                       "semantics": "endpoint-observation-check-only-not-interval-completeness"},
             "portability-interval-completeness-mismatch")


def _validate_movement(value: Any) -> None:
    _require(type(value) is dict and set(value) == {"sampleCount", "excludedMismatchedStartDates", "directionAgreement",
             "returnDifferenceMean", "returnDifferenceP95", "returnCorrelation"}
             and learning._integer(value["sampleCount"], 0, 10000)
             and learning._integer(value["excludedMismatchedStartDates"], 0, 10000)
             and _optional_number(value["directionAgreement"], low=0, high=1)
             and _optional_number(value["returnDifferenceMean"], low=0)
             and _optional_number(value["returnDifferenceP95"], low=0)
             and _optional_number(value["returnCorrelation"], low=-1, high=1), "portability-invalid-report")


def _validate_cases(report: dict[str, Any]) -> None:
    expected = {"ordinary", "largestRelative", "largestAbsolute", "stressed", "earlyClose", "corporateAction"}
    cases = report["cases"]
    _require(type(cases) is dict and set(cases) == expected and type(report["conditionalDiscrepancies"]) is dict
             and set(report["conditionalDiscrepancies"]) == expected and type(report["caseCoverage"]) is dict
             and set(report["caseCoverage"]) == expected and type(report["leadLagAssociations"]) is list
             and len(report["leadLagAssociations"]) == 5, "portability-invalid-report")
    for row, lag in zip(report["leadLagAssociations"], range(-2, 3)):
        _require(type(row) is dict and set(row) == {"rightLagObservations", "sampleCount", "returnCorrelation"}
                 and row["rightLagObservations"] == lag and learning._integer(row["sampleCount"], 0, 10000)
                 and _optional_number(row["returnCorrelation"], low=-1, high=1), "portability-invalid-report")
    for key, rows in cases.items():
        _require(type(rows) is list and len(rows) <= 10000 and report["caseCoverage"][key] == ("available" if rows else "unavailable"),
                 "portability-invalid-report")
        for row in rows:
            _require(type(row) is dict and set(row) == {"date", "leftClose", "rightClose", "absolutePriceDifference",
                     "relativePriceDifference", "leftDailyReturn", "rightDailyReturn", "causalStatus"}
                     and learning._iso(row["date"]) and _finite(row["leftClose"], low=1e-8)
                     and _finite(row["rightClose"], low=1e-8) and _finite(row["absolutePriceDifference"])
                     and _finite(row["relativePriceDifference"]) and _optional_number(row["leftDailyReturn"])
                     and _optional_number(row["rightDailyReturn"])
                     and row["causalStatus"] == "unresolved-unless-separately-documented", "portability-invalid-report")
        summary = report["conditionalDiscrepancies"][key]
        _require(type(summary) is dict and set(summary) == {"classification", "sampleCount", "meanAbsoluteRelativePriceDifference",
                 "p95AbsoluteRelativePriceDifference", "interpretation"} and summary["classification"] == "statistical-association"
                 and summary["sampleCount"] == len(rows) and _optional_number(summary["meanAbsoluteRelativePriceDifference"], low=0)
                 and _optional_number(summary["p95AbsoluteRelativePriceDifference"], low=0)
                 and summary["interpretation"] == "descriptive-selected-case-association-not-causal-attribution", "portability-invalid-report")


def _validate_behaviour(value: Any) -> None:
    _require(type(value) is dict and set(value) == {"sampleCount", "excludedOutcomeDateMismatch", "unpairedFraction",
             "unpairedPredictionDates", "stateAgreement", "labelAgreement", "probabilityMeanAbsoluteDifference",
             "probabilityMaxAbsoluteDifference", "triggerUnionCount", "triggerJaccard", "leftTriggerDates",
             "rightTriggerDates", "disagreementDates", "stressSampleCount", "stressStateAgreement", "leftBrier", "rightBrier"}
             and all(learning._integer(value[key], 0, 10000) for key in ("sampleCount", "excludedOutcomeDateMismatch", "unpairedPredictionDates", "triggerUnionCount", "stressSampleCount"))
             and _optional_number(value["unpairedFraction"], low=0, high=1)
             and all(_optional_number(value[key], low=0, high=1) for key in ("stateAgreement", "labelAgreement", "probabilityMeanAbsoluteDifference", "probabilityMaxAbsoluteDifference", "triggerJaccard", "stressStateAgreement", "leftBrier", "rightBrier"))
             and all(type(value[key]) is list and value[key] == sorted(set(value[key])) and all(learning._iso(item) for item in value[key])
                     for key in ("leftTriggerDates", "rightTriggerDates", "disagreementDates")), "portability-invalid-report")


def _validate_cohort(value: Any) -> None:
    _require(type(value) is dict and set(value) == {"sampleCount", "warmupObservations", "trainingCutoff", "firstFeatureDate", "lastFeatureDate", "lastOutcomeDate"}
             and learning._integer(value["sampleCount"], 0, 10000) and learning._integer(value["warmupObservations"], 1, 10000)
             and learning._iso(value["trainingCutoff"])
             and all(item is None or learning._iso(item) for item in (value["firstFeatureDate"], value["lastFeatureDate"], value["lastOutcomeDate"])), "portability-invalid-report")


def _validate_directions(strategy: dict[str, Any], descriptors: list[dict[str, Any]], report: dict[str, Any]) -> tuple[list[str], set[str]]:
    sources = [descriptor["source"] for descriptor in descriptors]
    expected_routes = [(sources[0], sources[1]), (sources[1], sources[0])]
    breaches = set()
    reasons = set()
    for direction, route in zip(strategy["directions"], expected_routes):
        _require(type(direction) is dict and (direction.get("from"), direction.get("to")) == route
                 and direction.get("status") in ("evaluated", "insufficient-source-development-history"), "portability-invalid-report")
        if direction["status"] != "evaluated":
            _require(set(direction) == {"from", "to", "status"}, "portability-invalid-report")
            reasons.add(direction["status"])
            continue
        _require(set(direction) == {"from", "to", "status", "parameters", "sourceFit", "sourceFitSha256", "transferFitSha256",
                 "candidateAttempts", "retuning", "transfer", "nativeTrainingCohorts", "caseBehaviour", "independentNativeFitSameParameters"}
                 and direction["parameters"] in learning.candidate_grid(strategy["strategy"])
                 and direction["retuning"] == "none" and learning._hash(direction["sourceFitSha256"])
                 and direction["sourceFitSha256"] == direction["transferFitSha256"]
                 and direction["sourceFitSha256"] == learning._digest(direction["sourceFit"]), "portability-invalid-report")
        learning._validate_fit(direction["sourceFit"], strategy["strategy"])
        attempts = direction["candidateAttempts"]
        _require(type(attempts) is list and len(attempts) == len(learning.candidate_grid(strategy["strategy"]))
                 and all(type(item) is dict and set(item) == {"parameters", "selectionBrier"} and _finite(item["selectionBrier"], low=0, high=1) for item in attempts)
                 and [item["parameters"] for item in attempts] == learning.candidate_grid(strategy["strategy"]),
                 "portability-invalid-report")
        _require(direction["parameters"] == min(attempts, key=lambda item: item["selectionBrier"])["parameters"],
                 "portability-selection-contract-mismatch")
        _validate_behaviour(direction["transfer"])
        transfer = direction["transfer"]
        if transfer["sampleCount"] < 100:
            reasons.add("insufficient-comparable-outcomes")
        if transfer["stressSampleCount"] < 10:
            reasons.add("insufficient-stress-observations")
        if transfer["triggerUnionCount"] < 5:
            reasons.add("insufficient-trigger-transitions")
        cohorts = direction["nativeTrainingCohorts"]
        _require(type(cohorts) is dict and set(cohorts) == {"source", "target"}, "portability-invalid-report")
        _validate_cohort(cohorts["source"])
        _validate_cohort(cohorts["target"])
        native = direction["independentNativeFitSameParameters"]
        _require(native is None or isinstance(native, dict), "portability-invalid-report")
        if native is not None:
            _validate_behaviour(native)
        cases = direction["caseBehaviour"]
        _require(type(cases) is dict and set(cases) == set(report["cases"])
                 and all(type(rows) is list and all(type(row) is dict and set(row) == {"date", "source", "transfer"}
                         and learning._iso(row["date"]) for row in rows) for rows in cases.values()), "portability-invalid-report")
        combined = {**direction["transfer"], "directionAgreement": report["fiveObservationMovements"]["directionAgreement"],
                    "returnDifferenceP95": report["fiveObservationMovements"]["returnDifferenceP95"]}
        for key, limit in strategy["tolerances"].items():
            actual = combined[key]
            if actual is None:
                reasons.add("unavailable-" + key)
            elif actual > limit if key in ("probabilityMeanAbsoluteDifference", "returnDifferenceP95", "unpairedFraction") else actual < limit:
                breaches.add(key)
    return sorted(breaches), reasons


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


def _descriptor(value: dict[str, Any], bars: list[Bar] | None, as_of: str, current_utc: datetime) -> list[str]:
    _require(type(value) is dict and set(value) == {"version", "source", "symbol", "fields", "retention", "findings", "retrieval"},
             "portability-invalid-descriptor")
    _require(value["version"] == "feed-description.v2" and value["symbol"] in ("SPY", "QQQ", "VAS"),
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
    _require(bars is None or (type(bars) is list and len(bars) <= 10000), "portability-row-limit")
    previous = ""
    for bar in bars or []:
        _require(isinstance(bar, Bar), "portability-invalid-observation-contract")
        _require(bar.symbol == value["symbol"] and bar.source == value["source"], "portability-source-identity-mismatch")
        _require(previous < learning._iso(bar.date) <= as_of, "portability-duplicate-unordered-or-future-date")
        previous = bar.date
        _require(type(bar.close) in (int, float) and math.isfinite(bar.close) and 1e-8 <= bar.close <= 1e12,
                 "portability-invalid-price")
    retrieval = value["retrieval"]
    _require(type(retrieval) is dict and set(retrieval) == {"retrievedAt", "inputSeriesSha256", "evidence"}
             and learning._hash(retrieval["inputSeriesSha256"])
             and (bars is None or retrieval["inputSeriesSha256"] == _series_digest(bars))
             and type(retrieval["evidence"]) is str and bool(retrieval["evidence"].strip()),
             "portability-invalid-retrieval-evidence")
    retrieved_at = _timestamp(retrieval["retrievedAt"]).astimezone(timezone.utc)
    _require(retrieved_at <= current_utc, "portability-future-retrieval-evidence")
    _require(bars is None or as_of <= retrieved_at.date().isoformat(), "portability-as-of-not-bound-to-retrieval")
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
                  protocol_path: str | Path, *, as_of: str, case_dates: dict[str, list[str]] | None = None,
                  clock: Callable[[], datetime] = _utc_now) -> dict[str, Any]:
    """Evaluate both transfer directions without fitting on the reserved period.

    case_dates supplies reviewed earlyClose/corporateAction dates. Empty categories
    remain explicit unavailable evidence. Statistical associations never prove causes.
    """
    learning._iso(as_of)
    current_utc = _clock(clock)
    _require(as_of <= current_utc.date().isoformat(), "portability-future-as-of")
    protocol = load_protocol(protocol_path)
    unresolved = (_descriptor(left_descriptor, left, as_of, current_utc)
                  + _descriptor(right_descriptor, right, as_of, current_utc))
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
    retrieved = [_timestamp(descriptor["retrieval"]["retrievedAt"]).astimezone(timezone.utc)
                 for descriptor in (left_descriptor, right_descriptor)]
    reserve_endpoint_coverage = {"expectedLastObservation": expected_end, "evaluationEnd": end, "asOf": as_of,
        "leftLastReservedObservation": max((d for d in by_a if start <= d <= end), default=None),
        "rightLastReservedObservation": max((d for d in by_b if start <= d <= end), default=None),
        "leftExpectedEndpointObserved": expected_end in by_a, "rightExpectedEndpointObserved": expected_end in by_b,
        "evaluationPeriodElapsed": as_of >= end, "asOfBoundedByRetrieval": True,
        "leftRetrievedAt": retrieved[0].isoformat(), "rightRetrievedAt": retrieved[1].isoformat(),
        "retrievalEvidenceCoversEvaluationEnd": all(stamp.date().isoformat() > end for stamp in retrieved),
        "endSessionPolicy": protocol["endSessionPolicy"]}
    endpoint_complete = (reserve_endpoint_coverage["evaluationPeriodElapsed"]
                         and reserve_endpoint_coverage["retrievalEvidenceCoversEvaluationEnd"]
                         and expected_end in by_a and expected_end in by_b)
    reserve_endpoint_coverage["status"] = "complete" if endpoint_complete else "incomplete"
    reserve_interval_completeness = {"status": "unproven", "reason": "no-pinned-reserved-session-calendar-evidence",
                                     "semantics": "endpoint-observation-check-only-not-interval-completeness"}
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
        reasons = []
        if not endpoint_complete:
            reasons.append("reserved-comparison-endpoint-incomplete")
        # Endpoint observations cannot prove there was no shared or one-sided
        # interior gap.  V1 has no pinned exchange-session calendar, so this
        # remains conclusive only about endpoint presence and all verdicts stay
        # inconclusive until a future calendar-backed protocol is reviewed.
        reasons.append("reserved-comparison-interval-completeness-unproven")
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
        "inputSeriesSha256": [_series_digest(series) for series in (left,right)],
        "descriptors": [left_descriptor, right_descriptor], "retention": [left_descriptor["retention"],right_descriptor["retention"]],
        "retentionScope": "all-input-copies-hashes-models-and-mixed-reports", "normalization": protocol["normalization"],
        "reserveEndpointCoverage": reserve_endpoint_coverage, "reserveIntervalCompleteness": reserve_interval_completeness,
        "alignedDateCount": len(common), "missingFromLeft": sorted(d for d in by_b.keys()-by_a.keys() if start <= d <= end),
        "missingFromRight": sorted(d for d in by_a.keys()-by_b.keys() if start <= d <= end),
        "dailyMovements": daily, "fiveObservationMovements": five, "leadLagAssociations": lag_rows,
        "stressMovements": _movement(left,right,stressed,1), "cases": evidence_cases,
        "conditionalDiscrepancies": conditional_discrepancies,
        "caseCoverage": {k: "available" if v else "unavailable" for k,v in evidence_cases.items()},
        "strategies": strategies, "limitations": ["same-market-events-not-independent-predictive-confirmation",
            "historical-robustness-only-not-forward-predictive-evidence", "overlapping-five-observation-outcomes-dependent",
            "case-dates-supplied-not-complete-exchange-calendar", "reserved-interval-completeness-unproven-without-pinned-session-calendar", "price-returns-exclude-trading-costs-and-distributions",
            "unknown-causes-preserved-not-automatic-rejection", "subscription-status-operator-attested-not-automatically-detected", "no-combined-provider-training-series"],
        "boundary": dict(learning.BOUNDARY)}
