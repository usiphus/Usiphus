"""Evidence provenance helpers for the Usiphus medical deck generator.

Only curated excerpts are treated as evidence context. Bibliographic links alone
are retained as references but never counted as claim support. Automated
source-ID checks are traceability checks, not semantic verification.
"""
from __future__ import annotations

from typing import Any

MAX_EXCERPT_CHARS = 6000


def build_evidence_bundle(
    source_refs: Any, *, require_evidence: bool = False
) -> dict[str, Any]:
    if source_refs is None:
        source_refs = []
    if not isinstance(source_refs, list):
        raise ValueError("reference_sources must be a list")
    sources = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(source_refs, 1):
        if isinstance(raw, str):
            source = {
                "id": f"reference-{index:02d}",
                "title": raw.strip(),
                "url": "",
                "source_type": "unspecified",
                "excerpt": "",
            }
        elif isinstance(raw, dict):
            source = {
                "id": str(raw.get("id") or f"source-{index:02d}").strip(),
                "title": str(raw.get("title") or raw.get("name") or "").strip(),
                "url": str(raw.get("url") or raw.get("doi_url") or "").strip(),
                "source_type": str(raw.get("source_type") or raw.get("type") or "unspecified").strip(),
                "excerpt": str(raw.get("excerpt") or raw.get("text") or raw.get("content") or "").strip(),
                "published_at": raw.get("published_at") or raw.get("publication_date"),
                "retrieved_at": raw.get("retrieved_at"),
                "organization": raw.get("organization") or raw.get("author"),
                "licence": raw.get("licence") or raw.get("license"),
            }
        else:
            raise ValueError(f"reference_sources item {index} must be a string or object")
        if not source["title"]:
            raise ValueError(f"reference_sources item {index} needs a title")
        if not source["id"] or source["id"] in seen_ids:
            raise ValueError("reference_sources IDs must be non-empty and unique")
        seen_ids.add(source["id"])
        source["excerpt"] = source["excerpt"][:MAX_EXCERPT_CHARS]
        source["usable_as_evidence"] = bool(source["excerpt"])
        sources.append(source)
    usable = [source for source in sources if source["usable_as_evidence"]]
    if require_evidence and not usable:
        raise ValueError(
            "Evidence-required generation needs at least one reference_sources "
            "entry with a non-empty excerpt/text/content field; URLs alone are not evidence."
        )
    return {
        "sources": sources,
        "usable_sources": usable,
        "source_ids": {source["id"] for source in usable},
        "evidence_available": bool(usable),
    }


def assess_card_evidence(card: dict[str, Any], bundle: dict[str, Any]) -> dict[str, Any]:
    """Validate provenance IDs and surface review flags; never assert semantic support."""
    if not bundle["evidence_available"]:
        return {
            "evidence_review_state": "unverified_no_source_excerpt",
            "claim_provenance": [],
            "evidence_flags": ["No source excerpt supplied; all clinical claims require independent review."],
            "potential_conflicts": [],
        }

    raw_claims = card.get("claims")
    flags: list[str] = []
    provenance = []
    allowed = bundle["source_ids"]
    if not isinstance(raw_claims, list) or not raw_claims:
        flags.append("No claim-level source mapping was returned.")
        raw_claims = []
    for index, raw in enumerate(raw_claims, 1):
        if not isinstance(raw, dict) or not str(raw.get("claim") or "").strip():
            flags.append(f"Claim {index} is malformed or has no text.")
            continue
        refs = raw.get("source_ids", [])
        if not isinstance(refs, list):
            refs = []
        refs = [str(ref) for ref in refs]
        valid_refs = [ref for ref in refs if ref in allowed]
        invalid_refs = [ref for ref in refs if ref not in allowed]
        if invalid_refs:
            flags.append(f"Claim {index} cites unknown or metadata-only source IDs: {', '.join(invalid_refs)}.")
        if not valid_refs:
            flags.append(f"Claim {index} has no usable source excerpt linked.")
        provenance.append({
            "claim": str(raw["claim"]).strip(),
            "source_ids": valid_refs,
            "invalid_source_ids": invalid_refs,
            "support_status": "source_linked_pending_human_verification" if valid_refs else "unsupported_or_unlinked",
        })

    raw_conflicts = card.get("potential_conflicts", card.get("conflicts", []))
    conflicts = []
    if isinstance(raw_conflicts, list):
        for conflict in raw_conflicts:
            if isinstance(conflict, dict):
                conflicts.append({
                    "description": str(conflict.get("description") or conflict.get("claim") or "Potential source disagreement").strip(),
                    "source_ids": [str(ref) for ref in conflict.get("source_ids", []) if str(ref) in allowed]
                    if isinstance(conflict.get("source_ids", []), list) else [],
                })
            elif isinstance(conflict, str) and conflict.strip():
                conflicts.append({"description": conflict.strip(), "source_ids": []})
    if conflicts:
        flags.append("Potential source conflict reported by the model; faculty adjudication required.")
    if flags:
        status = "needs_evidence_review"
    else:
        status = "source_linked_pending_human_verification"
    return {
        "evidence_review_state": status,
        "claim_provenance": provenance,
        "evidence_flags": flags,
        "potential_conflicts": conflicts,
    }


def evidence_prompt_sources(bundle: dict[str, Any]) -> list[dict[str, str]]:
    """Minimize model context to source identifiers, titles, and curated excerpts."""
    return [
        {"id": item["id"], "title": item["title"], "source_type": item["source_type"],
         "published_at": str(item.get("published_at") or ""),
         "excerpt": item["excerpt"]}
        for item in bundle["usable_sources"]
    ]
