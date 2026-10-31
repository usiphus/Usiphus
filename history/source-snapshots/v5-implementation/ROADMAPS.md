# Unified project roadmap — V1 through V8

This index is the source of truth for version history. A version is labelled implemented only when corresponding code or a preserved release artifact can be inspected. A design document or archived snapshot is not proof that all planned features shipped.

## Version status

| Version | Evidence in this workspace | Status | Role |
|---|---|---|---|
| V1 | No separately identifiable V1 source tree or release archive | Historical record missing | Original prototype; scope cannot be reconstructed reliably |
| V2 | No separately identifiable V2 source tree or release archive | Historical record missing | Intermediate iteration; scope cannot be reconstructed reliably |
| V3 | `clinical_case_flashcards_v3.0.zip` | Archived release snapshot | Conservative diagnosis extraction, quality CSV, differential/pitfall cards, basic QA, DOI deduplication, cache re-filtering |
| V4 | `ROADMAP_v4.md`, V4 code baseline and tests | Implemented baseline | MeSH relevance, assertion attributes, evidence sentence display, differential cards, deduplication, provenance, cache repair/re-filtering |
| V5 | `ROADMAP_v5.md`, `v5_release.py`, V5 tests | Engineering workflows implemented; clinical-safety release blocked | Persistent checkpoints/cache verification, retry/rate-limit controls, signed role-scoped review tokens, clinician-assessed claim coverage, guideline allow-list workflow, and gold-set evaluation engine; real clinician dataset, institutional IdP, live provider verification, Crossmark and governance approval remain external gates |
| V6 | `ROADMAP_v6.md` | Partial implementation (SQLite schema v4) | Idempotent JATS import, exact evidence validation, FTS5, conflict flags, asset metadata and local review history; concept/event annotation population, deterministic rebuild/diff and authenticated governance remain open |
| V7 | `ROADMAP_v7.md`, current source/tests | Core workflow implemented locally; production gates open | Stable card/evidence IDs, explicit answer claims, signed audit-backed review, clinician claim assessments, high-risk double review, strict-evidence export; institutional SSO/MFA, real clinical validation and external immutable audit remain open |
| V8 | `ROADMAP_v8.md`, current source/tests | Media-inventory foundation implemented; visual release incomplete | Metadata-only figure/table inventory with fail-closed licensing; licensed asset download/validation, visual cards, image review UX and safe Anki update integration remain open |

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
7. A passing offline test suite does not close clinical, licensing, authentication, live-provider, or real-Anki release gates; see `ROADMAP_COMPLETION_REPORT.md`.

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

## 8.2.0 delivery update (2026-10-30)

- V5: engineering workflows are implemented: status screening/retraction gates, persistent harvest checkpoints, retry/rate-limit handling, cache verification, signed reviewer tokens, per-claim authenticated clinical assessments (double review for high-risk claims), guideline ingestion/allow-list gates, and a clinician-gold-set metrics runner. No real adjudicated gold set, institutional IdP/key management, Crossmark adapter, live provider verification, or governance-approved guideline allow-list is supplied; clinical-safety release remains blocked.
- V6: SQLite schema v4, idempotent JATS imports, hash verification, nested sections, FTS5, identity conflict reporting, asset metadata, review-card/evidence import, hash-bound append-only review decisions, authenticated claim assessments, schema migrations v1–v4, and JSONL manifest/review-queue exports are implemented. Concept/event population and deterministic rebuild/diff remain incomplete.
- Verification: 59 offline unit tests pass; Python compilation and SQLite integrity/foreign-key checks pass. HMAC token and clinician-claim workflows are exercised with test keys, not a production identity provider. No real clinical gold-set evaluation, live API run, licensed asset review, or real Anki collection import was performed.
