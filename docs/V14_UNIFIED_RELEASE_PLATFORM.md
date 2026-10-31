# Usiphus V14.1 — Unified Release Platform

## Purpose

V14 adds a single configuration-driven release command over the existing V8–V13 engines. It runs the V8–V13 self-test suites by default, creates the V12 integrated specialty bundle, validates it with V13, then publishes a provenance manifest and human-readable release report.

V14 is an orchestration layer, not a replacement for the earlier engines. It does not approve clinical content, authenticate faculty, or certify clinical truth, educational effectiveness, learner competence, anonymization, or legal compliance.

## Requirements

Python 3.11 or later; no third-party Python packages; the V8–V13 source files; and the seven source artifacts listed below.

## Configuration

Copy examples/v14.config.example.json and update the seven input paths. Paths are resolved relative to the config file.

| Key | Description |
|---|---|
| schema_version | Required integer 1 |
| inputs.cards | V8/V9 cards JSON |
| inputs.curriculum | V10 specialty curriculum JSON |
| inputs.mapping | V10 curriculum coverage JSON |
| inputs.evidence | V8 evidence registry JSON |
| inputs.learner_progress | V11 learner progress JSON |
| inputs.cohort_analytics | V11 cohort analytics JSON |
| inputs.review_schedule | V11 review schedule CSV |
| output_dir | Dedicated output directory |
| as_of | Optional fixed YYYY-MM-DD date for evidence freshness |
| max_evidence_age_days | Optional positive integer; default 730 |
| run_regression_tests | Optional boolean; default true |

Run:

```bash
python usiphus_v14.py --config path/to/v14.config.json
```

The example config points to placeholder source artifacts. Replace those paths before running it.

## Pipeline

1. Validate the config, required inputs, file sizes, and options.
2. Run V8, V9, V10, V11, V12, and V13 self-tests (enabled by default).
3. Stage read-only copies of the seven inputs under V13's canonical filenames.
4. Run V12 integration and generate its platform report, faculty queue, checklist, and manifest.
5. Run V13 against the staged inputs and V12 outputs.
6. Combine both results into the V14 summary, Markdown report, and SHA-256 manifest.
7. Publish the complete output directory only after all required stages finish. A failed run leaves the previous published directory intact.

## Outputs

- v12/: V12 report, faculty review queue, release checklist, and manifest.
- v13_validation_report.json: V13 software-assurance report.
- release_summary.json and release_report.md: V14 release status and stage results.
- v14_manifest.json: config/input/output hashes, byte counts, engine versions, and release status.

Hashes support audit and change detection. Timestamps vary, so V14 does not claim that complete bundles are byte-identical between runs.

## Exit codes

- 0: V12 release gate ready and V13 validation passed.
- 1: pipeline completed and V13 passed, but the V12 release checklist is blocked. Review the checklist and faculty queue.
- 2: config, execution, or validation failure. Existing published output is preserved.

## Governance and privacy

A ready status is a software/workflow result only. Faculty must review clinical claims, references, currency, curriculum mappings, and queued work. Learner progress and review schedules contain pseudonymous identifiers and remain sensitive educational records; apply appropriate access controls and retention. UUID formatting is not anonymization. V14 never automatically approves content.

## V14.1 hardening and end-to-end tests

V14.1 rejects output paths that would replace the repository/config directory or
an ancestor containing the configuration or any of the seven input artifacts.
Keep the output directory separate from all source artifacts.

Run the synthetic integration suite from the repository root:

```bash
python -m unittest discover -s tests -p "test_v14_e2e.py" -v
```

The tests use fictional data and verify:
- A complete V12 → V13 → V14 run publishes a ready bundle and all expected files.
- The seven source artifacts remain unchanged and the learner UUID is absent from V12 outputs.
- A failed subsequent run preserves the previously published bundle.
- An unsafe output directory is rejected before publication.

GitHub Actions runs this suite in addition to compilation and the V8–V14
self-tests. These tests validate software integration behavior only; they do
not validate clinical claims, educational effectiveness, or production data.
