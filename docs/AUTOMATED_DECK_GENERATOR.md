# Automated Ophthalmology Deck Generator

Usiphus generates one canonical ophthalmology card set with a locally running
Ollama model and exports it to Anki (`.apkg`) and PowerPoint (`.pptx`).

## Expanded undergraduate blueprint

The curriculum example now covers 22 domains and plans 132 draft cards:

- Foundations, ocular anatomy and physiology
- Focused history, visual acuity and complete eye examination
- Optics, refraction and low-vision principles
- Eyelid, orbit and lacrimal disorders
- Conjunctiva, cornea, sclera and ocular surface
- Cataract, glaucoma and uveitis
- Retina, macula, diabetic/vascular retinopathy and retinal detachment
- Neuro-ophthalmology, pupils, visual fields and ocular motility
- Pediatric ophthalmology, amblyopia and strabismus
- Trauma, chemical injuries, infections and emergencies
- Systemic disease, investigations, imaging, pharmacology and procedures
- Integrated clinical reasoning and OSCE practice

Each six-card topic uses a mixed blueprint by default: one basic recall/understanding
card, two MCQs, one clinical case, one viva and one OSCE task. Topics may override
this with a `card_mix` object whose non-negative counts must sum to that topic's
`card_count`. Learning objectives and exam-emphasis prompts are supplied per topic.
This is a broad undergraduate starter blueprint, not a substitute for mapping to
your university's current syllabus or examination specification.

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

The example has 22 topics, so it makes 22 local generation requests. The default
request ceiling is 25 and the default output ceiling is 3,500 tokens per topic.
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

See `examples/ophthalmology/curriculum.json`. Each topic can specify learning
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
