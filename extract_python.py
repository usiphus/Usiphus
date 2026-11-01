from __future__ import annotations

import argparse
import re
from pathlib import Path


HEADER = re.compile(
    r"(?m)^=+\r?\nFILE: C:\\Users\\YOUSE\\usiphus\\(?P<path>[^\r\n]+)\r?\n=+\r?\n"
)
NEXT_FILE = re.compile(r"(?m)^=+\r?\nFILE: ")
ROOT_TEST_COPIES = {
    "test_hardening2.py",
    "test_regressions.py",
    "test_v14_e2e.py",
    "test_v15_governance.py",
}


def extract(source: Path, destination: Path, all_files: bool = False) -> list[Path]:
    text = source.read_text(encoding="utf-8-sig")
    headers = []
    for header in HEADER.finditer(text):
        relative = Path(header.group("path"))
        if not all_files and relative.suffix != ".py":
            continue
        if relative.parent == Path(".") and relative.name in ROOT_TEST_COPIES:
            continue
        headers.append(header)
    if not headers:
        raise ValueError(f"No matching file sections found in {source}")

    written = []
    root = destination.resolve()
    for header in headers:
        next_header = NEXT_FILE.search(text, header.end())
        end = next_header.start() if next_header else len(text)
        content = text[header.end() : end].rstrip("\r\n") + "\n"
        relative = Path(header.group("path"))
        target = (root / relative).resolve()
        if not target.is_relative_to(root):
            raise ValueError(f"Unsafe output path: {relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
        written.append(target)
    return written


def main() -> int:
    default_source = Path(__file__).resolve().parents[2] / "combined_code.txt"
    parser = argparse.ArgumentParser(
        description="Recreate project files from a combined repository text dump."
    )
    parser.add_argument("source", nargs="?", type=Path, default=default_source)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--all-files", action="store_true", help="Extract documentation, examples, schemas, and other listed files too")
    args = parser.parse_args()

    paths = extract(args.source, args.output, all_files=args.all_files)
    label = "project files" if args.all_files else "Python files"
    print(f"Extracted {len(paths)} {label} into {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
