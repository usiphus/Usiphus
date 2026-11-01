
"""Second hardening pass: asymmetric signing + anchors, release ledger, pointer publishing, portability.

Run with: python -m unittest discover -s tests -t . -v   (synthetic, non-clinical data only)
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

import test_regressions as base  # noqa: E402  (module import, so its tests are not collected twice)

ROOT = base.ROOT
import usiphus_audit as audit  # noqa: E402
import usiphus_ledger as ledger_mod  # noqa: E402
import usiphus_signing as sg  # noqa: E402

v11, v14, v15, mig = base.v11, base.v14, base.v15, base.mig
HAVE_OPENSSL = shutil.which("openssl") is not None
POSIX = os.name == "posix"
UTF8 = {**os.environ, "PYTHONUTF8": "1"}


def backends():
    out = []
    if sg.HAVE_CRYPTOGRAPHY:
        out.append(False)
    if HAVE_OPENSSL:
        out.append(True)
    return out


class SigningBackends(unittest.TestCase):
    def tearDown(self):
        sg._FORCE_OPENSSL = False

    def test_at_least_one_backend_available(self):
        self.assertTrue(backends(), "install `cryptography` or provide the openssl command line tool")

    def test_sign_verify_roundtrip_each_backend_with_and_without_passphrase(self):
        for force in backends():
            sg._FORCE_OPENSSL = force
            for pw in (None, b"correct horse"):
                with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
                    d = Path(d); kid = sg.generate_keypair(d / "k.pem", d / "p.pem", pw)
                    signer = sg.Ed25519Signer(d / "k.pem", pw)
                    self.assertEqual(signer.key_id, kid)
                    s = signer.sign("hello")
                    trusted = sg.TrustedKeys([("alice", (d / "p.pem").read_text())])
                    self.assertTrue(trusted.verify("hello", s, "alice"))
                    self.assertFalse(trusted.verify("hellO", s, "alice"))          # tampered message
                    self.assertFalse(trusted.verify("hello", s, "bob"))            # wrong actor
                    self.assertFalse(trusted.verify("hello", "ed25519:" + kid + ":AAAA", "alice"))
                    self.assertFalse(trusted.verify("hello", "garbage", "alice"))
                    self.assertFalse(trusted.verify("hello", "ed25519:0000000000000000:" + s.split(":")[2], "alice"))  # unknown key id

    def test_cross_backend_interoperability(self):
        if len(backends()) < 2:
            self.skipTest("needs both cryptography and openssl")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            d = Path(d); sg._FORCE_OPENSSL = False
            sg.generate_keypair(d / "k.pem", d / "p.pem")
            s = sg.Ed25519Signer(d / "k.pem").sign("m")
            sg._FORCE_OPENSSL = True
            self.assertTrue(sg.TrustedKeys([("a", (d / "p.pem").read_text())]).verify("m", s, "a"))

    def test_wrong_or_missing_passphrase_rejected(self):
        if not sg.HAVE_CRYPTOGRAPHY:
            self.skipTest("cryptography backend only")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            d = Path(d); sg.generate_keypair(d / "k.pem", d / "p.pem", b"pw")
            with self.assertRaises(sg.SigningError): sg.Ed25519Signer(d / "k.pem", None)
            with self.assertRaises(sg.SigningError): sg.Ed25519Signer(d / "k.pem", b"nope")

    @unittest.skipUnless(POSIX, "POSIX permission bits")
    def test_loose_private_key_permissions_rejected_and_keygen_is_0600(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            d = Path(d); sg.generate_keypair(d / "k.pem", d / "p.pem")
            self.assertEqual((d / "k.pem").stat().st_mode & 0o777, 0o600)
            os.chmod(d / "k.pem", 0o644)
            with self.assertRaises(sg.SigningError): sg.Ed25519Signer(d / "k.pem")

    def test_keygen_never_overwrites(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            d = Path(d); sg.generate_keypair(d / "k.pem", d / "p.pem")
            with self.assertRaises(sg.SigningError): sg.generate_keypair(d / "k.pem", d / "p2.pem")

    def test_trusted_keys_file_validation(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            p = Path(d) / "t.json"
            for bad in ({}, {"keys": []}, {"keys": [{"actor": "a"}]}, [1]):
                base.dump(p, bad)
                with self.assertRaises(sg.SigningError): sg.TrustedKeys.load(p)


class AuditChainsAndAnchors(unittest.TestCase):
    SRC = {"source_id": "S1", "title": "G", "source_type": "guideline", "authority": "pub", "jurisdiction": "XX", "version": "1"}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True); self.d = Path(self.tmp.name)
        sg.generate_keypair(self.d / "alice.key", self.d / "alice.pub")
        sg.generate_keypair(self.d / "mal.key", self.d / "mal.pub")
        self.trusted = sg.TrustedKeys([("alice", (self.d / "alice.pub").read_text())])
        self.alice = sg.Ed25519Signer(self.d / "alice.key")
        self.mal = sg.Ed25519Signer(self.d / "mal.key")

    def tearDown(self):
        self.tmp.cleanup()

    def events(self, signer, actor="alice"):
        e1 = v15.build_event("0" * 64, actor, "submit", "2026-10-30T10:00:00Z", {"to": "in_review"}, signer=signer)
        e2 = v15.build_event(e1["event_hash"], actor, "approve", "2026-10-31T10:00:00Z", {"to": "approved"}, signer=signer)
        return [e1, e2]

    def claim(self, events):
        return {"claim_id": "C1", "card_id": "K1", "claim": "x", "references": [{"source_id": "S1"}], "status": "approved",
                "reviewed_on": "2026-10-31", "audit_events": events,
                "review_decisions": [{"reviewer_id": "R1", "decision": "approve"}]}

    def run_v15(self, claim, **kw):
        return v15.analyze([self.SRC], [claim], as_of=__import__("datetime").date(2026, 10, 31), max_age_days=730,
                           minimum_reviewers=1, require_registered_sources=True, **kw)["governance_status"]

    def test_valid_ed25519_chain_passes(self):
        self.assertEqual(self.run_v15(self.claim(self.events(self.alice)), audit_verifier=self.trusted, require_signed_audit=True), "checks_passed")

    def test_attacker_rewriting_whole_chain_with_plain_hashes_is_rejected(self):
        self.assertEqual(self.run_v15(self.claim(self.events(None)), audit_verifier=self.trusted), "blocked")

    def test_unknown_signer_impersonating_trusted_actor_is_rejected(self):
        self.assertEqual(self.run_v15(self.claim(self.events(self.mal)), audit_verifier=self.trusted), "blocked")

    def test_trusted_key_cannot_sign_for_a_different_actor(self):
        self.assertEqual(self.run_v15(self.claim(self.events(self.alice, actor="bob")), audit_verifier=self.trusted), "blocked")

    def test_signature_survives_only_unmodified_events(self):
        events = self.events(self.alice); events[1]["timestamp"] = "2026-11-01T10:00:00Z"
        self.assertEqual(self.run_v15(self.claim(events), audit_verifier=self.trusted), "blocked")

    def test_signed_anchor_detects_truncation_even_if_inline_anchor_is_edited(self):
        good = self.events(self.alice); heads = {"C1": good[-1]["event_hash"]}
        doc = sg.build_anchor_document(heads, self.alice, "alice", "2026-10-31T12:00:00Z")
        anchors = sg.verify_anchor_document(doc, self.trusted)
        truncated = self.claim(good[:1]); truncated["status"] = "in_review"
        truncated["audit_anchor"] = good[0]["event_hash"]          # attacker also edits the inline anchor
        issues = v15.verify_hash_chain(truncated["audit_events"], verifier=self.trusted, expected_head=anchors["C1"])
        self.assertTrue(any("anchored head" in i for i in issues))
        approved_without_entry = self.claim(good)
        status = self.run_v15(approved_without_entry, audit_verifier=self.trusted, external_anchors={})
        self.assertEqual(status, "blocked")                          # no entry in the signed anchor file
        self.assertEqual(self.run_v15(approved_without_entry, audit_verifier=self.trusted, external_anchors=anchors), "checks_passed")

    def test_anchor_file_tampering_and_untrusted_signer_rejected(self):
        doc = sg.build_anchor_document({"C1": "a" * 64}, self.alice, "alice", "2026-10-31T12:00:00Z")
        doc["anchors"]["C1"] = "b" * 64
        with self.assertRaises(sg.SigningError): sg.verify_anchor_document(doc, self.trusted)
        forged = sg.build_anchor_document({"C1": "a" * 64}, self.mal, "alice", "2026-10-31T12:00:00Z")
        with self.assertRaises(sg.SigningError): sg.verify_anchor_document(forged, self.trusted)
        with self.assertRaises(v15.V15Error): v15.anchors_from_path(str(self.d / "x.json"), None)

    @unittest.skipUnless(POSIX, "POSIX permission bits")
    def test_hmac_key_file_permissions(self):
        p = self.d / "hmac.key"; p.write_text("secret\n"); os.chmod(p, 0o644)
        with self.assertRaises(v15.V15Error): v15.audit_key_from_file(str(p))
        os.chmod(p, 0o600); self.assertEqual(v15.audit_key_from_file(str(p)), b"secret")

    def cli(self, *args):
        out = io.StringIO(); err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = audit.main([str(a) for a in args])
        return code, out.getvalue(), err.getvalue()

    def test_audit_cli_end_to_end(self):
        claims = self.d / "claims.json"
        base.dump(claims, {"claims": [{"claim_id": "C1", "card_id": "K1", "claim": "x", "references": [{"source_id": "S1"}],
                                       "status": "draft", "reviewed_on": "2026-10-31",
                                       "review_decisions": [{"reviewer_id": "R1", "decision": "approve"}], "audit_events": []}]})
        trusted = self.d / "trusted.json"
        self.assertEqual(self.cli("trust", "--keys", trusted, "--actor", "alice", "--public", self.d / "alice.pub")[0], 0)
        self.assertFalse(json.loads(self.cli("trust", "--keys", trusted, "--actor", "alice", "--public", self.d / "alice.pub")[1])["added"])
        c1, c2 = self.d / "c1.json", self.d / "c2.json"
        self.assertEqual(self.cli("append", "--claims", claims, "--claim-id", "C1", "--actor", "alice", "--action", "submit",
                                  "--to", "in_review", "--key", self.d / "alice.key", "--output", c1)[0], 0)
        self.assertEqual(self.cli("append", "--claims", c1, "--claim-id", "C1", "--actor", "alice", "--action", "approve",
                                  "--to", "approved", "--key", self.d / "alice.key", "--output", c2)[0], 0)
        # illegal transition (approved -> draft) is refused and nothing is written
        code, _, err = self.cli("append", "--claims", c2, "--claim-id", "C1", "--actor", "alice", "--action", "x",
                                "--to", "draft", "--key", self.d / "alice.key", "--output", self.d / "c3.json")
        self.assertEqual(code, 2); self.assertFalse((self.d / "c3.json").exists())
        anchors = self.d / "anchors.json"
        self.assertEqual(self.cli("anchors", "--claims", c2, "--actor", "alice", "--key", self.d / "alice.key", "--output", anchors)[0], 0)
        self.assertEqual(self.cli("verify", "--claims", c2, "--trusted-keys", trusted, "--anchors", anchors)[0], 0)
        # attacker truncates the chain: verify must fail (exit 1)
        data = json.loads(c2.read_text()); data["claims"][0]["audit_events"].pop()
        attacked = self.d / "attacked.json"; base.dump(attacked, data)
        self.assertEqual(self.cli("verify", "--claims", attacked, "--trusted-keys", trusted, "--anchors", anchors)[0], 1)
        # V15 itself accepts the legitimate bundle under full strictness
        base.dump(self.d / "sources.json", {"sources": [self.SRC]})
        code = v15.main(["--sources", str(self.d / "sources.json"), "--claims", str(c2), "--as-of", "2026-10-31",
                         "--require-registered-sources", "--require-signed-audit", "--require-audit-anchor",
                         "--audit-trusted-keys", str(trusted), "--anchors", str(anchors), "--output-dir", str(self.d / "o")])
        self.assertEqual(code, 0)
        self.assertEqual(self.cli("append", "--claims", c2, "--claim-id", "NOPE", "--actor", "alice", "--action", "x",
                                  "--key", self.d / "alice.key", "--output", self.d / "z.json")[0], 2)


def uid(i: int) -> str:
    return str(uuid.UUID(int=i + 1, version=4))


class LedgerAcrossReleases(unittest.TestCase):
    K = 5

    def hashed(self, ledger, ids):
        return ledger_mod.hashed_members(ledger, ids)

    def ledger_with(self, cohorts):
        ledger = ledger_mod.new_ledger()
        ledger_mod.record_release(ledger, "r1", "2026-09-30T00:00:00Z", {c: self.hashed(ledger, ids) for c, ids in cohorts.items()})
        return ledger

    def test_one_new_learner_since_last_release_is_blocked(self):
        ledger = self.ledger_with({"U1": [uid(i) for i in range(6)]})
        now = {"U1": self.hashed(ledger, [uid(i) for i in range(7)])}
        self.assertIn("U1", ledger_mod.conflicts(now, ledger, self.K))

    def test_large_change_is_allowed_and_identical_cohort_is_allowed(self):
        ledger = self.ledger_with({"U1": [uid(i) for i in range(6)]})
        self.assertEqual(ledger_mod.conflicts({"U1": self.hashed(ledger, [uid(i) for i in range(12)])}, ledger, self.K), {})
        self.assertEqual(ledger_mod.conflicts({"U1": self.hashed(ledger, [uid(i) for i in range(6)])}, ledger, self.K), {})

    def test_shrinking_cohort_by_one_is_blocked(self):
        ledger = self.ledger_with({"U1": [uid(i) for i in range(8)]})
        self.assertIn("U1", ledger_mod.conflicts({"U1": self.hashed(ledger, [uid(i) for i in range(7)])}, ledger, self.K))

    def test_cross_competency_subset_is_blocked(self):
        ledger = self.ledger_with({"U1": [uid(i) for i in range(10)]})
        self.assertIn("U2", ledger_mod.conflicts({"U2": self.hashed(ledger, [uid(i) for i in range(8)])}, ledger, self.K))

    def test_strict_mode_blocks_one_in_one_out_but_subset_mode_allows_it(self):
        ledger = self.ledger_with({"U1": [uid(i) for i in range(10)]})
        churn = {"U1": self.hashed(ledger, [uid(i) for i in range(1, 11)])}    # learner 0 left, learner 10 joined
        self.assertEqual(ledger_mod.conflicts(churn, ledger, self.K, "subset"), {})
        self.assertIn("U1", ledger_mod.conflicts(churn, ledger, self.K, "strict"))

    def test_invalid_mode_and_malformed_ledger_are_errors(self):
        with self.assertRaises(ledger_mod.LedgerError): ledger_mod.conflicts({}, ledger_mod.new_ledger(), 5, "loose")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            p = Path(d) / "l.json"; p.write_text("{not json")
            with self.assertRaises(ledger_mod.LedgerError): ledger_mod.load_ledger(p)
            base.dump(p, {"schema_version": 1, "salt": "short", "releases": []})
            with self.assertRaises(ledger_mod.LedgerError): ledger_mod.load_ledger(p)

    def make_inputs(self, root: Path, n_learners: int, comp="U1"):
        base.dump(root / "curriculum.json", {"specialty": "urology", "competencies": [{"competency_id": comp, "title": "Retention", "domain": "Emergency"}]})
        base.dump(root / "mapping.json", {"specialty": "urology", "candidates": [dict(card_id="c1", competency_id=comp, status="verified", **base.PROV)]})
        base.dump(root / "attempts.json", {
            "privacy": {"retention_days": 365, "minimum_cohort_size": 5},
            "learners": [{"learner_id": uid(i), "consent_granted": True, "consented_at": "2026-09-01T00:00:00Z"} for i in range(n_learners)],
            "attempts": [{"attempt_id": f"a{i}", "learner_id": uid(i), "card_id": "c1", "answered_at": "2026-10-30T10:00:00Z", "score": 0.8}
                         for i in range(n_learners)]})

    def run_v11(self, root: Path, **kw):
        out = root / f"out{len(list(root.glob('out*')))}"
        code = v11.run(root / "curriculum.json", root / "mapping.json", root / "attempts.json", out, None,
                       "2026-10-31T12:00:00Z", **kw)
        self.assertEqual(code, 0)
        return json.loads((out / "cohort_analytics.json").read_text())

    def test_two_releases_end_to_end(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            root = Path(d); led = root / "ledger.json"
            self.make_inputs(root, 6)
            first = self.run_v11(root, ledger_path=led)
            self.assertFalse(first["competencies"][0]["suppressed"])
            self.assertEqual(first["release_ledger"], {"used": True, "mode": "subset", "prior_releases": 0, "suppressed_by_ledger": 0})
            text = led.read_text()
            for i in range(6):
                self.assertNotIn(uid(i), text)                       # raw IDs are never stored
            if POSIX: self.assertEqual(led.stat().st_mode & 0o777, 0o600)
            before = led.read_text()
            self.make_inputs(root, 7)                                # one more learner next month
            dry = self.run_v11(root, ledger_path=led, update_ledger=False)
            self.assertTrue(dry["competencies"][0]["suppressed"])
            self.assertIn("Cross-release", dry["competencies"][0]["suppression_reason"])
            self.assertEqual(led.read_text(), before)                # --no-ledger-update leaves it untouched
            second = self.run_v11(root, ledger_path=led)
            self.assertEqual(second["release_ledger"]["suppressed_by_ledger"], 1)
            self.assertEqual(len(json.loads(led.read_text())["releases"]), 2)
            self.assertEqual(json.loads(led.read_text())["releases"][1]["cohorts"], {})   # suppressed cohorts are not recorded
            self.make_inputs(root, 13)                               # large change: publishable again
            self.assertFalse(self.run_v11(root, ledger_path=led)["competencies"][0]["suppressed"])

    def test_without_ledger_report_says_protection_is_absent(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            root = Path(d); self.make_inputs(root, 6)
            report = self.run_v11(root)
            self.assertFalse(report["release_ledger"]["used"])
            self.assertIn("NOT prevented", " ".join(report["limitations"]))

    def test_cohort_resolution_coarsens_published_means(self):
        settings = v11.load_settings(None); settings["cohort_resolution"] = 0.25
        self.assertEqual(v11.coarsen(0.62, 0.25), 0.5); self.assertEqual(v11.coarsen(0.64, 0.01), 0.64)
        for bad in (0, 1.5, True, "x"):
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
                p = Path(d) / "s.json"; base.dump(p, {"cohort_resolution": bad})
                with self.assertRaises(ValueError): v11.load_settings(p)


class PointerPublishing(unittest.TestCase):
    def stage(self, root: Path, name: str, manifest=True) -> Path:
        s = root / name; s.mkdir(); (s / "payload.txt").write_text(name)
        if manifest: (s / "v14_manifest.json").write_text(json.dumps({"n": name}))
        return s

    def test_pointer_lifecycle_keep_and_prune(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            root = Path(d); out = root / "out"; seen = []
            for i in range(4):
                self.assertEqual(v14.publish(self.stage(root, f"s{i}"), out, "pointer", keep=2), "pointer")
                current = v14.read_current(out); seen.append(current.name)
                self.assertEqual((current / "payload.txt").read_text(), f"s{i}")
                pointer = json.loads((out / "current.json").read_text())
                self.assertEqual(pointer["release_id"], current.name)
                self.assertEqual(pointer["manifest_sha256"], v14.digest(current / "v14_manifest.json"))
            kept = sorted(p.name for p in (out / "releases").iterdir())
            self.assertEqual(len(kept), 2); self.assertEqual(kept[-1], seen[-1]); self.assertNotIn(seen[0], kept)
            self.assertTrue((out / v14.MARKER).is_file())
            self.assertEqual([p.name for p in root.iterdir() if p.name.startswith(".")], [])

    def test_never_switches_modes_in_place(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            root = Path(d); out = root / "out"
            v14.publish(self.stage(root, "a"), out, "pointer")
            with self.assertRaises(v14.ReleaseError): v14.publish(self.stage(root, "b"), out, "auto")
            plain = root / "plain"; v14.publish(self.stage(root, "c"), plain, "auto")
            (plain / v14.MARKER).write_text("x")
            with self.assertRaises(v14.ReleaseError): v14.publish(self.stage(root, "e"), plain, "pointer")

    def test_unrelated_directory_is_never_touched_in_pointer_mode(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            root = Path(d); out = root / "precious"; out.mkdir(); (out / "keep.txt").write_text("mine")
            with self.assertRaises(v14.ReleaseError): v14.publish(self.stage(root, "a"), out, "pointer")
            self.assertEqual((out / "keep.txt").read_text(), "mine")

    def test_read_current_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            out = Path(d); (out / "releases").mkdir()
            for rid in ("../etc", "a/b", ".hidden", ""):
                base.dump(out / "current.json", {"release_id": rid})
                with self.assertRaises(v14.ReleaseError): v14.read_current(out)

    def test_config_validation_and_cli_roundtrip(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            root = Path(d); fix = root / "fx"; fix.mkdir(); base.V12Fixes().build(fix)
            files = {k: f"{k}.json" for k in ("cards", "curriculum", "mapping", "evidence", "learner_progress", "cohort_analytics")}
            files["review_schedule"] = "review_schedule.csv"
            cfg = {"schema_version": 1, "as_of": "2026-10-31", "run_regression_tests": False, "inputs": files,
                   "output_dir": "../out", "publish_mode": "pointer", "keep_releases": 2}
            path = fix / "cfg.json"; base.dump(path, cfg)
            run = lambda *a: subprocess.run([sys.executable, str(ROOT / "usiphus_v14.py"), "--config", str(path), *a],
                                            cwd=ROOT, text=True, capture_output=True, env=UTF8, encoding="utf-8")
            self.assertEqual(run().returncode, 0)
            self.assertEqual(run().returncode, 0)
            printed = run("--print-current")
            self.assertEqual(printed.returncode, 0, printed.stderr)
            self.assertTrue((Path(printed.stdout.strip()) / "v14_manifest.json").is_file())
            for bad in ({"publish_mode": "magic"}, {"keep_releases": 0}, {"keep_releases": True}):
                base.dump(path, {**cfg, **bad}); self.assertEqual(run().returncode, 2)


class PlatformExchange(unittest.TestCase):
    """The macOS/Linux syscalls cannot both run here, so the ctypes layer is simulated and the exact call is asserted."""

    def test_darwin_uses_renamex_np_swap_flag(self):
        lib = mock.Mock(); lib.renamex_np.return_value = 0
        with mock.patch.object(v14.sys, "platform", "darwin"), mock.patch.object(v14.ctypes, "CDLL", return_value=lib):
            self.assertTrue(v14._exchange(Path("/x/a"), Path("/x/b")))
        lib.renamex_np.assert_called_once_with(os.fsencode(str(Path("/x/a"))), os.fsencode(str(Path("/x/b"))), 2)   # RENAME_SWAP

    def test_linux_uses_renameat2_exchange_flag(self):
        lib = mock.Mock(); lib.renameat2.return_value = 0
        with mock.patch.object(v14.sys, "platform", "linux"), mock.patch.object(v14.ctypes, "CDLL", return_value=lib):
            self.assertTrue(v14._exchange(Path("/x/a"), Path("/x/b")))
        lib.renameat2.assert_called_once_with(-100, os.fsencode(str(Path("/x/a"))), -100, os.fsencode(str(Path("/x/b"))), 2)   # RENAME_EXCHANGE

    def test_failure_or_missing_symbol_or_windows_falls_back(self):
        failing = mock.Mock(); failing.renamex_np.return_value = -1
        with mock.patch.object(v14.sys, "platform", "darwin"):
            with mock.patch.object(v14.ctypes, "CDLL", return_value=failing): self.assertFalse(v14._exchange(Path("a"), Path("b")))
            with mock.patch.object(v14.ctypes, "CDLL", return_value=mock.Mock(spec=[])): self.assertFalse(v14._exchange(Path("a"), Path("b")))
            with mock.patch.object(v14.ctypes, "CDLL", side_effect=OSError("no libc")): self.assertFalse(v14._exchange(Path("a"), Path("b")))
        with mock.patch.object(v14.sys, "platform", "win32"): self.assertFalse(v14._exchange(Path("a"), Path("b")))

    def test_real_exchange_matches_platform_capability(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            a, b = Path(d) / "a", Path(d) / "b"; a.mkdir(); b.mkdir(); (a / "A").write_text("a"); (b / "B").write_text("b")
            if v14._exchange(a, b):
                self.assertTrue((a / "B").is_file() and (b / "A").is_file())
            else:
                self.assertTrue((a / "A").is_file() and (b / "B").is_file())    # unsupported -> untouched


class V14SignedAndLedgerGates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True); self.root = Path(self.tmp.name)
        self.fix = self.root / "fx"; self.fix.mkdir(); base.V12Fixes().build(self.fix)
        sg.generate_keypair(self.fix / "a.key", self.fix / "a.pub")
        self.signer = sg.Ed25519Signer(self.fix / "a.key")
        base.dump(self.fix / "trusted.json", {"keys": [sg.trusted_entry("alice", self.fix / "a.pub")]})
        base.dump(self.fix / "sources.json", {"sources": [AuditChainsAndAnchors.SRC]})
        e1 = v15.build_event("0" * 64, "alice", "submit", "2026-10-30T10:00:00Z", {"to": "in_review"}, signer=self.signer)
        e2 = v15.build_event(e1["event_hash"], "alice", "approve", "2026-10-31T10:00:00Z", {"to": "approved"}, signer=self.signer)
        self.claim = {"claim_id": "C1", "card_id": "K1", "claim": "x", "references": [{"source_id": "S1"}], "status": "approved",
                      "reviewed_on": "2026-10-31", "audit_events": [e1, e2],
                      "review_decisions": [{"reviewer_id": "R1", "decision": "approve"}]}
        base.dump(self.fix / "claims.json", {"claims": [self.claim]})
        base.dump(self.fix / "anchors.json", sg.build_anchor_document({"C1": e2["event_hash"]}, self.signer, "alice", "2026-10-31T12:00:00Z"))
        self.files = {k: f"{k}.json" for k in ("cards", "curriculum", "mapping", "evidence", "learner_progress", "cohort_analytics")}
        self.files["review_schedule"] = "review_schedule.csv"
        self.cfg_path = self.fix / "cfg.json"

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, **extra):
        cfg = {"schema_version": 1, "as_of": "2026-10-31", "run_regression_tests": False, "inputs": self.files,
               "output_dir": "../out", "v15_sources": "sources.json", "v15_claims": "claims.json", **extra}
        base.dump(self.cfg_path, cfg)
        env = {k: v for k, v in UTF8.items() if not k.startswith("USIPHUS_AUDIT")}
        return subprocess.run([sys.executable, str(ROOT / "usiphus_v14.py"), "--config", str(self.cfg_path)],
                              cwd=ROOT, text=True, capture_output=True, env=env, encoding="utf-8")

    def test_asymmetric_signatures_and_signed_anchors_pass_strict_defaults(self):
        r = self.run_cli(v15_audit_trusted_keys="trusted.json", v15_anchors="anchors.json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads(r.stdout)["v15_governance_status"], "checks_passed")

    def test_truncated_chain_is_blocked_by_signed_anchor(self):
        data = json.loads((self.fix / "claims.json").read_text()); c = data["claims"][0]
        c["audit_events"] = c["audit_events"][:1]; c["status"] = "in_review"
        base.dump(self.fix / "claims.json", data)
        self.assertEqual(self.run_cli(v15_audit_trusted_keys="trusted.json", v15_anchors="anchors.json").returncode, 1)

    def test_strict_defaults_fail_without_any_signing_configuration(self):
        r = self.run_cli()
        self.assertEqual(r.returncode, 2); self.assertIn("Signed audit is required", r.stdout + r.stderr)

    def test_anchors_require_trusted_keys_and_trusted_keys_require_v15(self):
        self.assertEqual(self.run_cli(v15_anchors="anchors.json").returncode, 2)
        cfg = {"schema_version": 1, "inputs": self.files, "output_dir": "../out", "v15_audit_trusted_keys": "trusted.json"}
        base.dump(self.cfg_path, cfg)
        with self.assertRaises(v14.ReleaseError): v14.load_config(self.cfg_path)

    def test_require_release_ledger_gate(self):
        r = self.run_cli(v15_audit_trusted_keys="trusted.json", v15_anchors="anchors.json", require_release_ledger=True)
        self.assertEqual(r.returncode, 2); self.assertIn("release ledger", r.stdout + r.stderr)
        doc = json.loads((self.fix / "cohort_analytics.json").read_text()); doc["release_ledger"] = {"used": True}
        base.dump(self.fix / "cohort_analytics.json", doc)
        r = self.run_cli(v15_audit_trusted_keys="trusted.json", v15_anchors="anchors.json", require_release_ledger=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
