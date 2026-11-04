#!/usr/bin/env python3
"""
LOINC Lab Test Validator
- Map lab/test names to standard LOINC codes
- Validate lab terminology across curriculum cards
- API: https://loinc.org/usage/terms/registration/ — free API, no key needed
"""
import json
import re
import requests
from pathlib import Path
from typing import Dict, List, Set

LOINC_API = "https://loinc.org/api/search"

def clean_lab_term(term: str) -> str:
    """Normalize lab names"""
    term = term.strip().lower()
    term = re.sub(r'[\s\-_]+', ' ', term)
    term = re.sub(r'\b( test| analysis| level| in| of| serum| blood| urine| csf| plasma)\b', '', term)
    return term.strip()

def lookup_lab(term: str, max_results: int = 3) -> Dict:
    """Search LOINC for a lab test name"""
    original = term
    cleaned = clean_lab_term(term)

    if not cleaned or len(cleaned) < 2:
        return {"term": original, "status": "empty"}

    try:
        # Use LOINC's public search API
        params = {
            "query": cleaned,
            "limit": max_results,
            "df": "name,code,component,property,time_asp,scale_method,system"
        }
        r = requests.get(LOINC_API, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()

        results = data.get("results", [])
        if not results:
            return {"term": original, "status": "not_found", "cleaned": cleaned}

        # Pick best match — shortest name usually most canonical
        best = min(results, key=lambda x: len(x.get("name", "")))

        return {
            "term": original,
            "status": "mapped",
            "loinc_code": best.get("code"),
            "official_name": best.get("name"),
            "component": best.get("component"),
            "property": best.get("property"),
            "system": best.get("system"),
            "scale_method": best.get("scale_method"),
            "all_candidates": [
                {"code": r.get("code"), "name": r.get("name")}
                for r in results[:5]
            ]
        }

    except requests.RequestException as e:
        return {"term": original, "status": "error", "error": str(e)}

def validate_lab_terms(terms: List[str]) -> Dict:
    """Validate a list of lab test names — find variants"""
    results = []
    code_to_forms: Dict[str, Set[str]] = {}

    for term in terms:
        info = lookup_lab(term)
        results.append(info)
        if info.get("status") == "mapped":
            code_to_forms.setdefault(info["loinc_code"], set()).add(term.lower())

    inconsistencies = []
    for code, forms in code_to_forms.items():
        if len(forms) > 1:
            pref = next(r["official_name"] for r in results if r.get("loinc_code") == code)
            inconsistencies.append({
                "loinc_code": code,
                "preferred_name": pref,
                "variants": sorted(forms),
                "recommendation": f"Standardize to: {pref} ({code})"
            })

    return {
        "total_terms": len(terms),
        "unique_codes": len(code_to_forms),
        "mapped": sum(1 for r in results if r["status"] == "mapped"),
        "not_found": sum(1 for r in results if r["status"] == "not_found"),
        "inconsistencies": inconsistencies,
        "details": results
    }

# ─── CLI ────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python usiphus_loinc.py 'HbA1c, Hemoglobin, Creatinine'")
        print("   or: python usiphus_loinc.py labs.json [output.json]")
        sys.exit(1)

    if Path(sys.argv[1]).exists():
        with open(sys.argv[1], encoding="utf-8") as f:
            terms = json.load(f)
        result = validate_lab_terms(terms)
        out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("build/loinc_report.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"✅ Done — {result['mapped']}/{result['total_terms']} mapped")
        if result["inconsistencies"]:
            print(f"⚠️  {len(result['inconsistencies'])} inconsistency groups found:")
            for inc in result["inconsistencies"]:
                print(f"   • {inc['loinc_code']}: {inc['preferred_name']}")
                print(f"     Variants: {', '.join(inc['variants'])}")
    else:
        terms = [t.strip() for t in sys.argv[1].split(",")]
        result = validate_lab_terms(terms)
        print(json.dumps(result, indent=2, ensure_ascii=False))
