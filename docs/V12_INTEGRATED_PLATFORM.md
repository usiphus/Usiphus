# Usiphus V12 — Integrated Specialty Learning Platform

## Scope

V12 is a local batch integration layer joining the existing versioned engines:

- **V8:** clinical evidence registry and its draft → in-review → approved → published audit workflow.
- **V10:** specialty curriculum, card-to-competency mappings, and verified/pending/suggested/invalid states.
- **V11:** consent-aware learner progress, cohort analytics with suppression, and spaced-repetition schedule.

It creates one specialty-level platform report, a faculty review work queue, a release checklist, and an input-hash manifest. It does not replace the V8/V10/V11 engines, run a hosted LMS, authenticate faculty, or edit source files.

## Inputs

The command requires these existing artifacts:

| Argument | Expected source |
|---|---|
| \`--cards\` | V8/V9-compatible cards JSON (list or \`{"cards":[...]}\`) |
| \`--curriculum\` | V10 curriculum JSON |
| \`--mapping\` | V10 \`curriculum_coverage.json\` |
| \`--evidence\` | V8 evidence registry (list or \`{"claims":[...]}\`) |
| \`--learner-progress\` | V11 \`learner_progress.json\` |
| \`--cohort-analytics\` | V11 \`cohort_analytics.json\` |
| \`--review-schedule\` | V11 \`review_schedule.csv\` |

All artifacts must describe the same specialty and stable card/competency IDs. Cards must have a specialty matching the curriculum. Verified mapping rows must reference known cards and competencies. Invalid V10 mapping rows are retained as faculty repair work items; suggested and pending rows never count as verified coverage.

V8 evidence claims are checked for card linkage, required provenance fields, allowed workflow transitions, reviewer/audit metadata, and review freshness. A current approved/published claim is required for every card to pass the evidence release gate. The age limit defaults to 730 days.

## Run

\`\`\`bash
python usiphus_v12.py --self-test
python usiphus_v12.py \\
  --cards cards.json \\
  --curriculum curriculum.json \\
  --mapping build-v10/curriculum_coverage.json \\
  --evidence evidence_registry.json \\
  --learner-progress build-v11/learner_progress.json \\
  --cohort-analytics build-v11/cohort_analytics.json \\
  --review-schedule build-v11/review_schedule.csv \\
  --output build-v12
\`\`\`

Optional reproducibility and evidence freshness controls:

\`\`\`bash
--as-of 2026-10-31 --max-evidence-age-days 730
\`\`\`

## Outputs

- \`platform_report.json\`: specialty curriculum coverage, evidence state, learner/cohort summary counts, and faculty queue totals.
- \`faculty_review_queue.csv\`: pending/suggested mapping reviews, invalid mapping repairs, evidence reviews, missing evidence, and required curriculum gaps.
- \`release_checklist.json\`: explicit blockers and a software-level release readiness result.
- \`v12_manifest.json\`: SHA-256 and byte size for each input plus output inventory and privacy notes.

The V12 summary does not copy individual V11 learner records or learner pseudonyms. The V11 source progress file still contains sensitive pseudonymous educational records and must remain access-controlled. Cohort suppression is checked structurally; it is not a guarantee against re-identification.

## Release gate and governance

A report is marked \`release_ready: true\` only when every required competency has a verified mapping, every card has current approved/published evidence, and no evidence-registry validation findings remain. This is a metadata/workflow gate only. It does not establish clinical truth, source quality, educational validity, or learner competence.

V12 does not approve faculty mappings or evidence, and it never changes the source cards, curriculum, mapping, evidence registry, or V11 artifacts. Faculty must review the CSV work queue and update the source artifacts through their existing governed workflows before rebuilding the report. V12 is not a faculty identity/authentication system; reviewer names and audit fields are not cryptographic proof of identity.

No hosted database, external API, paid service, or optional ML dependency is required.
