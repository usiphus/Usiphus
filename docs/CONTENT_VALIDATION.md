# Generated content validation contract

## Existing generator gates

The automated generator already enforces a curriculum-aware contract in
`usiphus_generation_quality.py` and `usiphus_auto_deck.py`:

- Curriculum preflight checks unique topic/domain IDs, valid mappings,
  objective lists, coverage tags, exam emphasis, card counts and format mix.
- Every generated card must map to an exact supplied learning objective.
- Every topic's requested card-format mix must be met.
- Duplicate prompts, invalid metadata, malformed MCQs, incomplete cases,
  weak viva answers and incomplete OSCE rubrics are rejected.
- Quality retries are bounded by `quality_attempts` and Ollama calls are
  bounded by `--max-ollama-calls`.
- Checkpoints are revalidated before reuse.
- Failed topic gates prevent a complete deck from being exported.
- Evidence packets are checked before inference; claim-level evidence mappings
  are checked against reviewed snippets and their source/locator provenance.

These are structural, provenance and traceability checks. They do not establish
clinical truth, guideline currency, source quality, exam validity or
educational effectiveness.

## JSON Schema

`schemas/generated-deck.schema.json` documents the shape of the
`generated_cards.json` artifact. It is an interchange/documentation contract;
the generator's existing semantic validators remain authoritative for rules
that require curriculum context or cross-card checks.

The schema intentionally permits additional properties so future card fields
and versioned metadata do not break older readers. Tighten the schema only with
a coordinated data migration and tests.

## Required semantic checks

Keep these rules in the Python validation layer, not just JSON Schema:

1. Every card ID is unique across the entire deck.
2. Every card links to a known topic and exact learning objective.
3. Every topic meets its planned objective coverage and requested format mix.
4. Prompt duplicates are rejected within a topic; cross-topic duplicates are
   flagged for review, not silently merged.
5. MCQs contain four or five distinct options and one listed correct option
   with a substantive rationale.
6. OSCE items include clear candidate instructions and at least three
   observable checklist items.
7. References must originate from faculty-supplied sources; model-invented
   citations are rejected.
8. Evidence-grounded cards must cite eligible reviewed snippets with exact
   source and locator provenance.
9. Risk heuristics route doses, contraindications, emergency actions,
   thresholds and red flags for stricter human review. Heuristics are not a
   complete risk classifier.
10. Generation never upgrades a card to reviewed or approved.

## Review and publication states

Generated content remains `unreviewed`. A clean generator report means only
that the configured automated checks passed. It is not clinical approval or
examiner approval. Source verification, clinical review, examination/assessment
review, rights checks and the governed release decision must remain explicit
human decisions recorded through the repository's review/release workflow.

## Coverage report

The generator already writes `coverage_report.json` and embeds the same
summary in `quality_report.json`. The companion
`scripts/coverage_report.py` independently recomputes coverage from a
curriculum and generated deck, making it useful for CI, audits and rechecking
saved artifacts.

Example:

```bash
python scripts/coverage_report.py \
  --curriculum examples/ophthalmology/curriculum.json \
  --generated build/ophthalmology/generated_cards.json \
  --output build/ophthalmology/coverage_report.rechecked.json
```

Coverage is a linkage metric: an objective is covered when one or more cards
map to it. It does not measure depth, evidence strength, clinical correctness,
or assessment validity.
