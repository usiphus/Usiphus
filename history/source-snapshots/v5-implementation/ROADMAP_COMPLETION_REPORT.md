# V4–V8 roadmap completion and release-gate report

**Audit date:** 2026-10-30
**Workspace:** local `8.2.0` implementation
**Overall status:** core educational pipeline foundations are implemented, but the complete V4–V8 production release is **not** complete. Items requiring external clinical governance, licensing decisions, credentials, or an Anki runtime remain explicitly blocked.

## Cross-version status

| Version | Delivered in this workspace | Still open / release gate |
|---|---|---|
| V4 | MeSH enrichment, heuristic assertion attributes, diagnosis evidence sentence, differential/pitfall cards, deduplication, provenance, cache re-filtering/repair, dotted output basenames. | Heuristic extraction limitations; no V5 status screening or clinician validation in the original V4 release. |
| V5 | Europe PMC/Crossref status screening; explicit unknown/conflict; TTL cache; retraction gate; bounded HTTP/retry/rate-limit; persistent harvest checkpoints/resume; target-saved and candidate budget; cache verifier; signed reviewer tokens; append-only hash-bound card decisions; separate answer claims and clinician assessments (double review for high-risk claims); guideline ingestion gate; per-specialty gold-set evaluator. | Real clinician-adjudicated multi-specialty gold set and metrics; institutional SSO/MFA/key vault and external immutable audit; Crossmark; live provider validation; governance approval of guideline allow-list; clinician adjudication of every semantic claim. |
| V6 | SQLite schema v4; idempotent JATS import; source XML hash verification; nested sections; FTS5; identity-conflict reporting; asset metadata; review-queue import; hash-bound local review history; JSONL manifest. | Populate concepts/events from reviewed annotations; deterministic full rebuild and dry-run diff; migration path beyond v4; larger real-world corpus validation; authenticated governance. |
| V7 | Stable card/evidence IDs; answer-claim declarations; sentence evidence spans; signed review queue/audit workflow; per-claim clinician assessment; high-risk double review; approved-only export verifies current card/evidence/claim state. | Institutional SSO/MFA and external immutable audit; real clinician-labelled metrics; expanded fuzz/property/performance tests; actual Anki import/update compatibility and history-preservation tests. |
| V8 | Deterministic JATS figure/table metadata inventory; source XML hash and source URL; graphic references/captions/licence metadata; fail-closed reuse state; no implicit downloads or embedding. | Human licence decision workflow; approved asset download and byte-level MIME/size/dimension/checksum validation; asset deduplication and safe processing; image-caption review; image occlusion/visual card generators; visual review UX; stable note GUIDs and dry-run conflict-safe Anki updates. |

## Safety rules retained

- A case report is not a guideline or universal recommendation.
- An unknown article status is not treated as current. Confirmed retractions are excluded unless the explicit historical-study override is used, in which case generated cards carry a warning.
- Unknown or unreviewed asset reuse rights block automatic embedding and redistribution.
- A model score, user-supplied reviewer label, or successful unit test is not clinician approval.
- Existing Anki scheduling/review history must not be overwritten; safe update semantics are not claimed until integration-tested.

## Verification actually performed

- `python -m unittest discover -s tests -q`: **62 tests passed** (offline fixtures and mocked provider responses).
- `python -m py_compile specialty_harvest.py cases2anki.py source_integrity.py case_database.py v5_release.py tests/*.py`: passed.
- SQLite CLI smoke test: database initialization, import, FTS search, manifest export, `PRAGMA integrity_check`, and `PRAGMA foreign_key_check` passed.
- ZIP integrity and patch applicability against the original README baseline were checked.

## Not performed (do not infer completion)

- No live Europe PMC or Crossref API verification in this test run.
- No real clinician review, adjudication, gold-standard dataset, or clinical performance metrics; the evaluation engine is implemented but has no real labelled input.
- No institutional SSO/MFA, reviewer directory, key vault, external immutable audit, or formal separation-of-duties provisioning.
- No legal/licence adjudication or permission to redistribute any figure.
- No real Anki collection import, update, or review-history preservation test.
- No production-scale corpus, cross-platform, fuzz, or performance qualification.

## Release decision

This workspace is suitable as a tested development foundation and for carefully supervised educational experimentation. It must **not** be described as a fully validated clinical product or a complete visual-card release. Close the external gates above before making those claims.
