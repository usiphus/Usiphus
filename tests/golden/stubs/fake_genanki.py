"""Minimal stand-in for the ``genanki`` package used by usiphus_v7.export_apkg.

It records exactly what the engine hands to genanki (model, deck, notes) and
writes that as JSON where a real ``.apkg`` would go.  Golden tests therefore pin
the Anki export *inputs* without needing genanki installed or a SQLite package.
"""
from __future__ import annotations

import json
import sys
import types


class Model:
    def __init__(self, model_id, name, fields=None, templates=None, **_):
        self.model_id, self.name, self.fields, self.templates = model_id, name, fields or [], templates or []


class Note:
    def __init__(self, model=None, fields=None, guid=None, tags=None, **_):
        self.model, self.fields, self.guid, self.tags = model, list(fields or []), guid, list(tags or [])


class Deck:
    def __init__(self, deck_id, name):
        self.deck_id, self.name, self.notes = deck_id, name, []

    def add_note(self, note):
        self.notes.append(note)


class Package:
    def __init__(self, deck):
        self.deck = deck

    def write_to_file(self, path):
        deck = self.deck
        model = deck.notes[0].model if deck.notes else None
        payload = {
            "fake_genanki": "JSON stand-in for an .apkg file",
            "deck": {"id": deck.deck_id, "name": deck.name},
            "model": None if model is None else {"id": model.model_id, "name": model.name,
                                                  "fields": [f["name"] for f in model.fields],
                                                  "templates": model.templates},
            "notes": [{"guid": n.guid, "tags": n.tags, "fields": n.fields} for n in deck.notes],
        }
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
            handle.write("\n")


def install() -> None:
    module = types.ModuleType("genanki")
    module.Model, module.Note, module.Deck, module.Package = Model, Note, Deck, Package
    sys.modules["genanki"] = module
