from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from inference_gateway.benchmark.models import BenchmarkRecord, TokenUsageRecord, load_scenario
from inference_gateway.benchmark.report import build_report
from inference_gateway.benchmark.slo import load_slo
from inference_gateway.models import QualityTier, UsageSource

ROOT = Path(__file__).resolve().parents[2]


def _run_dir(
    tmp_path: Path,
    *,
    provider: str,
    correct: bool | None,
    provider_mode: str = "local-mock",
    publishable: bool = False,
    location: str = "local-mock",
    node_group: str | None = None,
) -> Path:
    scenario_name = "generation-local.yaml" if correct is None else "classification-local.yaml"
    scenario = load_scenario(ROOT / "benchmark/scenarios" / scenario_name).model_copy(
        update={
            "provider_mode": provider_mode,
            "publishable": publishable,
            "requests": 1,
            "warmup_requests": 0,
            "repeats": 1,
            "bootstrap_iterations": 100,
        }
    )
    slo, _ = load_slo(ROOT / scenario.slo_config)
    run_dir = tmp_path / "report-run"
    run_dir.mkdir(parents=True)
    manifest: dict[str, object] = {
        "run_id": "report-test",
        "scenario": scenario.model_dump(mode="json", by_alias=True),
        "slo": {"target": slo.require_cell(scenario.slo_cell).model_dump(mode="json")},
        "statistics": {"repeat_group_id": "report-test"},
        "harness": {"location": location, "node_group": node_group},
    }
    (run_dir / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
    started = datetime(2026, 8, 15, tzinfo=UTC)
    record = BenchmarkRecord(
        run_id="report-test",
        repeat_index=1,
        request_id="request-1",
        workload=scenario.workload,
        dataset_item_id="item-1",
        difficulty="easy",
        data_class=scenario.data_class,
        quality_tier=scenario.quality_tier,
        route=provider,
        provider=provider,
        model_alias="lab-default",
        started_at=started,
        completed_at=started + timedelta(seconds=2),
        ttft_ms=10,
        e2e_ms=20,
        usage=TokenUsageRecord(
            input_tokens=4,
            input_tokens_source=UsageSource.PROVIDER_REPORTED,
            visible_output_tokens=2,
            visible_output_tokens_source=UsageSource.PROVIDER_REPORTED,
            billed_input_tokens=4,
            billed_input_tokens_source=UsageSource.PROVIDER_REPORTED,
            billed_output_tokens=2,
            billed_output_tokens_source=UsageSource.PROVIDER_REPORTED,
        ),
        http_status=200,
        error_class=None,
        fallback_count=0,
        task_correct=correct,
        quality_score=(
            None if correct is None else {"expected": "expected", "normalized_prediction": "wrong"}
        ),
        managed_inference_cost_usd="0.01" if provider.startswith("managed") else None,
    )
    (run_dir / "records.jsonl").write_text(record.json_line() + "\n", encoding="utf-8")
    return run_dir


def test_zero_quality_remains_zero_in_scenario_grid(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path, provider="private-vllm", correct=False)

    build_report(run_dir, ROOT)

    cost = json.loads((run_dir / "cost.json").read_text(encoding="utf-8"))
    measured_rows = [
        row
        for row in cost["scenario_grid"]["rows"]
        if row["quality_sensitivity_path"] == "measured"
    ]
    assert measured_rows
    assert {row["managed_quality_rate"] for row in measured_rows} == {"0.0"}
    assert {row["private_quality_rate"] for row in measured_rows} == {"0.0"}
    assert cost["scenario_grid"]["quality_basis"] == "observed_run_quality_rate"


def test_unmeasured_quality_uses_labeled_neutral_grid_center(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path, provider="private-vllm", correct=None)

    build_report(run_dir, ROOT)

    cost = json.loads((run_dir / "cost.json").read_text(encoding="utf-8"))
    assert cost["scenario_grid"]["quality_basis"] == "quality_not_measured"
    measured = next(
        row
        for row in cost["scenario_grid"]["rows"]
        if row["quality_sensitivity_path"] == "measured"
    )
    assert measured["managed_quality_rate"] == "1"
    assert measured["private_quality_rate"] == "1"


def test_private_billed_hours_override_and_source_labels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    estimated_dir = _run_dir(tmp_path, provider="private-vllm", correct=True)
    build_report(estimated_dir, ROOT)
    estimated = json.loads((estimated_dir / "cost.json").read_text(encoding="utf-8"))
    assert estimated["billed_hours_source"] == "request-span-estimate"
    assert float(estimated["request_span_estimate_billed_hours"]) == pytest.approx(2 / 3600)

    measured_dir = _run_dir(tmp_path / "measured", provider="private-vllm", correct=True)
    monkeypatch.setenv("BENCHMARK_PRIVATE_BILLED_HOURS", "2.5")
    build_report(measured_dir, ROOT)
    measured = json.loads((measured_dir / "cost.json").read_text(encoding="utf-8"))
    assert measured["billed_hours_source"] == "operator-measured"
    assert (
        measured["request_span_estimate_billed_hours"]
        == estimated["request_span_estimate_billed_hours"]
    )
    billed_inputs = measured["private_billed_inputs"]
    assert {
        billed_inputs[field]
        for field in (
            "gpu_node_billed_hours",
            "cpu_node_billed_hours",
            "model_storage_billed_hours",
            "shared_platform_billed_hours",
        )
    } == {"2.5"}


def test_observed_cost_payload_labels_only_meaningful_token_definitions(tmp_path: Path) -> None:
    private_dir = _run_dir(tmp_path / "private", provider="private-vllm", correct=True)
    build_report(private_dir, ROOT)
    private_cost = json.loads((private_dir / "cost.json").read_text(encoding="utf-8"))
    private_view = private_cost["views"]["view_a"]["private"]
    managed_view = private_cost["views"]["view_a"]["managed"]

    assert private_view["token_count"] == 6
    assert private_view["token_definition"] == "normalized input + visible output tokens"
    assert Decimal(private_view["cost_per_1m_tokens_usd"]) == (
        Decimal(private_view["total_usd"]) * Decimal(1_000_000) / Decimal(6)
    )
    assert managed_view["cost_per_1m_tokens_usd"] is None
    assert managed_view["token_definition"] is None

    managed_dir = _run_dir(tmp_path / "managed", provider="managed-premium", correct=True)
    build_report(managed_dir, ROOT)
    managed_cost = json.loads((managed_dir / "cost.json").read_text(encoding="utf-8"))
    managed_view = managed_cost["views"]["view_a"]["managed"]
    private_view = managed_cost["views"]["view_a"]["private"]

    assert managed_view["token_count"] == 6
    assert managed_view["token_definition"] == "provider-billed input + output tokens"
    assert Decimal(managed_view["cost_per_1m_tokens_usd"]) == (
        Decimal(managed_view["total_usd"]) * Decimal(1_000_000) / Decimal(6)
    )
    assert private_view["cost_per_1m_tokens_usd"] is None
    assert private_view["token_definition"] is None


def test_provider_mode_mismatches_are_reported_for_nonpublishable_run(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path, provider="private-vllm", correct=True, provider_mode="managed")

    summary = build_report(run_dir, ROOT)

    assert summary["provider_mode_mismatches"] == 1
    saved = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert saved["provider_mode_mismatches"] == 1


def test_publishable_provider_mode_mismatch_is_a_hard_error(tmp_path: Path) -> None:
    run_dir = _run_dir(
        tmp_path,
        provider="private-vllm",
        correct=True,
        provider_mode="managed",
        publishable=True,
    )

    with pytest.raises(ValueError, match="1 provider_mode mismatches"):
        build_report(run_dir, ROOT)

    saved = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert saved["provider_mode_mismatches"] == 1


def test_local_report_limitations_are_unchanged(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path, provider="private-vllm", correct=True)

    summary = build_report(run_dir, ROOT)

    assert summary["limitations"] == [
        "Local deterministic adapter run; timings and costs are plumbing evidence only.",
        "No treatment comparison was run, so no directional claim is available.",
    ]


def test_cloud_report_names_placement_and_missing_gpu_telemetry(tmp_path: Path) -> None:
    run_dir = _run_dir(
        tmp_path,
        provider="private-vllm",
        correct=True,
        provider_mode="private",
        publishable=True,
        location="aws-eks-us-east-1",
        node_group="system-20260815",
    )

    summary = build_report(run_dir, ROOT)

    assert summary["limitations"] == [
        "Benchmark placement was location aws-eks-us-east-1, node group system-20260815.",
        "No treatment comparison was run, so no directional claim is available.",
        "GPU telemetry was not captured for this run.",
    ]
    assert all("plumbing evidence only" not in item for item in summary["limitations"])


@pytest.mark.parametrize("stored_with_run", [False, True])
def test_paired_comparison_clears_no_comparison_limitation(
    tmp_path: Path, stored_with_run: bool
) -> None:
    run_dir = _run_dir(tmp_path, provider="private-vllm", correct=True)
    comparison = {
        "treatments": [{"run_id": "report-test"}, {"run_id": "other-run"}],
        "claimability": {"status": "inconclusive", "reason": "local placement"},
    }
    comparison_dir = run_dir if stored_with_run else tmp_path
    (comparison_dir / "comparison.json").write_text(json.dumps(comparison), encoding="utf-8")

    summary = build_report(run_dir, ROOT)

    assert summary["comparison"] == comparison
    assert summary["statistics"]["claimability"] == comparison["claimability"]
    assert all("No treatment comparison" not in item for item in summary["limitations"])


def test_report_discovers_pair_specific_comparison(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path, provider="private-vllm", correct=True)
    comparison = {
        "treatments": [{"run_id": "other-run"}, {"run_id": "report-test"}],
        "claimability": {"status": "inconclusive", "reason": "local placement"},
    }
    comparison_path = tmp_path / "comparison-other-run-vs-report-test.json"
    comparison_path.write_text(json.dumps(comparison), encoding="utf-8")

    summary = build_report(run_dir, ROOT)

    assert summary["comparison"] == comparison
    assert summary["statistics"]["claimability"] == comparison["claimability"]


def _mixed_tier_run(tmp_path: Path, *, publishable: bool, samples_per_cell: int) -> Path:
    scenario = load_scenario(ROOT / "benchmark/scenarios/hybrid-local.yaml").model_copy(
        update={
            "publishable": publishable,
            "requests": samples_per_cell * 3,
            "warmup_requests": 0,
            "repeats": 1,
            "bootstrap_iterations": 100,
        }
    )
    slo, _ = load_slo(ROOT / scenario.slo_config)
    run_dir = tmp_path / "mixed-tier"
    run_dir.mkdir(parents=True)
    manifest = {
        "run_id": "mixed-tier",
        "scenario": scenario.model_dump(mode="json", by_alias=True),
        "slo": {
            "cell": scenario.slo_cell,
            "target": slo.require_cell(scenario.slo_cell).model_dump(mode="json"),
            "cells": {
                cell: slo.require_cell(cell).model_dump(mode="json")
                for cell in scenario.slo_cells_used()
            },
        },
        "statistics": {"repeat_group_id": "mixed-tier"},
        "harness": {"location": "local-mock", "node_group": None},
    }
    (run_dir / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
    started = datetime(2026, 8, 15, tzinfo=UTC)
    records = []
    for tier in (QualityTier.ECONOMY, QualityTier.BALANCED, QualityTier.PREMIUM):
        for index in range(samples_per_cell):
            correct = not (tier is QualityTier.PREMIUM and samples_per_cell >= 30 and index < 3)
            records.append(
                BenchmarkRecord(
                    run_id="mixed-tier",
                    repeat_index=1,
                    request_id=f"{tier.value}-{index}",
                    workload="classification",
                    dataset_item_id=f"item-{tier.value}-{index}",
                    difficulty="easy",
                    data_class=scenario.data_class,
                    quality_tier=tier,
                    route="private-vllm",
                    provider="private-vllm",
                    model_alias="lab-default",
                    started_at=started,
                    completed_at=started + timedelta(seconds=2),
                    ttft_ms=10,
                    e2e_ms=20,
                    usage=TokenUsageRecord(),
                    http_status=200,
                    error_class=None,
                    fallback_count=0,
                    task_correct=correct,
                    quality_score={
                        "expected": "expected",
                        "normalized_prediction": "expected" if correct else "wrong",
                    },
                    managed_inference_cost_usd=None,
                )
            )
    (run_dir / "records.jsonl").write_text(
        "".join(record.json_line() + "\n" for record in records), encoding="utf-8"
    )
    return run_dir


def test_mixed_tier_slo_is_grouped_and_fails_when_one_cell_fails(tmp_path: Path) -> None:
    run_dir = _mixed_tier_run(tmp_path, publishable=False, samples_per_cell=30)

    summary = build_report(run_dir, ROOT)

    cells = {cell["cell"]: cell for cell in summary["slo"]["per_cell"]}
    assert set(cells) == {"WL-01/economy", "WL-01/balanced", "WL-01/premium"}
    assert cells["WL-01/economy"]["eligible"] is True
    assert cells["WL-01/balanced"]["eligible"] is True
    assert cells["WL-01/premium"]["eligible"] is False
    assert cells["WL-01/premium"]["checks"]["quality_rate"]["actual"] == 0.9
    assert summary["slo"]["aggregate"]["quality_rate"] == pytest.approx(0.9666666667)
    assert summary["slo"]["slo_eligible"] is False


@pytest.mark.parametrize("publishable, expected_eligible", [(False, True), (True, False)])
def test_insufficient_slo_cell_fails_closed_only_for_publishable_runs(
    tmp_path: Path, publishable: bool, expected_eligible: bool
) -> None:
    run_dir = _mixed_tier_run(tmp_path, publishable=publishable, samples_per_cell=29)

    summary = build_report(run_dir, ROOT)

    assert summary["slo"]["slo_eligible"] is expected_eligible
    assert all(
        cell["sample_status"] == "insufficient-sample" for cell in summary["slo"]["per_cell"]
    )
    assert all(cell["eligible"] is expected_eligible for cell in summary["slo"]["per_cell"])
    if publishable:
        assert all(cell["warning"] is None for cell in summary["slo"]["per_cell"])
    else:
        assert all(cell["warning"] for cell in summary["slo"]["per_cell"])
