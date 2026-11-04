"""Characterization ("contract") tests for helpers that used to be copy-pasted across engines.

Step 3 consolidates them into ``usiphus_lib``.  These tests were recorded from the code *before* that
change and must keep passing afterwards: they pin, per engine, what each helper returns, which exception
type and message it raises, which bytes it writes and which hash it produces.

    python -m unittest tests.test_helper_contracts          # check
    python tests/test_helper_contracts.py --record           # re-record (only for an intended change)
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DATA = Path(__file__).with_name("data") / "helper_contracts.json"

READ_JSON = ["usiphus_generate", "usiphus_review", "usiphus_v11", "usiphus_v12", "usiphus_v13",
             "usiphus_v15", "usiphus_v17", "usiphus_v18", "usiphus_v19"]
LIMIT_NAMES = ("MAX_CONFIG_BYTES", "MAX_FILE_BYTES", "MAX_BYTES")
TEXT_FIRST = [("usiphus_v18", "write_text"), ("usiphus_v19", "write_text"), ("usiphus_common", "atomic_write_text")]
PATH_FIRST = [("usiphus_generate", "atomic_write"), ("usiphus_review", "atomic_write"), ("usiphus_v11", "atomic_write"),
              ("usiphus_v12", "atomic_write"), ("usiphus_v13", "atomic_write"), ("usiphus_v20", "atomic_write")]
JSON_PATH_FIRST = [("usiphus_signing", "write_json"), ("usiphus_v14", "write_json"), ("usiphus_v17", "write_json")]
JSON_VALUE_FIRST = [("usiphus_v18", "write_json"), ("usiphus_v19", "write_json"), ("usiphus_v20", "write_json")]
CANONICAL = [("usiphus_v15", "canonical_json"), ("usiphus_v18", "canonical_json"),
             ("usiphus_v19", "canonical"), ("usiphus_v20", "canonical")]
VALUE_DIGEST = [("usiphus_v15", "digest"), ("usiphus_v18", "digest"), ("usiphus_v20", "digest")]
FILE_DIGEST = [("usiphus_v14", "digest"), ("usiphus_v17", "digest"), ("usiphus_v12", "sha256"), ("usiphus_v13", "sha256")]
CLEAN = [("usiphus_v10", "clean"), ("usiphus_v11", "clean"), ("usiphus_v12", "clean"), ("usiphus_v15", "clean"),
         ("usiphus_v17", "clean"), ("usiphus_v18", "clean"), ("usiphus_v20", "clean"),
         ("usiphus_resource_discovery", "_clean"), ("usiphus_migrate", "_clean")]
CLEAN_INPUTS = [None, "", "  a  b\t\nc ", "x\u00a0y", "tab\there", 0, 5, "a\x1cb", " \u2003z\u2003 ", ["l"], False]
PAYLOAD = {"b": [1, {"z": "é", "a": None}], "a": "ü \u2603", "n": 1.5}
ERRORS = ["usiphus_generate.GenerationError", "usiphus_ledger.LedgerError", "usiphus_resource_discovery.DiscoveryError",
          "usiphus_review.ReviewError", "usiphus_signing.SigningError", "usiphus_v13.ValidationError",
          "usiphus_v14.ReleaseError", "usiphus_v15.V15Error", "usiphus_v17.V17Error", "usiphus_v18.V18Error",
          "usiphus_v19.V19Error", "usiphus_v20.V20Error"]
OLLAMA_INPUTS = ["http://localhost:11434", "https://localhost", "http://127.0.0.1:11434/", "http://[::1]:11434",
                 "http://example.com:11434", "localhost:11434", "http://user:pw@localhost:11434", "http://localhost:99999",
                 "http://localhost:11434/api", "http://localhost:11434?x=1", "", None, "ftp://localhost"]


def mod(name: str):
    return importlib.import_module(name)


# Helpers that moved out of an engine into usiphus_lib.  The contract keeps the ORIGINAL name and calling
# convention, so each entry is the shared function wrapped to accept the old arguments.
def _lib(path: str, attr: str):
    return getattr(importlib.import_module(path), attr)


MOVED = {
    ("usiphus_v15", "digest"): lambda: _lib("usiphus_lib.hashing", "digest_value"),
    ("usiphus_v18", "digest"): lambda: _lib("usiphus_lib.hashing", "digest_value"),
    ("usiphus_v20", "digest"): lambda: _lib("usiphus_lib.hashing", "digest_value"),
    ("usiphus_v18", "digest_bytes"): lambda: _lib("usiphus_lib.hashing", "sha256_hex"),
    ("usiphus_v19", "canonical"): lambda: _lib("usiphus_lib.hashing", "canonical_json"),
    ("usiphus_v15", "canonical_json"): lambda: _lib("usiphus_lib.hashing", "canonical_json"),
    ("usiphus_v18", "canonical_json"): lambda: _lib("usiphus_lib.hashing", "canonical_json"),
    ("usiphus_v14", "digest"): lambda: _lib("usiphus_lib.hashing", "sha256_file"),
    ("usiphus_v17", "digest"): lambda: _lib("usiphus_lib.hashing", "sha256_file"),
    ("usiphus_v12", "sha256"): lambda: _lib("usiphus_lib.hashing", "sha256_file"),
    ("usiphus_v13", "sha256"): lambda: _lib("usiphus_lib.hashing", "sha256_file"),
    ("usiphus_v11", "clean"): lambda: _lib("usiphus_lib.text", "strip_text"),
    # writers: the contract still calls them with the ORIGINAL argument order
    **{(m, "atomic_write"): (lambda: _path_first(_lib("usiphus_lib.files", "atomic_write_text")))
       for m in ("usiphus_generate", "usiphus_review", "usiphus_v11", "usiphus_v12", "usiphus_v13", "usiphus_v20")},
    **{(m, "write_text"): (lambda: _lib("usiphus_lib.files", "atomic_write_text")) for m in ("usiphus_v18", "usiphus_v19")},
    ("usiphus_signing", "write_json"): lambda: _path_first(_lib("usiphus_lib.files", "write_json")),
    ("usiphus_v14", "write_json"): lambda: _path_first(_lib("usiphus_lib.files", "write_json")),
    ("usiphus_v17", "write_json"): lambda: _path_first(_lib("usiphus_lib.files", "write_json")),
    **{(m, "write_json"): (lambda: _lib("usiphus_lib.files", "write_json")) for m in ("usiphus_v18", "usiphus_v19", "usiphus_v20")},
    ("usiphus_v12", "clean"): lambda: _lib("usiphus_lib.text", "strip_text"),
    ("usiphus_resource_discovery", "_clean"): lambda: _lib("usiphus_lib.text", "clean"),
}


def _path_first(content_first):
    """Adapt a (content, destination) function to the old (destination, content) calling convention."""
    return lambda destination, content: content_first(content, destination)


def fn_of(name: str, fn: str):
    """The callable for ``name.fn`` as the contract calls it, following MOVED when the engine no longer defines it."""
    module = mod(name)
    if hasattr(module, fn) and (name, fn) not in MOVED:
        return getattr(module, fn)
    if (name, fn) in MOVED:
        return MOVED[(name, fn)]()
    raise AttributeError(f"{name}.{fn} is gone and not listed in MOVED")


def normalise(text: str, tmp: Path) -> str:
    return text.replace(str(tmp), "<TMP>").replace(tmp.as_posix(), "<TMP>")


def outcome(call, tmp: Path):
    try:
        value = call()
    except BaseException as exc:  # noqa: BLE001 - recording any outcome is the point
        return {"raised": type(exc).__name__, "message": normalise(str(exc), tmp),
                "code": getattr(exc, "code", None), "cause": type(exc.__cause__).__name__ if exc.__cause__ else None}
    return {"ok": value}


def limit_of(module) -> int:
    for name in LIMIT_NAMES:
        if hasattr(module, name):
            return getattr(module, name)
    return 50 * 1024 * 1024


def read_json_outcomes(name: str) -> dict:
    module, results = mod(name), {}
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as raw:
        tmp = Path(raw).resolve()
        files = {"valid": b'{"a": 1}', "bom": b'\xef\xbb\xbf{"a": [1, 2]}', "bad_json": b"{nope",
                 "bad_utf8": b"\xff\xfe{}", "empty": b""}
        for key, data in files.items():
            (tmp / f"{key}.json").write_bytes(data)
            results[key] = outcome(lambda p=tmp / f"{key}.json": module.read_json(p), tmp)
        results["missing"] = outcome(lambda: module.read_json(tmp / "absent.json"), tmp)
        results["directory"] = outcome(lambda: module.read_json(tmp), tmp)
        big = tmp / "big.json"
        with big.open("wb") as handle:
            handle.truncate(limit_of(module) + 1)          # sparse: size is checked before reading
        results["too_large"] = outcome(lambda: module.read_json(big), tmp)
    return results


def write_outcomes() -> dict:
    results = {}
    text = "line1\nline2\r\nünïcode ☃\n"
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as raw:
        tmp = Path(raw).resolve()
        for index, (name, fn) in enumerate(TEXT_FIRST + PATH_FIRST):
            target = tmp / f"d{index}" / "nested" / "out.txt"
            call = fn_of(name, fn)
            (call(text, target) if (name, fn) in TEXT_FIRST else call(target, text))
            call(text + "x", target) if (name, fn) in TEXT_FIRST else call(target, text + "x")      # overwrite
            results[f"{name}.{fn}"] = {"bytes": target.read_bytes().decode("utf-8"),
                                       "siblings": sorted(p.name for p in target.parent.iterdir())}
        for index, (name, fn) in enumerate(JSON_PATH_FIRST + JSON_VALUE_FIRST):
            target = tmp / f"j{index}" / "out.json"
            target.parent.mkdir()
            call = fn_of(name, fn)
            (call(target, PAYLOAD) if (name, fn) in JSON_PATH_FIRST else call(PAYLOAD, target))
            results[f"{name}.{fn}"] = {"bytes": target.read_bytes().decode("utf-8"),
                                       "siblings": sorted(p.name for p in target.parent.iterdir())}
    return results


def pure_outcomes() -> dict:
    results = {}
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as raw:
        tmp = Path(raw).resolve()
        blob = tmp / "blob.bin"
        blob.write_bytes(b"usiphus\x00\xff" * 300_000)          # > 1 MiB so chunked hashing is exercised
        for name, fn in CANONICAL:
            results[f"{name}.{fn}"] = fn_of(name, fn)(PAYLOAD)
        for name, fn in VALUE_DIGEST:
            results[f"{name}.{fn}"] = fn_of(name, fn)(PAYLOAD)
        for name, fn in FILE_DIGEST:
            results[f"{name}.{fn}"] = fn_of(name, fn)(blob if fn == "sha256" else str(blob))
        results["usiphus_v18.digest_bytes"] = fn_of("usiphus_v18", "digest_bytes")(b"abc")
        for name, fn in CLEAN:
            results[f"{name}.{fn}"] = [fn_of(name, fn)(value) for value in CLEAN_INPUTS]
        generate = mod("usiphus_generate")
        results["usiphus_generate.local_ollama_url"] = [outcome(lambda v=v: fn_of("usiphus_generate", "local_ollama_url")(v), tmp)
                                                        for v in OLLAMA_INPUTS]
    return results


def hierarchy() -> dict:
    out = {}
    for dotted in ERRORS:
        module_name, cls_name = dotted.split(".")
        cls = getattr(mod(module_name), cls_name)
        out[dotted] = {"is_value_error": issubclass(cls, ValueError), "is_exception_only": cls.__bases__ != (ValueError,)}
    return out


def collect() -> dict:
    data = {"read_json": {name: read_json_outcomes(name) for name in READ_JSON},
            "writes": write_outcomes(), "pure": pure_outcomes()}
    return json.loads(json.dumps(data, ensure_ascii=False))     # normalise tuples/None the way JSON will


class HelperContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.expected = json.loads(DATA.read_text(encoding="utf-8"))
        cls.actual = collect()

    def test_read_json_contract_per_engine(self) -> None:
        for name in READ_JSON:
            for case, expected in self.expected["read_json"][name].items():
                with self.subTest(engine=name, case=case):
                    self.assertEqual(self.actual["read_json"][name][case], expected)

    def test_write_contract_per_engine(self) -> None:
        for key, expected in self.expected["writes"].items():
            with self.subTest(writer=key):
                self.assertEqual(self.actual["writes"][key], expected)

    def test_hashing_text_and_ollama_contract_per_engine(self) -> None:
        for key, expected in self.expected["pure"].items():
            with self.subTest(helper=key):
                self.assertEqual(self.actual["pure"][key], expected)

    def test_every_engine_error_is_a_value_error(self) -> None:
        for dotted, facts in hierarchy().items():
            with self.subTest(error=dotted):
                self.assertTrue(facts["is_value_error"])

    def test_recording_covers_every_helper_family(self) -> None:
        self.assertEqual(sorted(self.expected), ["pure", "read_json", "writes"])
        self.assertEqual(len(self.expected["read_json"]), len(READ_JSON))
        self.assertGreaterEqual(len(self.expected["writes"]), 15)
        self.assertGreaterEqual(len(self.expected["pure"]), 22)


class SigningHashContract(unittest.TestCase):
    def test_anchor_message_hash_is_pinned(self) -> None:
        """The anchor signature covers this hash; it must not change when canonical JSON code is touched.

        The expected value was produced by the original hand-rolled implementation, before it was replaced
        by ``usiphus_lib.hashing.digest_value``.  The golden audit scenario cannot pin it (anchor signatures
        contain a timestamp), and sign/verify would stay self-consistent even if the hash silently changed.
        """
        doc = {"schema_version": 1, "generated_at": "2026-01-01T00:00:00Z", "signed_by": "alice",
               "anchors": {"C2": "ff00", "C1": "é☃"}, "signature": "ignored"}
        self.assertEqual(mod("usiphus_signing")._anchor_message(doc),
                         "adf95aa7e755c11bb44376a7f2f97caf1ad9bd83a58b7872481d1e59085b1bd6")


if __name__ == "__main__":
    if "--record" in sys.argv:
        DATA.parent.mkdir(exist_ok=True)
        DATA.write_text(json.dumps(collect(), indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        print(f"recorded {DATA}")
    else:
        unittest.main()
