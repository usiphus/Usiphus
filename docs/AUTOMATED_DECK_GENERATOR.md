# Automated Ophthalmology Deck Generator

Usiphus generates a canonical ophthalmology learning set with a locally running
Ollama model and exports it to Anki (`.apkg`) and PowerPoint (`.pptx`).

## Comprehensive specialty curriculum blueprint

The example curriculum is organized into **16 domains and 52 topic units**, with
six planned cards per topic (**312 draft cards**). Each topic has a stable ID,
domain assignment, learning objectives, exam emphasis, coverage tags and
topic-specific source-discovery terms.

The 16 domains span:

1. Foundations and visual sciences
2. Clinical assessment and diagnostic skills
3. Ocular surface, cornea and external disease
4. Lens, cataract and refractive surgery
5. Glaucoma and ocular hypertension
6. Uveitis and ocular immunology
7. Medical retina and retinal vascular disease
8. Vitreoretinal disease and surgery
9. Neuro-ophthalmology and ocular motility
10. Pediatric ophthalmology and strabismus
11. Oculoplastics, orbit and lacrimal system
12. Ocular oncology and pathology
13. Ocular trauma and emergency ophthalmology
14. Ocular infection and endophthalmitis
15. Systemic disease, pharmacology and perioperative medicine
16. Ophthalmic procedures, microsurgery, rehabilitation and professional practice

This is a comprehensive specialty map, not a claim that one generic blueprint
replaces a national curriculum, residency logbook or local examination
specification. Faculty should map domains and topics to local outcomes, training
stages, case exposure, competencies and assessment weighting. The structure is
designed to be reusable for other specialties by supplying a specialty-specific
curriculum JSON with the same stable domain/topic/objective structure.

Each six-card topic uses a mixed blueprint by default: one basic
recall/understanding card, two MCQs, one clinical case, one viva and one OSCE
task. Topic-level card counts and mixes can be adjusted to match learner level
and local examination priorities. The example makes 52 local Ollama requests;
the default request ceiling is 60. These are workload limits, not a promise of
completion time or a financial cap.

## External source discovery

The curriculum declares a source catalogue and topic-specific discovery terms.
The goal is to find resources relevant to **every topic and learning objective**,
not to rely on a single source or generate topics from whatever happens to be
easy to scrape.

| Source family | Examples | Intended contribution |
|---|---|---|
| Public Anki decks | AnkiWeb shared decks | Existing flashcard coverage, revision angles and gap detection |
| Free medical challenges | DoctorDE | Interactive challenge themes and exam-practice signals |
| Open-access journals | Cureus | Clinical studies, reviews and case-based evidence |
| Biomedical literature | PubMed Central, PubMed, DOAJ | Full-text research, abstracts and open-access journal discovery |
| Public exam-preparation sites | Geeky Medics, TeachMeSeries and other public specialty teaching sites | Commonly tested concepts, MCQ/OSCE formats and explanations |
| Professional guidance | Relevant specialty societies and national bodies | Current recommendations and consensus |
| Open educational resources | Open textbooks and university teaching materials | Foundational sciences and structured explanations |

### How source matching should work

1. Build searches from the topic name, learning objectives, exam emphasis,
   coverage tags, specialty and learner level.
2. Discover candidates through public search, official APIs/feeds where
   available, curated directories and faculty-submitted links.
3. Match each candidate to stable topic IDs and, where possible, individual
   learning-objective IDs. Keep unmatched candidates in a triage queue.
4. Record title, canonical URL, source family, author/organization, date,
   DOI/PMID where applicable, access status, licence/reuse status, retrieval
   date, matched topics/objectives and review status.
5. Rank separately for relevance, clinical authority, recency, methodological
   quality, learner-level fit and rights clarity. Popularity and exam frequency
   are not clinical evidence quality.
6. Show topic-level coverage gaps and conflicting claims for faculty review.
   Aim for multiple independent source types per topic where feasible.

### Rights, provenance and clinical safety

Publicly viewable does not mean reusable. Usiphus should default to storing
metadata, citations and links, not copying full decks, questions, article text,
images or explanations. Import, quotation, transformation or redistribution is
allowed only where the relevant licence, permission and site terms permit it.
Respect robots rules, rate limits, authentication boundaries and paywalls; do
not scrape gated material or bypass access controls. Where rights are unclear,
mark the item `rights_unknown` or `link_only` and retain only permitted
metadata.

Anki decks and public exam-preparation pages are useful learning and exam-practice
signals, but may contain errors or outdated content. Cureus and other journals
provide research evidence but individual studies and case reports are not
automatically practice guidelines. PubMed is a discovery index and does not
guarantee full-text availability. Clinical recommendations should be anchored
to appropriate current guidelines and critically appraised evidence. Conflicts
must be surfaced with jurisdiction and publication-date context, not silently
resolved by a model.

## Local generation

This generator uses the Ollama server at `http://localhost:11434` by default.
It does not use a cloud AI service, account, API key or API billing. Model
downloads require internet access and disk space; local inference uses your
computer's memory, CPU/GPU and electricity.

The default model is `llama3.1:8b`. Change it with `--model` or the
`USIPHUS_OLLAMA_MODEL` environment variable.

```bash
ollama pull llama3.1:8b
python -m pip install genanki python-pptx
```

Ensure the Ollama server is running. If it is not started automatically, run
`ollama serve`.

## Preview the workload before generating

Use `--plan-only` to see the number of topics, total cards, per-topic card
mix, model, formats and output directory without contacting Ollama or creating
the output directory. This is especially useful when testing
`--cards-per-topic` and `--override-curriculum-card-counts`.

```bash
python usiphus_auto_deck.py --generate \
  --curriculum examples/ophthalmology/curriculum.json \
  --cards-per-topic 3 --override-curriculum-card-counts --plan-only
```

## Generate both formats

Run from the repository root:

```bash
python usiphus_auto_deck.py --generate \
  --curriculum examples/ophthalmology/curriculum.json \
  --model llama3.1:8b --formats both \
  --output-dir build/ophthalmology
```

The 52-topic example makes 52 local generation requests. The default request
ceiling is 60 and the default output ceiling is 3,500 tokens per topic. Adjust
them with `--max-ollama-calls` and `--max-output-tokens`. Topic count
determines the number of model requests; total cards are controlled by topic
card counts.

Outputs include the Anki package, PowerPoint, `generated_cards.json` and
`quality_report.json`.

### Slow local computers, timeouts and safe resume

Each topic is generated as a compact batch. The model returns only learning
content (question, answer and type-specific fields); Python fills IDs,
specialty, tags, references, exam-domain metadata and evidence status. This
avoids spending inference tokens on fields that can be assigned deterministically.
Responses are streamed incrementally, and the model is kept loaded for 30 minutes
between requests. If an individual card is incomplete, valid cards are retained
and only the failed slots are regenerated. The token budget scales with the
number of cards still pending, within the configured --max-output-tokens ceiling.
The socket timeout measures inactivity between stream chunks, not total
generation time.

```bash
python usiphus_auto_deck.py --generate \
  --curriculum examples/ophthalmology/curriculum.json \
  --model phi4-mini --cards-per-topic 3 --override-curriculum-card-counts \
  --ollama-timeout 900 --max-output-tokens 2048 \
  --formats anki --output-dir build/ophthalmology
```

The `--override-curriculum-card-counts` switch makes `--cards-per-topic`
apply to every topic, even when the curriculum defines its own `card_count`.
Without that switch, topic-level counts remain authoritative.

After each successful topic, the generator atomically saves a checkpoint under
`build/ophthalmology/.checkpoints/` and refreshes `generated_cards.json` with
an `in_progress` status. You can inspect or back up the cards before the full
curriculum finishes. If the run stops or the computer restarts, rerun the same
command with `--resume` added. Matching completed topics are loaded from disk;
changed topics/settings are regenerated. The final JSON is marked `complete`,
then quality checks and requested exports run. Progress is printed after each
topic. A socket inactivity timeout is not blindly retried: repeating the same
expensive generation can waste another 15 minutes without fixing the cause.
Instead, check Ollama/model health, available RAM/VRAM and CPU/GPU load. If the
first token is still taking too long, first reduce the requested cards or
`--max-output-tokens`; consider a smaller model if necessary.

For a quick smoke test, use a one-topic curriculum copy and set
`--cards-per-topic 3 --override-curriculum-card-counts` before starting the
full specialty run.

## Export existing cards without Ollama

This workflow makes no model request and needs no running Ollama server:

```bash
python usiphus_auto_deck.py --cards path/to/ophthalmology_cards.json \
  --formats both --output-dir build/ophthalmology
```

## Curriculum and clinical governance

See `examples/ophthalmology/curriculum.json`. Every topic has a stable ID,
curriculum domain, coverage tags, learning objectives, exam emphasis, source
search terms and source families to discover. The source catalogue defines
discovery and rights safeguards. Actual source records must retain provenance
and article/deck/page-level rights metadata; source lists are not to be invented
by the generator.

Generated content is an **unreviewed educational draft**, not medical advice
or a clinical reference. Structural validation cannot establish clinical
accuracy, guideline currency, source quality, fairness or patient safety.
Qualified specialty faculty must verify every claim, answer, MCQ key, OSCE
checklist and reference before teaching or learner distribution. Drug doses,
local referral pathways and guideline-dependent recommendations require
source-based review. Do not use patient-identifiable data; use synthetic
teaching cases only.
