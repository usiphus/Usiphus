# Clinical Case Report → Exam Flashcards (Version 7.0)

> Roadmap index: [ROADMAPS.md](ROADMAPS.md) · [V4 implemented scope](ROADMAP_v4.md) · [V5 proposal](ROADMAP_v5.md) · [V7 implementation and pre-V8 gates](ROADMAP_v7.md) · [V8 visual-first proposal](ROADMAP_v8.md) · [Code audit](BUG_AUDIT.md)

A two-script workflow for finding open-access clinical case reports in Europe PMC and converting their JATS full text into source-linked, exam-style Anki cards. The pipeline is extractive and heuristic: it is a study aid, not a clinical decision system.

## What's new in 7.0

- **Evidence-linked cards:** deterministic card/evidence IDs and exact source-section sentence spans with character offsets.
- **Evidence coverage checks:** title-inferred diagnoses without direct diagnosis evidence are not treated as evidence-locked.
- **Author-stated reasoning cards:** optional cards quote explicit reasoning from the discussion; they do not infer next-best steps or guidelines. Disable with `--no-reasoning`.
- **Human-review workflow:** `--review-jsonl` writes card content, source URLs, evidence spans, and pending review status; `--review-decisions decisions.jsonl` imports explicit decisions; `--approved-only` exports only approved cards. The system never auto-approves cards.
- **Strict evidence export:** `--strict-evidence` keeps only cards with a direct evidence span and source URL.
- **Auditable reports:** quality CSV includes review status/reasons and evidence coverage; export counts are synchronized after filtering and limits.

## What was introduced in 4.0

- **MeSH-aware specialty relevance:** the harvester makes a best-effort Europe PMC `core` metadata request and stores available MeSH descriptor terms. These add a capped relevance signal alongside title/abstract/full-text matches; they do not independently determine specialty.
- **Assertion-aware diagnosis extraction:** candidate diagnosis sentences are screened for common negation, uncertainty, historical, and family-member contexts. The quality report records certainty, temporality, experiencer, exact evidence sentence, and rejected candidates.
- **Evidence shown with diagnosis cards:** the extracted evidence sentence is displayed in the answer-side reading panel and included in the quality CSV.
- **Differential / pitfall cards:** generates cards only from sentences explicitly discussing alternatives or diagnostic pitfalls. Disable them with `--no-differential`. The card warns that article-discussed alternatives are not a complete validated differential.
- **Multi-key deduplication:** converter skips repeated PMCID/case IDs, normalized DOIs, and normalized exact titles within one deck-generation run.
- **Reproducibility metadata:** stores source XML SHA-256, harvester/parser version, MeSH terms, and retrieval timestamps in `meta.json`.
- **Cache re-filtering:** cached articles are checked against current word-count and specialty-relevance thresholds; missing plain-text copies are repaired. Older cache entries can be enriched with MeSH terms on the next run.
- **Year validation:** explicit year zero or out-of-range values no longer bypass validation.

## Install

Python 3.10 or newer is recommended.

```bash
python -m pip install -r requirements.txt
```

The harvester and TSV exporter use the Python standard library. `genanki` is required only for `.apkg` output.

## Example workflow

Harvest urology and cardiology case reports from 2018 onward:

```bash
python specialty_harvest.py --specialty urology cardiology --year-from 2018 --limit 40 --sort citations --output case_library
```

`--limit` means search candidates per specialty, not guaranteed retained reports. Full-text availability, word count, relevance, and educational filters can reduce the saved number.

Create Anki packages and an import TSV:

```bash
python cases2anki.py case_library --format both --output decks/clinical_cases --deck "Clinical Case Reports" --quality-report --preview
```

Create a review queue and export only evidence-linked cards:

```bash
python cases2anki.py case_library --format both --output decks/v7 --strict-evidence --quality-report --review-jsonl --preview
```

After reviewing the JSONL, prepare one decision per card (using its exact `card_id`) and export only explicitly approved cards:

```bash
python cases2anki.py case_library --format both --output decks/v7_approved --review-decisions decisions.jsonl --approved-only --strict-evidence
```

Each decision line must be JSON with `card_id`, `status` (`approved`, `needs-changes`, or `rejected`), and `reviewer`; `reviewed_at` and `notes` are optional. Example:

```json
{"card_id":"a1b2c3d4e5f6a7b8c9d0","status":"approved","reviewer":"reviewer-1","reviewed_at":"2026-10-30T12:00:00Z","notes":"Checked against the cited source."}
```

Reviewer identity is not authenticated by this local tool.

Create a urology TSV without differential or reasoning cards:

```bash
python cases2anki.py case_library/urology --specialty urology --format tsv --output decks/urology --no-differential --no-reasoning --quality-report
```

Create only a TSV without installing `genanki`:

```bash
python cases2anki.py case_library --format tsv --output decks/clinical_cases
```

Use `--skip-low-confidence-cases` to exclude cases whose diagnosis extraction confidence is below `--min-confidence`. Confidence is a rule-based review signal, **not a calibrated probability**.

## Outputs

Harvester output is organized by specialty and PMCID:

- `article.xml`: retrieved JATS source document.
- `article.txt`: plain-text reading copy.
- `meta.json`: bibliographic metadata, licence string, score breakdown, MeSH terms when available, source hash, parser version, and retrieval details.
- `manifest.jsonl`: cumulative index of retained cases.
- `harvest_errors.jsonl`: retrieval/search errors.

Converter output:

- `.tsv`: Anki-importable notes.
- `.apkg`: optional Anki package when `genanki` is installed.
- `_quality.csv`: diagnosis, assertion attributes, evidence coverage, review status/reasons, rejected candidates, card types, and source details.
- `_review.jsonl`: optional structured human-review queue with stable card IDs and exact evidence spans.
- `_preview.html`: local review preview when `--preview` is used.

## Supported specialties

`anesthesia`, `cardiology`, `cardiothoracic-surgery`, `dermatology`, `emergency-medicine`, `endocrinology`, `gastroenterology`, `general-surgery`, `hematology`, `infectious-disease`, `nephrology`, `neurology`, `neurosurgery`, `obstetrics-gynecology`, `oncology`, `ophthalmology`, `orthopedics`, `otolaryngology`, `pediatrics`, `psychiatry`, `pulmonology`, `radiology`, `rheumatology`, `urology`, `vascular-surgery`.

## Clinical and licensing safeguards

- Diagnosis extraction and assertion detection use regular expressions and local rules. They can miss complex negation, uncertainty, multiple diagnoses, pathology qualifiers, temporality, family attribution, or context spanning multiple sentences. Human review is mandatory before relying on a diagnosis card.
- MeSH availability varies by article and indexing status. The MeSH relevance score is only a supplementary signal and may be incomplete or too broad.
- Differential cards repeat only alternatives discussed by the authors; they are not a complete or validated differential diagnosis.
- Management cards describe what happened in one published case. They are **not** guideline recommendations and must not be generalized to patient care.
- The educational score is a transparent text-feature score, not a peer-review, evidence-quality, novelty, or clinical-importance rating.
- `OPEN_ACCESS:Y` and full-text availability do not by themselves grant unrestricted redistribution rights. The original article's licence and publisher terms govern reuse. Preserve attribution and do not redistribute copied full text unless the licence permits it. The scripts preserve licence metadata but do not make legal determinations.
- V7 still does **not** perform authoritative retraction/correction screening, guideline comparison, or clinician adjudication. The local review-decision file is not an authenticated or tamper-proof audit trail. See [ROADMAP_v7.md](ROADMAP_v7.md) for the pre-V8 release gates.

## Offline tests

```bash
python -m unittest discover -s tests -v
python -m py_compile specialty_harvest.py cases2anki.py
```

The tests use synthetic JATS and mocked API responses. They do not establish live API compatibility, diagnosis accuracy, MeSH coverage, or clinical safety.

## Import TSV into Anki

In Anki, choose **File → Import**, select the `.tsv`, set the separator to **Tab**, map `Front`, `Back`, `Extra`, and `Tags`, and enable **Allow HTML in fields**. Choose the target deck and note type. If the first line is imported as a note, enable the option identifying it as a header row.

## V8 additions and current release status

The current local code is marked `8.0.0`. V8 adds a metadata-only JATS media inventory; it does **not** yet download images or generate visual/image-occlusion cards. Run the inventory alongside the normal export:

```bash
python cases2anki.py case_library --format both --output decks/v8_cases \
  --quality-report --review-jsonl --strict-evidence --asset-report
```

This creates `decks/v8_cases_assets.jsonl`. Each figure/table record includes its caption, JATS graphic references, source XML hash, and licence string. Unknown licences are blocked; known licence strings still require manual review. The tool does not interpret or embed article images.

The full version history is in `ROADMAPS.md`. V1/V2 source snapshots were not separately identifiable; legacy archives are preserved in the combined distribution rather than mislabelled. V5 and V6 are strengthened plans/designs, not completed software releases. V8's remaining visual-card, retraction-screening, clinician-validation, and safe Anki-update gates are listed in `ROADMAP_v8.md`.

## Recommended end-to-end commands

From the project root:

```bash
# 1) Create and activate an isolated environment (Linux/macOS)
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# 2) Harvest cases for one or more specialties
python specialty_harvest.py --specialty urology cardiology \
  --year-from 2018 --limit 40 --output case_library

# 3) Generate a strict-evidence deck, audit report, review queue and V8 asset inventory
python cases2anki.py case_library --format both --output decks/clinical_cases \
  --deck "Clinical Case Reports" --quality-report --review-jsonl \
  --strict-evidence --asset-report --preview

# 4) Run all offline checks before using the deck
python -m py_compile specialty_harvest.py cases2anki.py tests/test_pipeline.py
python -m unittest discover -s tests -v
```

Windows PowerShell activation:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

TSV-only export (no Anki package dependency needed):

```bash
python cases2anki.py case_library --format tsv --output decks/clinical_cases
```

Use `--approved-only --review-decisions decisions.jsonl` only after explicit human review decisions have been prepared. The local JSONL reviewer label is not authenticated and is not a tamper-proof audit trail. Always inspect the source article and quality report before studying or sharing cards.
