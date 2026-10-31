# Version 7.0 roadmap — advanced clinical reasoning, quality review, and pre-V8 hardening

**Status:** V7 code foundations implemented locally; full clinical validation and several release-hardening tasks remain open.
**Purpose:** make V4/V5/V6 case data and card generation traceable, reviewable, reproducible, and safe enough to serve as the stable foundation for V8 visual cards.

V7 is an educational content pipeline, not a diagnostic or treatment decision-support system. A case report describes an individual patient and must not be generalized into a clinical recommendation.

## 1. V7 objectives

1. Upgrade the existing harvester and converter version markers to 7.0.0.
2. Make generated cards auditable at source-sentence level.
3. Add restrained clinical-reasoning prompts that quote author-stated reasoning rather than inventing next steps.
4. Make machine review flags explicit and provide a structured human-review queue.
5. Add a strict evidence export gate.
6. Expand regression tests and define the remaining release gates before V8.

## 2. Implemented in this V7 workspace

### 2.1 Evidence-linked card records

- Every card has a deterministic `card_id` derived from case identity, card type, and source URL.
- Every extracted evidence sentence has a deterministic `evidence_id`.
- Evidence spans preserve the exact section title/category, sentence text, and section-local character offsets (`start_char`, `end_char`).
- Cards include `schema_version`, `evidence_spans`, `evidence_coverage`, and `review_status`.
- Diagnosis cards require a direct diagnosis evidence sentence for evidence coverage. A title-inferred diagnosis does not become evidence-locked merely because unrelated clinical context is present.

### 2.2 Advanced reasoning cards — intentionally conservative

- Added a `reasoning` card type sourced only from explicit reasoning sentences in the article's discussion section.
- The card asks what reasoning point the authors explicitly emphasized.
- The back includes a warning that the author's reasoning is not an independently validated diagnostic rule.
- Disable this card type with `--no-reasoning`.
- V7 deliberately does **not** generate a “next best investigation”, “next best management”, or guideline recommendation from case chronology. Those require an independent, current guideline source layer and are not safe to infer from one case report.

### 2.3 Quality review and export controls

- Per-case quality reports now include `review_status`, `review_reasons`, and `evidence_coverage`.
- Newly generated cards default to `needs-human-review`; the pipeline never auto-approves clinical content.
- `--review-jsonl` writes a structured card review queue containing card IDs, case/source metadata, front/back content, and exact evidence spans.
- The original V7 `--review-decisions` JSONL workflow is retained only as a legacy annotation/import path; unsigned reviewer labels cannot authorize approval.
- V5.8.2 adds signed reviewer tokens, append-only hash-bound decisions in SQLite, per-answer-claim clinician assessments, and `--review-audit-db` as the only authority for `--approved-only` exports. Current card content and evidence spans must match the audited records.
- `--strict-evidence` exports only cards that have a direct evidence span and a source URL.
- Export counts, card types, and evidence coverage are synchronized with the actual exported cards after strict filtering and `--limit`.
- The existing `--quality-report` CSV includes the new review fields.

### 2.4 Regression coverage

Added tests for:
- exact source span offsets;
- stable card/evidence IDs;
- author-reasoning card generation and opt-out;
- title-inferred diagnoses not being treated as direct evidence;
- strict-evidence export and review JSONL generation;
- V7 version markers;
- continued synchronization of export and quality-report counts.

## 3. V7 workflow

```bash
# Harvest specialty-specific open full-text case reports
python specialty_harvest.py --specialty urology cardiology --year-from 2018 --limit 40 --output case_library

# Build cards, quality report, review queue, and preview
python cases2anki.py case_library --format both --output decks/v7_cases \
  --quality-report --review-jsonl --preview --strict-evidence

# If reasoning cards are not wanted
python cases2anki.py case_library/urology --format tsv --output decks/urology \
  --no-reasoning --quality-report --review-jsonl
```

The JSONL queue is an export for external/manual review. V7 supports importing explicit decisions, but the V7-local import was unauthenticated; V5.8.2 now adds signed reviewer tokens and SQLite append-only history, but institutional SSO/MFA and external immutable audit remain out of scope.

## 4. Remaining work before V8 — release blockers

These items should be completed or explicitly accepted as limitations before V8 begins embedding and transforming clinical images.

### P0 — clinical safety and source integrity

1. **Authoritative retraction/correction checks:** query Europe PMC article status and Crossref update relationships; exclude retracted records by default; preserve status source, timestamp, and update relationships.
2. **Human review governance:** V7 now imports validated decisions and gates `--approved-only` exports. V5.8.2 adds HMAC-signed role-scoped tokens, mandatory timezone-aware timestamps, append-only hash-bound history, clinician-only approvals, and claim-level support assessments. Institutional identity provisioning, MFA, external immutable audit, and separation-of-duties still require deployment integration. Only explicitly approved cards should be eligible for a “reviewed” export.
3. **Claim-level evidence coverage:** represent each answer claim separately and require every clinically meaningful claim to map to one or more evidence spans. Current V7 spans are sentence-level and do not prove every clause in a long answer.
4. **Clinical validation set:** create clinician-adjudicated cases across specialties and measure diagnosis precision/recall, assertion labels, evidence-span correctness, specialty classification, and card factuality. Publish per-specialty results and known failure classes.
5. **Guideline source separation:** if “best next step” or guideline comparison cards are added, store guideline source, publisher, version/date, recommendation wording/strength, URL, and access date separately from case-report facts.
6. **License decision layer:** preserve article and asset license metadata; use explicit allow/reject/unknown reuse decisions. Unknown rights must block image embedding and redistribution.

### P1 — robustness and maintainability

7. **Schema validation and migration:** define versioned schemas for `meta.json`, manifest records, card records, evidence spans, and future visual assets. Validate old cache records before reuse.
8. **HTTP reliability:** parse both numeric and HTTP-date `Retry-After`; add bounded response-size limits, timeouts, retry budgets, and structured failure categories.
9. **Checkpoint/resume:** persist per-query and per-candidate states (`discovered`, `downloaded`, `parsed`, `rejected`, `failed`) so interrupted harvests can resume without repeating successful work.
10. **Identity resolution:** normalize PMID, PMCID, DOI, title, correction links, and version-of-record relationships without merging distinct case reports.
11. **Assertion evaluation:** test negation, temporality, experiencer, uncertainty, and family-history detection against a labelled clinical set before changing rules or enabling an NLP model.
12. **Specialty evaluation:** measure precision/recall per specialty; reduce broad-term false positives; retain classifier explanation and original terminology.
13. **Parser hardening:** add malformed/hostile XML tests, nested JATS sections, mixed content, tables, figures, supplementary files, Unicode, and unusual punctuation fixtures.
14. **Anki stability:** verify stable note GUIDs and model compatibility against actual Anki imports; define safe update semantics that preserve review history.

### P2 — engineering and operations

15. Add CI for supported Python versions and operating systems.
16. Add Ruff lint/format checks and optional static type checking.
17. Add property-based/fuzz tests for XML, text normalization, URLs, and malformed metadata.
18. Add deterministic fixture-based end-to-end tests from harvested JATS to TSV/APKG and review queue.
19. Add optional live API smoke tests that are rate-limited and do not run in the default offline suite.
20. Add performance/memory tests for large corpora and bounded output generation.
21. Add structured logs and a run summary with counts by specialty, source, rejection reason, and failure stage.
22. Add a reproducible clean-install test and dependency pin/lock strategy.

## 5. V8 entry criteria

V8 should begin only when:

- [ ] P0 source integrity and license safeguards are implemented or formally scoped out for the first V8 release.
- [ ] Every exported card has stable identity, a source URL, and auditable evidence spans.
- [ ] The human review process has institution-provisioned signed reviewer tokens, auditable append-only decision history, and clearly distinguishes unreviewed from approved content (institutional SSO/MFA remains external).
- [ ] Retraction/correction handling is tested.
- [ ] A clinician-labelled validation set and baseline metrics are available.
- [ ] CI, offline tests, and clean-install tests pass.
- [ ] Anki import/update behaviour is smoke-tested.
- [ ] The image/figure data contract includes source, caption, license, attribution, and permission state before any visual card is generated.

## 6. Known limitations

- Rule-based diagnosis and reasoning extraction remain heuristic; evidence spans improve auditability but do not prove clinical correctness.
- The reasoning card reproduces author-stated reasoning only. It does not independently evaluate the reasoning.
- Review decisions can be imported, but the legacy V7 import accepted user-supplied reviewer names; V5.8.2 adds signed-token and database-audit workflows; local SQLite is not tamper-proof against host/database replacement.
- `--strict-evidence` requires a direct source sentence and source URL; it does not guarantee that every statement in a card has been independently validated.
- Retraction screening is now available as a best-effort V5/V8 foundation, and V6 provides a local SQLite store. Guideline retrieval/governance, clinician validation, institutional SSO/MFA, clinician-adjudicated gold metrics, visual extraction, and visual card generation remain unimplemented or incomplete.

## 7. Release verification

Run:

```bash
python -m py_compile specialty_harvest.py cases2anki.py
python -m unittest discover -s tests -v
```

The offline suite verifies local logic against synthetic JATS and mocked API responses. It does not establish live API compatibility, clinical validity, publisher license compliance, or successful import into every Anki version.
