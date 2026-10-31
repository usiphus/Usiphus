# Pull request: V5 engineering implementation and 8.2.0 foundations

## Summary

- Implement V5 engineering workflows across harvesting, claim-level evidence, reviewer governance, guideline ingestion, evaluation, and cache verification.
- Reconcile the V4–V8 roadmap status against inspectable code and tests; document clinical/institutional release gates explicitly.

- Add optional Europe PMC `status-update-search` and Crossref `update-to` screening with TTL caching and explicit `unknown` / `conflict` outcomes.
- Exclude explicitly retracted articles from ordinary card exports; require an explicit override for historical use and attach a warning to every overridden card.
- Bound HTTP response reads and support numeric and HTTP-date `Retry-After` delays.
- Add persistent resumable harvest checkpoints, per-host rate limiting, target-saved budgets, and cache verification.
- Add signed role-scoped reviewer tokens, hash-bound append-only approvals, claim-level support assessments, and two-reviewer gates for high-risk claims.
- Add separate allow-list-gated guideline ingestion and a gold-set metrics engine that rejects synthetic/unadjudicated labels.
- Add SQLite schema v4, idempotent JATS import, source-hash verification, FTS5 search, identity conflicts, and review queue exports.
- Preserve the previous release archives and unclassified legacy material without inventing V1/V2 labels.
- Add CI for Python 3.10–3.13.

## Verification

- `python -m py_compile specialty_harvest.py cases2anki.py source_integrity.py case_database.py tests/*.py`
- `python -m unittest discover -s tests -v` — 62 tests passed.
- SQLite CLI smoke test: initialize, import, FTS search, manifest export, and integrity check passed.

## Safety and scope notes

This is a tested V5 engineering implementation, not a clinical-safety certification. No real clinician-adjudicated gold set, institutional SSO/MFA/key vault, Crossmark adapter, governance-approved guideline allow-list, live Europe PMC/Crossref validation, image download, visual-card generation, or real Anki collection update test is included. The shipped allow-list is disabled by default. HMAC tokens require institution-controlled provisioning and secret management.
