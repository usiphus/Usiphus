"""Fake local Ollama ``/api/chat`` for usiphus_auto_deck.

The fake model obeys the request: it returns exactly ``requested_card_count`` cards in the requested mix,
maps cards round-robin onto the supplied learning objectives with their exact wording, tags each card with
a supplied coverage tag, and, when a curated evidence packet is supplied, cites its first snippet.
``BAD`` makes it return defective cards (duplicate prompts, missing answer) so quality gates must reject them.
Responses are streamed as one newline-delimited JSON event, like Ollama.
"""
from __future__ import annotations

import json

from _common import as_json, install_urlopen

BAD = False
LONG_ANSWER = ("A structured explanation of {topic} covering assessment, differential diagnosis, investigations and safe "
               "first steps; faculty must verify every synthetic claim before any teaching use.")


def _card(payload: dict, kind: str, n: int, objective: str) -> dict:
    topic = payload["topic"]
    refs = [r for r in payload["faculty_supplied_reference_sources"] if isinstance(r, str)][:1]
    base = {
        "card_type": kind, "difficulty": "intermediate", "cognitive_level": "apply",
        "learning_objective": objective, "tags": [payload["coverage_tags"][0], kind],
        "references": refs, "exam_domain": payload["curriculum_domain"] or "Synthetic domain",
        "evidence_status": "unreviewed", "options": [], "correct_option": "", "rubric": [],
        "answer": LONG_ANSWER.format(topic=topic.lower()),
    }
    if kind == "mcq":
        options = [f"Synthetic option {c} describing a plausible step for {topic.lower()}" for c in "ABCD"]
        return {**base, "prompt": f"Which synthetic option is the best first step in {topic.lower()} (item {n})?",
                "options": options, "correct_option": options[0]}
    if kind == "osce":
        return {**base, "prompt": f"Candidate instruction: assess and explain your plan to a simulated patient with {topic.lower()} (item {n}):",
                "rubric": ["Introduces self and confirms identity", "Takes a focused history",
                           "Explains findings and plan in plain language"]}
    prompt = {"basic": f"What is the key teaching point about {topic.lower()} (item {n})?",
              "viva": f"Explain your structured approach to {topic.lower()} (item {n}):",
              "clinical_case": ("A synthetic adult patient presents with features of " + topic.lower() +
                                " and stable observations; what is your reasoning and safe first step (item "
                                f"{n})?")}[kind]
    return {**base, "prompt": prompt}


def handler(method: str, url: str, body):
    if not url.endswith("/api/chat"):
        return RuntimeError(f"unstubbed request: {url}")
    payload = json.loads(body["messages"][1]["content"])
    objectives = payload["learning_objectives"]
    cards: list[dict] = []
    for kind, amount in payload["requested_card_mix"].items():
        for _ in range(amount):
            cards.append(_card(payload, kind, len(cards) + 1, objectives[len(cards) % len(objectives)]))
    packet = payload.get("curated_evidence_packet")
    if packet:
        snippet = packet["snippets"][0]
        for number, card in enumerate(cards, 1):
            card["evidence_claims"] = [{"claim_id": f"{payload['topic_id']}-C{number}",
                                        "text": "Synthetic claim grounded in the curated snippet.",
                                        "source_id": snippet["source_id"], "snippet_id": snippet["snippet_id"],
                                        "locator": snippet["locator"]}]
    if BAD:                                   # duplicate prompt + missing answer -> quality gate must reject
        cards = [dict(c, prompt=cards[0]["prompt"]) for c in cards]
        cards[-1]["answer"] = ""
    event = {"message": {"content": json.dumps({"cards": cards})}, "done": True}
    return (json.dumps(event) + "\n").encode("utf-8")


def install(ctx) -> None:
    import fake_genanki
    fake_genanki.install()
    install_urlopen(ctx, handler)
