"""Checksum-verifying loaders for frozen synthetic datasets."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

Difficulty = Literal["easy", "medium", "hard"]


@dataclass(frozen=True, slots=True)
class DatasetItem:
    item_id: str
    difficulty: Difficulty
    prompt: str
    target: str | dict[str, object] | None
    input_token_target: int | None = None
    output_token_target: int | None = None


@dataclass(frozen=True, slots=True)
class DatasetBundle:
    name: str
    version: str
    workload: str
    seed: int
    checksum: str
    items: tuple[DatasetItem, ...]


class DatasetIntegrityError(ValueError):
    """A frozen dataset differs from its recorded checksum."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_checksum(checksum_path: Path) -> str:
    try:
        lines = checksum_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise DatasetIntegrityError(f"cannot read checksum file {checksum_path}: {exc}") from exc
    aggregate = hashlib.sha256()
    for line in lines:
        expected, separator, filename = line.partition("  ")
        if not separator or len(expected) != 64 or not filename:
            raise DatasetIntegrityError(f"invalid checksum line in {checksum_path}: {line!r}")
        target = checksum_path.parent / filename
        actual = sha256_file(target)
        if actual != expected:
            raise DatasetIntegrityError(
                f"checksum mismatch for {filename}: expected {expected}, got {actual}"
            )
        aggregate.update(f"{expected}  {filename}\n".encode())
    return aggregate.hexdigest()


def _jsonl(path: Path) -> list[dict[str, object]]:
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetIntegrityError(f"cannot load dataset file {path}: {exc}") from exc


def load_dataset(reference: str | Path, *, root: str | Path = ".") -> DatasetBundle:
    """Load ``<reference>.metadata.json`` and verify every frozen data file."""
    base = Path(reference)
    if not base.is_absolute():
        base = Path(root) / base
    checksum = verify_checksum(base.with_suffix(".sha256"))
    try:
        metadata = json.loads(base.with_suffix(".metadata.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetIntegrityError(f"cannot load metadata for {base}: {exc}") from exc
    prompts = _jsonl(base.with_suffix(".prompts.jsonl"))
    targets_path = base.with_suffix(".targets.jsonl")
    targets = (
        {str(row["id"]): row.get("target") for row in _jsonl(targets_path)}
        if targets_path.exists()
        else {}
    )
    items = tuple(
        DatasetItem(
            item_id=str(row["id"]),
            difficulty=cast(Difficulty, str(row["difficulty"])),
            prompt=str(row["prompt"]),
            target=cast(str | dict[str, object] | None, targets.get(str(row["id"]))),
            input_token_target=(
                int(str(row["input_token_target"])) if "input_token_target" in row else None
            ),
            output_token_target=(
                int(str(row["output_token_target"])) if "output_token_target" in row else None
            ),
        )
        for row in prompts
    )
    if len(items) != metadata["count"]:
        raise DatasetIntegrityError("metadata count does not match prompt records")
    if len({item.item_id for item in items}) != len(items):
        raise DatasetIntegrityError("dataset item identifiers are not unique")
    return DatasetBundle(
        name=str(metadata["name"]),
        version=str(metadata["version"]),
        workload=str(metadata["workload"]),
        seed=int(metadata["seed"]),
        checksum=checksum,
        items=items,
    )
