# Usiphus Product Roadmap

**Status:** Proposed, evidence-informed direction  
**Last reconciled:** 2026-11-01  
**Baseline:** V14.1 release orchestration and V15 evidence governance are present on `main`.

This is the single forward-looking roadmap for Usiphus. It reconciles the original [V10-and-beyond roadmap](ROADMAP_v10.md), which captures the product's educational capabilities and validation needs, with the research-backed engineering plan. The older file remains as a historical record of the V7–V12 plan; this document supersedes it for future prioritization.

The roadmap is deliberately conditional: version numbers identify capability milestones, not a requirement to ship a new engine every time. Do not start a later milestone until its user need, dependencies, acceptance evidence, and cost are clear.

## 1. Product direction

**Usiphus helps specialty medical-education teams create, map, review, assess, and reproducibly publish teaching content with traceable evidence and faculty control.**

Its differentiator should be the combination of:
- Medical-content and curriculum workflows designed around specialty education.
- Traceability from teaching cards and assessment items to claims, sources, mappings, and reviewer decisions.
- A local-first, reproducible workflow that can run without paid APIs or mandatory hosted services.
- Explicit separation of automated checks, candidate suggestions, faculty decisions, and release status.

The product should optimize for clinical evidence traceability, curriculum and assessment alignment, safe production of teaching artifacts, privacy-conscious learner workflows, low operating cost, and ease of review.

### Non-goals and guardrails

- Not clinical decision support, an autonomous guideline author, or a source of clinical authority.
- Not a hosted LMS, identity provider, accreditation service, or proof of learner competence.
- No automated clinical approval, automatic acceptance of suggested curriculum mappings, or unsupported psychometric claims.
- No mandatory paid LLM, cloud database, hosted API, or third-party Python dependency.
- No rewrite of working engines merely to achieve architectural neatness.
- Real patient or learner data must not be used in fixtures or examples.

## 2. Product history and verified baseline

The original roadmap established the educational product sequence below. The V7–V12 capabilities and acceptance notes remain useful context; completed software work must not be confused with real-world faculty validation.

| Milestone | Product capability | Status / remaining validation |
|---|---|---|
| V7 | Examination intelligence: objectives, rubrics, exam metadata and diagnostics | Historical capability |
| V8 | Clinical evidence governance and review workflow | Implemented; preserve governance boundaries |
| V9 | Semantic exam intelligence and duplicate/answer diagnostics | Implemented; suggestions require review |
| V10 | Specialty curriculum mapping and weighted coverage | Implemented; faculty-approved real curriculum validation remains important |
| V11 | Consent-aware learner progress, mastery estimates and spaced repetition | Implemented; educational-model and real-data validation remain outstanding |
| V12 | Integrated specialty learning platform, faculty queue and release checklist | Implemented as a local batch workflow; end-to-end faculty/institutional validation remains outstanding |
| V13 | Cross-artifact validation, manifest integrity and selected privacy checks | Implemented |
| V14.1 | Configuration-driven release orchestration, regression gate and atomic bundle publication | Merged and hardened |
| V15 | Evidence-source registry, supersession checks, reviewer quorum, hash-chained audit and source-to-card impact index | Merged; currently a separate workflow from V14 |

**Important gap:** V14 orchestrates V8–V13, while V15 runs independently. A V14 release therefore does not yet apply V15 governance as part of the same release decision.

The source history does not fully reconstruct every early release. Do not invent capabilities for missing historical versions. Check the actual repository and CI result before describing a feature as shipped or a release as passing.

## 3. Prioritized roadmap

### Continuous — Protect and validate the existing product

Before adding breadth:
- Preserve V14 end-to-end, protected-output-path and rollback tests.
- Preserve V15 schema, source lifecycle, supersession, quorum, audit-chain and impact-index tests.
- Keep V7–V15 interfaces usable until a documented compatibility/deprecation policy exists.
- Validate V10–V12 against at least one faculty-approved specialty dataset and review learner-data handling with the relevant institution before real learner records are used.
- Talk to actual educators and learners. Record the task they are trying to complete, current workaround, frequency, pain, and what would count as a meaningful improvement.
- Establish baseline task time, review effort, defect rates and user satisfaction before setting numerical product targets.

**Gate:** No expansion should be justified only by feature count, version number, or a technically impressive architecture.

### V16 — One Governed Release (next engineering milestone)

**User/engineering problem:** Evidence governance is disconnected from the main release workflow.

Deliver:
- Add V15 as an explicit stage in V14's configured release pipeline.
- Reuse V15 policy logic; do not duplicate source lifecycle or quorum rules.
- Support a clearly documented migration mode for legacy free-text references and a strict registered-source mode.
- Produce one release-level report joining V12/V13 validation with V15 source, claim and impact findings.
- Include V15 inputs, policy, engine versions and report hashes in the release manifest.
- Block publication on configured mandatory governance failures while preserving the previous known-good bundle.
- Keep existing V14 inputs and standalone V15 use working during migration.
- Add synthetic end-to-end tests for active, superseded and withdrawn sources, missing IDs, supersession cycles, insufficient quorum, legacy references and late-stage rollback.

**Acceptance:** One command produces one coherent release decision; V15 failure cannot publish a partial bundle; legacy migration is explicit; source inputs remain unchanged; human approval remains separate from machine findings.

### V17 — Stable Contracts and Real-Workflow Validation

**Problem:** The platform needs stable interfaces, but architecture work must follow actual workflow needs rather than lead them.

Deliver:
- Define versioned schemas and contracts for cards, claims, sources, curriculum mappings, assessments and release manifests.
- Specify compatibility rules, actionable error codes and explicit migrations.
- Extract only genuinely shared utilities into a small internal package; keep existing versioned scripts as compatibility wrappers.
- Add a single documented CLI only if it materially simplifies common workflows.
- Validate the end-to-end workflow with faculty using a real specialty curriculum and appropriately governed teaching data.
- Measure authoring/review time, mapping corrections, evidence traceability, release reproducibility and user-reported friction.
- Convert feedback into a prioritized backlog; distinguish must-fix reliability issues from optional convenience features.

**Acceptance:** Existing workflows remain compatible; schemas reject malformed data clearly; migrations do not silently change meaning; user testing identifies a repeatable workflow improvement. If a shared core or unified CLI does not remove demonstrated friction, defer it.

### V18 — Evidence Lifecycle Operations

**Problem:** A registry can track source metadata but cannot itself determine whether a source is clinically authoritative or what a changed guideline means.

Candidate work:
- Controlled intake of user-provided files/URLs with canonical identifiers, publisher, jurisdiction, dates, version, retrieval time and content hash where available.
- Duplicate/canonical-source candidates and change reports.
- Review queues prioritizing withdrawn/superseded sources, source changes, stale reviews, jurisdiction mismatches and affected-card counts.
- Claim-level faculty decisions and rationale.
- Optional, auditable retrieval; offline builds must continue to work.
- Human confirmation before changing source status or clinical claims.

**Acceptance:** Source changes create review candidates, never automatic clinical edits; provenance is inspectable; offline operation remains reliable; qualified reviewers can accept, reject or defer suggestions.

### V19 — Assessment Quality Workflow

**Problem:** Assessment features are useful only when aligned, reviewed and interpreted responsibly.

Candidate work:
- Assessment blueprints linking specialty, competency, learning objective, topic and cognitive level.
- Structured SBA/MCQ and clinical-vignette schemas with rationale, evidence links and reviewer state.
- Deterministic checks for incomplete items, duplicate candidates, ambiguous wording flags and blueprint coverage.
- Optional item statistics only from adequately described, explicitly supplied response data; include cohort size, missingness and assessment version.
- Human oversight for validity, reliability, fairness, standard setting and item retirement.

**Acceptance:** Released items have blueprint mapping and human approval; statistics are reproducible and contextualized; no heuristic is presented as proof of validity or learner competence.

### V20 — Faculty Authoring and Accessible Publishing

**Problem:** Faculty need a straightforward way to turn governed content into useful teaching artifacts.

Candidate work:
- Start with the simplest validated local-first authoring workflow; do not build a GUI until user research supports it.
- Structured editing for cards, cases, objectives, evidence links, mappings and assessment items.
- Review diffs, history, comments, approval queues and release previews.
- Reproducible PPTX, PDF, HTML and structured-data exports from one governed content model.
- Accessibility checks for heading/reading order, alternative text, table structure, contrast where measurable and keyboard navigation.
- WCAG 2.2 AA as a target for web interfaces; separately test exported documents.

**Acceptance:** Faculty can trace published content to evidence, mappings and approval state; exports are reproducible; accessibility limitations are visible; local/offline use remains supported.

### V21 — Interoperability and Deployment (only on demonstrated demand)

Consider only formats and deployment modes requested by pilot institutions:
- Import/export adapters for agreed curriculum, question-bank and learning-event formats.
- Portable bundles, backup/restore, retention/deletion and migration tools.
- Optional multi-user deployment only after identity, authorization, key management, threat modelling, audit, incident response and institutional ownership are specified.
- Standards-based integration only for a real use case; do not add speculative FHIR or hosted infrastructure.

**Acceptance:** Adapters have conformance and round-trip fixtures where possible; data minimization and access control are reviewed; local mode remains supported.

## 4. Prioritization rules

Choose work in this order:
1. Safety, data integrity, privacy or release-blocking defects.
2. V16 integration and reliability of the existing end-to-end workflow.
3. Faculty validation of V10–V12 and usability problems that block real adoption.
4. Stable contracts or refactoring only where they reduce demonstrated maintenance or user friction.
5. New evidence, assessment and authoring capabilities when validated users need them.
6. Interoperability, GUI, hosted or multi-user features only when a concrete pilot requires them.

For every proposed feature, record: target user, problem, current workaround, expected outcome, evidence for demand, implementation/maintenance cost, privacy/safety impact, and a testable acceptance condition. Defer ideas without a clear user or engineering benefit.

## 5. Cross-cutting release requirements

Every milestone must address the applicable requirements below.

### Clinical and educational governance
- Qualified reviewers remain responsible for clinical accuracy, source interpretation, curriculum meaning, assessment quality and release approval.
- Reports distinguish machine findings, reviewer decisions and release policy.
- Keep source version and jurisdiction explicit; preserve uncertainty and unresolved conflicts.
- Similarity, freshness or authority metadata must never become an automated clinical-quality score.

### Privacy and security
- Minimize data collection and classify data before adding fields.
- Treat pseudonymous learner records, audit ledgers, salts, backups and exports as sensitive.
- Document access, retention, deletion, backup and incident procedures with the data owner.
- Threat-model importers, output paths, plugins, logs and any multi-user features.
- Hash chains are tamper-evident only within their stated limits; do not overstate their guarantees.

### Reliability and supply chain
- Use least-privilege workflow permissions and keep secrets out of code, fixtures, logs and artifacts.
- Preserve atomic publication and prove rollback with tests.
- Record source revision, configuration, input/output hashes and test evidence for releases.
- Pin GitHub Actions to reviewed full commit SHAs when practical.
- Add SBOMs or stronger provenance attestations when distribution/dependencies make them worthwhile; do not add process for its own sake.

### Compatibility and maintainability
- Keep the standard-library-only baseline unless a dependency has a documented benefit, license, maintenance plan and fallback.
- Preserve compatibility wrappers and define schema migrations before changing formats.
- Prefer small, independently reviewable PRs.
- Do not create a new versioned engine for routine maintenance, bug fixes or documentation changes.

## 6. Definition of done

A milestone is complete only when applicable evidence exists for:
- Correctness: unit, regression, negative and representative end-to-end tests.
- Safety: human-review boundaries and failure modes documented.
- Data integrity: source immutability, safe paths, atomic publication and rollback.
- Privacy: output/log review and data-handling implications documented.
- Compatibility: supported contracts and migration tests.
- Reproducibility: fixed inputs/configuration yield stable semantic outputs.
- Usability: examples, CLI help, actionable errors and migration guidance.
- Human acceptance: relevant faculty/operator review before real clinical or learner data use.

Publish passed, failed, skipped and not-applicable checks separately. A green self-test is not clinical, educational, privacy-law or security certification.

## 7. CI and cost policy

- Pull requests: one fast required Linux/Python 3.11 check for compile, unit/integration tests and affected engine self-tests, where repository configuration permits.
- Main/release candidates: retain the full supported Python/OS matrix.
- Scheduled/manual deeper checks are optional unless a demonstrated risk justifies making them required.
- Avoid duplicate overlapping workflows and unnecessary matrix expansion.
- Use path filters only when shared-contract and cross-engine tests cannot be skipped accidentally.
- Do not introduce paid hosted services or mandatory paid APIs.

## 8. Product and engineering measures

Establish baselines before setting targets. Track:
- Time for a faculty member to prepare and review a specialty teaching release.
- Percentage of published claims with registered, versioned evidence and a recorded faculty decision.
- Age of unresolved high-priority evidence-review items.
- Verified curriculum coverage separately from suggested, pending and invalid mappings.
- Percentage of released assessment items with blueprint mapping and human approval.
- Release reproducibility, failed-publication rollback and regression health.
- User-reported usefulness and recurring workflow friction.
- Privacy incidents and time to contain/remediate.

Do not optimize for version count, lines of code, number of automated suggestions, or vanity deployment metrics.

## 9. Immediate decision

1. Keep V14.1 and V15 stable.
2. Complete V16 as a focused integration, preserving standalone compatibility and rollback.
3. Validate the existing V10–V12 educational workflow with faculty and a real specialty curriculum under appropriate governance.
4. Use those findings to decide how much V17 contract/core work is actually needed.
5. Reassess V18–V21 from user evidence rather than treating them as guaranteed commitments.

## 10. Reference basis

The engineering guardrails are informed by:
- [NIST SP 800-218, Secure Software Development Framework 1.1](https://csrc.nist.gov/pubs/sp/800/218/final).
- [SLSA build provenance](https://slsa.dev/spec/v1.2/build-provenance) and [build levels](https://slsa.dev/spec/v1.2/levels), applied proportionately.
- [DORA software delivery metrics](https://dora.dev/guides/dora-metrics/), used for learning rather than vanity targets.
- [W3C WCAG 2.2](https://www.w3.org/TR/WCAG22/) for web-interface accessibility.

These references inform engineering practice; they do not certify Usiphus or establish clinical, educational, privacy-law or security compliance.

## 11. Expanded medical-education product capabilities

This section expands the V17–V21 candidate work above. It is deliberately integrated into the existing milestones rather than creating a new versioned engine for every idea. The sequence remains conditional on faculty/learner discovery, licensing, safety review and implementation cost.

### 11.1 Curriculum quality and change intelligence (V17)

- Provide a curriculum quality view that separates verified, suggested, pending, missing and conflicting mappings.
- Show coverage by specialty, competency, objective, topic, cognitive level and assessment method; never turn incomplete mapping into a falsely reassuring aggregate score.
- Record why a mapping was proposed, its source, confidence/limitations, reviewer and approval history.
- Generate change-impact reports when a competency, objective or curriculum framework changes, identifying affected cards, cases, questions, OSCE stations and releases.
- Consider 1EdTech CASE exchange only after a real curriculum-framework exchange use case is identified.

**Gate:** Faculty can inspect, correct and approve mappings; dashboards distinguish machine suggestions from verified coverage.

### 11.2 Evidence-to-learning impact graph (V18)

Connect the existing V15 source/claim/card impact model to curriculum and assessment artifacts:

`source → claim → teaching card/case → objective/competency → assessment item/OSCE station → release`

- Show downstream impact of a changed, superseded or withdrawn source.
- Show upstream evidence and approval history for an assessment item or challenge case.
- Prioritize review queues using transparent policy factors such as source withdrawal, change detection, review age, jurisdiction mismatch and affected-artifact count.
- Keep source freshness, similarity and metadata distinct from clinical authority or correctness.
- Require a qualified reviewer to interpret source changes and approve clinical edits.

**Gate:** Every impact edge is traceable; machine-generated review candidates never silently modify clinical content.

### 11.3 Assessment, OSCE and challenge studio (V19)

**Blueprinting and written assessments**
- Blueprint by specialty, competency, objective, topic, cognitive level and assessment method; support rapid blueprint creation with faculty approval.
- Structured single-best-answer/MCQ and clinical-vignette authoring, including focused lead-ins, homogeneous plausible options, answer rationale, distractor rationale and evidence links.
- NBME-informed deterministic quality flags for missing data, ambiguous lead-ins, implausible/heterogeneous options, cueing, technical flaws and duplicate candidates. Flags are prompts for review, not automatic validity decisions.
- Review workflows for item approval, versioning, retirement and change history.
- Optional post-administration item statistics only when response data are explicitly supplied and sufficiently described; report cohort size, missingness, version and limitations. Do not infer validity, reliability or fairness from a single statistic.

**OSCE and clinical-station authoring**
- Station brief, candidate instructions, role-player/patient brief, equipment/setup, duration, examiner checklist, global rating scale, critical actions, marking rubric and accommodations.
- Faculty review, station version history, blueprint mapping and post-use review notes.
- Do not claim station validity or examiner reliability merely because a checklist is complete.

**Medical Challenges Hub — two distinct products**
1. **External challenge directory:** Curated links to independent medical challenge sites, categorized by specialty, format (diagnosis, radiology/image interpretation, ECG, dermatology, pathology, anatomy, pharmacology, etc.), level, access method and last-checked date. Store provider, URL, attribution, license/reuse status and whether embedding is explicitly permitted. Default to opening the provider's website.
2. **Native Usiphus challenge engine:** Original faculty-authored cases with progressive clues, bounded attempts, accepted answer aliases, reveal/explanation, differential comparison, source links, curriculum objectives, difficulty and reviewer status. Support daily or themed packs only when Usiphus owns or is licensed to distribute the cases; include untimed/offline practice.

- A native challenge record should include a stable ID/version, case stem, clue sequence, accepted answers/aliases, explanation, differential/distractor rationale, source and attribution, specialty/objective mapping, cognitive level, difficulty, image/media provenance and license, faculty reviewer/approval state, publication status and withdrawal/supersession history.
- Challenge feedback should explain why the intended answer fits and why plausible alternatives are less likely; avoid score-only feedback.
- Support image-based challenges only with documented rights, provenance, de-identification/privacy review and appropriate consent. Never place identifiable patient material in examples or fixtures.
- Connect challenge cases to V8 evidence governance, V10 curriculum mapping, V12 faculty review, V15 source lifecycle and the V16 governed release through versioned artifacts and explicit interfaces.
- Do not scrape, mirror, copy daily answers, reproduce third-party clues/images, bypass access controls or embed third-party games without explicit permission and compatible terms. A link in the directory does not imply endorsement, partnership, API access or content rights. If rights are unknown, mark them unknown and link out only.

**Gate:** Native cases are original or licensed, evidence-linked where clinically relevant, mapped and faculty-approved. External listings accurately disclose access and rights status. Challenge scores are descriptive engagement data, not proof of learning or competence.

### 11.4 Feedback-to-action and faculty authoring (V20)

- Capture learner reflection/confidence and faculty feedback with clear purpose, notice/consent where applicable, role-based visibility and retention limits.
- Turn feedback into an action record: issue/strength, linked objective or artifact, agreed next step, owner, due/review date, completion evidence and follow-up outcome.
- Keep formative feedback distinct from summative grading; do not use satisfaction ratings as a proxy for learning.
- Provide structured authoring for cards, cases, objectives, evidence links, curriculum mappings, challenge packs, OSCE stations and assessment items.
- Add review diffs, comments, approval queues, version history and release previews.
- Produce reproducible PPTX, PDF, HTML and structured-data exports from the governed content model.
- Evaluate interactive-content tools such as H5P only when user demand, license compatibility, accessibility, offline behavior and maintenance costs are established.
- Target WCAG 2.2 AA for any web interface and separately test exported documents, keyboard interaction, captions/transcripts, alternative text, reading order and assessment accessibility.

**Gate:** Feedback results in documented follow-up actions; access to learner feedback is appropriately restricted; published artifacts remain traceable to source, mapping and approval state.

### 11.5 Standards-based exchange and deployment (V21)

Adopt standards in response to an actual pilot requirement, not as a checklist:
- **1EdTech CASE:** competency frameworks, standards and learning-objective exchange.
- **1EdTech QTI:** question/item and test exchange; implement a documented supported profile and round-trip tests rather than claiming full-specification support.
- **Open Badges:** optional portable recognition of a defined, human-approved learning achievement; never imply licensure or clinical competence.
- **H5P:** optional interactive-content exchange/embedding subject to license, host compatibility, accessibility and offline constraints.
- **LTI 1.3:** only when a concrete LMS launch/gradebook integration is required and identity/security responsibilities are understood.
- Portable bundles, backup/restore, retention/deletion and migration tools before any hosted deployment.
- Multi-user deployment only after identity, authorization, key management, threat modelling, audit, incident response, data ownership and operational support are specified.
- No speculative FHIR integration, hosted LMS, cloud database or always-on service without a demonstrated use case.

**Gate:** Each adapter has a documented profile, conformance/round-trip fixtures and explicit unsupported-feature behavior. Local-first operation remains supported.

## 12. Expanded prioritization and release sequence

1. Complete V16 first: V15 evidence governance becomes part of the V14 governed release.
2. Validate V10–V12 with faculty and a real specialty curriculum under appropriate data governance.
3. V17: stabilize only the contracts needed by the validated workflow; add curriculum quality/change-impact views where they solve a demonstrated problem.
4. V18: connect evidence lifecycle events to downstream curriculum and assessment artifacts.
5. Optional small step after V16: publish an external challenge directory containing links and rights metadata only. This must not delay V16 and must not scrape or republish provider content.
6. V19: build the assessment/OSCE/challenge studio on the governed content model. Start with faculty-authored original cases and a narrow specialty pilot before adding image/media complexity.
7. V20: add feedback-to-action and authoring/publishing workflows based on pilot feedback.
8. V21: implement only the standards and deployment modes explicitly requested by pilot institutions.

A later milestone may be split into small PRs or deferred. These are product capabilities, not promises to ship a monolithic V19 or V20 engine.

## 13. Additional product measures

Establish baselines before setting targets. In addition to the measures above, consider:
- Verified curriculum coverage and unresolved mapping conflicts by objective/competency.
- Percentage of challenge cases with documented provenance/rights, curriculum mapping and faculty approval.
- Challenge completion, repeat practice and user-reported usefulness as descriptive engagement measures only.
- Percentage of OSCE stations with complete required materials and recorded faculty review.
- Assessment blueprint coverage and review-defect categories; item statistics only with adequate response-data context.
- Feedback action closure and follow-up outcomes, reported with privacy-preserving aggregation.
- External directory link health and percentage of entries with recently checked access and rights metadata.

Do not optimize for daily active users, streaks, leaderboards, challenge scores or badge counts at the expense of educational quality, accessibility, privacy or learner wellbeing.

## 14. Additional reference links

These resources inform exploration and implementation; they do not confer permission to reuse third-party content or certify Usiphus:
- [NBME](https://www.nbme.org/) — item-writing and assessment resources for faculty-led question development.
- [1EdTech CASE](https://www.1edtech.org/standards/case) — competency framework and learning-objective exchange.
- [1EdTech QTI](https://www.1edtech.org/standards/qti) — assessment-item and test exchange.
- [1EdTech LTI](https://www.1edtech.org/standards/lti) — optional LMS integration.
- [1EdTech Open Badges](https://www.1edtech.org/standards/open-badges) — portable recognition of defined achievements.
- [H5P](https://h5p.org/) — interactive-content ecosystem to evaluate only where justified.
- [Doctordle](https://doctordle.net/) and [Radiordle](https://www.radiordle.org/) — examples of external challenge formats, not sources to copy or republish.
- [EnterMedSchool](https://www.entermedschool.org/) — example of an external medical-education resource directory.



## 15. V19 implementation boundary and acceptance

V19 provides a local-first, standard-library-only authoring and structural-review workflow for:
- Assessment blueprints linking specialty, competency, objective, cognitive level and method.
- Single-best-answer MCQs with a focused lead-in, option set, one keyed answer, answer rationale, distractor rationales, evidence reference and objective links.
- OSCE stations with candidate/role-player briefs, setup, timing, examiner instructions, checklist, critical actions, global rating scale and rubric.
- Original progressive-clue challenge cases with accepted answer aliases, explanation, differential rationale, curriculum mapping and provenance.
- An external challenge directory that stores provider links, access and rights metadata; embedding is denied unless license or permission is explicit.
- Optional aggregate item counts with cohort size, missingness and version context; descriptive only, with no psychometric validity claims.

Automated flags are prompts for faculty review, not judgments of clinical correctness, item validity, station validity, fairness or reliability. Duplicate candidates are never auto-deleted. Third-party content is not scraped, mirrored or reproduced. Media requires documented rights, provenance, privacy/consent review and accessibility checks. Reviewer labels are not authenticated identities. Scores are practice/engagement signals, not proof of learning or competence.

**V19 acceptance:** deterministic self-tests and unit tests; synthetic examples; schema and documentation; actionable JSON/Markdown/CSV reports; no real patient or learner data in fixtures; faculty-reviewed pilot protocol; and explicit non-claims. A clean automated report is only *ready for faculty review*. Publication still requires the existing governed release and explicit human approval.
