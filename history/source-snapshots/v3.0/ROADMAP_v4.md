# Version 4.0 ideas — proposed roadmap

Version 3.0 improves extraction safeguards and traceability, but remains a rule-based pipeline. Version 4.0 should focus on **clinical correctness, measurable quality, and efficient study**, rather than simply harvesting more articles.

## Highest-value improvements

1. **MeSH-aware specialty classification**
   - Retrieve richer Europe PMC `core` metadata or use its annotations to incorporate MeSH disease headings and text-mined disease terms.
   - Combine controlled vocabulary, synonyms, title/abstract evidence, and specialty-specific exclusion rules.
   - Store why each specialty was assigned and expose borderline/multi-specialty cases for review.
   - Europe PMC documents that `core` results include MeSH terms and that its Annotations API exposes text-mined disease and other terms: https://dev.europepmc.org/RestfulWebService

2. **Clinical assertion extraction (not just regex matching)**
   - Represent each candidate diagnosis as `{concept, assertion, certainty, temporality, experiencer, evidence_span, section}`.
   - Distinguish confirmed, suspected, ruled-out, historical, family-history, and differential diagnoses.
   - Use a clinical NLP model only as a proposal generator; retain deterministic safeguards and human review for uncertain cases.
   - Benchmark against a manually labelled set before allowing automated diagnosis cards.

3. **Evidence-grounded card generation**
   - Require every answer fact to link to one or more exact source spans and section headings.
   - Add a “show evidence” panel and optionally export source sentence IDs in TSV/APKG fields.
   - Reject cards when the answer is not directly supported by the source or when the front leaks the answer.

4. **Clinical card types with stronger educational structure**
   - Differential diagnosis with discriminating clues (not a bare list).
   - Investigation interpretation (result → meaning) and next-best-step cards.
   - Complication, prognosis, and follow-up cards.
   - Short oral-exam / viva prompts and structured case summaries.
   - Keep “what this paper did” separate from “what current guidelines recommend”; guideline cards need a separate, dated, authoritative source layer.

5. **Real-world evaluation suite**
   - Create a diverse, manually adjudicated dataset of JATS case reports across specialties.
   - Measure diagnosis precision/recall, assertion/negation accuracy, specialty precision, section classification, card factuality, answer leakage, and duplicate rates.
   - Add regression cases for multiple diagnoses, pathology-only diagnoses, negated differentials, paediatric age expressions, tables/figures, and non-English or malformed markup.
   - Add optional integration tests against Europe PMC, clearly separated from offline tests.

## Useful workflow improvements

6. **Target number of accepted cases** — add `--target-saved` with a bounded candidate refill strategy, so the user can ask for 30 retained cases rather than 30 search results.
7. **Multi-source deduplication** — normalize DOI, PMID, PMCID, title fingerprints, preprint/article relationships, and corrections/retractions.
8. **Retraction and correction screening** — check article status before generating cards; label expressions of concern, corrections, and retractions prominently.
9. **Licence-aware exports** — distinguish metadata-only, fact-derived cards, and copied article text; make full-text inclusion configurable and honour licence-specific redistribution restrictions.
10. **Incremental refresh and provenance** — store source API version, retrieval timestamp, content hash, parser version, and extraction version; reprocess changed parser logic without redownloading unchanged XML.
11. **Study analytics** — optionally ingest Anki review logs and report weak topics/card types. Do not alter Anki scheduling itself; let Anki's FSRS scheduler manage intervals and desired retention.
12. **Better deck ergonomics** — cloze cards for key findings, image/figure cards only where rights and captions allow, sibling-card burying guidance, specialty tags, and configurable card limits per article.

## Suggested release gates

- No automatic high-confidence diagnosis card unless assertion status is confirmed and evidence spans are present.
- A published validation report with per-specialty metrics and representative failure cases.
- Zero known answer leaks in the regression suite.
- All generated management statements visibly labelled as case-reported unless independently sourced from a current guideline.
- Licence-aware export tests and a documented policy for article text reuse.

## Relevant technical references

- Europe PMC REST API and rich metadata / annotations: https://dev.europepmc.org/RestfulWebService
- Anki manual — FSRS and desired retention: https://docs.ankiweb.net/deck-options.html
- Anki FSRS overview: https://faqs.ankiweb.net/what-spaced-repetition-algorithm.html

FSRS is already Anki's own scheduler option. Version 4.0 should integrate cleanly with Anki rather than inventing a competing scheduling algorithm. Anki's documentation explains that desired retention trades review workload against recall probability; the deck-generation script should avoid prescribing a universal retention value.

Additional references for the V4 design:

- NLM explains that MeSH entry terms provide synonyms and near-synonyms for descriptors: https://www.nlm.nih.gov/mesh/intro_entry.html
- NLM MeSH RDF offers descriptor lookup, entry-term lookup, and a SPARQL interface: https://id.nlm.nih.gov/mesh/
- Clinical NLP assertion work identifies certainty, temporality, and experiencer as distinct attributes when extracting medical concepts: https://pubmed.ncbi.nlm.nih.gov/40092287/
