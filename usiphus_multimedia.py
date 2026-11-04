#!/usr/bin/env python3
"""
#9 — Multimedia & Rich Content Handler
Embed images, ECGs, X-rays, videos, tables — all referenced locally
"""
import json
import mimetypes
from pathlib import Path
from typing import Dict, List, Optional

class MultimediaManager:
    def __init__(self, media_root: str = "media"):
        self.media_root = Path(media_root)
        self.media_index_path = self.media_root / "media_index.json"
        self.index = self._load_index()
        self.media_root.mkdir(exist_ok=True)

    def _load_index(self) -> Dict:
        if self.media_index_path.exists():
            return json.loads(self.media_index_path.read_text(encoding="utf-8"))
        return {"media": {}, "card_links": {}}

    def _save_index(self):
        self.media_index_path.write_text(json.dumps(self.index, indent=2, ensure_ascii=False), encoding="utf-8")

    def register_media(self, card_id: str, file_path: str, caption: str = "", media_type: str = None) -> Optional[str]:
        """Link a media file to a card — returns media ID"""
        src = Path(file_path)
        if not src.exists():
            return None
        
        media_id = f"media_{len(self.index['media']) + 1:04d}"
        ext = src.suffix.lower()
        known_types = {
            ".jpg": "image", ".jpeg": "image", ".png": "image", ".gif": "image",
            ".mp4": "video", ".webm": "video", ".mp3": "audio", ".wav": "audio",
            ".ecg": "ecg", ".xray": "image", ".svg": "diagram"
        }
        media_type = media_type or known_types.get(ext, mimetypes.guess_type(file_path)[0] or "other")
        
        dest = self.media_root / src.name
        if not dest.exists():
            dest.write_bytes(src.read_bytes())
        
        self.index["media"][media_id] = {
            "id": media_id,
            "filename": src.name,
            "type": media_type,
            "caption": caption,
            "size_bytes": src.stat().st_size
        }
        if card_id not in self.index["card_links"]:
            self.index["card_links"][card_id] = []
        self.index["card_links"][card_id].append(media_id)
        self._save_index()
        return media_id

    def get_card_media(self, card_id: str) -> List[Dict]:
        """Get all media attached to a card"""
        media_ids = self.index["card_links"].get(card_id, [])
        return [self.index["media"][mid] for mid in media_ids if mid in self.index["media"]]

    def scan_media_folder(self):
        """Auto-scan media folder and build index"""
        for f in self.media_root.rglob("*"):
            if f.is_file() and f.suffix.lower() not in [".json", ".md"]:
                print(f"📎 Found media: {f.name}")
        print(f"\n📊 Media Library: {len(self.index['media'])} files attached to {len(self.index['card_links'])} cards")

if __name__ == "__main__":
    mm = MultimediaManager()
    mm.scan_media_folder()
