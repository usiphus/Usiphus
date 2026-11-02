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
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = Path(__file__).resolve().parent
FIXTURES = GOLDEN / "fixtures"
EXPECTED = GOLDEN / "expected"
EXAMPLES = ROOT / "examples"

SHA256 = re.compile(r"\b[0-9a-f]{64}\b")
WORK_TOKEN = "<WORK>"
VOLATILE_TOKEN = "<VOLATILE>"
SEVEN_ARTIFACTS = ("cards.json", "curriculum.json", "mapping.json", "evidence.json",
                   "learner_progress.json", "cohort_analytics.json", "review_schedule.csv")


@dataclass(frozen=True)
class Scenario:
    name: str
    script: str
    # (work_dir) -> argv after the script name.  Inputs live in work/in, outputs in work/out.
    args: Callable[[Path], list[str]]
    # copies/creates inputs inside work/in
    setup: Callable[[Path], None]
    expected_exit: int = 0
    volatile_keys: frozenset[str] = frozenset()
    capture_stdout: bool = False


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


def _in(work: Path, name: str) -> str:
    return str(work / "in" / name)


def _out(work: Path, name: str = "") -> str:
    return str(work / "out" / name) if name else str(work / "out")


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
)
BY_NAME = {s.name: s for s in SCENARIOS}
assert len(BY_NAME) == len(SCENARIOS), "duplicate scenario name"


# --------------------------------------------------------------------------- running
@dataclass
class Result:
    exit_code: int
    files: dict[str, str]       # relative posix path -> normalised text
    stderr_tail: str = ""


def run_scenario(scenario: Scenario, hash_seed: str = "1") -> Result:
    """Run one scenario in a fresh temp dir and return its normalised outputs."""
    with tempfile.TemporaryDirectory(prefix=f"usiphus-golden-{scenario.name}-",
                                     ignore_cleanup_errors=True) as tmp:
        work = Path(tmp).resolve()
        (work / "in").mkdir()
        scenario.setup(work)
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONHASHSEED": hash_seed}
        proc = subprocess.run([sys.executable, str(ROOT / scenario.script), *scenario.args(work)],
                              cwd=work, env=env, text=True, encoding="utf-8",
                              capture_output=True, check=False)
        raw: dict[str, bytes] = {}
        out = work / "out"
        if out.is_dir():
            for path in sorted(p for p in out.rglob("*") if p.is_file()):
                raw[path.relative_to(out).as_posix()] = path.read_bytes()
        if scenario.capture_stdout:
            raw["_stdout.txt"] = proc.stdout.encode("utf-8")
        return Result(proc.returncode, normalise(raw, work, scenario.volatile_keys), proc.stderr[-1500:])


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


def normalise(raw: dict[str, bytes], work: Path, volatile_keys: frozenset[str]) -> dict[str, str]:
    paths = _path_variants(work)
    texts: dict[str, str] = {}
    volatile_hashes: dict[str, str] = {}
    for name, data in raw.items():
        try:
            text = data.decode("utf-8").replace("\r\n", "\n")
        except UnicodeDecodeError:
            texts[name] = f"<binary sha256={hashlib.sha256(data).hexdigest()}>"
            continue
        touched = [False]
        parsed = None
        if name.endswith(".json") or name == "_stdout.txt":
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
        if parsed is not None:
            text = json.dumps(_mask_value(parsed, volatile_keys, paths, touched),
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
