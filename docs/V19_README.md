# V19 — Assessment, OSCE and Challenge Studio

**Local-first structured authoring and review workflows** for assessment blueprints, MCQs, OSCE stations, original medical challenges and an external challenge directory.

## Quick start

```bash
python usiphus_v19.py --self-test
python usiphus_v19.py --input examples/v19/studio.json --output-dir build-v19
python -m unittest tests.test_v19_assessment_studio -v
```

V19 is standard-library-only. Structural validation is not clinical validation, educational validity, accreditation, learner competence assessment or faculty approval. See [V19 documentation](docs/V19_ASSESSMENT_OSCE_CHALLENGE_STUDIO.md).
