# Usiphus Roadmap — V10 and Beyond

## Version sequence

| Version | Milestone | Primary capability |
|---|---|---|
| V7 | Examination Intelligence | Exam metadata, objectives, rubrics, quality diagnostics |
| V8 | Clinical Evidence Governance | Evidence registry, review workflow, freshness and release gates |
| V9 | Semantic Exam Intelligence | Local similarity analysis, duplicate candidates, answer diagnostics, human review queue |
| V10 | Specialty Curriculum Mapping | Competency mapping, verified-vs-suggested coverage, curriculum gap reports |
| V11 | Adaptive Learning & Assessment | Learner progress, mastery estimates, spaced-repetition scheduling, assessment analytics |
| V12 | Integrated Specialty Learning Platform | End-to-end curriculum, content governance, learner and faculty workflows |

## V10 acceptance criteria

- [x] Read card data in the V9-compatible JSON shape.
- [x] Read a specialty curriculum with stable competency IDs and domains.
- [x] Accept explicit card-to-competency links.
- [x] Generate transparent lexical suggestions without auto-approving them.
- [x] Calculate verified and candidate weighted coverage separately.
- [x] Report domain-level gaps and unmapped cards.
- [x] Export JSON, Markdown, CSV, and manifest artifacts.
- [x] Include deterministic self-tests.
- [ ] Obtain a green GitHub Actions run for compile and self-tests.
- [ ] Validate against at least one faculty-approved real specialty curriculum.
- [ ] Validate against at least one faculty-approved real specialty curriculum.

## V11 planning boundary

V11 should build on explicit V10 curriculum mappings and introduce learner-level
progress only after defining privacy, consent, retention, and data-minimization
requirements. Mastery estimates must be explainable and must not be presented
as proof of clinical competence. Spaced repetition should be configurable and
should not silently rewrite source cards or evidence records.

## Governance

All heuristic mappings remain suggestions until reviewed. Medical claims and
curriculum decisions require qualified human review. A passing software test
suite does not establish clinical accuracy or educational validity.
