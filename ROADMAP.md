# Usiphus Product & Engineering Roadmap

**Status:** Proposed direction, based on repository review  
**Research snapshot:** 2026-11-01  
**Current baseline:** `main` at `24e5d54e2cc8581f56bf660580cbbae09f9dfe5b`

This roadmap is an evidence-informed plan, not a promise that future capabilities already exist. Milestones are capability releases, not a reason to add a new engine file for every version. Each milestone must solve a demonstrated user or engineering problem and pass its acceptance gates before the next one begins.

## 1. Product direction

Usiphus should become a **local-first, evidence-governed medical education production system**. It should help a specialty team move from structured teaching material to curriculum mapping, faculty review, assessments, and reproducible release packages while keeping clinical and educational decisions under qualified human control.

The product should optimize for:

1. Clinical evidence traceability and timely human review.
2. Curriculum coverage and assessment alignment.
3. Safe, reproducible production of teaching artifacts.
4. Privacy-conscious handling of learner records.
5. Low operational cost, offline-capable workflows, and minimal dependencies.
6. Clear boundaries between automated software checks and clinical/educational approval.

### Explicit non-goals

- Not a clinical decision-support system or autonomous guideline author.
- Not an identity provider, hosted LMS, accreditation service, or proof of learner competence.
- Not a substitute for faculty review, institutional privacy/security controls, or psychometric expertise.
- No mandatory paid LLM, cloud database, hosted API, or third-party Python dependency.
- No large rewrite of V7–V15 just to create a cleaner architecture.

## 2. Verified baseline (main branch)

The repository review found:

| Area | Verified state |
|---|---|
| V14 | V14.1 unified release orchestration is merged |
| V14 hardening | Synthetic end-to-end, protected-path and rollback tests exist |
| V15 | Evidence-source registry, supersession checks, reviewer quorum, hash-chain verification and source-to-card impact index exist |
| V14/V15 integration | **Not integrated:** V14 orchestrates V8–V13; V15 is a separate command/report |
| CI | Python 3.11–3.13 on Ubuntu, macOS and Windows (9-job matrix) |
| Latest main CI at snapshot | 9/9 jobs succeeded for `24e5d54` |
| Dependencies | Core is documented as Python standard-library only |
| Governance boundary | Existing docs correctly state that software checks do not prove clinical truth, educational effectiveness, anonymization, competence or legal compliance |
| Repository state | No open pull requests at snapshot |

The version history is not fully reconstructable from the current source tree: the history index explicitly says distinct V1/V2 source archives were not recovered. Do not invent missing early-version capabilities in future documentation.

## 3. Strategic sequence

### Phase 0 — Maintain the proven baseline (continuous; no new version required)

**Objective:** Keep V14.1 and V15 trustworthy while planning the next release.

Work:
- Preserve the V14 synthetic end-to-end, unsafe-output-path and failed-publication rollback tests.
- Preserve V15 schema, source lifecycle, supersession-cycle, reviewer-quorum, audit-chain and impact-index tests.
- Ensure changes to V15 contracts trigger the relevant V15 and integrated regression tests.
- Verify every release against the actual CI run for its commit; a configured matrix is not proof that all jobs passed.
- Keep examples synthetic and never place real patient or learner records in fixtures.
- Document known limitations and compatibility contracts.

Exit criteria:
- All required tests pass on the release commit.
- No unexplained test skips or ignored failures.
- V14 publication failure leaves the last successful bundle intact.
- V15 governance findings are deterministic for fixed inputs and `as_of` date.
- The README, CLI help and version-specific guides agree.

### V16 — Governed Release Integration (highest priority)

**Problem:** V15 currently runs independently, so a V14 release does not automatically apply V15's richer source lifecycle and claim-to-card impact checks.

Deliverables:
- Add an explicit V15 stage to V14's configured pipeline, with a documented opt-in/strictness transition for legacy reference strings.
- Add configuration for source registry, claim mapping, `as_of`, maximum source age, and minimum distinct reviewers. Reuse V15 policy semantics; do not duplicate or fork them.
- Build one release-level report that joins V12/V13 findings with V15 source/claim findings and the source-to-card impact index.
- Block publication when configured mandatory governance checks fail; preserve a clearly labelled warning mode for migration from legacy references.
- Include V15 inputs, policy, engine versions, and report hashes in the release manifest.
- Keep the seven V14 legacy inputs working during migration; do not silently promote free-text references to registered evidence.
- Add synthetic end-to-end tests for active, superseded and withdrawn sources; missing source IDs; supersession cycles; insufficient reviewer quorum; legacy references; and rollback after a late V15 failure.

Exit criteria:
- A single V16 command produces one coherent V12/V13/V15 release decision and manifest.
- A failed V15 stage cannot replace the last successful published bundle.
- Legacy inputs remain readable and migration findings are explicit.
- Tests prove source files are unchanged and sensitive learner identifiers do not leak into release outputs.
- Faculty approval remains a human action; metadata status is not clinical approval.

### V17 — Stable Core, Contracts & CLI

**Problem:** The versioned standalone scripts are useful for history and compatibility, but the platform needs stable interfaces before the feature surface expands.

Deliverables:
- Define canonical internal models and versioned JSON Schemas for cards, claims, sources, curriculum, mappings, assessments, and release manifests.
- Define explicit input/output contracts, error codes, compatibility rules, and schema migration policy.
- Extract shared utilities incrementally into a small internal package (for example, `usiphus/`); keep existing `usiphus_vN.py` commands as compatibility wrappers.
- Add one documented CLI entry point with subcommands for validate, govern, build, report, and migrate.
- Add schema validation tests and golden fixtures; test old-to-new migration without modifying source files.
- Add structured logs with safe defaults and no learner identifiers or source text in routine logs.
- Keep the standard-library-only baseline unless a dependency has a documented benefit, license, maintenance plan, and fallback.

Exit criteria:
- Existing V7–V16 workflows still run through compatibility wrappers.
- Versioned schemas reject malformed input with actionable errors.
- Migration is explicit, reversible where practical, and never silently changes clinical meaning.
- Shared core extraction does not cause a wholesale rewrite.

### V18 — Evidence Lifecycle Operations

**Problem:** V15 governs registered metadata but explicitly does not fetch guidelines, appraise source quality, or determine clinical meaning.

Deliverables:
- Add controlled source intake from user-supplied files and URLs, recording canonical identifier, title, publisher, jurisdiction, publication/update dates, version, retrieval date, and content hash where available.
- Add duplicate/canonical-source candidates and change-detection reports; require human confirmation before superseding a source or changing a claim.
- Add evidence review queues sorted by withdrawn/superseded status, source changes, review age, jurisdiction mismatch, and affected-card count.
- Track claim-level review decisions and rationale, with reviewer identity authenticated only if a real identity mechanism is later introduced.
- Separate source metadata, source-quality appraisal, and clinical interpretation into distinct review fields.
- Keep retrieval optional and auditable; do not make live network access a requirement for deterministic builds.
- Provide a manual offline import/export path.

Exit criteria:
- Every registered claim has an inspectable provenance path to a specific source/version/jurisdiction.
- Source changes produce deterministic impact candidates, not automatic clinical edits.
- Network failure does not corrupt or block offline builds using already registered sources.
- Qualified faculty can accept, reject, or defer every suggested change.

### V19 — Assessment & Educational Measurement

**Problem:** Assessment generation is valuable only when items are aligned, reviewed, and measured appropriately. Heuristic item checks alone cannot establish validity or reliability.

Deliverables:
- Add assessment blueprints mapping specialty, competency, learning objective, topic, cognitive level, and intended difficulty.
- Add structured SBA/MCQ and clinical-vignette schemas with answer rationale, source links, and reviewer status.
- Add deterministic checks for missing stems/options/keys/rationales, multiple correct answers, ambiguous wording flags, duplicate candidates, and blueprint coverage.
- Add optional item statistics (difficulty, discrimination, distractor performance) only from explicitly supplied, sufficiently described response datasets.
- Record cohort size, missingness, assessment version, and limitations alongside statistics; suppress or avoid subgroup reports where privacy or sample size makes them unsafe.
- Keep psychometric interpretation, standard setting, fairness review, and item retirement under qualified human oversight.
- Add exportable exam blueprint and item-review reports.

Exit criteria:
- Every released item is mapped to a blueprint and has human approval.
- Statistics are reproducible and always accompanied by dataset and sample-size context.
- No automated score or heuristic is described as proof of validity, reliability, fairness, or learner competence.
- Synthetic test responses are clearly marked and never mixed with real outcomes.

### V20 — Faculty Authoring & Accessible Publishing

**Problem:** Faculty need a usable path from governed source content to teaching artifacts without weakening provenance or review.

Deliverables:
- Start with a local-first authoring workflow and CLI/API contracts; build a GUI only after a validated user need and workflow are documented.
- Provide structured editing for cards, cases, objectives, evidence links, curriculum mappings, and assessment items.
- Add review diffs, version history, approval queues, comments, and release previews.
- Generate PPTX, PDF, HTML and structured-data exports from the same governed content model.
- Add accessibility checks for heading order, reading order, alternative text, table structure, contrast where measurable, and keyboard navigation in any UI.
- Use WCAG 2.2 AA as a target for web interfaces; separately test exported documents for their own accessibility requirements.
- Keep the authoring source format open and exportable; never make a proprietary hosted service necessary to recover project content.

Exit criteria:
- Faculty can trace each published teaching element to its source claims, mapping and approval state.
- Exports are reproducible from a fixed content revision and configuration, with variable timestamps clearly separated.
- Accessibility findings are visible and reviewable; automated checks do not claim complete accessibility conformance.
- The local workflow works without paid services or network access for already available content.

### V21 — Interoperability & Deployment Options (only when demand is demonstrated)

**Problem:** Interoperability can add complexity and privacy risk; implement only formats needed by actual institutions.

Candidate deliverables:
- Stable import/export adapters for agreed curriculum, question-bank and learning-event formats.
- Optional standards-based integration selected with pilot institutions; avoid speculative FHIR integration unless a genuine health-record use case exists.
- Portable release bundles, documented backup/restore, retention/deletion workflows, and migration tools.
- Optional authenticated multi-user deployment only after threat modelling, identity, authorization, key management, audit, incident response and institutional ownership are specified.
- A documented plugin/adapter interface with strict input validation and least privilege.

Exit criteria:
- Every adapter has conformance fixtures and round-trip tests where possible.
- Data minimization and access control are reviewed before real learner records are exchanged.
- Local/offline mode remains supported.
- No deployment model is described as compliant or secure solely because its tests pass.

## 4. Cross-cutting engineering requirements

These requirements apply to every milestone, not only V17 or V21.

### Clinical and educational governance
- Qualified reviewers remain responsible for clinical accuracy, source interpretation, curriculum meaning, assessment quality and release approval.
- Distinguish machine findings, reviewer decisions, and release policy in every report.
- Keep jurisdiction and source version explicit.
- Never convert similarity, polarity, freshness, or authority metadata into an automated clinical-quality score.
- Record uncertainty and unresolved conflicts rather than hiding them.

### Privacy and security
- Classify data before adding fields; collect the minimum necessary.
- Treat pseudonymous learner records, audit ledgers, salts, backups and exports as sensitive.
- Define retention, deletion, access, backup, restore and incident procedures with the data-owning institution.
- Threat-model new importers, output paths, plugins, logs, and multi-user features.
- Protect signing keys separately from source code and generated bundles.
- Treat hash chains as tamper-evident only within their stated limits; use signatures and independently controlled anchors if stronger audit guarantees become a real requirement.
- Follow a risk-based secure development process informed by NIST SSDF; monitor the draft SSDF 1.2 rather than claiming it is final.

### Software supply chain and releases
- Pin GitHub Actions to reviewed full commit SHAs when practical; keep action updates deliberate and reviewable.
- Use least-privilege workflow permissions.
- Add a dependency inventory/SBOM when distributable artifacts or external dependencies are introduced.
- Produce release provenance describing source revision, build workflow, configuration and artifact hashes; adopt SLSA/in-toto-compatible attestations if release distribution warrants it.
- Keep secrets out of repository files, fixtures, logs and artifacts.
- Test backup restoration and failed-release rollback, not just successful publication.

### Compatibility and maintainability
- Preserve V7–V15 as historical/compatibility interfaces until a deprecation policy is published.
- Add schema versions and explicit migrations before changing formats.
- Prefer small, independently reviewable PRs.
- Do not add a new versioned engine if the work is a bug fix, documentation correction, or maintenance task.
- Keep the canonical product direction in this file and historical version plans in the history archive.

## 5. Release gates (definition of done)

A milestone is not complete because a self-test returns zero. It must satisfy all applicable gates:

| Gate | Required evidence |
|---|---|
| Correctness | Unit tests, regression tests, negative tests and representative end-to-end fixtures |
| Safety | Human-review boundary and failure modes documented; no automated clinical approval |
| Data integrity | Source immutability, safe path handling, atomic publication and rollback verified |
| Privacy | Data inventory, output/log review, disclosure tests and retention implications documented |
| Compatibility | Supported schemas/CLIs and migration tests pass |
| Reproducibility | Fixed inputs/configuration produce stable semantic outputs; timestamps and nondeterministic fields documented |
| Security | Threat model updated for new attack surfaces; least privilege and secret handling checked |
| Usability | Example workflow, CLI help, errors and migration guide updated |
| Release provenance | Commit, configuration, input/output hashes and test evidence recorded |
| Human acceptance | Relevant faculty/operator review completed before real clinical or learner data use |

For each release, publish a concise report listing passed, failed, skipped and not-applicable checks. Never hide skips inside an overall green status.

## 6. CI strategy and cost control

Current CI runs a 3 × 3 Python/OS matrix. The latest main-branch run at the snapshot passed all nine jobs. Keep this broad compatibility signal, but avoid duplicating the same expensive work across multiple overlapping workflows.

Recommended policy:
- Pull requests: one fast required Linux/Python 3.11 job for compile, unit/integration tests, and affected engine self-tests.
- Main/release candidates: full Python 3.11–3.13 × Linux/macOS/Windows matrix.
- Scheduled/manual: optional deeper security, performance, and long-running compatibility checks.
- Use path filters only when they cannot accidentally skip shared-contract or cross-engine tests.
- Do not add paid hosted services or mandatory third-party actions.
- Revisit the matrix only if runtime or maintenance burden becomes material; preserve at least one full cross-platform release check.

Branch protection is not assumed to be enabled. Before relying on required checks, verify repository settings and configure a required status check for the fast PR gate.

## 7. Product and engineering metrics

Track trends, not vanity totals:

- **Release integrity:** failed publication never replaces the last known-good bundle.
- **Regression health:** required checks passing; skips and flaky tests visible.
- **Traceability:** percentage of released claims with a registered, versioned source and a recorded faculty decision.
- **Review latency:** age of unresolved high-priority source/claim review items.
- **Curriculum coverage:** verified coverage versus pending, suggested and invalid mappings (never combine these states).
- **Assessment quality workflow:** percentage of released items with blueprint mapping and faculty approval; statistics reported only where data support them.
- **Privacy incidents:** unintended learner-data disclosures and time to contain/remediate.
- **Maintainability:** time to understand/fix regressions, compatibility breaks, and duplicated logic.

DORA's delivery metrics can inform engineering retrospectives, but a small single-maintainer project should not optimize for deployment frequency or compare itself mechanically with large teams. Prioritize release safety and useful outcomes.

## 8. Immediate execution order

1. Keep the current main baseline green; do not reopen completed V14.1 work without a failing test or demonstrated defect.
2. Implement V16 as a small integration PR: V15 invocation, policy/config mapping, unified report/manifest, and late-stage rollback tests.
3. Add V16 integration fixtures covering legacy and strict registered-source modes.
4. Only after V16 is stable, begin V17 schema contracts and incremental shared-core extraction.
5. Reassess V18–V21 after faculty workflow feedback; do not commit to building all candidate features in advance.

## 9. Research references

The roadmap draws on these primary or authoritative references:

- [NIST SP 800-218, Secure Software Development Framework (SSDF) 1.1](https://csrc.nist.gov/pubs/sp/800/218/final) — secure development practices across the lifecycle. [NIST SSDF publications](https://csrc.nist.gov/projects/ssdf/publications) lists SSDF 1.2 as a draft at the research snapshot.
- [SLSA Build Provenance](https://slsa.dev/spec/v1.2/build-provenance) — describing where, when and how software artifacts were produced.
- [SLSA Build Track](https://slsa.dev/spec/v1.2/levels) — graduated build integrity expectations; use proportionately rather than claiming a level without meeting it.
- [DORA software delivery metrics](https://dora.dev/guides/dora-metrics/) — throughput and instability measures, useful as a learning tool rather than vanity targets.
- [W3C WCAG 2.2](https://www.w3.org/TR/WCAG22/) — testable accessibility criteria for web interfaces.

External references inform engineering direction; they do not certify Usiphus or establish clinical, educational, privacy-law, or security compliance.

---

**Decision:** V16 should integrate V15 governance into V14's release pipeline. V17 should establish stable contracts and a shared core. Only then should Usiphus expand into richer evidence operations, assessment measurement, authoring, and optional interoperability.
