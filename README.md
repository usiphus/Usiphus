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
| **V15** | Evidence-source registry, supersession checks, reviewer quorum, hash-chained audit events, and source-to-card impact index |\n| **V16** | One governed release: V15 evidence governance integrated into V14 orchestration, with explicit migration/strict modes and a release-level manifest |

## Quick start

Requires Python 3.11 or later. The core project uses the Python standard library only. CI is configured for Python 3.11–3.13 on Linux, macOS and Windows; see the [workflow](.github/workflows/ci.yml). Configuring a matrix does not mean every job has already passed.

## Recommended workflows

- **Governed release (V16):** use the compatible V14 command to run V8–V13 validation and the configured V15 governance stage as one release decision:

  ```bash
  python usiphus_v14.py --config path/to/v14.config.json
  ```

- **Independent source governance:** use V15 to analyze registered sources and claims:

  ```bash
  python usiphus_v15.py --sources sources.json --claims claims.json --require-registered-sources --output-dir build-v15
  ```

- **Individual engines:** V7–V13 remain available as standalone tools for their specific workflows. Run their self-tests before relying on changes.

Run all version self-tests:

```bash
python usiphus_v7.py --self-test
python usiphus_v8.py --self-test
python usiphus_v9.py --self-test
python usiphus_v10.py --self-test
python usiphus_v11.py --self-test
python usiphus_v12.py --self-test
python usiphus_v13.py --self-test
python usiphus_v14.py --self-test
python usiphus_v15.py --self-test
python -m unittest discover -s tests -v
```

For an integrated release:

1. Prepare the seven source artifacts described in the [V14 guide](docs/V14_UNIFIED_RELEASE_PLATFORM.md).
2. Copy [the sample configuration](examples/v14.config.example.json) and update its input paths.
3. Run:

```bash
python usiphus_v14.py --config path/to/v14.config.json
```

Paths in the configuration are relative to the config file. The V16 implementation retains the usiphus_v14.py entry point and V14 config compatibility. When V15 source and claim files are configured, they are snapshotted and evaluated as an explicit stage; strict mode requires registered sources, while legacy mode permits documented free-text references during migration.

## V14 outputs

- `v12/`: platform report, faculty review queue, release checklist, and manifest.
- `v13_validation_report.json`: V13 software-assurance report.
- `release_summary.json` and `release_report.md`: V14 status and stage results.
- `v14_manifest.json`: the single release manifest (legacy filename retained for compatibility), including V15 policy, engine versions, source snapshot hashes, output hashes, and V15 report hashes.\n- `v15/`: V15 governance JSON, CSV findings, and Markdown report when the stage is configured.

Exit code 0 means the release is ready (or ready with explicitly non-blocking V15 warnings). Exit code 1 means the V12 gate is blocked. Exit code 2 means configuration, execution, validation, or a mandatory V15 governance gate failed. Mandatory V15 failures do not publish and preserve the previous known-good bundle.

## Privacy and governance

Learner progress and review schedules contain pseudonymous learner identifiers and remain sensitive educational records. Apply access controls, secure transfer/backup, and retention policies. UUID formatting is not anonymization. V14 does not authenticate faculty or automatically approve evidence, mappings, or clinical content.

## Documentation

- [Product and engineering roadmap](ROADMAP.md)
- [V15 evidence governance](docs/V15_EVIDENCE_GOVERNANCE.md)
- [V16 governed release](docs/V16_GOVERNED_RELEASE.md)\n- [V14 unified release platform](docs/V14_UNIFIED_RELEASE_PLATFORM.md)
- [V13 validation engine](docs/V13_VALIDATION_ENGINE.md)
- [V12 integrated platform](docs/V12_INTEGRATED_PLATFORM.md)
- [Safety, privacy, and security checklist](docs/SAFETY_AND_PRIVACY.md)
