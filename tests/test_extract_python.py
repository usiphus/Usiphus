from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from extract_python import _relative_path, extract


class ExtractPythonTests(unittest.TestCase):
    def test_windows_and_posix_absolute_paths_are_portable(self):
        self.assertEqual(
            _relative_path(r"C:\Users\someone\usiphus\src\engine.py"),
            Path("src/engine.py"),
        )
        self.assertEqual(
            _relative_path("/home/user/projects/usiphus/src/engine.py"),
            Path("src/engine.py"),
        )

    def test_relative_paths_are_supported(self):
        self.assertEqual(_relative_path("src/engine.py"), Path("src/engine.py"))

    def test_paths_outside_project_root_and_traversal_are_rejected(self):
        with self.assertRaises(ValueError):
            _relative_path("/tmp/other-project/engine.py")
        with self.assertRaises(ValueError):
            _relative_path("usiphus/../outside.py")

    def test_extracts_multiple_files_without_machine_specific_prefix(self):
        dump = (
            "====\nFILE: D:\\Work\\usiphus\\src\\one.py\n====\n"
            "print('one')\n"
            "====\nFILE: /home/user/usiphus/src/two.py\n====\n"
            "print('two')\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "dump.txt"
            output = root / "output"
            source.write_text(dump, encoding="utf-8")
            written = extract(source, output)
            self.assertEqual(len(written), 2)
            self.assertEqual((output / "src/one.py").read_text(), "print('one')\n")
            self.assertEqual((output / "src/two.py").read_text(), "print('two')\n")


if __name__ == "__main__":
    unittest.main()
