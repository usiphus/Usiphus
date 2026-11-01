# V20 — Feedback-to-Action and Faculty Authoring

**Local-first feedback workflow and versioned faculty authoring.**

## Quick start

```bash
python usiphus_v20.py --self-test
python usiphus_v20.py --input examples/v20/workflow.json --output-dir build-v20
python -m unittest tests.test_v20_feedback_authoring -v
```

V20 turns purpose-defined learner/faculty feedback into traceable actions and supports versioned authoring for cards, cases, objectives, evidence links, mappings, challenge packs, OSCE stations and MCQs. Reports minimize feedback text; structured JSON, CSV action queue and HTML release preview are generated. PDF/PPTX adapters are deferred; see docs/V20_FEEDBACK_TO_ACTION_AND_FACULTY_AUTHORING.md.

A clean structural report is not faculty approval, consent verification, privacy compliance, educational effectiveness or proof of competence. See docs/V20_FACULTY_PILOT_PROTOCOL.md.
