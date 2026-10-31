# SQLite schema v4

`case_database.py` owns the schema and explicit migration markers (v1–v4). The database is a rebuildable local index; `article.xml` and `meta.json` remain the source artifacts.

| Table | Purpose |
|---|---|
| `schema_migrations` | Applied schema versions |
| `articles` | Stable article identity, metadata, source hash, article-status provenance |
| `sections` | Ordered, nested JATS section text and hashes |
| `evidence_spans` | Exact section-local quote offsets bound to the source XML hash |
| `concepts` | Optional original/normalized terminology mappings; not auto-populated yet |
| `clinical_events` | Structured case events; schema is present, extraction is not yet shipped |
| `assets` | Figure/table caption and reference inventory, with fail-closed reuse state |
| `cards` | Generated front/back/extra/tags, full-content hash, and evidence references |
| `card_claims` | Explicit answer clauses with evidence references, support state, and high-risk flag; evidence links alone do not imply support |
| `claim_assessments` | Append-only signed clinician assessments bound to claim and evidence hashes; high-risk claims require two distinct reviewers |
| `review_decisions` | Append-only HMAC-authenticated decisions bound to card and evidence/source hashes; legacy unsigned decisions are non-authoritative |
| `identity_conflicts` | Duplicate/conflicting PMID/DOI observations; records are not silently merged |
| `guideline_sources`, `guideline_claims` | Separate allow-list-gated dated guideline layer, distinct from case-reported management |
| `article_fts` | FTS5 index over title, abstract, and section text |

`PRAGMA foreign_keys=ON` is enabled for every connection. `case_database.py check` runs SQLite integrity and foreign-key checks. The append-only triggers prevent ordinary SQL updates/deletes of review history but are not a security boundary against a database owner replacing or rebuilding the file. HMAC token issuance requires an institution-managed secret; this is not SSO/MFA. Approval hashes include front, back, extra reading panel, and tags. Claim support is a human assessment, not an automated entailment proof.
