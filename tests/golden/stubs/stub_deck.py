"""Fake local Ollama ``/api/chat`` for usiphus_auto_deck.  ``BAD`` makes it return defective cards."""
from __future__ import annotations

import json

from _common import as_json, install_urlopen

BAD = False


def _card(topic: str, kind: str, n: int) -> dict:
    base = {
        "card_type": kind, "difficulty": "intermediate", "cognitive_level": "apply",
        "learning_objective": f"Apply core principles of {topic.lower()}",
        "tags": ["synthetic", kind], "references": [], "exam_domain": "Synthetic domain",
        "evidence_status": "unreviewed",
    }
    if kind == "mcq":
        options = [f"Synthetic option {c} describing a plausible step for {topic.lower()}" for c in "ABCD"]
        return {**base, "prompt": f"Which synthetic option is the best first step in {topic.lower()} (item {n})?",
                "options": options, "correct_option": options[0],
                "answer": f"Option A is correct because it addresses the underlying problem in {topic.lower()} first; "
                          "faculty must verify this synthetic teaching point.",
                "rubric": []}
    if kind == "osce":
        return {**base, "prompt": f"Candidate instruction: assess a simulated patient with {topic.lower()} (item {n}).",
                "answer": "Introduce yourself, confirm identity, take a focused history, perform a focused examination "
                          "and explain the plan clearly; faculty must verify these synthetic marking points.",
                "options": [], "correct_option": "",
                "rubric": ["Introduces self and confirms identity", "Takes a focused history",
                           "Explains findings and plan in plain language"]}
    prompt = {"basic": f"What is the key teaching point about {topic.lower()} (item {n})?",
              "viva": f"Explain your approach to {topic.lower()} (item {n}).",
              "clinical_case": f"A synthetic patient presents with {topic.lower()}; what is your reasoning (item {n})?"}[kind]
    return {**base, "prompt": prompt, "options": [], "correct_option": "", "rubric": [],
            "answer": f"A structured explanation of {topic.lower()} covering assessment, differential and safe first steps; "
                      "faculty must verify every synthetic claim."}


def handler(method: str, url: str, body):
    if not url.endswith("/api/chat"):
        return RuntimeError(f"unstubbed request: {url}")
    payload = json.loads(body["messages"][1]["content"])
    topic = payload["topic"]
    cards: list[dict] = []
    for kind, amount in payload["requested_card_mix"].items():
        cards.extend(_card(topic, kind, len(cards) + i + 1) for i in range(amount))
    if BAD:                                   # duplicate prompt + missing answer -> quality gate must block
        cards = [dict(c, prompt=cards[0]["prompt"]) for c in cards]
        cards[-1]["answer"] = ""
    return as_json({"message": {"content": json.dumps({"cards": cards})}})


def install(ctx) -> None:
    import fake_genanki
    fake_genanki.install()
    install_urlopen(ctx, handler)
