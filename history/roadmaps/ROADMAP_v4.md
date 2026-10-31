# Version 4.0 — implemented release scope

This file records the V4 scope delivered in the project. For proposed future work, see `ROADMAP_v5.md`.

- Best-effort Europe PMC core metadata retrieval and MeSH descriptor relevance scoring.
- Rule-based diagnosis assertion attributes: certainty, temporality, and experiencer.
- Sentence-level evidence span stored in the quality report and shown in the card reading panel.
- Article-discussed differential / diagnostic-pitfall cards, with `--no-differential` opt-out.
- Converter deduplication by case ID, normalized DOI, and normalized exact title.
- Source XML SHA-256 and parser/harvester version provenance.
- Cached article re-filtering, optional MeSH enrichment for older cache entries, and repair of missing plain-text copies.
- Dotted output basenames preserved when generating TSV and APKG files.

## Release limitations

- MeSH terms may be absent for some articles; the extra metadata request is best-effort.
- Assertion detection is heuristic and is not a clinical NLP model. It can miss complex scope, coreference, and multi-sentence context.
- The exact diagnosis sentence is traceable, but not every derived phrase in every card has a separately annotated character-offset evidence span yet.
- Retraction/correction screening, guideline sourcing, clinician adjudication, and review-history analytics are not included in V4; see V5 roadmap.
- The offline test suite uses synthetic JATS and mocked responses.