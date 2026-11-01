# V17 — Stable Contracts and Curriculum Intelligence

V17 adds an additive, local-first analysis layer. It does not replace V14/V16 release orchestration or alter V7–V16 file formats.

## Quick start

    python usiphus_v17.py --self-test
    python usiphus_v17.py --curriculum examples/v17/curriculum.json --cards examples/v17/cards.json --mapping examples/v17/mapping.json --previous-curriculum examples/v17/curriculum.previous.json --assessments examples/v17/assessments.json --osce examples/v17/osce.json --output-dir build-v17
    python usiphus_v17.py --validate examples/v17/cards.json --contract-type card
    python usiphus_v17.py --migrate legacy-curriculum.json --contract-type curriculum --migration-output curriculum.v17.json
    python usiphus_v17.py --compare-manifests previous-manifest.json current-manifest.json
    python usiphus_v17.py --curriculum examples/v17/curriculum.json --cards examples/v17/cards.json --pilot-metrics pilot.csv --output-dir build-v17

## Outputs

- v17_curriculum_quality.json: verified coverage, gaps, candidates, conflicts and breakdowns.
- v17_curriculum_impact.json: added, removed and modified competencies plus linked cards, assessment items and OSCE stations.
- v17_faculty_review_queue.json: suggested/pending mapping reviews, repairs, missing competencies and unmapped cards.
- v17_dashboard.html: self-contained static dashboard with accessible text and table content.
- v17_pilot_metrics_template.csv: blank, privacy-conscious template for institution-led workflow evaluation.
- v17_manifest.json: hashes of exact inputs and generated outputs.
- v17_pilot_metrics_summary.json: privacy-conscious aggregate workflow measures when --pilot-metrics is supplied.

## Contracts and compatibility

V17 defines contract types for cards, claims, sources, curricula, assessment items and release manifests. The Python validator uses stable error codes (V17-E...) and checks required fields, basic types, duplicate identifiers and curriculum weights.

Unversioned legacy files are accepted in compatibility mode with a warning. Migration is explicit and non-destructive: it wraps the original payload in a contract_type / contract_version envelope and records meaning_changed=false. It does not rename fields, invent missing values or silently reinterpret content. Future schema versions are rejected rather than guessed. See schemas/v17_contracts.schema.json.

The existing V7–V16 engines and entry points remain unchanged. The V17 CLI is additive; no shared-core extraction or new mandatory package dependency is introduced.

## Curriculum quality semantics

- Only a mapping with status=verified, method=faculty-approved, a reviewer label and a timezone-aware reviewed_at is counted as verified.
- Reviewer labels are not authenticated identities. Institutions must establish identity and authorization outside this local tool.
- Suggested and pending mappings are candidates and never counted as verified coverage.
- Invalid/conflicting mappings are sent to the repair queue.
- Required competencies without verified mappings are reported as gaps.
- Coverage by domain, cognitive level and learning objective is descriptive metadata coverage, not educational effectiveness, clinical accuracy or learner competence. Exact normalized-prompt duplicates are surfaced as human-review candidates only. A dependency graph connects competencies, objectives, verified cards, assessment items and OSCE stations.

V17 can consume a V10 mapping report (candidates) or explicit card competency_ids with faculty approval metadata. If no mapping report is supplied, unapproved card links are suggestions, not verified coverage.

## Change-impact semantics

Supply --previous-curriculum to compare competency identifiers and selected fields (title, domain, level, required flag, weight and learning objectives). The report follows explicit competency links to cards, assessments and OSCE stations. It cannot discover artifacts that have no such links. Changes create review candidates only; V17 never edits downstream clinical or educational content.

--compare-manifests produces a deterministic field-level comparison of two release manifests. Hash differences are integrity/change indicators, not quality scores.

## Faculty pilot and measures

Use the CSV template during a real specialty pilot. Collect only information the institution has approved and avoid patient data, learner identifiers and free-text personal information. Establish a baseline before setting targets. Supply the completed CSV with --pilot-metrics to calculate elapsed-time median/range, mapping corrections, evidence traceability issues, release reproducibility and friction-note counts. Only rows explicitly marked consented_for_aggregate=yes are included; non-consented rows are excluded.

The repository cannot perform faculty validation on its own. A faculty-led pilot must assess repeatability, mapping correctness and workflow friction. Record cohort size, role, missingness and consent/notice arrangements before interpreting aggregate results.

## Limitations

V17 is not an LMS, identity provider, accreditation service, clinical decision support system or automated curriculum approver. It does not authenticate reviewers, certify mapping quality, establish clinical truth or demonstrate educational effectiveness. Clinical and educational decisions remain with qualified faculty. Keep local files and outputs under institutional access, retention and backup controls.
