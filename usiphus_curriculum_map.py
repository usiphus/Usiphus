#!/usr/bin/env python3
"""
#3 — Curriculum Cross-Map & Gap Analyzer
Detects overlaps, gaps, and contradictions across all specialties
"""
import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Set

class CurriculumMapper:
    def __init__(self, cards_path: str = "cards.json"):
        self.cards_path = Path(cards_path)
        self.cards = self._load(cards_path)
        self.topics = defaultdict(list)
        self.specialties = defaultdict(list)
        self.analysis = {}

    def _load(self, path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def build_index(self):
        """Index cards by topic and specialty"""
        for card in self.cards:
            topic = card.get("topic", card.get("title", "Unknown"))
            specialty = card.get("specialty", "General")
            self.topics[topic].append(card)
            self.specialties[specialty].append(card)

    def find_duplicate_topics(self) -> List[Dict]:
        """Find same topic taught across multiple specialties"""
        duplicates = []
        for topic, cards in self.topics.items():
            if len(cards) > 1:
                specialties_covered = list(set(c.get("specialty", "General") for c in cards))
                if len(specialties_covered
