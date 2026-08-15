from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from inference_gateway.benchmark.datasets import DatasetIntegrityError, load_dataset
from inference_gateway.benchmark.generators import (
    generate_classification,
    generate_extraction,
    generate_load,
)


def test_generators_are_deterministic_for_fixed_seed(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    for generator in (generate_classification, generate_extraction, generate_load):
        first_base = generator(first, seed=77)
        second_base = generator(second, seed=77)
        assert (
            first_base.with_suffix(".sha256").read_text()
            == second_base.with_suffix(".sha256").read_text()
        )


def test_classification_dataset_constraints() -> None:
    bundle = load_dataset("benchmark/datasets/synthetic/classification-v1")
    assert len(bundle.items) >= 300
    assert len({item.item_id for item in bundle.items}) == len(bundle.items)
    labels = Counter(item.target for item in bundle.items)
    assert len(labels) >= 8
    assert min(labels.values()) / len(bundle.items) >= 0.08
    difficulties = Counter(item.difficulty for item in bundle.items)
    assert all(difficulties[tier] / len(bundle.items) >= 0.20 for tier in difficulties)
    assert all("subject line" in item.prompt for item in bundle.items if item.difficulty == "hard")


def test_extraction_and_generation_dataset_constraints() -> None:
    extraction = load_dataset("benchmark/datasets/synthetic/extraction-v1")
    assert len(extraction.items) >= 300
    assert any(
        isinstance(item.target, dict) and item.target["owner"] is None for item in extraction.items
    )
    assert any("invoice" in item.prompt.casefold() for item in extraction.items)
    assert {item.difficulty for item in extraction.items} == {"easy", "medium", "hard"}
    generation = load_dataset("benchmark/datasets/synthetic/generation-v1")
    assert all(item.target is None for item in generation.items)
    assert {item.input_token_target for item in generation.items} == {64, 256, 512}
    assert {item.output_token_target for item in generation.items} == {32, 64, 128}


def test_loader_rejects_checksum_mismatch(tmp_path: Path) -> None:
    base = generate_classification(tmp_path, seed=5)
    with base.with_suffix(".prompts.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("{}\n")
    with pytest.raises(DatasetIntegrityError, match="checksum mismatch"):
        load_dataset(base)
