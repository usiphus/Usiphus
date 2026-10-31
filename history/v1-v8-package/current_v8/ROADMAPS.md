# Unified project roadmap — V1 through V8

This index is the source of truth for version history. A version is labelled implemented only when corresponding code or a preserved release artifact can be inspected. A design document or archived snapshot is not proof that all planned features shipped.

## Version status

| Version | Evidence in this workspace | Status | Role |
|---|---|---|---|
| V1 | No separately identifiable V1 source tree or release archive | Historical record missing | Original prototype; scope cannot be reconstructed reliably |
| V2 | No separately identifiable V2 source tree or release archive | Historical record missing | Intermediate iteration; scope cannot be reconstructed reliably |
| V3 | `clinical_case_flashcards_v3.0.zip` | Archived release snapshot | Conservative diagnosis extraction, quality CSV, differential/pitfall cards, basic QA, DOI deduplication, cache re-filtering |
| V4 | `ROADMAP_v4.md`, V4 code baseline and tests | Implemented baseline | MeSH relevance, assertion attributes, evidence sentence display, differential cards, deduplication, provenance, cache repair/re-filtering |
| V5 | `ROADMAP_v5.md` | Strengthened specification; not implemented as a release | Source-status checks, clinician validation, claim-level evidence, guideline separation, authenticated review, resilient harvesting |
| V6 | `ROADMAP_v6.md` | Formalized design; not implemented | Specialty-focused structured case database, normalized entities, evidence links, SQLite proposal |
| V7 | `ROADMAP_v7.md`, V7 archive | Implemented locally with limitations | Stable card/evidence IDs, sentence spans, author-reasoning cards, review queue/decision import, strict-evidence export |
| V8 | `ROADMAP_v8.md`, current V8 source and tests | V8.0 foundation implemented; full visual-card scope incomplete | Structured media inventory, provenance-first visual foundation, future visual cards and safe Anki updates |

## Version dependency chain

```text
V1 prototype (historical source not preserved)
  -> V2 iteration (historical source not preserved)
  -> V3 archived functional release
  -> V4 stronger extraction, metadata, evidence and QA baseline
  -> V5 source integrity, clinician validation and claim-level evidence specification
  -> V6 structured specialty case knowledge base design
  -> V7 evidence IDs, review workflow and strict export controls
  -> V8 structured JATS media inventory and visual-card foundation
```

## Combined release principles

1. Preserve inspectable historical archives; never overwrite older versions with newer code.
2. Keep original XML and metadata immutable; generated cards and media manifests are derived artifacts.
3. Every clinical card is educational and source-linked, not patient-specific medical advice.
4. Case-reported management must never be presented as a guideline.
5. Unknown article/asset licence means no automatic image embedding or redistribution.
6. Human approval must not be inferred from a model score or reviewer field.
7. V5/V6 requirements remain open until their release gates are implemented and tested.

## Recommended reading order

1. `README.md` — setup and commands.
2. `ROADMAPS.md` — this version history.
3. `ROADMAP_v4.md` — delivered V4 baseline.
4. `ROADMAP_v5.md` — strengthened source-integrity and validation plan.
5. `ROADMAP_v6.md` — structured database design.
6. `ROADMAP_v7.md` — evidence and review implementation.
7. `ROADMAP_v8.md` — V8 media foundation and remaining work.
8. `BUG_AUDIT.md` — audit and verification record.

## Historical archive notes

- `clinical_case_flashcards.zip` and `clinical_case_flashcards_reviewed.zip` are retained as legacy snapshots; neither archive identifies itself as V1 or V2.
- `clinical_case_flashcards_v3.0.zip` is explicitly labelled V3.
- The combined package includes legacy snapshots but does not fabricate V1/V2 roadmap files or claim their exact feature sets.
- V4 was repeated in the request; it is included once as a version, with historical snapshots retained separately.
