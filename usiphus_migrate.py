
#!/usr/bin/env python3
"""Usiphus migration helper for files created before the hardening pass.

It NEVER fabricates faculty approval. Instead it moves data in the safe direction:

  mapping   'verified' rows lacking faculty-approved provenance -> 'pending'
            (faculty must re-verify them; V10 records provenance automatically)
  settings  unknown/typo'd V11 settings keys are reported with close-match suggestions
            (--apply-suggestions renames them)
  events    privacy.minimum_cohort_size below the floor of 5 is raised to 5

The input file is never modified; --output must be a different path.
Each command prints a JSON report of exactly what changed.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from usiphus_common import atomic_write_text  # noqa: E402

VERSION = "1.0.0"
MIN_COHORT_FLOOR = 5
SETTINGS_KEYS = {"prior_alpha", "prior_beta", "mastery_thresholds", "interval_days", "minimum_attempts_for_target", "cohort_resolution"}


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _tz_ok(value: Any) -> bool:
    try:
        parsed = datetime.fromisoformat(_clean(value).replace("Z", "+00:00"))
        return parsed.tzinfo is not None and parsed.utcoffset() is not None
    except ValueError:
        return False


def migrate_mapping(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("candidates"), list):
        raise ValueError("Mapping must be an object with a 'candidates' list")
    out = json.loads(json.dumps(payload))
    downgraded: list[dict[str, str]] = []
    for row in out["candidates"]:
        if not isinstance(row, dict) or _clean(row.get("status")).casefold() != "verified":
            continue
        has_provenance = (_clean(row.get("method")).casefold() == "faculty-approved"
                          and bool(_clean(row.get("reviewer"))) and _tz_ok(row.get("reviewed_at")))
        if not has_provenance:
            row["status"] = "pending"
            row["migration_note"] = "Downgraded from 'verified': missing faculty-approved provenance. Re-verify via V10."
            downgraded.append({"card_id": _clean(row.get("card_id")), "competency_id": _clean(row.get("competency_id"))})
    return out, {"rows": len(out["candidates"]), "downgraded_to_pending": len(downgraded), "downgraded": downgraded}


def migrate_settings(payload: dict[str, Any], apply_suggestions: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ValueError("Settings must be a JSON object")
    out: dict[str, Any] = {}
    unknown: list[dict[str, Any]] = []
    for key, value in payload.items():
        if key in SETTINGS_KEYS:
            out[key] = value
            continue
        matches = difflib.get_close_matches(key, sorted(SETTINGS_KEYS), n=1, cutoff=0.6)
        suggestion = matches[0] if matches else None
        unknown.append({"key": key, "suggestion": suggestion})
        if apply_suggestions and suggestion and suggestion not in payload and suggestion not in out:
            out[suggestion] = value
    unresolved = [u["key"] for u in unknown if not (apply_suggestions and u["suggestion"])]
    return out, {"unknown_keys": unknown, "applied": bool(apply_suggestions), "still_unresolved": unresolved}


def migrate_events(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("privacy"), dict):
        raise ValueError("Attempt file requires a 'privacy' object")
    out = json.loads(json.dumps(payload))
    current = out["privacy"].get("minimum_cohort_size", MIN_COHORT_FLOOR)
    changed = isinstance(current, int) and not isinstance(current, bool) and current < MIN_COHORT_FLOOR
    if changed:
        out["privacy"]["minimum_cohort_size"] = MIN_COHORT_FLOOR
    return out, {"previous_minimum_cohort_size": current, "new_minimum_cohort_size": out["privacy"].get("minimum_cohort_size"),
                 "changed": changed}


def _run(args: argparse.Namespace) -> dict[str, Any]:
    source, dest = Path(args.input).resolve(), Path(args.output).resolve()
    if source == dest:
        raise ValueError("--output must differ from --input (the original is never modified)")
    payload = json.loads(source.read_text(encoding="utf-8-sig"))
    if args.command == "mapping":
        new, report = migrate_mapping(payload)
    elif args.command == "settings":
        new, report = migrate_settings(payload, args.apply_suggestions)
    else:
        new, report = migrate_events(payload)
    atomic_write_text(json.dumps(new, indent=2, ensure_ascii=False) + "\n", dest)
    report["output"] = str(dest)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("mapping", "settings", "events"):
        p = sub.add_parser(name)
        p.add_argument("--input", required=True)
        p.add_argument("--output", required=True)
        if name == "settings":
            p.add_argument("--apply-suggestions", action="store_true")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(_run(args), indent=2))
        return 0
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
