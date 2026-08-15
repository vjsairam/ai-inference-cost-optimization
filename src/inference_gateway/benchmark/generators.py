"""Seeded generators for the three synthetic workload families."""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Iterable
from pathlib import Path

DATASET_VERSION = "1.0.0"
DEFAULT_SEED = 20260815

_CATEGORIES = (
    "access",
    "billing",
    "delivery",
    "hardware",
    "network",
    "security",
    "software",
    "storage",
)
_DIFFICULTIES = ("easy", "medium", "hard")


def _stable_lines(rows: Iterable[dict[str, object]]) -> str:
    return "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows)


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _finish(base: Path, metadata: dict[str, object], files: list[Path]) -> None:
    metadata_path = base.with_suffix(".metadata.json")
    _write(metadata_path, json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    files.append(metadata_path)
    lines = []
    for path in sorted(files, key=lambda item: item.name):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.name}\n")
    _write(base.with_suffix(".sha256"), "".join(lines))


def generate_classification(output_dir: Path, seed: int = DEFAULT_SEED, count: int = 320) -> Path:
    if count < 300 or count % len(_CATEGORIES):
        raise ValueError("classification count must be >=300 and divisible by 8")
    rng = random.Random(seed)
    prompts: list[dict[str, object]] = []
    targets: list[dict[str, object]] = []
    verbs = ("cannot", "needs help to", "reports failure to", "asks how to")
    objects = {
        "access": "sign in after a credential reset",
        "billing": "correct a duplicate subscription charge",
        "delivery": "locate a delayed equipment shipment",
        "hardware": "replace a laptop with a failed keyboard",
        "network": "restore connectivity on the office network",
        "security": "report a suspicious authentication notice",
        "software": "resolve an application crash after an update",
        "storage": "recover space from a full shared volume",
    }
    distractors = {
        "access": "The invoice number is mentioned only as an account reference.",
        "billing": "The user can sign in; access is not the issue.",
        "delivery": "The tracking portal loads normally over the network.",
        "hardware": "A security scan passed before the physical fault appeared.",
        "network": "Local storage has ample capacity.",
        "security": "The application itself did not crash.",
        "software": "The device hardware diagnostic passed.",
        "storage": "Network access and credentials both work.",
    }
    for index in range(count):
        category = _CATEGORIES[index % len(_CATEGORIES)]
        difficulty = _DIFFICULTIES[(index // len(_CATEGORIES)) % 3]
        ticket = f"TKT-{seed % 10000:04d}-{index:04d}"
        cue = objects[category]
        body = f"Ticket {ticket}: The requester {rng.choice(verbs)} {cue}."
        if difficulty == "medium":
            body += f" {distractors[category]}"
        elif difficulty == "hard":
            other = _CATEGORIES[(_CATEGORIES.index(category) + 3) % len(_CATEGORIES)]
            body += (
                f" The subject line says {other}, but that earlier issue was resolved."
                f" {distractors[category]} Classify the unresolved request only."
            )
        prompt = "Return exactly one category from: " + ", ".join(_CATEGORIES) + ".\n" + body
        item_id = f"wl01-{index:04d}"
        prompts.append({"id": item_id, "difficulty": difficulty, "prompt": prompt})
        targets.append({"id": item_id, "target": category})
    order = list(range(count))
    rng.shuffle(order)
    prompts = [prompts[index] for index in order]
    targets_by_id = {str(row["id"]): row for row in targets}
    targets = [targets_by_id[str(row["id"])] for row in prompts]
    output_dir.mkdir(parents=True, exist_ok=True)
    base = output_dir / "classification-v1"
    prompt_path = base.with_suffix(".prompts.jsonl")
    target_path = base.with_suffix(".targets.jsonl")
    _write(prompt_path, _stable_lines(prompts))
    _write(target_path, _stable_lines(targets))
    _finish(
        base,
        {
            "name": "classification-v1",
            "version": DATASET_VERSION,
            "workload": "classification",
            "seed": seed,
            "count": count,
            "categories": list(_CATEGORIES),
        },
        [prompt_path, target_path],
    )
    return base


def generate_extraction(output_dir: Path, seed: int = DEFAULT_SEED, count: int = 320) -> Path:
    if count < 300:
        raise ValueError("extraction count must be >=300")
    rng = random.Random(seed + 1)
    severities = ("low", "medium", "high", "critical")
    services = ("accounts", "catalog", "checkout", "notifications", "reporting")
    prompts: list[dict[str, object]] = []
    targets: list[dict[str, object]] = []
    for index in range(count):
        difficulty = _DIFFICULTIES[index % 3]
        incident_id = f"INC-{(seed + index) % 100000:05d}"
        severity = severities[index % len(severities)]
        service = services[(index * 3) % len(services)]
        owner: str | None = None if index % 5 == 0 else f"team-{services[index % len(services)]}"
        opened = f"2026-08-{(index % 28) + 1:02d}T{index % 24:02d}:{index % 60:02d}:00Z"
        if difficulty == "easy":
            text = (
                f"Incident {incident_id}\nSeverity: {severity}\nService: {service}\n"
                f"Opened: {opened}\nOwner: {owner or 'unassigned'}"
            )
        elif difficulty == "medium":
            invoice = rng.randrange(10000, 99999)
            text = (
                f"At {opened}, {service} opened {incident_id} with severity {severity}. "
                f"Owner is {owner or 'not assigned'}. Reference invoice INV-{invoice} "
                "has amount 418.00 and is unrelated."
            )
        else:
            previous = severities[(severities.index(severity) + 1) % len(severities)]
            text = (
                f"Draft header: severity {previous}; superseded. Final record [{incident_id}] "
                f"service={service}; final_severity={severity}; opened_at={opened}; "
                f"owner={owner or 'NULL'}. Ignore order number {rng.randrange(100000, 999999)} "
                "and the draft header."
            )
        prompt = (
            "Return one JSON object with keys incident_id, severity, service, opened_at, owner. "
            "Use null for an unassigned owner and no extra keys.\n" + text
        )
        item_id = f"wl02-{index:04d}"
        target = {
            "incident_id": incident_id,
            "severity": severity,
            "service": service,
            "opened_at": opened,
            "owner": owner,
        }
        prompts.append({"id": item_id, "difficulty": difficulty, "prompt": prompt})
        targets.append({"id": item_id, "target": target})
    order = list(range(count))
    rng.shuffle(order)
    prompts = [prompts[index] for index in order]
    targets_by_id = {str(row["id"]): row for row in targets}
    targets = [targets_by_id[str(row["id"])] for row in prompts]
    output_dir.mkdir(parents=True, exist_ok=True)
    base = output_dir / "extraction-v1"
    prompt_path = base.with_suffix(".prompts.jsonl")
    target_path = base.with_suffix(".targets.jsonl")
    schema_path = base.with_suffix(".schema.json")
    _write(prompt_path, _stable_lines(prompts))
    _write(target_path, _stable_lines(targets))
    _write(
        schema_path,
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "additionalProperties": False,
                "required": ["incident_id", "severity", "service", "opened_at", "owner"],
                "properties": {
                    "incident_id": {"type": "string"},
                    "severity": {"enum": list(severities)},
                    "service": {"type": "string"},
                    "opened_at": {"type": "string"},
                    "owner": {"type": ["string", "null"]},
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    _finish(
        base,
        {
            "name": "extraction-v1",
            "version": DATASET_VERSION,
            "workload": "structured-extraction",
            "seed": seed,
            "count": count,
            "target_schema": schema_path.name,
        },
        [prompt_path, target_path, schema_path],
    )
    return base


def generate_load(output_dir: Path, seed: int = DEFAULT_SEED, count: int = 120) -> Path:
    if count < 1:
        raise ValueError("load count must be positive")
    rng = random.Random(seed + 2)
    profiles = ((64, 32), (256, 64), (512, 128))
    prompts: list[dict[str, object]] = []
    for index in range(count):
        input_target, output_target = profiles[index % len(profiles)]
        words = [f"term{rng.randrange(1000):03d}" for _ in range(max(8, input_target - 20))]
        prompt = (
            f"Summarize the following synthetic terms in about {output_target} tokens.\n"
            + " ".join(words)
        )
        prompts.append(
            {
                "id": f"wl03-{index:04d}",
                "difficulty": _DIFFICULTIES[index % 3],
                "prompt": prompt,
                "input_token_target": input_target,
                "output_token_target": output_target,
            }
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    base = output_dir / "generation-v1"
    prompt_path = base.with_suffix(".prompts.jsonl")
    _write(prompt_path, _stable_lines(prompts))
    _finish(
        base,
        {
            "name": "generation-v1",
            "version": DATASET_VERSION,
            "workload": "generation",
            "seed": seed,
            "count": count,
            "token_target_note": "whitespace-token approximation for controlled local load",
        },
        [prompt_path],
    )
    return base


def generate_all(output_dir: Path, seed: int = DEFAULT_SEED) -> tuple[Path, Path, Path]:
    return (
        generate_classification(output_dir, seed),
        generate_extraction(output_dir, seed),
        generate_load(output_dir, seed),
    )
