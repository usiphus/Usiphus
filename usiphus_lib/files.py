"""Reading and writing files the way every engine needs to."""
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

DEFAULT_MAX_BYTES = 50 * 1024 * 1024
BYTES_PER_MIB = 1024 * 1024


def atomic_write_text(text: str, destination: str | Path) -> None:
    """Write ``text`` to ``destination`` atomically and durably.

    The text goes to a uniquely named temp file in the same directory, is flushed and ``fsync``-ed, then
    renamed over the destination, so readers never see a partial file and an interrupted run leaves the old
    file intact.  ``newline=""`` keeps ``"\\n"`` as ``"\\n"`` on every platform (hashes of outputs stay stable).
    """
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


def json_text(payload: Any) -> str:
    """The project's standard JSON layout: 2-space indent, sorted keys, real unicode, trailing newline."""
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_json(payload: Any, destination: str | Path) -> None:
    """Atomically write ``payload`` as standard-layout JSON (content first, destination second)."""
    atomic_write_text(json_text(payload), destination)


@dataclass(frozen=True)
class JsonReadPolicy:
    """How one engine reports problems while reading a JSON input.

    ``error(kind, message)`` builds the exception to raise; ``kind`` is ``"missing"``, ``"too_large"``,
    ``"invalid"`` or ``"unreadable"``.  Message templates may use ``{path}``, ``{exc}``, ``{lineno}``,
    ``{colno}``, ``{limit}`` (bytes) and ``{limit_mib}``.  Exceptions listed in ``invalid_on`` /
    ``unreadable_on`` are wrapped (with ``from exc``); anything else propagates unchanged.
    """

    error: Callable[[str, str], Exception]
    missing: str = "Input file not found: {path}"
    too_large: str = "Input exceeds {limit_mib} MiB: {path}"
    invalid: str | None = "Invalid JSON in {path}: {exc}"
    invalid_on: tuple[type[BaseException], ...] = (UnicodeDecodeError, json.JSONDecodeError)
    unreadable: str | None = None
    unreadable_on: tuple[type[BaseException], ...] = ()
    max_bytes: int | None = DEFAULT_MAX_BYTES

    def with_limit(self, max_bytes: int | None) -> JsonReadPolicy:
        return replace(self, max_bytes=max_bytes)


def uniform(error_class: type[Exception]) -> Callable[[str, str], Exception]:
    """Error factory for engines whose single exception class ignores the failure kind."""
    return lambda kind, message: error_class(message)


def read_json_file(path: str | Path, policy: JsonReadPolicy) -> Any:
    """Read a UTF-8 (BOM tolerated) JSON file, reporting problems as ``policy`` dictates."""
    path = Path(path)
    if not path.is_file():
        raise policy.error("missing", policy.missing.format(path=path))
    limit = policy.max_bytes
    if limit is not None and path.stat().st_size > limit:
        raise policy.error("too_large", policy.too_large.format(path=path, limit=limit, limit_mib=limit // BYTES_PER_MIB))
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except policy.invalid_on as exc:
        raise policy.error("invalid", (policy.invalid or "{exc}").format(
            path=path, exc=exc, lineno=getattr(exc, "lineno", None), colno=getattr(exc, "colno", None))) from exc
    except policy.unreadable_on as exc:
        raise policy.error("unreadable", (policy.unreadable or "{exc}").format(path=path, exc=exc)) from exc
