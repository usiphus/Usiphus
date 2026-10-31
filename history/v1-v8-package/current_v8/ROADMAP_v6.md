# Version 6.0 roadmap — specialty-focused clinical case knowledge base

**Status:** historical planning concept formalized as a roadmap; no V6 database implementation is present in this repository.  
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

V6 should consume V5 source-status/evidence contracts and provide structured records for V7 review and V8 media-aware card generation. The current V7 code stores data in files and does not implement this database layer; V6 remains a design-stage version in the delivered project.
