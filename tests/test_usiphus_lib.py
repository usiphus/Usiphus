from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from usiphus_lib import files, hashing, ids, ollama, text  # noqa: E402
from usiphus_lib.errors import UsiphusError  # noqa: E402


class Tmp(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)


class ErrorsTests(unittest.TestCase):
    def test_usiphus_error_is_a_value_error(self) -> None:
        self.assertTrue(issubclass(UsiphusError, ValueError))
        with self.assertRaises(ValueError):
            raise UsiphusError("x")

    def test_every_engine_error_derives_from_usiphus_error(self) -> None:
        import importlib
        for dotted in ("usiphus_generate.GenerationError", "usiphus_ledger.LedgerError",
                       "usiphus_resource_discovery.DiscoveryError", "usiphus_review.ReviewError",
                       "usiphus_signing.SigningError", "usiphus_v13.ValidationError", "usiphus_v14.ReleaseError",
                       "usiphus_v15.V15Error", "usiphus_v17.V17Error", "usiphus_v18.V18Error",
                       "usiphus_v19.V19Error", "usiphus_v20.V20Error"):
            module_name, class_name = dotted.split(".")
            with self.subTest(error=dotted):
                self.assertTrue(issubclass(getattr(importlib.import_module(module_name), class_name), UsiphusError))


class AtomicWriteTests(Tmp):
    def test_creates_parents_overwrites_and_leaves_no_temp_files(self) -> None:
        target = self.tmp / "a" / "b" / "out.txt"
        files.atomic_write_text("one", target)
        files.atomic_write_text("two", target)
        self.assertEqual(target.read_text(encoding="utf-8"), "two")
        self.assertEqual([p.name for p in target.parent.iterdir()], ["out.txt"])

    def test_newlines_and_unicode_are_written_exactly(self) -> None:
        target = self.tmp / "out.txt"
        files.atomic_write_text("a\nb\r\nü ☃\n", target)
        self.assertEqual(target.read_bytes(), "a\nb\r\nü ☃\n".encode("utf-8"))

    def test_failed_replace_keeps_old_file_and_removes_temp(self) -> None:
        target = self.tmp / "out.txt"
        files.atomic_write_text("old", target)
        with patch("usiphus_lib.files.os.replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                files.atomic_write_text("new", target)
        self.assertEqual(target.read_text(encoding="utf-8"), "old")
        self.assertEqual([p.name for p in self.tmp.iterdir()], ["out.txt"])

    def test_data_is_fsynced_before_the_rename(self) -> None:
        order: list[str] = []
        real_fsync, real_replace = os.fsync, os.replace
        with patch("usiphus_lib.files.os.fsync", side_effect=lambda fd: (order.append("fsync"), real_fsync(fd))[1]), \
                patch("usiphus_lib.files.os.replace", side_effect=lambda a, b: (order.append("replace"), real_replace(a, b))[1]):
            files.atomic_write_text("x", self.tmp / "o.txt")
        self.assertEqual(order, ["fsync", "replace"])

    def test_write_json_uses_the_standard_layout(self) -> None:
        target = self.tmp / "o.json"
        files.write_json({"b": 1, "a": "é"}, target)
        self.assertEqual(target.read_text(encoding="utf-8"), '{\n  "a": "é",\n  "b": 1\n}\n')
        self.assertEqual(files.json_text({"z": [1]}), '{\n  "z": [\n    1\n  ]\n}\n')


class ReadJsonTests(Tmp):
    class Boom(Exception):
        def __init__(self, kind: str, message: str) -> None:
            super().__init__(message)
            self.kind = kind

    def policy(self, **kwargs):
        return files.JsonReadPolicy(error=lambda kind, message: self.Boom(kind, message), **kwargs)

    def test_valid_file_with_bom(self) -> None:
        p = self.tmp / "a.json"
        p.write_bytes(b'\xef\xbb\xbf{"a": [1]}')
        self.assertEqual(files.read_json_file(p, self.policy()), {"a": [1]})
        self.assertEqual(files.read_json_file(str(p), self.policy()), {"a": [1]})   # str paths accepted

    def test_missing_and_directory_report_missing(self) -> None:
        for target in (self.tmp / "nope.json", self.tmp):
            with self.subTest(target=target.name):
                with self.assertRaises(self.Boom) as caught:
                    files.read_json_file(target, self.policy(missing="gone: {path}"))
                self.assertEqual(caught.exception.kind, "missing")
                self.assertTrue(str(caught.exception).startswith("gone: "))

    def test_too_large_reports_limit_in_both_units(self) -> None:
        p = self.tmp / "big.json"
        p.write_bytes(b"x" * 11)
        with self.assertRaises(self.Boom) as caught:
            files.read_json_file(p, self.policy(max_bytes=10, too_large="{limit} bytes / {limit_mib} MiB: {path}"))
        self.assertEqual(caught.exception.kind, "too_large")
        self.assertIn("10 bytes / 0 MiB", str(caught.exception))
        p.write_text("{}", encoding="utf-8")
        self.assertEqual(files.read_json_file(p, self.policy(max_bytes=None)), {})

    def test_invalid_json_and_bad_utf8_are_wrapped_with_cause(self) -> None:
        for name, data in (("j", b"{nope"), ("u", b"\xff\xfe{}"), ("e", b"")):
            p = self.tmp / f"{name}.json"
            p.write_bytes(data)
            with self.subTest(case=name):
                with self.assertRaises(self.Boom) as caught:
                    files.read_json_file(p, self.policy(invalid="bad {path}: {exc}"))
                self.assertEqual(caught.exception.kind, "invalid")
                self.assertIsInstance(caught.exception.__cause__, ValueError)

    def test_unlisted_exceptions_propagate_unchanged(self) -> None:
        p = self.tmp / "j.json"
        p.write_text("{nope", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            files.read_json_file(p, self.policy(invalid_on=()))

    def test_unreadable_kind_and_line_column_placeholders(self) -> None:
        p = self.tmp / "j.json"
        p.write_text("{\n nope", encoding="utf-8")
        policy = self.policy(invalid="line {lineno}, column {colno}", invalid_on=(json.JSONDecodeError,),
                             unreadable="cannot read {path}: {exc}", unreadable_on=(UnicodeDecodeError,))
        with self.assertRaises(self.Boom) as caught:
            files.read_json_file(p, policy)
        self.assertEqual(str(caught.exception), "line 2, column 2")
        p.write_bytes(b"\xff")
        with self.assertRaises(self.Boom) as caught:
            files.read_json_file(p, policy)
        self.assertEqual(caught.exception.kind, "unreadable")

    def test_uniform_factory_ignores_kind(self) -> None:
        class MyError(Exception):
            pass
        self.assertIsInstance(files.uniform(MyError)("missing", "m"), MyError)
        self.assertEqual(str(files.uniform(MyError)("invalid", "m")), "m")

    def test_with_limit_returns_a_new_policy(self) -> None:
        base = self.policy()
        self.assertEqual(base.with_limit(5).max_bytes, 5)
        self.assertEqual(base.max_bytes, files.DEFAULT_MAX_BYTES)


class HashingTests(Tmp):
    def test_canonical_json_is_sorted_compact_unicode(self) -> None:
        self.assertEqual(hashing.canonical_json({"b": 1, "a": ["é", None]}), '{"a":["é",null],"b":1}')

    def test_sha256_hex_accepts_bytes_and_text(self) -> None:
        self.assertEqual(hashing.sha256_hex(b"abc"), hashlib.sha256(b"abc").hexdigest())
        self.assertEqual(hashing.sha256_hex("é"), hashlib.sha256("é".encode()).hexdigest())

    def test_digest_value_hashes_the_canonical_form_and_ignores_key_order(self) -> None:
        a, b = {"x": 1, "y": [2, 3]}, {"y": [2, 3], "x": 1}
        self.assertEqual(hashing.digest_value(a), hashing.digest_value(b))
        self.assertEqual(hashing.digest_value(a), hashlib.sha256(hashing.canonical_json(a).encode()).hexdigest())

    def test_sha256_file_matches_hashlib_around_chunk_boundaries(self) -> None:
        for size in (0, 1, 1023, 1024, 1025, 5000):
            data = os.urandom(size)
            p = self.tmp / f"f{size}"
            p.write_bytes(data)
            with self.subTest(size=size):
                self.assertEqual(hashing.sha256_file(p, chunk_size=1024), hashlib.sha256(data).hexdigest())
                self.assertEqual(hashing.sha256_file(str(p)), hashlib.sha256(data).hexdigest())


class TextIdsOllamaTests(unittest.TestCase):
    def test_clean_collapses_and_strip_text_preserves(self) -> None:
        self.assertEqual(text.clean("  a \t b\n\u00a0c "), "a b c")
        self.assertEqual(text.strip_text("  a \t b  "), "a \t b")
        for fn in (text.clean, text.strip_text):
            self.assertEqual([fn(None), fn(""), fn(0), fn(False)], ["", "", "", ""])
        self.assertEqual(text.clean(5), "5")

    def test_clean_matches_the_regex_form_used_before_consolidation(self) -> None:
        for value in ("a\x1cb", " \u2003z\u2003 ", "tab\there", "x\u00a0y", "\u0085n"):
            self.assertEqual(text.clean(value), re.sub(r"\s+", " ", str(value or "").strip()))

    def test_objective_id_format(self) -> None:
        self.assertEqual(ids.objective_id("retina-01", 2), "retina-01-LO02")
        self.assertEqual(ids.objective_id("t", 12), "t-LO12")

    def test_local_ollama_url_accepts_loopback_only(self) -> None:
        self.assertEqual(ollama.local_ollama_url("http://127.0.0.1:11434/"), "http://127.0.0.1:11434")
        self.assertEqual(ollama.local_ollama_url("http://[::1]:11434"), "http://[::1]:11434")
        for bad in ("http://example.com", "ftp://localhost", "http://u:p@localhost", "http://localhost/api", "", None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                ollama.local_ollama_url(bad)

    def test_local_ollama_url_raises_the_requested_class(self) -> None:
        class Mine(Exception):
            pass
        with self.assertRaises(Mine):
            ollama.local_ollama_url("http://example.com", error=Mine)
        with self.assertRaises(Mine):
            ollama.local_ollama_url("http://localhost:99999", error=Mine)


if __name__ == "__main__":
    unittest.main()
