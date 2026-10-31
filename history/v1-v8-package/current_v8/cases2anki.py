#!/usr/bin/env python3
"""Convert harvested JATS case reports into exam-style Anki flashcards.

Cards are extractive: case facts, investigations and management come from the
article text. No clinical facts are invented. Every note includes its source
and a reading panel with the extracted article sections.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

VERSION = "8.0.0"
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"

SECTION_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("presentation", re.compile(r"case presentation|case report|case description|clinical presentation|patient presentation", re.I)),
    ("history", re.compile(r"history|medical background|patient information|past medical|family history", re.I)),
    ("examination", re.compile(r"physical exam|clinical exam|examination|physical findings|clinical findings", re.I)),
    ("investigations", re.compile(r"investigation|diagnostic work.?up|work.?up|laborator|imaging|radiolog|patholog|histolog|diagnostic assessment|results", re.I)),
    ("diagnosis", re.compile(r"diagnos|impression|histopatholog|pathological findings|final assessment", re.I)),
    ("management", re.compile(r"treatment|management|therap|intervention|surgical technique|procedure", re.I)),
    ("outcome", re.compile(r"outcome|follow.?up|clinical course|prognos|postoperative course", re.I)),
    ("discussion", re.compile(r"discussion|comment|learning|clinical significance|teaching", re.I)),
    ("conclusion", re.compile(r"conclusion|summary|take.?home|final remarks|key message", re.I)),
)

DX_PATTERNS = [
    (re.compile(r"\b(?:final|definitive|working|clinical|pathological|primary|confirmed)?\s*diagnos(?:is|es)\s*(?:of|was|is|were|:)?\s+(?:an?\s+|the\s+)?(?P<dx>[A-Za-z][^.;:\n]{2,150})", re.I), 0.90, "explicit diagnosis statement"),
    (re.compile(r"\b(?:was|were|has been|had been)\s+diagnosed\s+with\s+(?:an?\s+|the\s+)?(?P<dx>[A-Za-z][^.;:\n]{2,150})", re.I), 0.86, "diagnosed with"),
    (re.compile(r"\b(?:biopsy|histopathology|histology|pathological examination)\s+(?:showed|revealed|confirmed|demonstrated)\s+(?P<dx>[A-Za-z][^.;:\n]{2,150})", re.I), 0.88, "pathological confirmation"),
    (re.compile(r"\b(?:was|were)\s+consistent\s+with\s+(?P<dx>[A-Za-z][^.;:\n]{2,150})", re.I), 0.75, "reported as consistent with"),
]

STOP_CLAUSE = re.compile(r"\s+(?:which|who|that|resulting in|leading to|and was treated|and were treated|for which|after which|because|therefore)\b", re.I)
FILLER_DX = {
    "patient", "case", "report", "presentation", "diagnosis", "symptom", "symptoms", "finding", "findings",
    "disease", "condition", "disorder", "syndrome", "was", "were", "the", "and", "with", "without",
}

INVESTIGATION_TERMS = re.compile(
    r"\b(?:computed tomography|\bCT\b|magnetic resonance imaging|\bMRI\b|ultrasound|sonograph|biopsy|histopatholog\w*|culture|echocardiograph|endoscop\w*|angiograph\w*|radiograph\w*|\bPET\b|electrocardiogram|\bECG\b|\bEEG\b|urinalysis|blood test|laboratory test|Doppler|angiogram)\b",
    re.I,
)
DIFFERENTIAL_TERMS = re.compile(
    r"\b(?:differential diagnosis|differential included|mimic\w*|masquerad\w*|distinguish\w* from|differentiat\w* from|confused with|ruled out|excluded)\b",
    re.I,
)
TEACHING_TERMS = re.compile(
    r"\b(?:learning point|take[- ]home|clinical pearl|key message|important to consider|should be considered|high index of suspicion|clinicians should|this case highlights)\b",
    re.I,
)
REASONING_TERMS = re.compile(
    r"\b(?:because|therefore|thus|suggest(?:s|ed)?|indicat(?:es|ed)?|consistent with|supports the diagnosis|diagnostic clue|diagnostic consideration|should raise suspicion|may explain)\b",
    re.I,
)

CARD_CSS = """
.card { font-family: Arial, sans-serif; font-size: 17px; line-height: 1.6; color: #172033; background: #fff; padding: 22px; max-width: 900px; margin: auto; text-align: left; }
.question { font-size: 1.08em; font-weight: 700; margin-bottom: 12px; }
.stem { border-left: 4px solid #2563eb; background: #eff6ff; padding: 12px 15px; border-radius: 6px; }
.answer { border-left: 4px solid #059669; background: #ecfdf5; padding: 12px 15px; border-radius: 6px; }
.label { font-size: .78em; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; color: #475569; margin-bottom: 5px; }
.muted { color: #64748b; font-size: .86em; }
.source { font-size: .85em; border-top: 1px solid #cbd5e1; margin-top: 16px; padding-top: 10px; }
.reading { margin-top: 14px; border-top: 1px solid #cbd5e1; padding-top: 12px; }
.reading h4 { margin: 14px 0 4px; color: #334155; }
.reading p { margin: 4px 0 9px; }
.warning { color: #92400e; background: #fffbeb; border-left: 4px solid #f59e0b; padding: 9px 12px; }
a { color: #0369a1; }
.nightMode .card, .night_mode .card { color: #e2e8f0; background: #111827; }
.nightMode .stem, .night_mode .stem { background: #172554; }
.nightMode .answer, .night_mode .answer { background: #064e3b; }
.nightMode .label, .night_mode .label { color: #cbd5e1; }
.nightMode .source, .night_mode .source { border-color: #475569; }
""".strip()


@dataclass
class Section:
    title: str
    text: str
    category: str = "other"


@dataclass
class CaseReport:
    case_id: str
    title: str
    doi: str = ""
    year: str = ""
    journal: str = ""
    specialty: str = "general-medicine"
    abstract: str = ""
    sections: list[Section] = field(default_factory=list)
    source_url: str = ""
    license: str = ""
    cited_by_count: int = 0
    educational_score: int = 0
    raw_meta: dict = field(default_factory=dict)

    def category_text(self, category: str) -> str:
        return "\n\n".join(s.text for s in self.sections if s.category == category and s.text)

    def all_body_text(self) -> str:
        return "\n\n".join(s.text for s in self.sections if s.text)


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def element_text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    pieces = [t for t in element.itertext() if t and t.strip()]
    return clean_text(" ".join(pieces))


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = value.replace("\u00a0", " ")
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"\s+([,.;:!?])", r"\1", value)
    return value.strip()


def classify_section(title: str) -> str:
    for category, pattern in SECTION_RULES:
        if pattern.search(title or ""):
            return category
    return "other"


def extract_sections(body: ET.Element | None) -> list[Section]:
    output: list[Section] = []
    if body is None:
        return output

    def walk(sec: ET.Element, parent_title: str = "") -> None:
        title_node = next((c for c in list(sec) if local_name(c.tag) == "title"), None)
        title = element_text(title_node) or parent_title or "Case details"
        chunks: list[str] = []
        for child in list(sec):
            kind = local_name(child.tag)
            if kind in {"title", "sec"}:
                continue
            if kind in {"p", "list", "disp-quote", "boxed-text", "fig", "table-wrap", "supplementary-material"}:
                txt = element_text(child)
                if txt:
                    chunks.append(txt)
        text = clean_text(" ".join(chunks))
        if text:
            output.append(Section(title=title, text=text, category=classify_section(title)))
        for child in list(sec):
            if local_name(child.tag) == "sec":
                walk(child, title)

    for node in list(body):
        if local_name(node.tag) == "sec":
            walk(node)
        elif local_name(node.tag) in {"p", "fig", "table-wrap"}:
            txt = element_text(node)
            if txt:
                output.append(Section(title="Case details", text=txt, category="other"))
    return output


def article_meta(root: ET.Element, folder: Path) -> dict:
    meta_path = folder / "meta.json"
    metadata: dict = {}
    if meta_path.exists():
        try:
            loaded = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                metadata = loaded
        except (OSError, json.JSONDecodeError):
            metadata = {}

    title = str(metadata.get("title") or "")
    doi = str(metadata.get("doi") or "")
    journal = str(metadata.get("journal") or "")
    year = str(metadata.get("year") or "")
    license_text = str(metadata.get("license") or "")
    for node in root.iter():
        name = local_name(node.tag)
        if name == "article-title" and not title:
            title = element_text(node)
        elif name == "article-id" and node.attrib.get("pub-id-type", "").lower() == "doi" and not doi:
            doi = element_text(node)
        elif name == "journal-title" and not journal:
            journal = element_text(node)
        elif name == "year" and not year:
            candidate_year = element_text(node)
            if re.fullmatch(r"\d{4}", candidate_year):
                year = candidate_year
        elif name == "license" and not license_text:
            license_text = node.attrib.get(XLINK_HREF, "") or element_text(node)
    return {
        "title": title, "doi": doi.strip().lower(), "journal": journal,
        "year": year, "license": license_text,
        "id": str(metadata.get("pmcid") or metadata.get("id") or folder.name),
        "specialty": str(metadata.get("specialty") or "general-medicine"),
        "source_url": str(metadata.get("source_url") or ""),
        "cited_by_count": metadata.get("cited_by_count", 0),
        "educational_score": metadata.get("educational_score", 0),
        "metadata": metadata,
    }


def extract_jats_media(root: ET.Element, case_id: str, source_url: str, license_text: str, xml_sha256: str) -> list[dict]:
    """Extract structured figure/table references without downloading or embedding assets.

    Unknown or ambiguous reuse rights are never treated as permission. This is a
    provenance inventory for manual review, not an image interpretation layer.
    """
    records: list[dict] = []
    for node in root.iter():
        kind = local_name(node.tag)
        if kind not in {"fig", "table-wrap"}:
            continue
        label = ""
        caption = ""
        graphic_refs: list[str] = []
        for child in node.iter():
            child_kind = local_name(child.tag)
            if child_kind == "label" and not label:
                label = element_text(child)
            elif child_kind == "caption" and not caption:
                caption = element_text(child)
            elif child_kind in {"graphic", "inline-graphic", "media"}:
                href = child.attrib.get(XLINK_HREF) or child.attrib.get("href") or ""
                if href and href not in graphic_refs:
                    graphic_refs.append(href)
        identity = f"{case_id}|{kind}|{label}|{caption}|{'|'.join(graphic_refs)}"
        asset_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
        license_norm = (license_text or "").strip()
        records.append({
            "schema_version": 1,
            "asset_id": asset_id,
            "case_id": case_id,
            "asset_type": "figure" if kind == "fig" else "table",
            "label": label,
            "caption": caption,
            "graphic_refs": graphic_refs,
            "source_url": source_url,
            "source_xml_sha256": xml_sha256,
            "license": license_norm,
            "reuse_decision": "manual_review_required" if license_norm else "blocked_unknown_license",
            "downloaded": False,
            "embedded": False,
        })
    return records


def parse_jats(path: Path) -> CaseReport:
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise ValueError(f"Could not parse {path}: {exc}") from exc
    if local_name(root.tag) != "article":
        raise ValueError(f"{path} is not a JATS <article> document")
    meta = article_meta(root, path.parent)
    abstract_nodes = [node for node in root.iter() if local_name(node.tag) == "abstract"]
    abstracts = [element_text(node) for node in abstract_nodes if element_text(node)]
    abstract = clean_text(" ".join(dict.fromkeys(abstracts)))
    body = next((node for node in root.iter() if local_name(node.tag) == "body"), None)
    sections = extract_sections(body)
    if not sections and body is not None:
        fallback = element_text(body)
        if fallback:
            sections = [Section("Full text", fallback, "other")]
    case_id = meta["id"] or path.parent.name or path.stem
    source_url = meta["source_url"]
    if not source_url and re.fullmatch(r"PMC\d+", case_id, re.I):
        source_url = f"https://europepmc.org/articles/{case_id.upper()}"
    return CaseReport(
        case_id=case_id, title=meta["title"] or path.stem, doi=meta["doi"], year=meta["year"],
        journal=meta["journal"], specialty=meta["specialty"], abstract=abstract,
        sections=sections, source_url=source_url, license=meta["license"],
        cited_by_count=safe_int(meta.get("cited_by_count", 0)),
        educational_score=safe_int(meta.get("educational_score", 0)), raw_meta=meta["metadata"],
    )


def safe_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def sentence_list(text: str) -> list[str]:
    # Keep common clinical abbreviations from causing aggressive sentence splits.
    value = re.sub(r"\b(?:Dr|Mr|Mrs|Ms|Prof|Fig|Figs|No|vs|i\.e|e\.g)\.", lambda m: m.group(0).replace(".", "<DOT>"), text or "", flags=re.I)
    pieces = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9(])", value)
    return [clean_text(p.replace("<DOT>", ".")) for p in pieces if clean_text(p.replace("<DOT>", "."))]


def trim_diagnosis(value: str) -> str:
    value = clean_text(value)
    value = re.sub(r"^(?:an?\s+|the\s+)?", "", value, flags=re.I)
    value = re.sub(r"^(?:a|an|the)\s+(?:rare|unusual|novel|unique|first|interesting|challenging)\s+(?:case|presentation|report)\s+of\s+", "", value, flags=re.I)
    value = re.sub(r"^(?:case report|case study|case)\s*[:\-]\s*", "", value, flags=re.I)
    value = re.sub(r"\s+(?:which|who|that|resulting in|leading to|for which|after which)\b.*$", "", value, flags=re.I)
    value = re.split(r"[.;:\n]", value, maxsplit=1)[0]
    value = re.sub(r"\s+(?:and)\s+(?:was|were|had|has|have|the patient|the subject)\b.*$", "", value, flags=re.I)
    value = value.strip(" ,;:-–—()")
    return value[:140].strip()


def plausible_diagnosis(value: str) -> bool:
    if not value or len(value) < 4 or len(value) > 140:
        return False
    words = re.findall(r"[A-Za-z][A-Za-z-]*", value.lower())
    if not words:
        return False
    meaningful = [w for w in words if w not in FILLER_DX]
    if not meaningful:
        return False
    if value.lower().startswith(("the patient", "our patient", "clinical features", "symptoms of", "history of")):
        return False
    return True


def diagnosis_from_title(title: str) -> str:
    value = clean_text(title)
    value = re.sub(r"\s*[:;]\s*(?:a\s+)?case report\.?$", "", value, flags=re.I)
    value = re.sub(r"\s*[-–—]\s*a\s+case report\.?$", "", value, flags=re.I)
    value = re.sub(r"^(?:a|an|the)\s+(?:rare|unusual|novel|unique|first|interesting|challenging)\s+(?:case|presentation|report)\s+of\s+", "", value, flags=re.I)
    value = re.sub(r"^(?:a|an|the)?\s*(?:case report|case study|case)\s*(?:of\s+)?[:\-]?\s*", "", value, flags=re.I)
    value = re.sub(r"^(?:a|an|the)\s+case\s+of\s+", "", value, flags=re.I)
    value = re.sub(r"\s+(?:presenting|manifesting|masquerading|mimicking)\s+(?:as|with)\s+.*$", "", value, flags=re.I)
    value = re.sub(r"\s+(?:in|among)\s+(?:a|an|the)?\s*(?:\d+[- ]year[- ]old\s+)?(?:man|woman|patient|boy|girl|child|infant|neonate|adult|elderly).*?$", "", value, flags=re.I)
    value = re.sub(r"\s+in\s+(?:a|an|the)?\s*\d+[- ](?:year|month|week|day)[- ]old.*$", "", value, flags=re.I)
    value = value.strip(" .,:;-")
    # Very long titles often describe a phenomenon rather than a compact diagnosis.
    if len(value) > 100:
        return ""
    return value if plausible_diagnosis(value) else ""


def assertion_attributes(sentence: str) -> dict[str, str]:
    """Rule-based assertion attributes; conservative and explicitly heuristic."""
    text = sentence or ""
    lower = text.lower()
    certainty = "positive"
    if re.search(r"\b(?:no evidence of|ruled out|excluded|negative for|without evidence of|did not show|not diagnosed with|absence of)\b", lower):
        certainty = "negated"
    elif re.search(r"\b(?:possible|possibly|probable|probably|suspected|suspicious for|may represent|might represent|could represent|cannot exclude|differential diagnosis)\b", lower):
        certainty = "possible"
    elif re.search(r"\b(?:considered|consideration of|risk of|at risk for|screen for)\b", lower):
        certainty = "hypothetical"

    temporality = "current"
    if re.search(r"\b(?:previously|history of|past history|prior|years? ago|months? ago|previous diagnosis|remote)\b", lower):
        temporality = "historical"
    experiencer = "patient"
    if re.search(r"\b(?:mother|father|sister|brother|family history|maternal|paternal|relative|sibling)\b", lower):
        experiencer = "family"
    return {"certainty": certainty, "temporality": temporality, "experiencer": experiencer}


def find_diagnosis_detail(case: CaseReport) -> dict[str, object]:
    """Extract one diagnosis candidate plus the exact sentence and assertion labels."""
    ordered_texts: list[tuple[str, str]] = []
    for category in ("diagnosis", "investigations", "conclusion", "presentation", "discussion"):
        text = case.category_text(category)
        if text:
            ordered_texts.append((category, text))
    body = case.all_body_text()
    if body:
        ordered_texts.append(("body", body))
    if case.abstract:
        ordered_texts.append(("abstract", case.abstract))

    seen: set[tuple[str, str]] = set()
    rejected: list[dict[str, str]] = []
    for source, text in ordered_texts:
        for sentence in sentence_list(text):
            for pattern, confidence, rationale in DX_PATTERNS:
                match = pattern.search(sentence)
                if not match:
                    continue
                candidate = trim_diagnosis(match.group("dx"))
                if not plausible_diagnosis(candidate):
                    continue
                attrs = assertion_attributes(sentence)
                if attrs["certainty"] != "positive" or attrs["temporality"] != "current" or attrs["experiencer"] != "patient":
                    rejected.append({"sentence": sentence, **attrs})
                    continue
                key = (candidate.lower(), sentence.lower())
                if key in seen:
                    continue
                seen.add(key)
                return {"diagnosis": candidate, "source": source, "confidence": confidence,
                        "rationale": rationale, "evidence_sentence": sentence,
                        "certainty": attrs["certainty"], "temporality": attrs["temporality"],
                        "experiencer": attrs["experiencer"], "rejected_candidates": rejected}

    title_dx = diagnosis_from_title(case.title)
    if title_dx:
        return {"diagnosis": title_dx, "source": "title", "confidence": 0.58,
                "rationale": "diagnosis inferred from article title; manual verification required",
                "evidence_sentence": "", "certainty": "inferred", "temporality": "unknown",
                "experiencer": "unknown", "rejected_candidates": rejected}
    return {"diagnosis": "", "source": "", "confidence": 0.0,
            "rationale": "no reliable current positive patient diagnosis extracted",
            "evidence_sentence": "", "certainty": "unknown", "temporality": "unknown",
            "experiencer": "unknown", "rejected_candidates": rejected}


def find_diagnosis(case: CaseReport) -> tuple[str, str, float, str]:
    detail = find_diagnosis_detail(case)
    return (str(detail["diagnosis"]), str(detail["source"]),
            float(detail["confidence"]), str(detail["rationale"]))


def mask_answer(text: str, diagnosis: str) -> str:
    if not text or not diagnosis:
        return text
    result = re.sub(r"(?<!\w)" + re.escape(diagnosis) + r"(?!\w)", "[diagnosis hidden]", text, flags=re.I)
    # Also hide the core disease phrase when the extracted label includes
    # qualifiers (e.g. "symptomatic renal artery aneurysm" vs "renal artery aneurysm").
    modifiers = {
        "acute", "chronic", "symptomatic", "asymptomatic", "mild", "moderate", "severe", "recurrent",
        "relapsed", "metastatic", "primary", "secondary", "left", "right", "bilateral", "unilateral",
        "congenital", "idiopathic", "spontaneous", "rare", "unusual", "novel", "suspected", "probable",
        "confirmed", "complicated", "advanced", "early", "late", "stage", "grade",
    }
    core = [w for w in re.findall(r"[A-Za-z][A-Za-z-]*", diagnosis) if w.lower() not in modifiers and w.lower() not in FILLER_DX]
    if len(core) >= 2 or (len(core) == 1 and len(core[0]) >= 5):
        core_pattern = r"(?<!\w)" + r"[\s-]+".join(re.escape(w) for w in core) + r"(?!\w)"
        result = re.sub(core_pattern, "[diagnosis hidden]", result, flags=re.I)
    return result


def make_stem(case: CaseReport, diagnosis: str, max_chars: int = 1100) -> str:
    ordered = []
    for category in ("presentation", "history", "examination", "investigations"):
        text = case.category_text(category)
        if text:
            ordered.append(text)
    if not ordered and case.abstract:
        ordered.append(case.abstract)
    if not ordered:
        ordered.append(case.all_body_text())
    # Deduplicate repeated paragraphs across broad case-report headings.
    seen: set[str] = set()
    selected: list[str] = []
    for block in ordered:
        for sentence in sentence_list(block):
            sentence = clean_text(mask_answer(sentence, diagnosis))
            if not sentence or sentence in seen:
                continue
            seen.add(sentence)
            if len(sentence) < 20:
                continue
            selected.append(sentence)
            if len(selected) >= 6 or sum(len(s) for s in selected) >= max_chars:
                break
        if len(selected) >= 6 or sum(len(s) for s in selected) >= max_chars:
            break
    stem = " ".join(selected)
    if len(stem) > max_chars:
        cut = stem[:max_chars]
        boundary = max(cut.rfind(". "), cut.rfind("; "))
        stem = cut[:boundary + 1] if boundary > max_chars * 0.55 else cut.rsplit(" ", 1)[0] + " …"
    return clean_text(stem)


def make_context(case: CaseReport, diagnosis: str, categories: Iterable[str], max_chars: int = 650,
                 exclude_pattern: re.Pattern[str] | None = None) -> str:
    blocks = [case.category_text(category) for category in categories]
    blocks = [block for block in blocks if block]
    if not blocks and "presentation" in categories and case.abstract:
        blocks = [case.abstract]
    sentences: list[str] = []
    seen: set[str] = set()
    for block in blocks:
        for sentence in sentence_list(block):
            sentence = clean_text(mask_answer(sentence, diagnosis))
            if exclude_pattern and exclude_pattern.search(sentence):
                continue
            if len(sentence) < 20 or sentence in seen:
                continue
            seen.add(sentence)
            sentences.append(sentence)
            if len(" ".join(sentences)) >= max_chars or len(sentences) >= 3:
                break
        if len(" ".join(sentences)) >= max_chars or len(sentences) >= 3:
            break
    output = " ".join(sentences)
    if len(output) > max_chars:
        output = output[:max_chars].rsplit(" ", 1)[0] + " …"
    return clean_text(output)


def context_html(text: str) -> str:
    return f'<div class="stem"><div class="label">Case context</div>{esc(text)}</div>' if text else ""


def source_link(case: CaseReport) -> str:
    if case.doi:
        doi = case.doi.strip()
        if doi.startswith("http://") or doi.startswith("https://"):
            return doi
        return "https://doi.org/" + quote(doi, safe="/()")
    if case.source_url:
        return case.source_url
    if re.fullmatch(r"PMC\d+", case.case_id, re.I):
        return f"https://europepmc.org/articles/{case.case_id.upper()}"
    return ""


def esc(value: str) -> str:
    return html.escape(value or "", quote=True)


def html_paragraphs(text: str) -> str:
    if not text:
        return ""
    return "".join(f"<p>{esc(p)}</p>" for p in sentence_list(text))


def build_reading_extra(case: CaseReport, dx: str, dx_source: str, confidence: float, max_chars: int,
                        evidence_sentence: str = "", assertion: dict[str, str] | None = None) -> str:
    parts = ['<div class="reading"><div class="label">Full case reading · extracted from source</div>']
    if evidence_sentence:
        parts.append('<h4>Diagnosis evidence in the article</h4>' + html_paragraphs(evidence_sentence))
    if assertion:
        parts.append('<p class="muted">Assertion review: ' + esc(assertion.get("certainty", "unknown")) +
                     ' · temporality: ' + esc(assertion.get("temporality", "unknown")) +
                     ' · experiencer: ' + esc(assertion.get("experiencer", "unknown")) + '</p>')
    if case.abstract:
        abstract = case.abstract
        if len(abstract) > max_chars:
            abstract = abstract[:max_chars].rsplit(" ", 1)[0] + " …"
        parts.append('<h4>Abstract</h4>' + html_paragraphs(abstract))
    total = len(abstract) if case.abstract else 0
    for section in case.sections:
        if not section.text:
            continue
        # This panel is on the answer side: keep the full text visible here.
        text = section.text
        remaining = max_chars - total
        if remaining <= 0:
            parts.append('<p class="muted">Reading panel clipped at the configured character limit. Open the source article for the full text.</p>')
            break
        if len(text) > remaining:
            text = text[:remaining].rsplit(" ", 1)[0] + " …"
        parts.append(f"<h4>{esc(section.title)}</h4>{html_paragraphs(text)}")
        total += len(text)
    parts.append(f'<p class="muted">Extraction confidence: {confidence:.2f} · Diagnosis source: {esc(dx_source or "not found")}</p>')
    link = source_link(case)
    if link:
        parts.append(f'<p class="source">Source: <a href="{esc(link)}">{esc(case.title or case.case_id)}</a> · {esc(case.journal)} {esc(case.year)}</p>')
    if case.license:
        parts.append(f'<p class="muted">Reported article licence: {esc(case.license)}</p>')
    parts.append("</div>")
    return "".join(parts)


def evidence_spans(case: CaseReport, sentences: Iterable[str]) -> list[dict]:
    """Return exact, section-local evidence spans for source sentences."""
    wanted = {clean_text(sentence) for sentence in sentences if clean_text(sentence)}
    spans: list[dict] = []
    seen: set[tuple[str, int, int]] = set()
    for section in case.sections:
        for sentence in sentence_list(section.text):
            sentence = clean_text(sentence)
            if sentence not in wanted:
                continue
            start = section.text.find(sentence)
            if start < 0:
                continue
            key = (section.title, start, start + len(sentence))
            if key in seen:
                continue
            seen.add(key)
            evidence_id = hashlib.sha256(
                f"{case.case_id}|{section.title}|{start}|{start + len(sentence)}|{sentence}".encode("utf-8")
            ).hexdigest()[:20]
            spans.append({
                "evidence_id": evidence_id,
                "section": section.title,
                "category": section.category,
                "text": sentence,
                "start_char": start,
                "end_char": start + len(sentence),
            })
    return spans


def make_card(case: CaseReport, card_type: str, front: str, back: str, extra: str,
              tags: list[str], evidence_sentences: Iterable[str] = ()) -> dict:
    spans = evidence_spans(case, evidence_sentences)
    card_id = hashlib.sha256(
        f"{case.case_id}::{card_type}::{source_link(case)}".encode("utf-8")
    ).hexdigest()[:20]
    return {
        "schema_version": 1,
        "card_id": card_id,
        "case_id": case.case_id,
        "card_type": card_type,
        "front": front,
        "back": back,
        "extra": extra,
        "tags": tags,
        "title": case.title,
        "specialty": case.specialty,
        "source_url": source_link(case),
        "evidence_spans": spans,
        "evidence_coverage": bool(spans),
        "review_status": "needs-human-review",
    }


def build_cards(case: CaseReport, *, min_confidence: float = 0.60,
                include_management: bool = True, include_outcome: bool = True,
                include_investigation: bool = True, include_teaching: bool = True,
                include_differential: bool = True, include_reasoning: bool = True,
                reading_limit: int = 18000) -> tuple[list[dict], dict]:
    diagnosis_detail = find_diagnosis_detail(case)
    diagnosis = str(diagnosis_detail["diagnosis"])
    dx_source = str(diagnosis_detail["source"])
    confidence = float(diagnosis_detail["confidence"])
    rationale = str(diagnosis_detail["rationale"])
    evidence_sentence = str(diagnosis_detail["evidence_sentence"])
    assertion = {k: str(diagnosis_detail[k]) for k in ("certainty", "temporality", "experiencer")}
    quality_flags: list[str] = []
    cards: list[dict] = []
    extra = build_reading_extra(case, diagnosis, dx_source, confidence, reading_limit,
                                evidence_sentence=evidence_sentence, assertion=assertion)
    diagnosis_stem = make_stem(case, diagnosis)
    investigation_context = make_context(case, diagnosis, ("presentation", "history", "examination"), exclude_pattern=INVESTIGATION_TERMS)
    management_context = make_context(case, diagnosis, ("presentation", "history", "examination", "investigations"))
    outcome_context = make_context(case, diagnosis, ("presentation", "history", "examination", "investigations", "management"))
    teaching_context = make_context(case, diagnosis, ("presentation", "history", "examination", "investigations"))
    specialty_tag = "specialty::" + tagify(case.specialty)

    if not diagnosis:
        quality_flags.append("no-diagnosis")
    elif dx_source == "title":
        quality_flags.append("title-inferred-diagnosis")
    if diagnosis and confidence < min_confidence:
        quality_flags.append("low-diagnosis-confidence")
    if diagnosis and confidence >= min_confidence:
        stem = diagnosis_stem
        if len(stem) >= 45:
            front = (
                '<div class="question">A patient in this case report presents with the following findings. '
                'What is the single most likely diagnosis?</div>'
                f'<div class="stem">{esc(stem)}</div>'
            )
            support = []
            for category in ("examination", "investigations", "presentation"):
                text = case.category_text(category)
                for sentence in sentence_list(text):
                    if diagnosis and re.search(r"(?<!\w)" + re.escape(diagnosis) + r"(?!\w)", sentence, re.I):
                        continue
                    if len(sentence) >= 25 and sentence not in support:
                        support.append(sentence)
                    if len(support) >= 3:
                        break
                if len(support) >= 3:
                    break
            back = (
                '<div class="answer"><div class="label">Most likely diagnosis</div>'
                f'<strong>{esc(diagnosis)}</strong></div>'
                f'<p><strong>Why it fits:</strong> {esc(rationale)}. Findings reported in the article are listed below; '
                'interpret them in the context of the original case.</p>'
            )
            if support:
                back += "<ul>" + "".join(f"<li>{esc(s)}</li>" for s in support) + "</ul>"
            tags = ["case-report", "card::diagnosis", specialty_tag, "confidence::" + confidence_tag(confidence)]
            cards.append(make_card(case, "diagnosis", front, back, extra, tags, [evidence_sentence]))
        else:
            quality_flags.append("insufficient-case-stem")

    if include_investigation:
        inv_sentences = []
        for sentence in sentence_list(case.category_text("investigations") + " " + case.category_text("diagnosis")):
            if INVESTIGATION_TERMS.search(sentence) and len(sentence) >= 25:
                inv_sentences.append(sentence)
            if len(inv_sentences) >= 3:
                break
        if inv_sentences:
            front = context_html(investigation_context) + '<div class="question">Which investigation(s) provided the key diagnostic evidence in this report?</div>'
            back = "<div class=\"answer\"><div class=\"label\">Reported investigation and finding</div>" + "".join(f"<p>{esc(s)}</p>" for s in inv_sentences) + "</div>"
            tags = ["case-report", "card::investigation", specialty_tag]
            cards.append(make_card(case, "investigation", front, back, extra, tags, inv_sentences))

    if include_management:
        management = case.category_text("management")
        if len(management) >= 45:
            front = context_html(management_context) + '<div class="question">What management was actually reported for this patient?</div>'
            back = (
                '<div class="answer"><div class="label">Case-reported management</div>'
                f'{html_paragraphs(management)}</div>'
                '<p class="warning">This is the treatment reported in one case, not a universal guideline or a substitute for current clinical guidance.</p>'
            )
            tags = ["case-report", "card::management", specialty_tag]
            cards.append(make_card(case, "management", front, back, extra, tags, sentence_list(management)))

    if include_outcome:
        outcome = case.category_text("outcome")
        if len(outcome) >= 35:
            front = context_html(outcome_context) + '<div class="question">What was the reported outcome and follow-up in this case?</div>'
            back = '<div class="answer"><div class="label">Reported outcome</div>' + html_paragraphs(outcome) + "</div>"
            tags = ["case-report", "card::outcome", specialty_tag]
            cards.append(make_card(case, "outcome", front, back, extra, tags, sentence_list(outcome)))

    if include_teaching:
        teaching_sentences = []
        teaching_sections = [s for s in case.sections if s.category in {"conclusion", "discussion"}]
        for section in teaching_sections:
            heading_is_teaching = bool(re.search(r"learning|key point|take.?home|teaching|summary", section.title, re.I))
            for sentence in sentence_list(section.text):
                if TEACHING_TERMS.search(sentence) or heading_is_teaching or (section.category == "conclusion" and 35 <= len(sentence) <= 300):
                    if 30 <= len(sentence) <= 420 and sentence not in teaching_sentences:
                        teaching_sentences.append(sentence)
                if len(teaching_sentences) >= 3:
                    break
            if len(teaching_sentences) >= 3:
                break
        if teaching_sentences:
            front = context_html(teaching_context) + '<div class="question">What is the key clinical / exam takeaway from this case?</div>'
            back = '<div class="answer"><div class="label">Article-reported teaching point</div>' + "".join(f"<p>{esc(s)}</p>" for s in teaching_sentences) + "</div>"
            tags = ["case-report", "card::teaching-point", specialty_tag]
            cards.append(make_card(case, "teaching", front, back, extra, tags, teaching_sentences))

    if include_differential:
        differential_sentences: list[str] = []
        for section in case.sections:
            for sentence in sentence_list(section.text):
                if DIFFERENTIAL_TERMS.search(sentence) and 35 <= len(sentence) <= 420:
                    if sentence not in differential_sentences:
                        differential_sentences.append(sentence)
                if len(differential_sentences) >= 3:
                    break
            if len(differential_sentences) >= 3:
                break
        if differential_sentences:
            front = context_html(teaching_context) + '<div class="question">Which alternative diagnoses or diagnostic pitfalls did the authors specifically discuss?</div>'
            back = ('<div class="answer"><div class="label">Article-discussed alternatives / pitfalls</div>' +
                    ''.join(f'<p>{esc(sentence)}</p>' for sentence in differential_sentences) + '</div>' +
                    '<p class="warning">This is only what the authors discussed; it is not a complete or independently validated differential diagnosis.</p>')
            cards.append(make_card(case, "differential", front, back, extra,
                                   ["case-report", "card::differential", specialty_tag], differential_sentences))

    # Reasoning cards quote only explicit author reasoning from the discussion.
    # They do not infer a next-best step or turn a case report into a guideline.
    reasoning_sentences: list[str] = []
    for section in case.sections:
        if not include_reasoning or section.category != "discussion":
            continue
        for sentence in sentence_list(section.text):
            if REASONING_TERMS.search(sentence) and 35 <= len(sentence) <= 420:
                if sentence not in reasoning_sentences:
                    reasoning_sentences.append(sentence)
            if len(reasoning_sentences) >= 2:
                break
        if len(reasoning_sentences) >= 2:
            break
    if reasoning_sentences:
        front = context_html(teaching_context) + '<div class="question">Which clinical reasoning point did the authors explicitly emphasize?</div>'
        back = ('<div class="answer"><div class="label">Article-stated reasoning</div>' +
                ''.join(f'<p>{esc(sentence)}</p>' for sentence in reasoning_sentences) + '</div>' +
                "<p class=\"warning\">This summarizes the authors\' reasoning in this report; it is not an independently validated diagnostic rule.</p>")
        cards.append(make_card(case, "reasoning", front, back, extra,
                               ["case-report", "card::reasoning", specialty_tag], reasoning_sentences))

    if not cards:
        quality_flags.append("no-cards-generated")
    evidence_count = sum(bool(card.get("evidence_spans")) for card in cards)
    if cards and evidence_count < len(cards):
        quality_flags.append("cards-missing-evidence-spans")
    if not source_link(case):
        quality_flags.append("missing-source-link")
    review_reasons = list(quality_flags)
    if diagnosis and (dx_source == "title" or confidence < min_confidence):
        review_reasons.append("diagnosis-needs-manual-verification")
    if not diagnosis:
        review_reasons.append("no-reliable-diagnosis-extracted")
    report = {
        "case_id": case.case_id,
        "title": case.title,
        "diagnosis": diagnosis,
        "diagnosis_source": dx_source,
        "confidence": round(confidence, 3),
        "diagnosis_rationale": rationale,
        "diagnosis_evidence_sentence": evidence_sentence,
        "diagnosis_evidence_section": dx_source,
        "assertion_certainty": assertion["certainty"],
        "assertion_temporality": assertion["temporality"],
        "assertion_experiencer": assertion["experiencer"],
        "rejected_diagnosis_candidates": json.dumps(diagnosis_detail.get("rejected_candidates", []), ensure_ascii=False),
        "card_count": len(cards),
        "card_types": ";".join(card["card_type"] for card in cards),
        "quality_flags": ";".join(quality_flags),
        "review_status": "needs-human-review",
        "review_reasons": ";".join(dict.fromkeys(review_reasons)),
        "evidence_coverage": f"{evidence_count}/{len(cards)}" if cards else "0/0",
        "specialty": case.specialty,
        "educational_score": case.educational_score,
        "source_url": source_link(case),
    }
    return cards, report


def apply_card_limit(cards: list[dict], reports: list[dict], limit: int) -> list[dict]:
    """Apply the export limit and synchronize audit metrics with actual cards."""
    limited = cards[:limit] if limit > 0 else cards
    exported_by_case: dict[str, list[dict]] = {}
    for card in limited:
        exported_by_case.setdefault(str(card.get("case_id", "")), []).append(card)
    for report in reports:
        case_id = str(report.get("case_id", ""))
        exported = exported_by_case.get(case_id, [])
        previous_count = int(report.get("card_count", 0) or 0)
        report["card_count"] = len(exported)
        report["card_types"] = ";".join(str(card.get("card_type", "")) for card in exported)
        evidence_count = sum(bool(card.get("evidence_spans")) for card in exported)
        report["evidence_coverage"] = f"{evidence_count}/{len(exported)}" if exported else "0/0"
        if limit > 0 and len(limited) < len(cards) and len(exported) < previous_count:
            flags = [flag for flag in str(report.get("quality_flags", "")).split(";") if flag]
            if "export-limit-truncated" not in flags:
                flags.append("export-limit-truncated")
            report["quality_flags"] = ";".join(flags)
    return limited


def confidence_tag(value: float) -> str:
    if value >= 0.85:
        return "high"
    if value >= 0.70:
        return "medium"
    return "low"


def tagify(value: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", (value or "").lower()).strip("_") or "general_medicine"


def discover_xml_files(input_path: Path) -> list[Path]:
    if input_path.is_file():
        if input_path.name.lower() != "article.xml" and input_path.suffix.lower() != ".xml":
            raise ValueError("Input file must be a JATS article.xml file")
        return [input_path]
    if not input_path.is_dir():
        raise FileNotFoundError(f"Input path does not exist: {input_path}")
    files = sorted(input_path.rglob("article.xml"))
    return files


def write_tsv(path: Path, cards: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t", quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        writer.writerow(["Front", "Back", "Extra", "Tags"])
        for card in cards:
            writer.writerow([card["front"], card["back"], card["extra"], " ".join(card["tags"])])


def write_apkg(path: Path, cards: list[dict], deck_name: str) -> None:
    try:
        import genanki  # type: ignore
    except ImportError as exc:
        raise RuntimeError("The .apkg export needs genanki. Install dependencies with: pip install -r requirements.txt") from exc

    model_id = 1647392294
    model = genanki.Model(
        model_id,
        "Clinical Case Exam Cards v7",
        fields=[{"name": "Front"}, {"name": "Back"}, {"name": "Extra"}],
        templates=[{
            "name": "Clinical Case Q&A",
            "qfmt": "{{Front}}",
            "afmt": "{{FrontSide}}<hr id=answer>{{Back}}{{#Extra}}<hr>{{Extra}}{{/Extra}}",
        }],
        css=CARD_CSS,
    )
    deck_id = int(hashlib.sha1(deck_name.encode("utf-8")).hexdigest()[:8], 16) or 1
    deck = genanki.Deck(deck_id, deck_name)
    for card in cards:
        guid_source = f"{card['case_id']}::{card['card_type']}"
        note = genanki.Note(
            model=model,
            fields=[card["front"], card["back"], card["extra"]],
            guid=genanki.guid_for(guid_source),
            tags=card["tags"],
        )
        deck.add_note(note)
    package = genanki.Package(deck)
    path.parent.mkdir(parents=True, exist_ok=True)
    package.write_to_file(str(path))


def write_preview(path: Path, cards: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sample = cards[:50]
    html_doc = (
        "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<style>{CARD_CSS}.preview-card{{border:1px solid #cbd5e1;border-radius:10px;margin:18px auto;}}</style>"
        "</head><body>"
        + "".join(
            '<div class="card preview-card">' + card["front"] + '<hr id="answer">' + card["back"] + card["extra"] + "</div>"
            for card in sample
        )
        + "</body></html>"
    )
    path.write_text(html_doc, encoding="utf-8")


def load_review_decisions(path: Path, cards: list[dict]) -> None:
    """Apply explicit human decisions to cards, rejecting stale or malformed rows."""
    by_id = {str(card["card_id"]): card for card in cards}
    seen: set[str] = set()
    allowed = {"approved", "rejected", "needs-changes"}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"Could not read review decisions {path}: {exc}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            decision = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in review decisions line {line_number}: {exc}") from exc
        if not isinstance(decision, dict):
            raise ValueError(f"Review decision line {line_number} must be a JSON object")
        card_id = str(decision.get("card_id", "")).strip()
        status = str(decision.get("status", "")).strip().lower()
        reviewer = str(decision.get("reviewer", "")).strip()
        if not card_id or card_id not in by_id:
            raise ValueError(f"Review decision line {line_number} references unknown card_id: {card_id!r}")
        if card_id in seen:
            raise ValueError(f"Duplicate review decision for card_id {card_id}")
        if status not in allowed:
            raise ValueError(f"Review decision line {line_number} has invalid status {status!r}")
        if not reviewer:
            raise ValueError(f"Review decision line {line_number} must include a reviewer")
        seen.add(card_id)
        by_id[card_id]["review_status"] = status
        by_id[card_id]["reviewer"] = reviewer
        by_id[card_id]["reviewed_at"] = str(decision.get("reviewed_at", "")).strip()
        by_id[card_id]["review_notes"] = str(decision.get("notes", "")).strip()


def update_case_review_status(reports: list[dict], cards: list[dict]) -> None:
    statuses: dict[str, list[str]] = {}
    for card in cards:
        statuses.setdefault(str(card.get("case_id", "")), []).append(str(card.get("review_status", "needs-human-review")))
    for report in reports:
        values = statuses.get(str(report.get("case_id", "")), [])
        if not values or any(value == "needs-human-review" for value in values):
            status = "needs-human-review"
        elif all(value == "approved" for value in values):
            status = "approved"
        elif "rejected" in values:
            status = "rejected-or-mixed"
        elif "needs-changes" in values:
            status = "needs-changes"
        else:
            status = "needs-human-review"
        report["review_status"] = status


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert harvested JATS clinical cases into exam-style Anki cards.")
    parser.add_argument("input", type=Path, help="Folder containing article.xml files, or one JATS XML file")
    parser.add_argument("-o", "--output", type=Path, default=Path("decks/clinical_cases"),
                        help="Output basename, without extension (default: decks/clinical_cases)")
    parser.add_argument("--deck", default="Clinical Case Reports", help="Anki deck name")
    parser.add_argument("--specialty", default="auto", help="Override specialty tag; default reads harvest metadata")
    parser.add_argument("--format", choices=("apkg", "tsv", "both"), default="both")
    parser.add_argument("--limit", type=int, default=0, help="Maximum total cards; 0 means no limit")
    parser.add_argument("--min-confidence", type=float, default=0.60,
                        help="Minimum diagnosis confidence for diagnosis cards (0–1)")
    parser.add_argument("--reading-limit", type=int, default=18000,
                        help="Maximum characters in the extra reading panel per card")
    parser.add_argument("--no-investigation", action="store_true")
    parser.add_argument("--no-management", action="store_true")
    parser.add_argument("--no-outcome", action="store_true")
    parser.add_argument("--no-teaching", action="store_true")
    parser.add_argument("--no-differential", action="store_true", help="Do not generate author-discussed differential / pitfall cards")
    parser.add_argument("--no-reasoning", action="store_true", help="Do not generate evidence-quoted author-reasoning cards")
    parser.add_argument("--strict-evidence", action="store_true", help="Export only cards with a direct evidence span and a source link")
    parser.add_argument("--review-jsonl", action="store_true", help="Write a structured human-review queue with evidence spans")
    parser.add_argument("--review-decisions", type=Path, help="Apply human decisions from JSONL keyed by card_id")
    parser.add_argument("--approved-only", action="store_true", help="Export only cards explicitly approved in --review-decisions")
    parser.add_argument("--quality-report", action="store_true", help="Write a CSV audit report")
    parser.add_argument("--asset-report", action="store_true", help="Write a V8 JSONL inventory of JATS figures/tables; does not download or embed media")
    parser.add_argument("--preview", action="store_true", help="Write an HTML preview of the first 50 cards")
    parser.add_argument("--skip-low-confidence-cases", action="store_true",
                        help="Exclude every case with diagnosis confidence below --min-confidence")
    args = parser.parse_args(argv)
    if args.limit < 0 or args.reading_limit < 100:
        parser.error("--limit must be >= 0 and --reading-limit must be >= 100")
    if not 0 <= args.min_confidence <= 1:
        parser.error("--min-confidence must be between 0 and 1")
    if args.approved_only and not args.review_decisions:
        parser.error("--approved-only requires --review-decisions")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        xml_files = discover_xml_files(args.input)
    except (ValueError, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not xml_files:
        print(f"No article.xml files found under {args.input}", file=sys.stderr)
        return 1

    all_cards: list[dict] = []
    reports: list[dict] = []
    media_assets: list[dict] = []
    seen_cases: set[str] = set()
    seen_dois: set[str] = set()
    seen_titles: set[str] = set()
    skipped = 0
    for xml_path in xml_files:
        try:
            case = parse_jats(xml_path)
            if args.asset_report:
                root = ET.parse(xml_path).getroot()
                xml_sha = hashlib.sha256(xml_path.read_bytes()).hexdigest()
                media_assets.extend(extract_jats_media(root, case.case_id, case.source_url, case.license, xml_sha))
            if args.specialty != "auto":
                case.specialty = tagify(args.specialty)
            case_key = case.case_id.lower()
            doi_key = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", (case.doi or "").strip().lower())
            title_key = re.sub(r"[^a-z0-9]+", " ", case.title.lower()).strip()
            if case_key in seen_cases or (doi_key and doi_key in seen_dois) or (title_key and title_key in seen_titles):
                skipped += 1
                continue
            seen_cases.add(case_key)
            if doi_key:
                seen_dois.add(doi_key)
            if title_key:
                seen_titles.add(title_key)
            cards, report = build_cards(
                case,
                min_confidence=args.min_confidence,
                include_management=not args.no_management,
                include_outcome=not args.no_outcome,
                include_investigation=not args.no_investigation,
                include_teaching=not args.no_teaching,
                include_differential=not args.no_differential,
                include_reasoning=not args.no_reasoning,
                reading_limit=args.reading_limit,
            )
            if args.skip_low_confidence_cases and report["confidence"] < args.min_confidence:
                report["quality_flags"] = (report["quality_flags"] + ";case-skipped-low-confidence").strip(";")
                cards = []
            all_cards.extend(cards)
            reports.append(report)
        except (ValueError, OSError, ET.ParseError) as exc:
            skipped += 1
            reports.append({"case_id": xml_path.parent.name, "title": xml_path.name, "diagnosis": "",
                            "diagnosis_source": "", "confidence": 0, "card_count": 0,
                            "card_types": "", "quality_flags": f"parse-error: {exc}",
                            "specialty": args.specialty, "educational_score": 0, "source_url": ""})
            print(f"SKIP {xml_path}: {exc}", file=sys.stderr)

    if args.review_decisions:
        try:
            load_review_decisions(args.review_decisions, all_cards)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    update_case_review_status(reports, all_cards)
    if args.approved_only:
        all_cards = [card for card in all_cards if card.get("review_status") == "approved"]

    if args.strict_evidence:
        before_filter = len(all_cards)
        all_cards = [card for card in all_cards if card.get("evidence_coverage") and card.get("source_url")]
        if len(all_cards) < before_filter:
            retained_by_case: dict[str, list[dict]] = {}
            for card in all_cards:
                retained_by_case.setdefault(str(card.get("case_id", "")), []).append(card)
            for report in reports:
                case_id = str(report.get("case_id", ""))
                retained = retained_by_case.get(case_id, [])
                previous_count = int(report.get("card_count", 0) or 0)
                if len(retained) < previous_count:
                    report["quality_flags"] = ";".join(filter(None, [report.get("quality_flags", ""), "strict-evidence-filtered-cards"]))
                report["card_count"] = len(retained)
                report["card_types"] = ";".join(str(card.get("card_type", "")) for card in retained)
                evidence_count = sum(bool(card.get("evidence_spans")) for card in retained)
                report["evidence_coverage"] = f"{evidence_count}/{len(retained)}" if retained else "0/0"
    all_cards = apply_card_limit(all_cards, reports, args.limit)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    outputs: list[str] = []
    if args.asset_report:
        asset_path = args.output.with_name(args.output.name + "_assets.jsonl")
        with asset_path.open("w", encoding="utf-8") as handle:
            for asset in media_assets:
                handle.write(json.dumps(asset, ensure_ascii=False, sort_keys=True) + "\n")
        outputs.append(str(asset_path))
    # Treat --output as a basename, even when the basename itself contains dots.
    tsv_path = args.output.with_name(args.output.name + ".tsv")
    apkg_path = args.output.with_name(args.output.name + ".apkg")
    if args.format in {"tsv", "both"}:
        write_tsv(tsv_path, all_cards)
        outputs.append(str(tsv_path))
    if args.format in {"apkg", "both"}:
        try:
            write_apkg(apkg_path, all_cards, args.deck)
            outputs.append(str(apkg_path))
        except RuntimeError as exc:
            if args.format == "apkg":
                print(str(exc), file=sys.stderr)
                return 2
            print(f"WARNING: {exc}; TSV was still generated.", file=sys.stderr)

    if args.quality_report:
        report_path = args.output.with_name(args.output.name + "_quality.csv")
        columns = ["case_id", "title", "diagnosis", "diagnosis_source", "confidence", "diagnosis_rationale",
                   "diagnosis_evidence_sentence", "diagnosis_evidence_section", "assertion_certainty", "assertion_temporality", "assertion_experiencer",
                   "rejected_diagnosis_candidates", "card_count", "card_types", "quality_flags", "review_status", "review_reasons", "evidence_coverage", "specialty", "educational_score", "source_url"]
        with report_path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(reports)
        outputs.append(str(report_path))

    if args.review_jsonl:
        review_path = args.output.with_name(args.output.name + "_review.jsonl")
        review_rows = []
        for card in all_cards:
            review_rows.append({
                "card_id": card["card_id"], "case_id": card["case_id"], "title": card["title"], "specialty": card["specialty"],
                "card_type": card["card_type"], "review_status": card["review_status"],
                "reviewer": card.get("reviewer", ""), "reviewed_at": card.get("reviewed_at", ""),
                "review_notes": card.get("review_notes", ""),
                "front": card["front"], "back": card["back"], "source_url": card["source_url"],
                "evidence_spans": card["evidence_spans"],
            })
        review_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in review_rows), encoding="utf-8")
        outputs.append(str(review_path))

    if args.preview:
        preview_path = args.output.with_name(args.output.name + "_preview.html")
        write_preview(preview_path, all_cards)
        outputs.append(str(preview_path))

    card_counts: dict[str, int] = {}
    for card in all_cards:
        card_counts[card["card_type"]] = card_counts.get(card["card_type"], 0) + 1
    print(f"Parsed {len(reports)} reports; skipped {skipped}; generated {len(all_cards)} cards")
    if card_counts:
        print("Card types: " + ", ".join(f"{kind}={count}" for kind, count in sorted(card_counts.items())))
    for output in outputs:
        print(f"Wrote: {output}")
    if not outputs:
        print("No output generated.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
