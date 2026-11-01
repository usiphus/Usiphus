#!/usr/bin/env python3
"""Standalone local review queue with separate, unauthenticated reviewer profiles."""
from __future__ import annotations

import argparse
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

LOG = logging.getLogger("usiphus.review")
MAX_FILE_BYTES = 50 * 1024 * 1024
EDITABLE_FIELDS = ("prompt", "answer", "learning_objective")
DECISIONS = {"keep", "edit", "reject"}


class ReviewError(ValueError):
    """Raised when review input or persisted decisions are invalid."""


def read_json(path: Path) -> Any:
    if not path.is_file():
        raise ReviewError(f"File not found: {path}")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ReviewError(f"File exceeds {MAX_FILE_BYTES} bytes: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewError(f"Cannot read JSON file {path}: {exc}") from exc


def load_cards(path: Path) -> list[dict[str, Any]]:
    payload = read_json(path)
    rows = payload.get("cards") if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or not rows:
        raise ReviewError("Card input must be a non-empty list or contain a non-empty cards list")
    cards, seen = [], set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ReviewError(f"Card {index} must be an object")
        card_id = str(row.get("card_id") or row.get("id") or "").strip()
        if not card_id or card_id.casefold() in seen:
            raise ReviewError(f"Card {index} must have a unique card_id")
        if not str(row.get("prompt") or row.get("question") or "").strip():
            raise ReviewError(f"Card {card_id} is missing a prompt")
        seen.add(card_id.casefold())
        cards.append(row)
    return cards


def load_store(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 1, "reviewer_profiles": {}}
    payload = read_json(path)
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ReviewError("Review store must have schema_version 1")
    profiles = payload.get("reviewer_profiles")
    if not isinstance(profiles, dict):
        raise ReviewError("Review store reviewer_profiles must be an object")
    for reviewer_id, profile in profiles.items():
        if not isinstance(profile, dict) or not isinstance(profile.get("decisions"), dict):
            raise ReviewError(f"Malformed reviewer profile: {reviewer_id}")
        for card_id, decision in profile["decisions"].items():
            if not isinstance(decision, dict) or decision.get("decision") not in DECISIONS:
                raise ReviewError(f"Malformed decision for {reviewer_id}/{card_id}")
    return payload


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temp_name = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def record_decision(
    store: dict[str, Any],
    reviewer_id: str,
    card_id: str,
    decision: str,
    edited_fields: dict[str, str] | None = None,
    notes: str = "",
) -> None:
    reviewer_id, card_id = reviewer_id.strip(), card_id.strip()
    if not reviewer_id or len(reviewer_id) > 120:
        raise ReviewError("reviewer ID must be 1–120 characters")
    if not card_id:
        raise ReviewError("card ID is required")
    if decision not in DECISIONS:
        raise ReviewError("decision must be keep, edit, or reject")
    edited_fields = edited_fields or {}
    if set(edited_fields) - set(EDITABLE_FIELDS):
        raise ReviewError("Only prompt, answer, and learning_objective may be edited")
    if decision == "edit" and not edited_fields:
        raise ReviewError("An edit decision must contain at least one changed field")
    if decision != "edit" and edited_fields:
        raise ReviewError("Only edit decisions may contain edited fields")
    if any(not isinstance(value, str) or not value.strip() for value in edited_fields.values()):
        raise ReviewError("Edited card fields must be non-empty strings")
    profiles = store.setdefault("reviewer_profiles", {})
    profile = profiles.setdefault(reviewer_id, {"decisions": {}})
    profile["decisions"][card_id] = {
        "decision": decision,
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "edited_fields": edited_fields,
        "notes": notes.strip(),
    }


def build_export(cards: list[dict[str, Any]], profile: dict[str, Any], reviewer_id: str) -> dict[str, Any]:
    decisions = profile.get("decisions", {})
    kept = []
    for original in cards:
        card_id = str(original.get("card_id") or original.get("id") or "").strip()
        record = decisions.get(card_id)
        if not isinstance(record, dict) or record.get("decision") not in {"keep", "edit"}:
            continue
        card = dict(original)
        if record["decision"] == "edit":
            edits = record.get("edited_fields")
            if not isinstance(edits, dict) or set(edits) - set(EDITABLE_FIELDS):
                raise ReviewError(f"Invalid edits stored for card {card_id}")
            card.update(edits)
        card["personal_review_status"] = record["decision"]
        kept.append(card)
    return {
        "schema_version": 1,
        "reviewer_profile": reviewer_id,
        "review_notice": (
            "Personal study selection only. This profile is not authenticated, "
            "and keep/edit decisions do not constitute clinical, evidence, or faculty approval."
        ),
        "cards": kept,
    }


def review_cards(
    cards: list[dict[str, Any]],
    store: dict[str, Any],
    reviewer_id: str,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    redo: bool = False,
) -> int:
    profile = store.setdefault("reviewer_profiles", {}).setdefault(reviewer_id, {"decisions": {}})
    decisions = profile.setdefault("decisions", {})
    for card in cards:
        card_id = str(card.get("card_id") or card.get("id") or "").strip()
        if card_id in decisions and not redo:
            continue
        output_fn(f"\n[{card_id}] {card.get('prompt') or card.get('question')}")
        output_fn(f"Answer: {card.get('answer') or card.get('explanation') or '(none)'}")
        refs = card.get("references") or card.get("sources") or []
        if refs:
            output_fn("Sources: " + "; ".join(str(item) for item in refs))
        while True:
            choice = input_fn("Decision [k]eep, [e]dit, [r]eject, [s]kip, [q]uit: ").strip().casefold()
            if choice in {"k", "keep"}:
                record_decision(store, reviewer_id, card_id, "keep")
                break
            if choice in {"r", "reject"}:
                record_decision(store, reviewer_id, card_id, "reject")
                break
            if choice in {"s", "skip"}:
                break
            if choice in {"q", "quit"}:
                return 0
            if choice in {"e", "edit"}:
                edits = {}
                for field in EDITABLE_FIELDS:
                    old = str(card.get(field) or "")
                    value = input_fn(f"{field} (Enter keeps current): ")
                    if value.strip():
                        edits[field] = value.strip()
                    elif old and field == "prompt" and not old.strip():
                        continue
                if not edits:
                    output_fn("No edits entered; no decision recorded.")
                    continue
                record_decision(store, reviewer_id, card_id, "edit", edits)
                break
            output_fn("Enter keep, edit, reject, skip, or quit.")
    return 0


def run(
    cards_path: Path,
    reviewer_id: str,
    store_path: Path,
    export_path: Path | None,
    redo: bool = False,
) -> int:
    reviewer_id = reviewer_id.strip()
    if not reviewer_id or len(reviewer_id) > 120:
        raise ReviewError("reviewer ID must be 1–120 characters")
    cards = load_cards(cards_path)
    store = load_store(store_path)
    profile = store.setdefault("reviewer_profiles", {}).setdefault(reviewer_id, {"decisions": {}})
    review_cards(cards, store, reviewer_id, redo=redo)
    atomic_write(store_path, json.dumps(store, indent=2, ensure_ascii=False) + "\n")
    if export_path:
        export = build_export(cards, profile, reviewer_id)
        atomic_write(export_path, json.dumps(export, indent=2, ensure_ascii=False) + "\n")
        LOG.info("Wrote %d personally selected cards to %s", len(export["cards"]), export_path)
    LOG.info("Saved review decisions for local profile %s to %s", reviewer_id, store_path)
    LOG.warning("Reviewer profiles are labels, not authenticated accounts; protect the local review file.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Review draft cards in a separate local reviewer profile")
    parser.add_argument("--cards", required=True, help="Input JSON card file")
    parser.add_argument("--reviewer", required=True, help="Local reviewer profile label; not authenticated")
    parser.add_argument("--store", default=".usiphus_reviews.json", help="Private local decisions JSON")
    parser.add_argument("--export", help="Optional personal deck JSON output")
    parser.add_argument("--redo", action="store_true", help="Revisit cards already decided in this profile")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    try:
        return run(
            Path(args.cards),
            args.reviewer,
            Path(args.store),
            Path(args.export) if args.export else None,
            args.redo,
        )
    except (ReviewError, OSError, TypeError, ValueError) as exc:
        LOG.error("%s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
