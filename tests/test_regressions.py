
"""Regression tests for the Usiphus hardening pass (synthetic, non-clinical data).

Run with: python -m unittest discover -s tests -t . -v
"""
from __future__ import annotations

import ast
import csv
import http.server
import importlib.util
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from argparse import Namespace
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if not (ROOT / "usiphus_common.py").is_file():
    ROOT = ROOT.parent
TESTS_DIR = str(Path(__file__).resolve().parent)
if TESTS_DIR in sys.path:
    sys.path.remove(TESTS_DIR)
sys.path.insert(0, TESTS_DIR)
sys.path.append(str(ROOT))
import usiphus_common as common  # noqa: E402


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


v7, v8, v9 = load("usiphus_v7"), load("usiphus_v8"), load("usiphus_v9")
v11, v12 = load("usiphus_v11"), load("usiphus_v12")
v14, v15, mig = load("usiphus_v14"), load("usiphus_v15"), load("usiphus_migrate")
import usiphus_core as core  # noqa: E402  (same module object the engines imported)
LID = "123e4567-e89b-42d3-a456-426614174000"
PROV = {"method": "faculty-approved", "reviewer": "Faculty", "reviewed_at": "2026-10-31T12:00:00Z"}


def dump(path: Path, payload) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


class ContentFidelity(unittest.TestCase):
    def test_spaced_hyphen_range_is_not_altered(self):
        for mod in (common, v7, v8, v9):
            self.assertEqual(mod.canonicalize_prompt("Normal pH is 7.35 - 7.45: what is it?"),
                             "Normal pH is 7.35 - 7.45: what is it?")

    def test_double_hyphen_still_becomes_em_dash(self):
        self.assertEqual(common.canonicalize_prompt("Term -- what is it"), "Term \u2014 what is it?")

    def test_answer_keeps_line_breaks_in_every_engine(self):
        for mod in (v7, v8, v9):
            card = mod.Card("c", "surgery", "Q?", "Step 1: do this\n  Step 2:   do that\n\n\n\n- item")
            self.assertEqual(card.answer, "Step 1: do this\nStep 2: do that\n\n- item")


class StructuralFixes(unittest.TestCase):
    def test_v9_has_no_shadowed_duplicate_definitions(self):
        tree = ast.parse((ROOT / "usiphus_v9.py").read_text(encoding="utf-8"))
        names = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
        self.assertEqual(sorted({n for n in names if names.count(n) > 1}), [])

    def test_card_model_and_quality_checks_are_single_source(self):
        for mod in (v7, v8, v9):
            self.assertIs(mod.Card, core.Card)
            self.assertIs(mod.evaluate_quality, core.evaluate_quality)
            self.assertIs(mod.FindingCode, core.FindingCode)
        self.assertIs(v8.analyze_evidence_registry, v9.analyze_evidence_registry)

    def test_historical_report_schema_versions_preserved(self):
        card = v7.Card("c", "surgery", "What is X?", "A sufficiently long answer here.")
        for mod, expected in ((v7, 4), (v8, 5), (v9, 6)):
            report = mod.evaluate_quality([card], schema_version=mod.REPORT_SCHEMA_VERSION)
            self.assertEqual(report.schema_version, expected)

    def test_engines_share_one_implementation(self):
        self.assertIs(v7.find_near_duplicate_pairs, common.find_near_duplicate_pairs)
        self.assertIs(v8.canonicalize_prompt, common.canonicalize_prompt)
        self.assertIs(v9.push_to_ankiconnect, common.push_to_ankiconnect)

    def test_near_duplicates_match_bruteforce_and_scale(self):
        words = ["renal", "colic", "ureter", "stone", "bladder", "pain", "flank", "acute", "chronic", "drain"]
        cards = [v7.Card(f"c{i}", "urology" if i % 7 else "other",
                         " ".join(words[(i * k) % 10] for k in (1, 3, 7)) + f" variant{i % 5}?", "a")
                 for i in range(300)]
        fast = {(p["card_id_a"], p["card_id_b"]) for p in common.find_near_duplicate_pairs(cards, 0.5)}
        brute = set()
        for i, left in enumerate(cards):
            for right in cards[i + 1:]:
                if (left.specialty == right.specialty and left.prompt.casefold() != right.prompt.casefold()
                        and common.lexical_similarity(left.prompt, right.prompt) >= 0.5):
                    brute.add((left.card_id, right.card_id))
        self.assertEqual(fast, brute)
        big = [v7.Card(f"b{i}", "x", f"unique{i} topic{i % 40} term{i % 97}?", "a") for i in range(4000)]
        start = time.time()
        common.find_near_duplicate_pairs(big, 0.78)
        self.assertLess(time.time() - start, 15)

    def test_as_of_makes_staleness_deterministic(self):
        card = v7.Card("c", "surgery", "What is X?", "A sufficiently long answer.", references=("r",),
                       source="s", evidence_status="reviewed", reviewed_on="2026-01-01", reviewer="r")
        def stale(as_of):
            report = v7.evaluate_quality([card], as_of=as_of, max_review_age_days=730)
            return any(f.code == v7.FindingCode.REVIEW_STALE for f in report.results[0].findings)
        self.assertTrue(stale(date(2030, 1, 1)))
        self.assertFalse(stale(date(2026, 6, 1)))

    def test_strict_dates(self):
        self.assertTrue(common.valid_iso_date("2026-10-31"))
        self.assertFalse(common.valid_iso_date("20261031"))
        self.assertFalse(common.valid_iso_date("2026-1-5"))

    def test_csv_safe(self):
        self.assertEqual(common.csv_safe("=1+1"), "'=1+1")
        self.assertEqual(common.csv_safe("normal"), "normal")


class FakeAnki(http.server.BaseHTTPRequestHandler):
    existing: set = set()
    notes: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        notes = body["params"]["notes"]
        key = lambda n: n["fields"]["Front"]
        if body["action"] == "canAddNotes":
            result = [key(n) not in FakeAnki.existing for n in notes]
        else:
            result = []
            for n in notes:
                FakeAnki.existing.add(key(n)); FakeAnki.notes.append(n); result.append(len(FakeAnki.notes))
        data = json.dumps({"result": result, "error": None}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


class AnkiConnectTests(unittest.TestCase):
    def test_includes_mcq_options_and_is_idempotent(self):
        FakeAnki.existing, FakeAnki.notes = set(), []
        server = http.server.HTTPServer(("127.0.0.1", 0), FakeAnki)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{server.server_port}"
        try:
            cards = [v7.Card("m1", "surgery", "Which artery?", "Femoral artery rationale here.", card_type="mcq",
                             options=("Femoral", "Popliteal"), correct_option="Femoral", tags=("two words",)),
                     v7.Card("m2", "surgery", "Another question?", "Another answer is here.")]
            self.assertEqual(common.push_to_ankiconnect(cards, "Deck", url), {"added": 2, "skipped_duplicates": 0})
            front = FakeAnki.notes[0]["fields"]["Front"]
            self.assertIn("Femoral", front); self.assertIn("Popliteal", front)
            self.assertIn("Correct option", FakeAnki.notes[0]["fields"]["Back"])
            self.assertIn("two_words", FakeAnki.notes[0]["tags"])
            self.assertEqual(common.push_to_ankiconnect(cards, "Deck", url), {"added": 0, "skipped_duplicates": 2})
        finally:
            server.shutdown()
            server.server_close()


class V11Fixes(unittest.TestCase):
    def test_settings_typo_rejected(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            p = Path(d) / "s.json"; dump(p, {"interval_day": [1, 1, 1, 1, 1]})
            with self.assertRaises(ValueError): v11.load_settings(p)

    def test_cohort_floor_enforced(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            p = Path(d) / "e.json"
            dump(p, {"privacy": {"retention_days": 30, "minimum_cohort_size": 4}, "learners": [], "attempts": []})
            with self.assertRaises(ValueError): v11.load_events(p, v11.parse_time("2026-10-31T00:00:00Z", "t"))

    def test_unprovenanced_verified_mapping_rejected(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            p = Path(d) / "m.json"
            dump(p, {"specialty": "urology", "candidates": [{"card_id": "c", "competency_id": "U1", "status": "verified"}]})
            with self.assertRaises(ValueError): v11.load_verified_card_map(p, {"u1"}, "urology")


class CohortDifferencing(unittest.TestCase):
    NOW = __import__("datetime").datetime(2026, 10, 31, tzinfo=__import__("datetime").timezone.utc)
    COMPS = [{"competency_id": "U1", "title": "a", "domain": "d"}, {"competency_id": "U2", "title": "b", "domain": "d"}]
    MAP = {"c1": {"U1"}, "c2": {"U2"}}

    def run_case(self, n1, n2, overlap=True):
        ids1 = [f"L{i}" for i in range(n1)]
        ids2 = ids1[:n2] if overlap else [f"M{i}" for i in range(n2)]
        ev = lambda lid, card: {"attempt_id": f"{lid}{card}", "learner_id": lid, "card_id": card,
                                "answered_at": self.NOW, "score": 1.0}
        events = [ev(l, "c1") for l in ids1] + [ev(l, "c2") for l in ids2]
        _, cohort, _ = v11.build_analytics("u", self.COMPS, self.MAP, events, {}, 5, 365, v11.load_settings(None), self.NOW)
        return {r["competency_id"]: r["suppressed"] for r in cohort["competencies"]}

    def test_subset_with_tiny_remainder_is_suppressed(self):
        self.assertEqual(self.run_case(6, 5), {"U1": False, "U2": True})

    def test_subset_with_large_remainder_is_published(self):
        self.assertEqual(self.run_case(12, 5), {"U1": False, "U2": False})

    def test_disjoint_cohorts_are_published(self):
        self.assertEqual(self.run_case(6, 5, overlap=False), {"U1": False, "U2": False})

    def test_report_states_cross_release_limitation(self):
        _, cohort, _ = v11.build_analytics("u", self.COMPS, self.MAP, [], {}, 5, 365, v11.load_settings(None), self.NOW)
        self.assertIn("NOT prevented", " ".join(cohort["limitations"]))


class PublishModes(unittest.TestCase):
    def make(self, root, name):
        d = root / name; d.mkdir(); (d / name).write_text(name); return d

    def test_replace_via_exchange_or_fallback(self):
        original = v14._exchange
        try:
            for force_fallback in (False, True):
                with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
                    root = Path(d); old = self.make(root, "old"); (old / v14.MARKER).write_text("x")
                    new = self.make(root, "new")
                    if force_fallback:
                        v14._exchange = lambda a, b: False
                    mode = v14.publish(new, old)
                    self.assertIn(mode, {"atomic-exchange", "rename-swap"})
                    if force_fallback: self.assertEqual(mode, "rename-swap")
                    self.assertTrue((old / "new").is_file()); self.assertFalse((old / "old").exists())
                    self.assertEqual([p.name for p in root.iterdir()], ["old"])  # no stage/backup leftovers
        finally:
            v14._exchange = original

    def test_first_publish_creates_directory(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            root = Path(d); new = self.make(root, "new")
            self.assertEqual(v14.publish(new, root / "out"), "created")


class MigrationTool(unittest.TestCase):
    def test_malformed_inputs_are_rejected_without_writing_output(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            root = Path(d)
            malformed_json = root / "malformed.json"
            malformed_json.write_text('{"candidates":', encoding="utf-8")
            invalid_shape = root / "invalid-shape.json"
            dump(invalid_shape, ["not", "a", "mapping"])

            for source in (malformed_json, invalid_shape):
                with self.subTest(source=source.name):
                    output = root / f"{source.stem}-migrated.json"
                    result = subprocess.run(
                        [sys.executable, str(ROOT / "usiphus_migrate.py"),
                         "mapping", "--input", str(source), "--output", str(output)],
                        cwd=ROOT, text=True, capture_output=True, check=False,
                    )
                    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                    self.assertIn("ERROR:", result.stderr)
                    self.assertFalse(output.exists())

    def test_unprovenanced_verified_downgraded_never_upgraded(self):
        payload = {"specialty": "u", "candidates": [
            {"card_id": "a", "competency_id": "U1", "status": "verified"},
            dict(card_id="b", competency_id="U1", status="verified", **PROV),
            {"card_id": "c", "competency_id": "U1", "status": "suggested"}]}
        out, report = mig.migrate_mapping(payload)
        self.assertEqual([r["status"] for r in out["candidates"]], ["pending", "verified", "suggested"])
        self.assertEqual(report["downgraded_to_pending"], 1)
        self.assertEqual(payload["candidates"][0]["status"], "verified")  # input object untouched

    def test_migrated_mapping_loads_in_v11_only_with_real_provenance(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            old = Path(d) / "m.json"
            dump(old, {"specialty": "urology", "candidates": [
                {"card_id": "c", "competency_id": "U1", "status": "verified"},
                dict(card_id="ok", competency_id="U1", status="verified", **PROV)]})
            new = Path(d) / "m2.json"
            self.assertEqual(mig.main(["mapping", "--input", str(old), "--output", str(new)]), 0)
            # the unprovenanced row no longer counts; the properly verified one still does
            self.assertEqual(v11.load_verified_card_map(new, {"u1"}, "urology"), {"ok": {"U1"}})
            only_bad = Path(d) / "bad.json"
            dump(only_bad, {"specialty": "urology", "candidates": [{"card_id": "c", "competency_id": "U1", "status": "verified"}]})
            out = Path(d) / "bad2.json"
            mig.main(["mapping", "--input", str(only_bad), "--output", str(out)])
            with self.assertRaises(ValueError):          # nothing verified -> V11 refuses, never silently trusts
                v11.load_verified_card_map(out, {"u1"}, "urology")

    def test_settings_suggestions(self):
        out, report = mig.migrate_settings({"interval_day": [1, 2], "prior_alpha": 1}, apply_suggestions=True)
        self.assertIn("interval_days", out); self.assertEqual(report["still_unresolved"], [])
        _, report = mig.migrate_settings({"zzzz": 1})
        self.assertEqual(report["still_unresolved"], ["zzzz"])

    def test_events_cohort_floor_raised(self):
        out, report = mig.migrate_events({"privacy": {"retention_days": 30, "minimum_cohort_size": 2}})
        self.assertEqual(out["privacy"]["minimum_cohort_size"], 5); self.assertTrue(report["changed"])

    def test_refuses_to_overwrite_input(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            p = Path(d) / "m.json"; dump(p, {"candidates": []})
            self.assertEqual(mig.main(["mapping", "--input", str(p), "--output", str(p)]), 2)


class V12Fixes(unittest.TestCase):
    def build(self, root: Path, *, extra_comp=False, reviewed_on="2026-10-31", schedule_learner=LID, claim_id="E1"):
        comps = [{"competency_id": "U1", "title": "t", "required": True}]
        if extra_comp: comps.append({"competency_id": "U2", "title": "t2", "required": True})
        dump(root / "cards.json", {"cards": [{"card_id": "K1", "specialty": "urology"}]})
        dump(root / "curriculum.json", {"specialty": "urology", "competencies": comps})
        dump(root / "mapping.json", {"specialty": "urology", "candidates": [
            dict(card_id="K1", competency_id="U1", status="verified", **PROV)]})
        dump(root / "evidence.json", {"claims": [{
            "claim_id": claim_id, "card_id": "K1", "claim": "c", "reference": "r", "source_type": "g",
            "status": "approved", "reviewed_on": reviewed_on, "reviewer": "rv",
            "audit_trail": [{"to": "in_review", "actor": "rv", "date": "2026-10-30"},
                            {"to": "approved", "actor": "rv", "date": "2026-10-31"}]}]})
        dump(root / "learner_progress.json", {"specialty": "urology", "accepted_attempts": 1, "learners": [
            {"learner_id": LID, "competencies": [{"competency_id": "U1", "mastery_estimate": 0.5}]}]})
        dump(root / "cohort_analytics.json", {"specialty": "urology", "minimum_cohort_size": 5,
            "competencies": [{"competency_id": "U1", "suppressed": True}]})
        (root / "review_schedule.csv").write_text(
            "learner_id,competency_id,next_due_at,recommended_interval_days,status\n"
            f"{schedule_learner},U1,2026-11-01T12:00:00Z,4,learning\n", encoding="utf-8")
        return Namespace(cards=str(root / "cards.json"), curriculum=str(root / "curriculum.json"),
            mapping=str(root / "mapping.json"), evidence=str(root / "evidence.json"),
            learner_progress=str(root / "learner_progress.json"), cohort_analytics=str(root / "cohort_analytics.json"),
            review_schedule=str(root / "review_schedule.csv"), output=str(root / "out"), as_of="2026-10-31",
            max_evidence_age_days=730)

    def checklist(self, **kw):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            root = Path(d); args = self.build(root, **kw)
            v12.run(args)
            return (json.loads((root / "out/release_checklist.json").read_text()),
                    (root / "out/faculty_review_queue.csv").read_text())

    def test_happy_path_ready(self):
        self.assertTrue(self.checklist()[0]["release_ready"])

    def test_evidence_check_independent_of_curriculum_gap(self):
        checklist, _ = self.checklist(extra_comp=True)
        checks = {c["check"]: c["passed"] for c in checklist["checks"]}
        self.assertFalse(checklist["release_ready"])
        self.assertFalse(checks["all_required_competencies_have_verified_mappings"])
        self.assertTrue(checks["all_cards_have_current_approved_evidence"])

    def test_future_review_date_blocks(self):
        checklist, _ = self.checklist(reviewed_on="2027-06-01")
        self.assertFalse(checklist["release_ready"])

    def test_schedule_without_progress_row_blocks(self):
        checklist, _ = self.checklist(schedule_learner="123e4567-e89b-42d3-a456-426614174999")
        checks = {c["check"]: c["passed"] for c in checklist["checks"]}
        self.assertFalse(checklist["release_ready"])
        self.assertFalse(checks["review_schedule_matches_learner_progress"])
        self.assertTrue(checklist["blocking_items"])

    def test_formula_injection_neutralised_in_queue(self):
        _, queue = self.checklist(reviewed_on="2027-06-01", claim_id="=cmd|' /C calc'!A0")
        self.assertNotIn("\n=cmd", queue)
        self.assertIn("'=cmd", queue)

    def test_unprovenanced_verified_mapping_rejected(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            root = Path(d); args = self.build(root)
            dump(root / "mapping.json", {"specialty": "urology", "candidates": [
                {"card_id": "K1", "competency_id": "U1", "status": "verified"}]})
            with self.assertRaises(ValueError): v12.run(args)


class V14Fixes(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True); self.root = Path(self.temp.name)
        self.fix = self.root / "fixtures"; self.fix.mkdir()
        V12Fixes().build(self.fix)
        self.cfg_path = self.fix / "cfg.json"

    def tearDown(self):
        self.temp.cleanup()

    def config(self, **extra):
        cfg = {"schema_version": 1, "as_of": "2026-10-31", "run_regression_tests": False,
               "inputs": {k: f"{k}.json" for k in ("cards", "curriculum", "mapping", "evidence", "learner_progress", "cohort_analytics")},
               "output_dir": "../build/release"}
        cfg["inputs"]["review_schedule"] = "review_schedule.csv"
        cfg.update(extra)
        dump(self.cfg_path, cfg)

    def run_cli(self):
        return subprocess.run([sys.executable, str(ROOT / "usiphus_v14.py"), "--config", str(self.cfg_path)],
                              cwd=ROOT, text=True, capture_output=True,
                              env={**__import__("os").environ, "PYTHONUTF8": "1"}, encoding="utf-8")

    def test_refuses_to_wipe_unrelated_directory(self):
        precious = self.root / "build" / "release"; precious.mkdir(parents=True)
        (precious / "thesis.docx").write_text("do not delete")
        self.config()
        result = self.run_cli()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual((precious / "thesis.docx").read_text(), "do not delete")

    def test_second_release_replaces_marked_bundle(self):
        self.config()
        self.assertEqual(self.run_cli().returncode, 0)
        self.assertTrue((self.root / "build/release/.usiphus_v14_bundle").is_file())
        self.assertEqual(self.run_cli().returncode, 0)

    def test_v15_defaults_demand_signed_anchored_audit_for_approved_claims(self):
        dump(self.fix / "sources.json", {"sources": [{"source_id": "S1", "title": "G", "source_type": "guideline",
              "authority": "pub", "jurisdiction": "XX", "version": "1"}]})
        e1 = v15.build_event("0" * 64, "r1", "submit", "2026-10-30T10:00:00Z", {"to": "in_review"})
        e2 = v15.build_event(e1["event_hash"], "r1", "approve", "2026-10-31T10:00:00Z", {"to": "approved"})
        claim = {"claim_id": "C1", "card_id": "K1", "claim": "x", "references": [{"source_id": "S1"}],
                 "status": "approved", "reviewed_on": "2026-10-31", "audit_events": [e1, e2],
                 "review_decisions": [{"reviewer_id": "R1", "decision": "approve"}]}
        dump(self.fix / "claims.json", {"claims": [claim]})
        self.config(v15_sources="sources.json", v15_claims="claims.json")
        result = self.run_cli()                       # no HMAC key in environment -> hard error
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("Signed audit is required", result.stderr + result.stdout)
        self.config(v15_sources="sources.json", v15_claims="claims.json",
                    v15_require_signed_audit=False, v15_require_audit_anchor=False)
        self.assertEqual(self.run_cli().returncode, 0)  # explicit opt-out still works
        claim["audit_anchor"] = e2["event_hash"]
        dump(self.fix / "claims.json", {"claims": [claim]})
        self.config(v15_sources="sources.json", v15_claims="claims.json", v15_require_signed_audit=False)
        self.assertEqual(self.run_cli().returncode, 0)  # anchor required and present

    def test_v15_stage_blocks_release_and_passes_when_clean(self):
        dump(self.fix / "sources.json", {"sources": [{"source_id": "S1", "title": "G", "source_type": "guideline",
              "authority": "pub", "jurisdiction": "XX", "version": "1"}]})
        draft = {"claim_id": "C1", "card_id": "K1", "claim": "x", "references": [{"source_id": "S1"}], "status": "draft"}
        dump(self.fix / "claims.json", {"claims": [draft]})
        self.config(v15_sources="sources.json", v15_claims="claims.json")
        self.assertEqual(self.run_cli().returncode, 0)
        report = json.loads((self.root / "build/release/v15/v15_governance_report.json").read_text())
        self.assertEqual(report["governance_status"], "checks_passed")
        bad = dict(draft, status="published", reviewed_on="2026-10-31",
                   review_decisions=[{"reviewer_id": "R1", "decision": "approve"}])
        dump(self.fix / "claims.json", {"claims": [bad]})  # published with no audit trail
        self.config(v15_sources="sources.json", v15_claims="claims.json",
                    v15_require_signed_audit=False, v15_require_audit_anchor=False)
        self.assertEqual(self.run_cli().returncode, 1)


if __name__ == "__main__":
    unittest.main()
