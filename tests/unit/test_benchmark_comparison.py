from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from inference_gateway.benchmark.comparison import build_comparison, comparison_output_paths
from inference_gateway.benchmark.models import BenchmarkRecord, TokenUsageRecord
from inference_gateway.models import ErrorClass


def _records(run_id: str, *, correct: bool, provider: str) -> list[BenchmarkRecord]:
    started = datetime(2026, 8, 15, tzinfo=UTC)
    return [
        BenchmarkRecord(
            run_id=run_id,
            repeat_index=repeat,
            request_id=f"{run_id}-{repeat}-{item}",
            workload="classification",
            dataset_item_id=f"item-{item:03d}",
            difficulty="easy",
            data_class="public",
            quality_tier="premium",
            route=provider,
            provider=provider,
            model_alias="lab-default",
            started_at=started,
            completed_at=started + timedelta(seconds=1),
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
            managed_inference_cost_usd="0.01" if provider.startswith("managed") else None,
        )
        for repeat in range(1, 4)
        for item in range(67)
    ]


def _summary(run_id: str, *, mode: str, cost: str) -> dict[str, object]:
    ci = (
        {"low": "1.8", "high": "2.2"}
        if mode == "managed"
        else {
            "low": "0.8",
            "high": "1.2",
        }
    )
    return {
        "run_id": run_id,
        "latency_ms": {"p50": 20, "p95": 30},
        "ttft_ms": {"p50": 10, "p95": 15},
        "cost": {
            "views": {
                "view_a": {
                    mode: {"cost_per_correct_task_usd": cost},
                },
                "view_b": {
                    "operations_sensitivity": {
                        "typical": {mode: {"cost_per_correct_task_usd": str(int(cost) + 1)}}
                    }
                },
            }
        },
        "statistics": {f"{mode}_view_a_cost_per_correct_task_95_ci": ci},
    }


def _write_run(
    parent: Path,
    name: str,
    *,
    mode: str,
    correct: bool,
    cost: str,
) -> Path:
    run_dir = parent / name
    run_dir.mkdir(parents=True)
    manifest = {
        "run_id": name,
        "completed_at": "2026-08-15T01:00:00Z",
        "publishable": True,
        "scenario": {
            "treatment": f"{name}-treatment",
            "provider_mode": mode,
            "workload": "classification",
            "slo_cell": "WL-01/premium",
            "repeats": 3,
            "bootstrap_iterations": 100,
            "seed": 7,
        },
        "workload": {"dataset_sha256": "a" * 64},
        "harness": {
            "location": "aws-eks",
            "node": "ip-10-0-0-1",
            "workload_kind": "kubernetes-job",
            "network_path": "runner Pod -> gateway ClusterIP -> provider",
        },
    }
    (run_dir / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
    records = _records(name, correct=correct, provider=f"{mode}-provider")
    (run_dir / "records.jsonl").write_text(
        "".join(record.json_line() + "\n" for record in records), encoding="utf-8"
    )
    (run_dir / "summary.json").write_text(
        json.dumps(_summary(name, mode=mode, cost=cost)), encoding="utf-8"
    )
    return run_dir


def _pair(tmp_path: Path) -> tuple[Path, Path]:
    return (
        _write_run(tmp_path, "run-a", mode="managed", correct=False, cost="2"),
        _write_run(tmp_path, "run-b", mode="private", correct=True, cost="1"),
    )


def _manifest(path: Path) -> dict[str, object]:
    return yaml.safe_load((path / "manifest.yaml").read_text(encoding="utf-8"))


def _write_manifest(path: Path, manifest: dict[str, object]) -> None:
    (path / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")


def test_comparison_pairs_items_and_supports_pareto_direction(tmp_path: Path) -> None:
    run_a, run_b = _pair(tmp_path)

    comparison = build_comparison(run_a, run_b, tmp_path)

    assert comparison["quality_effect"]["paired_observations"] == 201
    assert comparison["quality_effect"]["unique_dataset_items"] == 67
    assert comparison["quality_effect"]["confidence_interval"]["low"] == 1.0
    assert comparison["cost_per_correct_task_delta"]["view_a"]["delta_b_minus_a"] == -1
    assert comparison["claimability"]["status"] == "supported"
    assert comparison["claimability"]["direction"] == "treatment_b"
    json_path, markdown_path = comparison_output_paths(tmp_path, comparison)
    assert json_path == tmp_path / "comparison-run-a-vs-run-b.json"
    assert markdown_path == tmp_path / "comparison-run-a-vs-run-b.md"
    assert json_path.is_file()
    assert not (tmp_path / "comparison.json").exists()


def test_comparison_refuses_different_dataset_item_sets(tmp_path: Path) -> None:
    run_a, run_b = _pair(tmp_path)
    records = (run_b / "records.jsonl").read_text(encoding="utf-8").splitlines()
    filtered = [line for line in records if '"dataset_item_id":"item-066"' not in line]
    (run_b / "records.jsonl").write_text("\n".join(filtered) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="dataset item sets differ"):
        build_comparison(run_a, run_b, tmp_path)


@pytest.mark.parametrize(
    "failure, expected_reason",
    [
        ("publishable", "both manifests must be marked publishable"),
        ("checksum", "dataset checksums must be present and equal"),
        ("slo", "SLO cells must be present and equal"),
        ("workload", "workloads must be present and equal"),
        ("repeats_a", "treatment A must have at least 3"),
        ("repeats_b", "treatment B must have at least 3"),
        ("samples_a", "treatment A must have at least 200 successful responses"),
        ("samples_b", "treatment B must have at least 200 successful responses"),
        ("placement_a", "treatment A manifest must carry non-local placement"),
        ("placement_b", "treatment B manifest must carry non-local placement"),
        ("cost", "View A and View B costs and View A bootstrap bounds must be present"),
        ("direction", "quality and View A cost intervals must support the same Pareto direction"),
    ],
)
def test_claimability_fails_closed_for_each_condition(
    tmp_path: Path, failure: str, expected_reason: str
) -> None:
    run_a, run_b = _pair(tmp_path)
    manifest_a = _manifest(run_a)
    manifest_b = _manifest(run_b)
    if failure == "publishable":
        manifest_b["publishable"] = False
    elif failure == "checksum":
        manifest_b["workload"]["dataset_sha256"] = "b" * 64  # type: ignore[index]
    elif failure == "slo":
        manifest_b["scenario"]["slo_cell"] = "WL-01/balanced"  # type: ignore[index]
    elif failure == "workload":
        manifest_b["scenario"]["workload"] = "structured-extraction"  # type: ignore[index]
    elif failure == "repeats_a":
        manifest_a["scenario"]["repeats"] = 2  # type: ignore[index]
    elif failure == "repeats_b":
        manifest_b["scenario"]["repeats"] = 2  # type: ignore[index]
    elif failure in {"placement_a", "placement_b"}:
        selected = manifest_a if failure.endswith("a") else manifest_b
        selected["harness"] = {
            "location": "local-mock",
            "node": "local",
            "workload_kind": "local-process",
            "network_path": "in-process",
        }
    _write_manifest(run_a, manifest_a)
    _write_manifest(run_b, manifest_b)

    if failure in {"samples_a", "samples_b"}:
        selected_run = run_a if failure.endswith("a") else run_b
        records = [
            BenchmarkRecord.model_validate_json(line)
            for line in (selected_run / "records.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        for index in range(2):
            records[index] = records[index].model_copy(
                update={"error_class": ErrorClass.TIMEOUT, "http_status": 504}
            )
        (selected_run / "records.jsonl").write_text(
            "".join(record.json_line() + "\n" for record in records), encoding="utf-8"
        )
    elif failure in {"cost", "direction"}:
        summary = json.loads((run_b / "summary.json").read_text(encoding="utf-8"))
        if failure == "cost":
            summary["statistics"].pop("private_view_a_cost_per_correct_task_95_ci")
        else:
            summary["statistics"]["private_view_a_cost_per_correct_task_95_ci"] = {
                "low": "0.8",
                "high": "2.5",
            }
        (run_b / "summary.json").write_text(json.dumps(summary), encoding="utf-8")

    comparison = build_comparison(run_a, run_b, tmp_path)

    assert comparison["claimability"]["status"] == "inconclusive"
    assert expected_reason in comparison["claimability"]["reason"]
