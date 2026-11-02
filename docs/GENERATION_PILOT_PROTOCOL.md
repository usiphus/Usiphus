# Specialty generation pilot protocol

## Purpose

Measure whether a single specialty can be generated reproducibly with complete
curriculum coverage, traceable evidence, acceptable clinical quality and
practical local inference cost. A successful structural run is not a clinical
validation.

## Entry requirements

- A versioned specialty blueprint with stable topic/domain/objective IDs.
- Faculty-curated evidence packets for the pilot topics. Sources must have
  reviewed full-text access, rights/access status, publication date and
  guideline/version metadata (use `not_applicable` when not relevant).
- Snippets with source IDs, stable snippet IDs, exact page/section/paragraph
  locators and faculty-assigned objective IDs.
- Synthetic teaching cases only; never place identifiable patient data in the
  benchmark.
- A pinned Ollama model tag, machine specification and generator commit SHA.
- A faculty-approved gold set and scoring rubric. Do not use model-generated
  cards as their own gold standard.

## Pilot design

1. Select five representative topics: one common/high-volume topic, one
   emergency/high-risk topic, one diagnostic pathway, one procedural/OSCE
   topic and one topic with guideline-dependent management.
2. Generate a small batch (3-6 cards per topic) with the same pinned model,
   prompt, temperature and token limit. Record all settings.
3. Repeat the same run three times from clean output directories to measure
   variability. Do not reuse checkpoints for repeatability measurements.
4. Have two specialty faculty reviewers independently score cards, blinded to
   run number where practical. Resolve disagreements by adjudication.
5. Only after the five-topic pilot passes should the complete specialty
   blueprint be generated and assessed.

## Required measurements

| Dimension | Measurement |
|---|---|
| Curriculum coverage | Planned vs generated topics and objectives; missing objective count |
| Evidence coverage | Objectives with eligible snippets; cards with valid claim mappings |
| Unsupported content | Faculty-audited unsupported or contradicted claims per 100 claims |
| Clinical safety | Unsupported dose, contraindication, emergency, threshold or red-flag claims |
| MCQ quality | Ambiguity rate, key validity, distractor plausibility and rationale completeness |
| Case quality | Self-contained vignette, reasoning quality and safe first-step approach |
| Viva/OSCE quality | Answer completeness; observable checklist coverage and station feasibility |
| Duplication | Exact and semantic duplicate prompt rate |
| Reliability | Topic pass rate, retries per topic, failed-topic rate and run-to-run variation |
| Feasibility | Wall-clock time, cards/minute, tokens, peak RAM/VRAM, CPU/GPU, model load and disk use |

## Proposed release gates

These are initial project thresholds to be reviewed by faculty before the pilot;
they are not validated educational standards.

- 100% of planned topics and learning objectives represented.
- 100% of evidence-grounded cards pass source/snippet/locator validation.
- Zero faculty-confirmed unsafe or materially false high-risk claims.
- Zero duplicate prompts after deterministic and semantic duplicate review.
- At least 90% of cards rated acceptable or better by both reviewers, with no
  unresolved critical disagreement.
- No unreviewed card exported as clinically approved.
- All three repeat runs complete without silent topic loss; record any retry.
- Runtime and hardware cost are reported, not hidden behind a single success flag.

Any unsafe emergency recommendation, fabricated citation, wrong dose,
contraindication error or materially wrong MCQ key is an automatic stop and
root-cause review, regardless of aggregate score.

## Pilot report template

Record:
- Specialty, blueprint version, topic IDs and objective count.
- Generator commit SHA, model tag/digest, Ollama version and full generation
  settings.
- Machine CPU, GPU, RAM/VRAM, OS and available disk.
- Source packet IDs, evidence coverage and unresolved evidence gaps.
- Cards requested/generated/accepted/rejected; topic-level retry counts.
- Faculty reviewer scores, disagreements and adjudicated critical errors.
- Runtime per topic and total, cards/minute, peak resource use.
- Failure analysis, corrective actions and decision: stop, revise, or expand.

## Current status

The repository now contains the evidence packet validator, objective-specific
scholarly discovery, a loader for per-topic JSON packets, and generator preflight
before Ollama inference or checkpoint reuse. Five example ophthalmology packets
are bundled as candidate-source/review prompts only. They are deliberately not
eligible evidence: full-text review, rights/access verification, source-currentness
checks and faculty approval have not been completed. The live pilot has **not**
been run: it requires a local Ollama instance, pinned model, eligible curated
evidence packets and a faculty-approved gold set. Do not report feasibility or
clinical-quality success until the above measurements have actually been
collected.
