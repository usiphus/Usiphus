#!/usr/bin/env python3
"""Generate source-grounded, unreviewed study-card drafts with local Ollama."""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

LOG = logging.getLogger("usiphus.generate")
MAX_CONFIG_BYTES = 2 * 1024 * 1024
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_SOURCE_CHARS = 250_000
CARD_TYPES = {"basic", "mcq", "clinical_case", "viva", "osce"}
COGNITIVE_LEVELS = {"remember", "understand", "apply", "analyze", "evaluate", "create"}
EVIDENCE_STOP_WORDS = {
    "about", "after", "also", "and", "are", "because", "been", "before", "being",
    "both", "but", "can", "could", "does", "during", "each", "either", "from",
    "have", "into", "its", "may", "might", "must", "not", "only", "other",
    "should", "since", "some", "such", "than", "that", "the", "their", "then",
    "there", "these", "this", "those", "through", "under", "very", "was", "were",
    "what", "when", "where", "which", "while", "with", "would", "your",
}
CARD_RESPONSE_SCHEMA = {
    "type": "object",
    "required": ["cards"],
    "additionalProperties": False,
    "properties": {
        "cards": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "prompt", "answer", "learning_objective", "card_type",
                    "difficulty", "cognitive_level", "tags", "source_ids", "evidence_quote",
                ],
                "additionalProperties": False,
                "properties": {
                    "prompt": {"type": "string", "minLength": 10, "maxLength": 500},
                    "answer": {"type": "string", "minLength": 50, "maxLength": 5000},
                    "learning_objective": {"type": "string", "minLength": 10},
                    "card_type": {"type": "string", "enum": sorted(CARD_TYPES)},
                    "difficulty": {"type": "string", "enum": ["beginner", "intermediate", "advanced"]},
                    "cognitive_level": {"type": "string", "enum": sorted(COGNITIVE_LEVELS)},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "source_ids": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                    "evidence_quote": {"type": "string", "minLength": 20, "maxLength": 500},
                    "options": {
                        "type": "array", "minItems": 4, "maxItems": 4, "uniqueItems": True,
                        "items": {"type": "string", "minLength": 10, "maxLength": 500},
                    },
                    "correct_option": {"type": "string"},
                    "rationale": {"type": "string", "minLength": 20},
                    "rubric": {"type": "array", "minItems": 3, "items": {"type": "string", "minLength": 10}},
                },
            },
        },
    },
}


def evidence_quote_candidates(sources: dict[str, dict[str, str]]) -> list[str]:
    candidates = []
    seen = set()
    for source in sources.values():
        content = source.get("content", "")
        for line in content.splitlines():
            line = re.sub(r"^\s*(?:[-*]\s+|#+\s*)", "", line.strip())
            for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", line):
                quote = sentence.strip()
                key = _normalize_evidence(quote)
                if 20 <= len(quote) <= 500 and key not in seen:
                    candidates.append(quote)
                    seen.add(key)
    if not candidates:
        raise GenerationError("Source material contains no usable 20–500 character evidence excerpts")
    return candidates


def card_response_schema(
    source_ids: list[str],
    card_types: list[str],
    evidence_quotes: list[str],
) -> dict[str, Any]:
    schema = json.loads(json.dumps(CARD_RESPONSE_SCHEMA))
    properties = schema["properties"]["cards"]["items"]["properties"]
    properties["source_ids"]["items"]["enum"] = source_ids
    properties["card_type"]["enum"] = card_types
    properties["evidence_quote"]["enum"] = evidence_quotes
    return schema


class GenerationError(ValueError):
    """Raised when generator configuration, source material, or model output is invalid."""


def read_json(path: Path, max_bytes: int | None = MAX_CONFIG_BYTES) -> Any:
    if not path.is_file():
        raise GenerationError(f"File not found: {path}")
    if max_bytes is not None and path.stat().st_size > max_bytes:
        raise GenerationError(f"JSON file exceeds {max_bytes} bytes: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GenerationError(f"Cannot read JSON file {path}: {exc}") from exc


def local_ollama_url(value: Any) -> str:
    raw = str(value or "").strip()
    parsed = urllib.parse.urlsplit(raw)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise GenerationError("Ollama base_url must be a local loopback URL, such as http://localhost:11434")
    try:
        port = parsed.port
    except ValueError as exc:
        raise GenerationError("Ollama base_url has an invalid port") from exc
    netloc = parsed.hostname
    if ":" in netloc:
        netloc = f"[{netloc}]"
    if port is not None:
        netloc = f"{netloc}:{port}"
    return f"{parsed.scheme}://{netloc}"


def load_config(path: Path) -> tuple[dict[str, Any], Path]:
    path = path.expanduser().resolve()
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise GenerationError("Generator config root must be a JSON object")
    allowed = {
        "schema_version", "generator", "specialty", "curriculum",
        "source_material", "batch_size", "max_no_progress_batches", "max_batches",
        "output", "base_cards", "card_types", "format_mix",
    }
    extra = set(payload) - allowed
    if extra:
        raise GenerationError("Unknown config keys: " + ", ".join(sorted(extra)))
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
        raise GenerationError("schema_version must be integer 1")
    specialty = str(payload.get("specialty") or "").strip().casefold()
    if not specialty:
        raise GenerationError("specialty is required")
    generator = payload.get("generator")
    if not isinstance(generator, dict) or set(generator) - {
        "provider", "base_url", "model", "timeout_seconds", "temperature",
    }:
        raise GenerationError("generator must contain supported Ollama settings")
    if generator.get("provider") != "ollama":
        raise GenerationError("generator.provider must be 'ollama'")
    generator["base_url"] = local_ollama_url(generator.get("base_url", "http://localhost:11434"))
    model = str(generator.get("model") or "").strip()
    if not model or len(model) > 200:
        raise GenerationError("generator.model must name an installed Ollama model")
    timeout = generator.get("timeout_seconds", 180)
    if type(timeout) is not int or not 1 <= timeout <= 3600:
        raise GenerationError("generator.timeout_seconds must be an integer from 1 to 3600")
    temperature = generator.get("temperature", 0.2)
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not 0 <= temperature <= 2:
        raise GenerationError("generator.temperature must be a number from 0 to 2")
    curriculum_value = payload.get("curriculum")
    output_value = payload.get("output")
    if not isinstance(curriculum_value, str) or not curriculum_value.strip():
        raise GenerationError("curriculum path is required")
    if not isinstance(output_value, str) or not output_value.strip():
        raise GenerationError("output path is required")
    curriculum = (path.parent / curriculum_value).resolve()
    output = (path.parent / output_value).resolve()
    base_value = payload.get("base_cards")
    base_cards = (path.parent / base_value).resolve() if isinstance(base_value, str) and base_value.strip() else None
    protected = {path, curriculum}
    if base_cards:
        protected.add(base_cards)
    if output in protected:
        raise GenerationError("output must not overwrite the config, curriculum, or base_cards file")
    if not curriculum.is_file():
        raise GenerationError(f"Curriculum file not found: {curriculum}")
    if base_cards and not base_cards.is_file():
        raise GenerationError(f"base_cards file not found: {base_cards}")
    sources = payload.get("source_material")
    if not isinstance(sources, list) or not sources:
        raise GenerationError("source_material must list at least one local .txt or .md source")
    resolved_sources = []
    for index, item in enumerate(sources):
        if not isinstance(item, dict) or set(item) != {"title", "path", "reference"}:
            raise GenerationError(f"source_material[{index}] requires exactly title, path, and reference")
        title, reference, source_path = (
            str(item.get("title") or "").strip(),
            str(item.get("reference") or "").strip(),
            item.get("path"),
        )
        if not title or not reference or not isinstance(source_path, str) or not source_path.strip():
            raise GenerationError(f"source_material[{index}] has an empty title, path, or reference")
        parsed_reference = urllib.parse.urlsplit(reference)
        if parsed_reference.scheme != "https" or not parsed_reference.netloc:
            raise GenerationError(f"source_material[{index}].reference must be an HTTPS source URL")
        resolved = (path.parent / source_path).resolve()
        if resolved.suffix.casefold() not in {".txt", ".md"} or not resolved.is_file():
            raise GenerationError(f"Source must be an existing .txt or .md file: {resolved}")
        if resolved.stat().st_size > MAX_SOURCE_BYTES:
            raise GenerationError(f"Source material exceeds {MAX_SOURCE_BYTES} bytes: {resolved}")
        resolved_sources.append({"title": title, "reference": reference, "path": str(resolved)})
    batch_size = payload.get("batch_size", 20)
    if type(batch_size) is not int or batch_size < 1:
        raise GenerationError("batch_size must be a positive integer")
    no_progress_limit = payload.get("max_no_progress_batches", 3)
    if type(no_progress_limit) is not int or no_progress_limit < 1:
        raise GenerationError("max_no_progress_batches must be a positive integer")
    max_batches = payload.get("max_batches")
    if max_batches is not None and (type(max_batches) is not int or max_batches < 1):
        raise GenerationError("max_batches must be null or a positive integer")
    card_types = payload.get("card_types", ["basic", "mcq", "clinical_case", "viva", "osce"])
    if (
        not isinstance(card_types, list)
        or not card_types
        or any(not isinstance(item, str) or item not in CARD_TYPES for item in card_types)
    ):
        raise GenerationError("card_types must be a non-empty list selected from: " + ", ".join(sorted(CARD_TYPES)))
    card_types = list(dict.fromkeys(card_types))
    format_mix = payload.get("format_mix")
    if format_mix is None:
        format_mix = {card_type: 100 / len(card_types) for card_type in card_types}
    elif (
        not isinstance(format_mix, dict)
        or set(format_mix) != set(card_types)
        or any(
            isinstance(weight, bool)
            or not isinstance(weight, (int, float))
            or not math.isfinite(weight)
            or weight <= 0
            for weight in format_mix.values()
        )
        or not math.isclose(sum(format_mix.values()), 100, abs_tol=0.01)
    ):
        raise GenerationError("format_mix must assign positive percentages to every card_type and sum to 100")
    payload.update(
        specialty=specialty,
        generator=generator,
        curriculum=str(curriculum),
        output=str(output),
        base_cards=str(base_cards) if base_cards else None,
        source_material=resolved_sources,
        batch_size=batch_size,
        max_no_progress_batches=no_progress_limit,
        max_batches=max_batches,
        card_types=list(dict.fromkeys(card_types)),
        format_mix=format_mix,
    )
    return payload, path


def load_sources(rows: list[dict[str, str]]) -> tuple[str, dict[str, dict[str, str]]]:
    blocks = []
    sources = {}
    total_chars = 0
    for index, item in enumerate(rows, start=1):
        source_id = f"SRC-{index:03d}"
        path = Path(item["path"])
        try:
            text = path.read_text(encoding="utf-8-sig").strip()
        except (OSError, UnicodeDecodeError) as exc:
            raise GenerationError(f"Cannot read source material {path}: {exc}") from exc
        if not text:
            raise GenerationError(f"Source material is empty: {path}")
        total_chars += len(text)
        if total_chars > MAX_SOURCE_CHARS:
            raise GenerationError(f"Combined source material exceeds {MAX_SOURCE_CHARS} characters")
        sources[source_id] = {
            "title": item["title"],
            "reference": item["reference"],
            "content": text,
        }
        blocks.append(f"[{source_id}] {item['title']}\n{text}")
    return "\n\n".join(blocks), sources


def call_ollama(
    base_url: str,
    model: str,
    prompt: str,
    timeout: int,
    temperature: float,
    format_schema: dict[str, Any] | None = None,
) -> str:
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": format_schema or "json",
        "options": {"temperature": temperature},
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw_response = response.read(10 * 1024 * 1024 + 1)
            if len(raw_response) > 10 * 1024 * 1024:
                raise GenerationError("Ollama response exceeds 10 MiB")
            result = json.loads(raw_response.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read(2048).decode("utf-8", errors="replace")
        raise GenerationError(f"Ollama returned HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GenerationError(f"Cannot reach local Ollama at {base_url}: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GenerationError(f"Ollama returned an invalid response: {exc}") from exc
    if not isinstance(result, dict) or not isinstance(result.get("response"), str):
        raise GenerationError("Ollama response did not contain generated text")
    return result["response"]


def generation_prompt(
    specialty: str,
    competency: dict[str, Any],
    count: int,
    sources: str,
    card_types: list[str] | None = None,
    previous_prompts: list[str] | None = None,
    format_targets: dict[str, int] | None = None,
) -> str:
    allowed_types = card_types or ["basic", "mcq", "clinical_case", "viva", "osce"]
    avoid = "\n".join(f"- {prompt}" for prompt in (previous_prompts or [])[-30:])
    requested_layouts = format_targets or {card_type: 0 for card_type in allowed_types}
    layout_plan = "\n".join(
        f"- {card_type}: {quantity}" for card_type, quantity in requested_layouts.items()
        if quantity
    )
    return f"""Create up to {count} concise draft medical education flashcards.
Specialty: {specialty}
Curriculum competency: {competency.get("id", competency.get("competency_id"))} — {competency["title"]}
Description: {competency.get("description", "")}
Specialty domain: {competency.get("domain", "Use the specialty and competency context")}
Learning objectives: {json.dumps(competency.get("learning_objectives", []), ensure_ascii=False)}

Use only facts directly supported by the source material below. Do not infer missing
clinical recommendations, dosages, thresholds, or emergency actions. If the source
does not support enough distinct cards, return fewer cards rather than inventing or
repeating facts. Use varied, non-duplicate prompts and vary the cognitive task and
format across the batch. Select card_type from: {", ".join(allowed_types)}.
Target layout mix for this batch (adjust downward or return fewer cards when a
format is not supported by the sources):
{layout_plan}
Vary decision-making, recall, interpretation, explanation and practical
communication tasks as supported by the source and learning objective. Avoid
generic questions that could apply to any specialty. A case must be fictional,
de-identified, clinically coherent, and limited to evidence in the sources.
Return a JSON object with one key "cards", an array of objects.
Avoid creating a card that repeats any of these existing prompts:
{avoid or "(none yet)"}
Each object must contain: prompt, answer, learning_objective, card_type, difficulty
("beginner", "intermediate", or "advanced"), cognitive_level ("remember",
"understand", "apply", "analyze", "evaluate", or "create"), tags (array of strings),
and source_ids (array containing one or more source IDs shown below).
Also include evidence_quote: a short, exact, verbatim excerpt from one cited
source that directly supports the answer. Preserve its wording; do not invent or
join separate source fragments.
For MCQ cards, include four plausible, distinct options, exactly one defensible
correct_option (one exact option string), and a rationale grounded in the cited
source. Include explanations for distractors only when they are also source-based.
For clinical_case cards, include a self-contained fictional case in prompt and a
structured answer that identifies the key problem, reasoning, and source-supported
next step; do not add patient-identifying details or unsupported management.
For viva cards, phrase prompt as an oral-exam question and provide a structured
spoken-answer outline.
For OSCE cards, phrase prompt as a timed station task, include a clear candidate
instruction and at least three distinct, observable, source-supported marking
points. End each answer with sentence punctuation.
Write every answer as a complete, grammatical explanation of at least eight
words. Never return a bare label, list fragment, leading punctuation, or
repeated word. Write clean, single-sentence questions or clear task instructions.
Do not include markdown or commentary.

SOURCE MATERIAL:
{sources}
"""


def validate_generated(
    response_text: str,
    specialty: str,
    competency: dict[str, Any],
    sources: dict[str, dict[str, str]],
    first_number: int,
    count: int,
    allowed_card_types: list[str] | None = None,
) -> list[dict[str, Any]]:
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise GenerationError(f"Model output is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("cards"), list):
        raise GenerationError("Model output must be an object containing a cards list")
    rows = payload["cards"]
    if len(rows) > count:
        raise GenerationError(f"Model returned {len(rows)} cards; expected at most {count}")
    competency_id = str(competency.get("id", competency.get("competency_id")))
    generated = []
    seen_prompts = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise GenerationError(f"Generated card {index} is not an object")
        prompt = str(row.get("prompt") or "").strip()
        answer = str(row.get("answer") or "").strip()
        objective = str(row.get("learning_objective") or "").strip()
        card_type = str(row.get("card_type") or "basic").casefold()
        difficulty = str(row.get("difficulty") or "intermediate").casefold()
        cognitive = str(row.get("cognitive_level") or "understand").casefold()
        if not prompt or not answer or not objective or len(prompt) > 500:
            raise GenerationError(f"Generated card {index} requires prompt, answer, and learning_objective")
        if not prompt.endswith(("?", ":")):
            raise GenerationError(f"Generated card {index} prompt must end with a question or task marker")
        if len(answer.split()) < 8:
            raise GenerationError(f"Generated card {index} answer must contain at least eight words")
        if re.search(r"\b([\w'-]+)\b(?:[\s,;:—-]+)\1\b", prompt, re.IGNORECASE):
            raise GenerationError(f"Generated card {index} prompt repeats a word")
        if re.search(r"\b([\w'-]+)\b(?:[\s,;:—-]+)\1\b", answer, re.IGNORECASE):
            raise GenerationError(f"Generated card {index} answer repeats a word")
        if answer.lstrip().startswith((",", ":", ";")):
            raise GenerationError(f"Generated card {index} answer must be a complete sentence, not a fragment")
        if answer[-1] not in ".!?":
            raise GenerationError(f"Generated card {index} answer must end with sentence punctuation")
        if card_type not in CARD_TYPES:
            raise GenerationError(f"Generated card {index} has unsupported card_type: {card_type}")
        if allowed_card_types is not None and card_type not in allowed_card_types:
            raise GenerationError(f"Generated card {index} uses a card_type not enabled in config: {card_type}")
        if difficulty not in {"beginner", "intermediate", "advanced"}:
            raise GenerationError(f"Generated card {index} has unsupported difficulty: {difficulty}")
        if cognitive not in COGNITIVE_LEVELS:
            raise GenerationError(f"Generated card {index} has unsupported cognitive_level: {cognitive}")
        normalized_prompt = re.sub(r"\W+", " ", prompt.casefold()).strip()
        if normalized_prompt in seen_prompts:
            continue
        seen_prompts.add(normalized_prompt)
        source_ids = row.get("source_ids")
        if isinstance(source_ids, str) and source_ids in sources:
            source_ids = [source_ids]
        if (
            not isinstance(source_ids, list)
            or not source_ids
            or any(not isinstance(source_id, str) or source_id not in sources for source_id in source_ids)
        ):
            raise GenerationError(
                f"Generated card {index} must cite known source_ids; "
                f"received {str(source_ids)[:160]!r}, expected IDs from {', '.join(sources)}"
            )
        evidence_quote = row.get("evidence_quote")
        has_source_content = any("content" in sources[source_id] for source_id in source_ids)
        if has_source_content:
            if not isinstance(evidence_quote, str) or not 20 <= len(evidence_quote.strip()) <= 500:
                raise GenerationError(f"Generated card {index} requires a 20-500 character evidence_quote")
            evidence_quote = evidence_quote.strip()
            supporting_sources = [
                source_id for source_id in source_ids
                if "content" in sources[source_id]
                and _normalize_evidence(sources[source_id]["content"]).find(_normalize_evidence(evidence_quote)) >= 0
            ]
            if not supporting_sources:
                raise GenerationError(
                    f"Generated card {index} evidence_quote {evidence_quote[:120]!r} is not an exact excerpt "
                    "from its cited source material"
                )
            cited_content = "\n".join(sources[source_id]["content"] for source_id in source_ids if "content" in sources[source_id])
            answer_terms = _evidence_terms(answer)
            source_terms = _evidence_terms(cited_content)
            overlap = answer_terms & source_terms
            minimum_overlap = max(2, math.ceil(len(answer_terms) * 0.25))
            if len(overlap) < minimum_overlap:
                raise GenerationError(
                    f"Generated card {index} answer has insufficient meaningful term overlap with cited source "
                    f"({len(overlap)} matched; {minimum_overlap} required)"
                )
            if not (_evidence_terms(prompt) & source_terms):
                raise GenerationError(
                    f"Generated card {index} prompt has no meaningful term overlap with cited source"
                )
            if len(answer_terms & _evidence_terms(evidence_quote)) < 2:
                raise GenerationError(
                    f"Generated card {index} evidence_quote does not meaningfully overlap the answer"
                )
            answer_numbers = set(re.findall(r"(?<!\w)\d+(?:\.\d+)?", answer))
            source_numbers = set(re.findall(r"(?<!\w)\d+(?:\.\d+)?", cited_content))
            unsupported_numbers = answer_numbers - source_numbers
            if unsupported_numbers:
                raise GenerationError(
                    f"Generated card {index} answer contains numeric claims absent from cited sources: "
                    + ", ".join(sorted(unsupported_numbers))
                )
        else:
            supporting_sources = list(source_ids)
        tags = row.get("tags", [])
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            raise GenerationError(f"Generated card {index} tags must be a list of strings")
        card = {
            "card_id": f"GEN-{re.sub(r'[^A-Z0-9]+', '-', competency_id.upper()).strip('-')}-{first_number + index:04d}",
            "specialty": specialty,
            "competency_ids": [competency_id],
            "prompt": prompt,
            "answer": answer,
            "learning_objective": objective,
            "card_type": card_type,
            "exam_domain": str(competency.get("domain") or competency.get("title") or specialty),
            "difficulty": difficulty,
            "cognitive_level": cognitive,
            "tags": list(dict.fromkeys([specialty, *[tag.strip() for tag in tags if tag.strip()]])),
            "source": "Generated draft from: " + "; ".join(sources[source_id]["title"] for source_id in source_ids),
            "references": list(dict.fromkeys(sources[source_id]["reference"] for source_id in source_ids)),
            "evidence_status": "unreviewed",
            "review_status": "draft",
            "generator": {"provider": "ollama", "competency_id": competency_id},
        }
        if has_source_content:
            card["evidence_excerpt"] = evidence_quote
            card["evidence_source_ids"] = supporting_sources
        if card_type == "mcq":
            options, correct = row.get("options"), row.get("correct_option")
            rationale = row.get("rationale")
            clean_options = (
                [option.strip() for option in options]
                if isinstance(options, list) and all(isinstance(option, str) for option in options)
                else []
            )
            if (
                len(clean_options) != 4
                or any(len(option) < 10 for option in clean_options)
                or len({option.casefold() for option in clean_options}) != 4
                or not isinstance(correct, str)
                or correct.strip() not in clean_options
                or not isinstance(rationale, str)
                or len(rationale.strip()) < 20
                or len(rationale.split()) < 5
                or rationale.rstrip()[-1] not in ".!?"
                or re.search(r"\b([\w'-]+)\b(?:[\s,;:—-]+)\1\b", rationale, re.IGNORECASE)
            ):
                raise GenerationError(
                    f"Generated MCQ {index} requires four distinct explanatory options, an exact correct_option, "
                    "and a meaningful rationale"
                )
            card["options"] = clean_options
            card["correct_option"] = correct.strip()
            card["answer"] = f"{answer}\n\nRationale: {rationale.strip()}"
        elif card_type == "osce":
            rubric = row.get("rubric")
            if (
                not isinstance(rubric, list)
                or len(rubric) < 3
                or any(not isinstance(item, str) or len(item.strip()) < 10 for item in rubric)
                or len({item.strip().casefold() for item in rubric if isinstance(item, str)}) != len(rubric)
            ):
                raise GenerationError(
                    f"Generated OSCE card {index} requires at least three distinct, descriptive marking points"
                )
            card["rubric"] = [item.strip() for item in rubric]
        generated.append(card)
    return generated


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temp_name = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def run(config: dict[str, Any], request_fn: Callable[..., str] = call_ollama) -> int:
    curriculum_payload = read_json(Path(config["curriculum"]))
    if not isinstance(curriculum_payload, dict) or not isinstance(curriculum_payload.get("competencies"), list):
        raise GenerationError("Curriculum must contain a competencies list")
    if str(curriculum_payload.get("specialty", "")).casefold() != config["specialty"]:
        raise GenerationError("Configured specialty does not match curriculum specialty")
    competencies = []
    for index, row in enumerate(curriculum_payload["competencies"]):
        if not isinstance(row, dict):
            raise GenerationError(f"Curriculum competency {index} must be an object")
        required = row.get("required", True)
        if not isinstance(required, bool):
            raise GenerationError(f"Curriculum competency {index} required must be boolean")
        if required:
            competencies.append(row)
    if not competencies:
        raise GenerationError("Curriculum has no required competencies")
    card_types = config.get("card_types", ["basic", "mcq", "clinical_case", "viva", "osce"])
    format_mix = config.get(
        "format_mix",
        {card_type: 100 / len(card_types) for card_type in card_types},
    )
    competency_ids = []
    for row in competencies:
        competency_id = str(row.get("id") or row.get("competency_id") or "").strip()
        if not competency_id or not str(row.get("title") or "").strip():
            raise GenerationError("Every required curriculum competency must have an ID and title")
        if competency_id.casefold() in competency_ids:
            raise GenerationError(f"Duplicate curriculum competency ID: {competency_id}")
        competency_ids.append(competency_id.casefold())
    source_text, source_rows = load_sources(config["source_material"])
    evidence_quotes = evidence_quote_candidates(source_rows)
    existing: list[dict[str, Any]] = []
    output = Path(config["output"])
    if output.exists():
        checkpoint = read_json(output, None)
        if (
            not isinstance(checkpoint, dict)
            or checkpoint.get("generation_status") != "draft_unreviewed"
            or str(checkpoint.get("specialty", "")).casefold() != config["specialty"]
            or not isinstance(checkpoint.get("cards"), list)
            or any(not isinstance(row, dict) for row in checkpoint["cards"])
        ):
            raise GenerationError("Existing output is not a resumable draft generated for this specialty")
        for index, card in enumerate(checkpoint["cards"]):
            generator = card.get("generator")
            if not isinstance(generator, dict) or generator.get("provider") != "ollama":
                continue
            excerpt = card.get("evidence_excerpt")
            evidence_ids = card.get("evidence_source_ids")
            if not isinstance(excerpt, str) or not isinstance(evidence_ids, list):
                raise GenerationError(
                    f"Existing generated card {index} lacks a source-verified evidence excerpt; "
                    "start a new output file rather than resuming unverifiable drafts"
                )
            if not any(
                isinstance(source_id, str)
                and source_id in source_rows
                and "content" in source_rows[source_id]
                and _normalize_evidence(excerpt) in _normalize_evidence(source_rows[source_id]["content"])
                for source_id in evidence_ids
            ):
                raise GenerationError(
                    f"Existing generated card {index} evidence excerpt no longer matches configured sources"
                )
        existing.extend(checkpoint["cards"])
    if config["base_cards"]:
        base = read_json(Path(config["base_cards"]), None)
        base_rows = base.get("cards") if isinstance(base, dict) else base
        if not isinstance(base_rows, list) or any(not isinstance(row, dict) for row in base_rows):
            raise GenerationError("base_cards must be a list or an object containing a cards list")
        existing.extend(base_rows)
    all_cards = _deduplicate_existing(existing)
    protected = {
        Path(config["curriculum"]).resolve(),
        *(Path(item["path"]).resolve() for item in config["source_material"]),
    }
    if config["base_cards"]:
        protected.add(Path(config["base_cards"]).resolve())
    if output.resolve() in protected:
        raise GenerationError("Refusing to overwrite source input")

    seen_prompts = {
        _prompt_key(card.get("prompt") or card.get("question"))
        for card in all_cards
    }
    seen_ids = {
        str(card.get("card_id") or card.get("id") or "").strip().casefold()
        for card in all_cards
    }
    sequence_by_competency: dict[str, int] = {}
    competency_by_id = {
        str(row.get("id") or row.get("competency_id")).casefold(): row
        for row in competencies
    }
    for card in all_cards:
        generator = card.get("generator")
        if isinstance(generator, dict):
            competency_id = str(generator.get("competency_id", "")).casefold()
            if competency_id in competency_by_id:
                prefix = "GEN-" + re.sub(r"[^A-Z0-9]+", "-", competency_id.upper()).strip("-") + "-"
                match = re.fullmatch(re.escape(prefix) + r"(\d+)", str(card.get("card_id") or ""))
                current = int(match.group(1)) if match else 0
                sequence_by_competency[competency_id] = max(
                    sequence_by_competency.get(competency_id, 0), current
                )

    def write_checkpoint() -> None:
        generated_cards = [
            card for card in all_cards
            if isinstance(card.get("generator"), dict)
            and card["generator"].get("provider") == "ollama"
        ]
        generated_total = len(generated_cards)
        cards_by_type: dict[str, int] = {}
        for card in generated_cards:
            card_type = str(card.get("card_type") or "basic").casefold()
            cards_by_type[card_type] = cards_by_type.get(card_type, 0) + 1
        result = {
            "schema_version": 1,
            "specialty": config["specialty"],
            "generation_status": "draft_unreviewed",
            "generator": {
                "provider": "ollama",
                "model": config["generator"]["model"],
                "local_only": True,
                "cards_generated": generated_total,
                "batch_size": config["batch_size"],
                "format_mix_target_percent": format_mix,
                "cards_by_type": cards_by_type,
            },
            "review_notice": "Generated cards are unreviewed drafts. Verify every clinical statement against the cited source and obtain qualified review before use.",
            "cards": all_cards,
        }
        atomic_write(output, json.dumps(result, indent=2, ensure_ascii=False) + "\n")

    write_checkpoint()
    no_progress = {str(row.get("id") or row.get("competency_id")).casefold(): 0 for row in competencies}
    active = list(competencies)
    calls = 0
    total_generated_at_start = sum(
        1 for card in all_cards
        if isinstance(card.get("generator"), dict)
        and card["generator"].get("provider") == "ollama"
    )
    while active and (config["max_batches"] is None or calls < config["max_batches"]):
        remaining = []
        for competency in active:
            if config["max_batches"] is not None and calls >= config["max_batches"]:
                remaining.append(competency)
                continue
            competency_id = str(competency.get("id") or competency.get("competency_id"))
            key = competency_id.casefold()
            existing_prompts = [
                str(card.get("prompt") or card.get("question") or "")
                for card in all_cards
                if key in {
                    str(item).casefold()
                    for item in card.get("competency_ids", [])
                }
            ]
            existing_format_counts: dict[str, int] = {}
            for card in all_cards:
                generator = card.get("generator")
                if (
                    isinstance(generator, dict)
                    and str(generator.get("competency_id", "")).casefold() == key
                ):
                    card_type = str(card.get("card_type") or "basic").casefold()
                    existing_format_counts[card_type] = existing_format_counts.get(card_type, 0) + 1
            format_targets = _format_targets(
                card_types, format_mix, config["batch_size"], existing_format_counts,
            )
            prompt = generation_prompt(
                config["specialty"], competency, config["batch_size"], source_text,
                card_types,
                existing_prompts,
                format_targets,
            )
            response_schema = card_response_schema(list(source_rows), card_types, evidence_quotes)
            feedback = None
            rows = None
            for attempt in range(2):
                if attempt and config["max_batches"] is not None and calls >= config["max_batches"]:
                    raise GenerationError(
                        f"Card batch for {competency_id} failed validation, and max_batches prevents a quality retry"
                    )
                request_prompt = prompt
                if feedback:
                    request_prompt += (
                        "\n\nQUALITY-CONTROL RETRY: The previous response was rejected: "
                        f"{feedback[:500]}\nReturn a corrected JSON response that fixes this issue. "
                        "Do not repeat the rejected defect."
                    )
                calls += 1
                try:
                    raw = request_fn(
                        config["generator"]["base_url"],
                        config["generator"]["model"],
                        request_prompt,
                        config["generator"].get("timeout_seconds", 180),
                        config["generator"].get("temperature", 0.2),
                        response_schema,
                    )
                    rows = validate_generated(
                        raw, config["specialty"], competency, source_rows,
                        first_number=1, count=config["batch_size"], allowed_card_types=card_types,
                    )
                    break
                except GenerationError as exc:
                    if attempt:
                        raise GenerationError(
                            f"Model failed card validation after two attempts for {competency_id}: {exc}"
                        ) from exc
                    feedback = str(exc)
                    LOG.warning(
                        "Rejected generated batch for %s; retrying once after quality failure: %s",
                        competency_id, feedback,
                    )
            first_number = sequence_by_competency.get(key, 0) + 1
            assert rows is not None
            new_rows = []
            for row in rows:
                prompt_key = _prompt_key(row["prompt"])
                if prompt_key in seen_prompts:
                    continue
                seen_prompts.add(prompt_key)
                while True:
                    card_id = f"GEN-{re.sub(r'[^A-Z0-9]+', '-', competency_id.upper()).strip('-')}-{first_number:04d}"
                    first_number += 1
                    if card_id.casefold() not in seen_ids:
                        break
                row["card_id"] = card_id
                seen_ids.add(card_id.casefold())
                new_rows.append(row)
            if new_rows:
                all_cards.extend(new_rows)
                sequence_by_competency[key] = first_number - 1
                no_progress[key] = 0
                LOG.info("Generated %d draft cards for %s", len(new_rows), competency_id)
                write_checkpoint()
            else:
                no_progress[key] += 1
                LOG.info(
                    "No new cards for %s (%d/%d consecutive batches)",
                    competency_id, no_progress[key], config["max_no_progress_batches"],
                )
            if no_progress[key] < config["max_no_progress_batches"]:
                remaining.append(competency)
        active = remaining
    if active:
        LOG.info("Stopped after %d batch(es); checkpoint saved for resuming", calls)
    generated_total = sum(
        1 for card in all_cards
        if isinstance(card.get("generator"), dict)
        and card["generator"].get("provider") == "ollama"
    )
    LOG.info("Wrote %d new draft cards to %s (%d total generated)", generated_total - total_generated_at_start, output, generated_total)
    return 0


def _prompt_key(value: Any) -> str:
    return re.sub(r"\W+", " ", str(value or "").casefold()).strip()


def _normalize_evidence(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _evidence_terms(value: str) -> set[str]:
    terms = set()
    for raw_term in re.findall(r"[a-z0-9]+", value.casefold()):
        if raw_term.isdigit() or len(raw_term) < 3 or raw_term in EVIDENCE_STOP_WORDS:
            continue
        term = raw_term
        if len(term) > 5 and term.endswith("ies"):
            term = term[:-3] + "y"
        elif len(term) > 5 and term.endswith("ing"):
            term = term[:-3]
        elif len(term) > 4 and term.endswith("ed"):
            term = term[:-2]
        elif len(term) > 4 and term.endswith("ly"):
            term = term[:-2]
        elif len(term) > 4 and term.endswith("s") and not term.endswith("ss"):
            term = term[:-1]
        terms.add(term)
    return terms


def _format_targets(
    card_types: list[str],
    format_mix: dict[str, float],
    count: int,
    existing_counts: dict[str, int],
) -> dict[str, int]:
    totals = {card_type: existing_counts.get(card_type, 0) for card_type in card_types}
    targets = {card_type: 0 for card_type in card_types}
    weight_total = sum(format_mix.values())
    for _ in range(count):
        next_total = sum(totals.values()) + 1
        selected = max(
            card_types,
            key=lambda card_type: (
                next_total * format_mix[card_type] / weight_total - totals[card_type],
                -card_types.index(card_type),
            ),
        )
        totals[selected] += 1
        targets[selected] += 1
    return targets


def _deduplicate_existing(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ids: dict[str, str] = {}
    prompts: dict[str, str] = {}
    result = []
    for index, card in enumerate(cards):
        card_id = str(card.get("card_id") or card.get("id") or "").strip().casefold()
        prompt = _prompt_key(card.get("prompt") or card.get("question"))
        if not card_id or not prompt:
            raise GenerationError(f"Output contains a missing card ID or prompt at row {index}")
        if card_id in ids or prompt in prompts:
            if ids.get(card_id) == prompt and prompts.get(prompt) == card_id:
                continue
            raise GenerationError(f"Output contains a duplicate card ID or prompt at row {index}")
        ids[card_id] = prompt
        prompts[prompt] = card_id
        result.append(card)
    return result


def self_test() -> int:
    import unittest

    class GeneratorTests(unittest.TestCase):
        def test_local_url_rejects_remote_endpoint(self):
            with self.assertRaises(GenerationError):
                local_ollama_url("https://example.com")

        def test_validate_generates_unreviewed_card_with_source(self):
            competency = {"id": "OPH-01", "title": "Eye care"}
            sources = {"SRC-001": {"title": "Guideline", "reference": "https://example.org/guideline"}}
            response = json.dumps({"cards": [{
                "prompt": "What is an examination?",
                "answer": "A source-supported answer of sufficient length.",
                "learning_objective": "Describe the examination.",
                "card_type": "basic",
                "difficulty": "intermediate",
                "cognitive_level": "understand",
                "tags": ["diagnosis"],
                "source_ids": ["SRC-001"],
            }]})
            cards = validate_generated(response, "ophthalmology", competency, sources, 1, 1)
            self.assertEqual(cards[0]["evidence_status"], "unreviewed")
            self.assertEqual(cards[0]["review_status"], "draft")
            self.assertEqual(cards[0]["references"], ["https://example.org/guideline"])

        def test_unknown_source_is_rejected(self):
            row = {"cards": [{
                "prompt": "Question?",
                "answer": "A source-supported answer.",
                "learning_objective": "Understand.",
                "source_ids": ["SRC-999"],
            }]}
            with self.assertRaises(GenerationError):
                validate_generated(json.dumps(row), "ophthalmology", {"id": "OPH-01", "title": "Eye"}, {}, 1, 1)

        def test_mcq_requires_exact_valid_options(self):
            row = {"cards": [{
                "prompt": "Question?",
                "answer": "A source-supported answer.",
                "learning_objective": "Understand.",
                "card_type": "mcq",
                "options": ["A", "B"],
                "correct_option": "A",
                "source_ids": ["SRC-001"],
            }]}
            source = {"SRC-001": {"title": "Guideline", "reference": "https://example.org"}}
            with self.assertRaises(GenerationError):
                validate_generated(json.dumps(row), "ophthalmology", {"id": "OPH-01", "title": "Eye"}, source, 1, 1)

    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(GeneratorTests)
    )
    return 0 if result.wasSuccessful() else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate source-grounded medical flashcard drafts with local Ollama")
    parser.add_argument("--config", help="Path to generator JSON config")
    parser.add_argument("--self-test", action="store_true", help="Run built-in tests")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    if args.self_test:
        return self_test()
    if not args.config:
        parser.error("--config is required unless --self-test is used")
    try:
        config, _ = load_config(Path(args.config))
        return run(config)
    except (GenerationError, OSError, TypeError, ValueError) as exc:
        LOG.error("%s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
