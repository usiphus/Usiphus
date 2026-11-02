"""Evidence provenance and claim-level grounding gates for medical education drafts.

This module validates curated evidence packets. It deliberately does not scrape
article bodies or treat abstracts/search snippets as sufficient clinical proof.
"""
from __future__ import annotations

import re
from typing import Any

RISK_PATTERNS = {
    "drug_dose": re.compile(r"\b(?:mg|mcg|µg|g|ml|mL|units?)\s*/?\s*(?:kg|day|dose|hour|hr)?\b", re.I),
    "contraindication": re.compile(r"\bcontraindicat(?:ed|ion|ions)\b", re.I),
    "emergency_action": re.compile(r"\b(?:immediate|urgent|emergency|first[- ]line|resuscitat)\b", re.I),
    "threshold": re.compile(r"\b(?:cut[- ]?off|threshold|greater than|less than|\d+\s*(?:mmhg|mmol|cm|%) )\b", re.I),
    "red_flag": re.compile(r"\b(?:red flag|danger sign|life[- ]threatening|must not miss)\b", re.I),
}

REQUIRED_SOURCE_FIELDS = (
    "source_id", "canonical_url", "title", "source_type", "published_on",
    "guideline_version", "licence_or_reuse_status", "review_status",
)
REQUIRED_CLAIM_FIELDS = ("claim_id", "text", "source_id", "snippet_id", "locator")


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def validate_evidence_source(source: dict[str, Any]) -> list[str]:
    """Return validation errors; only explicitly reviewed sources are eligible."""
    errors = []
    for field in REQUIRED_SOURCE_FIELDS:
        if not _text(source.get(field)):
            errors.append(f"missing {field}")
    url = _text(source.get("canonical_url"))
    if url and not url.startswith(("https://doi.org/", "https://pubmed.ncbi.nlm.nih.gov/",
                                   "https://europepmc.org/", "https://")):
        errors.append("canonical_url must use HTTPS")
    if source.get("review_status") != "faculty_reviewed":
        errors.append("source is not faculty_reviewed")
    eligible_access = {
        "full_text_reviewed", "guideline_full_text_reviewed",
        "faculty_provided_full_text", "licensed_full_text_reviewed",
    }
    if source.get("access_status") not in eligible_access:
        errors.append("source full text has not been reviewed through an eligible access path")
    if source.get("licence_or_reuse_status") in {"", "not_verified", "rights_unknown"}:
        errors.append("reuse/access rights are not verified")
    return errors


def validate_evidence_packet(packet: dict[str, Any]) -> dict[str, Any]:
    """Validate source/snippet provenance and return an auditable summary."""
    sources = packet.get("sources")
    snippets = packet.get("snippets")
    if not isinstance(sources, list) or not isinstance(snippets, list):
        raise ValueError("Evidence packet requires sources and snippets arrays")
    source_ids = set()
    source_errors = {}
    for source in sources:
        if not isinstance(source, dict) or not _text(source.get("source_id")):
            raise ValueError("Every evidence source requires source_id")
        sid = _text(source["source_id"])
        if sid in source_ids:
            raise ValueError(f"Duplicate evidence source_id: {sid}")
        source_ids.add(sid)
        errors = validate_evidence_source(source)
        if errors:
            source_errors[sid] = errors
    snippet_ids = set()
    snippet_errors = {}
    for snippet in snippets:
        if not isinstance(snippet, dict):
            raise ValueError("Every evidence snippet must be an object")
        sid = _text(snippet.get("snippet_id"))
        if not sid or sid in snippet_ids:
            raise ValueError("Evidence snippet IDs must be present and unique")
        snippet_ids.add(sid)
        if snippet.get("source_id") not in source_ids:
            snippet_errors[sid] = ["unknown source_id"]
        elif not _text(snippet.get("text")) or not _text(snippet.get("locator")):
            snippet_errors[sid] = ["snippet requires text and a page/section/paragraph locator"]
    eligible = [s for s in sources if s["source_id"] not in source_errors]
    eligible_ids = {s["source_id"] for s in eligible}
    eligible_snippets = [s for s in snippets if s.get("source_id") in eligible_ids
                         and s.get("snippet_id") not in snippet_errors]
    return {
        "source_count": len(sources),
        "eligible_source_count": len(eligible),
        "snippet_count": len(snippets),
        "eligible_snippet_count": len(eligible_snippets),
        "source_errors": source_errors,
        "snippet_errors": snippet_errors,
        "eligible_snippets": eligible_snippets,
        "evidence_ready": bool(eligible_snippets),
        "notice": "Eligibility checks provenance metadata; they do not appraise study quality or prove a claim.",
    }


def detect_high_risk_claims(text: str) -> list[str]:
    """Conservative routing flags; not a complete clinical-risk classifier."""
    content = _text(text)
    return [label for label, pattern in RISK_PATTERNS.items() if pattern.search(content)]


def validate_card_evidence(card: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
    """Require every declared clinical claim to cite an eligible snippet."""
    report = validate_evidence_packet(packet)
    eligible = {x["snippet_id"]: x for x in report["eligible_snippets"]}
    claims = card.get("evidence_claims", [])
    if not isinstance(claims, list):
        raise ValueError("evidence_claims must be a list")
    errors = []
    seen = set()
    for index, claim in enumerate(claims, 1):
        if not isinstance(claim, dict) or any(not _text(claim.get(k)) for k in REQUIRED_CLAIM_FIELDS):
            errors.append(f"claim {index} requires claim_id, text, source_id, snippet_id and locator")
            continue
        if claim["claim_id"] in seen:
            errors.append(f"duplicate claim_id: {claim['claim_id']}")
        seen.add(claim["claim_id"])
        snippet = eligible.get(claim["snippet_id"])
        if not snippet:
            errors.append(f"claim {claim['claim_id']} cites no eligible evidence snippet")
        elif (claim["source_id"] != snippet["source_id"]
              or claim["locator"] != snippet["locator"]):
            errors.append(f"claim {claim['claim_id']} source/locator does not match snippet provenance")
    combined = _text(card.get("prompt")) + " " + _text(card.get("answer"))
    risk_flags = detect_high_risk_claims(combined)
    if not claims:
        errors.append("each evidence-grounded card requires at least one claim-level evidence mapping")
    return {
        "valid": not errors,
        "errors": errors,
        "high_risk_flags": risk_flags,
        "claim_count": len(claims),
        "evidence_status": "evidence_mapped_unreviewed" if claims and not errors else "unreviewed",
        "faculty_review_required": True,
    }


def build_objective_evidence_map(
    topics: list[dict[str, Any]], packet: dict[str, Any]
) -> dict[str, Any]:
    """Map curated snippets to objectives using curator-supplied objective IDs."""
    report = validate_evidence_packet(packet)
    eligible = report["eligible_snippets"]
    mapping = {}
    for topic in topics:
        topic_id = _text(topic.get("id"))
        for index, objective in enumerate(topic.get("learning_objectives", []), 1):
            objective_id = f"{topic_id}-LO{index:02d}"
            matches = [s["snippet_id"] for s in eligible
                       if objective_id in s.get("matched_objective_ids", [])]
            mapping[objective_id] = {
                "topic_id": topic_id, "objective": objective,
                "snippet_ids": matches, "evidence_status": "candidate_evidence_found" if matches else "evidence_gap",
            }
    return {
        "objective_map": mapping,
        "objectives_total": len(mapping),
        "objectives_with_candidate_evidence": sum(bool(x["snippet_ids"]) for x in mapping.values()),
        "objectives_with_evidence_gaps": [k for k, v in mapping.items() if not v["snippet_ids"]],
        "clinical_accuracy_verified": False,
        "faculty_review_required": True,
    }
