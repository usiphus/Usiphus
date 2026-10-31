#!/usr/bin/env python3
"""Harvest open-access, full-text clinical case reports from Europe PMC.

The script deliberately downloads only PMC records with open full text.  It
scores records for educational value but keeps the score transparent; it does
not claim to establish clinical importance or guideline validity.
"""
from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import hashlib
import html
import json
import math
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from source_integrity import check_article_status, fetch_crossref_updates, post_status_request
from v5_release import checkpoint as save_checkpoint, init_harvest_state, retry_delay, verify_cache

VERSION = "8.2.0"
EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
DEFAULT_UA = "ClinicalCaseFlashcards/8.1 (personal medical education)"

# Terms are intentionally clinical and fairly specific.  They are used both
# in the API query and in a second local specialty-relevance check.
SPECIALTIES: dict[str, dict[str, Any]] = {
    "anesthesia": {"label": "Anesthesiology", "terms": ["anesthesia", "anaesthesia", "airway", "perioperative", "regional anesthesia", "spinal anesthesia", "general anesthesia", "anesthetic complication"]},
    "cardiology": {"label": "Cardiology", "terms": ["cardiac", "heart", "myocardial", "cardiomyopathy", "coronary", "aortic", "atrial", "ventricular", "arrhythmia", "pericardial", "endocarditis", "valvular", "heart failure", "myocarditis"]},
    "cardiothoracic-surgery": {"label": "Cardiothoracic Surgery", "terms": ["thoracic surgery", "cardiac surgery", "coronary bypass", "valve replacement", "lung resection", "lobectomy", "pneumonectomy", "mediastinal", "aortic repair", "chest wall"]},
    "dermatology": {"label": "Dermatology", "terms": ["skin", "cutaneous", "dermatitis", "rash", "psoriasis", "pemphigus", "urticaria", "eczema", "bullous", "melanoma", "alopecia", "vasculitic rash"]},
    "emergency-medicine": {"label": "Emergency Medicine", "terms": ["emergency department", "emergency medicine", "acute trauma", "poisoning", "resuscitation", "shock", "acute abdomen", "anaphylaxis", "emergency presentation"]},
    "endocrinology": {"label": "Endocrinology", "terms": ["thyroid", "diabetes", "adrenal", "pituitary", "parathyroid", "insulin", "endocrine", "metabolic", "cushing", "addison"]},
    "gastroenterology": {"label": "Gastroenterology", "terms": ["gastrointestinal", "gastric", "hepatic", "liver", "pancreas", "pancreatic", "colon", "intestinal", "bowel", "biliary", "esophageal", "oesophageal", "inflammatory bowel"]},
    "general-surgery": {"label": "General Surgery", "terms": ["general surgery", "acute abdomen", "appendicitis", "hernia", "bowel obstruction", "peritonitis", "cholecystitis", "laparotomy", "postoperative complication", "surgical abdomen"]},
    "hematology": {"label": "Hematology", "terms": ["anemia", "anaemia", "thrombocytopenia", "coagulopathy", "hemophilia", "haemophilia", "sickle cell", "thalassemia", "leukemia", "leukaemia", "lymphoma", "myeloma", "hematologic"]},
    "infectious-disease": {"label": "Infectious Diseases", "terms": ["infection", "infectious", "sepsis", "septic", "abscess", "tuberculosis", "viral", "bacterial", "fungal", "parasitic", "meningitis", "endocarditis", "osteomyelitis"]},
    "nephrology": {"label": "Nephrology", "terms": ["renal", "kidney", "nephrology", "glomerulonephritis", "nephrotic", "acute kidney injury", "chronic kidney disease", "dialysis", "electrolyte"]},
    "neurology": {"label": "Neurology", "terms": ["neurology", "neurologic", "neurological", "cerebral", "brain", "stroke", "seizure", "epilepsy", "encephalitis", "meningitis", "myelitis", "neuropathy", "movement disorder"]},
    "neurosurgery": {"label": "Neurosurgery", "terms": ["neurosurgery", "intracranial", "brain tumor", "brain tumour", "spinal cord", "spine surgery", "subarachnoid hemorrhage", "subarachnoid haemorrhage", "hydrocephalus", "cerebral aneurysm", "epidural hematoma"]},
    "obstetrics-gynecology": {"label": "Obstetrics & Gynecology", "terms": ["pregnancy", "pregnant", "obstetric", "gynecology", "gynaecology", "ovarian", "uterine", "cervical", "placenta", "preeclampsia", "endometriosis", "labor", "labour"]},
    "oncology": {"label": "Oncology", "terms": ["tumor", "tumour", "cancer", "carcinoma", "lymphoma", "leukemia", "leukaemia", "sarcoma", "melanoma", "metastasis", "metastatic", "malignancy", "neoplasm"]},
    "ophthalmology": {"label": "Ophthalmology", "terms": ["ophthalmology", "ophthalmic", "ocular", "eye", "retina", "retinal", "retinopathy", "retinitis", "retinoblastoma", "cornea", "corneal", "glaucoma", "uveitis", "optic nerve", "optic disc", "macular", "cataract", "conjunctiva", "vitreous", "strabismus", "keratitis", "visual loss", "vision loss"]},
    "orthopedics": {"label": "Orthopedics", "terms": ["orthopedic", "orthopaedic", "bone", "fracture", "joint", "arthroplasty", "osteomyelitis", "osteosarcoma", "spine", "ligament", "tendon", "musculoskeletal"]},
    "otolaryngology": {"label": "Otolaryngology / ENT", "terms": ["otolaryngology", "otorhinolaryngology", "ear", "nose", "throat", "laryngeal", "sinus", "tonsil", "hearing", "vestibular", "middle ear", "head and neck"]},
    "pediatrics": {"label": "Pediatrics", "terms": ["pediatric", "paediatric", "neonate", "neonatal", "infant", "childhood", "child", "congenital", "birth"]},
    "psychiatry": {"label": "Psychiatry", "terms": ["psychiatry", "psychiatric", "psychosis", "schizophrenia", "bipolar", "depression", "mania", "anxiety disorder", "eating disorder", "catatonia"]},
    "pulmonology": {"label": "Pulmonology", "terms": ["pulmonary", "lung", "pneumonia", "pleural", "bronchial", "bronchiectasis", "asthma", "copd", "pneumothorax", "respiratory failure", "interstitial lung"]},
    "radiology": {"label": "Radiology", "terms": ["radiology", "radiologic", "radiological", "computed tomography", "magnetic resonance imaging", "ultrasound", "radiograph", "imaging finding", "angiography", "nuclear medicine"]},
    "rheumatology": {"label": "Rheumatology", "terms": ["rheumatology", "rheumatoid", "lupus", "vasculitis", "autoimmune", "scleroderma", "sjogren", "myositis", "ankylosing spondylitis", "inflammatory arthritis"]},
    "urology": {"label": "Urology", "terms": ["urology", "urologic", "urological", "urinary", "bladder", "prostate", "renal", "kidney", "ureter", "urethra", "testicular", "penile", "hydronephrosis", "hematuria", "haematuria"]},
    "vascular-surgery": {"label": "Vascular Surgery", "terms": ["vascular surgery", "arterial", "aneurysm", "peripheral arterial", "ischemia", "ischaemia", "thrombosis", "embolism", "varicose", "venous ulcer", "aortic aneurysm", "vascular malformation"]},
}

ALIASES = {
    "ent": "otolaryngology", "obgyn": "obstetrics-gynecology", "ob-gyn": "obstetrics-gynecology",
    "id": "infectious-disease", "infectious": "infectious-disease", "gastro": "gastroenterology",
    "endo": "endocrinology", "ortho": "orthopedics", "nephro": "nephrology",
    "uro": "urology", "ophthalmic": "ophthalmology", "vascular": "vascular-surgery",
    "ct-surgery": "cardiothoracic-surgery", "peds": "pediatrics",
}

CASE_REPORT_QUERY = '(PUB_TYPE:"Case Reports" OR TITLE:"case report" OR TITLE:"case reports")'
NONCLINICAL_RE = re.compile(r"\b(?:animal model|rat model|mouse model|rabbit model|in vitro|ex vivo|cadaveric study|cell line)\b", re.I)
CASE_SECTION_RE = re.compile(r"case presentation|case report|case description|clinical course|patient information|clinical findings", re.I)
NOVELTY_RE = re.compile(r"\b(?:rare|unusual|novel|first reported|first case|unexpected|misdiagnos\w*|diagnostic challenge|masquerad\w*)\b", re.I)
INVESTIGATION_RE = re.compile(r"\b(?:computed tomography|\bCT\b|magnetic resonance|\bMRI\b|ultrasound|sonograph|biopsy|histopatholog|patholog\w*|culture|echocardiograph|endoscop|angiograph|radiograph|\bPET\b|electrocardiogram|\bECG\b|\bEEG\b)\b", re.I)
MANAGEMENT_RE = re.compile(r"\b(?:treated|treatment|managed|management|underwent|surgery|surgical|resection|repair|drainage|antibiotic|chemotherapy|radiotherapy|immunotherapy|intervention)\b", re.I)
FOLLOWUP_RE = re.compile(r"\b(?:follow[- ]?up|months? later|years? later|at \d+[- ]month|recovered|improved|resolved|discharged|recurrence|remission|outcome)\b", re.I)
TEACHING_RE = re.compile(r"\b(?:learning point|take[- ]home|clinical pearl|key message|important to consider|should be considered|high index of suspicion)\b", re.I)


class HarvestError(RuntimeError):
    """Expected API/download failure with structured retry metadata."""
    def __init__(self, message: str, *, endpoint: str = "", status_code: int | None = None,
                 retryable: bool = False, attempt: int | None = None):
        super().__init__(message)
        self.endpoint = endpoint
        self.status_code = status_code
        self.retryable = retryable
        self.attempt = attempt


@dataclass
class Candidate:
    pmcid: str
    title: str = ""
    doi: str = ""
    year: str = ""
    journal: str = ""
    cited_by_count: int = 0
    source: str = "Europe PMC"


class RateLimiter:
    def __init__(self, requests_per_second: float = 3.0) -> None:
        self.interval = 1.0 / max(0.1, requests_per_second)
        self.next_allowed: dict[str, float] = {}

    def wait(self, url: str = EPMC) -> None:
        host = urllib.parse.urlparse(url).hostname or "default"
        now = time.monotonic()
        due = self.next_allowed.get(host, now)
        if due > now:
            time.sleep(due - now)
        self.next_allowed[host] = max(now, due) + self.interval


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def element_text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return re.sub(r"\s+", " ", " ".join(t for t in element.itertext() if t)).strip()


def first_text(root: ET.Element, name: str) -> str:
    node = next((e for e in root.iter() if local_name(e.tag) == name), None)
    return element_text(node)


def normalize_specialty(value: str) -> str:
    key = value.strip().lower().replace("_", "-").replace(" ", "-")
    key = ALIASES.get(key, key)
    if key not in SPECIALTIES:
        valid = ", ".join(sorted(SPECIALTIES))
        raise argparse.ArgumentTypeError(f"Unknown specialty '{value}'. Valid keys: {valid}, all")
    return key


def terms_query(terms: Iterable[str]) -> str:
    def quoted(term: str) -> str:
        safe = term.replace('\\', ' ').replace('"', ' ')
        return f'TITLE_ABS:"{safe}"'
    return "(" + " OR ".join(quoted(t) for t in terms) + ")"


def build_query(specialty: str, year_from: int | None = None, year_to: int | None = None) -> str:
    if specialty not in SPECIALTIES:
        raise ValueError(f"Unsupported specialty: {specialty}")
    query = f"{CASE_REPORT_QUERY} AND {terms_query(SPECIALTIES[specialty]['terms'])} AND OPEN_ACCESS:Y AND HAS_FT:Y"
    if year_from is not None or year_to is not None:
        low = year_from if year_from is not None else 1900
        high = year_to if year_to is not None else dt.date.today().year
        if low > high:
            raise ValueError("year_from must be less than or equal to year_to")
        query += f" AND PUB_YEAR:[{low} TO {high}]"
    return query


def http_get(url: str, *, timeout: int = 45, retries: int = 4, accept: str = "application/json", user_agent: str = DEFAULT_UA, max_bytes: int = 25_000_000) -> bytes:
    headers = {"User-Agent": user_agent, "Accept": accept}
    last_error = "unknown network error"
    last_status: int | None = None
    last_retryable = False
    for attempt in range(retries):
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read(max_bytes + 1)
                if len(raw) > max_bytes:
                    raise HarvestError(f"Response exceeds configured {max_bytes}-byte limit: {url}",
                                       endpoint=url, retryable=False, attempt=attempt + 1)
                return raw
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}"
            last_status = exc.code
            last_retryable = exc.code not in {400, 401, 403, 404, 410}
            if not last_retryable:
                raise HarvestError(f"{last_error} for {url}", endpoint=url, status_code=exc.code,
                                   retryable=False, attempt=attempt + 1) from exc
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            delay = retry_delay(retry_after, attempt, cap=60.0)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            last_status = None
            last_retryable = True
            delay = retry_delay(None, attempt, cap=30.0)
        if attempt < retries - 1:
            time.sleep(delay)
    raise HarvestError(f"Request failed after {retries} attempts ({last_error}): {url}", endpoint=url,
                       status_code=last_status, retryable=last_retryable, attempt=retries)


def _search_page(query: str, cursor: str, page_size: int, limiter: RateLimiter, user_agent: str,
                 sort_mode: str = "citations") -> dict[str, Any]:
    params_dict = {
        "query": query,
        "format": "json",
        "resultType": "lite",
        "pageSize": page_size,
        "cursorMark": cursor,
    }
    sort_value = {"citations": "CITED desc", "newest": "P_PDATE_D desc"}.get(sort_mode)
    if sort_value:
        params_dict["sort"] = sort_value
    params = urllib.parse.urlencode(params_dict)
    limiter.wait()
    raw = http_get(f"{EPMC}/search?{params}", user_agent=user_agent)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HarvestError("Europe PMC returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise HarvestError("Europe PMC returned an unexpected response")
    if data.get("errCode") or data.get("error"):
        detail = data.get("errMsg") or data.get("error") or data.get("errCode")
        raise HarvestError(f"Europe PMC search error: {detail}")
    return data


def search_candidates(specialty: str, *, limit: int, page_size: int, max_pages: int,
                      year_from: int | None, year_to: int | None,
                      limiter: RateLimiter, user_agent: str,
                      sort_mode: str = "citations") -> list[Candidate]:
    query = build_query(specialty, year_from, year_to)
    cursor = "*"
    seen: set[str] = set()
    candidates: list[Candidate] = []
    for _page in range(max_pages):
        data = _search_page(query, cursor, page_size, limiter, user_agent, sort_mode)
        results = data.get("resultList", {}).get("result", [])
        if not isinstance(results, list) or not results:
            break
        for row in results:
            pmcid = str(row.get("pmcid") or "").strip().upper()
            if not re.fullmatch(r"PMC\d+", pmcid) or pmcid in seen:
                continue
            seen.add(pmcid)
            try:
                cited = max(0, int(row.get("citedByCount") or 0))
            except (TypeError, ValueError):
                cited = 0
            candidates.append(Candidate(
                pmcid=pmcid,
                title=str(row.get("title") or "").strip(),
                doi=str(row.get("doi") or "").strip().lower(),
                year=str(row.get("pubYear") or "").strip(),
                journal=str(row.get("journalTitle") or "").strip(),
                cited_by_count=cited,
            ))
            if limit > 0 and len(candidates) >= limit:
                return candidates
        next_cursor = data.get("nextCursorMark")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = str(next_cursor)
    return candidates


def parse_article_metadata(xml_bytes: bytes) -> dict[str, str]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise HarvestError(f"Downloaded content is not valid XML: {exc}") from exc
    if local_name(root.tag) != "article":
        raise HarvestError("Downloaded XML is not a JATS article")
    meta: dict[str, str] = {"title": "", "doi": "", "year": "", "journal": "", "license": ""}
    for node in root.iter():
        name = local_name(node.tag)
        if name == "article-title" and not meta["title"]:
            meta["title"] = element_text(node)
        elif name == "article-id" and node.attrib.get("pub-id-type", "").lower() == "doi" and not meta["doi"]:
            meta["doi"] = element_text(node).lower()
        elif name == "journal-title" and not meta["journal"]:
            meta["journal"] = element_text(node)
        elif name == "year" and not meta["year"]:
            year = element_text(node)
            if re.fullmatch(r"\d{4}", year):
                meta["year"] = year
        elif name == "license" and not meta["license"]:
            href = node.attrib.get("{http://www.w3.org/1999/xlink}href", "")
            meta["license"] = href or element_text(node)
    return meta


def get_article_text(xml_bytes: bytes) -> tuple[str, str, list[str]]:
    root = ET.fromstring(xml_bytes)
    abstract_parts: list[str] = []
    body_parts: list[str] = []
    headings: list[str] = []

    def walk_section(sec: ET.Element) -> None:
        heading = ""
        for child in list(sec):
            if local_name(child.tag) == "title":
                heading = element_text(child)
                break
        if heading:
            headings.append(heading)
        for child in list(sec):
            kind = local_name(child.tag)
            if kind == "sec":
                continue
            if kind in {"p", "list", "disp-quote", "boxed-text", "table-wrap", "fig", "supplementary-material"}:
                txt = element_text(child)
                if txt:
                    body_parts.append(txt)
        for child in list(sec):
            if local_name(child.tag) == "sec":
                walk_section(child)

    for node in root.iter():
        if local_name(node.tag) == "abstract":
            text = element_text(node)
            if text:
                abstract_parts.append(text)
    body = next((e for e in root.iter() if local_name(e.tag) == "body"), None)
    if body is not None:
        for child in list(body):
            if local_name(child.tag) == "sec":
                walk_section(child)
            elif local_name(child.tag) in {"p", "fig", "table-wrap"}:
                txt = element_text(child)
                if txt:
                    body_parts.append(txt)
    return " ".join(abstract_parts), " ".join(body_parts), headings


def specialty_relevance(specialty: str, title: str, abstract: str, body: str) -> float:
    terms = SPECIALTIES[specialty]["terms"]
    score = 0.0
    for term in terms:
        pattern = re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)", re.I)
        if pattern.search(title):
            score += 3.0
        elif pattern.search(abstract):
            score += 2.0
        elif pattern.search(body):
            score += 1.0
    return score


def educational_score(title: str, abstract: str, body: str, cited_by_count: int) -> tuple[int, list[str]]:
    text = " ".join((title, abstract, body))
    score = 0
    reasons: list[str] = []
    checks = [
        (NOVELTY_RE, 12, "unusual / diagnostic-pitfall feature"),
        (INVESTIGATION_RE, 15, "named diagnostic investigation"),
        (MANAGEMENT_RE, 14, "reported management / intervention"),
        (FOLLOWUP_RE, 10, "outcome or follow-up"),
        (TEACHING_RE, 12, "explicit teaching point"),
        (re.compile(r"\b(?:histopatholog\w*|biopsy|molecular test|genetic test|culture result|pathognomonic)\b", re.I), 10, "diagnostic confirmation clue"),
        (re.compile(r"\b(?:differential diagnosis|misdiagnosed|mimicking|masquerading|difficult to diagnose)\b", re.I), 8, "diagnostic reasoning / differential"),
    ]
    for pattern, points, label in checks:
        if pattern.search(text):
            score += points
            reasons.append(label)
    if CASE_SECTION_RE.search(body):
        score += 8
        reasons.append("dedicated clinical case section")
    if len(re.findall(r"\b\w+\b", body)) >= 500:
        score += 6
        reasons.append("substantial full-text clinical detail")
    if cited_by_count > 0:
        citation_points = min(10, int(2 * math.log1p(cited_by_count)))
        score += citation_points
        if citation_points >= 3:
            reasons.append("citation signal")
    return min(100, score), reasons


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_bytes(content)
    os.replace(temp, path)


def write_json(path: Path, value: Any) -> None:
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    atomic_write(path, payload)


def log_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def fetch_core_metadata(pmcid: str, *, limiter: RateLimiter, user_agent: str) -> dict[str, Any]:
    """Best-effort Europe PMC core metadata lookup, including MeSH headings."""
    params = urllib.parse.urlencode({
        "query": f"EXT_ID:{pmcid}", "format": "json", "resultType": "core", "pageSize": 1,
    })
    limiter.wait()
    raw = http_get(f"{EPMC}/search?{params}", user_agent=user_agent)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    results = payload.get("resultList", {}).get("result", []) if isinstance(payload, dict) else []
    if not isinstance(results, list) or not results:
        return {}
    row = results[0]
    mesh: list[str] = []
    mesh_list = row.get("meshHeadingList", {}) if isinstance(row, dict) else {}
    headings = mesh_list.get("meshHeading", []) if isinstance(mesh_list, dict) else []
    if isinstance(headings, list):
        for item in headings:
            if not isinstance(item, dict):
                continue
            descriptor = item.get("descriptorName")
            if isinstance(descriptor, dict):
                descriptor = descriptor.get("$") or descriptor.get("name") or descriptor.get("value")
            if descriptor:
                mesh.append(str(descriptor).strip())
    return {"mesh_terms": list(dict.fromkeys(x for x in mesh if x)),
            "core_metadata_available": True}


def mesh_relevance(specialty: str, mesh_terms: Iterable[str]) -> float:
    """Conservative MeSH descriptor overlap score; not a specialty classifier."""
    terms = [str(x).lower() for x in mesh_terms]
    specialty_terms = [str(x).lower() for x in SPECIALTIES[specialty]["terms"]]
    score = 0.0
    for descriptor in terms:
        if any(term in descriptor or descriptor in term for term in specialty_terms if len(term) >= 4):
            score += 2.0
    return min(score, 12.0)


def harvest_one(candidate: Candidate, specialty: str, output: Path, *, limiter: RateLimiter,
                user_agent: str, min_words: int, min_relevance: float, refresh: bool) -> dict[str, Any] | None:
    folder = output / specialty / candidate.pmcid
    xml_path = folder / "article.xml"
    meta_path = folder / "meta.json"
    if xml_path.exists() and meta_path.exists() and not refresh:
        try:
            existing = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                abstract, body, headings = get_article_text(xml_path.read_bytes())
                words = len(re.findall(r"\b\w+\b", body))
                mesh_terms = existing.get("mesh_terms", [])
                if not mesh_terms:
                    try:
                        mesh_terms = fetch_core_metadata(candidate.pmcid, limiter=limiter, user_agent=user_agent).get("mesh_terms", [])
                    except HarvestError:
                        mesh_terms = []
                relevance = specialty_relevance(specialty, str(existing.get("title", "")), abstract, body)
                relevance += mesh_relevance(specialty, mesh_terms)
                existing["mesh_terms"] = mesh_terms
                existing["parser_version"] = VERSION
                existing.setdefault("content_sha256", hashlib.sha256(xml_path.read_bytes()).hexdigest())
                if words < min_words or relevance < min_relevance:
                    return None
                if not (folder / "article.txt").exists():
                    lines = [str(existing.get("title", candidate.title)), str(existing.get("journal", candidate.journal)),
                             str(existing.get("source_url", "")), "", "ABSTRACT", abstract, "", "FULL TEXT", body]
                    atomic_write(folder / "article.txt", ("\n".join(lines) + "\n").encode("utf-8"))
                existing["word_count_body"] = words
                existing["specialty_relevance"] = round(relevance, 2)
                existing["filter_checked_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
                write_json(meta_path, existing)
                return existing
        except (OSError, json.JSONDecodeError, ET.ParseError, ValueError):
            pass

    url = f"{EPMC}/{candidate.pmcid}/fullTextXML"
    limiter.wait()
    xml_bytes = http_get(url, accept="application/xml", user_agent=user_agent)
    parsed_meta = parse_article_metadata(xml_bytes)
    abstract, body, headings = get_article_text(xml_bytes)
    # Core metadata is best-effort: full-text harvesting must still work if this
    # supplementary metadata endpoint is unavailable or returns an unfamiliar shape.
    try:
        core_meta = fetch_core_metadata(candidate.pmcid, limiter=limiter, user_agent=user_agent)
    except HarvestError:
        core_meta = {}
    title = parsed_meta.get("title") or candidate.title
    doi = parsed_meta.get("doi") or candidate.doi
    year = parsed_meta.get("year") or candidate.year
    journal = parsed_meta.get("journal") or candidate.journal
    combined = " ".join((title, abstract, body))
    if NONCLINICAL_RE.search(" ".join((title, abstract[:1500]))):
        return None
    words = len(re.findall(r"\b\w+\b", body))
    if words < min_words:
        return None
    text_relevance = specialty_relevance(specialty, title, abstract, body)
    mesh_terms = core_meta.get("mesh_terms", [])
    mesh_score = mesh_relevance(specialty, mesh_terms)
    relevance = text_relevance + mesh_score
    if relevance < min_relevance:
        return None
    score, reasons = educational_score(title, abstract, " ".join(headings) + " " + body, candidate.cited_by_count)
    source_url = f"https://europepmc.org/articles/{candidate.pmcid}"
    metadata = {
        "schema_version": 5,
        "harvester_version": VERSION,
        "parser_version": VERSION,
        "content_sha256": hashlib.sha256(xml_bytes).hexdigest(),
        "mesh_terms": mesh_terms,
        "mesh_relevance": round(mesh_score, 2),
        "text_relevance": round(text_relevance, 2),
        "id": candidate.pmcid,
        "pmcid": candidate.pmcid,
        "title": title,
        "doi": doi,
        "year": year,
        "journal": journal,
        "cited_by_count": candidate.cited_by_count,
        "specialty": specialty,
        "specialty_label": SPECIALTIES[specialty]["label"],
        "specialty_relevance": round(relevance, 2),
        "educational_score": score,
        "educational_score_reasons": reasons,
        "word_count_body": words,
        "section_headings": headings,
        "abstract": abstract,
        "source": "Europe PMC",
        "source_url": source_url,
        "doi_url": f"https://doi.org/{doi}" if doi else "",
        "license": parsed_meta.get("license", ""),
        "downloaded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "discovered_via": f"europepmc:{specialty}",
        "status": "downloaded",
    }
    atomic_write(xml_path, xml_bytes)
    write_json(meta_path, metadata)
    # Plain-text version supports quick reading without launching Anki.
    text_lines = [title, f"{journal} ({year})".strip(), source_url]
    if doi:
        text_lines.append(f"DOI: https://doi.org/{doi}")
    if abstract:
        text_lines.extend(["", "ABSTRACT", abstract])
    if body:
        text_lines.extend(["", "FULL TEXT", body])
    atomic_write(folder / "article.txt", ("\n".join(text_lines) + "\n").encode("utf-8"))
    return metadata


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Harvest high-yield, open-access clinical case reports by medical specialty."
    )
    parser.add_argument("--specialty", nargs="+", required=True,
                        help="One or more specialty keys, or 'all'. Example: --specialty urology cardiology")
    parser.add_argument("--output", type=Path, default=Path("case_library"), help="Output folder (default: case_library)")
    parser.add_argument("--limit", type=int, default=40, help="Legacy maximum candidates per specialty; 0 means no limit")
    parser.add_argument("--target-saved", type=int, default=0, help="Stop after this many retained articles across all specialties; 0 disables")
    parser.add_argument("--max-candidates", type=int, default=1000, help="Hard total candidate budget across specialties")
    parser.add_argument("--state-db", type=Path, help="Persistent resumable harvest state SQLite path")
    parser.add_argument("--verify-cache", action="store_true", help="Verify manifest, metadata, required files and XML hashes after harvest")
    parser.add_argument("--year-from", type=int, default=None)
    parser.add_argument("--year-to", type=int, default=None)
    parser.add_argument("--page-size", type=int, default=100, help="Europe PMC page size (1–1000)")
    parser.add_argument("--sort", choices=("citations", "newest", "relevance"), default="citations",
                        help="Search order: most cited, newest first, or Europe PMC relevance")
    parser.add_argument("--max-pages", type=int, default=30)
    parser.add_argument("--min-words", type=int, default=100, help="Minimum full-text body word count")
    parser.add_argument("--min-relevance", type=float, default=1.0, help="Minimum specialty relevance score")
    parser.add_argument("--min-educational-score", type=int, default=0,
                        help="Optional educational-score cutoff (0–100); default keeps all relevant reports")
    parser.add_argument("--requests-per-second", type=float, default=2.5)
    parser.add_argument("--delay", type=float, default=0.0, help="Extra pause between article downloads")
    parser.add_argument("--email", default=os.environ.get("CASE_HARVEST_EMAIL", ""),
                        help="Optional contact email included in User-Agent; can also use CASE_HARVEST_EMAIL")
    parser.add_argument("--refresh", action="store_true", help="Re-download records already present")
    parser.add_argument("--check-status", action="store_true", help="Check Europe PMC article updates/retractions; failures remain unknown")
    parser.add_argument("--status-cache", type=Path, help="Status cache JSON path (default: OUTPUT/.article_status_cache.json)")
    parser.add_argument("--status-ttl-hours", type=float, default=24.0, help="Article-status cache lifetime (default: 24 hours)")
    parser.add_argument("--force-status-refresh", action="store_true", help="Ignore cached article-status results")
    parser.add_argument("--keep-below-cutoff", action="store_true", help="Save records below min educational score")
    args = parser.parse_args(argv)
    if not 1 <= args.page_size <= 1000:
        parser.error("--page-size must be between 1 and 1000")
    if args.limit < 0 or args.max_pages < 1 or args.min_words < 0 or args.target_saved < 0 or args.max_candidates < 1:
        parser.error("--limit, --target-saved and --min-words must be non-negative; --max-pages and --max-candidates must be > 0")
    if not 0 <= args.min_educational_score <= 100:
        parser.error("--min-educational-score must be between 0 and 100")
    if args.year_from is not None and not 1900 <= args.year_from <= dt.date.today().year:
        parser.error("--year-from must be a valid year")
    if args.year_to is not None and not 1900 <= args.year_to <= dt.date.today().year:
        parser.error("--year-to must be a valid year")
    if args.year_from is not None and args.year_to is not None and args.year_from > args.year_to:
        parser.error("--year-from cannot be later than --year-to")
    if args.requests_per_second <= 0 or args.delay < 0 or args.min_relevance < 0:
        parser.error("Rate, delay and relevance must be non-negative; rate must be > 0")
    if args.status_ttl_hours < 0:
        parser.error("--status-ttl-hours must be non-negative")
    if args.specialty == ["all"]:
        args.specialty = sorted(SPECIALTIES)
    else:
        if "all" in args.specialty:
            parser.error("Use --specialty all by itself, not with other specialty keys")
        try:
            args.specialty = list(dict.fromkeys(normalize_specialty(s) for s in args.specialty))
        except argparse.ArgumentTypeError as exc:
            parser.error(str(exc))
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    clean_email = args.email.replace("\r", "").replace("\n", "").strip()
    contact = f"; contact: {clean_email}" if clean_email else ""
    user_agent = DEFAULT_UA + contact
    limiter = RateLimiter(args.requests_per_second)
    all_records: dict[tuple[str, str], dict[str, Any]] = {}
    errors_path = args.output / "harvest_errors.jsonl"
    state_conn = init_harvest_state(args.state_db or (args.output / ".harvest_state.sqlite3"))
    total_candidates = 0
    total_saved = 0

    for specialty in args.specialty:
        if args.target_saved and total_saved >= args.target_saved:
            break
        print(f"\n[{SPECIALTIES[specialty]['label']}] Searching Europe PMC…")
        try:
            candidates = search_candidates(
                specialty, limit=args.limit, page_size=args.page_size, max_pages=args.max_pages,
                year_from=args.year_from, year_to=args.year_to, limiter=limiter, user_agent=user_agent,
                sort_mode=args.sort,
            )
            print(f"  Found {len(candidates)} full-text PMC candidates")
        except HarvestError as exc:
            print(f"  SEARCH ERROR: {exc}", file=sys.stderr)
            log_jsonl(errors_path, {"specialty": specialty, "stage": "search", "error": str(exc)})
            continue

        saved = 0
        for index, candidate in enumerate(candidates, 1):
            if total_candidates >= args.max_candidates:
                print(f"  Candidate budget reached ({args.max_candidates}); stopping safely")
                break
            if args.target_saved and total_saved >= args.target_saved:
                break
            previous = state_conn.execute("SELECT state,retryable FROM harvest_jobs WHERE candidate_id=?", (candidate.pmcid,)).fetchone()
            if previous and previous["state"] == "failed" and not previous["retryable"]:
                print(f"  SKIP {candidate.pmcid}: previous failure is marked non-retryable")
                continue
            total_candidates += 1
            save_checkpoint(state_conn, candidate.pmcid, specialty, "discovered")
            try:
                meta = harvest_one(
                    candidate, specialty, args.output, limiter=limiter, user_agent=user_agent,
                    min_words=args.min_words, min_relevance=args.min_relevance, refresh=args.refresh,
                )
                if meta is None:
                    save_checkpoint(state_conn, candidate.pmcid, specialty, "rejected", error="Failed content/relevance filters")
                    continue
                article_dir = args.output / specialty / candidate.pmcid
                xml_path = article_dir / "article.xml"
                source_hash = hashlib.sha256(xml_path.read_bytes()).hexdigest() if xml_path.exists() else ""
                save_checkpoint(state_conn, candidate.pmcid, specialty, "downloaded", source_path=str(xml_path), source_sha256=source_hash)
                save_checkpoint(state_conn, candidate.pmcid, specialty, "parsed", source_path=str(xml_path), source_sha256=source_hash)
                if args.check_status:
                    status_cache = args.status_cache or (args.output / ".article_status_cache.json")
                    status_result = check_article_status(
                        pmcid=candidate.pmcid, doi=str(meta.get("doi") or candidate.doi or ""),
                        cache_path=status_cache, ttl_hours=args.status_ttl_hours,
                        force=args.force_status_refresh,
                        request_fn=lambda ids: (limiter.wait(), post_status_request(ids))[1],
                        crossref_fn=lambda doi: (limiter.wait("https://api.crossref.org"), fetch_crossref_updates(doi))[1],
                    )
                    meta["article_status"] = status_result
                    write_json(args.output / specialty / candidate.pmcid / "meta.json", meta)
                    if status_result.get("status") == "retracted":
                        article_dir = args.output / specialty / candidate.pmcid
                        for name in ("article.xml", "meta.json", "article.txt"):
                            try:
                                (article_dir / name).unlink()
                            except FileNotFoundError:
                                pass
                        log_jsonl(errors_path, {"specialty": specialty, "pmcid": candidate.pmcid,
                                                "stage": "status-screen", "status": "retracted",
                                                "warning": "Excluded from active library by default"})
                        print(f"  EXCLUDED {candidate.pmcid}: confirmed retraction status")
                        save_checkpoint(state_conn, candidate.pmcid, specialty, "rejected", error="Confirmed retraction")
                        continue
                if meta.get("educational_score", 0) < args.min_educational_score and not args.keep_below_cutoff:
                    # Keep a compact rejection record; do not leave a full article
                    # in the active study library below the requested threshold.
                    article_dir = args.output / specialty / candidate.pmcid
                    for name in ("article.xml", "meta.json", "article.txt"):
                        try:
                            (article_dir / name).unlink()
                        except FileNotFoundError:
                            pass
                    save_checkpoint(state_conn, candidate.pmcid, specialty, "rejected", error="Below educational score cutoff")
                    continue
                saved += 1
                total_saved += 1
                save_checkpoint(state_conn, candidate.pmcid, specialty, "retained", source_path=str(xml_path), source_sha256=source_hash)
                all_records[(specialty, candidate.pmcid)] = meta
                print(f"  [{index}/{len(candidates)}] {candidate.pmcid} | score {meta.get('educational_score', 0):>2}/100 | {meta.get('title', '')[:90]}")
            except (HarvestError, OSError, ET.ParseError, ValueError) as exc:
                print(f"  SKIP {candidate.pmcid}: {exc}", file=sys.stderr)
                retryable = bool(getattr(exc, "retryable", isinstance(exc, OSError)))
                log_jsonl(errors_path, {"specialty": specialty, "pmcid": candidate.pmcid, "stage": "download",
                                        "endpoint": getattr(exc, "endpoint", ""), "status_code": getattr(exc, "status_code", None),
                                        "error": str(exc), "retryable": retryable,
                                        "attempt": getattr(exc, "attempt", None), "timestamp": dt.datetime.now(dt.timezone.utc).isoformat()})
                save_checkpoint(state_conn, candidate.pmcid, specialty, "failed", error=str(exc), retryable=retryable)
            if args.delay:
                time.sleep(args.delay)
        print(f"  Saved {saved} usable case reports")

    # Merge this run with the existing manifest so resuming does not erase older cases.
    manifest_path = args.output / "manifest.jsonl"
    existing: dict[tuple[str, str], dict[str, Any]] = {}
    if manifest_path.exists():
        for line in manifest_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                existing[(str(row.get("specialty", "")), str(row.get("pmcid", row.get("id", ""))))] = row
            except json.JSONDecodeError:
                continue
    existing.update(all_records)
    existing = {
        key: row for key, row in existing.items()
        if (args.output / str(row.get("specialty", "")) / str(row.get("pmcid", row.get("id", ""))) / "article.xml").exists()
    }
    ordered = sorted(existing.values(), key=lambda r: (-int(r.get("educational_score", 0)), str(r.get("specialty", "")), str(r.get("title", ""))))
    atomic_write(manifest_path, ("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in ordered)).encode("utf-8"))
    print(f"\nManifest: {manifest_path} ({len(ordered)} records total)")
    state_conn.close()
    if args.verify_cache:
        report = verify_cache(args.output)
        report_path = args.output / "cache_verification.json"
        write_json(report_path, report)
        print(f"Cache verification: {'PASS' if report['ok'] else 'FAIL'} ({len(report['issues'])} issues) — {report_path}")
        if not report["ok"]:
            return 2
    print(f"Finished. Candidates processed: {total_candidates}; retained this run: {total_saved}.")
    print("Use cases2anki.py on the output folder to build study cards.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
