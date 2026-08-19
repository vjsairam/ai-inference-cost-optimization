"""Paired offline comparison and fail-closed claimability derivation."""

from __future__ import annotations

import json
from dataclasses import asdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast

import yaml

from inference_gateway.benchmark.models import BenchmarkRecord
from inference_gateway.benchmark.statistics import paired_quality_effect_ci


def _read_run(run_dir: Path) -> tuple[dict[str, Any], list[BenchmarkRecord], dict[str, Any]]:
    required = ("manifest.yaml", "records.jsonl", "summary.json")
    missing = [name for name in required if not (run_dir / name).is_file()]
    if missing:
        raise ValueError(f"run directory {run_dir} is missing: {', '.join(missing)}")
    try:
        manifest = yaml.safe_load((run_dir / "manifest.yaml").read_text(encoding="utf-8"))
        summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
        records = [
            BenchmarkRecord.model_validate_json(line)
            for line in (run_dir / "records.jsonl").read_text(encoding="utf-8").splitlines()
        ]
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot load completed run {run_dir}: {exc}") from exc
    if not isinstance(manifest, dict) or not isinstance(summary, dict):
        raise ValueError(f"run directory {run_dir} has invalid manifest or summary data")
    if not manifest.get("completed_at"):
        raise ValueError(f"run directory {run_dir} is not completed")
    if not records:
        raise ValueError(f"run directory {run_dir} has no records")
    if summary.get("run_id") != manifest.get("run_id"):
        raise ValueError(f"run directory {run_dir} has inconsistent run IDs")
    return cast(dict[str, Any], manifest), records, cast(dict[str, Any], summary)


def _paired_quality(
    records_a: list[BenchmarkRecord],
    records_b: list[BenchmarkRecord],
    *,
    iterations: int,
    seed: int,
) -> dict[str, Any]:
    item_ids_a = {record.dataset_item_id for record in records_a}
    item_ids_b = {record.dataset_item_id for record in records_b}
    if item_ids_a != item_ids_b:
        only_a = sorted(item_ids_a - item_ids_b)
        only_b = sorted(item_ids_b - item_ids_a)
        raise ValueError(
            "dataset item sets differ between runs: "
            f"only in treatment A={only_a[:5]}, only in treatment B={only_b[:5]}"
        )

    def indexed(records: list[BenchmarkRecord], label: str) -> dict[tuple[str, int], bool]:
        pairs: dict[tuple[str, int], bool] = {}
        for record in records:
            key = (record.dataset_item_id, record.repeat_index)
            if key in pairs:
                raise ValueError(f"duplicate paired record in treatment {label}: {key}")
            if record.task_correct is None:
                raise ValueError(
                    "paired quality comparison requires objective task_correct values; "
                    f"treatment {label} has none for {key}"
                )
            pairs[key] = record.task_correct
        return pairs

    indexed_a = indexed(records_a, "A")
    indexed_b = indexed(records_b, "B")
    if set(indexed_a) != set(indexed_b):
        raise ValueError(
            "paired record sets differ after matching dataset item id and repeat index"
        )
    keys = sorted(indexed_a)
    baseline = [indexed_a[key] for key in keys]
    treatment = [indexed_b[key] for key in keys]
    interval = paired_quality_effect_ci(
        baseline,
        treatment,
        iterations=iterations,
        seed=seed,
    )
    estimate = sum(
        float(candidate) - float(reference)
        for reference, candidate in zip(baseline, treatment, strict=True)
    ) / len(keys)
    return {
        "definition": "treatment B quality rate minus treatment A quality rate",
        "estimate": estimate,
        "paired_observations": len(keys),
        "unique_dataset_items": len(item_ids_a),
        "pairing_key": ["dataset_item_id", "repeat_index"],
        "confidence_interval": asdict(interval),
    }


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def _provider_mode(manifest: dict[str, Any]) -> str | None:
    scenario = manifest.get("scenario")
    return str(scenario.get("provider_mode")) if isinstance(scenario, dict) else None


def _view_a_cost(summary: dict[str, Any], mode: str | None) -> Decimal | None:
    cost = summary.get("cost")
    if not isinstance(cost, dict):
        return None
    if mode == "hybrid":
        row = cost.get("hybrid_combined_view_a")
    else:
        views = cost.get("views")
        view_a = views.get("view_a") if isinstance(views, dict) else None
        row = (
            view_a.get(mode)
            if isinstance(view_a, dict) and mode in {"managed", "private"}
            else None
        )
    return _decimal(row.get("cost_per_correct_task_usd")) if isinstance(row, dict) else None


def _view_b_cost(summary: dict[str, Any], mode: str | None) -> Decimal | None:
    cost = summary.get("cost")
    if not isinstance(cost, dict):
        return None
    if mode == "hybrid":
        hybrid = cost.get("hybrid_combined_view_b")
        sensitivities = hybrid.get("operations_sensitivity") if isinstance(hybrid, dict) else None
        typical = sensitivities.get("typical") if isinstance(sensitivities, dict) else None
        row = typical.get("combined") if isinstance(typical, dict) else None
    else:
        views = cost.get("views")
        view_b = views.get("view_b") if isinstance(views, dict) else None
        sensitivities = view_b.get("operations_sensitivity") if isinstance(view_b, dict) else None
        typical = sensitivities.get("typical") if isinstance(sensitivities, dict) else None
        row = (
            typical.get(mode)
            if isinstance(typical, dict) and mode in {"managed", "private"}
            else None
        )
    return _decimal(row.get("cost_per_correct_task_usd")) if isinstance(row, dict) else None


def _view_a_ci(summary: dict[str, Any], mode: str | None) -> tuple[Decimal, Decimal] | None:
    name = {
        "managed": "managed_view_a_cost_per_correct_task_95_ci",
        "private": "private_view_a_cost_per_correct_task_95_ci",
        "hybrid": "hybrid_view_a_cost_per_correct_task_95_ci",
    }.get(mode or "")
    statistics = summary.get("statistics")
    row = statistics.get(name) if isinstance(statistics, dict) and name else None
    low = _decimal(row.get("low")) if isinstance(row, dict) else None
    high = _decimal(row.get("high")) if isinstance(row, dict) else None
    return (low, high) if low is not None and high is not None else None


def _cost_delta(
    summary_a: dict[str, Any],
    summary_b: dict[str, Any],
    mode_a: str | None,
    mode_b: str | None,
) -> tuple[dict[str, Any], tuple[Decimal, Decimal] | None]:
    view_a_a = _view_a_cost(summary_a, mode_a)
    view_a_b = _view_a_cost(summary_b, mode_b)
    view_b_a = _view_b_cost(summary_a, mode_a)
    view_b_b = _view_b_cost(summary_b, mode_b)
    ci_a = _view_a_ci(summary_a, mode_a)
    ci_b = _view_a_ci(summary_b, mode_b)
    delta_ci = None
    if ci_a is not None and ci_b is not None:
        delta_ci = (ci_b[0] - ci_a[1], ci_b[1] - ci_a[0])

    def row(a: Decimal | None, b: Decimal | None) -> dict[str, Decimal | None]:
        return {
            "treatment_a": a,
            "treatment_b": b,
            "delta_b_minus_a": b - a if a is not None and b is not None else None,
        }

    return (
        {
            "definition": "treatment B cost_per_correct_task minus treatment A",
            "view_a": {
                **row(view_a_a, view_a_b),
                "delta_95_ci": (
                    {"low": delta_ci[0], "high": delta_ci[1]} if delta_ci is not None else None
                ),
                "ci_method": "conservative difference of independent run bootstrap bounds",
            },
            "view_b_typical": {
                **row(view_b_a, view_b_b),
                "operations_sensitivity": "typical",
            },
        },
        delta_ci,
    )


def _latency_delta(summary_a: dict[str, Any], summary_b: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for output_name, summary_name in (("e2e_ms", "latency_ms"), ("ttft_ms", "ttft_ms")):
        values: dict[str, Any] = {}
        for percentile_name in ("p50", "p95"):
            row_a = summary_a.get(summary_name)
            row_b = summary_b.get(summary_name)
            value_a = row_a.get(percentile_name) if isinstance(row_a, dict) else None
            value_b = row_b.get(percentile_name) if isinstance(row_b, dict) else None
            values[percentile_name] = {
                "treatment_a": value_a,
                "treatment_b": value_b,
                "delta_b_minus_a": (
                    float(value_b) - float(value_a)
                    if value_a is not None and value_b is not None
                    else None
                ),
            }
        output[output_name] = values
    return output


def _placement_is_non_local(manifest: dict[str, Any]) -> bool:
    harness = manifest.get("harness")
    if not isinstance(harness, dict):
        return False
    return bool(
        harness.get("location") not in {None, "", "local-mock"}
        and harness.get("node") not in {None, "", "local"}
        and harness.get("workload_kind") not in {None, "", "local-process"}
        and harness.get("network_path")
    )


def _run_metadata(
    label: str,
    run_dir: Path,
    manifest: dict[str, Any],
    records: list[BenchmarkRecord],
) -> dict[str, Any]:
    raw_scenario = manifest.get("scenario")
    scenario = cast(dict[str, Any], raw_scenario) if isinstance(raw_scenario, dict) else {}
    declared_repeats = scenario.get("repeats")
    return {
        "label": label,
        "run_id": manifest.get("run_id"),
        "run_dir": str(run_dir),
        "treatment": scenario.get("treatment"),
        "sample_size": len(records),
        "successful_responses": sum(record.error_class is None for record in records),
        "repeat_count": len({record.repeat_index for record in records}),
        "declared_repeat_count": declared_repeats,
    }


def _claimability(
    manifest_a: dict[str, Any],
    manifest_b: dict[str, Any],
    metadata_a: dict[str, Any],
    metadata_b: dict[str, Any],
    quality: dict[str, Any],
    costs: dict[str, Any],
    delta_ci: tuple[Decimal, Decimal] | None,
) -> dict[str, Any]:
    failed: list[str] = []
    if manifest_a.get("publishable") is not True or manifest_b.get("publishable") is not True:
        failed.append("both manifests must be marked publishable")
    workload_a = manifest_a.get("workload")
    workload_b = manifest_b.get("workload")
    checksum_a = workload_a.get("dataset_sha256") if isinstance(workload_a, dict) else None
    checksum_b = workload_b.get("dataset_sha256") if isinstance(workload_b, dict) else None
    if not checksum_a or checksum_a != checksum_b:
        failed.append("dataset checksums must be present and equal")
    scenario_a = manifest_a.get("scenario")
    scenario_b = manifest_b.get("scenario")
    scenario_a = scenario_a if isinstance(scenario_a, dict) else {}
    scenario_b = scenario_b if isinstance(scenario_b, dict) else {}
    if not scenario_a.get("slo_cell") or scenario_a.get("slo_cell") != scenario_b.get("slo_cell"):
        failed.append("SLO cells must be present and equal")
    if not scenario_a.get("workload") or scenario_a.get("workload") != scenario_b.get("workload"):
        failed.append("workloads must be present and equal")
    for metadata in (metadata_a, metadata_b):
        label = metadata["label"]
        if (
            metadata["repeat_count"] < 3
            or not isinstance(metadata["declared_repeat_count"], int)
            or metadata["declared_repeat_count"] < 3
        ):
            failed.append(f"treatment {label} must have at least 3 declared and observed repeats")
        if metadata["successful_responses"] < 200:
            failed.append(f"treatment {label} must have at least 200 successful responses")
    if not _placement_is_non_local(manifest_a):
        failed.append("treatment A manifest must carry non-local placement")
    if not _placement_is_non_local(manifest_b):
        failed.append("treatment B manifest must carry non-local placement")

    view_a = costs["view_a"]
    view_b = costs["view_b_typical"]
    costs_complete = all(
        value is not None
        for value in (
            view_a["treatment_a"],
            view_a["treatment_b"],
            view_b["treatment_a"],
            view_b["treatment_b"],
            delta_ci,
        )
    )
    if not costs_complete:
        failed.append("View A and View B costs and View A bootstrap bounds must be present")

    quality_ci = quality["confidence_interval"]
    direction = None
    if delta_ci is not None:
        if delta_ci[1] < 0 and quality_ci["low"] >= 0:
            direction = "treatment_b"
        elif delta_ci[0] > 0 and quality_ci["high"] <= 0:
            direction = "treatment_a"
    if direction is None:
        failed.append("quality and View A cost intervals must support the same Pareto direction")

    status = "supported" if not failed else "inconclusive"
    return {
        "status": status,
        "direction": direction,
        "rule": (
            "Support requires every evidence gate plus a View A cost delta interval that "
            "excludes zero and a paired quality interval showing the cheaper treatment is "
            "not worse. Delta signs are treatment B minus treatment A."
        ),
        "failed_conditions": failed,
        "reason": (f"Supported direction: {direction}." if not failed else "; ".join(failed)),
    }


def _jsonable(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(_jsonable(payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def comparison_output_paths(output_dir: Path, comparison: dict[str, Any]) -> tuple[Path, Path]:
    """Return the pair-specific JSON and Markdown output paths."""
    treatments = comparison.get("treatments")
    if not isinstance(treatments, list) or len(treatments) != 2:
        raise ValueError("comparison must contain exactly two treatments")
    run_ids = [
        str(treatment.get("run_id", "")) if isinstance(treatment, dict) else ""
        for treatment in treatments
    ]
    if any(not run_id or Path(run_id).name != run_id for run_id in run_ids):
        raise ValueError("comparison run IDs must be valid file name components")
    stem = f"comparison-{run_ids[0]}-vs-{run_ids[1]}"
    return output_dir / f"{stem}.json", output_dir / f"{stem}.md"


def build_comparison(
    run_dir_a: str | Path,
    run_dir_b: str | Path,
    repository_root: str | Path,
) -> dict[str, Any]:
    """Compare two completed runs and write pair-specific JSON beside the first run."""
    root = Path(repository_root).resolve()
    path_a = Path(run_dir_a).resolve()
    path_b = Path(run_dir_b).resolve()
    manifest_a, records_a, summary_a = _read_run(path_a)
    manifest_b, records_b, summary_b = _read_run(path_b)
    scenario_a = manifest_a.get("scenario")
    scenario_b = manifest_b.get("scenario")
    scenario_a = scenario_a if isinstance(scenario_a, dict) else {}
    scenario_b = scenario_b if isinstance(scenario_b, dict) else {}
    iterations = min(
        int(scenario_a.get("bootstrap_iterations", 2000)),
        int(scenario_b.get("bootstrap_iterations", 2000)),
    )
    seed = int(scenario_a.get("seed", 20260815))
    quality = _paired_quality(
        records_a,
        records_b,
        iterations=iterations,
        seed=seed,
    )
    mode_a = _provider_mode(manifest_a)
    mode_b = _provider_mode(manifest_b)
    costs, delta_ci = _cost_delta(summary_a, summary_b, mode_a, mode_b)
    metadata_a = _run_metadata("A", path_a, manifest_a, records_a)
    metadata_b = _run_metadata("B", path_b, manifest_b, records_b)
    for metadata in (metadata_a, metadata_b):
        metadata_path = Path(metadata["run_dir"])
        if metadata_path == root or root in metadata_path.parents:
            metadata["run_dir"] = str(metadata_path.relative_to(root))
    comparison = {
        "schema_version": "benchmark-comparison-v1",
        "treatments": [metadata_a, metadata_b],
        "quality_effect": quality,
        "cost_per_correct_task_delta": costs,
        "latency_delta": _latency_delta(summary_a, summary_b),
    }
    comparison["claimability"] = _claimability(
        manifest_a,
        manifest_b,
        metadata_a,
        metadata_b,
        quality,
        costs,
        delta_ci,
    )
    json_path, _ = comparison_output_paths(path_a.parent, comparison)
    _write_json(json_path, comparison)
    return comparison


def write_comparison_markdown(path: Path, comparison: dict[str, Any]) -> None:
    """Write a compact operator-facing comparison table."""
    treatments = comparison["treatments"]
    quality = comparison["quality_effect"]
    costs = comparison["cost_per_correct_task_delta"]
    latency = comparison["latency_delta"]
    claimability = comparison["claimability"]
    rows = [
        ("Treatment", treatments[0]["treatment"], treatments[1]["treatment"], ""),
        ("Samples", treatments[0]["sample_size"], treatments[1]["sample_size"], ""),
        (
            "Successful responses",
            treatments[0]["successful_responses"],
            treatments[1]["successful_responses"],
            "",
        ),
        ("Repeats", treatments[0]["repeat_count"], treatments[1]["repeat_count"], ""),
        (
            "Quality rate effect",
            "",
            "",
            f"{quality['estimate']:.6g} (B minus A)",
        ),
        (
            "View A cost per correct task",
            costs["view_a"]["treatment_a"],
            costs["view_a"]["treatment_b"],
            costs["view_a"]["delta_b_minus_a"],
        ),
        (
            "View B typical cost per correct task",
            costs["view_b_typical"]["treatment_a"],
            costs["view_b_typical"]["treatment_b"],
            costs["view_b_typical"]["delta_b_minus_a"],
        ),
        (
            "p95 E2E ms",
            latency["e2e_ms"]["p95"]["treatment_a"],
            latency["e2e_ms"]["p95"]["treatment_b"],
            latency["e2e_ms"]["p95"]["delta_b_minus_a"],
        ),
        (
            "p95 TTFT ms",
            latency["ttft_ms"]["p95"]["treatment_a"],
            latency["ttft_ms"]["p95"]["treatment_b"],
            latency["ttft_ms"]["p95"]["delta_b_minus_a"],
        ),
    ]
    lines = [
        "# Treatment comparison",
        "",
        "| Metric | Treatment A | Treatment B | Delta or effect |",
        "|:--|--:|--:|--:|",
    ]
    lines.extend(f"| {name} | {a} | {b} | {delta} |" for name, a, b, delta in rows)
    lines.extend(
        [
            "",
            f"Claimability: **{claimability['status']}**",
            "",
            str(claimability["reason"]),
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
