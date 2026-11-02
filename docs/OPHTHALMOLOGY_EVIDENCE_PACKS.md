# Ophthalmology topic evidence packs

This file documents the first populated discovery layer for
`examples/ophthalmology/curriculum.json`.

## Coverage

- 52 topics across 16 curriculum domains.
- 195 topic-mapped source records.
- Every topic has a topic-specific PubMed query and an AAO guideline discovery
  link, plus relevant official guideline collections, government health
  information or university teaching directories where applicable.
- Each record carries a stable ID, source family, evidence role, topic mapping,
  objective mapping where relevant, access/reuse status and retrieval date.

## What is and is not included

The current pack is a **curated discovery and source-selection layer**, not a
faculty-approved evidence library. Search URLs are labelled
`search_link_only`; they are not represented as verified articles. Collection
pages are `link_only` until a reviewer selects the exact guideline or resource
and verifies its current version and applicability.

No article abstract or full-text excerpt has been fabricated, copied, or
silently treated as evidence. In particular, the `reference_sources` records
currently contain no `excerpt` field. The evidence generator therefore
correctly treats these as bibliography/discovery metadata, not as usable
evidence excerpts. `--require-evidence` remains blocked until permitted,
source-faithful excerpts are added.

## Source hierarchy and use

1. **Clinical recommendations:** select the current AAO Preferred Practice
   Pattern or another relevant national/international society guideline.
   Record its version, date, jurisdiction and supersession status. The AAO
   states that PPP recommendations are reviewed annually and are valid for
   five years unless superseded.
2. **Research evidence:** use the topic-specific PubMed search to select
   systematic reviews, randomized trials, diagnostic-accuracy studies and
   relevant observational evidence. Record PMID/DOI and article-level
   publication date. Appraise study design and limitations; search ranking is
   not an evidence grade.
3. **Foundational learning:** use the university teaching directory and
   government eye-health resources for anatomy, examination and learner-facing
   explanations. Verify the destination and its reuse terms.
4. **Exam practice:** Anki, DoctorDE and public exam-preparation sites may be
   added as separate `exam_practice_signal` records after a human verifies
   that the resource exists. They must not independently establish clinical
   recommendations.

Useful source collections already linked in the curriculum include:

- AAO Preferred Practice Pattern guideline browser:
  https://www.aao.org/education/guidelines-browse
- AAO 2025 Summary Benchmarks:
  https://www.aao.org/education/summary-benchmark-detail/summary-benchmarks-full-set-2020
- AAO Cornea/External Disease benchmarks:
  https://www.aao.org/education/summary-benchmark-detail/cornea-external-disease-summary-benchmarks-2020
- AAO Retina benchmarks:
  https://www.aao.org/education/summary-benchmark-detail/retina-summary-benchmarks-2020
- AAO Pediatric Ophthalmology/Strabismus benchmarks:
  https://www.aao.org/education/summary-benchmark-detail/pediatric-ophthalmology-strabismus-summary-benchma
- National Eye Institute condition directory:
  https://www.nei.nih.gov/eye-health-information/eye-conditions-and-diseases
- Duke Ophthalmology education resource library:
  https://dukeeyecenter.duke.edu/education-and-training/medical-student-education/resources-medical-students/library-ophthalmology

## Faculty completion workflow

For each topic:

1. Open the PubMed query and select a small, defensible set of papers that
   directly address its learning objectives.
2. Open the AAO/guideline discovery link and identify the current applicable
   guideline. Do not assume a generic collection page is the right guideline.
3. Replace broad search pointers with canonical article/guideline URLs and
   bibliographic metadata (authors/organization, title, source type, dates,
   DOI/PMID, jurisdiction, article type).
4. Map each item to the exact objective IDs it supports. Remove mappings that
   are merely topical rather than directly relevant.
5. Verify full-text access and article-level reuse licence. Public access,
   PubMed indexing or an open-access flag does not by itself grant permission
   to store or redistribute text.
6. Add only a short, rights-cleared excerpt or a clearly labelled
   faculty-authored paraphrase to `excerpt`. Keep the original source URL and
   enough metadata for a reviewer to verify it.
7. Set a review status only after a named qualified reviewer has checked
   accuracy, methodology, currency, conflicts, jurisdiction and local
   applicability. The repository currently leaves all records unreviewed.
8. Re-run strict evidence preflight and inspect the generated evidence review
   queue before any teaching or publication use.

## Clinical governance

- No source is approved by this commit.
- No generated card is clinically approved merely because a source ID resolves.
- Source-ID matching is provenance validation, not semantic entailment.
- Conflicting recommendations must remain visible for faculty adjudication.
- Local curriculum mapping (including Suez University requirements) and local
  policy review remain separate from this international source-discovery layer.
- Do not copy Anki decks, DoctorDE challenges, proprietary questions, images,
  or guideline text without permission.

## Strict generation

The curriculum records `evidence_pack.status` and
`evidence_pack_policy.strict_generation_gate`. At this stage the usable excerpt
count is zero, intentionally. After faculty curate permitted excerpts for
every topic, run:

```bash
python usiphus_auto_deck.py --generate \
  --curriculum examples/ophthalmology/curriculum.json \
  --require-evidence --plan-only
```

Only remove `--plan-only` after the strict preflight succeeds and the faculty
review process is ready.
