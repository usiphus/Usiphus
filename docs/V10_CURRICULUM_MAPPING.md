# Usiphus V10 — Specialty Curriculum Mapping

## Purpose

V10 adds a curriculum-mapping and coverage-analysis layer to the V7–V9
examination-intelligence pipeline. It relates existing exam cards to an
explicit specialty curriculum and makes gaps visible by domain and competency.

## Delivered in this version

- JSON curriculum ingestion with stable competency IDs, domains, Bloom levels,
  required/optional status, learning objectives, tags, and configurable weights.
- V9-compatible card ingestion (list or `{"cards": [...]} `).
- Explicit card-to-competency IDs, treated as pending until backed by auditable faculty approval metadata.
- Faculty approval records with required reviewer identity, approval status, and timezone-aware review timestamp.
- Deterministic lexical candidate generation for unmapped pairs.
- Strict separation of verified mappings, suggested mappings, and invalid IDs.
- Weighted coverage and per-domain coverage summaries.
- Lists of required competencies without verified links and cards with no
  candidate mapping.
- JSON, Markdown, CSV mapping matrix, and build manifest outputs.
- Built-in regression tests, atomic text output writes, and atomic CSV matrix export.
- Strict validation for finite positive weights, boolean required flags, specialty alignment, and finite match thresholds.
- Auditable faculty approval provenance included in JSON and CSV reports.

## Important semantics

A lexical suggestion is not a verified mapping. It never increases verified
coverage. An ID in `competency_ids` (or `curriculum_competencies`) is pending,
not verified. To count it as verified, the card must also include a matching
`faculty_approvals` record with `status: "approved"`, a named `reviewer`,
and a timezone-aware ISO-8601 `reviewed_at` timestamp. These fields provide
audit metadata; they do not cryptographically authenticate the reviewer.

Cards must have a specialty matching the curriculum specialty exactly. Missing
or mismatched specialties are rejected instead of being mixed into coverage.

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
  "competency_ids": ["URO-01"],
  "faculty_approvals": [{
    "competency_id": "URO-01",
    "status": "approved",
    "reviewer": "Dr Faculty Reviewer",
    "reviewed_at": "2026-10-31T12:00:00Z"
  }]
}
```

## Output files

- `curriculum_coverage.json`: machine-readable report.
- `curriculum_coverage.md`: readable summary by domain.
- `curriculum_mapping_matrix.csv`: card/competency candidate matrix.
- `curriculum_manifest.json`: version and output metadata.

## Validation status

The script includes nineteen deterministic self-tests. A GitHub Actions workflow
now compiles the script and runs the self-tests on pushes to this feature branch
and relevant pull requests. A green workflow run is required before describing
the release as runtime-tested. Software tests do not establish clinical validity.
