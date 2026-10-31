# Clinical Case Report → Exam Flashcards (Version 8.2.0)

> Roadmap index: [ROADMAPS.md](ROADMAPS.md) · [V4 baseline](ROADMAP_v4.md) · [V5 safety roadmap](ROADMAP_v5.md) · [V6 database design](ROADMAP_v6.md) · [V7 review workflow](ROADMAP_v7.md) · [V8 media roadmap](ROADMAP_v8.md) · [Code audit](BUG_AUDIT.md)

A local workflow for finding open-access clinical case reports in Europe PMC, screening known article updates, indexing source material in SQLite, and converting JATS full text into source-linked, exam-style Anki cards. The pipeline is extractive and heuristic: it is a study aid, not a clinical decision system.

## V7.0 evidence and review features

- **Evidence-linked cards:** deterministic card/evidence IDs and exact source-section sentence spans with character offsets.
- **Evidence contracts:** answer claims are stored separately from source quotations. A linked quotation is not automatically treated as proof that the answer clause is supported.
- **Claim assessments:** signed clinician assessments bind to exact claim/evidence hashes; high-risk claims require two distinct reviewers.
- **Signed review workflow:** `--review-jsonl` creates a review queue. `--approved-only` requires an expiring, role-scoped signed reviewer token and matching current content/evidence hashes. A typed reviewer name or legacy unsigned decision cannot approve a card.
- **Strict evidence export:** `--strict-evidence` keeps only cards with a direct evidence span and source URL.
- **Author-stated reasoning cards:** quote explicit reasoning from the discussion; do not infer guidelines or next-best steps.

## What's new in 8.2.0

- **Source status:** Europe PMC status-update and Crossref `update-to` checks with TTL cache, force refresh, explicit unknown/conflict states, and default exclusion of confirmed retractions.
- **Reliable harvesting:** bounded HTTP reads/retries, numeric and HTTP-date `Retry-After`, jittered backoff, per-host rate limiting, persistent SQLite checkpoints, resumable processing, `--target-saved`, and hard `--max-candidates` budget.
- **Cache verification:** `--verify-cache` validates manifest consistency, required files, metadata schema, identity/specialty/status, and source XML SHA-256.
- **Claim-level evidence:** exact evidence spans and answer clauses are distinct. Only authenticated clinician assessments can mark claims supported; high-risk claims need two different reviewers.
- **Guideline separation:** guideline sources and claims are stored separately from case reports. Ingestion requires HTTPS, dates, version/grade, an allow-listed host, and explicit governance approval.
- **Evaluation runner:** per-specialty and per-card-type precision/recall/F1, evidence exactness, unsupported-claim rate, denominators and error examples. It accepts only real adjudicated data; no synthetic gold set or fabricated metrics is included.
- **SQLite schema v4:** explicit migrations, append-only claim-assessment history, and hash-bound review decisions.

## V5 release boundary

The V5 engineering workflows are implemented, but this is **not a clinical-safety certification**. A real multi-specialty clinician-adjudicated gold set, institutional reviewer identity provider and key management, Crossmark integration, live Europe PMC/Crossref verification, and a governance-approved guideline allow-list must be supplied by the deploying institution. The shipped allow-list is intentionally unapproved. HMAC tokens provide signed role claims only when an institution controls issuance and secret storage; they are not SSO/MFA. See `ROADMAP_v5.md` for exact gates.

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

`--limit` means search candidates per specialty, not guaranteed retained reports. Full-text availability, word count, relevance, and educational filters can reduce the saved number. For resumable bounded runs, use:

```bash
python specialty_harvest.py --specialty urology cardiology --target-saved 40 \
  --max-candidates 500 --state-db case_library/.harvest_state.sqlite3 \
  --check-status --verify-cache --output case_library
```

The harvest state database records candidate transitions and retryability. `--verify-cache` writes `cache_verification.json` and exits non-zero if the cache is inconsistent. `--target-saved` is distinct from the legacy per-specialty candidate `--limit`.

Run the evaluation engine only after collecting real, de-identified, clinician-adjudicated labels:

```bash
python v5_release.py evaluate clinician_gold.jsonl --dataset-version 1.0 \
  --protocol "documented dual-review and adjudication protocol" --output clinical_metrics.json
```

The evaluator rejects synthetic/unadjudicated data, requires unique de-identified reviewer IDs, and enforces double review for high-risk examples. The repository contains no gold dataset and reports no clinical performance score.

Create Anki packages and an import TSV:

```bash
python cases2anki.py case_library --format both --output decks/clinical_cases --deck "Clinical Case Reports" --quality-report --preview
```

Create a review queue and export only evidence-linked cards:

```bash
python cases2anki.py case_library --format both --output decks/v7 --strict-evidence --quality-report --review-jsonl --preview
```

Issue a signed, expiring reviewer token using an institution-controlled secret (do not commit the secret or token file):

```bash
export ANKI_REVIEW_HMAC_SECRET="<secret-from-your-secret-manager-at-least-32-characters>"
python v5_release.py issue-token --reviewer-id clinician-01 --role clinician \
  --expires-at 2027-01-01T00:00:00Z --output reviewer-token.json
```

Import the generated review queue into SQLite, then export the hash-bound audit queue for reviewers:

```bash
python case_database.py import --db case_library.sqlite3 --input case_library \
  --review-queue decks/review_review.jsonl
python case_database.py review-queue --db case_library.sqlite3 --output audit_review.jsonl
```

Each claim in `audit_review.jsonl` has its own `claim_hash` and `evidence_hash`; each card has `content_hash` and card-level `evidence_hash`. Clinicians must inspect the source and record a signed assessment for each answer claim. A high-risk claim needs two distinct clinician/adjudicator tokens. Card approval also needs a signed decision with the exact card hashes:

```bash
python case_database.py import --db case_library.sqlite3 --input case_library \
  --claim-assessments claim_assessments.jsonl \
  --authenticated-review-decisions signed_decisions.jsonl
```

Finally, export only cards whose database audit record, content, evidence spans, and all claim assessments still match:

```bash
python cases2anki.py case_library --format both --output decks/v5_approved \
  --review-audit-db case_library.sqlite3 --approved-only --strict-evidence
```

The HMAC signer is an institutional trust boundary, not an integrated SSO/MFA provider. Reviewer identity must be provisioned and verified by the institution. SQLite append-only triggers do not protect against host/database replacement.

Run optional status screening while harvesting (requires internet access):

```bash
python specialty_harvest.py --specialty urology --year-from 2018 --limit 40 \
  --check-status --status-ttl-hours 24 --output case_library
```

Create and populate the local SQLite index, then search it:

```bash
python case_database.py init --db case_library.sqlite3
python case_database.py import --db case_library.sqlite3 --input case_library
python case_database.py search --db case_library.sqlite3 --query '"renal artery"' --specialty urology
python case_database.py manifest --db case_library.sqlite3 --output case_manifest.jsonl
python case_database.py check --db case_library.sqlite3
```

Import generated review cards after the articles have been indexed:

```bash
python cases2anki.py case_library --format tsv --output decks/review \
  --review-jsonl --strict-evidence
python case_database.py import --db case_library.sqlite3 --input case_library \
  --review-queue decks/review_review.jsonl
```

For authenticated database review, use `--authenticated-review-decisions` and `--claim-assessments` with `ANKI_REVIEW_HMAC_SECRET`. Plain `--review-decisions` remains a legacy import path and never authorizes approved-only export. Claim assessments are individually hash-bound; high-risk claims require two distinct clinician identities. Local SQLite triggers are not tamper-proof against host/database replacement.

Guideline JSONL can be imported separately with `--guideline-jsonl`. Before importing, the allow-list JSON must be approved by the clinical governance owner (`governance_approved: true`, `approved_by`, and `reviewed_at`). The shipped allow-list is disabled by default.


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
- Article-status screening is best-effort; unknown/conflicting status requires manual checking. HMAC-signed reviewer tokens are only as trustworthy as institutional provisioning and key management. The local database is not tamper-proof. No clinician gold set or clinical metrics are shipped, and the guideline allow-list requires governance approval.

## Offline tests

```bash
python -m unittest discover -s tests -v
python -m py_compile specialty_harvest.py cases2anki.py source_integrity.py case_database.py tests/*.py
```

The tests use synthetic JATS and mocked API responses. They do not establish live API compatibility, diagnosis accuracy, MeSH coverage, or clinical safety.

## Import TSV into Anki

In Anki, choose **File → Import**, select the `.tsv`, set the separator to **Tab**, map `Front`, `Back`, `Extra`, and `Tags`, and enable **Allow HTML in fields**. Choose the target deck and note type. If the first line is imported as a note, enable the option identifying it as a header row.

## V8 additions and current release status

The V8 media inventory remains metadata-only; it does **not** yet download images or generate visual/image-occlusion cards. The current local code is marked `8.2.0`. Run the inventory alongside the normal export:

```bash
python cases2anki.py case_library --format both --output decks/v8_cases \
  --quality-report --review-jsonl --strict-evidence --asset-report
```

This creates `decks/v8_cases_assets.jsonl`. Each figure/table record includes its caption, JATS graphic references, source XML hash, and licence string. Unknown licences are blocked; known licence strings still require manual review. The tool does not interpret or embed article images.

The full version history is in `ROADMAPS.md`. V1/V2 source snapshots were not separately identifiable; legacy archives are preserved in the combined distribution rather than mislabelled. V5 engineering workflows are implemented, but its clinical release gates remain open; V6 schema v4 is the current local database foundation. V8's remaining visual-card, licensing, and safe Anki-update gates are listed in `ROADMAP_v8.md`.

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
python -m py_compile specialty_harvest.py cases2anki.py source_integrity.py case_database.py tests/*.py
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

Use `--approved-only --review-audit-db case_library.sqlite3` only after signed card decisions and clinician claim assessments have been imported into the SQLite audit workflow. HMAC tokens do not replace SSO/MFA, and SQLite audit triggers do not protect against host compromise. Always inspect the source article and quality report before studying or sharing cards.

## Historical source snapshots

Preserved legacy ZIP archives and unclassified pasted source artifacts are stored under `history/`. V1/V2 labels remain intentionally unassigned because the source material did not identify those versions reliably. See `history/PACKAGE_MANIFEST.md`.
