# Usiphus V16 — One Governed Release

V16 integrates V15 evidence governance into the existing V14 release command. It preserves the V14 CLI/config contract and the independent V15 CLI. No new runtime dependency or hosted service is required.

See the V16 section in [the V14 unified release guide](V14_UNIFIED_RELEASE_PLATFORM.md#v16--one-governed-release) for configuration and migration details.

## Acceptance boundaries

- V15 runs as an explicit configured stage after V13 validation.
- Strict mode requires registered evidence sources; legacy mode permits free-text migration references without promoting them to registered evidence.
- V15 inputs are snapshotted before analysis and are not modified.
- Mandatory V15 findings abort before publication and preserve the previous known-good bundle.
- One release summary/report and one manifest capture V12/V13/V15 outcomes, policy, engine versions and hashes.
- Synthetic tests cover source lifecycle, missing IDs, supersession cycles, reviewer quorum, legacy migration and rollback.

Automated checks do not establish clinical truth, source quality, identity, educational validity, learner competence, anonymization, or legal compliance. Faculty approval remains a separate human decision.
