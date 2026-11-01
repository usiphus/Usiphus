# V19 — Assessment, OSCE and Challenge Studio

V19 adds a local-first structured workflow for assessment blueprints, single-best-answer MCQs, OSCE stations, original challenge cases and an external challenge directory.

## Quick start

```bash
python usiphus_v19.py --self-test
python usiphus_v19.py --input examples/v19/studio.json --output-dir build-v19
python -m unittest tests.test_v19_assessment_studio -v
```

Outputs: JSON report, Markdown review report and CSV review queue. Exit code 2 means structural errors block progression. Warnings require faculty review; a clean report means **ready for faculty review**, not approved.

## Authoring model

- **Blueprint:** specialty, competency, objective, cognitive level and assessment method.
- **MCQ:** focused stem/lead-in, homogeneous option set, exactly one keyed answer, answer rationale, distractor rationales, objective links and evidence reference.
- **OSCE:** candidate instructions, role-player brief, setup/equipment, examiner instructions, duration, checklist, critical actions, global rating scale and rubric.
- **Native challenge:** original case stem, ordered clues, accepted answer aliases, explanation, differential rationale, objective mapping and provenance.
- **External directory:** provider URL, format, access method, rights/reuse status and last-checked date. Default is link-out; embedding is rejected unless rights are explicitly licensed or permission-granted.

## Quality flags

Checks are deterministic structural prompts inspired by faculty item-writing practice. They identify missing or inconsistent fields, duplicate prompt candidates, missing rationales, blueprint mismatches and incomplete station/case materials. They do not determine whether an item is clinically correct, unambiguous, fair, valid or reliable. Duplicate candidates are never auto-merged or deleted.

Optional item statistics accept aggregate counts only. The report records cohort size, missingness and proportion correct, labels small cohorts, and explicitly avoids validity/reliability/fairness conclusions. No learner-level responses or identifiers are accepted.

## Governance and privacy

- Keep all fixtures synthetic. Do not put identifiable patient images, patient records or learner data in examples.
- Require documented rights, provenance, consent/de-identification review and accessibility checks before media use.
- Do not scrape, mirror or reproduce third-party challenge clues, answers, images or games. A directory link does not imply affiliation, endorsement or reuse rights.
- Reviewer labels are labels, not authenticated identities. Approval metadata is not proof of identity or competence.
- Faculty must approve clinical accuracy, evidence interpretation, curriculum mapping, assessment quality, accessibility and publication.
- Scores and completion data are engagement/practice signals, not proof of learning or clinical competence.
- V19 does not claim QTI, CASE, LTI or other standards conformance.

## Pilot exit criteria

A specialty pilot should document faculty review of every released artifact, usability feedback, accessibility and media-rights checks, defect categories, version traceability and safe withdrawal/revision. Do not use real learner/patient data until an institution has specified purpose, lawful basis/consent where applicable, access, retention, deletion and incident processes.
