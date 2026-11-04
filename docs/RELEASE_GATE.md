# Usiphus release-gate checklist

A release is blocked if a required gate is unchecked, unknown, or supported
only by an assumption. Record evidence against the exact commit and release
manifest.

## 1. Code and reproducibility
- [ ] Clean checkout installs/runs using the documented Python 3.11+ workflow.
- [ ] Python compile step passes.
- [ ] Unit/integration tests pass on the exact release commit.
- [ ] All configured engine self-tests pass; inspect skips and failures.
- [ ] CI matrix results have been inspected for Python 3.11–3.13 and supported
      operating systems. Matrix configuration alone is not a passing result.
- [ ] No untracked secrets, private keys, local learner data, checkpoints or
      temporary build artifacts are included.

## 2. Generator and curriculum
- [ ] Curriculum preflight passes before inference and checkpoint reuse.
- [ ] Every planned topic has a stable unique ID and valid domain mapping.
- [ ] Every objective has at least one generated card.
- [ ] Requested card-format counts match the actual generated mix.
- [ ] Duplicate IDs/prompts and malformed cards are resolved.
- [ ] Checkpoints were revalidated against the current blueprint.
- [ ] Regeneration attempts and Ollama request budgets are bounded.
- [ ] `coverage_report.json` and `quality_report.json` were reviewed.
- [ ] Uncovered objectives and topic gaps are resolved or explicitly documented.

## 3. Evidence, rights and clinical review
- [ ] No search-result metadata or discovery link is represented as clinical
      evidence.
- [ ] Every evidence-grounded claim points to a reviewed source/snippet and
      exact locator.
- [ ] Full-text access, reuse rights, source currency and jurisdiction have
      been checked by a qualified curator.
- [ ] Clinical claims, answers, MCQ keys, distractors, doses, emergency actions,
      contraindications and OSCE critical actions receive independent
      qualified clinical review.
- [ ] Examination/assessment review is recorded separately from clinical
      content review; one approval must not imply the other.
- [ ] No generator or script automatically sets a human approval state.
- [ ] No identifiable patient information is present; examples use synthetic
      cases and approved/licensed media only.

## 4. Governance and publication
- [ ] V13 validation findings are reviewed and blocking errors resolved.
- [ ] V15 evidence registry, source lifecycle, reviewer quorum and audit
      requirements pass under the selected policy.
- [ ] V16 governed release completes with a verified manifest and expected
      hashes.
- [ ] Any required signed audit events and external anchors verify using
      trusted keys distributed out of band.
- [ ] The release preview contains only intended, approved artifacts.
- [ ] No draft, rejected, stale, or unreviewed high-risk item is published.
- [ ] Rollback and recovery have been rehearsed on a non-production copy.

## 5. Privacy, security and operations
- [ ] Access to learner records, audit files, release ledgers, salts, backups
      and signing material is restricted.
- [ ] Logs and reports do not expose secrets, private records or raw feedback.
- [ ] Retention/deletion, backup and incident-response responsibilities are
      documented by the responsible institution.
- [ ] Public issue reports contain no credentials, patient data or learner
      identifiers.
- [ ] License/copyright and third-party content rights have been reviewed.

## 6. Approval record

**Decision:** Go / No-go

**Release owner:** ______________________________

**Clinical reviewer:** ___________________________

**Examination/assessment reviewer:** ______________

**Security/operations reviewer:** _________________

**Commit / tag:** ________________________________

**Manifest / release ID:** ________________________

**Date:** _______________________________________

**Exceptions and rationale:** ____________________________________________
