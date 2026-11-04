"""Golden-output tests: pin the observable behaviour of every engine before refactoring.

Run:      python -m unittest tests.test_golden -v
Update:   UPDATE_GOLDEN=1 python -m unittest tests.test_golden   (then review `git diff tests/golden/expected`)

See tests/golden/README.md for what is and is not normalised.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.golden import harness as h

_CACHE: dict[tuple[str, str], h.Result] = {}


def result_for(scenario: h.Scenario, seed: str = "1") -> h.Result:
    key = (scenario.name, seed)
    if key not in _CACHE:
        _CACHE[key] = h.run_scenario(scenario, hash_seed=seed)
    return _CACHE[key]


class GoldenOutputs(unittest.TestCase):
    """One generated test per scenario: ``test_<scenario name>``."""


class Determinism(unittest.TestCase):
    """Outputs must not depend on hash randomisation (set/dict ordering) or wall-clock time."""


def _make_golden_test(scenario: h.Scenario):
    def test(self: unittest.TestCase) -> None:
        missing = h.unmet_requirements(scenario)
        if missing:
            self.skipTest("requires " + "; ".join(missing))
        result = result_for(scenario)
        if h.update_requested():
            self.assertEqual(result.exit_code, scenario.expected_exit,
                             f"refusing to snapshot unexpected exit code\n{result.stderr_tail}")
            h.write_snapshot(scenario, result)
            return
        problems = h.compare(scenario, result)
        self.assertEqual(problems, [], f"\n{scenario.name}: behaviour changed vs tests/golden/expected/\n"
                         + "\n\n".join(problems))
        self.assertEqual(result.exit_code, scenario.expected_exit)
    test.__name__ = f"test_{scenario.name}"
    return test


def _make_determinism_test(scenario: h.Scenario):
    def test(self: unittest.TestCase) -> None:
        missing = h.unmet_requirements(scenario)
        if missing:
            self.skipTest("requires " + "; ".join(missing))
        first, second = result_for(scenario, "1"), result_for(scenario, "2")
        self.assertEqual(first.exit_code, second.exit_code)
        self.assertEqual(sorted(first.files), sorted(second.files))
        for name in first.files:
            self.assertEqual(first.files[name], second.files[name],
                             f"{scenario.name}/{name} differs between PYTHONHASHSEED=1 and 2")
    test.__name__ = f"test_{scenario.name}"
    return test


for _scenario in h.SCENARIOS:
    setattr(GoldenOutputs, f"test_{_scenario.name}", _make_golden_test(_scenario))
    setattr(Determinism, f"test_{_scenario.name}", _make_determinism_test(_scenario))


class StubSafety(unittest.TestCase):
    def test_stubbed_scenarios_record_requests_and_block_the_network(self) -> None:
        stubbed = [s for s in h.SCENARIOS if s.stub]
        self.assertGreaterEqual(len(stubbed), 10)
        probe = h.GOLDEN / "stubs" / "runner.py"
        # a stubbed engine that tries a real connection must fail loudly, not reach the internet
        import subprocess, sys
        code = ("import urllib.request; import socket; "
                "socket.create_connection(('example.org', 80), timeout=2)")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            script = Path(tmp) / "probe_engine.py"
            script.write_text(code, encoding="utf-8")
            proc = subprocess.run([sys.executable, str(probe), "discovery", str(script)],
                                  capture_output=True, text=True, timeout=60)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("network access is blocked", proc.stderr)


class SnapshotInventory(unittest.TestCase):
    def test_every_scenario_has_a_snapshot_and_vice_versa(self) -> None:
        if h.update_requested():
            self.skipTest("updating snapshots")
        self.assertEqual(sorted(h.snapshot_names()), sorted(s.name for s in h.SCENARIOS),
                         "scenario list and tests/golden/expected/ are out of sync")

    def test_snapshots_are_not_vacuous(self) -> None:
        if h.update_requested():
            self.skipTest("updating snapshots")
        for scenario in h.SCENARIOS:
            snapshot = h.read_snapshot(scenario)
            self.assertIsNotNone(snapshot, scenario.name)
            meta, files = snapshot
            self.assertGreater(len(meta["files"]), 0, f"{scenario.name} snapshots no files")
            self.assertTrue(all(text for text in files.values()), f"{scenario.name} has an empty snapshot file")

    def test_no_snapshot_contains_a_machine_specific_path(self) -> None:
        if h.update_requested():
            self.skipTest("updating snapshots")
        banned = (tempfile.gettempdir().replace("\\", "/"), "/home/", "/Users/", "C:\\Users")
        for scenario in h.SCENARIOS:
            snapshot = h.read_snapshot(scenario)
            self.assertIsNotNone(snapshot, scenario.name)
            for name, text in snapshot[1].items():
                for fragment in banned:
                    self.assertNotIn(fragment, text, f"{scenario.name}/{name} leaks a local path")


class NormaliserTests(unittest.TestCase):
    """The normaliser is what keeps golden tests honest: it must hide only volatile data."""

    def setUp(self) -> None:
        self.work = Path(tempfile.gettempdir()).resolve() / "usiphus-golden-unit"

    def test_masks_only_requested_keys(self) -> None:
        raw = {"a.json": json.dumps({"generated_at": "now", "keep": "x"}).encode()}
        masked = h.normalise(raw, self.work, frozenset({"generated_at"}))
        self.assertEqual(json.loads(masked["a.json"]), {"generated_at": h.VOLATILE_TOKEN, "keep": "x"})
        unmasked = h.normalise(raw, self.work, frozenset())
        self.assertEqual(json.loads(unmasked["a.json"])["generated_at"], "now")

    def test_replaces_work_dir_in_json_and_text(self) -> None:
        raw = {"m.json": json.dumps({"path": f"{self.work}/in/x.json"}).encode(),
               "r.md": f"see {self.work.as_posix()}/out\n".encode()}
        out = h.normalise(raw, self.work, frozenset())
        self.assertEqual(json.loads(out["m.json"])["path"], "<WORK>/in/x.json")
        self.assertEqual(out["r.md"], "see <WORK>/out\n")

    def test_path_tail_uses_forward_slashes(self) -> None:
        raw = {"m.json": json.dumps({"path": f"{self.work}\\in\\x.json"}).encode()}
        self.assertEqual(json.loads(h.normalise(raw, self.work, frozenset())["m.json"])["path"],
                         "<WORK>/in/x.json")

    def test_replaces_work_dir_in_json_keys(self) -> None:
        raw = {"r.json": json.dumps({"input_hashes": {f"{self.work}/in/a.json": "h"}}).encode()}
        out = h.normalise(raw, self.work, frozenset())
        self.assertEqual(json.loads(out["r.json"]), {"input_hashes": {"<WORK>/in/a.json": "h"}})

    def test_hash_of_volatile_file_is_pinned_but_other_hashes_are_kept(self) -> None:
        import hashlib
        volatile = json.dumps({"generated_at": "t1"}).encode()
        stable_hash = "ab" * 32
        volatile_hash = hashlib.sha256(volatile).hexdigest()
        manifest = json.dumps({"outputs": {"v.json": {"sha256": volatile_hash},
                                           "s.json": {"sha256": stable_hash}}}).encode()
        out = h.normalise({"v.json": volatile, "manifest.json": manifest}, self.work, frozenset({"generated_at"}))
        body = json.loads(out["manifest.json"])["outputs"]
        self.assertEqual(body["v.json"]["sha256"], "<SHA256 of volatile v.json>")
        self.assertEqual(body["s.json"]["sha256"], stable_hash)

    def test_changed_value_is_not_hidden(self) -> None:
        a = h.normalise({"a.json": b'{"score": 1}'}, self.work, frozenset({"generated_at"}))
        b = h.normalise({"a.json": b'{"score": 2}'}, self.work, frozenset({"generated_at"}))
        self.assertNotEqual(a, b)

    def test_crlf_is_normalised(self) -> None:
        out = h.normalise({"q.csv": b"a,b\r\n1,2\r\n"}, self.work, frozenset())
        self.assertEqual(out["q.csv"], "a,b\n1,2\n")


if __name__ == "__main__":
    unittest.main()
