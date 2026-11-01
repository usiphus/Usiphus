#!/usr/bin/env python3
"""Usiphus V19 — local-first assessment, OSCE and challenge studio.

Deterministic structural checks create human-review flags; they do not decide
clinical truth, item validity, station validity, learner competence or fairness.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

VERSION = "19.0.0"
MAX_BYTES = 25 * 1024 * 1024
STATES = {"draft", "in_review", "approved", "retired"}
LEVELS = {"remember", "understand", "apply", "analyze", "evaluate", "create"}

class V19Error(ValueError):
    """Invalid V19 input or policy."""

def text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())

def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

def sha(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()

def read_json(path: Path) -> Any:
    path = Path(path)
    if not path.is_file():
        raise V19Error(f"Input file not found: {path}")
    if path.stat().st_size > MAX_BYTES:
        raise V19Error(f"Input exceeds {MAX_BYTES // (1024 * 1024)} MiB: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise V19Error(f"Invalid JSON in {path}: {exc}") from exc

def write_text(value: str, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)

def write_json(value: Any, path: Path) -> None:
    write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n", path)

def rows(payload: Any, key: str) -> list[dict[str, Any]]:
    data = payload.get(key, []) if isinstance(payload, dict) else None
    if not isinstance(data, list) or any(not isinstance(x, dict) for x in data):
        raise V19Error(f"Input field '{key}' must be a list of objects")
    return data

def valid_http_url(value: Any) -> bool:
    try:
        p = urlsplit(text(value))
        return p.scheme in {"http", "https"} and bool(p.hostname) and not p.username and not p.password
    except ValueError:
        return False

def flag(kind: str, artifact_id: str, severity: str, message: str) -> dict[str, str]:
    return {"kind": kind, "artifact_id": artifact_id, "severity": severity, "message": message}

def check_unique(items: list[dict[str, Any]], id_key: str, kind: str,
                 issues: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(items):
        ident = text(item.get(id_key))
        if not ident:
            issues.append(flag(kind, f"row-{index+1}", "error", f"{id_key} is required"))
        elif ident in seen:
            issues.append(flag(kind, ident, "error", f"duplicate {id_key}"))
        else:
            seen[ident] = item
        state = text(item.get("status") or "draft")
        if state not in STATES:
            issues.append(flag(kind, ident or f"row-{index+1}", "error", f"unsupported status: {state}"))
        if state == "approved" and not text(item.get("reviewer_label")):
            issues.append(flag(kind, ident, "warning", "approved status has no reviewer_label"))
        if state == "approved" and not text(item.get("reviewed_at")):
            issues.append(flag(kind, ident, "warning", "approved status has no reviewed_at timestamp"))
    return seen

def check_blueprint(blueprint: list[dict[str, Any]], issues: list[dict[str, str]]) -> None:
    check_unique(blueprint, "objective_id", "blueprint", issues)
    for obj in blueprint:
        oid = text(obj.get("objective_id"))
        if not text(obj.get("specialty")):
            issues.append(flag("blueprint", oid, "error", "specialty is required"))
        if not text(obj.get("competency_id")):
            issues.append(flag("blueprint", oid, "error", "competency_id is required"))
        if text(obj.get("cognitive_level")).casefold() not in LEVELS:
            issues.append(flag("blueprint", oid, "warning", "cognitive_level is missing or outside the supported taxonomy"))
        if not text(obj.get("assessment_method")):
            issues.append(flag("blueprint", oid, "warning", "assessment_method is missing"))

def check_mcqs(items: list[dict[str, Any]], blueprint_ids: set[str],
               issues: list[dict[str, str]], duplicates: list[dict[str, Any]]) -> None:
    check_unique(items, "item_id", "mcq", issues)
    prompts: dict[str, list[str]] = defaultdict(list)
    for item in items:
        ident = text(item.get("item_id"))
        stem = text(item.get("stem"))
        if not stem:
            issues.append(flag("mcq", ident, "error", "stem is required"))
        if len(stem.split()) < 8:
            issues.append(flag("mcq", ident, "warning", "very short stem; check whether sufficient context is supplied"))
        lead = text(item.get("lead_in"))
        if not lead:
            issues.append(flag("mcq", ident, "error", "focused lead_in is required"))
        elif not lead.endswith("?"):
            issues.append(flag("mcq", ident, "warning", "lead_in should usually be a direct, focused question"))
        opts = item.get("options")
        if not isinstance(opts, list) or len(opts) < 3 or any(not isinstance(o, dict) for o in opts):
            issues.append(flag("mcq", ident, "error", "options must contain at least three option objects"))
            opts = []
        labels = [text(o.get("label")) for o in opts]
        bodies = [text(o.get("text")) for o in opts]
        if len(set(labels)) != len(labels) or any(not x for x in labels):
            issues.append(flag("mcq", ident, "error", "option labels must be present and unique"))
        if any(not x for x in bodies):
            issues.append(flag("mcq", ident, "error", "every option needs non-empty text"))
        correct = [o for o in opts if o.get("correct") is True]
        if len(correct) != 1:
            issues.append(flag("mcq", ident, "error", "exactly one option must be marked correct for single-best-answer format"))
        if len({x.casefold() for x in bodies if x}) != len([x for x in bodies if x]):
            issues.append(flag("mcq", ident, "warning", "duplicate option text candidate"))
        if not text(item.get("answer_rationale")):
            issues.append(flag("mcq", ident, "warning", "answer rationale is missing"))
        wrong = [o for o in opts if o.get("correct") is not True]
        if wrong and any(not text(o.get("rationale")) for o in wrong):
            issues.append(flag("mcq", ident, "warning", "one or more distractors lack rationale"))
        if not text(item.get("evidence_ref")):
            issues.append(flag("mcq", ident, "warning", "clinical evidence_ref is missing"))
        for field in ("objective_ids", "competency_ids"):
            values = item.get(field, [])
            if not isinstance(values, list):
                issues.append(flag("mcq", ident, "error", f"{field} must be a list"))
                continue
            if field == "objective_ids":
                for oid in values:
                    if text(oid) not in blueprint_ids:
                        issues.append(flag("mcq", ident, "warning", f"objective not found in blueprint: {oid}"))
        norm = re.sub(r"\s+", " ", stem.casefold()).strip()
        if norm:
            prompts[norm].append(ident)
    for prompt, ids in prompts.items():
        if len(ids) > 1:
            duplicates.append({"normalized_stem_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                               "candidate_item_ids": sorted(ids),
                               "decision": "human_review_required"})

def check_osce(stations: list[dict[str, Any]], blueprint_ids: set[str],
               issues: list[dict[str, str]]) -> None:
    check_unique(stations, "station_id", "osce", issues)
    required = ("candidate_instructions", "role_player_brief", "setup_equipment",
                "examiner_instructions", "global_rating_scale", "marking_rubric")
    for station in stations:
        ident = text(station.get("station_id"))
        for field in required:
            if not text(station.get(field)):
                issues.append(flag("osce", ident, "error", f"{field} is required"))
        duration = station.get("duration_minutes")
        if isinstance(duration, bool) or not isinstance(duration, int) or not 1 <= duration <= 60:
            issues.append(flag("osce", ident, "error", "duration_minutes must be an integer from 1 to 60"))
        checklist = station.get("checklist")
        if not isinstance(checklist, list) or not checklist:
            issues.append(flag("osce", ident, "error", "checklist must contain at least one criterion"))
        else:
            for index, criterion in enumerate(checklist):
                if not isinstance(criterion, dict) or not text(criterion.get("criterion")):
                    issues.append(flag("osce", ident, "error", f"checklist criterion {index+1} is incomplete"))
        if not isinstance(station.get("critical_actions", []), list):
            issues.append(flag("osce", ident, "error", "critical_actions must be a list"))
        for oid in station.get("objective_ids", []) if isinstance(station.get("objective_ids", []), list) else []:
            if text(oid) not in blueprint_ids:
                issues.append(flag("osce", ident, "warning", f"objective not found in blueprint: {oid}"))
        if station.get("media") and not text(station.get("media_license")):
            issues.append(flag("osce", ident, "warning", "media supplied without documented media_license/provenance"))

def check_challenges(challenges: list[dict[str, Any]], blueprint_ids: set[str],
                     issues: list[dict[str, str]]) -> None:
    check_unique(challenges, "challenge_id", "challenge", issues)
    for case in challenges:
        ident = text(case.get("challenge_id"))
        if not text(case.get("case_stem")):
            issues.append(flag("challenge", ident, "error", "case_stem is required"))
        clues = case.get("clues")
        if not isinstance(clues, list) or not clues or any(not text(x) for x in clues):
            issues.append(flag("challenge", ident, "error", "clues must be a non-empty ordered list of text"))
        answers = case.get("accepted_answers")
        if not isinstance(answers, list) or not any(text(x) for x in answers):
            issues.append(flag("challenge", ident, "error", "accepted_answers must contain at least one answer"))
        if not text(case.get("explanation")):
            issues.append(flag("challenge", ident, "error", "explanation is required"))
        if not text(case.get("differential_rationale")):
            issues.append(flag("challenge", ident, "warning", "differential_rationale is missing"))
        if not text(case.get("provenance")):
            issues.append(flag("challenge", ident, "warning", "case provenance/attribution is missing"))
        if case.get("media") and (not text(case.get("media_license")) or not text(case.get("media_provenance"))):
            issues.append(flag("challenge", ident, "error", "media requires explicit license and provenance"))
        for oid in case.get("objective_ids", []) if isinstance(case.get("objective_ids", []), list) else []:
            if text(oid) not in blueprint_ids:
                issues.append(flag("challenge", ident, "warning", f"objective not found in blueprint: {oid}"))

def check_directory(entries: list[dict[str, Any]], issues: list[dict[str, str]]) -> None:
    check_unique(entries, "entry_id", "directory", issues)
    for entry in entries:
        ident = text(entry.get("entry_id"))
        if not valid_http_url(entry.get("url")):
            issues.append(flag("directory", ident, "error", "url must be a safe HTTP(S) URL without embedded credentials"))
        if not text(entry.get("provider")):
            issues.append(flag("directory", ident, "error", "provider is required"))
        if text(entry.get("reuse_status")) not in {"unknown", "link_only", "licensed", "permission_granted", "prohibited"}:
            issues.append(flag("directory", ident, "error", "reuse_status must explicitly describe content rights"))
        if entry.get("embed_allowed") is True and text(entry.get("reuse_status")) not in {"licensed", "permission_granted"}:
            issues.append(flag("directory", ident, "error", "embedding requires licensed or permission_granted status"))
        if not text(entry.get("last_checked_at")):
            issues.append(flag("directory", ident, "warning", "last_checked_at is missing"))

def descriptive_stats(stats: list[dict[str, Any]], issues: list[dict[str, str]]) -> list[dict[str, Any]]:
    output = []
    for index, row in enumerate(stats):
        ident = text(row.get("item_id")) or f"row-{index+1}"
        n, correct, missing = row.get("cohort_size"), row.get("correct_count"), row.get("missing_count", 0)
        if any(isinstance(x, bool) or not isinstance(x, int) for x in (n, correct, missing)) or n <= 0 or not 0 <= correct <= n or not 0 <= missing <= n:
            issues.append(flag("statistics", ident, "error", "cohort_size, correct_count and missing_count must be valid non-negative integer counts"))
            continue
        if correct + missing > n:
            issues.append(flag("statistics", ident, "error", "correct_count + missing_count cannot exceed cohort_size"))
            continue
        output.append({
            "item_id": ident, "item_version": text(row.get("item_version")),
            "cohort_size": n, "correct_count": correct, "missing_count": missing,
            "proportion_correct": round(correct / (n - missing), 4) if n > missing else None,
            "missing_rate": round(missing / n, 4),
            "interpretation": "descriptive_only_low_n" if n < 30 else "descriptive_only",
            "limitations": "No validity, reliability, fairness or discrimination inference is made from these counts."
        })
    return output

def analyze(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise V19Error("input must be an object with schema_version=1")
    blueprint = rows(payload, "blueprint")
    mcqs = rows(payload, "mcqs")
    osce = rows(payload, "osce_stations")
    challenges = rows(payload, "challenges")
    directory = rows(payload, "external_directory")
    stats = rows(payload, "item_statistics")
    issues: list[dict[str, str]] = []
    duplicates: list[dict[str, Any]] = []
    check_blueprint(blueprint, issues)
    blueprint_ids = {text(x.get("objective_id")) for x in blueprint if text(x.get("objective_id"))}
    check_mcqs(mcqs, blueprint_ids, issues, duplicates)
    check_osce(osce, blueprint_ids, issues)
    check_challenges(challenges, blueprint_ids, issues)
    check_directory(directory, issues)
    stat_summary = descriptive_stats(stats, issues)
    counts = {level: sum(1 for x in issues if x["severity"] == level) for level in ("error", "warning")}
    artifacts = (blueprint, mcqs, osce, challenges, directory)
    manifest = {"schema_version": 1, "engine_version": VERSION, "input_sha256": sha(payload),
                "artifact_counts": {"blueprint_objectives": len(blueprint), "mcqs": len(mcqs),
                                    "osce_stations": len(osce), "challenges": len(challenges),
                                    "external_directory_entries": len(directory)},
                "issue_counts": counts, "duplicate_candidates": len(duplicates)}
    return {"schema_version": 1, "engine_version": VERSION, "summary": manifest,
            "issues": issues, "duplicate_candidates": duplicates,
            "item_statistics": stat_summary,
            "review_queue": sorted(issues, key=lambda x: ({"error": 0, "warning": 1}.get(x["severity"], 2), x["kind"], x["artifact_id"])),
            "publication_gate": "blocked" if counts["error"] else ("review_required" if counts["warning"] else "ready_for_faculty_review"),
            "manifest": manifest,
            "governance_note": "A ready_for_faculty_review result is not approval. Faculty must review clinical accuracy, educational quality, accessibility, provenance and release suitability."}

def self_test() -> int:
    sample = {"schema_version": 1, "blueprint": [{"objective_id":"OBJ-1","specialty":"Urology","competency_id":"U1","cognitive_level":"apply","assessment_method":"MCQ"}],
      "mcqs":[{"item_id":"Q1","stem":"A synthetic adult patient presents with a defined clinical scenario for teaching purposes.","lead_in":"Which is the most appropriate next step?","options":[{"label":"A","text":"Option A","correct":True,"rationale":"Best answer"},{"label":"B","text":"Option B","rationale":"Less appropriate"},{"label":"C","text":"Option C","rationale":"Not supported"}],"answer_rationale":"Synthetic rationale.","objective_ids":["OBJ-1"],"evidence_ref":"SRC-1"}],
      "osce_stations":[],"challenges":[],"external_directory":[],"item_statistics":[]}
    report = analyze(sample)
    if report["publication_gate"] != "ready_for_faculty_review":
        raise AssertionError(report["issues"])
    sample["mcqs"][0]["options"][1]["correct"] = True
    if analyze(sample)["publication_gate"] != "blocked":
        raise AssertionError("multiple correct answers should block structural validation")
    return 0

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="V19 studio JSON input")
    parser.add_argument("--output-dir", type=Path, default=Path("build-v19"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.self_test:
            return self_test()
        if not args.input:
            parser.error("--input is required unless --self-test is used")
        payload = read_json(args.input)
        report = analyze(payload)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        write_json(report, args.output_dir / "v19_assessment_studio_report.json")
        lines = ["# V19 Assessment Studio Report", "", f"**Publication gate:** {report['publication_gate']}", "",
                 f"Errors: {report['summary']['issue_counts']['error']}  |  Warnings: {report['summary']['issue_counts']['warning']}", "",
                 "## Review queue", ""]
        for item in report["review_queue"]:
            lines.append(f"- **{item['severity'].upper()} · {item['kind']} · {item['artifact_id']}** — {item['message']}")
        lines += ["", "## Governance", "", report["governance_note"], ""]
        write_text("\n".join(lines), args.output_dir / "v19_assessment_studio_report.md")
        with (args.output_dir / "v19_review_queue.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["severity", "kind", "artifact_id", "message"])
            writer.writeheader()
            writer.writerows(report["review_queue"])
        print(json.dumps(report["summary"], indent=2))
        return 2 if report["publication_gate"] == "blocked" else 0
    except (V19Error, OSError, ValueError) as exc:
        print(f"V19 error: {exc}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
