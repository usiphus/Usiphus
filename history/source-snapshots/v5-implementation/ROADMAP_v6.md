# Version 6.0 roadmap — specialty-focused clinical case knowledge base

**Status:** SQLite schema v4 and core local database workflows are implemented in `case_database.py`; V6 remains partial because deterministic rebuild/diff and richer annotation population are still open.
**Purpose:** define a structured, queryable case library between V5 source/evidence hardening and V7 card review workflows.

## 1. Goals

- Store clinical case reports as normalized records rather than only folders of XML and JSON.
- Support specialty, subspecialty, age group, presentation, diagnosis, investigation, management-as-reported, outcome, and teaching-point filters.
- Preserve original source wording and source identifiers alongside normalized terminology.
- Maintain links between article, patient/case narrative, evidence spans, figures/tables, generated cards, and review decisions.
- Make the data store portable and rebuildable from the source archive.

## 2. Proposed logical entities

| Entity | Key fields | Integrity rule |
|---|---|---|
| Article | PMCID, PMID, DOI, title, journal, year, source URL, XML hash | stable external identifiers; no title-only merge |
| Case | case ID, article ID, specialty, age band, sex if source-reported | do not infer missing demographics |
| Section | section ID, article ID, title, category, order, text hash | preserve source order and exact text |
| Evidence span | span ID, section ID, start/end, quote, source hash | offsets must resolve against source section |
| Concept | original term, normalized ID/system, mapping confidence | original wording is always retained |
| Clinical event | presentation/investigation/diagnosis/treatment/outcome | label treatment as case-reported |
| Asset | figure/table ID, caption, href, licence, checksum, review state | unknown rights block embedding |
| Card | stable card ID, type, content hash, evidence refs, review state | every answer has traceable evidence |

## 3. Storage approach

Start with a versioned SQLite database for a single-user local workflow. Keep harvested XML and original metadata as immutable source artifacts; the database is a rebuildable index, not the sole copy. Use migrations and foreign keys. A future multi-user deployment may move to PostgreSQL, but should not be required for the CLI.

## 4. Import and update workflow

1. Read the existing case folder and validate `meta.json` plus XML SHA-256.
2. Upsert article identity without overwriting conflicting identifiers.
3. Store sections and evidence spans with source hash and parser version.
4. Store normalized concepts as optional annotations, never as replacements for article text.
5. Link generated cards and review decisions by stable IDs and content hashes.
6. On parser/schema changes, rebuild derived records while preserving source files and review history; mark affected approvals stale.

## 5. Search and reporting

- Filter by specialty, source year, reported diagnosis, presentation, investigation, and review status.
- Provide full-text search over article sections and exact evidence quotes.
- Report duplicate/conflicting identifiers and unreviewed records.
- Export a complete database manifest and permit deterministic rebuild from source folders.

## 6. Privacy and clinical boundaries

The dataset is for educational case reports, not identifiable patient records. Do not add patient names, addresses, record numbers, or other direct identifiers. Avoid extracting sensitive demographic details unless they are necessary for the published case and educational purpose. No decision support or patient-specific recommendations.

## 7. Acceptance criteria

- [ ] Schema and migrations documented and tested.
- [ ] Import is idempotent and preserves source XML hashes.
- [ ] Conflicting PMCID/PMID/DOI values are flagged, not silently merged.
- [ ] Evidence spans resolve to exact source text.
- [ ] Rebuild from a clean case folder reproduces the same logical records.
- [ ] Search filters and export manifests have fixture-based tests.

## 8. Relationship to adjacent versions

V6 should consume V5 source-status/evidence contracts and provide structured records for V7 review and V8 media-aware card generation. V6 now provides the local SQLite layer consumed by the V7/V8 workflow. It is not yet a complete rebuildable knowledge-base release: concepts/events are not automatically populated from reviewed annotations, rebuild/diff commands are incomplete, and full rebuild/diff and annotation population remain incomplete.


## 8.2.0 delivery record (schema v4; 2026-10-30)

Implemented in `case_database.py`:
- SQLite schema/migration marker v4 (migrations 1–4) with articles, sections, evidence spans, concepts, clinical events, assets, cards, card claims, append-only card decisions and clinician claim assessments, identifier conflicts, separate guideline sources/claims, and FTS5 index.
- Idempotent case-folder import keyed by PMCID/source path; XML SHA-256 is verified against metadata when supplied. Source XML remains untouched.
- Nested JATS sections are retained; figure/table metadata is indexed without downloading or embedding media.
- Duplicate DOI/PMID identifiers are flagged rather than merged. Conflicting identifiers on an existing PMCID are retained as conflict metadata rather than silently replacing the original identifier.
- Review-queue imports verify exact section-local offsets and quotes. HMAC-authenticated card decisions and claim assessments bind to exact content/evidence/source hashes; high-risk claims require two distinct reviewers. Plain local reviewer names cannot authorize approved-only export.
- CLI supports init/import/search/manifest/review-queue/integrity-check and authenticated decision/claim-assessment/guideline imports.

Remaining work: populate concepts and clinical events from reviewed annotations, add deterministic full rebuild and dry-run diff commands, populate concepts and clinical events, add full rebuild/diff commands, provide institutional SSO/MFA and external immutable audit, and validate behavior against larger real-world JATS corpora.
