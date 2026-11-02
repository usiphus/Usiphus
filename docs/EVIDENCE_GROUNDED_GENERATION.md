# Evidence-grounded medical card generation

## What this workflow does

Usiphus can now pass curated source excerpts into the local Ollama generation
request, ask the model to decompose each card into atomic clinical claims, and
attach source IDs to each claim. The application validates that every cited ID
belongs to a supplied source excerpt and writes a faculty review queue.

This is a provenance and review workflow, **not an automated clinical fact
checker**. A valid source ID proves only that the source was supplied; it does
not prove that the excerpt entails the claim, that the excerpt is current, or
that the source is authoritative. Faculty must verify the semantic support,
clinical context, conflicts, dates and applicable jurisdiction.

## Source record format

Add curated excerpts to each topic's `reference_sources` array:

```json
{
  "id": "aaO-guideline-2024",
  "title": "Professional guidance on example topic",
  "url": "https://example.org/guidance",
  "source_type": "professional_guideline",
  "organization": "Relevant professional body",
  "published_at": "2024-05-01",
  "retrieved_at": "2026-11-03",
  "licence": "link_only",
  "excerpt": "A short, faculty-curated excerpt that is permitted to be stored and used."
}
```

The fields `id`, `title` and `excerpt` are the minimum useful evidence
record. `url`, source type, organization, dates and licence improve review
and provenance. Keep excerpts short and comply with copyright, licence, site
terms and access restrictions. Do not paste paywalled or otherwise restricted
content unless its terms permit this use.

Legacy string references and objects containing only a URL remain bibliography
metadata. They are **not** counted as evidence excerpts.

## Run modes

Normal assisted mode is backward-compatible. It uses any available curated
excerpts and marks cards without excerpts as
`unverified_no_source_excerpt` (in `evidence_review_state`).

To fail before generation unless every topic has at least one usable excerpt:

```bash
python usiphus_auto_deck.py --generate \
  --curriculum examples/ophthalmology/curriculum.json \
  --require-evidence --plan-only
```

Remove `--plan-only` to start generation after the source check passes. The
current example ophthalmology curriculum has empty `reference_sources`
arrays, so it will correctly fail this strict check until faculty curate
excerpts for every topic.

## Claim-level output

When excerpts are available, the model is asked to return a `claims` array.
Each claim includes its text and `source_ids`. The application:

- accepts only IDs belonging to supplied sources with non-empty excerpts;
- flags unknown IDs and claims with no usable source link;
- records potential conflicts reported by the model for faculty adjudication;
- labels traceable claims
  `source_linked_pending_human_verification` (in `evidence_review_state`), never approved;
- labels missing or invalid claim mapping `needs_evidence_review` (in `evidence_review_state`).

The model may miss a conflict or incorrectly associate an excerpt with a
claim. Conflict detection is therefore a review prompt, not a guarantee that
all contradictions have been found.

## Outputs

In addition to `generated_cards.json`, the generator writes
`evidence_review_queue.json`. It contains card IDs, objective mapping,
claim-level source links, evidence flags, potential conflicts and the original
source records needed for review. The canonical generated card JSON retains
the structured source records and provenance fields.

Do not publish or teach from generated cards solely because their status is
source-linked. The workflow deliberately keeps faculty verification separate
from source discovery and automated traceability.
