#!/usr/bin/env python3
"""Draft source-linked curriculum competencies with a local Ollama model."""
from __future__ import annotations

import argparse
import json
import logging
import re
import urllib.parse
from pathlib import Path
from typing import Any, Callable

import usiphus_generate as generation

LOG = logging.getLogger("usiphus.competencies")
LEVELS = {"remember", "understand", "apply", "analyze", "evaluate", "create"}


def load_config(path: Path) -> tuple[dict[str, Any], Path]:
    path = path.expanduser().resolve()
    payload = generation.read_json(path)
    if not isinstance(payload, dict):
        raise generation.GenerationError("Competency config root must be a JSON object")
    allowed = {
        "schema_version", "generator", "specialty", "source_material",
        "output", "base_curriculum", "batch_size", "max_no_progress_batches",
        "max_batches",
    }
    extra = set(payload) - allowed
    if extra:
        raise generation.GenerationError("Unknown competency config keys: " + ", ".join(sorted(extra)))
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
        raise generation.GenerationError("schema_version must be integer 1")
    specialty = str(payload.get("specialty") or "").strip().casefold()
    if not specialty:
        raise generation.GenerationError("specialty is required")
    generator = payload.get("generator")
    if not isinstance(generator, dict) or set(generator) - {
        "provider", "base_url", "model", "timeout_seconds", "temperature",
    } or generator.get("provider") != "ollama":
        raise generation.GenerationError("generator must contain supported Ollama settings")
    generator["base_url"] = generation.local_ollama_url(generator.get("base_url", "http://localhost:11434"))
    model = str(generator.get("model") or "").strip()
    if not model or len(model) > 200:
        raise generation.GenerationError("generator.model must name an installed Ollama model")
    timeout = generator.get("timeout_seconds", 180)
    if type(timeout) is not int or not 1 <= timeout <= 3600:
        raise generation.GenerationError("generator.timeout_seconds must be an integer from 1 to 3600")
    temperature = generator.get("temperature", 0.2)
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not 0 <= temperature <= 2:
        raise generation.GenerationError("generator.temperature must be a number from 0 to 2")
    output_value = payload.get("output")
    if not isinstance(output_value, str) or not output_value.strip():
        raise generation.GenerationError("output path is required")
    output = (path.parent / output_value).resolve()
    base_value = payload.get("base_curriculum")
    base = (path.parent / base_value).resolve() if isinstance(base_value, str) and base_value.strip() else None
    if base_value is not None and base is None:
        raise generation.GenerationError("base_curriculum must be a non-empty path or omitted")
    if base and not base.is_file():
        raise generation.GenerationError(f"Base curriculum file not found: {base}")
    sources = payload.get("source_material")
    if not isinstance(sources, list) or not sources:
        raise generation.GenerationError("source_material must list at least one local .txt or .md source")
    resolved_sources = []
    for index, item in enumerate(sources):
        if not isinstance(item, dict) or set(item) != {"title", "path", "reference"}:
            raise generation.GenerationError(f"source_material[{index}] requires exactly title, path, and reference")
        title = str(item.get("title") or "").strip()
        reference = str(item.get("reference") or "").strip()
        source_path = item.get("path")
        parsed = urllib.parse.urlsplit(reference)
        if not title or parsed.scheme != "https" or not parsed.netloc:
            raise generation.GenerationError(f"source_material[{index}] requires a title and HTTPS reference")
        if not isinstance(source_path, str) or not source_path.strip():
            raise generation.GenerationError(f"source_material[{index}].path is required")
        resolved = (path.parent / source_path).resolve()
        if resolved.suffix.casefold() not in {".txt", ".md"} or not resolved.is_file():
            raise generation.GenerationError(f"Source must be an existing .txt or .md file: {resolved}")
        if resolved.stat().st_size > generation.MAX_SOURCE_BYTES:
            raise generation.GenerationError(f"Source material exceeds {generation.MAX_SOURCE_BYTES} bytes: {resolved}")
        resolved_sources.append({"title": title, "reference": reference, "path": str(resolved)})
    batch_size = payload.get("batch_size", 20)
    if type(batch_size) is not int or batch_size < 1:
        raise generation.GenerationError("batch_size must be a positive integer")
    no_progress = payload.get("max_no_progress_batches", 2)
    if type(no_progress) is not int or no_progress < 1:
        raise generation.GenerationError("max_no_progress_batches must be a positive integer")
    max_batches = payload.get("max_batches")
    if max_batches is not None and (type(max_batches) is not int or max_batches < 1):
        raise generation.GenerationError("max_batches must be null or a positive integer")
    protected = {path, *(Path(item["path"]).resolve() for item in resolved_sources)}
    if base:
        protected.add(base)
    if output in protected:
        raise generation.GenerationError("output must not overwrite the config, sources, or base_curriculum")
    payload.update(
        specialty=specialty,
        output=str(output),
        base_curriculum=str(base) if base else None,
        source_material=resolved_sources,
        batch_size=batch_size,
        max_no_progress_batches=no_progress,
        max_batches=max_batches,
    )
    return payload, path


def competency_prompt(
    specialty: str,
    count: int,
    source_text: str,
    previous_titles: list[str],
) -> str:
    avoid = "\n".join(f"- {title}" for title in previous_titles[-50:])
    return f"""Draft up to {count} distinct curriculum competencies for specialty: {specialty}.
Use only competency-level learning outcomes that are directly supported by the
source material. Do not invent guidelines, clinical recommendations, or claims
that the sources do not support. Cover different domains and levels where the
sources support them; do not make superficial variations of existing outcomes.
Return a JSON object with one key "competencies", an array of objects. Each
object must contain: title, description, domain, level ("remember", "understand",
"apply", "analyze", "evaluate", or "create"), required (boolean), weight
(positive number), learning_objectives (non-empty array of strings), and
source_ids (non-empty array of IDs from the source material).
Avoid these existing competency titles:
{avoid or "(none yet)"}
If no additional distinct, source-supported competencies can be drafted, return
an empty competencies array. Do not include markdown or commentary.

SOURCE MATERIAL:
{source_text}
"""


def validate_competencies(
    response_text: str,
    specialty: str,
    sources: dict[str, dict[str, str]],
    first_number: int,
    count: int,
) -> list[dict[str, Any]]:
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise generation.GenerationError(f"Model output is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("competencies"), list):
        raise generation.GenerationError("Model output must be an object containing a competencies list")
    rows = payload["competencies"]
    if len(rows) > count:
        raise generation.GenerationError(f"Model returned {len(rows)} competencies; expected at most {count}")
    generated = []
    seen_titles = set()
    prefix = re.sub(r"[^A-Z0-9]+", "-", specialty.upper()).strip("-") or "SPECIALTY"
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise generation.GenerationError(f"Generated competency {index} is not an object")
        title = str(row.get("title") or "").strip()
        description = str(row.get("description") or "").strip()
        domain = str(row.get("domain") or "").strip()
        level = str(row.get("level") or "").casefold()
        required = row.get("required")
        weight = row.get("weight")
        objectives = row.get("learning_objectives")
        source_ids = row.get("source_ids")
        if not title or len(title) > 250 or not description or not domain:
            raise generation.GenerationError(f"Generated competency {index} requires title, description, and domain")
        if level not in LEVELS:
            raise generation.GenerationError(f"Generated competency {index} has unsupported level: {level}")
        if not isinstance(required, bool):
            raise generation.GenerationError(f"Generated competency {index} required must be boolean")
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or weight <= 0:
            raise generation.GenerationError(f"Generated competency {index} weight must be a positive number")
        if (
            not isinstance(objectives, list)
            or not objectives
            or any(not isinstance(item, str) or not item.strip() for item in objectives)
        ):
            raise generation.GenerationError(f"Generated competency {index} requires learning objectives")
        if (
            not isinstance(source_ids, list)
            or not source_ids
            or any(not isinstance(source_id, str) or source_id not in sources for source_id in source_ids)
        ):
            raise generation.GenerationError(f"Generated competency {index} must cite known source_ids")
        title_key = re.sub(r"\W+", " ", title.casefold()).strip()
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)
        refs = list(dict.fromkeys(sources[source_id]["reference"] for source_id in source_ids))
        generated.append({
            "id": f"AUTO-{prefix}-{first_number + len(generated):04d}",
            "title": title,
            "description": description,
            "domain": domain,
            "level": level,
            "required": required,
            "weight": weight,
            "learning_objectives": [item.strip() for item in objectives],
            "tags": [specialty, domain],
            "source_ids": list(dict.fromkeys(source_ids)),
            "source_references": refs,
            "competency_status": "unvalidated_draft",
        })
    return generated


def run(config: dict[str, Any], request_fn: Callable[..., str] = generation.call_ollama) -> int:
    source_text, source_rows = generation.load_sources(config["source_material"])
    output = Path(config["output"])
    competencies: list[dict[str, Any]] = []
    if config["base_curriculum"]:
        base = generation.read_json(Path(config["base_curriculum"]), None)
        if (
            not isinstance(base, dict)
            or str(base.get("specialty", "")).casefold() != config["specialty"]
            or not isinstance(base.get("competencies"), list)
            or any(not isinstance(row, dict) for row in base["competencies"])
        ):
            raise generation.GenerationError("base_curriculum must match the specialty and contain competencies")
        competencies.extend(base["competencies"])
    if output.exists():
        checkpoint = generation.read_json(output, None)
        if (
            not isinstance(checkpoint, dict)
            or checkpoint.get("generation_status") != "draft_unvalidated"
            or str(checkpoint.get("specialty", "")).casefold() != config["specialty"]
            or not isinstance(checkpoint.get("competencies"), list)
            or any(not isinstance(row, dict) for row in checkpoint["competencies"])
        ):
            raise generation.GenerationError("Existing output is not a resumable competency draft for this specialty")
        competencies = checkpoint["competencies"]
    known_ids = set()
    title_keys = set()
    for index, row in enumerate(competencies):
        competency_id = str(row.get("id") or row.get("competency_id") or "").strip().casefold()
        title_key = re.sub(r"\W+", " ", str(row.get("title") or "").casefold()).strip()
        if not competency_id or not title_key:
            raise generation.GenerationError(f"Existing competency {index} requires an ID and title")
        if competency_id in known_ids or title_key in title_keys:
            raise generation.GenerationError(f"Base curriculum contains a duplicate competency at row {index}")
        known_ids.add(competency_id)
        title_keys.add(title_key)
    previous_count = len(competencies)
    prefix = re.sub(r"[^A-Z0-9]+", "-", config["specialty"].upper()).strip("-") or "SPECIALTY"
    next_number = max(
        (
            int(match.group(1))
            for row in competencies
            if (match := re.fullmatch(
                re.escape(f"AUTO-{prefix}-") + r"(\d+)",
                str(row.get("id") or row.get("competency_id") or ""),
            ))
        ),
        default=0,
    ) + 1
    no_progress = 0

    def write_checkpoint() -> None:
        result = {
            "schema_version": 1,
            "specialty": config["specialty"],
            "draft_status": "unvalidated_ai_draft",
            "generation_status": "draft_unvalidated",
            "source_material": [
                {"title": row["title"], "reference": row["reference"]}
                for row in config["source_material"]
            ],
            "review_notice": "AI-generated draft competencies are unvalidated. Faculty must verify scope, completeness, wording, and source support before curriculum use.",
            "competencies": competencies,
        }
        generation.atomic_write(output, json.dumps(result, indent=2, ensure_ascii=False) + "\n")

    write_checkpoint()
    calls = 0
    while (
        no_progress < config["max_no_progress_batches"]
        and (config["max_batches"] is None or calls < config["max_batches"])
    ):
        existing_titles = [str(row.get("title") or "") for row in competencies]
        prompt = competency_prompt(config["specialty"], config["batch_size"], source_text, existing_titles)
        raw = request_fn(
            config["generator"]["base_url"],
            config["generator"]["model"],
            prompt,
            config["generator"].get("timeout_seconds", 180),
            config["generator"].get("temperature", 0.2),
        )
        calls += 1
        rows = validate_competencies(raw, config["specialty"], source_rows, next_number, config["batch_size"])
        new_rows = []
        for row in rows:
            title_key = re.sub(r"\W+", " ", row["title"].casefold()).strip()
            if title_key in title_keys:
                continue
            while True:
                competency_id = f"AUTO-{prefix}-{next_number:04d}"
                next_number += 1
                if competency_id.casefold() not in known_ids:
                    break
            row["id"] = competency_id
            title_keys.add(title_key)
            known_ids.add(row["id"].casefold())
            new_rows.append(row)
        if new_rows:
            competencies.extend(new_rows)
            no_progress = 0
            LOG.info("Added %d draft competencies", len(new_rows))
            write_checkpoint()
        else:
            no_progress += 1
            LOG.info("No new competencies (%d/%d consecutive batches)", no_progress, config["max_no_progress_batches"])
    if calls >= (config["max_batches"] or calls + 1) and config["max_batches"] is not None:
        LOG.info("Stopped after %d batch(es); checkpoint saved for resuming", calls)
    LOG.info("Wrote %d new competencies to %s (%d total)", len(competencies) - previous_count, output, len(competencies))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Draft source-linked curriculum competencies using local Ollama")
    parser.add_argument("--config", help="Path to competency generator JSON config")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    if not args.config:
        parser.error("--config is required")
    try:
        config, _ = load_config(Path(args.config))
        return run(config)
    except (generation.GenerationError, OSError, TypeError, ValueError) as exc:
        LOG.error("%s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
