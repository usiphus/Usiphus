# Objective-level deck blueprint compiler

The automated generator now compiles a deterministic blueprint before generation.

Each planned card slot is assigned:
- A stable learning-objective ID and objective text.
- An assessment format (basic, MCQ, clinical case, viva or OSCE).
- A cognitive level derived from the assessment format.
- Its topic and curriculum-domain mapping.

Objective strings remain supported. Objective objects may include `id`, `title` or `name`, and an optional positive numeric `weight` (or `priority`). Weighted allocation distributes more slots to higher-priority objectives; unweighted objectives are balanced. A topic with no objectives receives a topic-overview fallback objective.

The model is asked to write content for the exact objective assigned to each slot. Python writes the authoritative objective and curriculum metadata into the card, rather than trusting model-generated metadata. The generated card carries `objective_id`, `topic_id`, `curriculum_domain_id`, `blueprint_slot`, `learning_objective` and `cognitive_level`.

A full `deck_blueprint.json` is saved before model generation begins. It includes all planned slots and per-objective counts, so the blueprint remains available if generation is interrupted. `--plan-only` prints the same blueprint without contacting Ollama or creating output files.

This improves traceability and coverage planning; it does not prove that generated content correctly teaches an objective. Faculty must still review clinical accuracy, objective alignment, MCQ keys, OSCE rubrics and references before release.
