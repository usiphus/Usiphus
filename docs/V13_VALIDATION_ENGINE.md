# Usiphus V13 — Clinical & Educational Validation Engine

## Purpose and safety boundary

V13 adds a deterministic assurance pass over the seven V12 source artifacts and the four V12 outputs. It checks data contracts, cross-file consistency, schedule integrity, cohort suppression, manifest integrity, and accidental disclosure of pseudonymous learner IDs.

V13 is a **software validation layer**, not a clinical guideline reviewer, educational accreditation process, identity provider, privacy-law compliance tool, or proof of learner competence. A passed report means only that the implemented checks passed for the supplied files.

## Inputs

Place the original files used by V12 together in one directory:

| File | Purpose |
|---|---|
| \`cards.json\` | V8/V9 cards |
| \`curriculum.json\` | V10 specialty curriculum |
| \`mapping.json\` | V10 curriculum coverage report |
| \`evidence.json\` | V8 evidence registry |
| \`learner_progress.json\` | V11 pseudonymous learner progress |
| \`cohort_analytics.json\` | V11 cohort summary |
| \`review_schedule.csv\` | V11 review schedule |

The names are the V13 input-directory contract. They can be copied from their source locations; do not modify them between the V12 run and V13 validation. V13 verifies the V12 manifest hashes against these exact input files.

The V12 output directory must contain \`platform_report.json\`, \`faculty_review_queue.csv\`, \`release_checklist.json\`, and \`v12_manifest.json\`.

## Run

First generate V12 outputs using the same seven input files:

\`\`\`bash
python usiphus_v12.py --self-test
python usiphus_v12.py \
  --cards source-artifacts/cards.json \
  --curriculum source-artifacts/curriculum.json \
  --mapping source-artifacts/mapping.json \
  --evidence source-artifacts/evidence.json \
  --learner-progress source-artifacts/learner_progress.json \
  --cohort-analytics source-artifacts/cohort_analytics.json \
  --review-schedule source-artifacts/review_schedule.csv \
  --output build-v12
\`\`\`

Then run V13:

\`\`\`bash
python usiphus_v13.py --self-test
python usiphus_v13.py \
  --input-dir source-artifacts \
  --output-dir build-v12 \
  --report build-v13/v13_validation_report.json
\`\`\`

Exit code \`0\` means validation passed. Exit code \`2\` means validation failed or required arguments are missing. Failures are logged with the relevant artifact/field; no source file is modified.

## Validation coverage

- Curriculum: specialty, non-empty competencies, unique IDs, boolean required flags, finite positive weights.
- Learner progress: specialty consistency, canonical UUIDv4 pseudonyms, unique learner IDs, unique known competency rows, mastery estimates in [0, 1], and rejection of direct-identifier/free-text field names.
- Cohort analytics: specialty, minimum cohort size, unique known competency rows, suppression flags, absence of metrics in suppressed rows, and minimum count for unsuppressed rows.
- Review schedule: required columns, UUIDv4 learner IDs, known competencies, unique learner/competency pairs, timezone-aware ISO-8601 due dates, positive integer intervals, supported V11 statuses, and optional mastery estimates in [0, 1].
- V12 outputs: schema/version checks, matching report/checklist release sections, required output inventory, manifest input hashes and byte counts, queue header, release blocker consistency, and no learner pseudonym appearing in V12 output files.

## Privacy notes and limitations

- V11 learner progress and review schedules contain pseudonymous learner IDs and remain sensitive educational records. Restrict access, transport, backups, and retention accordingly.
- UUIDv4 is a format check, not anonymization. It does not prevent linkage or re-identification.
- V13 scans structured field names for common direct identifiers and checks that learner UUIDs do not appear in V12 outputs. It cannot detect every identifier hidden in arbitrary text, filenames, logs, metadata, or external systems.
- Cohort suppression checks the declared minimum cohort size and the absence of selected metrics in suppressed rows. It is not a formal statistical disclosure-control or repeated-query attack assessment.
- Hashes prove that supplied files match the V12 manifest; they do not prove that the source data are true, current, authorized, or clinically correct.
- Evidence quality, guideline jurisdiction/currentness, faculty identity, educational validity, psychometric quality, and legal compliance require separate qualified human or institutional review.

## Reproducibility

The V12 manifest must refer to the exact source artifacts used for the V12 run. If an input is edited after that run, V13 intentionally rejects the bundle because its hash/byte count no longer matches. Re-run V12 after any approved source change, then run V13 again.

V13 writes one JSON validation report to the requested path. It does not change V8–V12 inputs or generated outputs. No hosted service, external API, paid dependency, or non-standard Python package is required.
