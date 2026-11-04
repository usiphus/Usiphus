"""Golden-output (characterization) harness for the Usiphus engines.

Each *scenario* runs one engine through its real command line against small
synthetic fixtures (or the shipped ``examples/``), then snapshots every file the
engine wrote.  A refactor that changes behaviour shows up as a diff against the
committed snapshot in ``tests/golden/expected/<scenario>/``.

Only genuinely volatile values are normalised:

* absolute temp-directory paths               -> ``<WORK>``
* keys a scenario lists in ``volatile_keys``  -> ``"<VOLATILE>"`` (wall-clock
  ``generated_at`` in V12/V13/V14)
* SHA-256 values equal to the hash of a file that itself contained volatile
  content (e.g. V14's manifest hashes V12's platform report)

Everything else is compared exactly.  Regenerate snapshots deliberately with::

    UPDATE_GOLDEN=1 python -m unittest tests.test_golden

and review the resulting git diff before committing.
"""
from __future__ import annotations

import difflib
import base64
import hashlib
import html
import io
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = Path(__file__).resolve().parent
FIXTURES = GOLDEN / "fixtures"
EXPECTED = GOLDEN / "expected"
EXAMPLES = ROOT / "examples"
STUB_RUNNER = GOLDEN / "stubs" / "runner.py"

SHA256 = re.compile(r"\b[0-9a-f]{64}\b")
LOG_TIMESTAMP = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} \| ", re.M)
WORK_TOKEN = "<WORK>"
VOLATILE_TOKEN = "<VOLATILE>"
SEVEN_ARTIFACTS = ("cards.json", "curriculum.json", "mapping.json", "evidence.json",
                   "learner_progress.json", "cohort_analytics.json", "review_schedule.csv")


@dataclass(frozen=True)
class Step:
    """One engine invocation.  ``stub`` runs it under tests/golden/stubs/runner.py (network blocked, services faked)."""
    script: str
    args: list[str]
    label: str = ""
    stdin: str | None = None
    stub: str | None = None


@dataclass(frozen=True)
class Scenario:
    name: str
    script: str
    # (work_dir) -> argv after the script name.  Inputs live in work/in, outputs in work/out.
    args: Callable[[Path], list[str]]
    # copies/creates inputs inside work/in
    setup: Callable[[Path], None]
    expected_exit: int = 0
    # Keys to mask as volatile: "key" (everywhere) or "relative/file.json|key" (only in that file).
    volatile_keys: frozenset[str] = frozenset()
    capture_stdout: bool = False
    capture_stderr: bool = False
    stub: str | None = None            # fake services for the primary step (see stubs/)
    stdin: str | None = None           # text piped to the primary step
    label: str = ""                    # name shown for the primary step in multi-step transcripts
    # follow-up steps run after the primary one: Step objects or callables(work) that edit files between steps
    then: Callable[[Path], list] | None = None
    # AND of OR-groups, e.g. (("mod:pptx",), ("mod:cryptography", "openssl3")); unmet -> test is skipped
    requires: tuple[tuple[str, ...], ...] = ()


# --------------------------------------------------------------------------- setup helpers
def _copy(dest: Path, *sources: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for source in sources:
        shutil.copy2(source, dest / source.name)


def fixtures(*names: str, base: Path = FIXTURES) -> Callable[[Path], None]:
    return lambda work: _copy(work / "in", *(base / n for n in names))


def seven_artifacts(base: Path) -> Callable[[Path], None]:
    return fixtures(*SEVEN_ARTIFACTS, base=base)


def v14_config(base: Path, extra: dict | None = None) -> Callable[[Path], None]:
    def setup(work: Path) -> None:
        seven_artifacts(base)(work)
        if extra:
            for name in extra.get("_copy", ()):
                _copy(work / "in", base / name)
        config = {"schema_version": 1, "inputs": {Path(n).stem: n for n in SEVEN_ARTIFACTS},
                  "output_dir": "../out", "as_of": "2026-11-01", "max_evidence_age_days": 730,
                  "run_regression_tests": False}
        config.update({k: v for k, v in (extra or {}).items() if k != "_copy"})
        # newline="\n": the config's SHA-256 is recorded in the manifest, so it must not vary by OS
        (work / "in" / "v14.config.json").write_text(json.dumps(config, indent=2), encoding="utf-8", newline="\n")
    return setup


def both(*steps: Callable[[Path], None]) -> Callable[[Path], None]:
    def setup(work: Path) -> None:
        for step in steps:
            step(work)
    return setup


def _copy_as(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _in(work: Path, name: str) -> str:
    return str(work / "in" / name)


def _out(work: Path, name: str = "") -> str:
    return str(work / "out" / name) if name else str(work / "out")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def audit_setup(work: Path) -> None:
    """Throwaway TEST-ONLY Ed25519 key (stored base64 so no bare PEM private key sits in the repo)."""
    folder = FIXTURES / "audit"
    _copy(work / "in", folder / "alice.pub", folder / "claims.json")
    key = work / "in" / "alice.key"
    key.write_bytes(base64.b64decode((folder / "TEST_ONLY_alice.key.b64").read_text(encoding="ascii")))
    if os.name == "posix":
        key.chmod(0o600)


def _tamper_audit_event(work: Path) -> None:
    payload = json.loads((work / "out" / "claims.2.json").read_text(encoding="utf-8"))
    payload["claims"][0]["audit_events"][0]["actor"] = "mallory"
    _write_text(work / "out" / "claims.tampered.json", json.dumps(payload, indent=2) + "\n")


def audit_steps(work: Path) -> list:
    inp, out = work / "in", work / "out"
    key = ["--key", str(inp / "alice.key")]

    def step(label: str, *args: str) -> Step:
        return Step("usiphus_audit.py", list(args), label)

    return [
        step("trust (already trusted)", "trust", "--keys", str(out / "trusted_keys.json"),
             "--actor", "alice", "--public", str(inp / "alice.pub")),
        step("append submit", "append", "--claims", str(inp / "claims.json"), "--claim-id", "C1", "--actor", "alice",
             "--action", "submit", "--to", "in_review", "--timestamp", "2026-10-30T10:00:00Z", *key,
             "--output", str(out / "claims.1.json")),
        step("append approve", "append", "--claims", str(out / "claims.1.json"), "--claim-id", "C1", "--actor", "alice",
             "--action", "approve", "--to", "approved", "--timestamp", "2026-10-31T10:00:00Z", *key,
             "--output", str(out / "claims.2.json")),
        step("append invalid transition", "append", "--claims", str(inp / "claims.json"), "--claim-id", "C2",
             "--actor", "alice", "--action", "approve", "--to", "approved", "--timestamp", "2026-10-31T10:00:00Z", *key,
             "--output", str(out / "claims.rejected.json")),
        step("anchors", "anchors", "--claims", str(out / "claims.2.json"), "--actor", "alice", *key,
             "--output", str(out / "anchors.json")),
        step("verify clean", "verify", "--claims", str(out / "claims.2.json"),
             "--trusted-keys", str(out / "trusted_keys.json"), "--anchors", str(out / "anchors.json")),
        _tamper_audit_event,
        step("verify tampered", "verify", "--claims", str(out / "claims.tampered.json"),
             "--trusted-keys", str(out / "trusted_keys.json"), "--anchors", str(out / "anchors.json")),
    ]


def _v7_args(extra: tuple[str, ...] = ()) -> Callable[[Path], list[str]]:
    def build(w: Path) -> list[str]:
        return ["--specialty", "urology", "--exam-cards", _in(w, "exam_cards.json"),
                "--case-library", _in(w, "case_library.json"), "--blueprint", _in(w, "blueprint.json"),
                "--output", _out(w), "--as-of", "2026-11-01", "--apkg", "--study-guide", "--anki-connect",
                "--deck-name", "Synthetic Deck",
                *[_in(w, "evidence_registry.json") if token == "REGISTRY" else token for token in extra]]
    return build


LEGACY = ("exam_cards.json", "case_library.json", "blueprint.json", "evidence_registry.json")
RESOURCE_FILES = ("deck_curriculum.json", "bad_curriculum.json")
SOURCE_TO_DECK = lambda w: ["--generate", "--curriculum", _in(w, "deck_curriculum.json"), "--formats", "anki",
                            "--output-dir", _out(w)]

V17_EXAMPLE_FILES = ("curriculum.json", "cards.json", "mapping.json", "curriculum.previous.json",
                     "assessments.json", "osce.json")
V18_EXAMPLE_FILES = ("sources.json", "claims.json", "cards.json", "assessments.json", "osce.json", "releases.json")
GOVERNED = {"v15_sources": "v15_sources.json", "v15_claims": "v15_claims.json",
            "v15_evidence_mode": "strict", "v15_block_on_failure": True, "v15_minimum_reviewers": 1,
            "v15_require_signed_audit": False, "v15_require_audit_anchor": False,
            "_copy": ("v15_sources.json", "v15_claims.json")}
CLOCK = frozenset({"generated_at"})

SCENARIOS: tuple[Scenario, ...] = (
    Scenario("v10_curriculum_mapping", "usiphus_v10.py",
             lambda w: ["--cards", _in(w, "cards.json"), "--curriculum", _in(w, "curriculum.json"),
                        "--output", _out(w)],
             fixtures("cards.json", "curriculum.json")),
    Scenario("v11_adaptive_learning", "usiphus_v11.py",
             lambda w: ["--curriculum", _in(w, "curriculum.json"), "--mapping", _in(w, "mapping.json"),
                        "--attempts", _in(w, "attempts.json"), "--as-of", "2026-11-01T00:00:00Z",
                        "--output", _out(w)],
             fixtures("curriculum.json", "mapping.json", "attempts.json")),
    Scenario("v12_integrated_platform", "usiphus_v12.py",
             lambda w: ["--cards", _in(w, "cards.json"), "--curriculum", _in(w, "curriculum.json"),
                        "--mapping", _in(w, "mapping.json"), "--evidence", _in(w, "evidence.json"),
                        "--learner-progress", _in(w, "learner_progress.json"),
                        "--cohort-analytics", _in(w, "cohort_analytics.json"),
                        "--review-schedule", _in(w, "review_schedule.csv"),
                        "--as-of", "2026-11-01", "--output", _out(w)],
             seven_artifacts(FIXTURES), volatile_keys=CLOCK),
    Scenario("v12_required_competency_gap", "usiphus_v12.py",
             lambda w: ["--cards", _in(w, "cards.json"), "--curriculum", _in(w, "curriculum.json"),
                        "--mapping", _in(w, "mapping.json"), "--evidence", _in(w, "evidence.json"),
                        "--learner-progress", _in(w, "learner_progress.json"),
                        "--cohort-analytics", _in(w, "cohort_analytics.json"),
                        "--review-schedule", _in(w, "review_schedule.csv"),
                        "--as-of", "2026-11-01", "--output", _out(w)],
             both(seven_artifacts(FIXTURES / "ready"), fixtures("curriculum.json", base=FIXTURES / "ready_gap")),
             volatile_keys=CLOCK),
    Scenario("v13_validation_engine", "usiphus_v13.py",
             lambda w: ["--input-dir", str(w / "in"), "--output-dir", str(w / "in" / "v12_output"),
                        "--report", _out(w, "v13_validation_report.json")],
             both(seven_artifacts(FIXTURES),
                  lambda w: shutil.copytree(FIXTURES / "v12_output", w / "in" / "v12_output")),
             volatile_keys=CLOCK),
    Scenario("v14_release_blocked", "usiphus_v14.py",
             lambda w: ["--config", _in(w, "v14.config.json")],
             v14_config(FIXTURES), expected_exit=1, volatile_keys=CLOCK),
    Scenario("v14_release_ready", "usiphus_v14.py",
             lambda w: ["--config", _in(w, "v14.config.json")],
             v14_config(FIXTURES / "ready"), volatile_keys=CLOCK),
    Scenario("v16_governed_release", "usiphus_v14.py",
             lambda w: ["--config", _in(w, "v14.config.json")],
             v14_config(FIXTURES / "ready", GOVERNED), volatile_keys=CLOCK),
    Scenario("v15_evidence_governance_example", "usiphus_v15.py",
             lambda w: ["--sources", _in(w, "v15.sources.example.json"),
                        "--claims", _in(w, "v15.claims.example.json"), "--as-of", "2026-10-31",
                        "--require-registered-sources", "--output-dir", _out(w)],
             fixtures("v15.sources.example.json", "v15.claims.example.json", base=EXAMPLES)),
    Scenario("v15_evidence_governance_findings", "usiphus_v15.py",
             lambda w: ["--sources", _in(w, "sources.json"), "--claims", _in(w, "claims.json"),
                        "--as-of", "2026-10-31", "--require-registered-sources", "--output-dir", _out(w)],
             fixtures("sources.json", "claims.json", base=FIXTURES / "v15_findings"), expected_exit=1),
    Scenario("v15_reviewer_quorum_and_staleness", "usiphus_v15.py",
             lambda w: ["--sources", _in(w, "sources.json"), "--claims", _in(w, "claims.json"),
                        "--as-of", "2026-10-31", "--minimum-reviewers", "2",
                        "--require-registered-sources", "--output-dir", _out(w)],
             fixtures("sources.json", "claims.json", base=FIXTURES / "v15_quorum"), expected_exit=1),
    Scenario("v17_curriculum_intelligence", "usiphus_v17.py",
             lambda w: ["--curriculum", _in(w, "curriculum.json"), "--cards", _in(w, "cards.json"),
                        "--mapping", _in(w, "mapping.json"),
                        "--previous-curriculum", _in(w, "curriculum.previous.json"),
                        "--assessments", _in(w, "assessments.json"), "--osce", _in(w, "osce.json"),
                        "--output-dir", _out(w)],
             fixtures(*V17_EXAMPLE_FILES, base=EXAMPLES / "v17")),
    Scenario("v17_pilot_metrics", "usiphus_v17.py",
             lambda w: ["--curriculum", _in(w, "curriculum.json"), "--cards", _in(w, "cards.json"),
                        "--pilot-metrics", _in(w, "v17_pilot.csv"), "--output-dir", _out(w)],
             both(fixtures("curriculum.json", "cards.json", base=EXAMPLES / "v17"), fixtures("v17_pilot.csv"))),
    Scenario("v17_contract_validation", "usiphus_v17.py",
             lambda w: ["--validate", _in(w, "cards.json"), "--contract-type", "card"],
             fixtures("cards.json", base=EXAMPLES / "v17"), capture_stdout=True),
    Scenario("v17_contract_migration", "usiphus_v17.py",
             lambda w: ["--migrate", _in(w, "curriculum.previous.json"), "--contract-type", "curriculum",
                        "--migration-output", _out(w, "curriculum.v17.json")],
             fixtures("curriculum.previous.json", base=EXAMPLES / "v17"), capture_stdout=True),
    Scenario("v18_evidence_lifecycle_example", "usiphus_v18.py",
             lambda w: ["--sources", _in(w, "sources.json"), "--claims", _in(w, "claims.json"),
                        "--cards", _in(w, "cards.json"), "--assessments", _in(w, "assessments.json"),
                        "--osce", _in(w, "osce.json"), "--release-links", _in(w, "releases.json"),
                        "--as-of", "2026-11-01T00:00:00Z", "--output-dir", _out(w)],
             fixtures(*V18_EXAMPLE_FILES, base=EXAMPLES / "v18")),
    Scenario("v18_evidence_lifecycle_changes", "usiphus_v18.py",
             lambda w: ["--sources", _in(w, "sources.json"), "--claims", _in(w, "claims.json"),
                        "--cards", _in(w, "cards.json"), "--assessments", _in(w, "assessments.json"),
                        "--osce", _in(w, "osce.json"), "--release-links", _in(w, "releases.json"),
                        "--previous-state", _in(w, "previous_state.json"),
                        "--as-of", "2026-11-01T00:00:00Z", "--output-dir", _out(w)],
             both(fixtures("cards.json", "assessments.json", "osce.json", "releases.json", base=EXAMPLES / "v18"),
                  fixtures("sources.json", "claims.json", "previous_state.json", base=FIXTURES / "v18_changed"))),
    Scenario("v19_assessment_studio", "usiphus_v19.py",
             lambda w: ["--input", _in(w, "studio.json"), "--output-dir", _out(w)],
             fixtures("studio.json", base=EXAMPLES / "v19")),
    Scenario("v20_feedback_authoring", "usiphus_v20.py",
             lambda w: ["--input", _in(w, "workflow.json"), "--output-dir", _out(w)],
             fixtures("workflow.json", base=EXAMPLES / "v20")),
    # ---- engines that need network / LLM / keys / a TTY, run under tests/golden/stubs ----
    Scenario("resource_discovery_stubbed", "usiphus_resource_discovery.py",
             lambda w: ["--curriculum", _in(w, "deck_curriculum.json"), "--output-dir", _out(w),
                        "--delay", "0", "--limit", "3"],
             fixtures("deck_curriculum.json"), stub="discovery", capture_stdout=True,
             volatile_keys=frozenset({"retrieved_at", "generated_at"})),
    Scenario("resource_discovery_invalid_curriculum", "usiphus_resource_discovery.py",
             lambda w: ["--curriculum", _in(w, "bad_curriculum.json"), "--output-dir", _out(w), "--delay", "0"],
             fixtures("bad_curriculum.json"), stub="discovery", capture_stderr=True, expected_exit=2),
    Scenario("auto_deck_generate_anki", "usiphus_auto_deck.py", SOURCE_TO_DECK,
             fixtures("deck_curriculum.json"), stub="deck", capture_stdout=True),
    Scenario("auto_deck_quality_gate_blocked", "usiphus_auto_deck.py", SOURCE_TO_DECK,
             fixtures("deck_curriculum.json"), stub="deck_lowquality", capture_stderr=True, expected_exit=2),
    Scenario("auto_deck_existing_cards_anki", "usiphus_auto_deck.py",
             lambda w: ["--cards", _in(w, "cards.json"), "--specialty", "urology", "--formats", "anki",
                        "--output-dir", _out(w)],
             fixtures("cards.json"), stub="deck", capture_stdout=True),
    Scenario("auto_deck_existing_cards_powerpoint", "usiphus_auto_deck.py",
             lambda w: ["--cards", _in(w, "cards.json"), "--specialty", "urology", "--formats", "powerpoint",
                        "--output-dir", _out(w)],
             fixtures("cards.json"), stub="deck", capture_stdout=True, requires=(("mod:pptx",),)),
    Scenario("generate_stubbed", "usiphus_generate.py",
             lambda w: ["--config", _in(w, "config.json")],
             fixtures("config.json", "curriculum.json", "guideline.txt", base=FIXTURES / "generate"), stub="generate"),
    Scenario("generate_rejects_unsupported_evidence", "usiphus_generate.py",
             lambda w: ["--config", _in(w, "config.json")],
             fixtures("config.json", "curriculum.json", "guideline.txt", base=FIXTURES / "generate"),
             stub="generate_hallucinated", capture_stderr=True, expected_exit=2),
    Scenario("generate_resume_from_checkpoint", "usiphus_generate.py",
             lambda w: ["--config", _in(w, "config.json")],
             both(fixtures("config.json", "curriculum.json", "guideline.txt", base=FIXTURES / "generate"),
                  lambda w: _copy_as(FIXTURES / "generate" / "checkpoint.json", w / "out" / "generated_cards.json")),
             stub="generate"),
    Scenario("review_session", "usiphus_review.py",
             lambda w: ["--cards", _in(w, "cards.json"), "--reviewer", "synthetic-reviewer",
                        "--store", _out(w, "reviews.json"), "--export", _out(w, "personal_deck.json")],
             fixtures("cards.json", base=FIXTURES / "review"), capture_stdout=True, capture_stderr=True,
             stdin="k\nx\ne\nNew prompt text\n\nNew objective\nr\ns\nq\n",
             volatile_keys=frozenset({"updated_at"})),
    Scenario("review_resume_session", "usiphus_review.py",
             lambda w: ["--cards", _in(w, "cards.json"), "--reviewer", "synthetic-reviewer",
                        "--store", _out(w, "reviews.json"), "--export", _out(w, "personal_deck.json")],
             both(fixtures("cards.json", base=FIXTURES / "review"),
                  lambda w: _copy_as(FIXTURES / "review" / "store.json", w / "out" / "reviews.json")),
             capture_stdout=True, stdin="k\nk\n", volatile_keys=frozenset({"updated_at"})),
    Scenario("review_path_collision", "usiphus_review.py",
             lambda w: ["--cards", _in(w, "cards.json"), "--reviewer", "x", "--store", _in(w, "cards.json")],
             fixtures("cards.json", base=FIXTURES / "review"), capture_stderr=True, expected_exit=2),
    Scenario("review_rejects_corrupt_store", "usiphus_review.py",
             lambda w: ["--cards", _in(w, "cards.json"), "--reviewer", "x", "--store", _in(w, "store.json")],
             both(fixtures("cards.json", base=FIXTURES / "review"),
                  lambda w: _copy_as(FIXTURES / "review" / "store_corrupt.json", w / "in" / "store.json")),
             capture_stderr=True, expected_exit=2),
    Scenario("audit_signed_chain_and_tamper", "usiphus_audit.py",
             lambda w: ["trust", "--keys", _out(w, "trusted_keys.json"), "--actor", "alice",
                        "--public", _in(w, "alice.pub")],
             audit_setup, label="trust (new key)", capture_stdout=True, expected_exit=1,
             then=audit_steps, requires=(("mod:cryptography", "openssl3"),),
             volatile_keys=frozenset({"anchors.json|generated_at", "anchors.json|signature"})),
    Scenario("v7_pipeline_stubbed_anki", "usiphus_v7.py", _v7_args(),
             fixtures(*LEGACY[:3], base=FIXTURES / "legacy"), stub="anki",
             volatile_keys=frozenset({"created_unix"})),
    Scenario("v7_quality_gate_blocked", "usiphus_v7.py",
             lambda w: ["--specialty", "urology", "--exam-cards", _in(w, "exam_cards.json"),
                        "--output", _out(w), "--as-of", "2026-11-01"],
             fixtures("exam_cards.json", base=FIXTURES / "legacy_blocked"), stub="anki", expected_exit=1,
             capture_stderr=True),
    Scenario("v8_pipeline_with_evidence_registry", "usiphus_v8.py",
             _v7_args(("--evidence-registry", "REGISTRY")),
             fixtures(*LEGACY, base=FIXTURES / "legacy"), stub="anki",
             volatile_keys=frozenset({"created_unix"})),
    Scenario("v8_require_approved_evidence_blocked", "usiphus_v8.py",
             _v7_args(("--evidence-registry", "REGISTRY", "--require-approved-evidence")),
             fixtures(*LEGACY, base=FIXTURES / "legacy"), stub="anki", expected_exit=1, capture_stderr=True,
             volatile_keys=frozenset({"created_unix"})),
    Scenario("v9_pipeline_semantic_tfidf", "usiphus_v9.py",
             _v7_args(("--evidence-registry", "REGISTRY", "--semantic-backend", "tfidf")),
             fixtures(*LEGACY, base=FIXTURES / "legacy"), stub="anki",
             volatile_keys=frozenset({"created_unix"})),
)
BY_NAME = {s.name: s for s in SCENARIOS}
assert len(BY_NAME) == len(SCENARIOS), "duplicate scenario name"


# --------------------------------------------------------------------------- running
@dataclass
class Result:
    exit_code: int
    files: dict[str, str]       # relative posix path -> normalised text
    stderr_tail: str = ""


def requirement_met(token: str) -> bool:
    if token.startswith("mod:"):
        return importlib.util.find_spec(token[4:]) is not None
    if token == "openssl3":
        exe = shutil.which("openssl")
        if not exe:
            return False
        try:
            out = subprocess.run([exe, "version"], capture_output=True, text=True, timeout=20).stdout
        except (OSError, subprocess.SubprocessError):
            return False
        return out.startswith("OpenSSL 3")
    raise ValueError(f"unknown requirement token: {token}")


def unmet_requirements(scenario: Scenario) -> list[str]:
    return [" or ".join(group) for group in scenario.requires if not any(requirement_met(t) for t in group)]


def _execute(step: Step, work: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    cmd = [sys.executable]
    step_env = dict(env)
    if step.stub:
        cmd += [str(STUB_RUNNER), step.stub]
        step_env["USIPHUS_STUB_LOG"] = str(work / "stub_requests.json")
    cmd += [str(ROOT / step.script), *step.args]
    return subprocess.run(cmd, cwd=work, env=step_env, text=True, encoding="utf-8",
                          input=step.stdin if step.stdin is not None else "",   # EOF, never the runner's stdin
                          capture_output=True, check=False)


def run_scenario(scenario: Scenario, hash_seed: str = "1") -> Result:
    """Run one scenario in a fresh temp dir and return its normalised outputs."""
    with tempfile.TemporaryDirectory(prefix=f"usiphus-golden-{scenario.name}-",
                                     ignore_cleanup_errors=True) as tmp:
        work = Path(tmp).resolve()
        (work / "in").mkdir()
        scenario.setup(work)
        env = {k: v for k, v in os.environ.items() if not k.startswith("USIPHUS_")}
        env.update(PYTHONUTF8="1", PYTHONHASHSEED=hash_seed)
        primary = Step(scenario.script, scenario.args(work), scenario.label or Path(scenario.script).stem,
                       scenario.stdin, scenario.stub)
        plan: list = [primary] + (scenario.then(work) if scenario.then else [])
        done: list[tuple[Step, subprocess.CompletedProcess]] = []
        for item in plan:
            if callable(item):
                item(work)
            else:
                done.append((item, _execute(item, work, env)))
        last = done[-1][1]
        raw: dict[str, bytes] = {}
        out = work / "out"
        if out.is_dir():
            for path in sorted(p for p in out.rglob("*") if p.is_file()):
                raw[path.relative_to(out).as_posix()] = path.read_bytes()
        if (work / "stub_requests.json").is_file():
            raw["_requests.json"] = (work / "stub_requests.json").read_bytes()
        if scenario.capture_stdout:
            if len(done) == 1:
                raw["_stdout.txt"] = last.stdout.encode("utf-8")
            else:   # transcript of every step: label, exit code, stdout, stderr
                parts = []
                for step, proc in done:
                    parts.append(f"$ {step.label}\nexit={proc.returncode}\n--- stdout ---\n{proc.stdout}"
                                 f"--- stderr ---\n{proc.stderr}")
                raw["_transcript.txt"] = "\n".join(parts).encode("utf-8")
        if scenario.capture_stderr:
            raw["_stderr.txt"] = last.stderr.encode("utf-8")
        return Result(last.returncode, normalise(raw, work, scenario.volatile_keys), last.stderr[-1500:])


def _path_variants(work: Path) -> list[str]:
    variants = {str(work), work.as_posix(), str(work.resolve()), work.resolve().as_posix()}
    return sorted(variants, key=len, reverse=True)


def _strip_paths(text: str, paths: list[str], touched: list[bool]) -> str:
    """Replace the temp work dir with <WORK>; the path tail is forced to '/' so Windows matches POSIX."""
    for prefix in paths:
        if prefix in text:
            text = re.sub(re.escape(prefix) + r"((?:[\\/][^\s\"',;:<>|*?]*)?)",
                          lambda m: WORK_TOKEN + m.group(1).replace("\\", "/"), text)
            touched[0] = True
    return text


def _mask_value(value, keys: frozenset[str], paths: list[str], touched: list[bool]):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            # paths also appear as dict *keys* (e.g. V18 input_hashes)
            new_key = _strip_paths(key, paths, touched)
            if key in keys:
                out[new_key] = VOLATILE_TOKEN
                touched[0] = True
            else:
                out[new_key] = _mask_value(item, keys, paths, touched)
        return out
    if isinstance(value, list):
        return [_mask_value(item, keys, paths, touched) for item in value]
    if isinstance(value, str):
        return _strip_paths(value, paths, touched)
    return value


def summarise_pptx(data: bytes) -> str:
    """Deterministic text view of a .pptx (slide order and text only; zip metadata/timestamps ignored)."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        slides = sorted((n for n in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                        key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[1]).group(1)))
        lines = [f"pptx slides: {len(slides)}"]
        for number, name in enumerate(slides, 1):
            xml = archive.read(name).decode("utf-8")
            texts = [html.unescape(t) for t in re.findall(r"<a:t>(.*?)</a:t>", xml, re.S)]
            lines.append(f"slide {number}: " + " | ".join(texts))
    return "\n".join(lines) + "\n"


def _keys_for(name: str, volatile_keys: frozenset[str]) -> frozenset[str]:
    keys = set()
    for spec in volatile_keys:
        file_part, sep, key = spec.partition("|")
        if not sep:
            keys.add(spec)
        elif file_part == name:
            keys.add(key)
    return frozenset(keys)


def normalise(raw: dict[str, bytes], work: Path, volatile_keys: frozenset[str]) -> dict[str, str]:
    paths = _path_variants(work)
    texts: dict[str, str] = {}
    volatile_hashes: dict[str, str] = {}
    for name, data in raw.items():
        if name.endswith(".pptx"):
            texts[name] = summarise_pptx(data)
            continue
        try:
            text = data.decode("utf-8").replace("\r\n", "\n")
        except UnicodeDecodeError:
            texts[name] = f"<binary sha256={hashlib.sha256(data).hexdigest()}>"
            continue
        if name in ("_stderr.txt", "_transcript.txt"):    # log lines: "2026-11-02 21:25:17,732 | ERROR | ..."
            text = LOG_TIMESTAMP.sub("<TS> | ", text)
        keys = _keys_for(name, volatile_keys)
        touched = [False]
        parsed = None
        if name.endswith(".json") or name == "_stdout.txt":
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
        if parsed is not None:
            text = json.dumps(_mask_value(parsed, keys, paths, touched),
                              indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        else:
            text = _strip_paths(text, paths, touched)
        if touched[0]:
            volatile_hashes[hashlib.sha256(data).hexdigest()] = name
        texts[name] = text
    if volatile_hashes:   # hashes of volatile files change run to run; pin only the file they refer to
        for name, text in texts.items():
            texts[name] = SHA256.sub(
                lambda m: f"<SHA256 of volatile {volatile_hashes[m.group(0)]}>"
                if m.group(0) in volatile_hashes else m.group(0), text)
    return texts


# --------------------------------------------------------------------------- snapshots
def update_requested() -> bool:
    return os.environ.get("UPDATE_GOLDEN", "") not in ("", "0", "false", "False")


def write_snapshot(scenario: Scenario, result: Result) -> None:
    target = EXPECTED / scenario.name
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for name, text in result.files.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    meta = {"exit_code": result.exit_code, "files": sorted(result.files)}
    (target / "_meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8", newline="\n")


def read_snapshot(scenario: Scenario) -> tuple[dict, dict[str, str]] | None:
    target = EXPECTED / scenario.name
    meta_path = target / "_meta.json"
    if not meta_path.is_file():
        return None
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    files = {name: (target / name).read_text(encoding="utf-8") if (target / name).is_file() else None
             for name in meta["files"]}
    return meta, files


def compare(scenario: Scenario, result: Result, max_diff_lines: int = 40) -> list[str]:
    """Return human-readable differences (empty list means identical)."""
    snapshot = read_snapshot(scenario)
    if snapshot is None:
        return [f"no snapshot for {scenario.name}; run UPDATE_GOLDEN=1 python -m unittest tests.test_golden"]
    meta, expected = snapshot
    problems: list[str] = []
    if result.exit_code != meta["exit_code"]:
        problems.append(f"exit code {result.exit_code} != expected {meta['exit_code']}"
                        + (f"\n--- stderr tail ---\n{result.stderr_tail}" if result.stderr_tail else ""))
    for name in sorted(set(expected) - set(result.files)):
        problems.append(f"missing output file: {name}")
    for name in sorted(set(result.files) - set(expected)):
        problems.append(f"unexpected new output file: {name}")
    for name in sorted(set(expected) & set(result.files)):
        if expected[name] is None:
            problems.append(f"snapshot file missing on disk: {name}")
        elif expected[name] != result.files[name]:
            diff = list(difflib.unified_diff(expected[name].splitlines(), result.files[name].splitlines(),
                                             f"expected/{name}", f"actual/{name}", lineterm="", n=2))
            shown = diff[:max_diff_lines]
            if len(diff) > max_diff_lines:
                shown.append(f"... {len(diff) - max_diff_lines} more diff lines")
            problems.append(f"{name} differs:\n" + "\n".join(shown))
    return problems


def snapshot_names() -> Iterable[str]:
    return sorted(p.name for p in EXPECTED.iterdir() if p.is_dir()) if EXPECTED.is_dir() else []
