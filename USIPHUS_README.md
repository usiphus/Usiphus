# Usiphus

Medical exam-deck builder, validation pipeline, evidence governance, and semantic review tools.

## Version sequence

- **V7 — Examination Intelligence:** multi-source ingestion, canonical card model, exam-format validation, deterministic Anki identifiers, atomic exports, metadata completeness scoring, and lexical duplicate screening.
- **V8 — Clinical Evidence & Knowledge Governance:** V7 plus a claim-to-reference evidence registry, draft → in-review → approved → published workflow checks, reviewer/audit metadata, freshness checks, contradiction candidates, and an optional release gate.
- **V9 — Semantic Exam Intelligence:** V8 plus optional local sentence-transformer / TF-IDF / lexical similarity, semantic duplicate candidates, prompt-answer overlap diagnostics, and a review queue. Automated suggestions never rewrite clinical answers.

## Requirements

- Python 3.10+ (standard library for the core pipeline)
- Optional `genanki` for `.apkg` export
- Optional `sentence-transformers` or `scikit-learn` for richer V9 similarity; V9 safely falls back to lexical Jaccard

## Quick start

```bash
python usiphus_v7.py --specialty surgery --exam-cards cards.json --self-test
python usiphus_v8.py --specialty surgery --exam-cards cards.json --evidence-registry evidence.json --self-test
python usiphus_v9.py --specialty surgery --exam-cards cards.json --semantic-backend auto --self-test
```

## V8 evidence registry format

```json
{
  "claims": [
    {
      "claim_id": "claim-001",
      "card_id": "card-001",
      "claim": "The clinical statement being supported",
      "reference": "Guideline or publication citation",
      "source_type": "clinical_guideline",
      "status": "approved",
      "reviewed_on": "2026-10-01",
      "reviewer": "reviewer-id",
      "audit_trail": [
        {"to": "in_review", "actor": "reviewer-id", "date": "2026-09-28"},
        {"to": "approved", "actor": "reviewer-id", "date": "2026-10-01"}
      ]
    }
  ]
}
```

Use `--require-approved-evidence` to block release unless every card has mapped evidence in an approved/published state with a current review, reviewer, and valid audit trail. `--force` is an explicit override. Contradiction candidates are deliberately conservative review prompts, not clinical adjudications.

## V9 semantic review

```bash
python usiphus_v9.py --specialty surgery --exam-cards cards.json \
  --semantic-backend auto --semantic-threshold 0.82 --output ./build
```

V9 writes `semantic_review_queue.json`. The `auto` backend tries sentence-transformers, then TF-IDF, then lexical Jaccard. Sentence-transformers may need to download its model the first time; use `--semantic-backend lexical` for a strictly dependency-free run.

## Clinical safety boundary

These tools validate data structure, metadata, workflow state, and heuristic similarity. They do **not** verify clinical truth, guideline currency, source quality, or patient safety. Contradiction and semantic findings require qualified human review. No version automatically changes or approves medical answers.