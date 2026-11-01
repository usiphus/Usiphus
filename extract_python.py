from __future__ import annotations

import argparse
import re
from pathlib import Path, PurePosixPath


HEADER = re.compile(
    r"(?m)^=+\\r?\\nFILE: (?P<path>[^\\r\\n]+)\\r?\\n=+\\r?\\n"
)
NEXT_FILE = re.compile(r"(?m)^=+\\r?\\nFILE: ")
ROOT_TEST_COPIES = {
    "test_hardening2.py",
    "test_regressions.py",
    "test_v14_e2e.py",
    "test_v15_governance.py",
}


def _relative_path(raw_path: str, project_root_name: str = "usiphus") -> Path:
    """Convert a dump header path to a safe path relative to the project root.

    Supports Windows and POSIX absolute paths as well as relative paths. For
    absolute paths, the project-root directory must appear as a path component.
    """
    parts = PurePosixPath(raw_path.replace("\\\\", "/")).parts
    if not parts:
        raise ValueError("Empty file path in dump header")
    root_positions = [i for i, part in enumerate(parts) if part.casefold() == project_root_name.casefold()]
    if root_positions:
        parts = parts[root_positions[-1] + 1 :]
    elif PurePosixPath(raw_path.replace("\\\\", "/")).is_absolute():
        raise ValueError(
            f"Absolute path does not contain project root {project_root_name!r}: {raw_path}"
        )
    relative = Path(*parts)
    if not parts or relative == Path("."):
        raise ValueError(f"Header does not identify a file below {project_root_name!r}: {raw_path}")
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Unsafe output path: {raw_path}")
    return relative


def extract(
    source: Path,
    destination: Path,
    all_files: bool = False,
    project_root_name: str = "usiphus",
) -> list[Path]:
    text = source.read_text(encoding="utf-8-sig")
    headers = []
    for header in HEADER.finditer(text):
        relative = _relative_path(header.group("path"), project_root_name)
        if not all_files and relative.suffix != ".py":
            continue
        if relative.parent == Path(".") and relative.name in ROOT_TEST_COPIES:
            continue
        headers.append((header, relative))
    if not headers:
        raise ValueError(f"No matching file sections found in {source}")

    written = []
    root = destination.resolve()
    for header, relative in headers:
        next_header = NEXT_FILE.search(text, header.end())
        end = next_header.start() if next_header else len(text)
        content = text[header.end() : end].rstrip("\\r\\n") + "\\n"
        target = (root / relative).resolve()
        if not target.is_relative_to(root):
            raise ValueError(f"Unsafe output path: {relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\\n")
        written.append(target)
    return written


def main() -> int:
    default_source = Path(__file__).resolve().parents[2] / "combined_code.txt"
    parser = argparse.ArgumentParser(
        description="Recreate project files from a combined repository text dump."
    )
    parser.add_argument("source", nargs="?", type=Path, default=default_source)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument(
        "--project-root-name",
        default="usiphus",
        help="Directory name used to identify the project root in absolute dump paths.",
    )
    parser.add_argument(
        "--all-files",
        action="store_true",
        help="Extract documentation, examples, schemas, and other listed files too",
    )
    args = parser.parse_args()

    paths = extract(
        args.source,
        args.output,
        all_files=args.all_files,
        project_root_name=args.project_root_name,
    )
    label = "project files" if args.all_files else "Python files"
    print(f"Extracted {len(paths)} {label} into {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
