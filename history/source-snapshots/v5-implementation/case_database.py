#!/usr/bin/env python3
"""Versioned, rebuildable SQLite index for the local clinical case library."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

DB_SCHEMA_VERSION = 4
SUPPORTED_METADATA_SCHEMA_VERSION = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, description TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS articles (
    article_pk INTEGER PRIMARY KEY,
    pmcid TEXT UNIQUE,
    pmid TEXT,
    doi TEXT,
    title TEXT NOT NULL DEFAULT '', journal TEXT NOT NULL DEFAULT '', year TEXT NOT NULL DEFAULT '',
    specialty TEXT NOT NULL DEFAULT 'general-medicine', source_url TEXT NOT NULL DEFAULT '',
    license TEXT NOT NULL DEFAULT '', source_xml_sha256 TEXT NOT NULL,
    source_path TEXT NOT NULL, meta_json TEXT NOT NULL,
    article_status TEXT NOT NULL DEFAULT 'unknown', status_json TEXT NOT NULL DEFAULT '{}',
    parser_version TEXT NOT NULL DEFAULT '', imported_at TEXT NOT NULL,
    UNIQUE(source_path)
);
CREATE INDEX IF NOT EXISTS idx_articles_doi ON articles(doi);
CREATE INDEX IF NOT EXISTS idx_articles_pmid ON articles(pmid);
CREATE INDEX IF NOT EXISTS idx_articles_specialty_year ON articles(specialty, year);
CREATE TABLE IF NOT EXISTS sections (
    section_pk INTEGER PRIMARY KEY, article_pk INTEGER NOT NULL REFERENCES articles(article_pk) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL, title TEXT NOT NULL, category TEXT NOT NULL, text TEXT NOT NULL,
    text_sha256 TEXT NOT NULL, UNIQUE(article_pk, ordinal)
);
CREATE TABLE IF NOT EXISTS evidence_spans (
    evidence_id TEXT PRIMARY KEY, article_pk INTEGER NOT NULL REFERENCES articles(article_pk) ON DELETE CASCADE,
    section_pk INTEGER NOT NULL REFERENCES sections(section_pk) ON DELETE CASCADE,
    start_offset INTEGER NOT NULL, end_offset INTEGER NOT NULL, quote TEXT NOT NULL,
    source_xml_sha256 TEXT NOT NULL, claim_type TEXT NOT NULL DEFAULT 'case_reported',
    CHECK(start_offset >= 0 AND end_offset > start_offset)
);
CREATE TABLE IF NOT EXISTS concepts (
    concept_pk INTEGER PRIMARY KEY, article_pk INTEGER NOT NULL REFERENCES articles(article_pk) ON DELETE CASCADE,
    original_term TEXT NOT NULL, normalized_id TEXT NOT NULL DEFAULT '', system TEXT NOT NULL DEFAULT '',
    mapping_confidence REAL, UNIQUE(article_pk, original_term, normalized_id, system)
);
CREATE TABLE IF NOT EXISTS clinical_events (
    event_pk INTEGER PRIMARY KEY, article_pk INTEGER NOT NULL REFERENCES articles(article_pk) ON DELETE CASCADE,
    event_type TEXT NOT NULL, original_text TEXT NOT NULL, section_pk INTEGER REFERENCES sections(section_pk),
    evidence_id TEXT REFERENCES evidence_spans(evidence_id), source_label TEXT NOT NULL DEFAULT 'case_reported'
);
CREATE TABLE IF NOT EXISTS assets (
    asset_id TEXT PRIMARY KEY, article_pk INTEGER NOT NULL REFERENCES articles(article_pk) ON DELETE CASCADE,
    asset_type TEXT NOT NULL, label TEXT NOT NULL DEFAULT '', caption TEXT NOT NULL DEFAULT '',
    graphic_refs_json TEXT NOT NULL DEFAULT '[]', license TEXT NOT NULL DEFAULT '',
    reuse_decision TEXT NOT NULL, source_xml_sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cards (
    card_id TEXT PRIMARY KEY, article_pk INTEGER NOT NULL REFERENCES articles(article_pk) ON DELETE CASCADE,
    card_type TEXT NOT NULL, front TEXT NOT NULL, back TEXT NOT NULL,
    extra TEXT NOT NULL DEFAULT '', tags_json TEXT NOT NULL DEFAULT '[]',
    content_hash TEXT NOT NULL, evidence_json TEXT NOT NULL DEFAULT '[]',
    review_status TEXT NOT NULL DEFAULT 'needs_review', generator_version TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS card_claims (
    claim_id TEXT PRIMARY KEY, card_id TEXT NOT NULL REFERENCES cards(card_id) ON DELETE CASCADE,
    claim_text TEXT NOT NULL, claim_type TEXT NOT NULL DEFAULT 'case_reported',
    evidence_ids_json TEXT NOT NULL DEFAULT '[]', evidence_covered INTEGER NOT NULL DEFAULT 0,
    high_risk INTEGER NOT NULL DEFAULT 0 CHECK(high_risk IN (0,1))
);
CREATE TABLE IF NOT EXISTS claim_assessments (
    assessment_pk INTEGER PRIMARY KEY, claim_id TEXT NOT NULL,
    reviewer_id TEXT NOT NULL, reviewer_role TEXT NOT NULL, supported INTEGER NOT NULL CHECK(supported IN (0,1)),
    reviewed_at TEXT NOT NULL, claim_hash TEXT NOT NULL, evidence_hash TEXT NOT NULL,
    decision_source TEXT NOT NULL DEFAULT 'authenticated_hmac'
);
CREATE TRIGGER IF NOT EXISTS claim_assessments_no_update
BEFORE UPDATE ON claim_assessments BEGIN SELECT RAISE(ABORT, 'claim_assessments is append-only'); END;
CREATE TRIGGER IF NOT EXISTS claim_assessments_no_delete
BEFORE DELETE ON claim_assessments BEGIN SELECT RAISE(ABORT, 'claim_assessments is append-only'); END;
CREATE TABLE IF NOT EXISTS review_decisions (
    decision_pk INTEGER PRIMARY KEY, card_id TEXT NOT NULL REFERENCES cards(card_id) ON DELETE CASCADE,
    reviewer TEXT NOT NULL, role TEXT NOT NULL DEFAULT '', decision TEXT NOT NULL,
    reviewed_at TEXT NOT NULL, content_hash TEXT NOT NULL, evidence_hash TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '', decision_source TEXT NOT NULL DEFAULT 'local_unverified'
);
CREATE TRIGGER IF NOT EXISTS review_decisions_no_update
BEFORE UPDATE ON review_decisions BEGIN SELECT RAISE(ABORT, 'review_decisions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS review_decisions_no_delete
BEFORE DELETE ON review_decisions BEGIN SELECT RAISE(ABORT, 'review_decisions is append-only'); END;
CREATE TABLE IF NOT EXISTS guideline_sources (
    guideline_id TEXT PRIMARY KEY, publisher TEXT NOT NULL, title TEXT NOT NULL,
    stable_url TEXT NOT NULL, publication_date TEXT NOT NULL DEFAULT '', last_updated TEXT NOT NULL DEFAULT '',
    accessed_at TEXT NOT NULL, version TEXT NOT NULL DEFAULT '', allowlisted INTEGER NOT NULL DEFAULT 0,
    review_status TEXT NOT NULL DEFAULT 'needs_review'
);
CREATE TABLE IF NOT EXISTS guideline_claims (
    guideline_claim_id TEXT PRIMARY KEY, guideline_id TEXT NOT NULL REFERENCES guideline_sources(guideline_id),
    recommendation_text TEXT NOT NULL, strength_grade TEXT NOT NULL DEFAULT '',
    evidence_reference TEXT NOT NULL DEFAULT '', review_status TEXT NOT NULL DEFAULT 'needs_review'
);
CREATE TABLE IF NOT EXISTS identity_conflicts (
    conflict_pk INTEGER PRIMARY KEY, identifier_type TEXT NOT NULL, identifier_value TEXT NOT NULL,
    article_pks_json TEXT NOT NULL, detected_at TEXT NOT NULL,
    UNIQUE(identifier_type, identifier_value, article_pks_json)
);
CREATE VIRTUAL TABLE IF NOT EXISTS article_fts USING fts5(title, abstract, section_text);
"""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def sha256(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def card_content_hash(front: str, back: str, extra: str = "", tags: list[str] | None = None) -> str:
    payload = str(front) + "\0" + str(back) + "\0" + str(extra) + "\0" + json.dumps(tags or [], ensure_ascii=False, separators=(",", ":"))
    return sha256(payload)


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def initialize(conn: sqlite3.Connection) -> None:
    has_migrations = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone()
    if has_migrations:
        current_before = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] or 0
        if current_before > DB_SCHEMA_VERSION:
            raise RuntimeError(f"Database schema {current_before} is newer than supported {DB_SCHEMA_VERSION}")
    conn.executescript(SCHEMA)
    current = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] or 0
    if current < 1:
        conn.execute("INSERT INTO schema_migrations(version, applied_at, description) VALUES(1, ?, ?)",
                      (_now(), "Initial normalized case/evidence/card schema"))
        current = 1
    if current < 2:
        conn.execute("INSERT INTO schema_migrations(version, applied_at, description) VALUES(2, ?, ?)",
                      (_now(), "Append-only authenticated claim assessments and fail-closed claim coverage"))
        current = 2
    if current < 3:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(card_claims)")}
        if "high_risk" not in columns:
            conn.execute("ALTER TABLE card_claims ADD COLUMN high_risk INTEGER NOT NULL DEFAULT 0 CHECK(high_risk IN (0,1))")
        conn.execute("INSERT INTO schema_migrations(version, applied_at, description) VALUES(3, ?, ?)",
                      (_now(), "High-risk claim flag for independent double review"))
        current = 3
    if current < 4:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(cards)")}
        if "extra" not in columns:
            conn.execute("ALTER TABLE cards ADD COLUMN extra TEXT NOT NULL DEFAULT ''")
        if "tags_json" not in columns:
            conn.execute("ALTER TABLE cards ADD COLUMN tags_json TEXT NOT NULL DEFAULT '[]'")
        conn.execute("INSERT INTO schema_migrations(version, applied_at, description) VALUES(4, ?, ?)",
                      (_now(), "Bind approvals to full card back panel and tags"))
    conn.commit()


def _xml_sections(xml_path: Path) -> tuple[str, list[tuple[str, str, str]]]:
    """Extract sections using the same text normalization/order as cases2anki."""
    root = ET.parse(xml_path).getroot()
    if root.tag.rsplit("}", 1)[-1] != "article":
        raise ValueError(f"Not a JATS article: {xml_path}")
    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]
    def txt(node) -> str:
        return re.sub(r"\s+", " ", " ".join(t for t in node.itertext() if t and t.strip())).strip()
    title_node = next((n for n in root.iter() if local(n.tag) == "article-title"), None)
    title = txt(title_node) if title_node is not None else xml_path.stem
    body = next((n for n in root.iter() if local(n.tag) == "body"), None)
    sections: list[tuple[str, str, str]] = []
    def walk(sec, parent_title: str = "") -> None:
        heading = next((c for c in list(sec) if local(c.tag) == "title"), None)
        sec_title = txt(heading) if heading is not None else parent_title or "Case details"
        chunks = []
        for child in list(sec):
            kind = local(child.tag)
            if kind in {"title", "sec"}:
                continue
            if kind in {"p", "list", "disp-quote", "boxed-text", "fig", "table-wrap", "supplementary-material"}:
                value = txt(child)
                if value:
                    chunks.append(value)
        sec_text = re.sub(r"\s+", " ", " ".join(chunks)).strip()
        if sec_text:
            category = re.sub(r"[^a-z0-9]+", "_", sec_title.lower()).strip("_") or "other"
            sections.append((sec_title, category, sec_text))
        for child in list(sec):
            if local(child.tag) == "sec":
                walk(child, sec_title)
    if body is not None:
        for node in list(body):
            kind = local(node.tag)
            if kind == "sec":
                walk(node)
            elif kind in {"p", "fig", "table-wrap"}:
                value = txt(node)
                if value:
                    sections.append(("Case details", "other", value))
    return title, sections


def _identity_conflicts(conn: sqlite3.Connection) -> None:
    for kind, col in (("doi", "doi"), ("pmid", "pmid")):
        rows = conn.execute(f"SELECT {col} AS ident, GROUP_CONCAT(article_pk) AS pks, COUNT(*) AS n FROM articles WHERE {col} != '' GROUP BY {col} HAVING COUNT(*) > 1").fetchall()
        for row in rows:
            pks = sorted(int(x) for x in row["pks"].split(","))
            conn.execute("INSERT OR IGNORE INTO identity_conflicts(identifier_type, identifier_value, article_pks_json, detected_at) VALUES(?,?,?,?)",
                         (kind, row["ident"], json.dumps(pks), _now()))


def import_case_folder(conn: sqlite3.Connection, folder: Path) -> dict[str, Any]:
    xml_path = folder / "article.xml"
    meta_path = folder / "meta.json"
    if not xml_path.is_file() or not meta_path.is_file():
        raise ValueError(f"Expected article.xml and meta.json in {folder}")
    xml_bytes = xml_path.read_bytes()
    digest = sha256(xml_bytes)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if not isinstance(meta, dict):
        raise ValueError(f"meta.json must contain an object: {meta_path}")
    try:
        metadata_schema = int(meta.get("schema_version", 1))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid metadata schema_version in {meta_path}") from exc
    if metadata_schema < 1 or metadata_schema > SUPPORTED_METADATA_SCHEMA_VERSION:
        raise ValueError(f"Unsupported metadata schema_version {metadata_schema}; supported through {SUPPORTED_METADATA_SCHEMA_VERSION}")
    declared = str(meta.get("content_sha256") or meta.get("source_xml_sha256") or "").lower()
    if declared and declared != digest:
        raise ValueError(f"Source XML hash mismatch in {folder}: metadata={declared}, actual={digest}")
    title, section_rows = _xml_sections(xml_path)
    pmcid = str(meta.get("pmcid") or meta.get("id") or folder.name).strip().upper()
    if not re.fullmatch(r"PMC\d+", pmcid):
        pmcid = ""
    pmid = str(meta.get("pmid") or "").strip()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", str(meta.get("doi") or "").strip(), flags=re.I).lower()
    status_obj = meta.get("article_status") if isinstance(meta.get("article_status"), dict) else {}
    status = str(status_obj.get("status") or "unknown")
    if status not in {"current", "corrected", "retracted", "expression_of_concern", "unknown", "conflict"}:
        status = "unknown"
    now = _now()
    existing = conn.execute("SELECT * FROM articles WHERE pmcid=?", (pmcid,)).fetchone() if pmcid else None
    if existing and existing["source_xml_sha256"] == digest and existing["meta_json"] == json.dumps(meta, ensure_ascii=False, sort_keys=True):
        return {"pmcid": pmcid, "status": "unchanged", "article_pk": existing["article_pk"]}
    if existing:
        article_pk = int(existing["article_pk"])
        conflicts = []
        for kind, old_value, new_value in (("doi", str(existing["doi"] or ""), doi), ("pmid", str(existing["pmid"] or ""), pmid)):
            if old_value and new_value and old_value != new_value:
                conflicts.append({"type": kind, "existing": old_value, "incoming": new_value})
                conn.execute("INSERT OR IGNORE INTO identity_conflicts(identifier_type,identifier_value,article_pks_json,detected_at) VALUES(?,?,?,?)",
                             (kind, f"{old_value} <> {new_value}", json.dumps([article_pk]), now))
        # Do not silently replace conflicting external identifiers on the same PMCID.
        pmid_to_store = str(existing["pmid"] or pmid) if conflicts else pmid
        doi_to_store = str(existing["doi"] or doi) if conflicts else doi
        status_to_store = "conflict" if conflicts else status
        status_json = dict(status_obj)
        if conflicts:
            status_json["identity_conflicts"] = conflicts
        conn.execute("UPDATE articles SET pmid=?,doi=?,title=?,journal=?,year=?,specialty=?,source_url=?,license=?,source_xml_sha256=?,source_path=?,meta_json=?,article_status=?,status_json=?,parser_version=?,imported_at=? WHERE article_pk=?",
                     (pmid_to_store, doi_to_store, str(meta.get("title") or title), str(meta.get("journal") or ""), str(meta.get("year") or ""), str(meta.get("specialty") or "general-medicine"), str(meta.get("source_url") or ""), str(meta.get("license") or ""), digest, str(folder.resolve()), json.dumps(meta, ensure_ascii=False, sort_keys=True), status_to_store, json.dumps(status_json, ensure_ascii=False, sort_keys=True), str(meta.get("parser_version") or ""), now, article_pk))
        conn.execute("DELETE FROM card_claims WHERE card_id IN (SELECT card_id FROM cards WHERE article_pk=?)", (article_pk,))
        conn.execute("UPDATE cards SET review_status='needs_review', evidence_json='[]' WHERE article_pk=?", (article_pk,))
        conn.execute("DELETE FROM sections WHERE article_pk=?", (article_pk,))
        conn.execute("DELETE FROM assets WHERE article_pk=?", (article_pk,))
        conn.execute("DELETE FROM article_fts WHERE rowid=?", (article_pk,))
    else:
        cur = conn.execute("INSERT INTO articles(pmcid,pmid,doi,title,journal,year,specialty,source_url,license,source_xml_sha256,source_path,meta_json,article_status,status_json,parser_version,imported_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (pmcid or None, pmid, doi, str(meta.get("title") or title), str(meta.get("journal") or ""), str(meta.get("year") or ""), str(meta.get("specialty") or "general-medicine"), str(meta.get("source_url") or ""), str(meta.get("license") or ""), digest, str(folder.resolve()), json.dumps(meta, ensure_ascii=False, sort_keys=True), status, json.dumps(status_obj, ensure_ascii=False, sort_keys=True), str(meta.get("parser_version") or ""), now))
        article_pk = int(cur.lastrowid)
    section_texts = []
    for ordinal, (sec_title, category, text) in enumerate(section_rows):
        cur = conn.execute("INSERT INTO sections(article_pk,ordinal,title,category,text,text_sha256) VALUES(?,?,?,?,?,?)",
                           (article_pk, ordinal, sec_title, category, text, sha256(text)))
        section_pk = int(cur.lastrowid)
        section_texts.append(text)
    abstract = str(meta.get("abstract") or "")
    conn.execute("INSERT INTO article_fts(rowid,title,abstract,section_text) VALUES(?,?,?,?)",
                 (article_pk, str(meta.get("title") or title), abstract, "\n\n".join(section_texts)))
    root = ET.parse(xml_path).getroot()
    for node in root.iter():
        kind = node.tag.rsplit("}", 1)[-1]
        if kind not in {"fig", "table-wrap"}:
            continue
        label = caption = ""
        refs = []
        for child in node.iter():
            child_kind = child.tag.rsplit("}", 1)[-1]
            if child_kind == "label" and not label:
                label = re.sub(r"\s+", " ", " ".join(child.itertext())).strip()
            elif child_kind == "caption" and not caption:
                caption = re.sub(r"\s+", " ", " ".join(child.itertext())).strip()
            elif child_kind in {"graphic", "inline-graphic", "media"}:
                href = child.attrib.get("{http://www.w3.org/1999/xlink}href") or child.attrib.get("href")
                if href and href not in refs:
                    refs.append(href)
        asset_id = sha256(f"{pmcid}|{kind}|{label}|{caption}|{refs}")[:20]
        lic = str(meta.get("license") or "")
        decision = "manual_review_required" if lic else "blocked_unknown_license"
        conn.execute("INSERT OR REPLACE INTO assets(asset_id,article_pk,asset_type,label,caption,graphic_refs_json,license,reuse_decision,source_xml_sha256) VALUES(?,?,?,?,?,?,?,?,?)",
                     (asset_id, article_pk, "figure" if kind == "fig" else "table", label, caption,
                      json.dumps(refs, ensure_ascii=False), lic, decision, digest))
    _identity_conflicts(conn)
    return {"pmcid": pmcid, "status": "imported" if not existing else "updated", "article_pk": article_pk,
            "sections": len(section_rows), "source_xml_sha256": digest}


def import_review_queue(conn: sqlite3.Connection, queue_path: Path) -> dict[str, int]:
    """Import V7/V8 review cards and verify every evidence offset against stored source text."""
    imported = invalid = missing_articles = 0
    for line_no, line in enumerate(queue_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid review queue JSON on line {line_no}: {exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"Review queue line {line_no} must be a JSON object")
        card_id = str(row.get("card_id") or "").strip()
        case_id = str(row.get("case_id") or "").strip()
        if not card_id or not case_id:
            raise ValueError(f"Review queue line {line_no} lacks card_id or case_id")
        article = conn.execute("SELECT * FROM articles WHERE pmcid=? OR source_path LIKE ? ORDER BY article_pk LIMIT 1",
                               (case_id.upper(), f"%/{case_id}")).fetchone()
        if not article:
            missing_articles += 1
            continue
        front, back = str(row.get("front") or ""), str(row.get("back") or "")
        extra = str(row.get("extra") or "")
        tags = row.get("tags") if isinstance(row.get("tags"), list) else []
        content_hash = card_content_hash(front, back, extra, tags)
        old = conn.execute("SELECT content_hash FROM cards WHERE card_id=?", (card_id,)).fetchone()
        review_status = "needs_review"
        conn.execute("INSERT INTO cards(card_id,article_pk,card_type,front,back,extra,tags_json,content_hash,evidence_json,review_status,generator_version) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(card_id) DO UPDATE SET article_pk=excluded.article_pk,card_type=excluded.card_type,front=excluded.front,back=excluded.back,extra=excluded.extra,tags_json=excluded.tags_json,content_hash=excluded.content_hash,evidence_json=excluded.evidence_json,review_status='needs_review'",
                     (card_id, article["article_pk"], str(row.get("card_type") or "unknown"), front, back, extra,
                      json.dumps(tags,ensure_ascii=False), content_hash, "[]", review_status, "8.2.0"))
        evidence_ids = []
        conn.execute("DELETE FROM card_claims WHERE card_id=?", (card_id,))
        imported_spans: dict[str, str] = {}
        for span in row.get("evidence_spans") or []:
            if not isinstance(span, dict):
                invalid += 1
                continue
            title = str(span.get("section") or "")
            section_index = span.get("section_index")
            start, end = span.get("start_char"), span.get("end_char")
            quote = str(span.get("text") or "")
            if type(section_index) is int and section_index >= 0:
                section = conn.execute("SELECT * FROM sections WHERE article_pk=? AND ordinal=?",
                                       (article["article_pk"], section_index)).fetchone()
            else:
                section = conn.execute("SELECT * FROM sections WHERE article_pk=? AND title=? ORDER BY ordinal LIMIT 1",
                                       (article["article_pk"], title)).fetchone()
            if not section or type(start) is not int or type(end) is not int or start < 0 or end <= start or end > len(section["text"]) or section["text"][start:end] != quote or article["source_xml_sha256"] == "":
                invalid += 1
                continue
            evidence_id = str(span.get("evidence_id") or sha256(f"{case_id}|{title}|{start}|{end}|{quote}")[:20])
            conn.execute("INSERT OR REPLACE INTO evidence_spans(evidence_id,article_pk,section_pk,start_offset,end_offset,quote,source_xml_sha256,claim_type) VALUES(?,?,?,?,?,?,?,?)",
                         (evidence_id, article["article_pk"], section["section_pk"], start, end, quote,
                          article["source_xml_sha256"], "case_reported"))
            evidence_ids.append(evidence_id)
            imported_spans[evidence_id] = quote
        # Evidence quotations are not themselves answer claims. Every answer
        # clause must be explicitly declared and linked to validated evidence.
        declared_claims = row.get("claims")
        if not isinstance(declared_claims, list) or not declared_claims:
            declared_claims = [{"text": back, "claim_type": "case_reported",
                                "evidence_ids": [], "requires_review": True}]
        for claim_index, claim in enumerate(declared_claims):
            if not isinstance(claim, dict) or not str(claim.get("text") or "").strip():
                invalid += 1
                continue
            claim_text = str(claim["text"]).strip()
            claim_type = str(claim.get("claim_type") or "case_reported")
            linked = claim.get("evidence_ids") or []
            if not isinstance(linked, list):
                linked = []
            valid_links = [str(eid) for eid in linked if str(eid) in imported_spans]
            # Evidence links are traceability only. Coverage remains false until
            # an authenticated clinician explicitly assesses this claim.
            covered = False
            claim_id = sha256(f"{card_id}|{claim_index}|{claim_text}")[:24]
            conn.execute("INSERT OR REPLACE INTO card_claims(claim_id,card_id,claim_text,claim_type,evidence_ids_json,evidence_covered,high_risk) VALUES(?,?,?,?,?,?,?)",
                         (claim_id, card_id, claim_text, claim_type, json.dumps(valid_links), int(covered), int(claim.get("high_risk") is True)))
        conn.execute("UPDATE cards SET evidence_json=? WHERE card_id=?", (json.dumps(evidence_ids), card_id))
        imported += 1
    return {"cards_imported": imported, "invalid_evidence_spans": invalid, "missing_articles": missing_articles}


def import_review_decisions(conn: sqlite3.Connection, decisions_path: Path) -> dict[str, int]:
    """Append decisions only when reviewer supplies hashes matching the exact current card."""
    imported = stale = invalid = 0
    for line_no, line in enumerate(decisions_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid decision JSON on line {line_no}: {exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"Decision line {line_no} must be an object")
        card_id = str(row.get("card_id") or "").strip()
        reviewer = str(row.get("reviewer") or "").strip()
        role = str(row.get("role") or "").strip()
        decision = str(row.get("decision") or row.get("status") or "").strip().lower().replace("-", "_")
        reviewed_at = str(row.get("reviewed_at") or "").strip()
        if not card_id or not reviewer or decision not in {"approved", "needs_changes", "rejected"} or not reviewed_at:
            invalid += 1
            continue
        try:
            dt.datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
        except ValueError:
            invalid += 1
            continue
        card = conn.execute("SELECT c.*,a.source_xml_sha256 FROM cards c JOIN articles a ON a.article_pk=c.article_pk WHERE c.card_id=?", (card_id,)).fetchone()
        if not card:
            stale += 1
            continue
        evidence_hash = sha256(card["evidence_json"] + "\0" + card["source_xml_sha256"])
        if row.get("content_hash") != card["content_hash"] or row.get("evidence_hash") != evidence_hash:
            stale += 1
            continue
        conn.execute("INSERT INTO review_decisions(card_id,reviewer,role,decision,reviewed_at,content_hash,evidence_hash,notes,decision_source) VALUES(?,?,?,?,?,?,?,?,?)",
                     (card_id, reviewer, role, decision, reviewed_at, card["content_hash"], evidence_hash,
                      str(row.get("notes") or ""), "local_unverified"))
        imported += 1
    return {"decisions_imported": imported, "stale_or_unknown_cards": stale, "invalid_decisions": invalid}



def import_authenticated_review_decisions(conn: sqlite3.Connection, decisions_path: Path, secret: str) -> dict[str, int]:
    """Import HMAC-authenticated reviewer decisions; plain JSON names never approve cards."""
    from v5_release import verify_reviewer_token
    imported = stale = invalid = unauthorized = 0
    for line_no, line in enumerate(decisions_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            claims = verify_reviewer_token(row.get("reviewer_token") or {}, secret=secret)
        except (json.JSONDecodeError, ValueError, PermissionError, TypeError) as exc:
            invalid += 1
            continue
        card_id = str(row.get("card_id") or "").strip()
        decision = str(row.get("decision") or "").strip().lower().replace("-", "_")
        reviewed_at = str(row.get("reviewed_at") or "").strip()
        if decision not in {"approved", "needs_changes", "rejected"} or not card_id or not reviewed_at:
            invalid += 1
            continue
        try:
            stamp = dt.datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError("timezone required")
        except ValueError:
            invalid += 1
            continue
        if decision == "approved" and claims["role"] not in {"clinician", "clinical_adjudicator"}:
            unauthorized += 1
            continue
        card = conn.execute("SELECT c.*,a.source_xml_sha256 FROM cards c JOIN articles a ON a.article_pk=c.article_pk WHERE c.card_id=?", (card_id,)).fetchone()
        if not card:
            stale += 1
            continue
        evidence_hash = sha256(card["evidence_json"] + "\0" + card["source_xml_sha256"])
        if row.get("content_hash") != card["content_hash"] or row.get("evidence_hash") != evidence_hash:
            stale += 1
            continue
        conn.execute("INSERT INTO review_decisions(card_id,reviewer,role,decision,reviewed_at,content_hash,evidence_hash,notes,decision_source) VALUES(?,?,?,?,?,?,?,?,?)",
                     (card_id, claims["reviewer_id"], claims["role"], decision, stamp.astimezone(dt.timezone.utc).isoformat(),
                      card["content_hash"], evidence_hash, str(row.get("notes") or ""), "authenticated_hmac"))
        imported += 1
    return {"authenticated_decisions_imported": imported, "stale_or_unknown_cards": stale,
            "invalid_decisions": invalid, "unauthorized_decisions": unauthorized}



def claim_evidence_hash(conn: sqlite3.Connection, claim_id: str) -> str:
    claim = conn.execute("SELECT cc.evidence_ids_json,a.source_xml_sha256 FROM card_claims cc JOIN cards c ON c.card_id=cc.card_id JOIN articles a ON a.article_pk=c.article_pk WHERE cc.claim_id=?", (claim_id,)).fetchone()
    if not claim:
        raise ValueError("Unknown claim")
    evidence_ids = json.loads(claim["evidence_ids_json"])
    rows = []
    for evidence_id in evidence_ids:
        span = conn.execute("SELECT evidence_id,start_offset,end_offset,quote,source_xml_sha256 FROM evidence_spans WHERE evidence_id=?", (evidence_id,)).fetchone()
        if not span or span["source_xml_sha256"] != claim["source_xml_sha256"]:
            raise ValueError("Claim references missing or stale evidence")
        rows.append(dict(span))
    return sha256(json.dumps({"spans": rows, "source_xml_sha256": claim["source_xml_sha256"]}, ensure_ascii=False, sort_keys=True))


def import_claim_assessments(conn: sqlite3.Connection, assessments_path: Path, secret: str) -> dict[str, int]:
    """Append authenticated clinician support/unsupported assessments for individual answer claims."""
    from v5_release import verify_reviewer_token
    imported = stale = invalid = unauthorized = 0
    for line_no, line in enumerate(assessments_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            reviewer = verify_reviewer_token(row.get("reviewer_token") or {}, secret=secret)
        except (json.JSONDecodeError, ValueError, PermissionError, TypeError):
            invalid += 1
            continue
        if reviewer["role"] not in {"clinician", "clinical_adjudicator"}:
            unauthorized += 1
            continue
        claim_id = str(row.get("claim_id") or "").strip()
        if not claim_id or type(row.get("supported")) is not bool:
            invalid += 1
            continue
        try:
            stamp = dt.datetime.fromisoformat(str(row.get("reviewed_at") or "").replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError("timezone required")
            claim = conn.execute("SELECT claim_text FROM card_claims WHERE claim_id=?", (claim_id,)).fetchone()
            if not claim:
                stale += 1
                continue
            expected_claim_hash = sha256(claim["claim_text"])
            evidence_ids = json.loads(conn.execute("SELECT evidence_ids_json FROM card_claims WHERE claim_id=?", (claim_id,)).fetchone()["evidence_ids_json"])
            if row["supported"] is True and not evidence_ids:
                invalid += 1
                continue
            expected_evidence_hash = claim_evidence_hash(conn, claim_id)
            if row.get("claim_hash") != expected_claim_hash or row.get("evidence_hash") != expected_evidence_hash:
                stale += 1
                continue
        except (ValueError, TypeError, json.JSONDecodeError):
            stale += 1
            continue
        conn.execute("INSERT INTO claim_assessments(claim_id,reviewer_id,reviewer_role,supported,reviewed_at,claim_hash,evidence_hash) VALUES(?,?,?,?,?,?,?)",
                     (claim_id, reviewer["reviewer_id"], reviewer["role"], int(row["supported"]),
                      stamp.astimezone(dt.timezone.utc).isoformat(), expected_claim_hash, expected_evidence_hash))
        assessment_summary = conn.execute("SELECT COUNT(DISTINCT reviewer_id) AS reviewers, MIN(supported) AS all_supported FROM claim_assessments WHERE claim_id=? AND claim_hash=? AND evidence_hash=?",
                                           (claim_id, expected_claim_hash, expected_evidence_hash)).fetchone()
        claim_risk = conn.execute("SELECT high_risk FROM card_claims WHERE claim_id=?", (claim_id,)).fetchone()["high_risk"]
        required_reviewers = 2 if claim_risk else 1
        covered = assessment_summary["reviewers"] >= required_reviewers and assessment_summary["all_supported"] == 1
        conn.execute("UPDATE card_claims SET evidence_covered=? WHERE claim_id=?", (int(covered), claim_id))
        imported += 1
    return {"assessments_imported": imported, "stale_claims": stale, "invalid_assessments": invalid,
            "unauthorized_assessments": unauthorized}



def card_approval_status(conn: sqlite3.Connection, card: dict[str, Any]) -> str:
    """Return approved only if current converter content and every evidence span match the indexed card."""
    row = conn.execute("SELECT c.*,a.source_xml_sha256,a.article_pk FROM cards c JOIN articles a ON a.article_pk=c.article_pk WHERE c.card_id=?",
                       (str(card.get("card_id") or ""),)).fetchone()
    if not row:
        return "needs_review"
    content_hash = card_content_hash(str(card.get("front") or ""), str(card.get("back") or ""),
                                     str(card.get("extra") or ""), card.get("tags") if isinstance(card.get("tags"),list) else [])
    if content_hash != row["content_hash"] or str(card.get("source_xml_sha256") or "") != row["source_xml_sha256"]:
        return "needs_review"
    expected = []
    for span in card.get("evidence_spans") or []:
        if not isinstance(span, dict):
            return "needs_review"
        ordinal = span.get("section_index")
        if type(ordinal) is not int:
            section_title = str(span.get("section") or "")
            section = conn.execute("SELECT ordinal FROM sections WHERE article_pk=? AND title=? ORDER BY ordinal LIMIT 1",
                                   (row["article_pk"], section_title)).fetchone()
            if not section:
                return "needs_review"
            ordinal = section["ordinal"]
        expected.append((ordinal, span.get("start_char"), span.get("end_char"), str(span.get("text") or "")))
    evidence_ids = json.loads(row["evidence_json"])
    if not evidence_ids:
        return "needs_review"
    placeholders = ",".join("?" for _ in evidence_ids)
    actual_rows = conn.execute(f"SELECT s.ordinal,e.start_offset,e.end_offset,e.quote FROM evidence_spans e JOIN sections s ON s.section_pk=e.section_pk WHERE e.evidence_id IN ({placeholders}) ORDER BY s.ordinal,e.start_offset,e.end_offset",
                               evidence_ids).fetchall()
    actual = [(r["ordinal"],r["start_offset"],r["end_offset"],r["quote"]) for r in actual_rows]
    if not expected or sorted(expected) != sorted(actual):
        return "needs_review"
    claim_state = conn.execute("SELECT COUNT(*) AS total, COALESCE(SUM(evidence_covered),0) AS covered FROM card_claims WHERE card_id=?",
                               (str(card.get("card_id") or ""),)).fetchone()
    if claim_state["total"] == 0 or claim_state["covered"] != claim_state["total"]:
        return "needs_review"
    return effective_review_status(conn, str(card.get("card_id") or ""))


def effective_review_status(conn: sqlite3.Connection, card_id: str) -> str:
    card = conn.execute("SELECT c.content_hash,c.evidence_json,a.source_xml_sha256 FROM cards c JOIN articles a ON a.article_pk=c.article_pk WHERE c.card_id=?", (card_id,)).fetchone()
    if not card:
        return "unknown_card"
    evidence_hash = sha256(card["evidence_json"] + "\0" + card["source_xml_sha256"])
    decision = conn.execute("SELECT decision FROM review_decisions WHERE card_id=? AND content_hash=? AND evidence_hash=? AND decision_source='authenticated_hmac' ORDER BY decision_pk DESC LIMIT 1",
                            (card_id, card["content_hash"], evidence_hash)).fetchone()
    return str(decision["decision"]) if decision else "needs_review"



def import_guideline_records(conn: sqlite3.Connection, records_path: Path, allowlist_path: Path) -> dict[str, int]:
    """Import dated guideline records into the separate guideline-only tables."""
    from v5_release import validate_guideline_record
    config = json.loads(allowlist_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or not isinstance(config.get("hosts"), list):
        raise ValueError("Guideline allow-list must contain a hosts array")
    if config.get("governance_approved") is not True or not str(config.get("approved_by") or "").strip():
        raise PermissionError("Guideline allow-list requires explicit governance_approved=true and approved_by")
    dt.date.fromisoformat(str(config.get("reviewed_at") or "")[:10])
    hosts = {str(host).lower() for host in config["hosts"]}
    if not hosts:
        raise ValueError("Guideline allow-list is empty; guideline ingestion is disabled")
    imported = rejected = 0
    for line_no, line in enumerate(records_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            rejected += 1
            continue
        try:
            clean = validate_guideline_record(row, hosts)
        except (ValueError, PermissionError):
            rejected += 1
            continue
        conn.execute("""INSERT INTO guideline_sources(guideline_id,publisher,title,stable_url,publication_date,last_updated,accessed_at,version,allowlisted,review_status)
          VALUES(?,?,?,?,?,?,?,?,1,'needs_review') ON CONFLICT(guideline_id) DO UPDATE SET
          publisher=excluded.publisher,title=excluded.title,stable_url=excluded.stable_url,
          publication_date=excluded.publication_date,last_updated=excluded.last_updated,
          accessed_at=excluded.accessed_at,version=excluded.version,allowlisted=1,review_status='needs_review'""",
          (clean["guideline_id"],clean["publisher"],clean["title"],clean["stable_url"],clean["publication_date"],
           clean["last_updated"],clean["accessed_at"],clean["version"]))
        claim_id = sha256(clean["guideline_id"] + "\0" + clean["recommendation_text"])
        conn.execute("""INSERT INTO guideline_claims(guideline_claim_id,guideline_id,recommendation_text,strength_grade,evidence_reference,review_status)
          VALUES(?,?,?,?,?,'needs_review') ON CONFLICT(guideline_claim_id) DO UPDATE SET
          recommendation_text=excluded.recommendation_text,strength_grade=excluded.strength_grade,
          evidence_reference=excluded.evidence_reference,review_status='needs_review'""",
          (claim_id,clean["guideline_id"],clean["recommendation_text"],clean["strength_grade"],clean.get("evidence_reference", "")))
        imported += 1
    return {"guidelines_imported": imported, "rejected_records": rejected}


def import_library(conn: sqlite3.Connection, root: Path) -> list[dict[str, Any]]:
    folders = sorted({p.parent for p in root.rglob("article.xml")}) if root.is_dir() else [root.parent]
    results = []
    for folder in folders:
        try:
            with conn:
                results.append(import_case_folder(conn, folder))
        except (OSError, ValueError, ET.ParseError, sqlite3.Error, json.JSONDecodeError) as exc:
            results.append({"folder": str(folder), "status": "error", "error": str(exc)})
    return results


def search_cases(conn: sqlite3.Connection, query: str = "", specialty: str = "", year: str = "",
                 diagnosis: str = "", status: str = "") -> list[dict[str, Any]]:
    clauses, params = [], []
    if specialty:
        clauses.append("a.specialty = ?"); params.append(specialty)
    if year:
        clauses.append("a.year = ?"); params.append(year)
    if status:
        clauses.append("a.article_status = ?"); params.append(status)
    if diagnosis:
        clauses.append("EXISTS (SELECT 1 FROM clinical_events e WHERE e.article_pk=a.article_pk AND e.event_type='diagnosis' AND e.original_text LIKE ?)")
        params.append(f"%{diagnosis}%")
    if query:
        sql = "SELECT a.article_pk,a.pmcid,a.pmid,a.doi,a.title,a.year,a.specialty,a.article_status,bm25(article_fts) AS rank FROM article_fts JOIN articles a ON a.article_pk=article_fts.rowid WHERE article_fts MATCH ?"
        params.insert(0, query)
    else:
        sql = "SELECT a.article_pk,a.pmcid,a.pmid,a.doi,a.title,a.year,a.specialty,a.article_status,0 AS rank FROM articles a"
    if clauses:
        sql += (" AND " if " WHERE " in sql else " WHERE ") + " AND ".join(clauses)
    sql += " ORDER BY rank, a.title LIMIT 500"
    try:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    except sqlite3.OperationalError as exc:
        raise ValueError(f"Invalid full-text query: {exc}") from exc



def export_review_queue(conn: sqlite3.Connection, output: Path) -> int:
    """Export current card/claim hashes for signed review and claim assessment."""
    rows = conn.execute("SELECT c.*,a.pmcid,a.title AS article_title,a.specialty,a.source_xml_sha256,a.source_url FROM cards c JOIN articles a ON a.article_pk=c.article_pk ORDER BY a.specialty,a.pmcid,c.card_id").fetchall()
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output.open("w", encoding="utf-8") as handle:
        for card in rows:
            evidence_ids = json.loads(card["evidence_json"])
            spans = []
            for evidence_id in evidence_ids:
                span = conn.execute("SELECT e.evidence_id,s.ordinal,s.title,e.start_offset,e.end_offset,e.quote,e.source_xml_sha256 FROM evidence_spans e JOIN sections s ON s.section_pk=e.section_pk WHERE e.evidence_id=?", (evidence_id,)).fetchone()
                if span:
                    spans.append(dict(span))
            claims = []
            for claim in conn.execute("SELECT * FROM card_claims WHERE card_id=? ORDER BY claim_id", (card["card_id"],)):
                claims.append({"claim_id":claim["claim_id"],"text":claim["claim_text"],"claim_type":claim["claim_type"],
                    "evidence_ids":json.loads(claim["evidence_ids_json"]),"claim_hash":sha256(claim["claim_text"]),
                    "evidence_hash":claim_evidence_hash(conn,claim["claim_id"]),"high_risk":bool(claim["high_risk"]),
                    "evidence_covered":bool(claim["evidence_covered"])})
            evidence_hash = sha256(card["evidence_json"] + "\0" + card["source_xml_sha256"])
            claim_state = conn.execute("SELECT COUNT(*) AS total,COALESCE(SUM(evidence_covered),0) AS covered FROM card_claims WHERE card_id=?", (card["card_id"],)).fetchone()
            current_status = effective_review_status(conn,card["card_id"])
            if claim_state["total"] == 0 or claim_state["covered"] != claim_state["total"]:
                current_status = "needs_review"
            row = {"card_id":card["card_id"],"case_id":card["pmcid"],"title":card["article_title"],
                "specialty":card["specialty"],"card_type":card["card_type"],"front":card["front"],"back":card["back"],
                "extra":card["extra"],"tags":json.loads(card["tags_json"]),"content_hash":card["content_hash"],"evidence_hash":evidence_hash,"source_xml_sha256":card["source_xml_sha256"],
                "source_url":card["source_url"],"evidence_spans":spans,"claims":claims,
                "review_status":current_status}
            handle.write(json.dumps(row,ensure_ascii=False,sort_keys=True)+"\n")
            count += 1
    return count


def export_manifest(conn: sqlite3.Connection, output: Path) -> int:
    rows = conn.execute("SELECT pmcid,pmid,doi,title,year,specialty,article_status,source_xml_sha256,source_path FROM articles ORDER BY pmcid,title").fetchall()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build and query the local SQLite clinical case knowledge base")
    parser.add_argument("command", choices=("init", "import", "search", "manifest", "review-queue", "check"))
    parser.add_argument("--db", type=Path, default=Path("case_library.sqlite3"))
    parser.add_argument("--input", type=Path, help="Case library root or a case folder")
    parser.add_argument("--review-queue", type=Path, help="Optional V7/V8 review JSONL to import after source articles")
    parser.add_argument("--review-decisions", type=Path, help="Legacy unverified decision JSONL (never authorizes approved-only export)")
    parser.add_argument("--authenticated-review-decisions", type=Path, help="Signed reviewer decisions JSONL")
    parser.add_argument("--claim-assessments", type=Path, help="Signed clinician claim-support assessments JSONL")
    parser.add_argument("--review-secret-env", default="ANKI_REVIEW_HMAC_SECRET", help="Environment variable containing the token signing secret")
    parser.add_argument("--guideline-jsonl", type=Path, help="Separate dated guideline records JSONL")
    parser.add_argument("--guideline-allowlist", type=Path, default=Path(__file__).with_name("guideline_allowlist.json"))
    parser.add_argument("--query", default="")
    parser.add_argument("--specialty", default="")
    parser.add_argument("--year", default="")
    parser.add_argument("--diagnosis", default="")
    parser.add_argument("--status", default="")
    parser.add_argument("--output", type=Path, default=Path("case_manifest.jsonl"))
    args = parser.parse_args(argv)
    try:
        with connect(args.db) as conn:
            initialize(conn)
            if args.command == "init":
                print(f"Initialized {args.db} (schema {DB_SCHEMA_VERSION})")
            elif args.command == "import":
                if not args.input:
                    parser.error("import requires --input")
                results = import_library(conn, args.input)
                for row in results:
                    print(json.dumps(row, ensure_ascii=False))
                if args.review_queue:
                    with conn:
                        review_result = import_review_queue(conn, args.review_queue)
                    print(json.dumps(review_result, ensure_ascii=False))
                if args.review_decisions:
                    with conn:
                        decision_result = import_review_decisions(conn, args.review_decisions)
                    print(json.dumps(decision_result, ensure_ascii=False))
                if args.authenticated_review_decisions or args.claim_assessments:
                    secret = os.environ.get(args.review_secret_env, "")
                    if len(secret) < 32:
                        parser.error(f"{args.review_secret_env} must contain a >=32-character signing secret")
                    if args.authenticated_review_decisions:
                        with conn:
                            result = import_authenticated_review_decisions(conn, args.authenticated_review_decisions, secret)
                        print(json.dumps(result, ensure_ascii=False))
                    if args.claim_assessments:
                        with conn:
                            result = import_claim_assessments(conn, args.claim_assessments, secret)
                        print(json.dumps(result, ensure_ascii=False))
                if args.guideline_jsonl:
                    with conn:
                        result = import_guideline_records(conn, args.guideline_jsonl, args.guideline_allowlist)
                    print(json.dumps(result, ensure_ascii=False))
                print(f"Imported/updated {sum(r.get('status') in {'imported','updated'} for r in results)}; unchanged {sum(r.get('status')=='unchanged' for r in results)}; errors {sum(r.get('status')=='error' for r in results)}")
                return 1 if any(r.get("status") == "error" for r in results) else 0
            elif args.command == "search":
                for row in search_cases(conn, args.query, args.specialty, args.year, args.diagnosis, args.status):
                    print(json.dumps(row, ensure_ascii=False))
            elif args.command == "manifest":
                count = export_manifest(conn, args.output)
                print(f"Wrote {count} article records to {args.output}")
            elif args.command == "review-queue":
                count = export_review_queue(conn, args.output)
                print(f"Wrote {count} card/claim review records to {args.output}")
            elif args.command == "check":
                integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
                fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
                conflicts = conn.execute("SELECT COUNT(*) FROM identity_conflicts").fetchone()[0]
                print(json.dumps({"integrity": integrity, "foreign_key_errors": len(fk_errors), "identity_conflicts": conflicts}))
                return 0 if integrity == "ok" and not fk_errors else 1
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
