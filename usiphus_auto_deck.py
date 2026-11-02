#!/usr/bin/env python3
"""Usiphus local Ollama specialty deck generator.

Generates one canonical card set using a locally running Ollama model and
exports it to Anki (.apkg) and PowerPoint (.pptx). No cloud API or API key.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import hashlib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from usiphus_core import coerce_record, evaluate_quality, report_to_dict
from usiphus_v7 import export_apkg

DEFAULT_OLLAMA_MODEL = "llama3.1:8b"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
MAX_RESPONSE_BYTES = 10_000_000  # Increased to 10MB to support large payloads
CARD_TYPE_CYCLE = ("basic", "mcq", "mcq", "clinical_case", "viva", "osce")
VALID_CARD_MIX_TYPES = {"basic", "mcq", "clinical_case", "viva", "osce"}


def load_json(path: Path) -> Any:
    if path.stat().st_size > 50 * 1024 * 1024:
        raise ValueError(f"Input too large: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^\```(?:json)?\s*|\s*\```$", "", text, flags=re.IGNORECASE)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("Model response did not contain a JSON object")
    return json.loads(text[start : end + 1])


def requested_card_mix(
    count: int, overrides: dict[str, Any] | None = None
) -> dict[str, int]:
    """Return a validated per-topic card-type blueprint summing to count."""
    if count < 1:
        raise ValueError("Card count must be positive")
    if overrides is not None:
        if not isinstance(overrides, dict):
            raise ValueError("card_mix must be a JSON object")
        mix: dict[str, int] = {}
        for card_type, amount in overrides.items():
            if card_type not in VALID_CARD_MIX_TYPES:
                raise ValueError(f"Unsupported card_mix type: {card_type}")
            if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
                raise ValueError("card_mix values must be non-negative integers")
            if amount:
                mix[card_type] = amount
        if sum(mix.values()) != count:
            raise ValueError(f"card_mix must sum to card_count ({count})")
        return mix
    mix = {kind: 0 for kind in VALID_CARD_MIX_TYPES}
    for index in range(count):
        mix[CARD_TYPE_CYCLE[index % len(CARD_TYPE_CYCLE)]] += 1
    return {kind: amount for kind, amount in mix.items() if amount}


def generate_topic_cards(
    topic: dict[str, Any],
    *,
    specialty: str,
    level: str,
    model: str,
    ollama_url: str,
    count: int,
    max_output_tokens: int,
    request_timeout: int = 300,
    max_retries: int = 3,
) -> list[dict[str, Any]]:
    """Generate compact card content and build trusted metadata locally.

    Successful cards are retained across repair attempts. Only missing or
    structurally incomplete slots are sent back to Ollama.
    """
    objectives = topic.get("learning_objectives", [])
    source_refs = topic.get("reference_sources", [])
    card_mix = requested_card_mix(count, topic.get("card_mix"))
    planned_types = [
        kind for kind, amount in card_mix.items() for _ in range(amount)
    ]
    topic_name = topic.get("name") or topic.get("topic") or "topic"
    topic_id = str(topic.get("id") or topic_name)
    coverage_tags = topic.get("coverage_tags", [])
    domain = topic.get("domain") or "General"
    base_url = ollama_url.rstrip("/")
    prefix = re.sub(r"[^A-Za-z0-9]+", "-", str(topic_id)).strip("-").upper()
    prefix = prefix[:24] or "TOPIC"

    # The model writes only pedagogical content. IDs, provenance, tags,
    # evidence state, specialty, and curriculum mapping are deterministic.
    system = (
        "Draft original medical education cards for faculty review. "
        "Be clinically cautious; flag uncertainty in the answer. "
        "Never invent references, guideline dates, statistics, or drug doses. "
        "Return JSON only: {\"cards\":[...]}. Each card has prompt and answer. "
        "MCQ cards also need 4-5 options and one correct_option letter A-E; "
        "explain the key and distractors in answer. OSCE cards need a concise "
        "rubric list. Clinical cases need context, focused findings, a reasoning "
        "question, and a safe first-step approach. Do not add metadata."
    )

    def valid_content(card: Any, card_type: str) -> bool:
        if not isinstance(card, dict):
            return False
        prompt = card.get("prompt")
        answer = card.get("answer")
        if not isinstance(prompt, str) or len(prompt.strip()) < 8:
            return False
        if not isinstance(answer, str) or len(answer.strip()) < 15:
            return False
        if card_type == "mcq":
            options = card.get("options")
            correct = str(card.get("correct_option") or "").strip().upper()
            if not isinstance(options, list) or not 4 <= len(options) <= 5:
                return False
            if any(not isinstance(option, str) or not option.strip() for option in options):
                return False
            if correct not in {"A", "B", "C", "D", "E"}:
                return False
            if ord(correct) - ord("A") >= len(options):
                return False
        if card_type == "osce":
            rubric = card.get("rubric")
            if not isinstance(rubric, list) or not rubric:
                return False
        return True

    pending = list(range(count))
    accepted: dict[int, dict[str, Any]] = {}
    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        if not pending:
            break
        slot_types = [planned_types[index] for index in pending]
        payload = {
            "topic": topic_name,
            "specialty": specialty,
            "learner_level": level,
            "curriculum_domain": domain,
            "learning_objectives": objectives,
            "coverage": coverage_tags,
            "cards_to_write": [
                {"slot": slot, "type": card_type}
                for slot, card_type in zip(pending, slot_types)
            ],
            "requirements": {
                "one_card_per_slot_in_order": True,
                "original_wording": True,
                "references": "Do not generate references; application adds supplied references.",
            },
        }
        if attempt > 1:
            payload["repair_instruction"] = (
                "Previous output had invalid or missing slots. Return only the "
                "requested slots, in order. Keep each card focused and complete."
            )

        # Scale the token budget to the remaining work, not the original topic.
        token_budget = min(
            max_output_tokens,
            max(512, 256 + len(pending) * 560),
        )
        request_body = {
            "model": model,
            "keep_alive": "30m",
            "stream": True,
            "format": "json",
            "options": {"temperature": 0.2, "num_predict": token_budget},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        }
        req = urllib.request.Request(
            f"{base_url}/api/chat",
            data=json.dumps(request_body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            content_parts: list[str] = []
            response_bytes = 0
            with urllib.request.urlopen(req, timeout=request_timeout) as response:
                for line in response:
                    if not line.strip():
                        continue
                    response_bytes += len(line)
                    if response_bytes > MAX_RESPONSE_BYTES:
                        raise ValueError("Ollama response exceeded safety size limit")
                    event = json.loads(line)
                    if event.get("error"):
                        raise RuntimeError(f"Ollama generation error: {event['error']}")
                    message = event.get("message") or {}
                    if message.get("content"):
                        content_parts.append(message["content"])
                    if event.get("done"):
                        break

            content = "".join(content_parts)
            if not content:
                raise ValueError("Ollama returned an empty streamed response")
            parsed = _extract_json(content)
            generated = parsed.get("cards") if isinstance(parsed, dict) else None
            if not isinstance(generated, list):
                raise ValueError("Model response must contain a cards array")

            next_pending: list[int] = []
            for offset, slot in enumerate(pending):
                candidate = generated[offset] if offset < len(generated) else None
                card_type = planned_types[slot]
                if not valid_content(candidate, card_type):
                    next_pending.append(slot)
                    continue
                accepted[slot] = candidate
            pending = next_pending
            if pending:
                print(
                    f"[Repair] Topic '{topic_name}': {len(accepted)}/{count} cards valid; "
                    f"retrying {len(pending)} incomplete card(s)",
                    file=sys.stderr,
                )
        except (ValueError, RuntimeError, KeyError, json.JSONDecodeError,
                TimeoutError, socket.timeout, urllib.error.URLError) as exc:
            last_error = exc
            timed_out = isinstance(exc, (TimeoutError, socket.timeout)) or (
                isinstance(exc, urllib.error.URLError)
                and isinstance(getattr(exc, "reason", None), (TimeoutError, socket.timeout))
            )
            if timed_out:
                detail = (
                    f"No Ollama stream data arrived for {request_timeout}s. "
                    "A slow first token or stalled local model needs local diagnosis."
                )
            elif isinstance(exc, urllib.error.URLError):
                detail = f"Could not reach Ollama at {base_url}: {exc.reason}"
            else:
                detail = str(exc)
            print(
                f"[Warning] Topic '{topic_name}' attempt {attempt}/{max_retries} failed: {detail}",
                file=sys.stderr,
            )
            if timed_out:
                break
            # A malformed batch does not invalidate cards accepted in earlier attempts.
            last_error = RuntimeError(detail)

    if pending:
        raise RuntimeError(
            f"Could not complete {len(pending)} card(s) for topic '{topic_name}' "
            f"after {max_retries} attempt(s). Last error: {last_error or 'incomplete card output'}. "
            "Try a smaller card count, a smaller model, or lower --max-output-tokens."
        )

    result: list[dict[str, Any]] = []
    cognitive_by_type = {
        "basic": "understand",
        "mcq": "apply",
        "clinical_case": "analyze",
        "viva": "understand",
        "osce": "apply",
    }
    for slot in range(count):
        content = accepted[slot]
        card_type = planned_types[slot]
        objective = content.get("learning_objective")
        if not isinstance(objective, str) or not objective.strip():
            objective = objectives[slot % len(objectives)] if objectives else topic_name
        tags = [str(tag) for tag in coverage_tags if str(tag).strip()]
        if topic_id not in tags:
            tags.append(topic_id)
        record = {
            "card_id": f"OLLAMA-{prefix}-{slot + 1:03d}",
            "specialty": specialty,
            "card_type": card_type,
            "difficulty": str(topic.get("difficulty") or "intermediate"),
            "prompt": content["prompt"].strip(),
            "answer": content["answer"].strip(),
            "options": content.get("options", []) if card_type == "mcq" else [],
            "correct_option": content.get("correct_option", "") if card_type == "mcq" else "",
            "learning_objective": objective,
            "tags": tags,
            "source": "AI-generated draft; faculty verification required",
            "references": source_refs if isinstance(source_refs, list) else [],
            "cognitive_level": cognitive_by_type.get(card_type, "understand"),
            "exam_domain": str(topic.get("exam_domain") or domain),
            "rubric": content.get("rubric", []) if card_type == "osce" else [],
            "evidence_status": "unreviewed",
        }
        result.append(record)
    return result


def load_curriculum(path: Path) -> dict[str, Any]:
    data = load_json(path)
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("topics"), list)
        or not data["topics"]
    ):
        raise ValueError(
            "Curriculum JSON must be an object with a non-empty topics array"
        )
    if not data.get("specialty"):
        raise ValueError("Curriculum must specify specialty")
    for item in data["topics"]:
        if not isinstance(item, dict) or not (item.get("name") or item.get("topic")):
            raise ValueError("Each topic needs a name")
    domains = data.get("curriculum_domains")
    if domains is not None:
        if not isinstance(domains, list) or not domains:
            raise ValueError("curriculum_domains must be a non-empty list")
        domain_ids = [item.get("id") for item in domains if isinstance(item, dict)]
        if len(domain_ids) != len(domains) or any(not value for value in domain_ids):
            raise ValueError("Every curriculum domain needs an id")
        if len(set(domain_ids)) != len(domain_ids):
            raise ValueError("Curriculum domain IDs must be unique")
        topic_ids = []
        for item in data["topics"]:
            topic_id = item.get("id")
            if not topic_id or not item.get("domain_id"):
                raise ValueError("Mapped topics need unique id and domain_id fields")
            if item["domain_id"] not in domain_ids:
                raise ValueError(
                    f"Topic {topic_id} references an unknown curriculum domain"
                )
            if (
                not isinstance(item.get("coverage_tags"), list)
                or not item["coverage_tags"]
            ):
                raise ValueError(f"Topic {topic_id} needs coverage_tags")
            topic_ids.append(topic_id)
        if len(set(topic_ids)) != len(topic_ids):
            raise ValueError("Curriculum topic IDs must be unique")
    return data


def export_powerpoint(
    cards: list[Any], destination: Path, title: str, level: str
) -> None:
    try:
        from pptx import Presentation
        from pptx.dml.color import RGBColor
        from pptx.util import Inches, Pt
    except ImportError as exc:
        raise RuntimeError(
            "PowerPoint export requires optional dependency: pip install python-pptx"
        ) from exc

    prs = Presentation()
    prs.core_properties.title = title
    prs.core_properties.subject = f"{level} medical education; faculty-review draft"
    prs.core_properties.author = "Usiphus"
    blank = prs.slide_layouts[6]

    def add_text(
        slide: Any,
        text: str,
        x: float,
        y: float,
        w: float,
        h: float,
        size: int,
        *,
        bold: bool = False,
    ) -> None:
        shape = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        frame = shape.text_frame
        frame.word_wrap = True
        frame.margin_left = Inches(0.12)
        frame.margin_right = Inches(0.12)
        p = frame.paragraphs[0]
        p.text = text
        p.font.size = Pt(size)
        p.font.bold = bold
        p.font.color.rgb = RGBColor(31, 48, 68)

    # Opening slide with visible governance status.
    slide = prs.slides.add_slide(blank)
    add_text(slide, title, 0.7, 1.0, 12.0, 1.3, 30, bold=True)
    add_text(slide, f"{level} • {len(cards)} learning cards", 0.8, 2.5, 11.5, 0.6, 20)
    add_text(
        slide,
        "AI-assisted draft — not clinically approved. Faculty must verify every claim and reference.",
        0.8,
        4.7,
        11.5,
        1.0,
        17,
        bold=True,
    )

    for card in cards:
        slide = prs.slides.add_slide(blank)
        add_text(slide, card.prompt, 0.55, 0.35, 12.1, 1.15, 23, bold=True)
        y = 1.7
        if card.options:
            add_text(
                slide,
                "\n".join(f"{chr(65 + i)}. {v}" for i, v in enumerate(card.options)),
                0.8,
                y,
                11.7,
                min(2.0, 0.42 * len(card.options) + 0.2),
                17,
            )
            y += min(2.2, 0.42 * len(card.options) + 0.35)
        add_text(slide, "Answer / teaching points", 0.8, y, 11.7, 0.35, 16, bold=True)
        add_text(slide, card.answer, 0.8, y + 0.42, 11.7, 2.0, 16)
        footer = f"{card.card_id} • {card.card_type} • {card.evidence_status}"
        if card.references:
            footer += " • " + "; ".join(card.references)
        add_text(slide, footer[:500], 0.8, 6.8, 11.7, 0.35, 9)
    destination.parent.mkdir(parents=True, exist_ok=True)
    prs.save(destination)


def _atomic_write_json(path: Path, payload: Any) -> None:
    """Write JSON atomically so an interrupted run cannot leave a partial checkpoint."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _checkpoint_key(index: int, topic: dict[str, Any]) -> str:
    identity = str(topic.get("id") or topic.get("name") or topic.get("topic") or f"topic-{index}")
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", identity).strip("-")[:48] or f"topic-{index}"
    return f"{index:03d}-{slug}"


def _topic_signature(
    topic: dict[str, Any], *, specialty: str, level: str, model: str,
    count: int, max_output_tokens: int,
) -> str:
    material = {
        "topic": topic, "specialty": specialty, "level": level,
        "model": model, "count": count, "max_output_tokens": max_output_tokens,
    }
    encoded = json.dumps(material, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()



def build(args: argparse.Namespace) -> int:
    if args.generate and args.cards:
        raise ValueError(
            "Choose --generate with --curriculum, or --cards for existing cards"
        )
    raw_cards: list[dict[str, Any]] = []
    level = args.level
    specialty = args.specialty
    if args.generate:
        if not args.curriculum:
            raise ValueError("--curriculum is required with --generate")
        curriculum = load_curriculum(Path(args.curriculum))
        specialty = specialty or curriculum["specialty"]
        level = level or curriculum.get("learner_level", "medical students")
        topic_count = len(curriculum["topics"])
        if args.max_ollama_calls < 1 or topic_count > args.max_ollama_calls:
            raise ValueError(
                f"Curriculum has {topic_count} topics; "
                f"--max-ollama-calls permits {args.max_ollama_calls}. "
                "Reduce topics or raise the limit."
            )
        if not 256 <= args.max_output_tokens <= 16000:
            raise ValueError("--max-output-tokens must be between 256 and 16000")
        if args.cards_per_topic < 1 or args.cards_per_topic > 50:
            raise ValueError("--cards-per-topic must be between 1 and 50")
        if args.ollama_timeout < 1:
            raise ValueError("--ollama-timeout must be at least 1 second")
        out = Path(args.output_dir)
        checkpoint_dir = out / ".checkpoints"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        total_cards = 0
        for index, topic in enumerate(curriculum["topics"], 1):
            count = (
                args.cards_per_topic
                if args.override_curriculum_card_counts
                else int(topic.get("card_count", args.cards_per_topic))
            )
            if count < 1 or count > 50:
                raise ValueError("Each topic card_count must be between 1 and 50")
            key = _checkpoint_key(index, topic)
            checkpoint_path = checkpoint_dir / f"{key}.json"
            signature = _topic_signature(
                topic, specialty=specialty, level=level, model=args.model,
                count=count, max_output_tokens=args.max_output_tokens,
            )
            cards = None
            if args.resume and checkpoint_path.exists():
                saved = load_json(checkpoint_path)
                if (
                    saved.get("signature") == signature
                    and isinstance(saved.get("cards"), list)
                    and len(saved["cards"]) == count
                ):
                    cards = saved["cards"]
                    print(f"[Resume] {index}/{topic_count}: {topic.get('name') or topic.get('topic')} — loaded {len(cards)} saved cards")
                else:
                    print(f"[Resume] {index}/{topic_count}: checkpoint changed; regenerating topic")
            if cards is None:
                print(f"[{index}/{topic_count}] Generating {count} cards: {topic.get('name') or topic.get('topic')}", flush=True)
                cards = generate_topic_cards(
                    topic, specialty=specialty, level=level, model=args.model,
                    ollama_url=args.ollama_url, count=count,
                    max_output_tokens=args.max_output_tokens,
                    request_timeout=args.ollama_timeout,
                )
                _atomic_write_json(checkpoint_path, {
                    "signature": signature, "topic_id": topic.get("id"),
                    "topic_name": topic.get("name") or topic.get("topic"),
                    "cards": cards,
                })
                print(f"[{index}/{topic_count}] Saved checkpoint ({len(cards)} cards)", flush=True)
            raw_cards.extend(cards)
            total_cards += len(cards)
            # Keep a usable partial deck on disk throughout long curriculum runs.
            _atomic_write_json(out / "generated_cards.json", {
                "specialty": specialty,
                "learner_level": level,
                "generation_notice": "AI-generated draft; faculty review required",
                "generation_status": "in_progress",
                "completed_topics": index,
                "total_topics": topic_count,
                "cards": raw_cards,
            })
            print(f"[Progress] {index}/{topic_count} topics complete; {total_cards} cards ready", flush=True)
    elif args.cards:
        payload = load_json(Path(args.cards))
        raw_cards = (
            payload.get("cards", payload) if isinstance(payload, dict) else payload
        )
        if not isinstance(raw_cards, list) or not raw_cards:
            raise ValueError("Cards file must contain a non-empty list")
        specialty = specialty or "ophthalmology"
        level = level or "medical students"
    else:
        raise ValueError("Supply --generate --curriculum or --cards")

    cards = [
        coerce_record(
            item,
            default_specialty=specialty,
            source_label="Usiphus automated generator",
            index=i,
        )
        for i, item in enumerate(raw_cards)
    ]
    report = evaluate_quality(cards, require_reviewed=args.require_reviewed)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(out / "generated_cards.json", {
        "specialty": specialty,
        "learner_level": level,
        "generation_notice": "AI-generated draft; faculty review required",
        "generation_status": "complete",
        "cards": raw_cards,
    })
    (out / "quality_report.json").write_text(
        json.dumps(
            {
                **report_to_dict(report),
                "faculty_review_required": True,
                "clinical_accuracy_verified": False,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    if report.error_count and not args.force:
        print(
            f"Quality gate blocked export: {report.error_count} errors; inspect {out / 'quality_report.json'}",
            file=sys.stderr,
        )
        return 2
    if args.formats in {"both", "anki"}:
        slug = re.sub(r"[^a-z0-9]+", "-", specialty.casefold()).strip("-") or "medical"
        export_apkg(
            cards,
            out / f"{slug}.apkg",
            args.deck_name or f"Usiphus::{specialty.title()}",
        )
    if args.formats in {"both", "powerpoint"}:
        slug = re.sub(r"[^a-z0-9]+", "-", specialty.casefold()).strip("-") or "medical"
        export_powerpoint(
            cards,
            out / f"{slug}.pptx",
            args.deck_title or f"{specialty.title()} — Medical Education",
            level,
        )
    print(f"Generated {len(cards)} cards in {out.resolve()}")
    print(f"Quality: {report.error_count} errors, {report.warning_count} warnings")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--generate",
        action="store_true",
        help="Generate content with a local Ollama model",
    )
    source.add_argument("--cards", help="Use an existing Usiphus card JSON file")
    p.add_argument("--curriculum", help="Curriculum JSON with specialty and topics")
    p.add_argument("--specialty", help="Override the specialty in the curriculum")
    p.add_argument("--level", help="Override the learner level in the curriculum")
    p.add_argument(
        "--model", default=os.getenv("USIPHUS_OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
    )
    p.add_argument(
        "--ollama-url",
        default=os.getenv("OLLAMA_HOST", DEFAULT_OLLAMA_URL),
        help="Ollama server base URL (default: http://localhost:11434)",
    )
    p.add_argument(
        "--cards-per-topic",
        type=int,
        default=10,
        help="Default cards per topic if not specified in topic (default: 10)",
    )
    p.add_argument(
        "--max-ollama-calls",
        type=int,
        default=500,
        help="Maximum local generation requests (default: 500)",
    )
    p.add_argument(
        "--ollama-timeout",
        type=int,
        default=300,
        help="Seconds to wait for each local Ollama request (default: 300)",
    )
    p.add_argument(
        "--override-curriculum-card-counts",
        action="store_true",
        help="Use --cards-per-topic for every topic, overriding topic-level card_count",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        help="Reuse matching per-topic checkpoints from a previous interrupted run",
    )
    p.add_argument(
        "--max-output-tokens",
        type=int,
        default=4096,
        help="Maximum generated tokens per topic request (default: 4096)",
    )
    p.add_argument("--formats", choices=("both", "anki", "powerpoint"), default="both")
    p.add_argument("--output-dir", default="build/ophthalmology")
    p.add_argument("--deck-name")
    p.add_argument("--deck-title")
    p.add_argument("--require-reviewed", action="store_true")
    p.add_argument(
        "--force",
        action="store_true",
        help="Explicitly override structural quality errors",
    )
    return p.parse_args(argv)


if __name__ == "__main__":
    try:
        raise SystemExit(build(parse_args()))
    except (ValueError, RuntimeError, OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)  # noqa: B904
