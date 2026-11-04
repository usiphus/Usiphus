#!/usr/bin/env python3
"""
Usiphus Terminology Validator
- Extract medical terms using scispaCy
- Standardize & cross-map via UMLS API
- Flag inconsistent/variant terms across your curriculum
"""
import json
import os
import re
from pathlib import Path
from typing import Dict, List, Set, Tuple

import spacy
import requests

# ─── CONFIG ──────────────────────────────────────
UMLS_API_KEY = os.environ.get("UMLS_API_KEY", "")
UMLS_API_URL = "https://uts-ws.nlm.nih.gov"
SCISPACY_MODEL = "en_core_sci_sm"

# Load scispaCy once (local — no internet needed!)
print(f"🔬 Loading {SCISPACY_MODEL}...")
nlp = spacy.load(SCISPACY_MODEL)
print("✅ scispaCy ready")

# ─── TERM EXTRACTION ─────────────────────────────
def extract_terms(text: str) -> List[Dict]:
    """Extract medical entities from text using scispaCy"""
    doc = nlp(text)
    terms = []
    for ent in doc.ents:
        terms.append({
            "text": ent.text,
            "label": ent.label_,
            "start": ent.start_char,
            "end": ent.end_char
        })
    return terms

# ─── UMLS STANDARDIZATION ───────────────────────
class UMLSLookup:
    def __init__(self, api_key: str = UMLS_API_KEY):
        self.api_key = api_key
        self.tgt = None
        if api_key:
            self._get_ticket_granting_ticket()

    def _get_ticket_granting_ticket(self):
        """UMLS authentication — get session ticket"""
        try:
            r = requests.post(
                "https://utslogin.nlm.nih.gov/cas/v1/tickets",
                data={"username": self.api_key},
                timeout=30
            )
            r.raise_for_status()
            self.tgt = r.headers["location"]
            print("✅ UMLS connected")
        except Exception as e:
            print(f"⚠️  UMLS connection failed: {e}")
            print("   Continuing in local mode — term extraction only")
            self.tgt = None

    def _get_service_ticket(self) -> str:
        r = requests.post(self.tgt, data={"service": UMLS_API_URL+"/search"}, timeout=10)
        return r.text

    def standardize(self, term: str) -> Dict:
        """Map a term to its UMLS Concept Unique Identifier & preferred name"""
        if not self.tgt:
            return {"raw_term": term, "cui": None, "preferred_name": None, "status": "offline_mode"}

        try:
            ticket = self._get_service_ticket()
            # Search for the term
            r = requests.get(
                f"{UMLS_API_URL}/rest/search/current",
                params={"apiKey": ticket, "string": term, "searchType": "exact", "pageSize": 1},
                timeout=15
            )
            r.raise_for_status()
            results = r.json()

            if not results.get("result", {}).get("results"):
                return {"raw_term": term, "cui": None, "preferred_name": None, "status": "not_found"}

            cui = results["result"]["results"][0]["ui"]
            # Get preferred name
            ticket2 = self._get_service_ticket()
            detail = requests.get(
                f"{UMLS_API_URL}/rest/content/current/CUI/{cui}",
                params={"apiKey": ticket2},
                timeout=15
            )
            detail.raise_for_status()
            pref_name = detail.json()["result"]["name"]

            return {
                "raw_term": term,
                "cui": cui,
                "preferred_name": pref_name,
                "status": "mapped",
                "uri": detail.json()["result"]["uri"]
            }
        except Exception as e:
            return {"raw_term": term, "error": str(e), "status": "lookup_failed"}

# ─── CONSISTENCY CHECK ───────────────────────────
def check_consistency(all_term_maps: List[Dict]) -> Dict:
    """Find terms that map to the same CUI but have different raw forms"""
    cui_to_forms: Dict[str, Set[str]] = {}
    for item in all_term_maps:
        if item.get("cui") and item.get("status") == "mapped":
            cui = item["cui"]
            cui_to_forms.setdefault(cui, set()).add(item["raw_term"].lower())

    issues = []
    for cui, forms in cui_to_forms.items():
        if len(forms) > 1:
            issues.append({
                "cui": cui,
                "preferred_name": next(i["preferred_name"] for i in all_term_maps if i["cui"] == cui),
                "variants": sorted(forms),
                "action": f"Standardize all to: {next(i['preferred_name'] for i in all_term_maps if i['cui'] == cui)}"
            })

    return {
        "unique_cuis": len(cui_to_forms),
        "inconsistent_sets": len(issues),
        "inconsistencies": issues
    }

# ─── MAIN SCAN ──────────────────────────────────
def scan_card(card: Dict, umls: UMLSLookup) -> Dict:
    """Scan one card — extract + normalize all terms"""
    content = f"{card.get('title', '')} {card.get('content', '')}"
    raw_terms = extract_terms(content)

    normalized = []
    for t in raw_terms:
        mapped = umls.standardize(t["text"])
        normalized.append({**t, **mapped})

    return {
        "card_id": card.get("id", card.get("title")),
        "terms_extracted": len(raw_terms),
        "terms_mapped": sum(1 for n in normalized if n.get("status") == "mapped"),
        "terms": normalized
    }

def scan_cards(cards: List[Dict], output_path: Path):
    """Scan all cards and write full report"""
    umls = UMLSLookup()
    all_results = []
    all_maps = []

    for i, card in enumerate(cards, 1):
        print(f"🔬 Scanning {i}/{len(cards)}: {card.get('title', 'Untitled')}")
        result = scan_card(card, umls)
        all_results.append(result)
        all_maps.extend(result["terms"])

    consistency = check_consistency(all_maps)

    report = {
        "summary": {
            "cards_scanned": len(cards),
            "total_terms_extracted": len(all_maps),
            "unique_normalized": consistency["unique_cuis"],
            "inconsistent_term_groups": consistency["inconsistent_sets"]
        },
        "inconsistencies": consistency["inconsistencies"],
        "card_details": all_results
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    # Print summary
    print("\n" + "="*50)
    print("📋 Terminology Scan Complete")
    print(f"   Cards scanned:    {report['summary']['cards_scanned']}")
    print(f"   Terms extracted:  {report['summary']['total_terms_extracted']}")
    print(f"   Unique concepts:  {report['summary']['unique_normalized']}")
    print(f"   ⚠️  Inconsistencies found: {report['summary']['inconsistent_term_groups']}")

    if consistency["inconsistencies"]:
        print("\n🔴 Variations Detected — Please Standardize:")
        for issue in consistency["inconsistencies"]:
            print(f"   • {issue['preferred_name']} → variants: {', '.join(issue['variants'])}")

    print(f"\n💾 Full report: {output_path}")
    print("\n⚠️  UMLS mappings are advisory. Always verify medical terminology.")
    return report

# ─── CLI ────────────────────────────────────────
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: UMLS_API_KEY=your_key python usiphus_terminology.py <cards.json> [output.json]")
        print("\nInput format: [{id, title, content}, ...]")
        sys.exit(1)

    input_file = Path(sys.argv[1])
    output_file = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("build/terminology_report.json")
    output_file.parent.mkdir(exist_ok=True)

    with open(input_file, encoding="utf-8") as f:
        cards = json.load(f)

    scan_cards(cards, output_file)
