# V18 Evidence Lifecycle Faculty Review Protocol

## Purpose
Evaluate whether V18 makes evidence changes easier to detect, prioritize and resolve while preserving faculty control and source provenance.

## Preparation
1. Select one bounded specialty evidence registry and a faculty owner.
2. Use public, licensed or institutionally approved teaching sources; do not include patient-identifiable data.
3. Freeze source and claim registry snapshots and record their hashes.
4. Define reviewer roles, identity verification, jurisdiction rules, review intervals, access and retention.
5. Establish a baseline for time to identify and resolve source changes.

## Pilot tasks
- Register a new source with publisher, jurisdiction, version, publication/retrieval dates and URL.
- Hash an institution-approved local source file and verify that the input file remains unchanged.
- Compare a prior state with changed version/content hash/status.
- Review duplicate URL/content-hash candidates without merging records.
- Inspect withdrawn, superseded, stale and jurisdiction-mismatch queue items.
- Follow one source through claim, card, competency, assessment and OSCE links.
- Record accepted/rejected/deferred faculty decisions with rationale.
- Repeat the run with identical inputs and compare semantic outputs and hashes.

## Measures
Record source changes detected, affected artifacts correctly identified, queue precision/recall as adjudicated by faculty, time to triage and resolve, unresolved high-priority items, false-positive duplicate candidates, jurisdiction mismatch corrections, and reproducibility. Do not interpret the priority score as clinical risk.

## Exit criteria
Faculty can explain every queue item, trace its affected artifacts, correct inaccurate links, and make explicit decisions without V18 changing clinical content. Source inputs remain unchanged, outputs are reproducible, offline operation works, and access/retention controls are accepted. If not, defer integration and revise the workflow.
