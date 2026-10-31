# Usiphus V11 — Adaptive Learning & Assessment

## Purpose and boundaries

V11 consumes a V10 specialty curriculum and its faculty-verified card-to-competency
mapping report. It produces learner study plans, review dates, and privacy-aware
cohort analytics. It is a local batch engine; it requires no hosted database,
third-party API, or paid service.

It does not edit cards, curriculum, mappings, or evidence. Mastery estimates
are educational descriptions, not proof of clinical competence, readiness for
unsupervised practice, or high-stakes pass/fail decisions.

## Data flow

1. V10 curriculum JSON with stable competency IDs.
2. V10 curriculum coverage JSON; only candidate rows with status "verified"
   contribute to learner competency estimates.
3. Consent registry and minimal assessment event JSON.
4. Individual progress JSON, suppressed cohort analytics JSON, review schedule
   CSV, and a manifest.

## Attempt file contract

Use randomly generated opaque UUIDv4 values for learner IDs. Never use names,
emails, phone numbers, student numbers, or other identifiers as learner IDs.
Do not include free-text answers or clinical case narratives.

Example:

    {
      "privacy": {
        "retention_days": 365,
        "minimum_cohort_size": 5
      },
      "learners": [
        {
          "learner_id": "123e4567-e89b-42d3-a456-426614174000",
          "consent_granted": true,
          "consented_at": "2026-10-01T09:00:00Z"
        }
      ],
      "attempts": [
        {
          "attempt_id": "attempt-0001",
          "learner_id": "123e4567-e89b-42d3-a456-426614174000",
          "card_id": "uro-card-001",
          "answered_at": "2026-10-30T11:30:00Z",
          "score": 0.8
        }
      ]
    }

Score is numeric from 0 to 1. consent_revoked_at may be supplied; attempts at
or after revocation are excluded. Learners without consent are excluded.
Attempts older than retention_days are excluded from analytics. Timestamps
must include a timezone.

V11 strictly allowlists learner and attempt fields and rejects direct identifiers
and free-text fields. UUIDv4 format validation does not prove anonymity.
Operators must protect any separate mapping between a real person and the
pseudonym. Generated outputs contain pseudonymous IDs and must be protected as
sensitive educational records.

## Explainable mastery estimate

For each learner and competency, V11 uses a Beta prior and fractional score
updates:

- Default prior: Beta(1, 1), with mean 0.5 before evidence.
- For each score s, add s to alpha and 1-s to beta.
- Estimate: alpha / (alpha + beta).

This is transparent but not a validated psychometric model. It is sensitive to
assessment quality, scoring consistency, and the number and difficulty of
questions. It must not be used alone for clinical competence decisions. The
minimum-attempt rule only gates the configurable study-target label.

## Configurable spaced repetition

Optional settings JSON:

    {
      "prior_alpha": 1.0,
      "prior_beta": 1.0,
      "mastery_thresholds": [0.5, 0.65, 0.8, 0.9],
      "interval_days": [1, 2, 4, 7, 14],
      "minimum_attempts_for_target": 3
    }

There must be one more interval than thresholds. Thresholds must be strictly
increasing in (0, 1); intervals must be positive and non-decreasing. Due dates
are calculated from the latest eligible attempt. Unseen mapped competencies
are scheduled immediately.

## Outputs

- learner_progress.json: individual progress keyed by pseudonymous learner ID.
- cohort_analytics.json: competency aggregates; metrics below minimum_cohort_size
  are suppressed.
- review_schedule.csv: due date and recommended interval per learner/competency.
- v11_manifest.json: output inventory and privacy notes.

Retention is applied to analytics generation; V11 does not delete the original
attempt file. Operators remain responsible for source deletion, access control,
and applicable institutional and legal obligations. Cohort suppression reduces
small-group disclosure risk but is not a guarantee against re-identification.

## Run

    python usiphus_v11.py --self-test
    python usiphus_v11.py --curriculum curriculum.json --mapping build/curriculum_coverage.json --attempts attempts.json --output build-v11

Use --as-of 2026-10-31T12:00:00Z for reproducible scheduling and retention
cutoffs. Source files are read-only.

## Governance

Only V10 faculty-verified mappings attribute an attempt to a competency.
Suggested, pending, and invalid mappings are ignored. Software tests validate
data structure and calculations, not clinical validity. Faculty and assessment
experts must review instruments and interpretation before operational use.
