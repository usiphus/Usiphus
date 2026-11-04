"""Keep the shared-library design from eroding.

``usiphus_lib`` exists so each helper has one implementation.  These tests fail when an engine grows its own
copy again, when the library starts depending on an engine, or when it picks up a third-party dependency.
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "usiphus_lib"

# Helpers that must exist only in usiphus_lib.  (``read_json`` is intentionally absent: each engine keeps a thin
# wrapper that states its own error class, messages and size limit as a ``JsonReadPolicy``.)
SHARED_NAMES = {
    "atomic_write", "atomic_write_text", "write_json", "write_text", "json_text",
    "digest", "digest_bytes", "digest_value", "sha256", "sha256_hex", "sha256_file",
    "canonical", "canonical_json", "clean", "strip_text", "local_ollama_url_impl",
}
# Engines may re-export a shared helper under its own name by importing it; they may not *define* one.
ALLOWED_DEFINITIONS: dict[str, set[str]] = {
    # usiphus_generate keeps a one-line adapter that raises its own GenerationError.
    "usiphus_generate.py": {"local_ollama_url"},
}


def engine_files() -> list[Path]:
    return sorted([*ROOT.glob("usiphus*.py"), ROOT / "extract_python.py"])


def top_level_defs(path: Path) -> set[str]:
    return {n.name for n in ast.parse(path.read_text(encoding="utf-8")).body if isinstance(n, ast.FunctionDef)}


class SharedLibraryArchitecture(unittest.TestCase):
    def test_engines_do_not_redefine_shared_helpers(self) -> None:
        offenders = []
        for path in engine_files():
            clashes = (top_level_defs(path) & SHARED_NAMES) - ALLOWED_DEFINITIONS.get(path.name, set())
            if clashes:
                offenders.append(f"{path.name}: {sorted(clashes)}")
        self.assertEqual(offenders, [], "define these once in usiphus_lib and import them")

    def test_library_never_imports_an_engine(self) -> None:
        engines = {p.stem for p in engine_files()}
        offenders = []
        for path in sorted(LIB.glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                modules = ([a.name for a in node.names] if isinstance(node, ast.Import)
                           else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
                offenders += [f"{path.name} imports {m}" for m in modules if m.split(".")[0] in engines]
        self.assertEqual(offenders, [], "dependencies must point from engines to usiphus_lib, never back")

    def test_library_uses_only_the_standard_library(self) -> None:
        stdlib = set(sys.stdlib_module_names)
        offenders = []
        for path in sorted(LIB.glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                modules = ([a.name for a in node.names] if isinstance(node, ast.Import)
                           else [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else [])
                offenders += [f"{path.name}: {m}" for m in modules
                              if m and m.split(".")[0] not in stdlib and m.split(".")[0] != "usiphus_lib"]
        self.assertEqual(offenders, [])

    def test_every_library_module_is_documented_in_the_package_docstring(self) -> None:
        doc = ast.get_docstring(ast.parse((LIB / "__init__.py").read_text(encoding="utf-8"))) or ""
        for path in sorted(LIB.glob("*.py")):
            if path.stem != "__init__":
                with self.subTest(module=path.stem):
                    self.assertIn(f"``{path.stem}``", doc)

    def test_engines_import_the_library_by_absolute_name(self) -> None:
        for path in engine_files():
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("usiphus_lib"):
                    with self.subTest(file=path.name, line=node.lineno):
                        self.assertEqual(node.level, 0)


if __name__ == "__main__":
    unittest.main()
