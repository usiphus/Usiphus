#!/usr/bin/env python3
"""
#11 — Collaborative Annotation System
Public/private notes on cards — like textbook margin notes
"""
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List

class AnnotationSystem:
    def __init__(self, data_path: str = "annotations.json"):
        self.data_path = Path(data_path)
        self.data = self._load()

    def _load(self) -> Dict:
        if self.data_path.exists():
            return json.loads(self.data_path.read_text(encoding="utf-8"))
        return {"public": {}, "private": {}}

    def _save(self):
        self.data_path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")

    def add_note(self, card_id: str, author: str, content: str, is_public: bool = False, 
                  note_type: str = "general") -> str:
        """Add a note — public visible to all, private only to you"""
        note_id = f"note_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        note = {
            "id": note_id,
            "card_id": card_id,
            "author": author,
            "content": content,
            "type": note_type,
            "created_at": datetime.now().isoformat(),
            "is_public": is_public
        }
        section = "public" if is_public else "private"
        if card_id not in self.data[section]:
            self.data[section][card_id] = []
        self.data[section][card_id].append(note)
        self._save()
        return note_id

    def get_notes(self, card_id: str, include_public: bool = True, include_private: bool = True) -> List[Dict]:
        """Get all notes for a card"""
        notes = []
        if include_public:
            notes.extend(self.data["public"].get(card_id, []))
        if include_private:
            notes.extend(self.data["private"].get(card_id, []))
        return sorted(notes, key=lambda n: n["created_at"], reverse=True)

    def get_all_user_notes(self, author: str) -> List[Dict]:
        """Find all notes by a specific user"""
        all_notes = []
        for section in ["public", "private"]:
            for card_notes in self.data[section].values():
                for note in card_notes:
                    if note["author"] == author:
                        all_notes.append(note)
        return sorted(all_notes, key=lambda n: n["created_at"], reverse=True)

if __name__ == "__main__":
    ann = AnnotationSystem()
    print("📝 Annotation System Ready")
    total_public = sum(len(notes) for notes in ann.data["public"].values())
    total_private = sum(len(notes) for notes in ann.data["private"].values())
    print(f"   Public notes: {total_public}")
    print(f"   Private notes: {total_private}")
