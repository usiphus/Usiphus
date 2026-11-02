# Automated Ophthalmology Deck Generator

Usiphus generates one canonical ophthalmology card set with a locally running
Ollama model and exports it to Anki (`.apkg`) and PowerPoint (`.pptx`).

## Comprehensive specialty curriculum blueprint

The example curriculum is organized into **16 domains and 52 topic units**, with
six planned cards per topic (**312 draft cards**). Each topic has a stable ID,
domain assignment, three learning objectives, exam-emphasis prompts and coverage
tags. The domain is passed into the local generation prompt so topic selection
and card drafting remain traceable to the curriculum map.

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
14. Ocular infection, systemic disease, therapeutics, surgery, rehabilitation and professional practice

Coverage includes clinical and surgical subspecialties, diagnostics, emergencies,
procedures, perioperative care, low vision, community ophthalmology and ethics.
The list is intended as a comprehensive specialty map, not a claim that one
generic blueprint replaces a national curriculum, residency logbook or local
examination specification. Faculty should map each domain and topic to the
relevant training stage, local outcomes, case exposure and assessment weighting.

Each six-card topic uses a mixed blueprint by default: one basic recall/understanding
card, two MCQs, one clinical case, one viva and one OSCE task. Topics may override
this with a `card_mix` object whose non-negative counts must sum to that topic's
`card_count`. The 52-topic example makes 52 local Ollama requests; the default
request ceiling is 60. These are workload limits, not a promise of completion
time or a financial cap.

## Local generation

This generator uses the Ollama server at `http://localhost:11434` by default.
It does not use a cloud AI service, account, API key, or API billing. Model
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

## Generate both formats

Run from the repository root:

```bash
python usiphus_auto_deck.py --generate \
  --curriculum examples/ophthalmology/curriculum.json \
  --model llama3.1:8b --formats both \
  --output-dir build/ophthalmology
```

The example has 42 topics, so it makes 42 local generation requests. The default
request ceiling is 50 and the default output ceiling is 3,500 tokens per topic.
These are workload limits, not a promise of completion time or a financial cap.
Adjust them with `--max-ollama-calls` and `--max-output-tokens`. You can reduce
generation time and hardware load by lowering topic `card_count` values in the
curriculum; the topic count determines the number of model requests.

Outputs include `ophthalmology.apkg`, `ophthalmology.pptx`,
`generated_cards.json`, and `quality_report.json`.

## Export existing cards without Ollama

This workflow makes no model request and needs no running Ollama server:

```bash
python usiphus_auto_deck.py --cards path/to/ophthalmology_cards.json \
  --formats both --output-dir build/ophthalmology
```

## Curriculum and clinical governance

See `examples/ophthalmology/curriculum.json`. Each topic can specify a stable ID, curriculum domain, coverage tags, learning
objectives, exam emphasis, faculty-supplied references, card count and optional
card-type mix. References are never invented by the generator; empty source lists
must be completed during review. Add local faculty-approved references before
using the generated content for teaching.

Generated content is an **unreviewed educational draft**, not medical advice or
a clinical reference. Structural validation cannot establish clinical accuracy,
guideline currency, source quality, fairness, or patient safety. Qualified
ophthalmology faculty must verify every claim, answer, MCQ key, OSCE checklist
and reference before teaching or learner distribution. Drug doses, local referral
pathways and guideline-dependent recommendations require source-based review.
Do not use patient-identifiable data; use synthetic teaching cases only.
