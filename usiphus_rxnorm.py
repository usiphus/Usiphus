#!/usr/bin/env python3
"""
RxNorm Drug Name Validator
- Normalize drug names (paracetamol ↔ acetaminophen → one standard)
- Check brand/generic/formulation consistency
- API: https://rxnav.nlm.nih.gov/RxNormAPIs.html (free, no API key!)
"""
import json
import re
import requests
from pathlib import Path
from typing import Dict, List, Optional, Set

RXNORM_API = "https://rxnav.nlm.nih.gov/REST"

def clean_drug_term(term: str) -> str:
    """Normalize whitespace/casing for lookup"""
    return re.sub(r'\s+', ' ', term.strip().lower())

def lookup_drug(term: str, fuzzy: bool = True) -> Dict:
    """Look up a drug in RxNorm — returns best match + alternatives"""
    term = clean_drug_term(term)
    if not term:
        return {"term": term, "status": "empty"}

    try:
        # Step 1: Search for the term
        r = requests.get(
            f"{RXNORM_API}/drugs.json",
            params={"name": term, "search": "2" if fuzzy else "0"},
            timeout=15
        )
        r.raise_for_status()
        data = r.json()

        groups = data.get("drugGroup", {}).get("conceptGroup", [])
        if not groups:
            return {"term": term, "status": "not_found"}

        # Get best match — prefer branded/full names
        best = None
        all_candidates = []
        for group in groups:
            concepts = group.get("conceptProperties", [])
            for c in concepts:
                all_candidates.append(c)
                if not best or len(c["name"]) < len(best["name"]):
                    best = c

        if not best:
            return {"term": term, "status": "not_found"}

        # Step 2: Get all synonyms for this RxCUI
        r2 = requests.get(
            f"{RXNORM_API}/rxcui/{best['rxcui']}/allnames.json",
            timeout=15
        )
        names_data = r2.json() if r2.ok else {}
        all_names = names_data.get("allNames", {}).get("name", [])

        return {
            "term": term,
            "status": "mapped",
            "rxcui": best["rxcui"],
            "preferred_name": best["name"],
            "synonyms": [n.strip() for n in all_names if n.strip()],
            "brand_names": list(set(
                c["name"] for c in all_candidates
                if c["tty"] == "BN"
            )),
            "type": best["tty"]
        }

    except requests.RequestException as e:
        return {"term": term, "status": "error", "error": str(e)}

def validate_drug_terms(terms: List[str]) -> Dict:
    """Check all drug terms — find variants that should be standardized"""
    results = []
    rxcui_to_forms: Dict[str, Set[str]] = {}

    for term in terms:
        info = lookup_drug(term)
        results.append(info)
        if info.get("status") == "mapped":
            rxcui_to_forms.setdefault(info["rxcui"], set()).add(term.lower())

    inconsistencies = []
    for rxcui, forms in rxcui_to_forms.items():
        if len(forms) > 1:
            pref = next(r["preferred_name"] for r in results if r.get("rxcui") == rxcui)
            inconsistencies.append({
                "rxcui": rxcui,
                "preferred_name": pref,
                "variants": sorted(forms),
                "recommendation": f"Use '{pref}' consistently"
            })

    return {
        "total_terms": len(terms),
        "unique_concepts": len(rxcui_to_forms),
        "mapped": sum(1 for r in results if r["status"] == "mapped"),
        "not_found": sum(1 for r in results if r["status"] == "not_found"),
        "inconsistencies": inconsistencies,
        "details": results
    }

# ─── CLI ────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python usiphus_rxnorm.py 'drug1, drug2, drug3'")
        print("   or: python usiphus_rxnorm.py drugs.json [output.json]")
        sys.exit(1)

    if Path(sys.argv[1]).exists():
        with open(sys.argv[1], encoding="utf-8") as f:
            terms = json.load(f)
        result = validate_drug_terms(terms)
        out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("build/rxnorm_report.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"✅ Done — {result['mapped']}/{result['total_terms']} mapped")
        if result["inconsistencies"]:
            print(f"⚠️  {len(result['inconsistencies'])} inconsistency groups found:")
            for inc in result["inconsistencies"]:
                print(f"   • {inc['preferred_name']} ← {', '.join(inc['variants'])}")
    else:
        terms = [t.strip() for t in sys.argv[1].split(",")]
        result = validate_drug_terms(terms)
        print(json.dumps(result, indent=2, ensure_ascii=False))
