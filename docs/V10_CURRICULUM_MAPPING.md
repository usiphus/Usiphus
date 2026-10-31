# Usiphus V10 — Specialty Curriculum Mapping

## Purpose

V10 adds a curriculum-mapping and coverage-analysis layer to the V7–V9
examination-intelligence pipeline. It relates existing exam cards to an
explicit specialty curriculum and makes gaps visible by domain and competency.

## Delivered in this version

- JSON curriculum ingestion with stable competency IDs, domains, Bloom levels,
  required/optional status, learning objectives, tags, and configurable weights.
- V9-compatible card ingestion (list or `{"cards": [...]} `).
- Explicit card-to-competency IDs, treated as verified links.
- Deterministic lexical candidate generation for unmapped pairs.
- Strict separation of verified mappings, suggested mappings, and invalid IDs.
- Weighted coverage and per-domain coverage summaries.
- Lists of required competencies without verified links and cards with no
  candidate mapping.
- JSON, Markdown, CSV mapping matrix, and build manifest outputs.
- Built-in regression tests, atomic text output writes, and atomic CSV matrix export.
- Strict validation for finite positive weights and boolean required flags.

## Important semantics

A lexical suggestion is not a verified mapping. It never increases verified
coverage. Add a competency ID to a card's `competency_ids` (or
`curriculum_competencies`) only after faculty review.

The candidate weighted coverage figure includes suggestions and is displayed
separately from verified weighted coverage. The tool does not claim to assess
clinical correctness, educational effectiveness, or learner competence.

## Run

```bash
python usiphus_v10.py --self-test
python usiphus_v10.py --cards cards.json --curriculum curriculum.json --specialty urology --output build
```

## Curriculum schema example

```json
{
  "specialty": "urology",
  "competencies": [
    {
      "id": "URO-01",
      "title": "Assess acute urinary retention",
      "description": "Recognize and initially manage acute retention",
      "domain": "Emergency urology",
      "level": "apply",
      "required": true,
      "weight": 2,
      "learning_objectives": ["Identify red flags", "Outline immediate management"],
      "tags": ["retention", "emergency"]
    }
  ]
}
```

Cards can explicitly link to one or more curriculum competency IDs:

```json
{
  "card_id": "uro-retention-001",
  "specialty": "urology",
  "prompt": "How do you initially assess acute urinary retention?",
  "answer": "Assess stability, examine the patient, and relieve retention as indicated.",
  "competency_ids": ["URO-01"]
}
```

## Output files

- `curriculum_coverage.json`: machine-readable report.
- `curriculum_coverage.md`: readable summary by domain.
- `curriculum_mapping_matrix.csv`: card/competency candidate matrix.
- `curriculum_manifest.json`: version and output metadata.

## Validation status

The script includes thirteen deterministic self-tests. A GitHub Actions workflow
now compiles the script and runs the self-tests on pushes to this feature branch
and relevant pull requests. A green workflow run is required before describing
the release as runtime-tested. Software tests do not establish clinical validity.
