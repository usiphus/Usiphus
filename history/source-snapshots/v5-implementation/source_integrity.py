#!/usr/bin/env python3
"""Conservative article-status checks for clinical case-report study material.

Europe PMC status updates are a signal, not a guarantee that an article is
clinically sound or free of later notices. Failures are represented as
'unknown'; they are never silently converted to 'current'.
"""
from __future__ import annotations

import datetime as dt
import email.utils
import hashlib
import json
import os
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

STATUS_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/status-update-search?format=json"
USER_AGENT = "ClinicalCaseFlashcards/8.1 (article status screening; educational use)"
VALID_STATUSES = {"current", "corrected", "retracted", "expression_of_concern", "unknown", "conflict"}


class StatusCheckError(RuntimeError):
    """Status provider could not be queried or its response was invalid."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def normalize_doi(value: str) -> str:
    value = (value or "").strip()
    value = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value, flags=re.I)
    return value.lower()


def identifier_inputs(*, pmcid: str = "", pmid: str = "", doi: str = "") -> list[dict[str, str]]:
    """Build valid Europe PMC status identifiers, preserving source-specific IDs."""
    items: list[dict[str, str]] = []
    pmcid = (pmcid or "").strip().upper()
    pmid = (pmid or "").strip()
    doi = normalize_doi(doi)
    if re.fullmatch(r"PMC\d+", pmcid):
        items.append({"src": "PMC", "extId": pmcid})
    if re.fullmatch(r"\d+", pmid):
        items.append({"src": "MED", "extId": pmid})
    if doi and re.fullmatch(r"10\.\d{4,9}/\S+", doi):
        items.append({"src": "DOI", "extId": doi})
    return items


def _retry_delay(value: str | None, attempt: int) -> float:
    if value:
        try:
            return min(max(float(value), 0.0), 60.0)
        except (TypeError, ValueError):
            try:
                when = email.utils.parsedate_to_datetime(value)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=dt.timezone.utc)
                return min(max((when - dt.datetime.now(dt.timezone.utc)).total_seconds(), 0.0), 60.0)
            except (TypeError, ValueError, OverflowError):
                pass
    return min((2 ** attempt) + random.uniform(0, 0.5), 30.0)


def post_status_request(ids: list[dict[str, str]], *, timeout: float = 20.0,
                        retries: int = 3, max_bytes: int = 2_000_000,
                        opener=None) -> dict[str, Any]:
    if not ids:
        raise ValueError("At least one valid article identifier is required")
    payload = json.dumps({"ids": ids}, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        STATUS_URL, data=payload, method="POST",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                 "Content-Type": "application/json"},
    )
    open_url = opener or urllib.request.urlopen
    last_error = "unknown status service error"
    for attempt in range(max(1, retries)):
        try:
            with open_url(request, timeout=timeout) as response:
                raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise StatusCheckError(f"Status response exceeds {max_bytes} bytes")
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise StatusCheckError("Europe PMC returned malformed status JSON") from exc
            if not isinstance(data, dict):
                raise StatusCheckError("Europe PMC returned an unexpected status response")
            return data
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}"
            if exc.code not in {408, 425, 429, 500, 502, 503, 504}:
                raise StatusCheckError(last_error) from exc
            delay = _retry_delay(exc.headers.get("Retry-After") if exc.headers else None, attempt)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            delay = _retry_delay(None, attempt)
        if attempt < max(1, retries) - 1:
            time.sleep(delay)
    raise StatusCheckError(f"Status check failed after {max(1, retries)} attempts ({last_error})")


def _walk_records(value: Any, inherited_ids: set[str] | None = None):
    inherited_ids = set(inherited_ids or ())
    if isinstance(value, dict):
        current_ids = inherited_ids | _record_ids(value)
        yield value, current_ids
        for child in value.values():
            yield from _walk_records(child, current_ids)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_records(child, inherited_ids)


def _record_ids(record: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    for key in ("extId", "externalId", "pmcid", "pmid", "doi", "articleId", "article_id", "publicationId"):
        val = record.get(key)
        if isinstance(val, (str, int)) and str(val).strip():
            found.add(str(val).strip().lower())
    generic_id = record.get("id")
    if isinstance(generic_id, (str, int)) and str(generic_id).strip():
        value = str(generic_id).strip()
        source = str(record.get("src") or record.get("source") or record.get("idType") or "").upper()
        if re.fullmatch(r"PMC\d+", value, re.I) or re.fullmatch(r"10\.\d{4,9}/\S+", value):
            found.add(value.lower())
        elif source in {"MED", "PMID"} and value.isdigit():
            found.add(value.lower())
    return found


def _update_label(record: dict[str, Any]) -> str:
    parts = []
    for key in ("updateType", "update_type", "updateNature", "update_nature", "nature", "type", "status", "publicationStatus"):
        val = record.get(key)
        if isinstance(val, str):
            parts.append(val.lower().replace("_", " "))
    return " ".join(parts)


def classify_status_response(data: dict[str, Any], ids: list[dict[str, str]]) -> dict[str, Any]:
    """Classify explicit update notices; absence of a notice remains unknown."""
    requested = {item["extId"].lower() for item in ids}
    matched_records = []
    for record, inherited_ids in _walk_records(data):
        if inherited_ids & requested:
            matched_records.append(record)
    labels = [_update_label(record) for record in matched_records]
    combined = " ".join(labels)
    detected = set()
    if re.search(r"retract|withdrawn|withdrawal", combined):
        detected.add("retracted")
    if re.search(r"expression.{0,12}concern|concern.{0,12}expression", combined):
        detected.add("expression_of_concern")
    if re.search(r"correct(?:ion|ed|ed article)|errat(?:um|a)", combined):
        detected.add("corrected")
    if len(detected) > 1:
        status = "conflict"
    elif detected:
        status = next(iter(detected))
    else:
        # Status API is an updates feed; no matching explicit record does not
        # prove the publication is current.
        status = "unknown"
    return {
        "schema_version": 1,
        "status": status,
        "checked_at": utc_now(),
        "provider": "Europe PMC status-update-search",
        "endpoint": STATUS_URL,
        "requested_identifiers": ids,
        "matched_record_count": len(matched_records),
        "raw_update_labels": sorted({label for label in labels if label}),
        "response_sha256": hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest(),
        "warning": "A clean/no-update response is not proof of clinical validity or absence of all notices." if status == "unknown" else "Manual source review remains required.",
    }


def fetch_crossref_updates(doi: str, *, timeout: float = 15.0, max_bytes: int = 1_000_000,
                          retries: int = 3, opener=None) -> dict[str, Any]:
    """Read Crossref update-to relations; an empty relation list is not proof of current status."""
    doi = normalize_doi(doi)
    if not re.fullmatch(r"10\.\d{4,9}/\S+", doi):
        return {"provider": "Crossref", "status": "unknown", "reason": "No valid DOI supplied"}
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    open_url = opener or urllib.request.urlopen
    last_error = "unknown Crossref error"
    for attempt in range(max(1, retries)):
        try:
            with open_url(request, timeout=timeout) as response:
                raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise StatusCheckError(f"Crossref response exceeds {max_bytes} bytes")
            payload = json.loads(raw.decode("utf-8"))
            message = payload.get("message", {}) if isinstance(payload, dict) else {}
            updates = message.get("update-to", []) if isinstance(message, dict) else []
            if not isinstance(updates, list):
                raise StatusCheckError("Crossref update-to field has an unexpected shape")
            labels = []
            for item in updates:
                if isinstance(item, dict):
                    labels.append(str(item.get("type") or item.get("label") or "").lower().replace("_", " "))
            detected = set()
            joined = " ".join(labels)
            if re.search(r"retract|withdraw", joined):
                detected.add("retracted")
            if re.search(r"expression.{0,12}concern", joined):
                detected.add("expression_of_concern")
            if re.search(r"correct|errat", joined):
                detected.add("corrected")
            status = "conflict" if len(detected) > 1 else next(iter(detected), "unknown")
            return {"provider": "Crossref", "status": status, "endpoint": url,
                    "raw_update_types": labels, "checked_at": utc_now(),
                    "warning": "No Crossref update relation is not proof that an article is current." if status == "unknown" else "Manual notice review remains required."}
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}"
            if exc.code not in {408, 425, 429, 500, 502, 503, 504}:
                break
            delay = _retry_delay(exc.headers.get("Retry-After") if exc.headers else None, attempt)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            delay = _retry_delay(None, attempt)
        except (UnicodeDecodeError, json.JSONDecodeError, StatusCheckError) as exc:
            last_error = str(exc)
            break
        if attempt < max(1, retries) - 1:
            time.sleep(delay)
    return {"provider": "Crossref", "status": "unknown", "endpoint": url,
            "checked_at": utc_now(), "error": last_error}


def combine_provider_statuses(europe_pmc: dict[str, Any], crossref: dict[str, Any] | None = None) -> dict[str, Any]:
    providers = [europe_pmc]
    if crossref is not None:
        providers.append(crossref)
    explicit = {str(p.get("status")) for p in providers
                if p.get("status") in {"corrected", "retracted", "expression_of_concern", "conflict"}}
    if "conflict" in explicit or len(explicit) > 1:
        overall = "conflict"
    elif explicit:
        overall = next(iter(explicit))
    else:
        overall = "unknown"
    return {"schema_version": 1, "status": overall, "checked_at": utc_now(),
            "providers": providers,
            "warning": "Provider disagreement or an unconfirmed status requires manual verification." if overall in {"unknown", "conflict"} else "Manual source review remains required."}


def check_article_status(*, pmcid: str = "", pmid: str = "", doi: str = "",
                         cache_path: Path | None = None, ttl_hours: float = 24.0,
                         force: bool = False, request_fn=post_status_request,
                         crossref_fn=fetch_crossref_updates) -> dict[str, Any]:
    ids = identifier_inputs(pmcid=pmcid, pmid=pmid, doi=doi)
    if not ids:
        return {"schema_version": 1, "status": "unknown", "checked_at": None,
                "provider": "Europe PMC status-update-search", "requested_identifiers": [], "providers": [],
                "warning": "No valid PMCID, PMID, or DOI was available for status screening."}
    cache: dict[str, Any] = {}
    if cache_path and cache_path.exists():
        try:
            loaded = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                cache = loaded
        except (OSError, json.JSONDecodeError):
            cache = {}
    cache_key = hashlib.sha256(json.dumps(ids, sort_keys=True).encode()).hexdigest()
    cached = cache.get(cache_key)
    if not force and isinstance(cached, dict):
        try:
            checked = dt.datetime.fromisoformat(str(cached.get("checked_at", "")).replace("Z", "+00:00"))
            age = (dt.datetime.now(dt.timezone.utc) - checked).total_seconds() / 3600
            if 0 <= age <= max(0.0, ttl_hours):
                return cached
        except (TypeError, ValueError):
            pass
    try:
        epmc_result = classify_status_response(request_fn(ids), ids)
    except (StatusCheckError, OSError, ValueError) as exc:
        epmc_result = {"schema_version": 1, "status": "unknown", "checked_at": utc_now(),
                       "provider": "Europe PMC status-update-search", "requested_identifiers": ids,
                       "error": str(exc), "warning": "Status check failed; unknown must not be treated as current."}
    crossref_result = crossref_fn(doi) if doi else None
    result = combine_provider_statuses(epmc_result, crossref_result)
    result["requested_identifiers"] = ids
    if cache_path:
        cache[cache_key] = result
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temp = cache_path.with_suffix(cache_path.suffix + ".tmp")
        temp.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temp, cache_path)
    return result
