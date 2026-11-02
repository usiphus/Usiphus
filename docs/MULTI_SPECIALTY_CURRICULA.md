# Multi-specialty curriculum library

## Scope

The repository now contains a shared index at `examples/specialties/manifest.json`
and 19 additional specialty blueprints alongside the existing ophthalmology
blueprint. The specialty maps are independent files and share a stable structure
compatible with the local deck generator and curriculum-aware resource discovery.

| Specialty | Curriculum file |
|---|---|
| Internal medicine | `examples/specialties/internal_medicine/curriculum.json` |
| General surgery | `examples/specialties/general_surgery/curriculum.json` |
| Pediatrics | `examples/specialties/pediatrics/curriculum.json` |
| Obstetrics and gynaecology | `examples/specialties/obstetrics_gynaecology/curriculum.json` |
| Emergency medicine | `examples/specialties/emergency_medicine/curriculum.json` |
| Family medicine | `examples/specialties/family_medicine/curriculum.json` |
| Psychiatry | `examples/specialties/psychiatry/curriculum.json` |
| Dermatology | `examples/specialties/dermatology/curriculum.json` |
| Otorhinolaryngology (ENT) | `examples/specialties/otorhinolaryngology/curriculum.json` |
| Orthopaedics and trauma | `examples/specialties/orthopaedics/curriculum.json` |
| Urology | `examples/specialties/urology/curriculum.json` |
| Neurology | `examples/specialties/neurology/curriculum.json` |
| Neurosurgery | `examples/specialties/neurosurgery/curriculum.json` |
| Anaesthesiology and critical care | `examples/specialties/anaesthesiology/curriculum.json` |
| Radiology | `examples/specialties/radiology/curriculum.json` |
| Pathology and laboratory medicine | `examples/specialties/pathology/curriculum.json` |
| Cardiothoracic surgery | `examples/specialties/cardiothoracic_surgery/curriculum.json` |
| Plastic, reconstructive and burns surgery | `examples/specialties/plastic_surgery/curriculum.json` |
| Community and public health medicine | `examples/specialties/community_medicine/curriculum.json` |
| Ophthalmology (existing) | `examples/ophthalmology/curriculum.json` |

## Structure

Each new specialty currently has four domains, three topic units per domain,
three learning objectives per topic, four exam-emphasis tags, stable topic IDs,
and six planned draft cards per topic. The default card mix is one basic card,
two MCQs, one clinical case, one viva and one OSCE task.

This is a broad first-pass blueprint, not an exhaustive disease catalogue,
national curriculum, residency logbook or approved examination specification.
Faculty should expand topic granularity, set learner-stage expectations,
weight objectives, remove overlaps and map local outcomes before production
generation. The existing ophthalmology file is not changed by this expansion.

## Resource discovery

Every new topic has source-discovery terms and requests the following source
families:

- Public AnkiWeb shared decks.
- DoctorDE challenge search links.
- Cureus open-access literature.
- PubMed Central, PubMed and DOAJ.
- Public exam-preparation resources such as Geeky Medics and TeachMeSeries.
- Relevant professional society and national guidance.
- Open textbooks and university teaching resources.

Run the discovery engine for one specialty at a time:

```bash
python usiphus_resource_discovery.py \
  --curriculum examples/specialties/internal_medicine/curriculum.json \
  --max-topics 2 --limit 3 --delay 0.34 \
  --output-dir build/internal-medicine-discovery
```

After reviewing a pilot, remove `--max-topics` to process the full specialty
map. Discovery can make multiple public API requests per topic; use a polite
delay and inspect provider errors. Search-link-only records do not count as
verified resources or objective coverage.

## Evidence, licensing and safety

- Anki decks and public exam-preparation/challenge sites are exam-practice
  signals, not clinical authorities.
- Cureus, PubMed Central, PubMed and DOAJ records require appraisal; indexing
  does not establish quality or applicability.
- Professional guidance must be checked for publication date, supersession,
  population and jurisdiction.
- Store metadata and canonical links by default. Do not copy deck contents,
  proprietary challenge questions, article text or figures without appropriate
  reuse rights.
- All discovered records begin unreviewed. Automated relevance is not faculty
  approval and does not establish clinical correctness.
- No institutional bylaw, local university curriculum or local examination
  blueprint was used to create these maps.

## Suggested quality gates before generating production decks

1. Faculty validates every domain, topic and objective against the intended
   specialty scope.
2. Every objective has appropriate source discovery and reviewed references;
   exam-practice sources are separated from clinical evidence.
3. Clinical recommendations are checked against current authoritative guidance.
4. Topic overlap, missing conditions, learner stage and exam weighting are
   reviewed.
5. Generated cards are checked for correctness, clarity, one-best-answer
   quality, safe clinical framing and reference traceability.
