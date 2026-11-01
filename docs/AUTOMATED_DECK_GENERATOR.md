# Automated Ophthalmology Deck Generator

Usiphus can draft one canonical ophthalmology card set and export it to **Anki**
(`.apkg`) and **PowerPoint** (`.pptx`) in one run.

## Cost and privacy

- AI generation uses the OpenAI API and is **separately billable** from ChatGPT.
- No API call is made unless `--generate` is supplied.
- Set `OPENAI_API_KEY` in the shell environment. Never paste the key into JSON,
  source code, a commit, or a prompt.
- Each topic is one API request. The default curriculum has 11 topics; the default
  ceiling is 11 requests. `--max-api-calls` blocks a larger curriculum before any
  network request. `--max-output-tokens` defaults to 2,500 per topic request.
- These request/token ceilings reduce exposure but are **not a hard dollar cap**;
  input tokens and model pricing also affect cost. Check API usage and billing limits
  in your OpenAI Platform account before generation.
- For a no-API workflow, provide an existing Usiphus cards JSON file with
  `--cards`; export still runs locally.
- Do not send patient-identifiable data. Use synthetic teaching cases only.

## Install optional exporters

The core generator uses the Python standard library for API transport and JSON.
Install the two optional exporters:

```bash
python -m pip install genanki python-pptx
```

## Curriculum file

See `examples/ophthalmology/curriculum.json`. Each topic can specify learning
objectives, faculty-supplied references and a card count. References are never
invented by the generator; an empty source list remains empty and must be
completed during faculty review.

## Generate both formats

Linux/macOS:

```bash
export OPENAI_API_KEY="your-key"
python usiphus_auto_deck.py --generate \
  --curriculum examples/ophthalmology/curriculum.json \
  --formats both --max-api-calls 11 --max-output-tokens 2500 \\
  --output-dir build/ophthalmology
```

Windows PowerShell:

```powershell
$env:OPENAI_API_KEY = "your-key"
python usiphus_auto_deck.py --generate --curriculum examples/ophthalmology/curriculum.json --formats both --output-dir build/ophthalmology
```

Outputs:
- `ophthalmology.apkg` — import into Anki.
- `ophthalmology.pptx` — lecture/study slides.
- `generated_cards.json` — canonical generated draft.
- `quality_report.json` — structural validation findings and explicit review status.

## Use existing cards without an API call

```bash
python usiphus_auto_deck.py --cards path/to/ophthalmology_cards.json \
  --formats both --output-dir build/ophthalmology
```

## Clinical governance

Generated content is an **unreviewed educational draft**, not medical advice or
a clinical reference. Structural validation cannot establish clinical accuracy,
guideline currency, source quality, fairness, or patient safety. A qualified
ophthalmology faculty member must verify every claim, answer, MCQ key, image
license, and reference before teaching or learner distribution. The generator
does not fetch references, scrape websites, use patient data, or approve content.
