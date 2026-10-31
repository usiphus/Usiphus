# Usiphus V15.0 — Evidence Governance Hardening

V8/V9 already provide claim-to-reference mapping, workflow transitions, review-age checks, contradiction candidates, and date-based audit trails. V12 consumes this legacy shape and blocks releases for missing, stale, or unapproved evidence. V15 is additive: it hardens source identity, lifecycle, traceability, impact analysis, reviewer policy, and audit integrity without replacing the earlier engines.

## New contracts

- Evidence sources have stable `source_id`, title, type, authority metadata, jurisdiction, version, publication date, and lifecycle status (`active`, `superseded`, `withdrawn`).
- Claims link to source IDs and may pin version and jurisdiction.
- Supersession references are checked for missing targets and cycles.
- A deterministic source-to-card impact index identifies content requiring review after a source change.
- Legacy V8/V9/V12 `reference` strings remain readable. They are never silently promoted to verified source records; strict mode flags them until manually mapped.
- Optional distinct-reviewer quorum applies to approved/published claims.
- SHA-256 chained audit events detect tampering; legacy date-only audit trails are not represented as tamper-evident.

## Capability/gap audit

| Capability | V8–V14 | V15.0 |
|---|---|---|
| Claim-reference mapping and workflow | Existing in V8/V9/V12 | Preserved |
| Review freshness | Existing | Configurable deterministic check |
| Contradiction candidates | Existing simple polarity heuristic | Not reimplemented; remains a human-review prompt |
| Source version, jurisdiction, authority metadata, lifecycle | Inconsistent/not a normalized registry | Structured source registry |
| Supersession integrity | No source lifecycle contract | Link and cycle checks |
| Source-to-card impact | Not present as a source index | Deterministic impact index |
| Reviewer quorum | Legacy single reviewer metadata | Optional distinct-reviewer decisions |
| Tamper-evident audit events | Date/actor transition history | SHA-256 hash-chain verification |
| Release orchestration | V14 | Separate report; V14 unchanged |

## Run

```bash
python usiphus_v15.py --self-test
python usiphus_v15.py --sources examples/v15.sources.example.json --claims examples/v15.claims.example.json --as-of 2026-10-31 --output-dir build-v15
```

Add `--require-registered-sources` after legacy references have been manually mapped. Outputs are JSON, CSV, and Markdown. Example data are synthetic placeholders and must be replaced.

## Safety and compatibility

V15 does not fetch guidelines, grade source quality, interpret clinical meaning, decide clinical truth, authenticate reviewers, or auto-approve content. An active source is a metadata state, not proof of currentness. A hash chain is not a digital signature, trusted timestamp, secure ledger, or proof of identity. Software checks do not establish clinical validation, educational accreditation, privacy/legal compliance, or competence. V8–V14 files and formats remain unchanged; use synthetic data in tests and qualified faculty review for clinical content.
