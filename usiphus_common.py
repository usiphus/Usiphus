
#!/usr/bin/env python3
"""Usiphus shared helpers (used by V7-V15).

One implementation of text normalisation, stable identifiers, near-duplicate
screening, strict ISO dates, atomic writes, spreadsheet-safe CSV cells and the
AnkiConnect client, so fixes are made once instead of in several copies.

These helpers validate structure only. They do not establish clinical truth.
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import tempfile
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any, Sequence

COMMON_VERSION = "1.0.0"
LOG = logging.getLogger("usiphus.common")
ANKI_CONNECT_TIMEOUT_SECONDS = 10
TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
STOP_WORDS = {
    "what", "is", "are", "the", "a", "an", "of", "in", "to", "for", "and",
    "or", "with", "on", "by", "from", "which", "how", "does", "do", "describe",
    "explain", "define", "discuss", "list", "name", "identify", "most", "common",
}
_WHITESPACE_RE = re.compile(r"\s+")
_INLINE_WS_RE = re.compile(r"[ \t\f\v]+")
_TRAILING_Q_RE = re.compile(r"\?+$")


# --------------------------------------------------------------------------
# Text normalisation
# --------------------------------------------------------------------------
def canonicalize_text(value: Any) -> str:
    """Collapse ALL whitespace (including newlines) to single spaces."""
    if value is None:
        return ""
    return _WHITESPACE_RE.sub(" ", str(value).strip())


def canonicalize_multiline(value: Any) -> str:
    """Normalise whitespace but PRESERVE line breaks (lists, checklists, steps)."""
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    lines = [_INLINE_WS_RE.sub(" ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines).strip()
    return re.sub(r"\n{3,}", "\n\n", text)


def canonicalize_card_id(raw: Any) -> str:
    return canonicalize_text(raw)


def canonicalize_specialty(value: Any) -> str:
    return canonicalize_text(value).casefold()


def canonicalize_tags(tags: Any) -> tuple[str, ...]:
    if tags is None:
        return ()
    if isinstance(tags, str):
        items: Sequence[Any] = [tags]
    elif isinstance(tags, (list, tuple, set)):
        items = list(tags)
    else:
        raise ValueError("tags must be a string or a list of strings")
    output: list[str] = []
    seen: set[str] = set()
    for raw in items:
        if raw is None:
            continue
        tag = canonicalize_text(raw)
        key = tag.casefold()
        if tag and key not in seen:
            seen.add(key)
            output.append(tag)
    return tuple(output)


def canonicalize_string_list(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple)):
        values = list(value)
    else:
        raise ValueError(f"{field_name} must be a string or list of strings")
    return tuple(canonicalize_text(x) for x in values if canonicalize_text(x))


def canonicalize_prompt(prompt: Any) -> str:
    """Collapse whitespace and normalise blueprint 'what is it' prompts.

    Only a spaced DOUBLE hyphen is converted to an em dash. A single spaced
    hyphen is left untouched because it is often a range or minus sign in
    clinical text (e.g. 'pH 7.35 - 7.45'), which must never be altered.
    """
    text = canonicalize_text(prompt)
    if not text:
        return ""
    text = re.sub(r"\s+--\s+", " \u2014 ", text)
    if re.search(r"\bwhat is it\??$", text, flags=re.IGNORECASE):
        text = re.sub(r"\bwhat is it\??$", "what is it?", text, flags=re.IGNORECASE)
        text = _TRAILING_Q_RE.sub("?", text)
    return text


# --------------------------------------------------------------------------
# Stable identifiers
# --------------------------------------------------------------------------
def stable_digest(*parts: str, length: int = 20) -> str:
    payload = "\x1f".join(canonicalize_text(p).casefold() for p in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]


def stable_blueprint_id(topic: Any, name: Any) -> str:
    return f"bp-{stable_digest(str(topic), str(name), length=20)}"


def anki_numeric_id(namespace: str, name: str) -> int:
    """Deterministic positive 31-bit Anki model/deck ID."""
    raw = int(stable_digest(namespace, name, length=8), 16)
    return max(1, raw & 0x7FFFFFFF)


def anki_guid(card_id: str) -> str:
    return hashlib.sha256(canonicalize_card_id(card_id).casefold().encode()).hexdigest()[:20]


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------
def valid_iso_date(value: Any) -> bool:
    """Strict YYYY-MM-DD only (Python 3.11+ would otherwise accept YYYYMMDD)."""
    try:
        return bool(value) and date.fromisoformat(str(value)).isoformat() == str(value)
    except (ValueError, TypeError):
        return False


def parse_as_of(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if not valid_iso_date(value):
        raise ValueError("--as-of must be a strict YYYY-MM-DD date")
    return date.fromisoformat(str(value))


# --------------------------------------------------------------------------
# Lexical similarity / near-duplicate screening
# --------------------------------------------------------------------------
def tokens(text: str) -> set[str]:
    return {t.casefold() for t in TOKEN_RE.findall(text)
            if t.casefold() not in STOP_WORDS and len(t) > 1}


def lexical_similarity(left: str, right: str) -> float:
    a, b = tokens(left), tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def find_near_duplicate_pairs(cards: Sequence[Any], threshold: float = 0.78) -> list[dict[str, Any]]:
    """Possible lexical duplicates for human review; never auto-deletes.

    Tokens are computed once per card and an inverted index limits comparisons
    to cards that share at least one content word, so large decks no longer
    pay an O(n^2) re-tokenisation cost.
    """
    threshold = max(float(threshold), 1e-9)
    groups: dict[str, list[tuple[Any, set[str], str]]] = {}
    for card in cards:
        groups.setdefault(card.specialty, []).append(
            (card, tokens(card.prompt), card.prompt.casefold()))
    found: list[dict[str, Any]] = []
    for items in groups.values():
        index: dict[str, list[int]] = {}
        for i, (_, toks, _) in enumerate(items):
            for tok in toks:
                index.setdefault(tok, []).append(i)
        for i, (left, ltoks, lprompt) in enumerate(items):
            if not ltoks:
                continue
            partners = {j for tok in ltoks for j in index[tok] if j > i}
            for j in sorted(partners):
                right, rtoks, rprompt = items[j]
                small, large = sorted((len(ltoks), len(rtoks)))
                if small / large < threshold:
                    continue
                score = len(ltoks & rtoks) / len(ltoks | rtoks)
                if score >= threshold and lprompt != rprompt:
                    found.append({"card_id_a": left.card_id, "card_id_b": right.card_id,
                                  "lexical_similarity": round(score, 3), "review_required": True})
    return sorted(found, key=lambda x: (-x["lexical_similarity"], x["card_id_a"], x["card_id_b"]))


# --------------------------------------------------------------------------
# Output helpers
# --------------------------------------------------------------------------
def atomic_write_text(text: str, destination: Path) -> None:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=destination.parent,
            prefix=f".{destination.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, destination)
        temp_name = None
    finally:
        if temp_name:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass


def csv_safe(value: Any) -> str:
    """Neutralise spreadsheet formula injection in text cells (=, +, -, @, TAB, CR)."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


# --------------------------------------------------------------------------
# AnkiConnect
# --------------------------------------------------------------------------
def _anki_call(url: str, action: str, params: dict[str, Any], timeout: int) -> Any:
    payload = json.dumps({"action": action, "version": 6, "params": params}).encode("utf-8")
    request = urllib.request.Request(url, data=payload,
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"AnkiConnect request failed: {exc}") from exc
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"AnkiConnect returned invalid JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise RuntimeError("AnkiConnect returned a non-object response")
    if body.get("error") is not None:
        raise RuntimeError(f"AnkiConnect error: {body['error']}")
    return body.get("result")


def _anki_note(card: Any, deck_name: str) -> dict[str, Any]:
    esc = html.escape
    front = esc(card.prompt)
    if card.options:
        front += "<hr><ol>" + "".join(f"<li>{esc(o)}</li>" for o in card.options) + "</ol>"
    back = esc(card.answer).replace("\n", "<br>")
    if card.correct_option:
        back += f"<br><b>Correct option:</b> {esc(card.correct_option)}"
    if card.learning_objective:
        back += f"<br><b>Learning objective:</b> {esc(card.learning_objective)}"
    if card.rubric:
        back += "<br><b>Checklist</b><ul>" + "".join(f"<li>{esc(r)}</li>" for r in card.rubric) + "</ul>"
    if card.references:
        back += "<br><small>" + esc("; ".join(card.references)) + "</small>"
    tags = [re.sub(r"\s+", "_", t) for t in card.tags]  # Anki tags cannot contain spaces
    tags.append(f"usiphus_id_{stable_digest(card.card_id, length=16)}")
    return {"deckName": deck_name, "modelName": "Basic", "fields": {"Front": front, "Back": back},
            "tags": tags, "options": {"allowDuplicate": False}}


def push_to_ankiconnect(cards: Sequence[Any], deck_name: str, url: str,
                        timeout: int = ANKI_CONNECT_TIMEOUT_SECONDS) -> dict[str, int]:
    """Idempotent push: duplicates are detected first and skipped, so re-runs work.

    MCQ options, correct option, objective, rubric and references are included.
    """
    notes = [_anki_note(card, deck_name) for card in cards]
    if not notes:
        return {"added": 0, "skipped_duplicates": 0}
    can_add = _anki_call(url, "canAddNotes", {"notes": notes}, timeout)
    if not isinstance(can_add, list) or len(can_add) != len(notes):
        raise RuntimeError("AnkiConnect canAddNotes returned an unexpected result")
    addable = [n for n, ok in zip(notes, can_add) if ok is True]
    skipped = len(notes) - len(addable)
    if addable:
        results = _anki_call(url, "addNotes", {"notes": addable}, timeout)
        if not isinstance(results, list) or len(results) != len(addable):
            raise RuntimeError("AnkiConnect addNotes returned an unexpected result")
        failures = [{"index": i, "error": r} for i, r in enumerate(results) if r is None]
        if failures:
            raise RuntimeError(f"AnkiConnect failed to add notes: {failures}")
    LOG.info("AnkiConnect added %d notes; skipped %d existing", len(addable), skipped)
    return {"added": len(addable), "skipped_duplicates": skipped}
