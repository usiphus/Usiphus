#!/usr/bin/env python3
"""
#1 — Evidence Auto-Updater
Scans references quarterly → detects updated guidelines → flags outdated cards
"""
import json
import re
import time
import feedparser
import requests
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Set, Tuple

EVIDENCE_SOURCES = {
    "pubmed": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
    "who_rss": "https://www.who.int/feeds/entity/guidelines/en/rss.xml",
    "updates_log": "build/evidence_updates.json"
}

GUIDELINE_KEYWORDS = [
    "guideline", "recommendation", "clinical practice", "standard of care",
    "updated", "new", "consensus", "position paper"
]

class EvidenceUpdater:
    def __init__(self, cards_path: str = "cards.json"):
        self.cards_path = Path(cards_path)
        self.cards = self._load_cards()
        self.updates_log = self._load_updates_log()
        self.alerts = []

    def _load_cards(self) -> List[Dict]:
        with open(self.cards_path, encoding="utf-8") as f:
            return json.load(f)

    def _load_updates_log(self) -> Dict:
        path = Path(EVIDENCE_SOURCES["updates_log"])
        if path.exists():
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        return {"last_check": None, "checks": [], "alerts": []}

    def _save_updates_log(self):
        Path("build").mkdir(exist_ok=True)
        with open(EVIDENCE_SOURCES["updates_log"], "w", encoding="utf-8") as f:
            json.dump(self.updates_log, f, indent=2, ensure_ascii=False)

    def extract_pubmed_id(self, url: str) -> str | None:
        """Extract PMID from PubMed URL"""
        match = re.search(r'pubmed/(\d+)', url) or re.search(r'pmid=(\d+)', url)
        return match.group(1) if match else None

    def check_pubmed_update(self, pmid: str, older_than_days: int = 1095) -> Dict:
        """Check if a PubMed reference is older than threshold or superseded"""
        try:
            r = requests.get(
                f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
                params={"db": "pubmed", "id": pmid, "retmode": "json"},
                timeout=15
            )
            data = r.json()
            article = data["result"][pmid]
            
            pub_date_str = article.get("pubdate", "")
            pub_year = int(re.search(r'20\d{2}|19\d{2}', pub_date_str).group()) if re.search(r'20\d{2}|19\d{2}', pub_date_str) else 0
            
            current_year = datetime.now().year
            is_old = (current_year - pub_year) * 365 > older_than_days if pub_year else None
            
            # Check for "cited by" or "superseded" status
            title = article.get("title", "").lower()
            is_guideline = any(kw in title for kw in GUIDELINE_KEYWORDS)
            
            return {
                "pmid": pmid,
                "year": pub_year,
                "is_older_than_3y": is_old,
                "is_guideline": is_guideline,
                "status": "old_reference" if is_old else "current"
            }
        except Exception as e:
            return {"pmid": pmid, "status": "lookup_failed", "error": str(e)}

    def scan_card_references(self, card: Dict) -> List[Dict]:
        """Check all references on one card"""
        alerts = []
        refs = card.get("references", [])
        
        for ref in refs:
            pmid = self.extract_pubmed_id(ref)
            if pmid:
                status = self.check_pubmed_update(pmid)
                if status["status"] != "current":
                    alerts.append({
                        "card_id": card.get("id"),
                        "reference": ref,
                        "pmid": pmid,
                        "alert_type": status["status"],
                        "message": f"Reference from {status.get('year', 'unknown')} — consider checking for newer guidelines",
                        "severity": "medium" if status["is_older_than_3y"] else "low"
                    })
        return alerts

    def check_who_guidelines(self):
        """Check WHO RSS feed for new guidelines"""
        alerts = []
        try:
            feed = feedparser.parse(EVIDENCE_SOURCES["who_rss"])
            for entry in feed.entries[:10]:  # Last 10 items
                for card in self.cards:
                    title_terms = set(card.get("title", "").lower().split())
                    entry_terms = set(entry.title.lower().split())
                    overlap = title_terms & entry_terms
                    if len(overlap) >= 2:
                        alerts.append({
                            "card_id": card.get("id"),
                            "alert_type": "possible_new_guideline",
                            "title": entry.title,
                            "link": entry.link,
                            "summary": entry.get("summary", ""),
                            "severity": "high",
                            "message": f"Possible NEW guideline matches this topic — please review"
                        })
        except Exception as e:
            print(f"⚠️  RSS check failed: {e}")
        return alerts

    def run_full_check(self) -> Dict:
        """Run complete evidence currency scan"""
        print("🔍 Running evidence currency check...")
        all_alerts = []
        
        # Check PubMed references
        for card in self.cards:
            card_alerts = self.scan_card_references(card)
            all_alerts.extend(card_alerts)
        
        # Check for new guidelines
        guideline_alerts = self.check_who_guidelines()
        all_alerts.extend(guideline_alerts)
        
        # Update log
        now = datetime.now().isoformat()
        self.updates_log["last_check"] = now
        self.updates_log["alerts"] = all_alerts
        self.updates_log["summary"] = {
            "cards_checked": len(self.cards),
            "alerts_generated": len(all_alerts),
            "high_severity": sum(1 for a in all_alerts if a.get("severity") == "high"),
            "medium_severity": sum(1 for a in all_alerts if a.get("severity") == "medium"),
        }
        self._save_updates_log()
        
        # Print summary
        print("\n📋 Evidence Update Check Complete")
        print(f"   Cards checked: {len(self.cards)}")
        print(f"   ⚠️  Total alerts: {len(all_alerts)}")
        print(f"   🔴 High priority: {self.updates_log['summary']['high_severity']}")
        print(f"   💾 Report: {EVIDENCE_SOURCES['updates_log']}")
        
        if all_alerts:
            print("\n🔔 Alerts:")
            for alert in all_alerts[:10]:
                emoji = "🔴" if alert["severity"] == "high" else "🟡"
                print(f"   {emoji} {alert['card_id']}: {alert['message']}")
        
        return self.updates_log

if __name__ == "__main__":
    import sys
    cards_file = sys.argv[1] if len(sys.argv) > 1 else "cards.json"
    updater = EvidenceUpdater(cards_file)
    updater.run_full_check()
