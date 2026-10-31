# Usiphus Roadmap — V10 and Beyond

## Version sequence

| Version | Milestone | Primary capability |
|---|---|---|
| V7 | Examination Intelligence | Exam metadata, objectives, rubrics, quality diagnostics |
| V8 | Clinical Evidence Governance | Evidence registry, review workflow, freshness and release gates |
| V9 | Semantic Exam Intelligence | Local similarity analysis, duplicate candidates, answer diagnostics, human review queue |
| V10 | Specialty Curriculum Mapping | Competency mapping, verified-vs-suggested coverage, curriculum gap reports |
| V11 | Adaptive Learning & Assessment | Consent-aware learner progress, explainable mastery estimates, configurable spaced repetition, privacy-suppressed cohort analytics |
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
- [x] Add compile and deterministic self-tests; CI result is tracked on the V10 PR.
- [ ] Validate against at least one faculty-approved real specialty curriculum.

## V11 implementation

- [x] Consume V10 curriculum and only faculty-verified card-to-competency mappings.
- [x] Require explicit per-learner consent and pseudonymous UUIDv4 identifiers.
- [x] Enforce configurable event retention during analytics generation.
- [x] Reject direct identifiers and free-text answers from learner/attempt records.
- [x] Calculate explainable Beta-prior mastery estimates.
- [x] Configure mastery bands and spaced-repetition intervals.
- [x] Suppress cohort metrics below a configurable minimum cohort size.
- [x] Export learner progress, cohort analytics, review schedule, and manifest.
- [x] Keep source cards, curriculum, mapping, and evidence files read-only.
- [ ] Faculty/assessment expert validation of the educational model and real data.

Mastery estimates are descriptive learning signals, not proof of clinical
competence. Consent flags and pseudonymous IDs are validated structurally but
cannot establish legal consent or guarantee anonymity. Operators remain
responsible for access controls, source deletion, and applicable policies.

## Governance

All heuristic mappings remain suggestions until reviewed. Medical claims and
curriculum decisions require qualified human review. A passing software test
suite does not establish clinical accuracy or educational validity.
