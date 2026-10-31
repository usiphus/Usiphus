# Clinical Case Report → Exam Flashcards (Version 3.0)

Two Python scripts discover open-access clinical case reports in Europe PMC and convert JATS full text into source-linked exam-style Anki cards.

## Version 3.0 changes

- Diagnosis extraction is more conservative: it works sentence-by-sentence, prioritizes final/confirmed diagnoses, and filters common differential, negation, and uncertainty contexts. Title-only diagnoses receive lower heuristic confidence.
- The quality CSV includes the diagnosis evidence sentence, extraction source, rationale, confidence, and quality flags.
- Card types include diagnosis, investigation, case-reported management, outcome, teaching point, and article-discussed differential / diagnostic pitfalls. Differential cards are explicitly framed as alternatives discussed by the authors.
- Basic QA flags detect duplicate fronts and possible answer leakage.
- Educational ranking now records a component breakdown in `meta.json`; it remains a heuristic, not a validated clinical-importance score.
- DOI deduplication is applied in search results and during deck generation.
- Cached XML is rechecked against current word-count and specialty-relevance thresholds.
- Urology/nephrology terms have additional synonyms, year validation handles explicitly supplied values correctly, and output basenames preserve dotted names.

## Install

Python 3.10 or newer is recommended.

```bash
python -m pip install -r requirements.txt
```

The harvester and TSV exporter use only the Python standard library. Install `genanki` from `requirements.txt` only if you need `.apkg` export.

## Example workflow

Harvest Urology and Cardiology case reports from 2018 onward:

```bash
python specialty_harvest.py --specialty urology cardiology --year-from 2018 --limit 40 --sort citations --output case_library
```

`--limit` is the number of search candidates per specialty, not a guarantee of 40 saved articles. Some candidates may fail full-text, word-count, relevance, or educational filters.

Build TSV and APKG files plus a review report and preview:

```bash
python cases2anki.py case_library --format both --output decks/clinical_cases --deck "Clinical Case Reports" --quality-report --preview
```

TSV-only export (no optional dependency required):

```bash
python cases2anki.py case_library --format tsv --output decks/clinical_cases
```

To disable differential cards:

```bash
python cases2anki.py case_library/urology --format tsv --output decks/urology --no-differential --quality-report
```

Use `--skip-low-confidence-cases` to exclude cases whose diagnosis extraction confidence is below `--min-confidence`. Confidence is a rule-based review signal, **not a calibrated probability**.

## Outputs

Each harvested case folder contains `article.xml`, `article.txt`, and `meta.json`. The output root also contains a cumulative `manifest.jsonl` and `harvest_errors.jsonl` where applicable. Metadata includes source URL, DOI, licence string, educational score and score breakdown.

The converter can write:

- `.tsv` — Anki import file.
- `.apkg` — optional Anki package (`genanki` required).
- `.quality.csv` — diagnosis, evidence sentence, confidence, card types, and quality flags.
- `.preview.html` — local review preview when `--preview` is enabled.

## Supported specialties

`anesthesia`, `cardiology`, `cardiothoracic-surgery`, `dermatology`, `emergency-medicine`, `endocrinology`, `gastroenterology`, `general-surgery`, `hematology`, `infectious-disease`, `nephrology`, `neurology`, `neurosurgery`, `obstetrics-gynecology`, `oncology`, `ophthalmology`, `orthopedics`, `otolaryngology`, `pediatrics`, `psychiatry`, `pulmonology`, `radiology`, `rheumatology`, `urology`, `vascular-surgery`.

## Safety, quality, and licence limitations

- Automated extraction can still misread complex prose, multiple diagnoses, pathology qualifiers, contradictory narratives, or unusual JATS structures. Review the quality report and original article before relying on a card.
- Management cards state what happened in one case report. They are **not** universal recommendations or current guidelines.
- The educational score is a text-feature heuristic, not a measure of peer-review quality, evidence strength, novelty, or clinical importance.
- Open-access full text does not automatically mean unrestricted redistribution. The article's licence and publisher terms govern reuse. Keep copied full text for permitted personal study, preserve attribution, and do not redistribute it unless the licence permits it. The project records licence information but does not interpret legal permissions.
- The scripts do not provide medical advice or validate current guidelines.

## Offline tests

```bash
python -m unittest discover -s tests -v
python -m py_compile specialty_harvest.py cases2anki.py
```

The tests use synthetic JATS and mocked API responses. They do not establish clinical accuracy, live API compatibility, or successful `.apkg` generation.

## Import TSV into Anki

Choose **File → Import**, select the `.tsv`, set the separator to **Tab**, map `Front`, `Back`, `Extra`, and `Tags`, and enable **Allow HTML in fields**. Select the target deck and note type; if needed, mark the first row as field names.
