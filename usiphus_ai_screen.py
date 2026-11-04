#!/usr/bin/env python3
"""
Usiphus AI Screening — automated pre-review before faculty review
Runs locally with Ollama; does NOT make clinical decisions — only triage
"""
import json
import sys
from pathlib import Path
from usiphus_terminology import scan_card as scan_terms, UMLSLookup

try:
    import requests
except ImportError:
    print("⚠️  Install requests: pip install requests")
    sys.exit(1)

OLLAMA_MODEL = "llama3.1:8b"
OLLAMA_URL = "http://localhost:11434/api/generate"
RUBRIC_PATH = Path(__file__).parent / "docs" / "faculty_review_rubric.json"
PROMPT_PATH = Path(__file__).parent / "docs" / "ollama_screening_prompt.md"

def load_template():
    """Load the prompt template"""
    with open(PROMPT_PATH, encoding="utf-8") as f:
        return f.read()

def build_prompt(card_data):
    """Inject card data into prompt template"""
    prompt = load_template()
    for key, val in card_data.items():
        prompt = prompt.replace("{{" + key + "}}", str(val))
    return prompt

def screen_card(card_data):
    """Send one card to Ollama for screening"""
    prompt = build_prompt(card_data)
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "temperature": 0.1  # Low temp = consistent, conservative
    }
    resp = requests.post(OLLAMA_URL, json=payload, timeout=120)
    resp.raise_for_status()
    output_text = resp.json()["response"].strip()
    
    # Extract JSON — handle any markdown code fences
    if output_text.startswith("```json"):
        output_text = output_text[7:]
    if output_text.startswith("```"):
        output_text = output_text[3:]
    output_text = output_text.rstrip("` \n")
    
    result = json.loads(output_text.strip())
    result["card_id"] = card_data.get("id", card_data.get("title"))
    result["review_status"] = "AI_SCREENED — awaiting faculty"
    return result

def generate_report(results, output_path):
    """Write screening report + faculty review queue"""
    total = len(results)
    auto = sum(1 for r in results if r["verdict"] == "auto_approve")
    flag = sum(1 for r in results if r["verdict"] == "flag_for_review")
    block = sum(1 for r in results if r["verdict"] == "block_until_review")
    
    report = {
        "summary": {
            "total_cards": total,
            "auto_screened_pass": auto,
            "flagged_for_review": flag,
            "blocked_until_review": block,
            "review_saving_estimate": f"{round((auto/total)*100)}% faculty time saved"
        },
        "faculty_review_queue": [r for r in results if r["verdict"] != "auto_approve"],
        "full_results": results
    }
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    
    print(f"\n📊 Screening Complete")
    print(f"   Total cards:     {total}")
    print(f"   ✅ Auto-pass:    {auto}")
    print(f"   ⚠️  Flagged:      {flag}")
    print(f"   🔴 Needs review: {block}")
    print(f"   💾 Report saved: {output_path}")
    return report

def main():
    if len(sys.argv) < 2:
        print("Usage: python usiphus_ai_screen.py <cards.json> [output.json]")
        print("\nInput JSON format: [{id, title, content, references, specialty, level}]")
        sys.exit(1)
    
    input_file = Path(sys.argv[1])
    output_file = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("build/screening_report.json")
    output_file.parent.mkdir(exist_ok=True)
    
    with open(input_file, encoding="utf-8") as f:
        cards = json.load(f)
    
    # Initialize UMLS lookup once before looping through cards
    umls = UMLSLookup()
    
    results = []
    for i, card in enumerate(cards, 1):
        print(f"🔍 Screening {i}/{len(cards)}: {card.get('title', 'Untitled')}...")
        try:
            result = screen_card(card)
            
            # Terminology mapping validation scan
            term_report = scan_terms(card, umls)
            if term_report["terms_mapped"] < term_report["terms_extracted"] * 0.5:
                result["verdict"] = "flag_for_review"
                result["comments"].append("⚠️ Low terminology mapping — check for typos or non-standard terms")
            
            results.append(result)
        except Exception as e:
            results.append({
                "card_id": card.get("id"),
                "verdict": "block_until_review",
                "comments": [f"Screening error — manual review required: {e}"],
                "overall_score": 0
            })
    
    generate_report(results, output_file)
    print("\n✅ Done — Faculty review queue ready at:", output_file)
    print("\n⚠️  IMPORTANT: AI screening does NOT replace faculty validation.")
    print("   All flagged items MUST be reviewed by qualified clinical faculty.")

if __name__ == "__main__":
    main()
