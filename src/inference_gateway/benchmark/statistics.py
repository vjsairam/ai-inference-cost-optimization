"""Seeded bootstrap procedures that preserve item pairing and repeat clusters."""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ConfidenceInterval:
    low: float
    high: float
    confidence: float
    iterations: int
    seed: int
    method: str
    resampling_unit: str


def percentile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    if not 0 <= probability <= 1:
        raise ValueError("probability must be between zero and one")
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _bounds(samples: list[float]) -> tuple[float, float]:
    return percentile(samples, 0.025), percentile(samples, 0.975)


def paired_quality_effect_ci(
    baseline: Sequence[bool],
    treatment: Sequence[bool],
    *,
    iterations: int,
    seed: int,
) -> ConfidenceInterval:
    if len(baseline) != len(treatment) or not baseline:
        raise ValueError("paired quality inputs must be non-empty and equal length")
    rng = random.Random(seed)
    differences = [
        float(candidate) - float(reference)
        for reference, candidate in zip(baseline, treatment, strict=True)
    ]
    samples = []
    for _ in range(iterations):
        draw = [differences[rng.randrange(len(differences))] for _ in differences]
        samples.append(statistics.fmean(draw))
    low, high = _bounds(samples)
    return ConfidenceInterval(
        low=low,
        high=high,
        confidence=0.95,
        iterations=iterations,
        seed=seed,
        method="paired percentile bootstrap",
        resampling_unit="dataset_item_id",
    )


def clustered_metric_ci(
    repeat_values: Mapping[int, Sequence[float]],
    metric: Callable[[Sequence[float]], float],
    *,
    iterations: int,
    seed: int,
) -> ConfidenceInterval:
    if not repeat_values or any(not values for values in repeat_values.values()):
        raise ValueError("every repeat cluster must contain values")
    rng = random.Random(seed)
    clusters = list(repeat_values.values())
    samples = []
    for _ in range(iterations):
        selected = [clusters[rng.randrange(len(clusters))] for _ in clusters]
        flattened = [value for cluster in selected for value in cluster]
        samples.append(metric(flattened))
    low, high = _bounds(samples)
    return ConfidenceInterval(
        low=low,
        high=high,
        confidence=0.95,
        iterations=iterations,
        seed=seed,
        method="cluster percentile bootstrap",
        resampling_unit="repeat_index",
    )


def repeat_ratio_ci(
    numerators: dict[int, float],
    denominators: dict[int, int],
    *,
    iterations: int,
    seed: int,
) -> ConfidenceInterval:
    if set(numerators) != set(denominators) or not numerators:
        raise ValueError("repeat ratios need matching non-empty repeat sets")
    if any(value <= 0 for value in denominators.values()):
        raise ValueError("repeat ratio denominators must be positive")
    rng = random.Random(seed)
    repeat_ids = sorted(numerators)
    samples = []
    for _ in range(iterations):
        selected = [repeat_ids[rng.randrange(len(repeat_ids))] for _ in repeat_ids]
        numerator = sum(numerators[index] for index in selected)
        denominator = sum(denominators[index] for index in selected)
        samples.append(numerator / denominator)
    low, high = _bounds(samples)
    return ConfidenceInterval(
        low=low,
        high=high,
        confidence=0.95,
        iterations=iterations,
        seed=seed,
        method="cluster ratio percentile bootstrap",
        resampling_unit="repeat_index",
    )


def claimability(repeat_effects: Sequence[float], interval: ConfidenceInterval) -> str:
    if not repeat_effects:
        return "inconclusive"
    all_positive = all(value > 0 for value in repeat_effects)
    all_negative = all(value < 0 for value in repeat_effects)
    excludes_zero = interval.low > 0 or interval.high < 0
    return "directional" if (all_positive or all_negative) and excludes_zero else "inconclusive"
