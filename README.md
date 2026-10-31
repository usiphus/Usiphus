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

## Quick start

Requires Python 3.11 or later. The core project uses the Python standard library only.

Run version self-tests:

```bash
python usiphus_v8.py --self-test
python usiphus_v9.py --self-test
python usiphus_v10.py --self-test
python usiphus_v11.py --self-test
python usiphus_v12.py --self-test
python usiphus_v13.py --self-test
python usiphus_v14.py --self-test
```

For an integrated release:

1. Prepare the seven source artifacts described in the [V14 guide](docs/V14_UNIFIED_RELEASE_PLATFORM.md).
2. Copy [the sample configuration](examples/v14.config.example.json) and update its input paths.
3. Run:

```bash
python usiphus_v14.py --config path/to/v14.config.json
```

Paths in the configuration are relative to the config file. V14 runs V8–V13 regression suites by default, creates V12 outputs, validates them with V13, and publishes a complete release bundle.

## V14 outputs

- `v12/`: platform report, faculty review queue, release checklist, and manifest.
- `v13_validation_report.json`: V13 software-assurance report.
- `release_summary.json` and `release_report.md`: V14 status and stage results.
- `v14_manifest.json`: config/input/output hashes, byte counts, engine versions, and release status.

Exit code 0 means the V12 metadata gate is ready and V13 validation passed. Exit code 1 means the pipeline completed but the V12 release gate is blocked. Exit code 2 means configuration, execution, or validation failed. A failed run does not replace the previous published bundle.

## Privacy and governance

Learner progress and review schedules contain pseudonymous learner identifiers and remain sensitive educational records. Apply access controls, secure transfer/backup, and retention policies. UUID formatting is not anonymization. V14 does not authenticate faculty or automatically approve evidence, mappings, or clinical content.

## Documentation

- [V14 unified release platform](docs/V14_UNIFIED_RELEASE_PLATFORM.md)
- [V13 validation engine](docs/V13_VALIDATION_ENGINE.md)
- [V12 integrated platform](docs/V12_INTEGRATED_PLATFORM.md)
