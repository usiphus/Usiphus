#!/usr/bin/env python3
"""
#8 — SM-2 Spaced Repetition Scheduler
Based on SuperMemo 2 algorithm — shows cards exactly when needed
"""
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

class SpacedRepetition:
    def __init__(self, user_data_path: str = "user_progress.json"):
        self.data_path = Path(user_data_path)
        self.data = self._load()

    def _load(self) -> Dict:
        if self.data_path.exists():
            return json.loads(self.data_path.read_text(encoding="utf-8"))
        return {"cards": {}, "daily_streak": 0, "last_study_date": None}

    def _save(self):
        Path(self.data_path.parent).mkdir(exist_ok=True)
        self.data_path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")

    def record_review(self, card_id: str, quality: int):
        """Quality: 0=complete blackout, 5=perfect recall"""
        card = self.data["cards"].get(card_id, {
            "interval": 1,
            "ease_factor": 2.5,
            "repetitions": 0,
            "next_review": None,
            "last_review": None
        })

        if quality < 3:
            card["repetitions"] = 0
            card["interval"] = 1
        else:
            new_intervals = {
                1: 1,
                2: 6,
            }
            if card["repetitions"] == 0:
                card["interval"] = 1
            elif card["repetitions"] == 1:
                card["interval"] = 6
            else:
                card["interval"] = round(card["interval"] * card["ease_factor"])
            card["repetitions"] += 1

        if quality >= 3:
            card["ease_factor"] = max(1.3, card["ease_factor"] + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)))

        now = datetime.now().isoformat()
        card["last_review"] = now
        card["next_review"] = (datetime.now() + timedelta(days=card["interval"])).isoformat()
        self.data["cards"][card_id] = card
        self._update_streak()
        self._save()

    def get_due_cards(self, cards: List[Dict]) -> List[Dict]:
        """Return cards due today"""
        today = datetime.now().date()
        due = []
        for card in cards:
            cid = card.get("id")
            data = self.data["cards"].get(cid)
            if not data:
                due.append(card)
                continue
            nr = datetime.fromisoformat(data["next_review"]).date()
            if nr <= today:
                due.append(card)
        return due

    def _update_streak(self):
        today = datetime.now().date().isoformat()
        yesterday = (datetime.now() - timedelta(days=1)).date().isoformat()
        if self.data["last_study_date"] == yesterday:
            self.data["daily_streak"] += 1
        elif self.data["last_study_date"] != today:
            self.data["daily_streak"] = 1
        self.data["last_study_date"] = today

    def get_stats(self) -> Dict:
        today = datetime.now().date().isoformat()
        due_today = sum(1 for c in self.data["cards"].values() 
                       if datetime.fromisoformat(c["next_review"]).date().isoformat() == today)
        return {
            "cards_in_system": len(self.data["cards"]),
            "due_today": due_today,
            "daily_streak": self.data["daily_streak"]
        }

if __name__ == "__main__":
    srs = SpacedRepetition()
    print("📅 Spaced Repetition Ready")
    print(f"   Cards in system: {srs.get_stats()['cards_in_system']}")
    print(f"   Due today: {srs.get_stats()['due_today']}")
    print(f"   Current streak: {srs.get_stats()['daily_streak']} days 🔥")
