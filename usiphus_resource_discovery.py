#!/usr/bin/env python3
"""Usiphus curriculum-aware medical resource discovery.

Uses public scholarly APIs (NCBI PubMed E-utilities, Europe PMC, Crossref)
and emits curated search links for sites without a documented public search
API. It stores metadata and URLs only; it does not download article bodies,
Anki decks, challenge content, or proprietary exam questions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

VERSION = "1.0.0"
USER_AGENT = "Usiphus-ResourceDiscovery/1.0 (educational metadata discovery)"
MAX_RESPONSE_BYTES = 4_000_000
DEFAULT_TIMEOUT = 20

WEB_SEARCH_TEMPLATES = {
    "anki_shared_decks": [
        ("AnkiWeb shared decks", "https://ankiweb.net/shared/decks?search={query}")
    ],
    "doctorde_challenges": [
        ("DoctorDE", "https://www.google.com/search?q=site%3Adoctorde.com+{query}")
    ],
    "public_exam_prep": [
        ("Geeky Medics", "https://www.google.com/search?q=site%3Ageekymedics.com+{query}"),
        ("TeachMeSeries", "https://www.google.com/search?q=site%3Ateachmeseries.com+{query}")
    ],
    "professional_guidelines": [
        ("Specialty society guidelines", "https://www.google.com/search?q={query}+specialty+guideline+consensus")
    ],
    "open_textbooks_resources": [
        ("Open educational resources", "https://www.google.com/search?q={query}+open+access+textbook+university+teaching")
    ],
    "doaj": [
        ("DOAJ", "https://doaj.org/search/articles/{query}")
    ],
}

SOURCE_ROLES = {
    "pubmed": "clinical_evidence",
    "pubmed_central": "clinical_evidence",
    "cureus_open_access": "clinical_evidence",
    "crossref": "clinical_evidence",
    "anki_shared_decks": "exam_practice_signal",
    "doctorde_challenges": "exam_practice_signal",
    "public_exam_prep": "exam_practice_signal",
    "professional_guidelines": "guideline_or_consensus",
    "open_textbooks_resources": "foundational_learning",
    "doaj": "clinical_evidence",
}


class DiscoveryError(ValueError):
    """Invalid curriculum or discovery configuration."""


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _tokens(value: str) -> set[str]:
    return {x for x in re.findall(r"[a-z0-9]+", value.casefold()) if len(x) > 2}


def _canonical_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    host = parsed.netloc.casefold()
    path = re.sub(r"/+", "/", parsed.path or "/").rstrip("/")
    return urllib.parse.urlunsplit((parsed.scheme, host, path, parsed.query, ""))


def _stable_id(source: str, url: str, title: str) -> str:
    raw = source + "|" + (url or title)
    return source.upper() + "-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _request_json(url: str, *, timeout: int = DEFAULT_TIMEOUT,
                  opener: Callable[..., Any] | None = None) -> dict[str, Any]:
    if not url.startswith("https://"):
        raise DiscoveryError("Only HTTPS provider endpoints are allowed")
    request = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    })
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise DiscoveryError(f"Provider request failed: {exc}") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise DiscoveryError("Provider response exceeded size limit")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DiscoveryError("Provider returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise DiscoveryError("Provider response must be a JSON object")
    return data


def _api_url(base: str, params: dict[str, Any]) -> str:
    return base + "?" + urllib.parse.urlencode(params)


def _query_for(topic: dict[str, Any]) -> str:
    # Search the canonical topic phrase for high recall. Learning objectives,
    # exam emphasis and coverage tags are applied during local matching rather
    # than AND-combined into provider syntax, which can suppress useful results.
    return _clean(topic.get("name") or topic.get("topic"))[:240]


def _record(source_id: str, title: str, url: str, *, publisher: str = "",
            authors: list[str] | None = None, published: str = "",
            doi: str = "", pmid: str = "", pmcid: str = "",
            abstract: str = "", licence: str = "",
            access_status: str = "metadata_only",
            article_type: str = "", provider: str = "") -> dict[str, Any]:
    canonical_url = _canonical_url(url)
    return {
        "resource_id": _stable_id(source_id, canonical_url, title),
        "source_id": source_id,
        "title": _clean(title),
        "canonical_url": canonical_url or url,
        "source_type": source_id,
        "publisher": _clean(publisher),
        "authors": authors or [],
        "published_on": _clean(published),
        "doi": _clean(doi),
        "pmid": _clean(pmid),
        "pmcid": _clean(pmcid),
        "abstract": _clean(abstract)[:1800],
        "licence_or_reuse_status": _clean(licence) or "not_verified",
        "access_status": access_status,
        "article_type": _clean(article_type),
        "discovery_provider": provider,
        "evidence_role": SOURCE_ROLES.get(source_id, "learning_resource"),
        "review_status": "unreviewed",
    }


def search_pubmed(query: str, limit: int, *, timeout: int = DEFAULT_TIMEOUT,
                  opener: Callable[..., Any] | None = None) -> list[dict[str, Any]]:
    search_url = _api_url("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi", {
        "db": "pubmed", "term": query, "retmode": "json", "retmax": limit, "sort": "relevance"
    })
    found = _request_json(search_url, timeout=timeout, opener=opener)
    ids = found.get("esearchresult", {}).get("idlist", [])
    if not ids:
        return []
    summary_url = _api_url("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi", {
        "db": "pubmed", "id": ",".join(ids), "retmode": "json"
    })
    summary = _request_json(summary_url, timeout=timeout, opener=opener).get("result", {})
    results = []
    for pmid in ids:
        item = summary.get(pmid, {})
        if not item:
            continue
        doi = ""
        for article_id in item.get("articleids", []):
            if article_id.get("idtype") == "doi":
                doi = article_id.get("value", "")
        authors = [x.get("name", "") for x in item.get("authors", []) if x.get("name")]
        results.append(_record(
            "pubmed", item.get("title", ""), f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            publisher=item.get("fulljournalname", ""), authors=authors,
            published=item.get("pubdate", ""), doi=doi, pmid=pmid,
            article_type=", ".join(
                (x.get("name", "") if isinstance(x, dict) else str(x))
                for x in item.get("pubtype", []) if x is not None
            ),
            provider="NCBI E-utilities",
        ))
    return results


def search_europe_pmc(query: str, limit: int, *, source_id: str = "pubmed_central",
                      timeout: int = DEFAULT_TIMEOUT,
                      opener: Callable[..., Any] | None = None) -> list[dict[str, Any]]:
    terms = f'OPEN_ACCESS:Y AND ({query})' if source_id == "pubmed_central" else f'JOURNAL:\"Cureus\" AND ({query})'
    url = _api_url("https://www.ebi.ac.uk/europepmc/webservices/rest/search", {
        "query": terms, "format": "json", "pageSize": limit, "resultType": "core"
    })
    data = _request_json(url, timeout=timeout, opener=opener)
    results = []
    for item in data.get("resultList", {}).get("result", []):
        pmcid = item.get("pmcid", "")
        pmid = item.get("pmid", "")
        doi = item.get("doi", "")
        link = (f"https://europepmc.org/articles/{pmcid}" if pmcid else
                f"https://doi.org/{doi}" if doi else
                f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "")
        licence = item.get("license", "")
        results.append(_record(
            source_id, item.get("title", ""), link,
            publisher=item.get("journalTitle", ""), authors=[
                x.get("fullName", "") for x in item.get("authorList", {}).get("author", [])
                if x.get("fullName")
            ][:8],
            published=item.get("firstPublicationDate", ""), doi=doi, pmid=pmid,
            pmcid=pmcid, abstract=item.get("abstractText", ""),
            licence=licence or "open_access_flag; article-level licence must be checked",
            access_status="open_access_flag_article_licence_check_required",
            article_type=item.get("pubType", ""), provider="Europe PMC REST",
        ))
    return results


def search_crossref(query: str, limit: int, *, timeout: int = DEFAULT_TIMEOUT,
                    opener: Callable[..., Any] | None = None) -> list[dict[str, Any]]:
    url = _api_url("https://api.crossref.org/works", {
        "query.bibliographic": query, "rows": limit, "select": "DOI,title,author,published,container-title,type,URL"
    })
    data = _request_json(url, timeout=timeout, opener=opener)
    results = []
    for item in data.get("message", {}).get("items", []):
        title = (item.get("title") or [""])[0]
        authors = [" ".join(x for x in (a.get("given"), a.get("family")) if x)
                   for a in item.get("author", [])[:8]]
        date_parts = item.get("published", {}).get("date-parts", [[]])[0]
        published = "-".join(str(x) for x in date_parts) if date_parts else ""
        doi = item.get("DOI", "")
        results.append(_record(
            "crossref", title, item.get("URL") or (f"https://doi.org/{doi}" if doi else ""),
            publisher=(item.get("container-title") or [""])[0], authors=authors,
            published=published, doi=doi, article_type=item.get("type", ""),
            provider="Crossref REST",
        ))
    return results


def _search_links(source_id: str, query: str) -> list[dict[str, Any]]:
    encoded = urllib.parse.quote_plus(query)
    records = []
    for label, template in WEB_SEARCH_TEMPLATES.get(source_id, []):
        url = template.format(query=encoded)
        records.append(_record(
            source_id, f"Search {label}: {query}", url,
            publisher=label, access_status="search_link_only",
            licence="not_applicable_to_search_link; destination rights vary",
            provider="curated public search link",
        ))
    return records


def score_resource(resource: dict[str, Any], topic: dict[str, Any]) -> tuple[float, list[str]]:
    topic_text = " ".join([
        _clean(topic.get("name") or topic.get("topic")),
        " ".join(topic.get("learning_objectives", [])),
        " ".join(topic.get("exam_emphasis", [])),
        " ".join(topic.get("coverage_tags", [])),
    ])
    wanted = _tokens(topic_text)
    candidate = _tokens(resource.get("title", "") + " " + resource.get("abstract", ""))
    overlap = len(wanted & candidate) / max(1, len(wanted))
    title_overlap = len(_tokens(resource.get("title", "")) & wanted) / max(1, len(wanted))
    score = min(1.0, overlap * 0.75 + title_overlap * 0.25)
    matched = sorted(wanted & candidate)
    return round(score, 4), matched


def match_resource(resource: dict[str, Any], topic: dict[str, Any]) -> dict[str, Any]:
    score, matched_terms = score_resource(resource, topic)
    result = dict(resource)
    result.update({
        "matched_topic_ids": [topic.get("id", "")],
        "matched_objective_ids": [],
        "matched_terms": matched_terms,
        "relevance_score": score,
        "retrieved_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    })
    title_abstract = _tokens(resource.get("title", "") + " " + resource.get("abstract", ""))
    for index, objective in enumerate(topic.get("learning_objectives", []), 1):
        obj_tokens = _tokens(objective)
        if obj_tokens and len(obj_tokens & title_abstract) / len(obj_tokens) >= 0.2:
            result["matched_objective_ids"].append(f"{topic.get('id', 'topic')}-LO{index:02d}")
    return result


def discover_topic(topic: dict[str, Any], *, limit: int = 5, timeout: int = DEFAULT_TIMEOUT,
                   delay: float = 0.34, providers: tuple[str, ...] = (
                       "pubmed", "pubmed_central", "cureus_open_access", "crossref"
                   ), opener: Callable[..., Any] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Discover at topic level and independently search each learning objective."""
    query = _query_for(topic)
    resources: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    def run_provider(provider: str, search_query: str, objective_id: str = "") -> None:
        try:
            if provider == "pubmed":
                found = search_pubmed(search_query, limit, timeout=timeout, opener=opener)
            elif provider == "pubmed_central":
                found = search_europe_pmc(search_query, limit, source_id="pubmed_central",
                                          timeout=timeout, opener=opener)
            elif provider == "cureus_open_access":
                found = search_europe_pmc(search_query, limit, source_id="cureus_open_access",
                                          timeout=timeout, opener=opener)
            elif provider == "crossref":
                found = search_crossref(search_query, limit, timeout=timeout, opener=opener)
            elif provider in WEB_SEARCH_TEMPLATES:
                found = _search_links(provider, search_query)
            else:
                errors.append({"provider": provider, "message": "Unsupported provider"})
                return
            for item in found:
                matched = match_resource(item, topic)
                matched["search_query_type"] = "objective" if objective_id else "topic"
                if objective_id:
                    title_abstract = _tokens(item.get("title", "") + " " + item.get("abstract", ""))
                    objective = next((x for i, x in enumerate(topic.get("learning_objectives", []), 1)
                                      if f"{topic.get('id', 'topic')}-LO{i:02d}" == objective_id), "")
                    objective_tokens = _tokens(objective)
                    if objective_tokens and len(objective_tokens & title_abstract) / len(objective_tokens) >= 0.15:
                        matched["matched_objective_ids"] = sorted(
                            set(matched.get("matched_objective_ids", [])) | {objective_id}
                        )
                resources.append(matched)
        except DiscoveryError as exc:
            errors.append({"provider": provider, "message": str(exc)[:300]})
        if delay:
            time.sleep(delay)

    # Broad topic search preserves recall across the configured source families.
    for provider in providers:
        run_provider(provider, query)

    # Evidence discovery is then repeated per objective through scholarly APIs.
    # Exam-practice/search-link sources remain discovery signals, not evidence.
    scholarly_providers = [x for x in ("pubmed", "pubmed_central", "cureus_open_access")
                           if x in providers]
    for index, objective in enumerate(topic.get("learning_objectives", []), 1):
        objective_query = _clean(objective)[:240]
        if not objective_query:
            continue
        objective_id = f"{topic.get('id', 'topic')}-LO{index:02d}"
        for provider in scholarly_providers:
            run_provider(provider, objective_query, objective_id)

    # Deduplicate by canonical URL while preserving all objective mappings.
    unique: dict[str, dict[str, Any]] = {}
    for item in resources:
        key = item.get("canonical_url") or item["resource_id"]
        if key not in unique:
            unique[key] = item
        else:
            current = unique[key]
            current["matched_topic_ids"] = sorted(set(
                current["matched_topic_ids"] + item["matched_topic_ids"]
            ))
            current["matched_objective_ids"] = sorted(set(
                current["matched_objective_ids"] + item["matched_objective_ids"]
            ))
            if item.get("relevance_score", 0) > current.get("relevance_score", 0):
                current["relevance_score"] = item["relevance_score"]
            if current.get("search_query_type") != "objective" and item.get("search_query_type") == "objective":
                current["search_query_type"] = "objective"
    return sorted(unique.values(), key=lambda x: (-x.get("relevance_score", 0), x["source_id"], x["title"])), errors


def discover_curriculum(curriculum: dict[str, Any], *, limit: int = 5,
                        max_topics: int | None = None, timeout: int = DEFAULT_TIMEOUT,
                        delay: float = 0.34, providers: tuple[str, ...] | None = None,
                        opener: Callable[..., Any] | None = None) -> dict[str, Any]:
    topics = curriculum.get("topics")
    if not isinstance(topics, list) or not topics:
        raise DiscoveryError("Curriculum must contain a non-empty topics array")
    specialty = _clean(curriculum.get("specialty"))
    if not specialty:
        raise DiscoveryError("Curriculum specialty is required")
    selected = topics[:max_topics] if max_topics else topics
    source_types = set()
    for topic in selected:
        source_types.update(topic.get("source_types_to_discover", []))
    if providers is None:
        defaults = ("pubmed", "pubmed_central", "cureus_open_access", "crossref")
        web_ids = tuple(x for x in ("anki_shared_decks", "doctorde_challenges",
                                    "public_exam_prep", "professional_guidelines",
                                    "open_textbooks_resources", "doaj") if x in source_types)
        providers = defaults + web_ids
    all_resources: list[dict[str, Any]] = []
    topic_reports = []
    errors = []
    for topic in selected:
        resources, topic_errors = discover_topic(
            topic, limit=limit, timeout=timeout, delay=delay,
            providers=providers, opener=opener
        )
        all_resources.extend(resources)
        errors.extend({"topic_id": topic.get("id", ""), **x} for x in topic_errors)
        source_counts: dict[str, int] = {}
        metadata_counts: dict[str, int] = {}
        objective_coverage = set()
        search_link_count = 0
        for resource in resources:
            source_id = resource["source_id"]
            source_counts[source_id] = source_counts.get(source_id, 0) + 1
            if resource.get("access_status") == "search_link_only":
                search_link_count += 1
            else:
                metadata_counts[source_id] = metadata_counts.get(source_id, 0) + 1
                objective_coverage.update(resource.get("matched_objective_ids", []))
        expected = topic.get("source_types_to_discover", [])
        found_types = set(metadata_counts)
        topic_reports.append({
            "topic_id": topic.get("id", ""),
            "topic_name": topic.get("name") or topic.get("topic", ""),
            "domain_id": topic.get("domain_id", ""),
            "resources_found": sum(metadata_counts.values()),
            "search_links_generated": search_link_count,
            "source_counts_including_search_links": source_counts,
            "metadata_result_counts_by_source": metadata_counts,
            "source_families_with_metadata_results": sorted(found_types),
            "source_families_without_metadata_results": sorted(set(expected) - found_types),
            "learning_objectives": len(topic.get("learning_objectives", [])),
            "learning_objectives_with_matched_resources": len(objective_coverage),
            "coverage_status": "resources_found" if metadata_counts else "gap",
        })
    # Global de-duplication while preserving all topic/objective mappings.
    merged: dict[str, dict[str, Any]] = {}
    for resource in all_resources:
        key = resource.get("canonical_url") or resource["resource_id"]
        if key not in merged:
            merged[key] = resource
        else:
            current = merged[key]
            current["matched_topic_ids"] = sorted(set(current["matched_topic_ids"] + resource["matched_topic_ids"]))
            current["matched_objective_ids"] = sorted(set(current["matched_objective_ids"] + resource["matched_objective_ids"]))
            if resource.get("relevance_score", 0) > current.get("relevance_score", 0):
                current["relevance_score"] = resource["relevance_score"]
    gaps = [x for x in topic_reports if x["coverage_status"] == "gap"]
    return {
        "schema_version": 1,
        "engine_version": VERSION,
        "specialty": specialty,
        "curriculum_version": curriculum.get("curriculum_version", ""),
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "discovery_scope": {"topics_requested": len(topics), "topics_processed": len(selected),
                            "providers": list(providers), "max_results_per_provider_topic": limit},
        "resources": sorted(merged.values(), key=lambda x: (-x.get("relevance_score", 0), x["source_id"], x["title"])),
        "topic_coverage": topic_reports,
        "coverage_summary": {
            "topics_processed": len(selected),
            "topics_with_resources": len(selected) - len(gaps),
            "topics_with_no_resources": len(gaps),
            "unique_resources": len(merged),
            "provider_errors": len(errors),
        },
        "provider_errors": errors,
        "governance": {
            "content_downloaded": False,
            "third_party_text_reproduced": False,
            "clinical_approval": "not_assessed",
            "rights_policy": "Metadata and links only; verify item-level licence before reuse.",
            "faculty_review_required": True,
            "ranking_warning": "Relevance ranking is not evidence quality or clinical authority.",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Discover medical learning resources for every curriculum topic.")
    parser.add_argument("--curriculum", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("build/resource-discovery"))
    parser.add_argument("--limit", type=int, default=5, help="Maximum API results per provider/topic (1-20).")
    parser.add_argument("--max-topics", type=int, help="Optional topic limit for a pilot run.")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--delay", type=float, default=0.34, help="Polite delay between provider requests.")
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 20 or args.timeout < 1 or args.delay < 0:
        parser.error("limit must be 1-20; timeout positive; delay non-negative")
    if args.max_topics is not None and args.max_topics < 1:
        parser.error("--max-topics must be positive")
    try:
        curriculum = json.loads(args.curriculum.read_text(encoding="utf-8"))
        report = discover_curriculum(curriculum, limit=args.limit, max_topics=args.max_topics,
                                     timeout=args.timeout, delay=args.delay)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "resources.json").write_text(
            json.dumps({"schema_version": 1, "resources": report["resources"]}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        (args.output_dir / "coverage_report.json").write_text(
            json.dumps({k: v for k, v in report.items() if k != "resources"}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
    except (OSError, json.JSONDecodeError, DiscoveryError) as exc:
        print(f"Discovery failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report["coverage_summary"], indent=2))
    print(f"Resources: {args.output_dir / 'resources.json'}")
    print(f"Coverage: {args.output_dir / 'coverage_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
