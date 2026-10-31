# Version 5.0 roadmap — source integrity, clinical validation, and evidence-locked learning

**Status:** strengthened implementation specification; the V5 features below are not represented as implemented unless listed in the V5 delivery record.  
**Depends on:** V4.0 baseline.  
**Primary objective:** ensure that clinical cards are not only readable, but traceable, current in source status, and evaluated against clinician-labelled examples.

## 1. Product principles and non-goals

1. A case report is evidence about an individual case, not a guideline or universal treatment recommendation.
2. Every clinical answer must be traceable to the article or to a separately stored, dated external source.
3. Retractions, corrections, expressions of concern, and article versions must be visible before cards are trusted.
4. Automation may prioritize review; it must not silently certify clinical correctness.
5. Unknown licence terms mean no image redistribution or automatic media embedding.
6. Preserve original article wording alongside normalized concepts; never replace the source statement with a model-generated interpretation.

**Non-goals:** autonomous diagnosis, treatment recommendations for real patients, unreviewed guideline synthesis, and redistribution of copyrighted full text or figures.

## 2. Release architecture

```text
Europe PMC candidates
  -> stable identity resolution (PMCID / PMID / DOI / title)
  -> article-status and update checks
  -> JATS parse + source hash
  -> specialty / assertion / diagnosis extraction
  -> evidence-span and claim coverage validation
  -> card generation (case facts only)
  -> clinician review queue + decision history
  -> quality gates
  -> TSV / APKG / preview / audit manifest
```

Keep V4's CLI compatible. New behaviour must be opt-in until validated, except safety protections such as excluding confirmed retractions by default once authoritative status checking is implemented.

## 3. Prioritized work packages

### P0 — source integrity and clinical safety (release blockers)

#### 3.1 Retraction, correction, and expression-of-concern screening
- Query the Europe PMC Article Status service for PMCID, PMID, and DOI in batches; record endpoint, response, checked-at timestamp, status, and update relationships.
- Supplement with Crossref update metadata/Crossmark where identifiers permit; preserve conflicting results rather than guessing which source is correct.
- Normalize status into `current`, `corrected`, `retracted`, `expression_of_concern`, `unknown`, and `conflict` while retaining raw provider values.
- Exclude confirmed retractions by default. An explicit educational override must add a prominent warning to every affected card and export manifest.
- Treat timeouts, missing records, malformed responses, and rate limits as `unknown`, never as `current`.
- Cache status checks with a configurable TTL and provide a force-refresh option.

**Acceptance:** fixture tests cover every status, provider conflict, missing identifier, timeout, malformed JSON, and stale cache. A retracted article cannot enter a normal export.

#### 3.2 Clinician-reviewed validation set
- Create a versioned, de-identified fixture set across at least five specialties, adult and paediatric cases, common and rare diagnoses, and multiple JATS structures.
- Annotate final diagnosis, evidence sentence(s), negation, certainty, temporality, experiencer, specialty, and whether each card answer is supported.
- Require two independent reviewers for the high-risk subset and adjudication of disagreements.
- Report per-specialty and per-card-type precision, recall, F1, evidence-span exactness, and unsupported-claim rate; publish denominators and failure examples.
- Do not use synthetic fixtures as clinical validation data.

**Acceptance:** a reproducible evaluation command emits machine-readable metrics, dataset version, reviewer protocol, and error examples. No aggregate-only score.

#### 3.3 Claim-level evidence contracts
- Represent each clinically meaningful answer claim as a separate item linked to one or more evidence spans.
- Store section ID/title, section-local offsets, exact quote, source XML SHA-256, and evidence ID.
- Validate offsets against the exact source section; stale or mismatched spans fail closed.
- Distinguish direct evidence, author interpretation, title inference, OCR, and external guideline evidence.
- Do not mark a card `evidence_covered` when only part of a multi-claim answer is supported.

**Acceptance:** deliberately altered offsets, missing spans, source-hash changes, and unsupported answer clauses are detected in tests.

#### 3.4 Human review governance
- Keep `needs_review`, `approved`, `needs_changes`, and `rejected` as explicit states.
- Record reviewer ID, role, timestamp, decision, notes, card content hash, and evidence hash in append-only history.
- Prevent the same person from silently approving their own generated content where institutional governance requires separation.
- The existing local V7 JSONL import is a convenience workflow, not authenticated identity or a tamper-proof audit log.

**Acceptance:** approved-only export requires a current approval for the exact card/evidence hash; edits invalidate approval.

#### 3.5 Guideline source separation
- Guideline cards must be stored separately from case-report cards.
- Record publisher, guideline title, stable URL, publication date, last-updated date, recommendation wording, strength/grade, access date, and version.
- Require a maintained authoritative source allow-list and human review for high-impact recommendations.
- Never infer a guideline from case chronology or an author's discussion sentence.

**Acceptance:** every guideline claim has a dated source and is visually labelled as guideline-derived; case-derived management remains labelled as reported management.

### P1 — reliability, identity, and parser quality

#### 3.6 Robust HTTP client
- Parse numeric and HTTP-date `Retry-After`; apply bounded exponential backoff with jitter to 429 and transient 5xx responses.
- Enforce connect/read timeouts, maximum response bytes, retry budgets, and a shared per-host rate limiter.
- Emit structured errors with endpoint, status code, retryability, attempt, and timestamp; do not log secrets.

**Acceptance:** mocked tests cover numeric/date Retry-After, timeout, oversized payload, 429, 5xx, and permanent 4xx.

#### 3.7 Resumable harvesting and cache verification
- Persist candidate states: `discovered`, `downloaded`, `parsed`, `retained`, `rejected`, `failed`.
- Resume successful records without repeating downloads; retry only retryable failures.
- Add `--target-saved` separately from legacy `--limit` (candidate limit), with a hard maximum-candidate budget.
- Add `--verify-cache` to validate metadata schema, XML hash, required files, and manifest consistency.

**Acceptance:** interrupted and resumed fixture runs produce the same retained set as a single run; corrupted cache entries are reported and repaired or rejected deterministically.

#### 3.8 Identity resolution and parser robustness
- Normalize PMCID, PMID, DOI, title, preprint/version-of-record links, and correction relationships.
- Never merge solely on similar titles; retain all identity evidence and conflict flags.
- Add nested sections, mixed content, tables, figures, supplementary files, Unicode, formulas, malformed XML, and hostile-input fixtures.

**Acceptance:** no cross-case merge in the adjudicated duplicate fixture set; parser failures are bounded and reported.

#### 3.9 Assertion and specialty evaluation
- Evaluate negation, uncertainty, temporality, experiencer, and family history independently.
- Preserve rule-based output as baseline; any model-based classifier must expose model/version/confidence and be benchmarked before default activation.
- Report specialty false positives and classifier explanation; do not equate a specialty keyword with relevance.

**Acceptance:** per-attribute and per-specialty evaluation meets predeclared thresholds agreed with clinical reviewers; otherwise retain `needs_review`.

### P2 — usability, reproducibility, and study tools

#### 3.10 Quality gates
- Duplicate-front and near-duplicate detection; answer-leak checks; malformed HTML and broken-link checks.
- Card readability/length limits, tag validation, per-article card caps, and source-attribution checks.
- Report each rejection reason and preserve deterministic output ordering.

#### 3.11 Incremental reprocessing
- Store parser, schema, and generator versions plus source hash.
- Reprocess only when the source or relevant processing version changes.
- Provide dry-run diff of new, changed, unchanged, and removed cards.

#### 3.12 Optional study analytics
- Import Anki review history only with explicit user consent; keep it local.
- Summarize weak topics and specialties without collecting free-text answers.
- Leave scheduling and FSRS state entirely to Anki.

## 4. Data contracts

Minimum metadata fields:

```json
{
  "schema_version": 5,
  "article_id": {"pmcid": "PMC...", "pmid": "", "doi": ""},
  "source_xml_sha256": "...",
  "article_status": {"status": "unknown", "checked_at": null, "sources": []},
  "license": {"raw": "", "reuse_decision": "unknown"},
  "processing": {"harvester_version": "5.0.0", "parser_version": "5.0.0"}
}
```

Minimum card evidence fields:

```json
{
  "card_id": "stable-id",
  "claims": [{"text": "...", "evidence_ids": ["span-id"], "claim_type": "case_reported"}],
  "review_status": "needs_review",
  "source_xml_sha256": "..."
}
```

These examples are target contracts, not assertions that V5 shipped them. Schema migrations must be explicit and tested; unknown future schema versions must not be silently accepted.

## 5. Delivery sequence and dependencies

| Milestone | Deliverable | Depends on | Exit gate |
|---|---|---|---|
| M0 | schemas, migration plan, golden fixtures | V4 baseline | backward compatibility tests pass |
| M1 | article status service + cache | M0 | retracted exclusion tests pass |
| M2 | clinician-labelled gold set + metrics | M0 | per-specialty report reviewed |
| M3 | claim-level evidence and source hashes | M0 | unsupported claims fail closed |
| M4 | authenticated review/audit model | M2, M3 | approval binds to exact content hash |
| M5 | separate guideline source layer | M1, M2, M3 | all recommendations traceable and dated |
| M6 | resumable HTTP/cache pipeline | M0 | deterministic resume tests pass |
| M7 | quality gates + release candidate | M1–M6 | all P0 gates pass |

Recommended implementation order: M0 → M1/M6 in parallel → M2/M3 → M4/M5 → M7. P0 items cannot be waived by a high aggregate test count.

## 6. V5 release checklist

- [ ] Retraction/correction status checked and visible; unknown is not treated as current.
- [ ] Confirmed retractions excluded by default.
- [ ] Clinician-labelled evaluation dataset and per-specialty metrics published.
- [ ] Every answer claim maps to exact, hash-bound evidence.
- [ ] Reviewer approval is authenticated, timestamped, append-only, and bound to content hash.
- [ ] Guideline claims use a separate dated authoritative source layer.
- [ ] HTTP retries, response bounds, cache validation, and resumability pass fault-injection tests.
- [ ] Quality, licence, and attribution gates pass.
- [ ] README, migration notes, changelog, and clean-install instructions match shipped behaviour.

## 7. Known boundaries

V5 is not complete merely because V7 has evidence spans and a local review-decision import. Current evidence spans are sentence-level, reviewer labels are user-supplied, and no authenticated audit system or clinician gold set is included in the present local project. Article status checks and guideline retrieval are also not implemented in this codebase.

## 8. Technical references

- Europe PMC REST API: https://dev.europepmc.org/RestfulWebService
- Europe PMC Article Status Monitor: https://dev.europepmc.org/ArticleStatusMonitor
- Europe PMC developer resources: https://dev.europepmc.org/developers
- Clinical assertion detection attributes: https://pmc.ncbi.nlm.nih.gov/articles/PMC11908446/
- Crossref versioning and update relationships: https://www.crossref.org/documentation/principles-practices/best-practices/versioning/
- Crossmark: https://www.crossref.org/crossmark/
- Crossref REST API: https://www.crossref.org/documentation/retrieve-metadata/rest-api/
