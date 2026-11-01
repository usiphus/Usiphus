# V18 — Evidence Lifecycle Operations

V18 adds a local-first operational layer over the V15 source/claim registry and V17 curriculum/assessment links. It detects metadata/content changes, identifies canonical-source candidates, prioritizes faculty review, and traces evidence impact through learning artifacts. It does not fetch URLs by default and does not make clinical decisions.

## Quick start

    python usiphus_v18.py --self-test
    python usiphus_v18.py --sources examples/v18/sources.json --claims examples/v18/claims.json --cards examples/v18/cards.json --assessments examples/v18/assessments.json --osce examples/v18/osce.json --release-links examples/v18/releases.json --as-of 2026-11-01T00:00:00Z --output-dir build-v18
    python usiphus_v18.py --sources examples/v18/sources.json --claims examples/v18/claims.json --previous-state build-v18/v18_state.json --output-dir build-v18-next

## Inputs

- `--sources`: V15-style source registry. V18 reads lifecycle status and metadata but never changes them.
- `--claims`: V15-style claim registry with source references and card IDs. Legacy free-text references are surfaced for manual registration.
- `--cards`, `--assessments`, `--osce`: V17-compatible explicit links used to trace downstream impact.
- `--previous-state`: prior `v18_state.json` used for deterministic version, status, URL, metadata and content-hash comparisons.
- `--release-links`: optional JSON containing `releases` with `release_id` and explicit `artifact_ids`; links graph nodes to releases.
- `--decisions`: optional JSON containing explicit faculty decision records. Decisions require target type/ID, accepted/rejected/deferred, reviewer label, rationale and timezone-aware timestamp. Labels are not authenticated identities.
- `--as-of`: timezone-aware timestamp for reproducible review-age calculations.

A source may include a canonical identifier (for example, a DOI) and a relative `content_path`. V18 resolves it beneath the source registry's directory, rejects path traversal, and hashes the local file. Alternatively, provide a SHA-256 hash from a controlled intake process. V18 never downloads URLs or follows redirects automatically. Retrieval time, URL, publisher, jurisdiction, version and local content hash are provenance metadata; they do not establish source authority or clinical currentness.

## Outputs

- `v18_evidence_lifecycle_report.json/.md`: summary, findings, changes, decisions and policy boundaries.
- `v18_review_queue.json/.csv`: prioritized review candidates with affected artifacts and rationale.
- `v18_source_changes.json`: changed fields and duplicate/canonical-source candidates.
- `v18_source_impact_graph.json`: source → claim → card → competency → assessment/OSCE → release links where explicit artifact IDs are supplied.
- `v18_state.json`: current source fingerprints for the next comparison.
- `v18_decision_audit.json`: hash-linked decision event sequence.
- `v18_manifest.json`: SHA-256 hashes of exact inputs and generated outputs.

Priority is a transparent workflow triage score: fixed event weights plus a capped affected-artifact count. It is not a clinical risk or source-quality score. Duplicate URL/hash matches are candidates only and are never merged automatically.

## Review lifecycle

V18 queues withdrawn/superseded sources, changed versions/content/metadata, stale/missing/future review dates, jurisdiction mismatches, and downstream impacts. Optional release links extend impact tracing to published releases. Qualified reviewers must interpret what a source change means, determine whether claims and learning artifacts remain appropriate, and explicitly accept, reject or defer review candidates. V18 records supplied decisions but does not alter claim text, source status, mappings, assessment items, OSCE stations or releases.

A decision hash chain is useful for detecting accidental edits when recomputed. Without signatures and an independently stored anchor it is not tamper-proof. Reviewer labels are not authenticated identities. Institutions remain responsible for access control, retention, backup, key management and identity verification.

## Compatibility and boundaries

- V15 registry and claim files remain unchanged and usable by V15.
- V17 curriculum and assessment files are read-only inputs.
- Python standard library only; no hosted service or paid API.
- Offline operation is the default. URL retrieval is deliberately not automatic.
- Metadata checks do not determine guideline authority, clinical correctness, applicability, or educational validity.
- A changed source does not imply that every linked claim is wrong; faculty review is required.
