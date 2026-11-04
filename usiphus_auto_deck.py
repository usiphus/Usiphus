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
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from usiphus_common import atomic_write_text
from usiphus_core import coerce_record, evaluate_quality, report_to_dict
from usiphus_generation_quality import (
    build_generation_coverage_report,
    validate_curriculum_blueprint,
    validate_topic_cards,
)
from usiphus_evidence import (
    load_topic_evidence_packets, preflight_topic_evidence,
    validate_card_evidence, validate_evidence_packet,
)
from usiphus_v7 import export_apkg
from usiphus_lib.ollama import local_ollama_url

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


class CallBudget:
    """Hard cap on real Ollama requests for one run (``--max-ollama-calls``).

    Quality and format retries each send a request, so the cap is enforced per request,
    not per topic.  ``consume`` is called *before* a request is built and raises
    ``RuntimeError`` outside the retry handlers so exhaustion can never be retried away.
    """

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0

    def consume(self) -> None:
        if self.used >= self.limit:
            raise RuntimeError(
                f"--max-ollama-calls budget of {self.limit} requests is exhausted after {self.used} requests; "
                "raise the limit or reduce topics, cards per topic or retries"
            )
        self.used += 1


def normalize_ollama_url(value: Any, *, allow_remote: bool = False) -> str:
    """Return a base URL for Ollama.

    Accepts Ollama's own ``host:port`` convention (``OLLAMA_HOST=127.0.0.1:11434``) by adding
    ``http://``.  Only loopback hosts are accepted unless ``allow_remote`` is set, matching
    ``usiphus_generate``: curricula and curated evidence packets must not leave the machine by accident.
    """
    raw = str(value or "").strip() or DEFAULT_OLLAMA_URL
    if "://" not in raw:
        raw = f"http://{raw}"
    if not allow_remote:
        return local_ollama_url(raw)
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Ollama URL must be an http(s) URL with a host")
    return raw.rstrip("/")


def infer_specialty(cards: list[Any]) -> str | None:
    """The single specialty named by every card that names one, else None (never guess)."""
    found: dict[str, str] = {}
    for card in cards:
        if isinstance(card, dict):
            value = str(card.get("specialty") or "").strip()
            if value:
                found.setdefault(value.casefold(), value)
    return next(iter(found.values())) if len(found) == 1 else None


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
    mix = dict.fromkeys(CARD_TYPE_CYCLE, 0)   # fixed order: iterating a set varies with PYTHONHASHSEED
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
    budget: CallBudget | None = None,
    allow_remote: bool = False,
) -> list[dict[str, Any]]:
    objectives = topic.get("learning_objectives", [])
    source_refs = topic.get("reference_sources", [])
    card_mix = requested_card_mix(count, topic.get("card_mix"))
    payload = {
        "specialty": specialty,
        "learner_level": level,
        "topic": topic.get("name") or topic.get("topic"),
        "topic_id": topic.get("id"),
        "curriculum_domain": topic.get("domain"),
        "domain_id": topic.get("domain_id"),
        "coverage_tags": topic.get("coverage_tags", []),
        "learning_objectives": objectives,
        "exam_emphasis": topic.get("exam_emphasis", []),
        "faculty_supplied_reference_sources": source_refs,
        "curated_evidence_packet": topic.get("evidence_packet"),
        "requested_card_count": count,
        "requested_card_mix": card_mix,
        "quality_feedback": topic.get("_quality_feedback", ""),
    }
    system = (
        "You are an educational drafting assistant for medical faculty. "
        "Respect the supplied curriculum domain, topic ID, coverage tags and learning objectives. "
        "Map every card to one supplied learning objective using that objective's exact wording. "
        "Include at least one supplied coverage_tags value verbatim in each card's tags. "
        "Cover the full scope of the named topic rather than drifting into adjacent topics. "
        "Create accurate, appropriately qualified educational drafts, not clinical advice. "
        "Do not invent references, DOI values, guideline dates, statistics, or quotations. "
        "Use only faculty-supplied references; if none are supplied, return references as []. "
        "When curated_evidence_packet is supplied, map each clinically material factual claim to exact snippet IDs using evidence_claims objects with claim_id, text, source_id, snippet_id and locator. Never cite search results or metadata as evidence. If the packet lacks support, state the gap rather than inventing a claim. "
        "If quality_feedback is present, correct every listed issue in this attempt. "
        "Flag uncertain or guideline-dependent claims in the answer for faculty verification. "
        "Return ONLY a JSON object with a cards array. Each card must have card_id, "
        "specialty, card_type (basic, mcq, viva, osce, clinical_case), difficulty, "
        "prompt, answer, options, correct_option, learning_objective, tags, references, "
        "cognitive_level, exam_domain, rubric, evidence_status, evidence_claims. Every prompt must end in '?' or ':'. "
        "Exactly one correct MCQ option. "
        "Follow requested_card_mix exactly where possible and return exactly requested_card_count cards. "
        "For MCQs use 4-5 plausible options, one unambiguous key, and explain why the key is correct "
        "and why distractors are wrong. Clinical cases must include age/context, focused findings, "
        "a reasoning question and a safe first-step approach; avoid unsupported drug doses. "
        "Viva answers should be structured and exam-ready. OSCE cards need candidate instructions, "
        "expected steps and a concise checklist rubric. Include red flags, differential diagnosis, "
        "investigations and management principles when relevant to the objective. "
        "Use original wording; do not copy textbook passages. evidence_status must be unreviewed."
    )

    base_url = normalize_ollama_url(ollama_url, allow_remote=allow_remote)
    last_error = None
    topic_name = topic.get("name") or topic.get("topic") or "topic"

    # Retry loop to gracefully handle formatting issues or incorrect card counts
    for attempt in range(1, max_retries + 1):
        user_content = json.dumps(payload, ensure_ascii=False)
        if attempt > 1:
            user_content += (
                f"\n\nCRITICAL REMINDER: Your previous attempt failed validation. "
                f"You MUST return a JSON object with EXACTLY {count} cards in the 'cards' array."
            )

        request_body = {
            "model": model,
            # Keep the model loaded between specialty topics and stream tokens so
            # long generations do not need to wait for one giant response body.
            "keep_alive": "30m",
            "stream": True,
            # "format": "json", removed to prevent hangs/timeouts with 8B models
            "options": {"temperature": 0.2, "num_predict": max_output_tokens},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
        }

        req = urllib.request.Request(
            f"{base_url}/api/chat",
            data=json.dumps(request_body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        if budget is not None:
            budget.consume()   # outside the try block: exhaustion must not be retried
        try:
            # Ollama streams newline-delimited JSON. The socket timeout now
            # measures inactivity between chunks rather than total generation time.
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
            cards = parsed.get("cards")

            if not isinstance(cards, list) or not cards:
                raise ValueError("Model response must contain a non-empty cards array")

            if len(cards) != count:
                raise ValueError(
                    f"Model returned {len(cards)} cards; requested exactly {count}"
                )

            # If we reach here, validation succeeded!
            topic_identity = topic.get("id") or topic_name
            prefix = re.sub(r"[^A-Za-z0-9]+", "-", str(topic_identity)).strip("-").upper()
            for index, card in enumerate(cards, 1):
                if not isinstance(card, dict):
                    raise ValueError("Every generated card must be an object")
                card["card_id"] = f"OLLAMA-{prefix[:32]}-{index:03d}"
                card["specialty"] = specialty
                card["evidence_status"] = "unreviewed"
                card.setdefault(
                    "source", "AI-generated draft; faculty verification required"
                )

            return cards

        except (ValueError, RuntimeError, KeyError, json.JSONDecodeError, TimeoutError, socket.timeout, urllib.error.URLError) as exc:
            last_error = exc
            timed_out = isinstance(exc, (TimeoutError, socket.timeout)) or (
                isinstance(exc, urllib.error.URLError)
                and isinstance(getattr(exc, "reason", None), (TimeoutError, socket.timeout))
            )
            if timed_out:
                detail = (
                    f"No Ollama stream data arrived for {request_timeout}s. "
                    "Streaming prevents a slow overall generation from hitting a total-response timeout, "
                    "but a stalled model or very slow first token still needs local diagnosis."
                )
            elif isinstance(exc, urllib.error.URLError):
                detail = f"Could not reach Ollama at {base_url}: {exc.reason}"
            else:
                detail = str(exc)
            last_error = RuntimeError(detail)
            print(
                f"[Warning] Topic '{topic_name}' attempt {attempt}/{max_retries} failed: {detail}",
                file=sys.stderr,
            )
            # Retrying an identical request after a socket inactivity timeout
            # repeats the same expensive generation and can waste another 15 minutes.
            if timed_out:
                break

    raise RuntimeError(
        f"Failed to generate {count} cards for topic '{topic_name}' after {max_retries} attempts. "
        f"Last error: {last_error}. Check that Ollama is running, the model is available, "
        "and your computer has enough RAM/VRAM. Try a smaller --max-output-tokens value, "
        "a smaller card count, or a smaller model before increasing --ollama-timeout."
    )



def generate_validated_topic_cards(
    topic: dict[str, Any],
    *,
    specialty: str,
    level: str,
    model: str,
    ollama_url: str,
    count: int,
    max_output_tokens: int,
    request_timeout: int = 300,
    quality_attempts: int = 3,
    budget: CallBudget | None = None,
    allow_remote: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Regenerate bounded batches with actionable feedback until topic gates pass."""
    working_topic = dict(topic)
    last_error: Exception | None = None
    mix = requested_card_mix(count, topic.get("card_mix"))
    for attempt in range(1, quality_attempts + 1):
        cards = generate_topic_cards(
            working_topic, specialty=specialty, level=level, model=model,
            ollama_url=ollama_url, count=count,
            max_output_tokens=max_output_tokens,
            request_timeout=request_timeout, max_retries=2,
            budget=budget, allow_remote=allow_remote,
        )
        try:
            report = validate_topic_cards(
                topic, cards, specialty=specialty, requested_mix=mix
            )
            packet = topic.get("evidence_packet")
            if packet is not None:
                packet_report = validate_evidence_packet(packet)
                if not packet_report["evidence_ready"]:
                    raise ValueError(
                        "Evidence packet has no eligible reviewed snippets; "
                        "metadata/search results cannot ground clinical claims"
                    )
                evidence_reports = [
                    validate_card_evidence(card, packet) for card in cards
                ]
                invalid = [
                    f"card {index}: {item['errors']}"
                    for index, item in enumerate(evidence_reports, 1)
                    if not item["valid"]
                ]
                if invalid:
                    raise ValueError("Claim-level evidence validation failed: " + "; ".join(invalid))
                report["evidence_claims_mapped"] = sum(
                    item["claim_count"] for item in evidence_reports
                )
                report["high_risk_claim_flags"] = [
                    flag for item in evidence_reports for flag in item["high_risk_flags"]
                ]
                report["evidence_status"] = "evidence_mapped_unreviewed"
                for card in cards:
                    # card["evidence_status"] stays in usiphus_core's shared vocabulary ("unreviewed"): faculty
                    # have not reviewed it, and any other value is rejected by evaluate_quality at export time.
                    # The grounding is recorded separately.
                    card["evidence_mapping_status"] = "evidence_mapped"
            else:
                report["evidence_status"] = "unreviewed_source_gap"
            return cards, report
        except ValueError as exc:
            last_error = exc
            working_topic["_quality_feedback"] = str(exc)
            print(
                f"[Quality retry] {topic.get('id') or topic.get('name')} "
                f"attempt {attempt}/{quality_attempts}: {exc}",
                file=sys.stderr,
            )
    raise RuntimeError(
        f"Topic {topic.get('id') or topic.get('name')} failed quality validation "
        f"after {quality_attempts} attempts: {last_error}"
    )


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
    """Write JSON atomically (unique temp file, fsync) so an interrupted run cannot leave a partial file."""
    atomic_write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", path)


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
    coverage_report: dict[str, Any] | None = None
    blueprint_summary: dict[str, Any] | None = None
    level = args.level
    specialty = args.specialty
    if args.generate:
        if not args.curriculum:
            raise ValueError("--curriculum is required with --generate")
        curriculum = load_curriculum(Path(args.curriculum))
        evidence_inventory = None
        if args.evidence_dir:
            evidence_inventory = load_topic_evidence_packets(
                curriculum, Path(args.evidence_dir)
            )
            print(
                f"[Evidence] Loaded {evidence_inventory['packet_count']} topic packets; "
                f"{len(evidence_inventory['topics_without_packets'])} topics have no packet"
            )
        # Fail before inference or checkpoint reuse when attached packets are not eligible.
        for evidence_topic in curriculum["topics"]:
            if evidence_topic.get("evidence_packet") is not None:
                preflight_topic_evidence(evidence_topic)
        blueprint_summary = (
            validate_curriculum_blueprint(curriculum)
            if curriculum.get("curriculum_domains") else None
        )
        specialty = specialty or curriculum["specialty"]
        level = level or curriculum.get("learner_level", "medical students")
        topic_count = len(curriculum["topics"])
        if args.max_ollama_calls < 1 or topic_count > args.max_ollama_calls:
            raise ValueError(
                f"Curriculum has {topic_count} topics; "
                f"--max-ollama-calls permits {args.max_ollama_calls}. "
                "Reduce topics or raise the limit."
            )
        ollama_url = normalize_ollama_url(args.ollama_url, allow_remote=args.allow_remote_ollama)
        budget = CallBudget(args.max_ollama_calls)
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
        topic_quality_reports: list[dict[str, Any]] = []
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
            loaded_checkpoint = False
            if args.resume and checkpoint_path.exists():
                saved = load_json(checkpoint_path)
                if (
                    saved.get("signature") == signature
                    and isinstance(saved.get("cards"), list)
                    and len(saved["cards"]) == count
                ):
                    cards = saved["cards"]
                    loaded_checkpoint = True
                    print(f"[Resume] {index}/{topic_count}: {topic.get('name') or topic.get('topic')} — loaded {len(cards)} saved cards")
                else:
                    print(f"[Resume] {index}/{topic_count}: checkpoint changed; regenerating topic")
            topic_quality_report = None
            if cards is not None and curriculum.get("curriculum_domains"):
                try:
                    topic_quality_report = validate_topic_cards(
                        topic, cards, specialty=specialty,
                        requested_mix=requested_card_mix(count, topic.get("card_mix")),
                    )
                except ValueError as exc:
                    print(
                        f"[Resume] Saved topic failed current quality gates; regenerating: {exc}",
                        file=sys.stderr,
                    )
                    cards = None
                    loaded_checkpoint = False
            if cards is None:
                print(f"[{index}/{topic_count}] Generating {count} cards: {topic.get('name') or topic.get('topic')}", flush=True)
                if curriculum.get("curriculum_domains"):
                    cards, topic_quality_report = generate_validated_topic_cards(
                        topic, specialty=specialty, level=level, model=args.model,
                        ollama_url=ollama_url, count=count,
                        max_output_tokens=args.max_output_tokens,
                        request_timeout=args.ollama_timeout,
                        budget=budget, allow_remote=args.allow_remote_ollama,
                    )
                else:
                    cards = generate_topic_cards(
                        topic, specialty=specialty, level=level, model=args.model,
                        ollama_url=ollama_url, count=count,
                        max_output_tokens=args.max_output_tokens,
                        request_timeout=args.ollama_timeout,
                        budget=budget, allow_remote=args.allow_remote_ollama,
                    )
            if curriculum.get("curriculum_domains"):
                if topic_quality_report is None:
                    topic_quality_report = validate_topic_cards(
                        topic, cards, specialty=specialty,
                        requested_mix=requested_card_mix(count, topic.get("card_mix")),
                    )
                topic_quality_reports.append(topic_quality_report)
            if not loaded_checkpoint:
                _atomic_write_json(checkpoint_path, {
                    "signature": signature, "topic_id": topic.get("id"),
                    "topic_name": topic.get("name") or topic.get("topic"),
                    "cards": cards,
                })
                print(f"[{index}/{topic_count}] Saved checkpoint ({len(cards)} cards)", flush=True)
            raw_cards.extend(cards)
            total_cards += len(cards)
            print(f"[Progress] {index}/{topic_count} topics complete; {total_cards} cards ready", flush=True)
    elif args.cards:
        payload = load_json(Path(args.cards))
        raw_cards = (
            payload.get("cards", payload) if isinstance(payload, dict) else payload
        )
        if not isinstance(raw_cards, list) or not raw_cards:
            raise ValueError("Cards file must contain a non-empty list")
        specialty = specialty or infer_specialty(raw_cards) or "ophthalmology"  # legacy default
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
    if args.generate and curriculum.get("curriculum_domains"):
        coverage_report = build_generation_coverage_report(
            curriculum, topic_quality_reports, raw_cards
        )
        _atomic_write_json(out / "coverage_report.json", coverage_report)
        if not coverage_report["all_topics_covered"]:
            print("Coverage gate blocked export: one or more curriculum topics are incomplete", file=sys.stderr)
            return 2
    _atomic_write_json(out / "generated_cards.json", {
        "specialty": specialty,
        "learner_level": level,
        "generation_notice": "AI-generated draft; faculty review required",
        "cards": raw_cards,
    })
    _atomic_write_json(out / "quality_report.json", {
        **report_to_dict(report),
        "curriculum_blueprint": blueprint_summary,
        "generation_coverage": coverage_report,
        "faculty_review_required": True,
        "clinical_accuracy_verified": False,
    })
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
    p.add_argument("--evidence-dir", help="Optional directory of per-topic <topic-id>.json packets; attached packets must pass review and rights gates before inference")
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
        "--allow-remote-ollama",
        action="store_true",
        help="Permit a non-loopback Ollama host. Curriculum text and evidence packets are sent to that host.",
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
