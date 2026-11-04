"""Fake genanki + fake AnkiConnect (http://localhost:8765) for the V7-V9 pipelines.

canAddNotes reports the first note as an existing duplicate (so the skip path runs);
addNotes returns synthetic note ids.  Every request body is recorded.
"""
from __future__ import annotations

from _common import as_json, install_urlopen


def handler(method: str, url: str, body):
    if not url.startswith("http://localhost:8765") or not isinstance(body, dict):
        return RuntimeError(f"unstubbed request: {url}")
    action, params = body["action"], body.get("params", {})
    if action == "canAddNotes":
        return as_json({"result": [i != 0 for i, _ in enumerate(params["notes"])], "error": None})
    if action == "addNotes":
        return as_json({"result": [1700000000000 + i for i, _ in enumerate(params["notes"])], "error": None})
    return as_json({"result": None, "error": f"unsupported stub action {action}"})


def install(ctx) -> None:
    import fake_genanki
    fake_genanki.install()
    install_urlopen(ctx, handler)
