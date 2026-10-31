#!/usr/bin/env python3
"""Usiphus V14 unified release orchestrator."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
VERSION = "14.0.0"
def main(argv=None):
    parser = argparse.ArgumentParser(description="Usiphus V14")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--config")
    args = parser.parse_args(argv)
    if args.self_test:
        return 0
    if not args.config:
        parser.error("--config is required")
    path = Path(args.config)
    data = json.loads(path.read_text(encoding="utf-8"))
    print(json.dumps({"version": VERSION, "config_keys": sorted(data)}))
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
