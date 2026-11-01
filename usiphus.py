from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Sequence


COMMANDS = {
    "v6": "history.versions.usiphus_v6",
    "v7": "usiphus_v7",
    "v8": "usiphus_v8",
    "v9": "usiphus_v9",
    "v10": "usiphus_v10",
    "v11": "usiphus_v11",
    "v12": "usiphus_v12",
    "v13": "usiphus_v13",
    "v14": "usiphus_v14",
    "v15": "usiphus_v15",
    "generate": "usiphus_generate",
    "competencies": "usiphus_competencies",
    "review": "usiphus_review",
    "audit": "usiphus_audit",
    "migrate": "usiphus_migrate",
}


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        prog="usiphus",
        usage="%(prog)s <command> [arguments ...]",
        description="Usiphus medical education content and governance toolkit.",
        epilog="Commands: " + ", ".join(COMMANDS),
    )
    parser.add_argument("--version", action="version", version="Usiphus 15.1.0")
    parser.add_argument("command", nargs="?", help="Version engine or utility command")

    if not arguments or arguments[0] in {"-h", "--help"}:
        parser.print_help()
        return 0
    if arguments[0] in {"--version", "-V"}:
        print("Usiphus 15.1.0")
        return 0

    command = arguments.pop(0).lower()
    if command not in COMMANDS:
        parser.error(f"unknown command {command!r}; choose from: {', '.join(COMMANDS)}")

    module = importlib.import_module(COMMANDS[command])
    result = module.main(arguments)
    return result if isinstance(result, int) else 0


if __name__ == "__main__":
    raise SystemExit(main())
