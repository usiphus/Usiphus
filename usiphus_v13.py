#!/usr/bin/env python3
"""Usiphus V13 — deterministic validation and privacy assurance for V12 bundles.

V13 checks data contracts, cross-artifact consistency, schedule integrity,
cohort suppression, output manifests, and accidental learner-ID disclosure.
It is a software assurance tool, not clinical or educational certification.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import math
import re
import tempfile
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Sequence

VERSION = "13.0.0"
LOG = logging.getLogger("usiphus.v13")
MAX_BYTES = 50 * 1024 * 1024
INPUTS = {
    "cards": "cards.json",
    "curriculum": "curriculum.json",
    "mapping": "mapping.json",
    "evidence": "evidence.json",
    "learner_progress": "learner_progress.json",
    "cohort_analytics": "cohort_analytics.json",
    "review_schedule": "review_schedule.csv",
}
OUTPUTS = ("platform_report.json", "faculty_review_queue.csv",
           "release_checklist.json", "v12_manifest.json")
SCHEDULE_STATUSES = {"not_started", "study_target_met", "review_due", "learning"}
DIRECT_IDENTIFIERS = {
    "name", "full_name", "email", "phone", "address", "date_of_birth",
    "dob", "password", "free_text", "response_text",
}


class ValidationError(ValueError):
    """Raised when an input artifact violates the V13 data contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def read_json(path: Path) -> Any:
    require(path.is_file(), f"Missing required file: {path}")
    require(path.stat().st_size <= MAX_BYTES, f"File exceeds 50 MiB: {path}")
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValidationError(f"Invalid UTF-8 JSON in {path}: {exc}") from exc


def parse_timestamp(value: Any, label: str) -> datetime:
    require(isinstance(value, str) and bool(value.strip()), f"{label} is required")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"{label} must be an ISO-8601 timestamp") from exc
    require(parsed.tzinfo is not None and parsed.utcoffset() is not None,
            f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def parse_uuid4(value: Any, label: str) -> str:
    require(isinstance(value, str), f"{label} must be a UUIDv4 string")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValidationError(f"{label} must be a UUIDv4 string") from exc
    require(parsed.version == 4 and str(parsed) == value.lower(),
            f"{label} must be a canonical UUIDv4 pseudonym")
    return str(parsed)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def scan_for_direct_identifiers(value: Any, path: str = "$") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).casefold() in DIRECT_IDENTIFIERS:
                findings.append(f"{path}.{key}")
            findings.extend(scan_for_direct_identifiers(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            findings.extend(scan_for_direct_identifiers(child, f"{path}[{index}]"))
    return findings


def validate_curriculum(path: Path) -> tuple[str, set[str]]:
    payload = read_json(path)
    require(isinstance(payload, dict), "Curriculum must be a JSON object")
    specialty = payload.get("specialty")
    require(isinstance(specialty, str) and specialty.strip(),
            "Curriculum specialty is required")
    rows = payload.get("competencies")
    require(isinstance(rows, list) and rows, "Curriculum competencies must be a non-empty list")
    ids: set[str] = set()
    for index, row in enumerate(rows):
        require(isinstance(row, dict), f"Competency {index} must be an object")
        cid = row.get("competency_id") or row.get("id")
        require(isinstance(cid, str) and cid.strip(), f"Competency {index} requires an ID")
        key = cid.strip().casefold()
        require(key not in ids, f"Duplicate competency ID: {cid}")
        ids.add(key)
        if "required" in row:
            require(isinstance(row["required"], bool), f"Competency {cid} required must be boolean")
        weight = row.get("weight", 1.0)
        require(not isinstance(weight, bool) and isinstance(weight, (int, float))
                and math.isfinite(weight) and weight > 0,
                f"Competency {cid} weight must be finite and positive")
    return specialty.strip().casefold(), ids


def validate_learner_progress(path: Path, specialty: str,
                              competency_ids: set[str]) -> set[str]:
    payload = read_json(path)
    require(isinstance(payload, dict), "Learner progress must be a JSON object")
    require(str(payload.get("specialty", "")).strip().casefold() == specialty,
            "Learner progress specialty mismatch")
    learners = payload.get("learners")
    require(isinstance(learners, list), "Learner progress requires a learners list")
    learner_ids: set[str] = set()
    for index, learner in enumerate(learners):
        require(isinstance(learner, dict), f"Learner {index} must be an object")
        direct = scan_for_direct_identifiers(learner)
        require(not direct, f"Learner {index} contains prohibited fields: {', '.join(direct)}")
        lid = parse_uuid4(learner.get("learner_id"), f"Learner {index} learner_id")
        require(lid not in learner_ids, f"Duplicate learner ID: {lid}")
        learner_ids.add(lid)
        rows = learner.get("competencies")
        require(isinstance(rows, list), f"Learner {lid} competencies must be a list")
        seen: set[str] = set()
        for item in rows:
            require(isinstance(item, dict), f"Learner {lid} competency row must be an object")
            cid = str(item.get("competency_id", "")).strip().casefold()
            require(cid in competency_ids, f"Learner {lid} references unknown competency: {cid}")
            require(cid not in seen, f"Learner {lid} has duplicate competency row: {cid}")
            seen.add(cid)
            estimate = item.get("mastery_estimate")
            if estimate is not None:
                require(not isinstance(estimate, bool) and isinstance(estimate, (int, float))
                        and math.isfinite(estimate) and 0 <= estimate <= 1,
                        f"Learner {lid} has invalid mastery estimate")
    return learner_ids


def validate_cohort(path: Path, specialty: str,
                    competency_ids: set[str]) -> tuple[int, int]:
    payload = read_json(path)
    require(isinstance(payload, dict), "Cohort analytics must be a JSON object")
    require(str(payload.get("specialty", "")).strip().casefold() == specialty,
            "Cohort analytics specialty mismatch")
    minimum = payload.get("minimum_cohort_size")
    require(isinstance(minimum, int) and not isinstance(minimum, bool) and minimum >= 2,
            "minimum_cohort_size must be an integer of at least 2")
    rows = payload.get("competencies")
    require(isinstance(rows, list), "Cohort analytics requires competencies")
    seen: set[str] = set()
    suppressed = 0
    for index, row in enumerate(rows):
        require(isinstance(row, dict), f"Cohort row {index} must be an object")
        cid = str(row.get("competency_id", "")).strip().casefold()
        require(cid in competency_ids, f"Cohort row references unknown competency: {cid}")
        require(cid not in seen, f"Duplicate cohort competency row: {cid}")
        seen.add(cid)
        flag = row.get("suppressed")
        require(isinstance(flag, bool), f"Cohort row {cid} suppressed must be boolean")
        if flag:
            suppressed += 1
            forbidden = {"learner_count", "mean_mastery_estimate", "mean_attempts_per_learner"}
            require(not (forbidden & row.keys()),
                    f"Suppressed cohort row {cid} exposes aggregate metrics")
        else:
            count = row.get("learner_count")
            require(isinstance(count, int) and not isinstance(count, bool) and count >= minimum,
                    f"Unsuppressed cohort row {cid} is below minimum cohort size")
            estimate = row.get("mean_mastery_estimate")
            require(not isinstance(estimate, bool) and isinstance(estimate, (int, float))
                    and math.isfinite(estimate) and 0 <= estimate <= 1,
                    f"Unsuppressed cohort row {cid} has invalid mastery estimate")
    return len(rows), suppressed


def validate_schedule(path: Path, competency_ids: set[str]) -> int:
    require(path.is_file(), f"Missing review schedule: {path}")
    require(path.stat().st_size <= MAX_BYTES, "Review schedule exceeds 50 MiB")
    required = {"learner_id", "competency_id", "next_due_at",
                "recommended_interval_days", "status"}
    seen: set[tuple[str, str]] = set()
    count = 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        require(reader.fieldnames is not None and required.issubset(reader.fieldnames),
                "Review schedule is missing required columns")
        for index, row in enumerate(reader, start=2):
            lid = parse_uuid4(row.get("learner_id"), f"Schedule row {index} learner_id")
            cid = str(row.get("competency_id", "")).strip().casefold()
            require(cid in competency_ids, f"Schedule row {index} has unknown competency: {cid}")
            key = (lid, cid)
            require(key not in seen, f"Duplicate learner/competency schedule row at line {index}")
            seen.add(key)
            parse_timestamp(row.get("next_due_at"), f"Schedule row {index} next_due_at")
            raw_interval = row.get("recommended_interval_days", "")
            require(re.fullmatch(r"[1-9][0-9]*", raw_interval or "") is not None,
                    f"Schedule row {index} interval must be a positive integer")
            status = (row.get("status") or "").strip()
            require(status in SCHEDULE_STATUSES,
                    f"Schedule row {index} has unsupported status: {status}")
            mastery = row.get("mastery_estimate")
            if mastery not in (None, ""):
                try:
                    number = float(mastery)
                except ValueError as exc:
                    raise ValidationError(f"Schedule row {index} mastery estimate is not numeric") from exc
                require(math.isfinite(number) and 0 <= number <= 1,
                        f"Schedule row {index} mastery estimate must be between 0 and 1")
            count += 1
    return count


def validate_v12_outputs(output_dir: Path, specialty: str,
                         competency_ids: set[str], learner_ids: set[str],
                         input_dir: Path) -> dict[str, Any]:
    for name in OUTPUTS:
        require((output_dir / name).is_file(), f"Missing V12 output: {name}")
    report = read_json(output_dir / "platform_report.json")
    checklist = read_json(output_dir / "release_checklist.json")
    manifest = read_json(output_dir / "v12_manifest.json")
    require(isinstance(report, dict) and report.get("schema_version") == 1,
            "Unsupported or malformed V12 platform report schema")
    require(str(report.get("specialty", "")).casefold() == specialty,
            "V12 platform report specialty mismatch")
    require(isinstance(checklist, dict) and isinstance(checklist.get("release_ready"), bool),
            "Release checklist requires boolean release_ready")
    require(report.get("release") == checklist,
            "Platform report release section differs from release_checklist.json")
    require(isinstance(manifest, dict) and manifest.get("usiphus_version") == "12.0.0",
            "V12 manifest version mismatch")
    listed_outputs = manifest.get("outputs")
    require(isinstance(listed_outputs, list) and set(listed_outputs) == set(OUTPUTS[:3]),
            "V12 manifest output inventory is incomplete or unexpected")
    inputs = manifest.get("inputs")
    require(isinstance(inputs, dict), "V12 manifest inputs must be an object")
    for key, filename in INPUTS.items():
        entry = inputs.get(key)
        source = input_dir / filename
        require(isinstance(entry, dict) and source.is_file(),
                f"Manifest/input missing for {key}")
        require(entry.get("sha256") == sha256(source),
                f"Input hash mismatch for {key}; source changed after V12 run")
        require(entry.get("bytes") == source.stat().st_size,
                f"Input byte-size mismatch for {key}")
    queue_path = output_dir / "faculty_review_queue.csv"
    require(queue_path.is_file(), "Missing faculty review queue")
    with queue_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        expected = {"work_type", "item_id", "card_id", "competency_id",
                    "current_status", "reason"}
        require(reader.fieldnames is not None and expected.issubset(reader.fieldnames),
                "Faculty review queue has an invalid header")
        queue_rows = list(reader)
    if not checklist["release_ready"]:
        require(bool(checklist.get("blocking_items")),
                "A blocked release must contain at least one blocking item")
    else:
        require(not checklist.get("blocking_items"),
                "A release marked ready cannot contain blocking items")
    output_text = "\n".join((output_dir / name).read_text(encoding="utf-8")
                            for name in OUTPUTS)
    leaked = sorted(lid for lid in learner_ids if lid in output_text)
    require(not leaked, "Pseudonymous learner IDs leaked into V12 outputs")
    require(report.get("specialty") == specialty,
            "V12 report specialty must be normalized consistently")
    curriculum = report.get("curriculum")
    require(isinstance(curriculum, dict), "V12 report curriculum summary is missing")
    require(curriculum.get("competencies", 0) >= len(competency_ids),
            "V12 report competency count is inconsistent with curriculum")
    return {
        "platform_release_ready": checklist["release_ready"],
        "faculty_queue_rows": len(queue_rows),
        "manifest_inputs_verified": len(INPUTS),
        "manifest_outputs_verified": len(OUTPUTS[:3]),
        "learner_id_leaks": 0,
    }


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8",
                                         dir=path.parent, delete=False) as handle:
            temporary = handle.name
            handle.write(content)
            handle.flush()
        Path(temporary).replace(path)
    finally:
        if temporary and Path(temporary).exists():
            Path(temporary).unlink()


def validate_bundle(input_dir: Path, output_dir: Path, report_path: Path) -> dict[str, Any]:
    specialty, competency_ids = validate_curriculum(input_dir / INPUTS["curriculum"])
    learner_ids = validate_learner_progress(
        input_dir / INPUTS["learner_progress"], specialty, competency_ids)
    cohort_rows, suppressed_rows = validate_cohort(
        input_dir / INPUTS["cohort_analytics"], specialty, competency_ids)
    schedule_rows = validate_schedule(input_dir / INPUTS["review_schedule"], competency_ids)
    output_summary = validate_v12_outputs(
        output_dir, specialty, competency_ids, learner_ids, input_dir)
    return {
        "schema_version": 1,
        "usiphus_version": VERSION,
        "validation_status": "passed",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "specialty": specialty,
        "contracts": {
            "competencies": len(competency_ids),
            "pseudonymous_learners": len(learner_ids),
            "cohort_rows": cohort_rows,
            "suppressed_cohort_rows": suppressed_rows,
            "review_schedule_rows": schedule_rows,
        },
        "v12_outputs": output_summary,
        "scope": [
            "Validates structure, consistency, integrity, and selected privacy safeguards.",
            "Does not establish clinical truth, educational validity, learner competence, or legal compliance.",
            "UUIDv4 format is not proof of anonymization or protection against re-identification.",
            "Input files contain sensitive pseudonymous educational records and require access controls."
        ],
    }


def run_self_test() -> int:
    import unittest

    class V13Tests(unittest.TestCase):
        def setUp(self):
            self.temp = tempfile.TemporaryDirectory()
            self.root = Path(self.temp.name)
            self.inputs = self.root / "inputs"
            self.outputs = self.root / "outputs"
            self.inputs.mkdir()
            self.outputs.mkdir()
            self.lid = "123e4567-e89b-42d3-a456-426614174000"
            self.competency = {"specialty": "urology", "competencies": [
                {"id": "U1", "title": "Acute retention", "required": True, "weight": 1.0}]}
            (self.inputs / "curriculum.json").write_text(json.dumps(self.competency))
            (self.inputs / "learner_progress.json").write_text(json.dumps({
                "specialty": "urology", "learners": [{"learner_id": self.lid,
                "competencies": [{"competency_id": "U1", "mastery_estimate": 0.7}]}]}))
            (self.inputs / "cohort_analytics.json").write_text(json.dumps({
                "specialty": "urology", "minimum_cohort_size": 5,
                "competencies": [{"competency_id": "U1", "suppressed": True,
                                  "suppression_reason": "small cohort"}]}))
            (self.inputs / "review_schedule.csv").write_text(
                "learner_id,competency_id,next_due_at,recommended_interval_days,status\n"
                f"{self.lid},U1,2026-11-01T12:00:00Z,4,learning\n")
            for key, filename in INPUTS.items():
                if key in {"curriculum", "learner_progress", "cohort_analytics", "review_schedule"}:
                    continue
                if filename.endswith(".csv"):
                    (self.inputs / filename).write_text("")
                else:
                    (self.inputs / filename).write_text("{}")
            (self.outputs / "platform_report.json").write_text(json.dumps({
                "schema_version": 1, "specialty": "urology",
                "curriculum": {"competencies": 1},
                "release": {"release_ready": False, "blocking_items": ["fixture blocker"]}}))
            (self.outputs / "release_checklist.json").write_text(json.dumps(
                {"release_ready": False, "blocking_items": ["fixture blocker"]}))
            (self.outputs / "faculty_review_queue.csv").write_text(
                "work_type,item_id,card_id,competency_id,current_status,reason\n")
            manifest_inputs = {}
            for key, filename in INPUTS.items():
                path = self.inputs / filename
                manifest_inputs[key] = {"sha256": sha256(path), "bytes": path.stat().st_size}
            (self.outputs / "v12_manifest.json").write_text(json.dumps({
                "usiphus_version": "12.0.0", "inputs": manifest_inputs,
                "outputs": list(OUTPUTS[:3])}))
        def tearDown(self):
            self.temp.cleanup()
        def test_valid_schedule(self):
            self.assertEqual(validate_schedule(self.inputs / "review_schedule.csv", {"u1"}), 1)
        def test_invalid_schedule_date(self):
            p = self.inputs / "review_schedule.csv"
            p.write_text("learner_id,competency_id,next_due_at,recommended_interval_days,status\n"
                         f"{self.lid},U1,not-a-date,4,learning\n")
            with self.assertRaises(ValidationError):
                validate_schedule(p, {"u1"})
        def test_naive_schedule_timestamp_rejected(self):
            p = self.inputs / "review_schedule.csv"
            p.write_text("learner_id,competency_id,next_due_at,recommended_interval_days,status\n"
                         f"{self.lid},U1,2026-11-01T12:00:00,4,learning\n")
            with self.assertRaises(ValidationError):
                validate_schedule(p, {"u1"})
        def test_zero_interval_rejected(self):
            p = self.inputs / "review_schedule.csv"
            p.write_text("learner_id,competency_id,next_due_at,recommended_interval_days,status\n"
                         f"{self.lid},U1,2026-11-01T12:00:00Z,0,learning\n")
            with self.assertRaises(ValidationError):
                validate_schedule(p, {"u1"})
        def test_unknown_schedule_status_rejected(self):
            p = self.inputs / "review_schedule.csv"
            p.write_text("learner_id,competency_id,next_due_at,recommended_interval_days,status\n"
                         f"{self.lid},U1,2026-11-01T12:00:00Z,4,unknown\n")
            with self.assertRaises(ValidationError):
                validate_schedule(p, {"u1"})
        def test_duplicate_schedule_pair_rejected(self):
            p = self.inputs / "review_schedule.csv"
            line = f"{self.lid},U1,2026-11-01T12:00:00Z,4,learning\n"
            p.write_text("learner_id,competency_id,next_due_at,recommended_interval_days,status\n" + line + line)
            with self.assertRaises(ValidationError):
                validate_schedule(p, {"u1"})
        def test_invalid_uuid_rejected(self):
            p = self.inputs / "review_schedule.csv"
            p.write_text("learner_id,competency_id,next_due_at,recommended_interval_days,status\n"
                         "not-a-uuid,U1,2026-11-01T12:00:00Z,4,learning\n")
            with self.assertRaises(ValidationError):
                validate_schedule(p, {"u1"})
        def test_unknown_competency_rejected(self):
            p = self.inputs / "review_schedule.csv"
            p.write_text("learner_id,competency_id,next_due_at,recommended_interval_days,status\n"
                         f"{self.lid},BAD,2026-11-01T12:00:00Z,4,learning\n")
            with self.assertRaises(ValidationError):
                validate_schedule(p, {"u1"})
        def test_suppressed_cohort_metrics_rejected(self):
            p = self.inputs / "cohort_analytics.json"
            p.write_text(json.dumps({"specialty": "urology", "minimum_cohort_size": 5,
                "competencies": [{"competency_id": "U1", "suppressed": True,
                                  "learner_count": 2}]}))
            with self.assertRaises(ValidationError):
                validate_cohort(p, "urology", {"u1"})
        def test_unsuppressed_small_cohort_rejected(self):
            p = self.inputs / "cohort_analytics.json"
            p.write_text(json.dumps({"specialty": "urology", "minimum_cohort_size": 5,
                "competencies": [{"competency_id": "U1", "suppressed": False,
                                  "learner_count": 4, "mean_mastery_estimate": 0.5}]}))
            with self.assertRaises(ValidationError):
                validate_cohort(p, "urology", {"u1"})
        def test_direct_identifier_fields_rejected(self):
            p = self.inputs / "learner_progress.json"
            p.write_text(json.dumps({"specialty": "urology", "learners": [
                {"learner_id": self.lid, "email": "private@example.test", "competencies": []}]}))
            with self.assertRaises(ValidationError):
                validate_learner_progress(p, "urology", {"u1"})
        def test_duplicate_learner_rejected(self):
            p = self.inputs / "learner_progress.json"
            learner = {"learner_id": self.lid, "competencies": []}
            p.write_text(json.dumps({"specialty": "urology", "learners": [learner, learner]}))
            with self.assertRaises(ValidationError):
                validate_learner_progress(p, "urology", {"u1"})
        def test_mastery_out_of_range_rejected(self):
            p = self.inputs / "learner_progress.json"
            p.write_text(json.dumps({"specialty": "urology", "learners": [
                {"learner_id": self.lid, "competencies": [
                    {"competency_id": "U1", "mastery_estimate": 1.2}]}]}))
            with self.assertRaises(ValidationError):
                validate_learner_progress(p, "urology", {"u1"})
        def test_unknown_competency_in_learner_rejected(self):
            p = self.inputs / "learner_progress.json"
            p.write_text(json.dumps({"specialty": "urology", "learners": [
                {"learner_id": self.lid, "competencies": [{"competency_id": "BAD"}]}]}))
            with self.assertRaises(ValidationError):
                validate_learner_progress(p, "urology", {"u1"})
        def test_manifest_hash_mismatch_rejected(self):
            p = self.outputs / "v12_manifest.json"
            manifest = json.loads(p.read_text())
            manifest["inputs"]["curriculum"]["sha256"] = "0" * 64
            p.write_text(json.dumps(manifest))
            with self.assertRaises(ValidationError):
                validate_v12_outputs(self.outputs, "urology", {"u1"}, {self.lid}, self.inputs)
        def test_learner_id_leak_rejected(self):
            p = self.outputs / "platform_report.json"
            report = json.loads(p.read_text())
            report["unexpected"] = self.lid
            p.write_text(json.dumps(report))
            with self.assertRaises(ValidationError):
                validate_v12_outputs(self.outputs, "urology", {"u1"}, {self.lid}, self.inputs)
        def test_valid_v12_output_bundle(self):
            result = validate_v12_outputs(self.outputs, "urology", {"u1"}, {self.lid}, self.inputs)
            self.assertEqual(result["learner_id_leaks"], 0)
        def test_report_and_checklist_must_match(self):
            p = self.outputs / "release_checklist.json"
            p.write_text(json.dumps({"release_ready": True, "blocking_items": []}))
            with self.assertRaises(ValidationError):
                validate_v12_outputs(self.outputs, "urology", {"u1"}, {self.lid}, self.inputs)
        def test_duplicate_curriculum_id_rejected(self):
            p = self.inputs / "curriculum.json"
            p.write_text(json.dumps({"specialty": "urology", "competencies": [
                {"id": "U1"}, {"id": "u1"}]}))
            with self.assertRaises(ValidationError):
                validate_curriculum(p)
        def test_naive_timestamp_rejected(self):
            with self.assertRaises(ValidationError):
                parse_timestamp("2026-11-01T12:00:00", "test")
        def test_direct_identifier_scan_case_insensitive(self):
            self.assertTrue(scan_for_direct_identifiers({"Email": "x"}))
        def test_deterministic_hash(self):
            p = self.inputs / "curriculum.json"
            self.assertEqual(sha256(p), sha256(p))

    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(V13Tests))
    return 0 if result.wasSuccessful() else 2


def parse_args(argv: Sequence[str] | None = None):
    parser = argparse.ArgumentParser(description="Usiphus V13 validation and privacy assurance")
    parser.add_argument("--input-dir", help="Directory containing the seven V12 source artifacts")
    parser.add_argument("--output-dir", help="Directory containing V12 generated outputs")
    parser.add_argument("--report", default="v13_validation_report.json")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    if args.self_test:
        return run_self_test()
    if not args.input_dir or not args.output_dir:
        LOG.error("--input-dir and --output-dir are required unless --self-test is used")
        return 2
    try:
        report = validate_bundle(Path(args.input_dir), Path(args.output_dir), Path(args.report))
        atomic_write(Path(args.report), json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        LOG.info("V13 validation passed for %s; report: %s", report["specialty"], args.report)
        return 0
    except (ValidationError, OSError, TypeError) as exc:
        LOG.error("V13 validation failed: %s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
