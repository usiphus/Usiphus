# Curriculum coverage analyzer

The analyzer turns the resource-discovery output into a curriculum gap report.
It measures whether relevant metadata records have been mapped to each topic
and learning objective, summarizes source-family diversity, and creates a
faculty action queue.

## Run

First run the discovery engine, then:

```bash
python usiphus_curriculum_coverage.py \
  --curriculum examples/ophthalmology/curriculum.json \
  --resources build/ophthalmology-discovery/resources.json \
  --output build/ophthalmology-discovery/curriculum_coverage_report.json
```

Offline unit checks:

```bash
python usiphus_curriculum_coverage.py --self-test
```

The default lexical relevance threshold is 0.08; override with
`--relevance-threshold` (0–1). Search-link-only records are excluded from
resource counts. The report identifies unmapped topics/objectives and
unreviewed metadata for faculty triage.

## Interpretation and safety

Coverage means a metadata record is mapped to a curriculum element. It does
not mean the resource teaches the objective adequately, is current, is
methodologically sound, or is clinically correct. The tool does not score
evidence quality, approve resources, infer learner competence, or generate
clinical recommendations. Faculty must inspect the original resource,
jurisdiction, date, methods, conflicts, and reuse rights before approval.
