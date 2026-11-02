# Automated Ophthalmology Deck Generator

Usiphus can generate one canonical ophthalmology card set with a locally running
Ollama model and export it to Anki (`.apkg`) and PowerPoint (`.pptx`).

## Local generation

This generator uses the Ollama server at `http://localhost:11434` by default.
It does not use a cloud AI service, account, API key, or API billing.
Install Ollama, download a model once, and run inference on your own computer.
Model downloads require internet access and disk space; local inference uses
your computer's memory, CPU/GPU and electricity.

The default model is `qwen2.5:7b`. Change it with `--model` or the
`USIPHUS_OLLAMA_MODEL` environment variable.

```bash
ollama pull qwen2.5:7b
python -m pip install genanki python-pptx
```

Ensure the Ollama server is running. If it is not started automatically, run
`ollama serve`.

## Generate both formats

```bash
python usiphus_auto_deck.py --generate \
  --curriculum examples/ophthalmology/curriculum.json \
  --formats both --model qwen2.5:7b \
  --output-dir build/ophthalmology
```

Use `--ollama-url` to specify another server address. One local request is made
per curriculum topic. The default ceiling is 11 requests and 2,500 output tokens
per topic; adjust with `--max-ollama-calls` and `--max-output-tokens`.

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
objectives, faculty-supplied references and a card count. References are never
invented by the generator; empty source lists must be completed during review.

Generated content is an **unreviewed educational draft**, not medical advice or
a clinical reference. Structural validation cannot establish clinical accuracy,
guideline currency, source quality, fairness, or patient safety. Qualified
ophthalmology faculty must verify every claim, answer, MCQ key, image license,
and reference before teaching or learner distribution. Do not use patient-
identifiable data; use synthetic teaching cases only.
