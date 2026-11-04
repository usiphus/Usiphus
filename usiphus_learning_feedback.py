#!/usr/bin/env python3
"""
#2 — Student Performance Feedback Loop
Tracks learner performance → identifies weak topics → highlights for faculty review
"""
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List

class LearningAnalytics:
    def __init__(self, cards_path: str = "cards.json", logs_path: str = "learning_logs.json"):
        self.cards_path = Path(cards_path)
        self.logs_path = Path(logs_path)
        self.cards = self._load_json(cards_path, [])
        self.logs = self._load_json(logs_path, [])
        self.performance = {}

    def _load_json(self, path: str, default):
        p = Path(path)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default

    def log_attempt(self, card_id: str, learner_id: str, correct: bool, response_time_sec: float = None):
        """Record a learner's interaction with a card"""
        entry = {
            "card_id": card_id,
            "learner_id": learner_id,
            "correct": correct,
            "response_time_sec": response_time_sec,
            "timestamp": datetime.now().isoformat()
        }
        self.logs.append(entry)
        Path("build").mkdir(exist_ok=True)
        with open(self.logs_path, "w", encoding="utf-8") as f:
            json.dump(self.logs, f, indent=2)

    def analyze_performance(self) -> Dict:
        """Calculate performance metrics per card"""
        stats = {}
        
        for log in self.logs:
            cid = log["card_id"]
            if cid not in stats:
                stats[cid] = {"attempts": 0, "correct": 0, "total_time": 0.0}
            stats[cid]["attempts"] += 1
            if log["correct"]:
                stats[cid]["correct"] += 1
            if log.get("response_time_sec"):
                stats[cid]["total_time"] += log["response_time_sec"]
        
        # Calculate percentages and flag issues
        flagged_cards = []
        for cid, s in stats.items():
            s["accuracy"] = round((s["correct"] / s["attempts"]) * 100, 1) if s["attempts"] > 0 else 0
            s["avg_time_sec"] = round(s["total_time"] / s["attempts"], 1) if s["attempts"] > 0 else 0
            
            # Flag conditions
            if s["attempts"] >= 5 and s["accuracy"] < 60:
                flagged_cards.append({
                    "card_id": cid,
                    "accuracy": s["accuracy"],
                    "attempts": s["attempts"],
                    "reason": "Low accuracy despite multiple attempts",
                    "priority": "high"
                })
            elif s["attempts"] >= 10 and s["accuracy"] < 75:
                flagged_cards.append({
                    "card_id": cid,
                    "accuracy": s["accuracy"],
                    "attempts": s["attempts"],
                    "reason": "Consistently below expected performance",
                    "priority": "medium"
                })
            elif s["avg_time_sec"] > 60 and s["attempts"] >= 5:
                flagged_cards.append({
                    "card_id": cid,
                    "avg_time_sec": s["avg_time_sec"],
                    "reason": "Learners take unusually long — may be unclear/confusing",
                    "priority": "medium"
                })
        
        self.performance = {
            "total_interactions": len(self.logs),
            "unique_cards_tested": len(stats),
            "unique_learners": len(set(log["learner_id"] for log in self.logs)),
            "flagged_cards": flagged_cards,
            "card_stats": stats,
            "generated_at": datetime.now().isoformat()
        }
        return self.performance

    def generate_faculty_report(self, output_path: str = "build/learning_feedback.json"):
        """Save full feedback report for faculty dashboard"""
        self.analyze_performance()
        Path("build").mkdir(exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(self.performance, f, indent=2, ensure_ascii=False)
        
        # Print summary
        print("\n📊 Learning Feedback Report")
        print(f"   Total interactions: {self.performance['total_interactions']}")
        print(f"   Cards tested: {self.performance['unique_cards_tested']}")
        print(f"   ⚠️  Cards flagged for review: {len(self.performance['flagged_cards'])}")
        
        if self.performance["flagged_cards"]:
            print("\n🔔 Flagged Cards — Review Recommended:")
            for flag in sorted(self.performance["flagged_cards"], key=lambda x: x["priority"]):
                emoji = "🔴" if flag["priority"] == "high" else "🟡"
                print(f"   {emoji} {flag['card_id']}: {flag['reason']} ({flag.get('accuracy', 'N/A')}% correct)")
        
        return self.performance

if __name__ == "__main__":
    import sys
    cards_file = sys.argv[1] if len(sys.argv) > 1 else "cards.json"
    logs_file = sys.argv[2] if len(sys.argv) > 2 else "learning_logs.json"
    analytics = LearningAnalytics(cards_file, logs_file)
    analytics.generate_faculty_report()
