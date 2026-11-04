"""Hashing and canonical serialisation used for digests, hash chains and manifests."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

CHUNK_BYTES = 1024 * 1024


def canonical_json(value: Any) -> str:
    """Deterministic JSON text: sorted keys, compact separators, real unicode.  Hash *this*, not ``str(value)``."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(data: bytes | str) -> str:
    """SHA-256 hex digest of bytes (text is encoded as UTF-8)."""
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def digest_value(value: Any) -> str:
    """SHA-256 of the canonical JSON form of ``value``."""
    return sha256_hex(canonical_json(value))


def sha256_file(path: str | Path, chunk_size: int = CHUNK_BYTES) -> str:
    """SHA-256 of a file, read in chunks so large inputs do not need to fit in memory."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()
