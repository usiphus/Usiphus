# Changelog

## 8.2.0 — 2026-10-30

### Added
- Persistent SQLite harvest state machine and resumable candidate checkpoint workflow.
- Separate `--target-saved` and hard `--max-candidates` controls, plus full cache verification.
- Per-host rate limiting and shared bounded Retry-After/backoff logic.
- HMAC-signed, expiring, role-scoped reviewer tokens; approvals are bound to current content/evidence hashes.
- Claim-level authenticated support assessments; evidence links alone no longer count as verified support; high-risk claims require two distinct reviewers.
- Separate allow-list-gated guideline ingestion and explicit governance approval fields.
- Gold-set evaluation runner with per-specialty/card-type precision, recall, F1, evidence exactness, unsupported-claim rate, denominators and error examples.
- SQLite schema migration v4 (full-card-content approval hashes) and expanded offline tests.

### Safety boundaries
- No synthetic clinical gold data or fabricated clinical metrics are shipped.
- The guideline allow-list is intentionally unapproved by default.
- HMAC tokens require institutional provisioning and secret management; no SSO/IdP is included.
- Engineering completion does not certify clinical safety; live provider checks and clinician adjudication remain required.

## 8.1.0 — 2026-10-30

### Added
- Optional Europe PMC status-update-search and Crossref update-to screening with TTL cache.
- Conservative status normalization; unknown and provider conflicts remain explicit.
- Default exclusion of confirmed retractions from card export; explicit warned override for historical study.
- Bounded HTTP response reads and numeric/HTTP-date Retry-After handling.
- SQLite schema v1 with normalized article, section, evidence, concept, event, asset, card, review, identity-conflict, and separate guideline tables.
- Idempotent case-folder imports, FTS5 search, source hash validation, nested JATS sections, media inventory indexing, and JSONL manifest export.
- Review queue import with exact section-offset validation and local append-only hash-bound review decisions.
- Expanded offline regression suite.

### Limitations at 8.1.0 (superseded by 8.2.0 engineering work)
- No clinician-adjudicated gold dataset or clinical performance metrics.
- Reviewer identity was local/unverified; guideline ingestion and durable harvest checkpoints were not yet implemented.
- No live API verification or real Anki collection import was performed for that release.
- V8 visual-card generation, OCR, image download, and image-occlusion generation remain unimplemented.
