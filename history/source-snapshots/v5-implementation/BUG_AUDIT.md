# Code review and bug audit

**Review date:** 2026-10-30
**Files inspected:** `specialty_harvest.py`, `cases2anki.py`, `tests/test_pipeline.py`, `README.md`, `ROADMAP_v4.md`, `ROADMAP_v5.md`.
**Review type:** static/manual review plus offline test and compile run. No live Europe PMC requests or real Anki collection import were performed.

## 1. Confirmed defect found and fixed

### Quality report could disagree with the exported deck when `--limit` was used

**Impact:** The converter generated reports for all cards, then truncated `all_cards` for export. The `.quality.csv` therefore could claim a case had more cards (and card types) than were actually present in the TSV/APKG. This makes audit counts misleading for limited exports.

**Fix:** Added `apply_card_limit()` to apply the limit and synchronize each case's `card_count` and `card_types` with exported cards. Cases truncated by the export limit receive the `export-limit-truncated` quality flag.

**Regression test:** `test_card_limit_keeps_quality_report_in_sync`.

## 2. Verified baseline checks

- `python -m unittest discover -s tests -v` — passed before the fix (20 tests).
- `python -m py_compile specialty_harvest.py cases2anki.py` — passed before the fix.
- After the fix, rerun both commands; the expected suite count is 21 tests.
- Tests are offline and use synthetic JATS / mocked API responses. They do not prove live API compatibility, clinical accuracy, licence compliance, or Anki import compatibility.

## 3. Risks and improvement opportunities (not confirmed runtime bugs)

| Area | Observation | Risk | Recommended action |
|---|---|---|---|
| HTTP retry | `Retry-After` is accepted only when it contains integer seconds. HTTP-date values are not parsed. | A server may ask the client to wait until a date; the current code may retry too early. | Parse both delta-seconds and HTTP-date; add 429/503 tests. |
| Download memory | `http_get()` reads the full response into memory without a maximum byte count. | Very large or unexpected responses can consume excessive memory. | Add endpoint-specific response-size limits and stream large XML/assets to disk. |
| Checkpointing | Existing XML/meta cache supports reuse, but there is no explicit per-candidate run-state checkpoint or bounded refill to a retained target. | A long run may repeat search work and cannot clearly distinguish rejected, interrupted, and failed candidates. | Add run ledger and `--target-saved` in V8 Phase 1. |
| Cache schema | Cached `mesh_terms` is consumed without validating that it is a list of strings. | Manually edited/corrupt metadata can produce odd scoring or inconsistent metadata. | Validate and normalize cached metadata against a schema. |
| JATS structure | Converter flattens figure/table nodes through `element_text`; it does not preserve figure graphic references, panel structure, or table cells as structured data. | Visual information is lost; text can be awkwardly concatenated. | Implement typed JATS nodes and media records in V8 Phase 3. |
| Evidence traceability | Diagnosis sentence is stored, but other answer facts and figure-derived claims do not all have independent span IDs and offsets. | A generated answer can be hard to audit at fact level. | Add evidence IDs and character offsets for every answer claim. |
| Assertion NLP | Negation, temporality, and experiencer use sentence-level regular expressions. | Scope across clauses/sentences, abbreviations, coreference, and nuanced family/patient references can be missed. | Build a clinician-labelled evaluation set before replacing or extending rules. |
| Specialty classifier | Specialty relevance is keyword/MeSH overlap with simple substring matching. | Broad or ambiguous terms may cause false positives; MeSH coverage varies. | Evaluate per-specialty precision/recall and move toward normalized concepts / weighted phrase matching. |
| Retraction status | Current V4 code does not perform authoritative retraction/correction screening. | Withdrawn or corrected articles may enter study decks. | Implement status monitor integration and default exclusion before export. |
| Licence enforcement | Licence text is stored, but the current workflow does not enforce a media redistribution decision. | Future image export could include content whose reuse rights are unclear. | Add explicit allow/reject/unknown licence decisions; unknown means no embedded image. |
| Anki integration | Offline tests do not import generated APKG files into a real Anki collection or test update behaviour across Anki versions. | Model/note compatibility and update semantics remain unverified. | Add versioned Anki import smoke tests and stable GUID/update tests. |
| Visual cards | Current converter does not extract or package article images. | The requested visual clues cannot be delivered safely with current parser/output schema. | Build image occlusion and image-recognition cards only after JATS media + licence support. |
| Test coverage | Current suite has 20 baseline tests (21 after the new regression test), mostly synthetic parser/CLI logic. | Several failure paths, network behaviours, image assets, and real import flows are untested. | Add property-based/fuzz tests, malformed XML fixtures, API error fixtures, media fixtures, and clean-install integration tests. |

## 4. Additional code-quality recommendations

1. Add a linter/formatter and static type check to CI (for example Ruff; mypy can be evaluated later).
2. Keep HTTP client logic separate from parsing and scoring so API changes can be tested independently.
3. Replace broad `except` fallbacks with structured warnings where a failed cache read or metadata request should be visible in the audit log.
4. Add explicit JSON schemas and migrations for `meta.json`, manifest rows, cards, and media assets.
5. Add deterministic IDs and checksums for every generated card and asset.
6. Add a dry-run report before deleting/replacing cached article files or changing existing Anki notes.
7. Add integration tests for filenames with Unicode, unusual punctuation, long titles, and duplicate DOI/title combinations.
8. Keep `--limit` semantics documented: in the harvester it limits search candidates; in the converter it limits exported cards. Consider clearer names in a future major release without silently changing existing CLI behaviour.

## 5. Scope boundaries

This audit did not run a live API test, inspect a user-owned Anki collection, validate actual publisher licences, or perform clinician adjudication. The risks above are engineering recommendations, not claims that those failures have already occurred.

## 6. V7 review and implementation update (2026-10-30)

### Implemented improvements

- Bumped harvester and converter version markers to `7.0.0`.
- Added deterministic card and evidence identifiers.
- Added exact section-local evidence spans with source sentence, section title/category, and character offsets.
- Added a conservative author-stated reasoning card from discussion sentences only; it does not generate a next-best-step or guideline recommendation.
- Added `--no-reasoning`, `--strict-evidence`, and `--review-jsonl` controls.
- Added per-case review status/reasons and evidence coverage to the quality CSV.
- Added a JSONL review queue and a decision-import workflow keyed by stable card IDs. `--approved-only` exports only explicit approvals; cards remain `needs-human-review` unless a reviewer decision is supplied.
- Ensured strict-evidence filtering occurs before the card limit and synchronized export counts/card types/evidence coverage with the final deck.
- Ensured title-inferred diagnoses without direct diagnosis evidence are not marked evidence-covered.

### Verification

- `python -m py_compile specialty_harvest.py cases2anki.py tests/test_pipeline.py` — passed.
- `python -m unittest discover -s tests -v` — 29 tests passed.
- Tests remain offline and synthetic. No live Europe PMC requests, real publisher license audit, clinician adjudication, or real Anki collection import was performed.

### Remaining material risks before V8

- Reviewer decisions can be imported and enforced for export, but reviewer identity is user-supplied, timestamps are optional, and the workflow is not authenticated or tamper-proof.
- Evidence is sentence-level, not claim-level. A sentence can contain multiple claims, and a span does not prove clinical correctness.
- Retraction/correction screening, guideline source management, clinician-labelled evaluation, and license-aware image handling remain unimplemented.
- HTTP-date `Retry-After`, response-size bounds, durable run checkpoints, and structured JATS media extraction remain open engineering tasks.

See `ROADMAP_v7.md` for prioritized pre-V8 release gates.

## V8.0 local implementation audit (2026-10-30)

### Changes
- Bumped local harvester and converter version markers to `8.0.0`; new harvested metadata schema marker is `5`.
- Added JATS `fig` and `table-wrap` metadata extraction for a deterministic JSONL asset inventory (`--asset-report`).
- Inventory records include deterministic asset ID, source URL, source XML SHA-256, caption/label, graphic references, and raw licence string.
- Media is never downloaded or embedded by this feature. Missing licence is blocked; present licence requires manual review.
- Added a JSON Schema contract at `schemas/v8_asset.schema.json`.
- Added regression coverage for figure/table extraction, stable IDs, and unknown-licence blocking.

### Verification
- `python -m py_compile specialty_harvest.py cases2anki.py tests/test_pipeline.py` — passed.
- `python -m unittest discover -s tests -v` — 30 tests passed.

### Explicit limitations
- This is a metadata inventory, not visual card generation.
- No article retraction/correction API integration, clinician gold set, authenticated review governance, image download/licence adjudication, OCR, image occlusion, or Anki update-history integration was implemented in this V8 increment.
- No live Europe PMC API run or real Anki import was performed.
- The project is educational software; automated test success does not establish clinical correctness.


## 8.1.0 implementation audit (2026-10-30)

### Implemented in this increment
- Added `source_integrity.py`: Europe PMC `status-update-search`, Crossref `update-to` relation lookup, provider conflict normalization, explicit unknown status, atomic TTL cache, bounded responses, and bounded retries.
- Added default converter exclusion of confirmed retractions plus an explicit warned `--allow-retracted` historical override. Non-current/unknown status warnings are attached to exported cards and quality reports.
- Added harvester HTTP response-size limits and numeric/HTTP-date `Retry-After` support. Status calls share the harvester's rate limiter for their initial requests.
- Added `case_database.py`: SQLite schema v1, idempotent import, source hash verification, nested section indexing, FTS5 search, identity conflict reporting, figure/table inventory, evidence-offset verification, and hash-bound append-only local review decisions.
- Added separate guideline source/claim tables to keep guideline-derived content structurally distinct from case-reported management.
- Preserved historical archives and unclassified V1/V2 artifacts under `history/` without relabelling them.
- Added GitHub Actions CI for Python 3.10–3.13 and expanded the offline suite to 51 tests.

### Verification
- `python -m py_compile specialty_harvest.py cases2anki.py source_integrity.py case_database.py tests/*.py` — passed.
- `python -m unittest discover -s tests -v` — 51 tests passed.
- SQLite `PRAGMA integrity_check` and `PRAGMA foreign_key_check` passed in fixture tests.
- No live Europe PMC/Crossref calls, clinician adjudication, publisher licence audit, or real Anki collection import was performed.

### Remaining risks / not claimed as complete
- Article status provider coverage is best-effort; unknown is not current, and no provider can guarantee absence of every notice. Crossmark is not integrated.
- Clinical extraction remains heuristic. There is no clinician-labelled gold set, performance report, or semantic validation of every claim in an answer.
- Local reviewer names/roles are not authenticated; append-only SQLite triggers do not protect against replacing the database file.
- Guideline tables exist, but no maintained allow-list, ingestion workflow, or clinical review process exists.
- Harvesting does not yet have a durable run ledger, target-saved refill mode, or full cache verifier.
- V8 media remains metadata-only; no asset download, licence adjudication, OCR, visual cards, image occlusion, or safe Anki update semantics.

## 8.2.0 V5 engineering completion audit (2026-10-30)

### Delivered
- Added `v5_release.py`: persistent SQLite harvest state, resumable retry queue, bounded Retry-After/backoff helper, per-host rate limiter, full manifest/cache verification, gold-set evaluation metrics, signed reviewer-token issuance/verification, and guideline record validation.
- Extended `specialty_harvest.py` with `--target-saved`, hard `--max-candidates`, persistent state DB, per-host rate limiting, retryable/permanent failure tracking, and `--verify-cache`.
- Extended `case_database.py` to schema v4 with append-only claim assessments, high-risk flags, double independent clinician assessment, signed reviewer decisions, separate guideline ingestion, and hash-bearing review queue export.
- Changed claim semantics: an evidence span is not itself a claim, and a linked span does not automatically set `evidence_covered`. Authenticated clinician assessment is required.
- Changed `cases2anki.py` so `--approved-only` requires the SQLite audit database and checks current card content, source XML hash, evidence spans, all claim assessments, and signed card approval.
- Added a gold-set evaluator that rejects synthetic/unadjudicated records and requires distinct reviewer IDs; no real gold dataset or clinical metrics are fabricated.

### Verification
- `python -m py_compile *.py tests/*.py` — passed.
- `python -m unittest discover -s tests -q` — 62 offline tests passed.
- SQLite schema v4 initialization, `PRAGMA integrity_check`, and `PRAGMA foreign_key_check` — passed.
- CLI help smoke tests for V5 harvest and evaluation/token tools — passed.

### External gates still open
- Real clinician-adjudicated gold dataset and performance report.
- Institutional SSO/MFA/reviewer directory, managed signing-key storage/rotation, and external immutable audit.
- Crossmark adapter and live Europe PMC/Crossref verification.
- Clinical governance approval of the shipped guideline allow-list (disabled by default).
- Production corpus, legal/licence review, and real Anki collection update/history tests.

The V5 engineering scope is implemented, but these external/human requirements prevent claiming a validated clinical-safety release.
