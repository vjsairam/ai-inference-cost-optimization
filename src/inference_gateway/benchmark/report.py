"""Aggregate records into audit-ready local reports."""

from __future__ import annotations

import csv
import json
import os
import statistics
from collections import Counter
from dataclasses import asdict, is_dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast

import yaml

from inference_gateway.benchmark.evaluators import classification_metrics
from inference_gateway.benchmark.models import BenchmarkRecord, BenchmarkScenario
from inference_gateway.benchmark.statistics import (
    clustered_metric_ci,
    percentile,
    repeat_ratio_ci,
)
from inference_gateway.costing import CostEngine, PrivateRunInputs, load_cost_config
from inference_gateway.costing.scenario_grid import GridRow, build_scenario_grid


def _jsonable(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if is_dataclass(value):
        return _jsonable(asdict(cast(Any, value)))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(_jsonable(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _latency(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p50": None, "p95": None, "p99": None}
    return {
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
    }


def _span_seconds(records: list[BenchmarkRecord]) -> Decimal:
    if not records:
        return Decimal(0)
    start = min(record.started_at for record in records)
    end = max(record.completed_at for record in records)
    return Decimal(str((end - start).total_seconds()))


def _quality_payload(records: list[BenchmarkRecord], scenario: BenchmarkScenario) -> dict[str, Any]:
    objective = scenario.workload != "generation"
    if not objective:
        return {
            "applicable": False,
            "task_correct": None,
            "quality_rate": None,
            "by_difficulty": {},
            "task_metrics": {},
        }
    correct = sum(record.task_correct is True for record in records)
    by_difficulty: dict[str, dict[str, float | int]] = {}
    for difficulty in ("easy", "medium", "hard"):
        group = [record for record in records if record.difficulty == difficulty]
        group_correct = sum(record.task_correct is True for record in group)
        by_difficulty[difficulty] = {
            "attempts": len(group),
            "correct": group_correct,
            "rate": group_correct / len(group) if group else 0.0,
        }
    task_metrics: dict[str, Any]
    if scenario.workload == "classification":
        expected = [
            str(record.quality_score.get("expected", "")) if record.quality_score else ""
            for record in records
        ]
        predicted = [
            str(record.quality_score.get("normalized_prediction", ""))
            if record.quality_score
            else ""
            for record in records
        ]
        task_metrics = classification_metrics(expected, predicted)
    else:
        scores = [record.quality_score or {} for record in records]
        task_metrics = {
            "json_valid_rate": sum(score.get("json_valid") is True for score in scores)
            / len(scores),
            "required_field_exact_match_rate": sum(
                score.get("required_field_exact_match") is True for score in scores
            )
            / len(scores),
            "field_f1_mean": statistics.fmean(
                float(score.get("field_f1", 0.0)) for score in scores
            ),
            "whole_record_correct_rate": sum(
                score.get("whole_record_correct") is True for score in scores
            )
            / len(scores),
        }
    return {
        "applicable": True,
        "task_correct": correct,
        "quality_rate": correct / len(records) if records else 0.0,
        "by_difficulty": by_difficulty,
        "task_metrics": task_metrics,
    }


def _slo(
    records: list[BenchmarkRecord],
    scenario: BenchmarkScenario,
    target: dict[str, Any],
    quality: dict[str, Any],
) -> dict[str, Any]:
    e2e_p95 = _latency([record.e2e_ms for record in records])["p95"]
    ttft_p95 = _latency([record.ttft_ms for record in records if record.ttft_ms is not None])["p95"]
    error_rate = sum(record.error_class is not None for record in records) / len(records)
    checks: dict[str, dict[str, Any]] = {
        "p95_e2e_ms": {
            "actual": e2e_p95,
            "target": target["p95_e2e_ms"],
            "applicable": True,
            "pass": e2e_p95 is not None and e2e_p95 <= target["p95_e2e_ms"],
        },
        "p95_ttft_ms": {
            "actual": ttft_p95,
            "target": target["p95_ttft_ms"],
            "applicable": scenario.stream,
            "pass": (not scenario.stream)
            or (ttft_p95 is not None and ttft_p95 <= target["p95_ttft_ms"]),
        },
        "error_rate": {
            "actual": error_rate,
            "target": float(target["max_error_rate"]),
            "applicable": True,
            "pass": error_rate <= float(target["max_error_rate"]),
        },
    }
    if scenario.workload != "generation":
        quality_rate = float(quality["quality_rate"])
        checks["quality_rate"] = {
            "actual": quality_rate,
            "target": float(target["min_quality_rate"]),
            "applicable": True,
            "pass": quality_rate >= float(target["min_quality_rate"]),
        }
    return {
        "cell": scenario.slo_cell,
        "targets": target,
        "checks": checks,
        "failed_targets": [name for name, check in checks.items() if not check["pass"]],
        "slo_eligible": all(check["pass"] for check in checks.values()),
    }


def _repeat_summary(records: list[BenchmarkRecord]) -> list[dict[str, Any]]:
    rows = []
    for repeat_index in sorted({record.repeat_index for record in records}):
        group = [record for record in records if record.repeat_index == repeat_index]
        correct = sum(record.task_correct is True for record in group)
        span = float(_span_seconds(group))
        rows.append(
            {
                "repeat_index": repeat_index,
                "requests": len(group),
                "errors": sum(record.error_class is not None for record in group),
                "quality_rate": correct / len(group) if group[0].task_correct is not None else None,
                "e2e_p95_ms": _latency([record.e2e_ms for record in group])["p95"],
                "ttft_p95_ms": _latency(
                    [record.ttft_ms for record in group if record.ttft_ms is not None]
                )["p95"],
                "throughput_requests_per_second": len(group) / span if span else None,
            }
        )
    return rows


def _dispersion(rows: list[dict[str, Any]], field: str) -> dict[str, float] | None:
    values = [float(row[field]) for row in rows if row[field] is not None]
    if not values:
        return None
    return {"min": min(values), "median": statistics.median(values), "max": max(values)}


def _pricing(repository_root: Path, scenario: BenchmarkScenario) -> tuple[Decimal, Decimal]:
    raw = yaml.safe_load((repository_root / scenario.pricing_config).read_text(encoding="utf-8"))
    alias = "lab-economy" if scenario.quality_tier.value == "economy" else "lab-premium"
    price = raw["pricing"]["managed-primary"][alias]
    return Decimal(str(price["input_per_1m"])), Decimal(str(price["output_per_1m"]))


def _write_grid(path: Path, rows: list[GridRow]) -> None:
    if not rows:
        raise ValueError("scenario grid is empty")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _provider_breakdown(
    records: list[BenchmarkRecord], private_total: Decimal
) -> dict[str, dict[str, object]]:
    providers = sorted({record.provider or "unknown" for record in records})
    rows: dict[str, dict[str, object]] = {}
    for provider in providers:
        group = [record for record in records if (record.provider or "unknown") == provider]
        correct = sum(record.task_correct is True for record in group)
        if provider.startswith("managed"):
            total = sum(
                (record.managed_inference_cost_usd or Decimal(0) for record in group),
                Decimal(0),
            )
            cost_basis = "provider-reported usage and date-stamped local mock pricing"
        elif provider.startswith("private"):
            total = private_total
            cost_basis = "local billed private service time allocated to the private route"
        else:
            total = Decimal(0)
            cost_basis = "no priced route"
        rows[provider] = {
            "requests": len(group),
            "correct_tasks": correct,
            "quality_rate": correct / len(group) if group[0].task_correct is not None else None,
            "view_a_cost_usd": total,
            "view_a_cost_per_request_usd": total / len(group) if group else None,
            "view_a_cost_per_correct_task_usd": total / correct if correct else None,
            "cost_basis": cost_basis,
        }
    return rows


def _private_billed_hours(span_estimate: Decimal) -> tuple[Decimal, str]:
    configured = os.environ.get("BENCHMARK_PRIVATE_BILLED_HOURS")
    if configured is None:
        return span_estimate, "request-span-estimate"
    try:
        measured = Decimal(configured)
    except InvalidOperation as exc:
        raise ValueError(
            "BENCHMARK_PRIVATE_BILLED_HOURS must be a decimal greater than zero"
        ) from exc
    if not measured.is_finite() or measured <= 0:
        raise ValueError("BENCHMARK_PRIVATE_BILLED_HOURS must be a decimal greater than zero")
    return measured, "operator-measured"


def _provider_mode_mismatches(records: list[BenchmarkRecord], scenario: BenchmarkScenario) -> int:
    if scenario.provider_mode in {"hybrid", "local-mock"}:
        return 0
    expected_prefix = scenario.provider_mode
    return sum(not (record.provider or "").startswith(expected_prefix) for record in records)


def build_report(run_dir: Path, repository_root: Path) -> dict[str, Any]:
    manifest = yaml.safe_load((run_dir / "manifest.yaml").read_text(encoding="utf-8"))
    scenario = BenchmarkScenario.model_validate(manifest["scenario"])
    records = [
        BenchmarkRecord.model_validate_json(line)
        for line in (run_dir / "records.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    if not records:
        raise ValueError("cannot report an empty run")
    quality = _quality_payload(records, scenario)
    repeat_rows = _repeat_summary(records)
    errors = Counter(
        record.error_class.value for record in records if record.error_class is not None
    )
    span = float(_span_seconds(records))
    output_tokens = sum(record.usage.visible_output_tokens or 0 for record in records)
    iterations = scenario.bootstrap_iterations
    seed = scenario.seed
    e2e_clusters = {
        index: [record.e2e_ms for record in records if record.repeat_index == index]
        for index in range(1, scenario.repeats + 1)
    }
    statistics_payload: dict[str, Any] = {
        "method": "cluster percentile bootstrap",
        "iterations": iterations,
        "seed": seed,
        "cluster_unit": "repeat_index",
        "quality_pairing_unit": "dataset_item_id",
        "p95_e2e_ms_95_ci": asdict(
            clustered_metric_ci(
                e2e_clusters,
                lambda values: percentile(values, 0.95),
                iterations=iterations,
                seed=seed,
            )
        ),
        "claimability": {
            "status": "inconclusive",
            "reason": "single local treatment; no paired comparison was run",
        },
    }
    ttft_clusters = {
        index: [
            record.ttft_ms
            for record in records
            if record.repeat_index == index and record.ttft_ms is not None
        ]
        for index in range(1, scenario.repeats + 1)
    }
    if scenario.stream and all(ttft_clusters.values()):
        statistics_payload["p95_ttft_ms_95_ci"] = asdict(
            clustered_metric_ci(
                ttft_clusters,
                lambda values: percentile(values, 0.95),
                iterations=iterations,
                seed=seed + 1,
            )
        )
    cost_config, cost_hash = load_cost_config(repository_root / scenario.cost_config)
    engine = CostEngine(cost_config)
    repeat_seconds = {
        index: _span_seconds([record for record in records if record.repeat_index == index])
        for index in range(1, scenario.repeats + 1)
    }
    minimum = Decimal(cost_config.private.minimum_billing_seconds)
    repeat_hours = {
        index: max(seconds, minimum) / Decimal(3600) for index, seconds in repeat_seconds.items()
    }
    span_estimate_hours = sum(repeat_hours.values(), Decimal(0))
    total_hours, billed_hours_source = _private_billed_hours(span_estimate_hours)
    private_inputs = PrivateRunInputs(
        gpu_node_billed_hours=total_hours,
        cpu_node_billed_hours=total_hours,
        model_storage_billed_hours=total_hours,
        shared_platform_billed_hours=total_hours,
    )
    managed = engine.aggregate_managed(records)
    correct_count = int(quality["task_correct"]) if quality["applicable"] else None
    views = engine.run_views(managed.total_usd, private_inputs, len(records), correct_count)
    private_total = engine.private_view_a(private_inputs)
    provider_breakdown = _provider_breakdown(records, private_total)
    hybrid_total = private_total + managed.total_usd
    hybrid_view_a = engine.view(hybrid_total, len(records), correct_count)
    private_applicable = any((record.provider or "").startswith("private") for record in records)
    managed_applicable = any((record.provider or "").startswith("managed") for record in records)
    private_numerators = {
        index: float(
            hours
            * (
                cost_config.private.gpu_node_hourly_usd
                + cost_config.private.cpu_node_hourly_usd
                + cost_config.private.model_storage_hourly_usd
            )
        )
        for index, hours in repeat_hours.items()
    }
    managed_numerators = {
        index: float(
            sum(
                (
                    record.managed_inference_cost_usd or Decimal(0)
                    for record in records
                    if record.repeat_index == index
                    and (record.provider or "").startswith("managed")
                ),
                Decimal(0),
            )
        )
        for index in repeat_hours
    }
    correct_by_repeat = {
        index: sum(
            record.task_correct is True for record in records if record.repeat_index == index
        )
        for index in repeat_hours
    }
    for row in repeat_rows:
        index = int(row["repeat_index"])
        denominator = correct_by_repeat[index]
        row["view_a_private_cost_per_correct_task_usd"] = (
            private_numerators[index] / denominator if denominator else None
        )
        row["view_a_managed_cost_per_correct_task_usd"] = (
            managed_numerators[index] / denominator if denominator and managed_applicable else None
        )
    if correct_count:
        statistics_payload["private_view_a_cost_per_correct_task_95_ci"] = asdict(
            repeat_ratio_ci(
                private_numerators,
                correct_by_repeat,
                iterations=iterations,
                seed=seed + 2,
            )
        )
        if managed_applicable:
            statistics_payload["managed_view_a_cost_per_correct_task_95_ci"] = asdict(
                repeat_ratio_ci(
                    managed_numerators,
                    correct_by_repeat,
                    iterations=iterations,
                    seed=seed + 3,
                )
            )
    input_price, output_price = _pricing(repository_root, scenario)
    quality_not_measured = quality["quality_rate"] is None
    measured_quality = Decimal(1) if quality_not_measured else Decimal(str(quality["quality_rate"]))
    grid = build_scenario_grid(
        cost_config,
        managed_input_per_1m=input_price,
        managed_output_per_1m=output_price,
        managed_quality_rate=measured_quality,
        private_quality_rate=measured_quality,
    )
    cost_payload = {
        "currency": "USD",
        "config_version": cost_config.version,
        "config_sha256": cost_hash,
        "managed_run_cost": asdict(managed),
        "observed_path_applicability": {
            "managed": managed_applicable,
            "private": private_applicable,
        },
        "private_billed_inputs": private_inputs.model_dump(mode="json"),
        "billed_hours_source": billed_hours_source,
        "request_span_estimate_billed_hours": span_estimate_hours,
        "views": views,
        "hybrid_combined_view_a": hybrid_view_a,
        "provider_breakdown": provider_breakdown,
        "scenario_grid": {
            "label": (
                "Break-even sensitivity table centered on the observed run quality rate for both "
                "architectures; the quality axis varies that shared center and is not an "
                "independent counterfactual measurement"
            ),
            "quality_basis": (
                "quality_not_measured" if quality_not_measured else "observed_run_quality_rate"
            ),
            "rows": grid,
        },
    }
    slo = _slo(records, scenario, manifest["slo"]["target"], quality)
    provider_mode_mismatches = _provider_mode_mismatches(records, scenario)
    summary = {
        "run_id": manifest["run_id"],
        "treatment": scenario.treatment,
        "workload": scenario.workload,
        "requests": len(records),
        "completions": sum(record.error_class is None for record in records),
        "errors": {"total": sum(errors.values()), "by_class": dict(errors)},
        "latency_ms": _latency([record.e2e_ms for record in records]),
        "ttft_ms": _latency([record.ttft_ms for record in records if record.ttft_ms is not None]),
        "throughput": {
            "requests_per_second": len(records) / span if span else None,
            "output_tokens_per_second": output_tokens / span if span else None,
        },
        "quality": {
            "task_correct": quality["task_correct"],
            "quality_rate": quality["quality_rate"],
        },
        "cost": {
            "observed_path_applicability": {
                "managed": managed_applicable,
                "private": private_applicable,
            },
            "views": views,
            "hybrid_combined_view_a": hybrid_view_a,
            "provider_breakdown": provider_breakdown,
        },
        "slo": slo,
        "routing_mix": dict(Counter(record.provider or "unknown" for record in records)),
        "provider_mode_mismatches": provider_mode_mismatches,
        "policy_input_mix": {
            "data_class": dict(
                Counter(
                    record.data_class.value if record.data_class is not None else "unknown"
                    for record in records
                )
            ),
            "quality_tier": dict(
                Counter(
                    record.quality_tier.value if record.quality_tier is not None else "unknown"
                    for record in records
                )
            ),
        },
        "fallback_count": sum(record.fallback_count for record in records),
        "repeats": {
            "group_id": manifest["statistics"]["repeat_group_id"],
            "count": scenario.repeats,
            "values": repeat_rows,
            "e2e_p95_ms_dispersion": _dispersion(repeat_rows, "e2e_p95_ms"),
            "ttft_p95_ms_dispersion": _dispersion(repeat_rows, "ttft_p95_ms"),
            "quality_rate_dispersion": _dispersion(repeat_rows, "quality_rate"),
            "throughput_requests_per_second_dispersion": _dispersion(
                repeat_rows, "throughput_requests_per_second"
            ),
            "view_a_private_cost_per_correct_task_usd_dispersion": _dispersion(
                repeat_rows, "view_a_private_cost_per_correct_task_usd"
            ),
            "view_a_managed_cost_per_correct_task_usd_dispersion": _dispersion(
                repeat_rows, "view_a_managed_cost_per_correct_task_usd"
            ),
        },
        "statistics": statistics_payload,
        "gpu": {
            "utilization_average": None,
            "utilization_p95": None,
            "memory_average": None,
            "memory_p95": None,
        },
        "harness": manifest["harness"],
        "sample_size": len(records),
        "limitations": [
            "Local deterministic adapter run; timings and costs are plumbing evidence only.",
            "No treatment comparison was run, so no directional claim is available.",
        ],
    }
    _write_json(run_dir / "summary.json", summary)
    _write_json(run_dir / "quality.json", quality)
    _write_json(run_dir / "cost.json", cost_payload)
    _write_json(run_dir / "scenario-grid.json", grid)
    _write_grid(run_dir / "comparison.csv", grid)
    if scenario.publishable and provider_mode_mismatches:
        raise ValueError(
            f"publishable report has {provider_mode_mismatches} provider_mode mismatches"
        )
    return summary
