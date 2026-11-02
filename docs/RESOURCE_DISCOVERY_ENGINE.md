# Curriculum-aware resource discovery engine

Usiphus resource discovery searches curriculum topics and learning objectives
against public scholarly metadata APIs, creates source-specific public search
links for educational sites without a documented API, and reports topic-level
coverage gaps. It is a discovery/indexing tool, not a content scraper or clinical
evidence approval system.

## Supported discovery paths

### Live metadata APIs

- **PubMed:** NCBI E-utilities search and summary endpoints; returns bibliographic metadata, PMID, DOI when present, journal, authors and publication date.
- **PubMed Central:** Europe PMC search restricted to its open-access flag; returns metadata and article-level licence when supplied. The OA flag is not a redistribution licence: verify the actual article terms.
- **Cureus:** Europe PMC search constrained to the Cureus journal; returns indexed article metadata and licence fields where available.
- **Crossref:** bibliographic metadata and DOI discovery.

### Public-site search links (not crawlers)

- **AnkiWeb:** topic-specific shared-deck search link.
- **DoctorDE:** topic-specific public web search link restricted to the DoctorDE domain.
- **Geeky Medics and TeachMeSeries:** topic-specific search links.
- **Professional guidelines:** specialty-guideline search link.
- **Open educational resources:** open textbook/university resource search link.
- **DOAJ:** article search link.

These sources do not expose a stable, documented public search API in this
implementation. Usiphus deliberately does not scrape their pages, access
authenticated/gated material, download decks, or copy proprietary question
banks. The learner or faculty member follows the link and submits a curated
resource record after checking the original page. Search links are not evidence
that a matching resource exists.

## Run

From the repository root:

```bash
python usiphus_resource_discovery.py \
  --curriculum examples/ophthalmology/curriculum.json \
  --output-dir build/ophthalmology-discovery
```

For a quick smoke/pilot run:

```bash
python usiphus_resource_discovery.py \
  --curriculum examples/ophthalmology/curriculum.json \
  --max-topics 2 --limit 3 --delay 0.34 \
  --output-dir build/discovery-pilot
```

Outputs:
- `resources.json`: deduplicated metadata records mapped to topic and objective IDs.
- `coverage_report.json`: topic-level source-family coverage, missing source families,
  provider errors and governance summary.

Options: `--limit` controls maximum API records per provider/topic (1–20);
`--max-topics` limits pilot scope; `--timeout` controls provider request timeout;
`--delay` sets a polite delay between provider requests. No API key is required
for the configured public metadata endpoints. Network access is required.

## Ranking and mapping

A transparent lexical relevance score compares resource title/abstract tokens
with the topic name, learning objectives, exam emphasis and coverage tags.
Matched terms and matched objective IDs are retained. This is a relevance aid,
not a quality score. Search engine ranking, popularity, Anki deck downloads,
citation counts and exam frequency are not used as clinical evidence grades.
Faculty can later add structured appraisal fields without overwriting original
provider metadata.

## Rights, safety and governance

- Metadata and canonical links only; no full-text articles, decks, images,
  challenge text or proprietary questions are downloaded.
- Publicly visible does not mean reusable. Store licence status and verify
  article/page/deck-level permissions before import, quotation, transformation
  or redistribution.
- Provider errors are reported per topic and provider; an error is not counted
  as a zero-result search.
- Search links are labelled `search_link_only`, not verified resources.
- Clinical evidence, exam-practice signals, guidelines and foundational
  teaching are separate evidence roles.
- All resources begin `unreviewed`. A clinician/faculty reviewer must assess
  accuracy, relevance, currency, methodology, jurisdiction and licence before
  use in teaching or generated clinical content.
- Do not use patient-identifiable data in topic queries or resource records.

The default run processes all curriculum topics and may make several hundred
public API requests. Start with `--max-topics 2`, use a sensible delay, and
scale up after reviewing the output and provider terms.
