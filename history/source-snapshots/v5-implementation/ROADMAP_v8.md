# Version 8.0 roadmap — evidence-linked, media-aware clinical case flashcards

**Status:** V8.0 foundation implemented locally; not all proposed visual-card and Anki-update features are complete. V8 is the final planned roadmap stage for the current project sequence, not a claim that the software will never need maintenance.

## 1. Goals and safety boundaries

- Preserve JATS structure: sections, figures, tables, captions, and media references.
- Build deterministic media inventories tied to source XML hashes.
- Never download, transform, embed, or redistribute an image solely because JATS references it.
- Unknown or ambiguous reuse rights block automatic embedding; manual licence review is required.
- Do not infer radiology/pathology findings from captions alone.
- Keep case-derived facts separate from guideline-derived claims.
- Maintain stable card identity and never overwrite Anki scheduling/review history.

Non-goals: autonomous diagnosis, patient-specific image interpretation, fabricated patient images, and unlicensed redistribution.

## 2. Architecture

```text
Europe PMC harvest
  -> source XML + metadata + SHA-256
  -> JATS parser
       |-> sections / evidence spans
       |-> figure + table inventory (V8 implemented)
       |-> graphic references (V8 implemented)
  -> licence and provenance review (manual gate)
  -> asset download / validation / OCR (planned)
  -> visual card generation (planned)
  -> quality and human review (V7 foundation; governance enhancements planned)
  -> TSV / APKG / preview (existing text-card export)
  -> stable Anki update/dry-run workflow (planned)
```

## 3. Implemented V8 scope

### 3.1 Version and metadata
- Harvester and converter version markers are `8.2.0`.
- New harvested metadata uses schema version 5.
- V7 card/evidence IDs and review controls remain in place.

### 3.2 Structured JATS media inventory
- `--asset-report` scans JATS `fig` and `table-wrap` elements.
- JSONL records deterministic `asset_id`, case ID, asset type, label, caption, graphic references, source URL, source XML SHA-256, and raw licence string.
- Every record starts with `downloaded=false` and `embedded=false`.
- Missing licence is `blocked_unknown_license`; present licence is `manual_review_required`. Neither grants automatic permission.
- Inventory is metadata-only: no image fetching, no image interpretation, and no visual-card creation.

### 3.3 Verification
- Regression test covers figure/table extraction, deterministic IDs, and fail-closed unknown-licence behaviour.
- Existing V7 evidence/review tests remain in the suite.

## 4. Remaining V8 work — not implemented

### P0 — clinical and licensing safety
- [x] Add optional Europe PMC/Crossref status screening and default exclusion of explicitly retracted articles; this remains best-effort, not a complete authoritative guarantee.
- [ ] Implement authenticated review governance. SQLite now records local append-only decisions bound to card/evidence/source hashes, but reviewer identity depends on institution-managed HMAC token issuance; no SSO/MFA or external immutable audit provider is integrated.
- [ ] Complete semantic claim-level evidence coverage and add clinician-adjudicated evaluation data. Current DB stores evidence spans as source-backed claim records but does not validate every answer clause.
- [ ] Add explicit asset licence decision workflow and provenance for downloaded media.
- [ ] Require human review for image-caption association and interpretations not explicit in the article.

### P1 — reliable harvesting and structured assets
- [ ] Add resumable candidate checkpoints and cache verification.
- [x] Parse HTTP-date and numeric `Retry-After`; enforce response-size ceilings for harvester/status responses. Structured run-level retry/error reporting remains open.
- [ ] Expand fixtures for nested JATS, supplementary assets, malformed XML, and unusual media references.
- [ ] Download assets only after licence approval; validate MIME from bytes, size, dimensions, and checksum.
- [ ] Add asset deduplication, safe thumbnail/crop processing, and multi-panel review.

### P2 — visual cards and Anki safety
- [ ] Implement image occlusion with normalized coordinates and text alternatives.
- [ ] Implement radiology/pathology/ECG cards only when source evidence and reuse rights permit.
- [ ] Add comparison/flowchart cards only from source-supported content or separately dated guidelines.
- [ ] Add stable note GUIDs, export manifest, dry-run update diff, and conflict protection.
- [ ] Test repeated imports, changed cards/media, removed sources, and preservation of Anki review history in a real test collection.

## 5. Current asset inventory contract

```json
{
  "schema_version": 1,
  "asset_id": "stable-id",
  "case_id": "PMC...",
  "asset_type": "figure",
  "label": "Figure 1",
  "caption": "Source caption",
  "graphic_refs": ["fig1.jpg"],
  "source_xml_sha256": "...",
  "license": "raw source licence",
  "reuse_decision": "manual_review_required",
  "downloaded": false,
  "embedded": false
}
```

Downloaded assets will additionally require byte-level SHA-256, detected MIME type, dimensions, source URL, attribution, reviewer decision, and processing history.

## 6. Full visual V8 release gates

- [ ] Every card claim maps to source text or a separately dated external source.
- [ ] Every image has verified provenance, licence decision, checksum, and attribution.
- [ ] Optional article retraction/correction checks are recorded and visible; provider coverage is not exhaustive.
- [ ] Image-caption matching, answer-leak, HTML, and rendering checks pass.
- [ ] Human-review approval is bound to exact content hash.
- [ ] Anki repeated-import/update tests preserve note identity and review history.
- [ ] Offline, clean-install, and documented live API smoke tests pass.
- [ ] Release manifest lists implemented features and known limitations accurately.

## 7. Final delivery order

1. Close V5 P0 source-status, clinical validation, and evidence gaps.
2. Implement V6 structured storage only if the file-based library becomes a demonstrated bottleneck.
3. Complete V8 asset licensing, download validation, and structured media processing.
4. Add visual card generators and manual-review UX.
5. Complete stable Anki update semantics and integration tests.
6. Release only after all gates are met or explicitly excluded from the declared release scope.
