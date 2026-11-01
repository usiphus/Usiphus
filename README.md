# Usiphus

**A local-first medical education content, curriculum, evidence-governance, and release toolkit.**

Usiphus helps structure medical teaching material and provides explicit workflows for evidence provenance, specialty curriculum mapping, learner progress, privacy-aware reporting, and software-level release assurance.

> Automated checks assess structure, metadata, workflow state, and selected consistency/privacy properties. They do not establish clinical truth, current guideline recommendations, educational effectiveness, learner competence, anonymization, or legal compliance. Clinical material requires qualified human review.

## Version map

| Version | Capability |
|---|---|
| V8 | Evidence registry, provenance, review workflow, and governed exports |
| V9 | Semantic duplicate candidates and answer diagnostics |
| V10 | Specialty curriculum mapping and weighted coverage |
| V11 | Consent-aware learner analytics and spaced-repetition schedules |
| V12 | Integrated specialty report, faculty queue, and release checklist |
| V13 | Cross-artifact validation, manifest integrity, and selected privacy checks |
| **V14** | Configuration-driven release orchestration, regression gate, provenance manifest, and atomic bundle publication |
| **V15** | Evidence-source registry, supersession checks, reviewer quorum, hash-chained audit events, and source-to-card impact index |
| **V16** | One governed release: V15 evidence governance integrated into V14 orchestration |
| **V17** | Stable data contracts, explicit migrations, curriculum quality intelligence, change-impact reports, dashboard and faculty pilot workflow |
| **V18** | Evidence lifecycle operations, source-change detection, prioritized faculty review, and evidence-to-learning impact graph |

## Quick start

Requires Python 3.11 or later. The core project uses the Python standard library only. CI is configured for Python 3.11–3.13 on Linux, macOS and Windows; see the workflow in .github/workflows/ci.yml. Configuring a matrix does not mean every job has already passed.

## Recommended workflows

**Governed release (V16):**

    python usiphus_v14.py --config path/to/v14.config.json

**Curriculum quality and change impact (V17):**

    python usiphus_v17.py --curriculum examples/v17/curriculum.json --cards examples/v17/cards.json --mapping examples/v17/mapping.json --previous-curriculum examples/v17/curriculum.previous.json --assessments examples/v17/assessments.json --osce examples/v17/osce.json --output-dir build-v17

**Contract validation and explicit migration:**

    python usiphus_v17.py --validate examples/v17/cards.json --contract-type card
    python usiphus_v17.py --migrate legacy-curriculum.json --contract-type curriculum --migration-output curriculum.v17.json
    python usiphus_v17.py --curriculum examples/v17/curriculum.json --cards examples/v17/cards.json --pilot-metrics pilot.csv --output-dir build-v17

**Independent source governance (V15):**

    python usiphus_v15.py --sources sources.json --claims claims.json --require-registered-sources --output-dir build-v15

## Tests

Run all engine self-tests and unit/integration tests:

    python usiphus_v7.py --self-test
    python usiphus_v8.py --self-test
    python usiphus_v9.py --self-test
    python usiphus_v10.py --self-test
    python usiphus_v11.py --self-test
    python usiphus_v12.py --self-test
    python usiphus_v13.py --self-test
    python usiphus_v14.py --self-test
    python usiphus_v15.py --self-test
    python usiphus_v17.py --self-test
    python -m unittest discover -s tests -v

## V18 — Evidence lifecycle operations

    python usiphus_v18.py --self-test
    python usiphus_v18.py --sources examples/v18/sources.json --claims examples/v18/claims.json --cards examples/v18/cards.json --assessments examples/v18/assessments.json --osce examples/v18/osce.json --release-links examples/v18/releases.json --as-of 2026-11-01T00:00:00Z --output-dir build-v18

V18 detects source metadata/content changes, duplicate-source candidates, stale reviews and jurisdiction mismatches; prioritizes human review; and traces source-to-claim-to-card-to-competency-to-assessment/OSCE impact. It is offline by default and never auto-edits clinical content.

## V17 outputs

- v17_curriculum_quality.json: verified coverage, gaps, candidates, conflicts and breakdowns.
- v17_curriculum_impact.json: changed competencies and linked downstream artifacts.
- v17_faculty_review_queue.json: mapping review/repair and curriculum gaps.
- v17_dashboard.html: self-contained static dashboard.
- v17_pilot_metrics_template.csv: blank template for an institution-led pilot.
- v17_manifest.json: input/output hashes and summary.
- v17_pilot_metrics_summary.json: consent-filtered workflow measures when pilot data are supplied.

V17 also generates a competency-to-objective/card/assessment/OSCE dependency graph and exact normalized-prompt duplicate candidates for human review.

See docs/V17_STABLE_CONTRACTS_AND_CURRICULUM_INTELLIGENCE.md and docs/V17_PILOT_PROTOCOL.md.

## Privacy and governance

Learner progress and review schedules contain pseudonymous learner identifiers and remain sensitive educational records. Apply access controls, secure transfer/backup, and retention policies. UUID formatting is not anonymization. V14–V17 do not authenticate faculty or automatically approve evidence, mappings, clinical content or curriculum decisions.

## Documentation

- Product and engineering roadmap: ROADMAP.md
- V17 stable contracts and curriculum intelligence: docs/V17_STABLE_CONTRACTS_AND_CURRICULUM_INTELLIGENCE.md
- V17 faculty pilot protocol: docs/V17_PILOT_PROTOCOL.md
- V16 governed release: docs/V16_GOVERNED_RELEASE.md
- V15 evidence governance: docs/V15_EVIDENCE_GOVERNANCE.md
- V14 unified release platform: docs/V14_UNIFIED_RELEASE_PLATFORM.md
- Safety, privacy, and security checklist: docs/SAFETY_AND_PRIVACY.md
