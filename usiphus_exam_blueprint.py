#!/usr/bin/env python3
"""
#10 — Exam Blueprint & Curriculum Objective Mapper
Map cards to official exam objectives → auto study plans → gap analysis
"""
import json
from pathlib import Path
from typing import Dict, List, Set

class ExamBlueprint:
    def __init__(self, objectives_path: str = "docs/exam_objectives.json", cards_path: str = "cards.json"):
        self.objectives_path = Path(objectives_path)
        self.cards_path = Path(cards_path)
        self.objectives = self._load_objectives()
        self.cards = self._load_cards()

    def _load_objectives(self) -> Dict:
        if self.objectives_path.exists():
            return json.loads(self.objectives_path.read_text(encoding="utf-8"))
        return {
            "exam_name": "Egyptian Medical Licensure Exam",
            "version": "2026",
            "domains": {}
        }

    def _load_cards(self) -> List[Dict]:
        return json.loads(self.cards_path.read_text(encoding="utf-8")) if self.cards_path.exists() else []

    def map_card_to_objective(self, card_id: str, objective_codes: List[str]):
        """Associate a card with one or more exam objectives"""
        for obj in self._get_all_objectives():
            if obj["code"] in objective_codes:
                if "cards" not in obj:
                    obj["cards"] = []
                if card_id not in obj["cards"]:
                    obj["cards"].append(card_id)
        self.objectives_path.write_text(json.dumps(self.objectives, indent=2, ensure_ascii=False), encoding="utf-8")

    def _get_all_objectives(self) -> List[Dict]:
        all_objs = []
        for domain in self.objectives.get("domains", {}).values():
            all_objs.extend(domain.get("objectives", []))
        return all_objs

    def get_objective_coverage(self) -> Dict:
        """Analyze which objectives have cards and which are missing"""
        all_objs = self._get_all_objectives()
        covered = [o for o in all_objs if o.get("cards")]
        missing = [o for o in all_objs if not o.get("cards")]
        
        return {
            "exam": self.objectives.get("exam_name"),
            "total_objectives": len(all_objs),
            "fully_covered": len(covered),
            "gaps": len(missing),
            "coverage_pct": round(len(covered)/len(all_objs)*100, 1) if all_objs else 0,
            "missing_objectives": missing
        }

    def generate_study_plan(self, objective_codes: List[str] = None) -> Dict:
        """Get all cards needed for specific exam sections"""
        plan = {}
        for obj in self._get_all_objectives():
            if objective_codes is None or obj["code"] in objective_codes:
                plan[obj["code"]] = {
                    "objective": obj["description"],
                    "cards": obj.get("cards", []),
                    "status": "complete" if obj.get("cards") else "GAP"
                }
        return plan

    def print_summary(self):
        cov = self.get_objective_coverage()
        print(f"\n📋 Exam Blueprint: {cov['exam']}")
        print(f"   Objectives: {cov['total_objectives']}")
        print(f"   ✅ Covered: {cov['fully_covered']} ({cov['coverage_pct']}%)")
        print(f"   ⚠️  Gaps: {cov['gaps']}")
        if cov["missing_objectives"]:
            print("\n🔔 Missing Content — Add Cards For:")
            for obj in cov["missing_objectives"][:10]:
                print(f"   • {obj['code']}: {obj['description']}")

if __name__ == "__main__":
    bp = ExamBlueprint()
    bp.print_summary()
