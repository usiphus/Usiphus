"""Fake local Ollama ``/api/generate`` for usiphus_generate.

First request for a competency returns two valid, source-grounded cards; later requests repeat them
(so the engine sees no progress and stops).  ``HALLUCINATE`` makes the evidence quote absent from the source.
"""
from __future__ import annotations

import json
import re

from _common import as_json, install_urlopen

HALLUCINATE = False
_CALLS: dict[str, int] = {}

CARDS = {
    "U1": [
        {"prompt": "How does acute urinary retention typically present?",
         "answer": "Acute urinary retention is the sudden inability to pass urine and is usually painful for the patient.",
         "evidence_quote": "Acute urinary retention is the sudden inability to pass urine and is usually painful.",
         "learning_objective": "Recognise acute urinary retention", "card_type": "basic",
         "difficulty": "beginner", "cognitive_level": "remember", "tags": ["emergency"], "source_ids": ["SRC-001"]},
        {"prompt": "Which immediate step relieves the symptoms of acute urinary retention?",
         "answer": "Immediate bladder drainage by urethral catheter relieves symptoms and allows assessment of residual volume.",
         "evidence_quote": "Immediate bladder drainage by urethral catheter relieves symptoms",
         "learning_objective": "Outline immediate drainage", "card_type": "mcq",
         "difficulty": "intermediate", "cognitive_level": "apply", "tags": ["emergency", "catheter"], "source_ids": ["SRC-001"],
         "options": ["Bladder drainage using a urethral catheter", "Observation at home without any drainage",
                     "Immediate referral for elective scheduled surgery", "Increasing oral fluids over several days"],
         "correct_option": "Bladder drainage using a urethral catheter",
         "rationale": "The source states that catheter drainage relieves symptoms and allows residual volume assessment."},
    ],
    "U2": [
        {"prompt": "Station task: explain the pain and imaging plan to a simulated patient with renal colic:",
         "answer": "Renal colic typically causes severe loin pain that radiates toward the groin, and imaging may confirm a ureteric stone.",
         "evidence_quote": "Renal colic typically causes severe loin pain that radiates toward the groin.",
         "learning_objective": "Select imaging for renal colic", "card_type": "osce",
         "difficulty": "intermediate", "cognitive_level": "apply", "tags": ["stone"], "source_ids": ["SRC-001"],
         "rubric": ["Describes loin pain radiating toward the groin", "Mentions imaging to confirm a ureteric stone",
                    "Checks the patient understanding of the plan"]},
        {"prompt": "When is non-contrast computed tomography commonly used for suspected ureteric stones?",
         "answer": "Non-contrast computed tomography is commonly used to confirm a ureteric stone when imaging is required by the clinician.",
         "evidence_quote": "Non-contrast computed tomography is commonly used to confirm a ureteric stone",
         "learning_objective": "Select imaging for renal colic", "card_type": "basic",
         "difficulty": "beginner", "cognitive_level": "understand", "tags": ["stone", "imaging"], "source_ids": ["SRC-001"]},
    ],
}


def handler(method: str, url: str, body):
    if not url.endswith("/api/generate"):
        return RuntimeError(f"unstubbed request: {url}")
    competency = re.search(r"Curriculum competency: (\S+) —", body["prompt"]).group(1)
    _CALLS[competency] = _CALLS.get(competency, 0) + 1
    cards = [dict(c) for c in CARDS[competency]]
    if HALLUCINATE:
        cards[0]["evidence_quote"] = "A sentence that does not appear anywhere in the supplied source material."
    return as_json({"response": json.dumps({"cards": cards})})


def install(ctx) -> None:
    install_urlopen(ctx, handler)
