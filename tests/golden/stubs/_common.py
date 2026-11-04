"""Shared helpers for stub modules: fake HTTP responses and request recording."""
from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from typing import Callable
from urllib.parse import parse_qs, urlsplit


class FakeResponse(io.BytesIO):
    status = 200

    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)
        self.headers = {"Content-Type": "application/json"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def install_urlopen(ctx, handler: Callable[[str, str, object], bytes | Exception]) -> None:
    """Replace urllib.request.urlopen. ``handler(method, url, body)`` returns bytes or an exception to raise."""

    def fake_urlopen(request, data=None, timeout=None, **_kwargs):
        if isinstance(request, str):
            url, method, raw = request, "GET", data
        else:
            url, method, raw = request.full_url, request.get_method(), request.data
        body = None
        if raw:
            text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
            try:
                body = json.loads(text)
            except json.JSONDecodeError:
                body = text
        split = urlsplit(url)
        ctx.requests.append({"method": method, "url": url, "body": body, "timeout": timeout})
        outcome = handler(method, url, body)
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome)

    urllib.request.urlopen = fake_urlopen


def query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def as_json(value) -> bytes:
    return json.dumps(value).encode("utf-8")


def url_error(message: str) -> urllib.error.URLError:
    return urllib.error.URLError(message)
