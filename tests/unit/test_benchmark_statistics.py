from __future__ import annotations

from inference_gateway.benchmark.statistics import (
    claimability,
    clustered_metric_ci,
    paired_quality_effect_ci,
    percentile,
    repeat_ratio_ci,
)


def test_seeded_bootstrap_is_reproducible() -> None:
    clusters = {1: [1.0, 2.0], 2: [3.0, 4.0], 3: [5.0, 6.0]}
    first = clustered_metric_ci(
        clusters, lambda values: percentile(values, 0.95), iterations=500, seed=42
    )
    second = clustered_metric_ci(
        clusters, lambda values: percentile(values, 0.95), iterations=500, seed=42
    )
    assert first == second
    assert first.resampling_unit == "repeat_index"


def test_paired_quality_bootstrap_and_claimability() -> None:
    interval = paired_quality_effect_ci(
        [False] * 20,
        [True] * 20,
        iterations=500,
        seed=7,
    )
    assert interval.low == interval.high == 1.0
    assert interval.resampling_unit == "dataset_item_id"
    assert claimability([0.8, 1.0, 0.9], interval) == "directional"
    assert claimability([0.8, -0.1, 0.9], interval) == "inconclusive"


def test_repeat_ratio_preserves_cost_and_correct_counts() -> None:
    interval = repeat_ratio_ci(
        {1: 2.0, 2: 4.0, 3: 6.0},
        {1: 2, 2: 4, 3: 6},
        iterations=300,
        seed=3,
    )
    assert interval.low == interval.high == 1.0
