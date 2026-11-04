"""Guard for the address of the local Ollama server."""
from __future__ import annotations

import urllib.parse
from typing import Any

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
LOOPBACK_MESSAGE = "Ollama base_url must be a local loopback URL, such as http://localhost:11434"


def local_ollama_url(value: Any, error: type[Exception] = ValueError) -> str:
    """Validate and normalise a loopback-only Ollama base URL (scheme + host[:port], nothing else).

    Curriculum text and curated evidence are sent to this address, so non-loopback hosts, credentials,
    paths, queries and fragments are refused.  ``error`` is the exception class to raise.
    """
    raw = str(value or "").strip()
    parsed = urllib.parse.urlsplit(raw)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in LOOPBACK_HOSTS
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise error(LOOPBACK_MESSAGE)
    try:
        port = parsed.port
    except ValueError as exc:
        raise error("Ollama base_url has an invalid port") from exc
    netloc = parsed.hostname
    if ":" in netloc:
        netloc = f"[{netloc}]"
    if port is not None:
        netloc = f"{netloc}:{port}"
    return f"{parsed.scheme}://{netloc}"
