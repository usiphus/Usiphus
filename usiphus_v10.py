#!/usr/bin/env python3
"""Usiphus V10 — Specialty Curriculum Mapping & Coverage Intelligence.

Adds a transparent curriculum layer to the V9 exam-card pipeline. It maps
cards to specialty competencies, reports coverage and gaps, and distinguishes
explicit faculty mappings from heuristic suggestions. Suggestions never count
as verified coverage until a human adds an explicit competency ID.

Curriculum JSON:
{"specialty":"urology","competencies":[
 {"id":"URO-01","title":"Assess acute urinary retention","description":"Recognize and initially manage retention","domain":"Emergency urology","level":"apply","required":true,"weight":2,"learning_objectives":["Identify red flags"]}
]}

Cards JSON: a list or {"cards":[...]}; each card requires card_id/id and
prompt/question. Optional fields: answer, specialty, tags, learning_objective,
competency_ids (or curriculum_competencies).

Examples:
 python usiphus_v10.py --cards cards.json --curriculum urology.json --output build
 python usiphus_v10.py --self-test

Clinical limitation: this measures mapping metadata, not clinical truth,
educational effectiveness, or learner competence. Faculty review is required.
"""
from __future__ import annotations
import argparse
import csv
import json
import logging
import math
import re
from datetime import datetime
import tempfile
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

VERSION = "10.0.0"
REPORT_SCHEMA_VERSION = 1
TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
STOP_WORDS = {"a","an","and","are","as","at","be","by","describe","do","does","for","from","how","in","into","is","it","list","name","of","on","or","the","to","what","when","which","with"}
VALID_LEVELS = {"remember","understand","apply","analyze","evaluate","create"}
LOG = logging.getLogger("usiphus.v10")


@dataclass(frozen=True)
class Competency:
    competency_id: str
    title: str
    description: str = ""
    domain: str = "Uncategorized"
    level: str = "understand"
    required: bool = True
    weight: float = 1.0
    learning_objectives: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Card:
    card_id: str
    specialty: str
    prompt: str
    answer: str
    learning_objective: str = ""
    tags: tuple[str, ...] = ()
    explicit_competency_ids: tuple[str, ...] = ()
    faculty_approvals: tuple[FacultyApproval, ...] = ()


@dataclass(frozen=True)
class FacultyApproval:
    competency_id: str
    reviewer: str
    reviewed_at: str


@dataclass(frozen=True)
class MappingCandidate:
    card_id: str
    competency_id: str
    score: float
    method: str
    status: str
    rationale: str
    reviewer: str = ""
    reviewed_at: str = ""


@dataclass
class CurriculumReport:
    schema_version: int = REPORT_SCHEMA_VERSION
    usiphus_version: str = VERSION
    specialty: str = ""
    cards_evaluated: int = 0
    competencies_evaluated: int = 0
    required_competencies: int = 0
    verified_covered_competencies: int = 0
    suggested_competencies: int = 0
    uncovered_required_competencies: int = 0
    verified_weighted_coverage: float = 0.0
    candidate_weighted_coverage: float = 0.0
    card_mapping_rate: float = 0.0
    domain_summary: list[dict[str, Any]] = field(default_factory=list)
    uncovered_competency_ids: list[str] = field(default_factory=list)
    unmapped_card_ids: list[str] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, (list, tuple, set)):
        raise ValueError("Expected a string or list of strings")
    result, seen = [], set()
    for item in values:
        text = clean(item)
        key = text.casefold()
        if text and key not in seen:
            result.append(text)
            seen.add(key)
    return tuple(result)


def tokens(value: str) -> set[str]:
    return {t.casefold() for t in TOKEN_RE.findall(value) if len(t) > 1 and t.casefold() not in STOP_WORDS}


def similarity(left: str, right: str) -> float:
    """Jaccard similarity; candidate generation only, not semantic understanding."""
    a, b = tokens(left), tokens(right)
    return len(a & b) / len(a | b) if a and b else 0.0


def load_json(path: Path) -> Any:
    if not path.exists() or not path.is_file():
        raise ValueError(f"Input file does not exist or is not a file: {path}")
    if path.stat().st_size > 50 * 1024 * 1024:
        raise ValueError(f"Input file exceeds 50 MiB: {path}")
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def load_faculty_approvals(raw: Any, card_id: str) -> tuple[FacultyApproval, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"faculty_approvals for card {card_id} must be a list")
    approvals, seen = [], set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"Faculty approval {index} for card {card_id} must be an object")
        competency_id = clean(item.get("competency_id"))
        reviewer = clean(item.get("reviewer"))
        reviewed_at = clean(item.get("reviewed_at"))
        status = clean(item.get("status")).casefold()
        if not competency_id or not reviewer or not reviewed_at or status != "approved":
            raise ValueError(f"Faculty approval {index} for card {card_id} requires competency_id, status='approved', reviewer, and timezone-aware reviewed_at")
        try:
            parsed = datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"Invalid reviewed_at timestamp for card {card_id}") from None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError(f"reviewed_at must include a timezone for card {card_id}")
        key = competency_id.casefold()
        if key in seen:
            raise ValueError(f"Duplicate faculty approval for {competency_id} on card {card_id}")
        seen.add(key)
        approvals.append(FacultyApproval(competency_id, reviewer, reviewed_at))
    return tuple(approvals)


def validate_specialty_alignment(cards: Sequence[Card], specialty: str) -> None:
    normalized = clean(specialty).casefold()
    if not normalized:
        raise ValueError("Curriculum specialty is required")
    mismatches = [f"{card.card_id} ({card.specialty or 'missing'})" for card in cards
                  if card.specialty.casefold() != normalized]
    if mismatches:
        raise ValueError(f"Card specialty must exactly match curriculum specialty '{normalized}'. Mismatched or missing cards: {', '.join(mismatches[:10])}")


def validate_threshold(threshold: float) -> None:
    if not math.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
        raise ValueError("min-match-score must be a finite number between 0 and 1")


def load_cards(path: Path, default_specialty: str = "") -> list[Card]:
    payload = load_json(path)
    records = payload.get("cards") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError("Cards JSON must be a list or contain a cards list")
    cards, seen = [], set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(f"Card record {index} must be an object")
        cid = clean(record.get("card_id") or record.get("id"))
        prompt = clean(record.get("prompt") or record.get("question") or record.get("task"))
        if not cid or not prompt:
            raise ValueError(f"Card record {index} requires card_id/id and prompt/question")
        if cid.casefold() in seen:
            raise ValueError(f"Duplicate card ID: {cid}")
        seen.add(cid.casefold())
        cards.append(Card(
            cid, clean(record.get("specialty") or default_specialty).casefold(),
            prompt, clean(record.get("answer") or record.get("explanation") or record.get("checklist")),
            clean(record.get("learning_objective") or record.get("objective")),
            string_tuple(record.get("tags")),
            string_tuple(record.get("competency_ids") or record.get("curriculum_competencies")),
            load_faculty_approvals(record.get("faculty_approvals"), cid),
        ))
    return cards


def load_competencies(path: Path) -> tuple[str, list[Competency]]:
    payload = load_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("competencies"), list):
        raise ValueError("Curriculum JSON must be an object with a competencies list")
    specialty, result, seen = clean(payload.get("specialty")).casefold(), [], set()
    if not specialty:
        raise ValueError("Curriculum must declare a non-empty specialty")
    for index, item in enumerate(payload["competencies"]):
        if not isinstance(item, dict):
            raise ValueError(f"Competency record {index} must be an object")
        cid, title = clean(item.get("competency_id") or item.get("id")), clean(item.get("title") or item.get("name"))
        if not cid or not title:
            raise ValueError(f"Competency record {index} requires id and title")
        if cid.casefold() in seen:
            raise ValueError(f"Duplicate competency ID: {cid}")
        seen.add(cid.casefold())
        try:
            weight = float(item.get("weight", 1.0))
        except (TypeError, ValueError):
            raise ValueError(f"Invalid weight for competency {cid}") from None
        if not math.isfinite(weight) or weight <= 0:
            raise ValueError(f"Competency weight must be finite and positive: {cid}")
        required_raw = item.get("required", True)
        if not isinstance(required_raw, bool):
            raise ValueError(f"'required' must be a boolean for competency {cid}")
        level = clean(item.get("level") or "understand").casefold()
        if level not in VALID_LEVELS:
            raise ValueError(f"Invalid Bloom level '{level}' for competency {cid}")
        result.append(Competency(
            cid, title, clean(item.get("description")), clean(item.get("domain") or "Uncategorized"),
            level, required_raw, weight,
            string_tuple(item.get("learning_objectives") or item.get("objectives")),
            string_tuple(item.get("tags")),
        ))
    if not result:
        raise ValueError("Curriculum must contain at least one competency")
    return specialty, result


def card_text(card: Card) -> str:
    return " ".join((card.prompt, card.answer, card.learning_objective, *card.tags))


def competency_text(item: Competency) -> str:
    return " ".join((item.title, item.description, *item.learning_objectives, *item.tags, item.domain))


def build_candidates(cards: Sequence[Card], competencies: Sequence[Competency], threshold: float = 0.12) -> list[MappingCandidate]:
    """Unreviewed explicit links are pending; only auditable approvals are verified."""
    validate_threshold(threshold)
    by_id = {c.competency_id.casefold(): c for c in competencies}
    rows = []
    for card in cards:
        explicit = {raw_id.casefold(): raw_id for raw_id in card.explicit_competency_ids}
        approvals = {item.competency_id.casefold(): item for item in card.faculty_approvals}
        for key, raw_id in explicit.items():
            if key not in by_id:
                rows.append(MappingCandidate(card.card_id, raw_id, 1.0, "explicit", "invalid", "Referenced ID is absent from this curriculum"))
                continue
            approval = approvals.get(key)
            if approval:
                rows.append(MappingCandidate(card.card_id, by_id[key].competency_id, 1.0, "faculty-approved", "verified",
                    "Explicit mapping approved by a named faculty reviewer", approval.reviewer, approval.reviewed_at))
            else:
                rows.append(MappingCandidate(card.card_id, by_id[key].competency_id, 1.0, "explicit", "pending",
                    "Explicit mapping supplied without auditable faculty approval"))
        for key, approval in approvals.items():
            if key not in by_id:
                rows.append(MappingCandidate(card.card_id, approval.competency_id, 1.0, "faculty-approved", "invalid",
                    "Approved ID is absent from this curriculum", approval.reviewer, approval.reviewed_at))
            elif key not in explicit:
                rows.append(MappingCandidate(card.card_id, by_id[key].competency_id, 1.0, "faculty-approved", "verified",
                    "Faculty-approved mapping", approval.reviewer, approval.reviewed_at))
        excluded = set(explicit) | set(approvals)
        text = card_text(card)
        for competency in competencies:
            key = competency.competency_id.casefold()
            if key in excluded:
                continue
            score = similarity(text, competency_text(competency))
            shared = {x.casefold() for x in card.tags} & {x.casefold() for x in competency.tags}
            if shared:
                score = max(score, min(0.99, 0.5 + 0.1 * len(shared)))
            if score >= threshold:
                rows.append(MappingCandidate(card.card_id, competency.competency_id, round(score, 4),
                    "lexical", "suggested", "Lexical candidate only; faculty confirmation required"))
    return sorted(rows, key=lambda x: (x.status != "verified", -x.score, x.card_id, x.competency_id))

def build_report(cards: Sequence[Card], competencies: Sequence[Competency], candidates: Sequence[MappingCandidate], specialty: str) -> CurriculumReport:
    validate_specialty_alignment(cards, specialty)
    verified_comp, suggested_comp, verified_card, suggested_card = (defaultdict(set) for _ in range(4))
    for row in candidates:
        if row.status == "verified":
            verified_comp[row.competency_id].add(row.card_id)
            verified_card[row.card_id].add(row.competency_id)
        elif row.status in {"suggested", "pending"}:
            suggested_comp[row.competency_id].add(row.card_id)
            suggested_card[row.card_id].add(row.competency_id)
    required = [c for c in competencies if c.required]
    denominator = sum(c.weight for c in required) or 1.0
    verified_weight = sum(c.weight for c in required if verified_comp[c.competency_id])
    candidate_weight = sum(c.weight for c in required if verified_comp[c.competency_id] or suggested_comp[c.competency_id])
    uncovered = [c.competency_id for c in required if not verified_comp[c.competency_id]]
    domains = defaultdict(list)
    for c in competencies:
        domains[c.domain].append(c)
    domain_rows = []
    for domain, items in sorted(domains.items()):
        req = [c for c in items if c.required]
        verified = sum(bool(verified_comp[c.competency_id]) for c in req)
        suggested = sum(bool(suggested_comp[c.competency_id]) and not bool(verified_comp[c.competency_id]) for c in req)
        domain_rows.append({
            "domain": domain, "competencies": len(items), "required": len(req),
            "verified_covered": verified, "suggested_only": suggested,
            "uncovered_required": len(req) - verified,
            "verified_coverage_percent": round(100 * verified / len(req), 1) if req else 100.0,
        })
    mapped = sum(bool(verified_card[c.card_id] or suggested_card[c.card_id]) for c in cards)
    warnings = [
        "Coverage measures mapping metadata, not clinical accuracy or learner competence.",
        "Explicit competency_ids without a matching faculty_approvals record remain pending and do not count as verified.",
        "Only faculty_approvals entries with reviewer, timezone-aware reviewed_at, and status=approved count as verified.",
    ]
    return CurriculumReport(
        specialty=specialty, cards_evaluated=len(cards), competencies_evaluated=len(competencies),
        required_competencies=len(required),
        verified_covered_competencies=sum(bool(verified_comp[c.competency_id]) for c in required),
        suggested_competencies=sum(bool(suggested_comp[c.competency_id]) and not bool(verified_comp[c.competency_id]) for c in required),
        uncovered_required_competencies=len(uncovered),
        verified_weighted_coverage=round(100 * verified_weight / denominator, 2),
        candidate_weighted_coverage=round(100 * candidate_weight / denominator, 2),
        card_mapping_rate=round(100 * mapped / len(cards), 2) if cards else 0.0,
        domain_summary=domain_rows, uncovered_competency_ids=uncovered,
        unmapped_card_ids=[c.card_id for c in cards if not verified_card[c.card_id] and not suggested_card[c.card_id]],
        candidates=[asdict(x) for x in candidates], warnings=warnings,
    )


def atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temp = handle.name
            handle.write(content)
            handle.flush()
        Path(temp).replace(path)
    finally:
        if temp and Path(temp).exists():
            Path(temp).unlink()


def render_markdown(report: CurriculumReport) -> str:
    lines = [
        f"# Usiphus V10 Curriculum Coverage — {report.specialty or 'General'}", "",
        f"- Cards evaluated: **{report.cards_evaluated}**",
        f"- Competencies: **{report.competencies_evaluated}** ({report.required_competencies} required)",
        f"- Verified required competencies: **{report.verified_covered_competencies}/{report.required_competencies}**",
        f"- Verified weighted coverage: **{report.verified_weighted_coverage:.2f}%**",
        f"- Candidate weighted coverage (includes unapproved suggestions): **{report.candidate_weighted_coverage:.2f}%**",
        f"- Cards with any mapping: **{report.card_mapping_rate:.2f}%**", "",
        "## Coverage by domain", "",
        "| Domain | Required | Verified | Suggested only | Uncovered | Verified coverage |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in report.domain_summary:
        lines.append(f"| {row['domain']} | {row['required']} | {row['verified_covered']} | {row['suggested_only']} | {row['uncovered_required']} | {row['verified_coverage_percent']:.1f}% |")
    lines.extend(["", "## Required competencies needing attention", ""])
    lines.extend(f"- {cid}" for cid in report.uncovered_competency_ids) if report.uncovered_competency_ids else lines.append("None.")
    lines.extend(["", "## Unmapped cards", ""])
    lines.extend(f"- {cid}" for cid in report.unmapped_card_ids) if report.unmapped_card_ids else lines.append("None.")
    lines.extend(["", "## Interpretation", "", "Suggested-only competencies remain uncovered until faculty verification."])
    lines.extend(f"- {warning}" for warning in report.warnings)
    return "\n".join(lines) + "\n"


def write_outputs(report: CurriculumReport, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    atomic_text(output / "curriculum_coverage.json", json.dumps(asdict(report), indent=2, ensure_ascii=False))
    atomic_text(output / "curriculum_coverage.md", render_markdown(report))
    csv_path = output / "curriculum_mapping_matrix.csv"
    fd, temp_name = tempfile.mkstemp(prefix=".mapping-", suffix=".csv", dir=output)
    try:
        with open(fd, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["card_id", "competency_id", "score", "method", "status", "rationale", "reviewer", "reviewed_at"])
            for row in report.candidates:
                writer.writerow([row["card_id"], row["competency_id"], row["score"], row["method"], row["status"], row["rationale"], row.get("reviewer", ""), row.get("reviewed_at", "")])
            handle.flush()
        Path(temp_name).replace(csv_path)
    finally:
        Path(temp_name).unlink(missing_ok=True)
    atomic_text(output / "curriculum_manifest.json", json.dumps({
        "usiphus_version": VERSION, "report_schema_version": REPORT_SCHEMA_VERSION,
        "cards": report.cards_evaluated, "competencies": report.competencies_evaluated,
        "report": "curriculum_coverage.json",
    }, indent=2))


def run(cards_path: Path, curriculum_path: Path, output: Path, threshold: float, default_specialty: str = "") -> int:
    cards = load_cards(cards_path, default_specialty=default_specialty)
    specialty, competencies = load_competencies(curriculum_path)
    validate_specialty_alignment(cards, specialty)
    validate_threshold(threshold)
    candidates = build_candidates(cards, competencies, threshold)
    report = build_report(cards, competencies, candidates, specialty)
    write_outputs(report, output)
    LOG.info("V10: %d/%d required competencies verified; weighted coverage %.2f%%",
             report.verified_covered_competencies, report.required_competencies, report.verified_weighted_coverage)
    return 0


def run_self_test() -> int:
    import unittest
    class CurriculumTests(unittest.TestCase):
        def setUp(self):
            self.cards = [
                Card("c1","urology","Assess acute urinary retention","Initial assessment and bladder drainage.","Recognize retention",("emergency",),("U1",)),
                Card("c2","urology","Explain renal colic","Pain from ureteric obstruction.",tags=("stone",)),
            ]
            self.comps = [
                Competency("U1","Acute urinary retention","Assess and manage retention","Emergency","apply",True,2.0,("Initial management",),("emergency",)),
                Competency("U2","Renal colic","Recognize and assess ureteric stones","Stone disease","understand",True,1.0,(),("stone",)),
                Competency("U3","Testicular torsion","Recognize time-critical torsion","Emergency","analyze",True,1.0),
            ]
        def test_explicit_link_verified(self):
            row = next(x for x in build_candidates(self.cards,self.comps) if x.card_id=="c1" and x.competency_id=="U1")
            self.assertEqual((row.status,row.method),("pending","explicit"))
        def test_faculty_approval_is_verified_with_provenance(self):
            approved = Card("approved","urology","Assess retention","",explicit_competency_ids=("U1",),
                faculty_approvals=(FacultyApproval("U1","Faculty Reviewer","2026-10-31T12:00:00Z"),))
            row = next(x for x in build_candidates([approved],self.comps) if x.competency_id=="U1")
            self.assertEqual(row.status,"verified")
            self.assertEqual(row.reviewer,"Faculty Reviewer")
            self.assertEqual(row.reviewed_at,"2026-10-31T12:00:00Z")
        def test_unapproved_explicit_link_is_not_verified(self):
            row = next(x for x in build_candidates(self.cards,self.comps) if x.card_id=="c1" and x.competency_id=="U1")
            self.assertEqual(row.status,"pending")
        def test_cross_specialty_rejected(self):
            mixed = self.cards + [Card("wrong","ophthalmology","Assess retina","")]
            with self.assertRaises(ValueError):
                build_report(mixed,self.comps,build_candidates(mixed,self.comps),"urology")
        def test_empty_curriculum_specialty_rejected(self):
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/"curriculum.json"
                path.write_text(json.dumps({"competencies":[{"id":"X","title":"Example"}]}),encoding="utf-8")
                with self.assertRaises(ValueError): load_competencies(path)
        def test_invalid_threshold_rejected(self):
            for value in (-0.1, 1.1, float("nan"), float("inf")):
                with self.assertRaises(ValueError): build_candidates(self.cards,self.comps,value)
        def test_approval_requires_reviewer_and_timezone(self):
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/"cards.json"
                path.write_text(json.dumps([{"id":"x","specialty":"urology","prompt":"Q","competency_ids":["U1"],"faculty_approvals":[{"competency_id":"U1","status":"approved","reviewer":"Faculty","reviewed_at":"2026-10-31T12:00:00"}]}]),encoding="utf-8")
                with self.assertRaises(ValueError): load_cards(path)
        def test_lexical_link_is_suggestion(self):
            rows = build_candidates(self.cards,self.comps,0.01)
            row = next(x for x in rows if x.card_id=="c2" and x.competency_id=="U2")
            self.assertEqual(row.status,"suggested")
        def test_uncovered_required_competency(self):
            report = build_report(self.cards,self.comps,build_candidates(self.cards,self.comps),"urology")
            self.assertIn("U3",report.uncovered_competency_ids)
            self.assertEqual(report.verified_covered_competencies,1)
        def test_weighted_coverage(self):
            report = build_report(self.cards,self.comps,build_candidates(self.cards,self.comps),"urology")
            self.assertEqual(report.verified_weighted_coverage,50.0)
        def test_unknown_explicit_id_invalid(self):
            card = Card("x","urology","Question","Answer",explicit_competency_ids=("MISSING",))
            row = build_candidates([card],self.comps)[0]
            self.assertEqual(row.status,"invalid")
        def test_optional_competencies_excluded(self):
            comps = self.comps + [Competency("U4","Optional",required=False)]
            report = build_report(self.cards,comps,build_candidates(self.cards,comps),"urology")
            self.assertEqual(report.required_competencies,3)
        def test_similarity_bounds(self):
            self.assertEqual(similarity("renal colic","renal colic"),1.0)
            self.assertEqual(similarity("","renal colic"),0.0)
        def test_report_version(self):
            report = build_report(self.cards,self.comps,build_candidates(self.cards,self.comps),"urology")
            self.assertEqual(report.usiphus_version,VERSION)
        def test_duplicate_card_ids_rejected(self):
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/"cards.json"
                path.write_text(json.dumps([{"id":"x","prompt":"Q"},{"id":"X","prompt":"Q2"}]),encoding="utf-8")
                with self.assertRaises(ValueError): load_cards(path)
        def test_non_finite_weight_rejected(self):
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/"curriculum.json"
                path.write_text(json.dumps({"specialty":"urology","competencies":[{"id":"X","title":"Example","weight":float("nan")}]}),encoding="utf-8")
                with self.assertRaises(ValueError): load_competencies(path)
        def test_required_must_be_boolean(self):
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/"curriculum.json"
                path.write_text(json.dumps({"specialty":"urology","competencies":[{"id":"X","title":"Example","required":"false"}]}),encoding="utf-8")
                with self.assertRaises(ValueError): load_competencies(path)
        def test_suggested_is_still_uncovered(self):
            comps=[Competency("X","Renal colic",required=True)]
            cards=[Card("c","urology","Renal colic","",tags=("stone",))]
            candidates=[MappingCandidate("c","X",0.8,"lexical","suggested","review")]
            report=build_report(cards,comps,candidates,"urology")
            self.assertEqual(report.uncovered_required_competencies,1)
            self.assertEqual(report.uncovered_competency_ids,["X"])
        def test_output_files(self):
            with tempfile.TemporaryDirectory() as directory:
                report=build_report(self.cards,self.comps,build_candidates(self.cards,self.comps),"urology")
                out=Path(directory)
                write_outputs(report,out)
                self.assertTrue((out/"curriculum_coverage.json").exists())
                self.assertTrue((out/"curriculum_mapping_matrix.csv").exists())
                self.assertIn("Verified weighted coverage",(out/"curriculum_coverage.md").read_text(encoding="utf-8"))
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(CurriculumTests))
    return 0 if result.wasSuccessful() else 2


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser=argparse.ArgumentParser(description="Usiphus V10 specialty curriculum mapping engine")
    parser.add_argument("--cards",help="V9-compatible JSON cards file")
    parser.add_argument("--curriculum",help="Specialty curriculum JSON file")
    parser.add_argument("--specialty",help="Default specialty for cards without specialty metadata")
    parser.add_argument("--output",default="./build",help="Output directory")
    parser.add_argument("--min-match-score",type=float,default=0.12,help="Lexical candidate threshold (0–1)")
    parser.add_argument("--self-test",action="store_true",help="Run built-in tests")
    parser.add_argument("--verbose",action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args=parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    if args.self_test:
        return run_self_test()
    if not args.cards or not args.curriculum:
        LOG.error("--cards and --curriculum are required unless --self-test is used")
        return 2
    if not math.isfinite(args.min_match_score) or not 0 <= args.min_match_score <= 1:
        LOG.error("--min-match-score must be between 0 and 1")
        return 2
    try:
        return run(Path(args.cards),Path(args.curriculum),Path(args.output),args.min_match_score,args.specialty or "")
    except (ValueError,OSError,json.JSONDecodeError) as exc:
        LOG.error("%s",exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
