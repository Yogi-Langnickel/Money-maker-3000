"""Separately authorized read-only market collector; never imported by the worker.

The default eToro research/retention gate is closed. An authenticated identity
probe is possible without retaining prices. Local v2 observations preserve
missing unused fields explicitly; they do not relax market-history-csv.v1.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import ssl
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

VERSION = "market-observations.v2"
TERMS_URL = "https://www.etoro.com/wp-content/uploads/2026/03/Master_eToro_Builders_Economy_Terms_17-Feb-2026-clean_R.pdf"
DOCS_URL = "https://api-portal.etoro.com/api-reference/market-data/get-instrument-candle-history"
SYMBOLS = {"SPY": ("SPY", "USD", ("spdr", "s&p")), "QQQ": ("QQQ", "USD", ("invesco", "qqq")), "VAS": ("VAS.ASX", "AUD", ("vanguard", "australian"))}
PRICE_FIELDS = ("open", "high", "low", "close", "volume")
HTTP_ERROR_BODY_CAP = 16 * 1024
_CLOUDFLARE_BROWSER_SIGNATURE_TITLE = "Error 1010: Access denied"
_CLOUDFLARE_BROWSER_SIGNATURE_DETAIL = "The site owner has blocked access based on your browser's signature."


class CollectionError(ValueError):
    """Only controlled codes, never provider payloads or credential values."""


def _utc(value: str) -> datetime:
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError
        return stamp.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        raise CollectionError("invalid-timestamp") from None


def _instrument_id(value: object) -> bool:
    return type(value) is int and 0 < value < 2**31


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _json(data: bytes) -> object:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise CollectionError("duplicate-json-key")
            result[key] = value
        return result
    try:
        return json.loads(data, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(CollectionError("invalid-json-number")))
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise CollectionError("invalid-json") from None


def _private_read(path: Path, cap: int) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            s = os.fstat(stream.fileno())
            if not stat.S_ISREG(s.st_mode) or s.st_uid != os.geteuid() or s.st_nlink != 1 or stat.S_IMODE(s.st_mode) & 0o077:
                raise CollectionError("private-file-permissions")
            data = stream.read(cap + 1)
            if len(data) > cap:
                raise CollectionError("private-file-too-large")
            return data
    except OSError:
        raise CollectionError("private-file-unavailable") from None


def validate_observations(rows: list[dict], required_fields=("close",)) -> tuple[dict, ...]:
    """Exact local v2 row contract. Absence is null, never fabricated OHLCV."""
    if not isinstance(rows, list) or len(rows) > 100000 or any(f not in PRICE_FIELDS for f in required_fields):
        raise CollectionError("invalid-observations")
    result, previous = [], None
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"date", "timestamp", *PRICE_FIELDS}:
            raise CollectionError("invalid-observation-fields")
        try:
            day = date.fromisoformat(row["date"])
        except (ValueError, TypeError):
            raise CollectionError("invalid-observation-date") from None
        stamp = _utc(row["timestamp"])
        if day.isoformat() != row["date"] or day != stamp.date() or (previous is not None and day <= previous):
            raise CollectionError("unordered-or-duplicate-observations")
        previous = day
        for field in PRICE_FIELDS:
            value = row[field]
            if value is None:
                if field == "close" or field in required_fields:
                    raise CollectionError("required-observation-field-missing")
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise CollectionError("invalid-observation-number")
            try:
                valid = math.isfinite(value) and value >= 0 if field == "volume" else math.isfinite(value) and value > 0
            except OverflowError:
                valid = False
            if not valid:
                raise CollectionError("invalid-observation-number")
        low, high = row["low"], row["high"]
        if low is not None and high is not None and low > high:
            raise CollectionError("invalid-ohlc-range")
        for field in ("open", "close"):
            value = row[field]
            if value is not None and ((low is not None and value < low) or (high is not None and value > high)):
                raise CollectionError("invalid-ohlc-range")
        result.append(dict(row))
    return tuple(result)


def parse_candles(payload: dict, instrument_id: int, retrieved_at: str) -> dict:
    """Normalize only documented fields; retain start timestamps and null volume.

    A candle must have started at least 26 hours before retrieval. This is a
    conservative completion policy, not a verified exchange-session calendar.
    """
    if not _instrument_id(instrument_id):
        raise CollectionError("candle-instrument-mismatch")
    now = _utc(retrieved_at)
    if not isinstance(payload, dict) or payload.get("interval") != "OneDay":
        raise CollectionError("unexpected-candle-interval")
    groups = payload.get("candles")
    if (not isinstance(groups, list) or len(groups) != 1 or not isinstance(groups[0], dict)
            or type(groups[0].get("instrumentId")) is not int
            or groups[0].get("instrumentId") != instrument_id):
        raise CollectionError("candle-instrument-mismatch")
    candles = groups[0].get("candles")
    if not isinstance(candles, list) or len(candles) > 1000:
        raise CollectionError("invalid-candle-count")
    observations, duplicates, unfinished = {}, 0, 0
    for candle in candles:
        if (not isinstance(candle, dict) or type(candle.get("instrumentID")) is not int
                or candle.get("instrumentID") != instrument_id):
            raise CollectionError("candle-instrument-mismatch")
        stamp = _utc(candle.get("fromDate"))
        row = {"date": stamp.date().isoformat(), "timestamp": stamp.isoformat().replace("+00:00", "Z"), **{f: candle.get(f) for f in PRICE_FIELDS}}
        validate_observations([row])
        # Subtraction avoids overflowing at datetime.max while preserving the
        # exact 26-hour completion boundary.
        if now - stamp < timedelta(hours=26):
            unfinished += 1
            continue
        prior = observations.get(row["date"])
        if prior is not None:
            if prior != row:
                raise CollectionError("conflicting-candles-in-response")
            duplicates += 1
        observations[row["date"]] = row
    rows = sorted(observations.values(), key=lambda row: row["date"])
    validate_observations(rows)
    potential = []
    for left, right in zip(rows, rows[1:]):
        cursor, end = date.fromisoformat(left["date"]) + timedelta(days=1), date.fromisoformat(right["date"])
        while cursor < end:
            if cursor.weekday() < 5:
                potential.append(cursor.isoformat())
            cursor += timedelta(days=1)
    return {"observations": rows, "duplicates": duplicates, "unfinished": unfinished,
            "potentialMissingWeekdays": potential, "missingMeaning": "weekday-grid-only-not-verified-exchange-sessions"}


def default_retention() -> dict:
    return {"status": "blocked", "researchAllowed": False, "retentionAllowed": False,
            "evidence": TERMS_URL, "reason": "etoro-part-v-1.7-model-use-requires-written-exception",
            "deleteOnProviderRequestHours": 24, "automaticTerminationDetection": False}


def check_retention(policy: dict, now: str, *, research=True) -> None:
    if not isinstance(policy, dict) or policy.get("status") != "approved" or policy.get("retentionAllowed") is not True:
        raise CollectionError("retention-not-approved")
    if policy.get("source") != "etoro":
        raise CollectionError("retention-source-mismatch")
    allowed = {"source", "status", "retentionAllowed", "researchAllowed", "evidence", "expiresAt", "writtenModelUseException", "providerDeletionRequested", "deleteOnProviderRequestHours", "automaticTerminationDetection"}
    if set(policy) - allowed or not _bounded_text(policy.get("evidence"), 1000):
        raise CollectionError("invalid-retention-policy")
    if "providerDeletionRequested" in policy and type(policy["providerDeletionRequested"]) is not bool:
        raise CollectionError("invalid-retention-policy")
    if "deleteOnProviderRequestHours" in policy and (type(policy["deleteOnProviderRequestHours"]) is not int or policy["deleteOnProviderRequestHours"] != 24):
        raise CollectionError("invalid-retention-policy")
    if "automaticTerminationDetection" in policy and policy["automaticTerminationDetection"] is not False:
        raise CollectionError("invalid-retention-policy")
    if research and policy.get("researchAllowed") is not True:
        raise CollectionError("research-rights-not-approved")
    if not isinstance(policy.get("evidence"), str) or not policy["evidence"] or policy.get("providerDeletionRequested") is True:
        raise CollectionError("retention-revoked-or-unsupported")
    if policy.get("expiresAt") is None or _utc(policy["expiresAt"]) <= _utc(now):
        raise CollectionError("retention-expired")
    if policy.get("source") == "etoro" and policy.get("writtenModelUseException") is not True:
        raise CollectionError("etoro-model-use-exception-required")


def _bounded_text(value, maximum=256):
    return isinstance(value, str) and 0 < len(value) <= maximum and value == value.strip() and all(ord(c) >= 32 and ord(c) != 127 for c in value)


def validate_interpretation(interpretation: dict, symbol: str) -> None:
    """Exact reviewed feed meaning; no arbitrary metadata/account payloads."""
    expected = {"source", "currency", "currencyVerified", "priceBasis", "sessionConvention", "currencyEvidence", "interpretationEvidence", "instrumentMapping"}
    if not isinstance(interpretation, dict) or set(interpretation) != expected or symbol not in SYMBOLS:
        raise CollectionError("invalid-interpretation-fields")
    if interpretation["source"] != "etoro":
        raise CollectionError("source-or-symbol-mismatch")
    ticker, currency, names = SYMBOLS[symbol]
    if interpretation["currency"] != currency or interpretation["currencyVerified"] is not True:
        raise CollectionError("source-currency-unverified")
    for key in ("priceBasis", "sessionConvention", "currencyEvidence", "interpretationEvidence"):
        if not _bounded_text(interpretation[key], 1000):
            raise CollectionError("invalid-interpretation-value")
    mapping = interpretation["instrumentMapping"]
    mapping_keys = {"symbol", "providerSymbol", "instrumentId", "displayName", "instrumentType", "exchange", "currency", "currencyVerification", "identityStatus"}
    if not isinstance(mapping, dict) or set(mapping) != mapping_keys:
        raise CollectionError("invalid-instrument-mapping-fields")
    if mapping["symbol"] != symbol or mapping["providerSymbol"] != ticker or mapping["currency"] != currency or type(mapping["instrumentId"]) is not int or not 0 < mapping["instrumentId"] < 2**31:
        raise CollectionError("instrument-mapping-mismatch")
    if any(not _bounded_text(mapping[key]) for key in mapping_keys - {"instrumentId"}):
        raise CollectionError("invalid-instrument-mapping-value")
    if not all(term in mapping["displayName"].lower() for term in names) or mapping["instrumentType"].lower() not in ("etf", "etfs", "exchange traded fund"):
        raise CollectionError("instrument-name-or-type-mismatch")
    exchanges = {"SPY": {"NYSE", "NYSE ARCA", "NYSEARCA", "NEW YORK STOCK EXCHANGE"}, "QQQ": {"NASDAQ", "NASDAQ STOCK MARKET"}, "VAS": {"ASX", "AUSTRALIAN SECURITIES EXCHANGE"}}
    if mapping["exchange"].upper() not in exchanges[symbol]:
        raise CollectionError("instrument-exchange-mismatch")
    if mapping["currencyVerification"] != "expected-listing-currency-not-returned-by-api" or mapping["identityStatus"] != "symbol-name-etf-exchange-matched-currency-pending":
        raise CollectionError("instrument-verification-state-mismatch")


def preflight_collection(retention: dict, interpretations: dict, symbols, now: str) -> None:
    """Must run before credential construction as well as before any GET."""
    check_retention(retention, now)
    check_retention(retention, datetime.now(timezone.utc).isoformat())
    if not isinstance(symbols, (list, tuple)) or not symbols or len(symbols) > 3 or any(not isinstance(symbol, str) or symbol not in SYMBOLS for symbol in symbols) or len(set(symbols)) != len(symbols):
        raise CollectionError("invalid-collection-symbols")
    if not isinstance(interpretations, dict) or set(interpretations) != set(symbols):
        raise CollectionError("invalid-interpretations-symbols")
    for symbol in symbols:
        validate_interpretation(interpretations[symbol], symbol)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CollectionError("redirect-blocked")


def _forbidden_code(error: urllib.error.HTTPError) -> str:
    """Classify only one documented, bounded 403 envelope without retaining it."""
    try:
        body = error.read(HTTP_ERROR_BODY_CAP + 1)
    except Exception:
        return "http-forbidden-cause-unresolved"
    if not isinstance(body, bytes) or len(body) > HTTP_ERROR_BODY_CAP:
        return "http-forbidden-cause-unresolved"
    try:
        envelope = _json(body)
    except CollectionError:
        return "http-forbidden-cause-unresolved"
    if (isinstance(envelope, dict) and set(envelope) == {"title", "detail"}
            and envelope.get("title") == _CLOUDFLARE_BROWSER_SIGNATURE_TITLE
            and envelope.get("detail") == _CLOUDFLARE_BROWSER_SIGNATURE_DETAIL):
        return "cloudflare-browser-signature-block"
    return "http-forbidden-cause-unresolved"


def _close_http_error(error: urllib.error.HTTPError) -> None:
    try:
        error.close()
    except Exception:
        # The transport failure is already represented by a controlled code.
        pass


class EtoroReader:
    """GET-only exact endpoint allowlist; credentials never exposed in repr/output."""
    def __init__(self, profile: Path, *, ca_file: str | None = None):
        text = _private_read(profile, 32768).decode("utf-8")
        values = {}
        canonical = {"ETORO_API_KEY", "ETORO_USER_KEY"}
        legacy = {"ETORO_AGENT_PUBLIC_KEY", "ETORO_AGENT_PRIVAT_KEY"}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, sep, value = line.partition("=")
            key = key.removeprefix("export ").strip()
            if key not in canonical | legacy:
                continue
            if key in values or not sep:
                raise CollectionError("credential-profile-invalid")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if not value or any(c in value for c in "\r\n\x00"):
                raise CollectionError("credential-profile-invalid")
            values[key] = value
        if canonical.intersection(values) and legacy.intersection(values):
            raise CollectionError("credential-profile-ambiguous-fields")
        if set(values) == legacy:
            values = {"ETORO_API_KEY": values["ETORO_AGENT_PUBLIC_KEY"],
                      "ETORO_USER_KEY": values["ETORO_AGENT_PRIVAT_KEY"]}
        elif set(values) != canonical:
            raise CollectionError("credential-profile-missing-fields")
        self._credentials = values
        context = ssl.create_default_context(cafile=ca_file)
        self._opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context), _NoRedirect(), urllib.request.ProxyHandler({}))
        self._last = 0.0
        self._stopped = False
        self.request_count = 0

    def get(self, path: str, query: dict | None = None) -> dict:
        if self._stopped:
            raise CollectionError("collection-stopped")
        if path != "/market-data/search" and not re.fullmatch(r"/market-data/instruments/[1-9][0-9]*/history/candles/desc/OneDay/(?:[1-9][0-9]{0,2}|1000)", path):
            raise CollectionError("endpoint-not-allowlisted")
        if query and (path != "/market-data/search" or set(query) != {"fields", "internalSymbolFull", "pageSize", "pageNumber"}):
            raise CollectionError("query-not-allowlisted")
        if self.request_count >= 12:
            self._stopped = True
            raise CollectionError("run-request-budget-exhausted")
        delay = 1.1 - (time.monotonic() - self._last)
        if delay > 0:
            time.sleep(delay)
        url = "https://public-api.etoro.com/api/v1" + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {"x-api-key": self._credentials["ETORO_API_KEY"], "x-user-key": self._credentials["ETORO_USER_KEY"], "x-request-id": str(uuid.uuid4()), "Accept": "application/json"}
        self._last = time.monotonic()
        self.request_count += 1
        try:
            with self._opener.open(urllib.request.Request(url, headers=headers, method="GET"), timeout=20) as response:
                data = response.read(4 * 1024 * 1024 + 1)
                if response.headers.get("RateLimit-Remaining") == "0":
                    self._stopped = True
                if len(data) > 4 * 1024 * 1024:
                    raise CollectionError("response-too-large")
                result = _json(data)
                if not isinstance(result, dict):
                    raise CollectionError("invalid-response-shape")
                return result
        except urllib.error.HTTPError as exc:
            status = exc.code
            try:
                code = (
                    _forbidden_code(exc) if status == 403 else
                    {401: "authentication-failed", 404: "instrument-unavailable", 429: "rate-limit-stop"}.get(status, "provider-http-failure")
                )
            finally:
                _close_http_error(exc)
            if status in (401, 403, 429):
                self._stopped = True
            raise CollectionError(code) from None
        except (urllib.error.URLError, OSError, TimeoutError):
            raise CollectionError("provider-transport-failure") from None


def resolve_instrument(reader, symbol: str) -> dict:
    if symbol not in SYMBOLS:
        raise CollectionError("symbol-not-allowlisted")
    ticker, currency, names = SYMBOLS[symbol]
    payload = reader.get("/market-data/search", {"fields": "instrumentId,internalSymbolFull,displayname,instrumentType,internalExchangeName", "internalSymbolFull": ticker, "pageSize": 10, "pageNumber": 1})
    items = payload.get("items")
    if not isinstance(items, list):
        raise CollectionError("invalid-search-response")
    matches = [item for item in items if isinstance(item, dict) and item.get("internalSymbolFull") == ticker]
    if len(matches) != 1:
        raise CollectionError("instrument-not-found" if not matches else "ambiguous-instrument")
    item = matches[0]
    instrument_id = item.get("instrumentId")
    name = item.get("displayname", "")
    kind = item.get("instrumentType", "")
    exchange = item.get("internalExchangeName", "")
    if not _instrument_id(instrument_id) or not isinstance(name, str) or not all(n in name.lower() for n in names):
        raise CollectionError("instrument-identity-unverified")
    if not isinstance(kind, str) or kind.lower() not in ("etf", "etfs", "exchange traded fund") or not isinstance(exchange, str) or not exchange:
        raise CollectionError("instrument-type-or-exchange-unverified")
    return {"symbol": symbol, "providerSymbol": ticker, "instrumentId": instrument_id,
            "displayName": name, "instrumentType": kind, "exchange": exchange,
            "currency": currency, "currencyVerification": "expected-listing-currency-not-returned-by-api",
            "identityStatus": "symbol-name-etf-exchange-matched-currency-pending"}


@contextmanager
def _store_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    s = root.lstat()
    if not stat.S_ISDIR(s.st_mode) or s.st_uid != os.geteuid() or stat.S_IMODE(s.st_mode) & 0o077:
        raise CollectionError("store-directory-permissions")
    fd = os.open(root / ".collector.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1 or s.st_uid != os.geteuid() or stat.S_IMODE(s.st_mode) & 0o077:
            raise CollectionError("store-lock-permissions")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _exclusive(path: Path, value: dict) -> None:
    data = _canonical(value)
    if path.exists():
        if _private_read(path, 32 * 1024 * 1024) != data:
            raise CollectionError("immutable-record-conflict")
        return
    # Atomic publication avoids an interrupted partial canonical record.
    temporary = path.with_name(".pending-" + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # All callers hold the exclusive store lock and checked absence above.
        # Rename publishes one link atomically, so a crash cannot strand nlink=2.
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def persist_version(root: Path, *, symbol: str, observations: list[dict], interpretation: dict,
                    retention: dict, retrieved_at: str) -> dict:
    check_retention(retention, retrieved_at)
    check_retention(retention, datetime.now(timezone.utc).isoformat())
    validate_interpretation(interpretation, symbol)
    rows = list(validate_observations(observations))
    retrieved = _utc(retrieved_at)
    if any(retrieved - _utc(row["timestamp"]) < timedelta(hours=26) for row in rows):
        raise CollectionError("unfinished-observation")
    if not rows:
        raise CollectionError("no-completed-observations")
    with _store_lock(root):
        prior = None
        records = sorted(root.glob(symbol + "-retrieval-*.json"))
        if records:
            record = _json(_private_read(records[-1], 2 * 1024 * 1024))
            if isinstance(record, dict) and record.get("schemaVersion") == "market-retrieval.v1":
                raise CollectionError("legacy-retrieval-migration-required")
            expected = {"schemaVersion", "symbol", "retrievedAt", "version", "added", "revisedDates", "missingPreviouslyObservedDates", "unresolvedMissingDates", "restoredDates", "restorationEvidence", "unchanged", "originalObservationsPreserved", "source", "inputDigest"}
            if not isinstance(record, dict) or set(record) != expected or record.get("schemaVersion") != "market-retrieval.v2" or record.get("symbol") != symbol or record.get("source") != "etoro" or record.get("originalObservationsPreserved") is not True:
                raise CollectionError("invalid-retrieval-record")
            if any(type(record[k]) is not int or record[k] < 0 for k in ("added", "unchanged")) or not isinstance(record["inputDigest"], str) or not re.fullmatch(r"[a-f0-9]{64}", record["inputDigest"]):
                raise CollectionError("invalid-retrieval-record")
            for key in ("revisedDates", "missingPreviouslyObservedDates", "unresolvedMissingDates", "restoredDates"):
                values = record[key]
                if not isinstance(values, list) or any(not isinstance(d, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", d) for d in values) or len(set(values)) != len(values):
                    raise CollectionError("invalid-retrieval-record")
                try:
                    if any(date.fromisoformat(day).isoformat() != day for day in values):
                        raise ValueError
                except ValueError:
                    raise CollectionError("invalid-retrieval-record") from None
            evidence = record["restorationEvidence"]
            if not isinstance(evidence, list) or len(evidence) != len(record["restoredDates"]):
                raise CollectionError("invalid-restoration-evidence")
            for day, restored in zip(record["restoredDates"], evidence):
                if not isinstance(restored, dict) or set(restored) != {"date", "observationTimestamp", "retrievedAt", "source"} or restored["date"] != day or restored["source"] != "etoro" or restored["retrievedAt"] != record["retrievedAt"] or _utc(restored["observationTimestamp"]).date().isoformat() != day:
                    raise CollectionError("invalid-restoration-evidence")
            name = record.get("version")
            if not isinstance(name, str) or not re.fullmatch(r"[a-f0-9]{64}", name):
                raise CollectionError("invalid-version-reference")
            raw = _private_read(root / (symbol + "-" + name + ".json"), 32 * 1024 * 1024)
            if hashlib.sha256(raw).hexdigest() != name:
                raise CollectionError("dataset-integrity-failed")
            prior = _json(raw)
            if not isinstance(prior, dict) or set(prior) != {"schemaVersion", "symbol", "source", "observations", "interpretation", "retention", "unresolvedMissingDates"} or prior.get("schemaVersion") != VERSION or prior.get("symbol") != symbol or prior.get("source") != "etoro":
                raise CollectionError("invalid-prior-version")
            validate_observations(prior["observations"])
            prior_dates = {row["date"] for row in prior["observations"]}
            if prior["unresolvedMissingDates"] != record["unresolvedMissingDates"] or any(day not in prior_dates for day in record["unresolvedMissingDates"]):
                raise CollectionError("invalid-availability-state")
            check_retention(prior["retention"], datetime.now(timezone.utc).isoformat())
            if prior.get("interpretation") != interpretation:
                raise CollectionError("source-interpretation-changed-use-separate-store")
            if _utc(record["retrievedAt"]) > _utc(retrieved_at):
                raise CollectionError("retrieval-clock-reversed")
        identity = _utc(retrieved_at).strftime("%Y%m%dT%H%M%S%fZ")
        replay_path = root / (symbol + "-retrieval-" + identity + ".json")
        if replay_path.exists():
            existing = _json(_private_read(replay_path, 2 * 1024 * 1024))
            if existing.get("inputDigest") != hashlib.sha256(_canonical({"observations": rows, "interpretation": interpretation, "retention": retention})).hexdigest():
                raise CollectionError("retrieval-identity-conflict")
            return existing
        old = {r["date"]: r for r in prior["observations"]} if prior else {}
        new = {r["date"]: r for r in rows}
        revisions = [d for d in new if d in old and new[d] != old[d]]
        missing = [d for d in old if rows[0]["date"] <= d <= rows[-1]["date"] and d not in new]
        previously_unresolved = set(prior["unresolvedMissingDates"]) if prior else set()
        restored = sorted(previously_unresolved & set(new))
        unresolved = sorted((previously_unresolved | set(missing)) - set(new))
        merged = {**old, **new}
        version = {"schemaVersion": VERSION, "symbol": symbol, "source": "etoro", "observations": sorted(merged.values(), key=lambda r: r["date"]), "interpretation": interpretation, "retention": retention, "unresolvedMissingDates": unresolved}
        digest = hashlib.sha256(_canonical(version)).hexdigest()
        _exclusive(root / (symbol + "-" + digest + ".json"), version)
        report = {"schemaVersion": "market-retrieval.v2", "symbol": symbol, "retrievedAt": retrieved_at,
                  "version": digest, "added": len(set(new) - set(old)), "revisedDates": revisions,
                  "missingPreviouslyObservedDates": missing, "unresolvedMissingDates": unresolved,
                  "restoredDates": restored, "restorationEvidence": [{"date": day, "observationTimestamp": new[day]["timestamp"], "retrievedAt": retrieved_at, "source": "etoro"} for day in restored], "unchanged": sum(d in old and new[d] == old[d] for d in new),
                  "originalObservationsPreserved": True, "source": "etoro",
                  "inputDigest": hashlib.sha256(_canonical({"observations": rows, "interpretation": interpretation, "retention": retention})).hexdigest()}
        identity = _utc(retrieved_at).strftime("%Y%m%dT%H%M%S%fZ")
        _exclusive(root / (symbol + "-retrieval-" + identity + ".json"), report)
        return report


def collect(reader, *, symbols=("SPY", "QQQ", "VAS"), retrieved_at: str,
            output_root: Path | None = None, retention: dict | None = None,
            interpretations: dict | None = None, probe_only=False) -> dict:
    _utc(retrieved_at)
    reports = []
    if not probe_only:
        try:
            preflight_collection(retention if retention is not None else default_retention(), interpretations, symbols, retrieved_at)
            if output_root is None:
                raise CollectionError("output-root-required")
        except CollectionError as exc:
            return {"schemaVersion": "feed-collection-status.v1", "retrievedAt": retrieved_at, "source": "etoro",
                    "instruments": [{"symbol": symbol, "status": "unavailable", "reason": str(exc)} for symbol in symbols],
                    "researchRights": "blocked", "accountData": "absent", "execution": "blocked"}
    stopped = False
    for symbol in symbols:
        if symbol not in SYMBOLS:
            raise CollectionError("symbol-not-allowlisted")
        if stopped:
            reports.append({"symbol": symbol, "status": "unavailable", "reason": "collection-stopped"})
            continue
        try:
            mapping = resolve_instrument(reader, symbol)
            if probe_only:
                reports.append({"symbol": symbol, "status": "identity-only", "mapping": mapping, "pricesRetained": False})
                continue
            policy = retention if retention is not None else default_retention()
            check_retention(policy, retrieved_at)
            interpretation = (interpretations or {}).get(symbol, {})
            if interpretation["instrumentMapping"] != mapping:
                raise CollectionError("resolved-instrument-differs-from-reviewed-mapping")
            payload = reader.get(f"/market-data/instruments/{mapping['instrumentId']}/history/candles/desc/OneDay/1000")
            completed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            parsed = parse_candles(payload, mapping["instrumentId"], completed_at)
            interpretation = {**interpretation, "instrumentMapping": mapping}
            report = persist_version(output_root, symbol=symbol, observations=parsed["observations"], interpretation=interpretation, retention=policy, retrieved_at=completed_at)
            reports.append({"symbol": symbol, "status": "collected", "mapping": mapping, "retrieval": report,
                            **{k: v for k, v in parsed.items() if k != "observations"},
                            "coverageLimit": "latest-up-to-1000-daily-candles-no-documented-date-cursor"})
        except CollectionError as exc:
            reason = str(exc)
            reports.append({"symbol": symbol, "status": "unavailable", "reason": reason})
            stopped = reason in ("authentication-failed", "cloudflare-browser-signature-block", "http-forbidden-cause-unresolved", "rate-limit-stop", "collection-stopped")
    return {"schemaVersion": "feed-collection-status.v1", "retrievedAt": retrieved_at, "source": "etoro", "instruments": reports,
            "researchRights": "approved-by-supplied-evidence" if retention and retention.get("writtenModelUseException") is True and retention.get("researchAllowed") is True else "requires-reviewed-written-exception", "accountData": "absent", "execution": "blocked"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--ca-file")
    parser.add_argument("--symbol", action="append", choices=tuple(SYMBOLS), help="Repeat for an eligible subset; default SPY, QQQ, VAS")
    parser.add_argument("--probe-only", action="store_true")
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--interpretations", type=Path)
    args = parser.parse_args(argv)
    symbols = tuple(args.symbol) if args.symbol is not None else tuple(SYMBOLS)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        if len(set(symbols)) != len(symbols):
            raise CollectionError("duplicate-collection-symbols")
        if not args.probe_only and (args.policy is None or args.interpretations is None or args.output_root is None):
            raise CollectionError("approved-policy-and-interpretations-required")
        policy = _json(_private_read(args.policy, 65536)) if args.policy else None
        meanings = _json(_private_read(args.interpretations, 65536)) if args.interpretations else None
        if not args.probe_only:
            preflight_collection(policy, meanings, symbols, now)
        reader = EtoroReader(args.profile, ca_file=args.ca_file)
        result = collect(reader, symbols=symbols, retrieved_at=now, output_root=args.output_root, retention=policy, interpretations=meanings, probe_only=args.probe_only)
    except (CollectionError, UnicodeError) as exc:
        result = {"schemaVersion": "feed-collection-status.v1", "status": "unavailable", "reason": str(exc) if isinstance(exc, CollectionError) else "credential-profile-invalid", "accountData": "absent", "execution": "blocked"}
    print(json.dumps(result, allow_nan=False, sort_keys=True))
    return int(result.get("status") == "unavailable" or any(r["status"] == "unavailable" for r in result.get("instruments", [])))


if __name__ == "__main__":
    raise SystemExit(main())
