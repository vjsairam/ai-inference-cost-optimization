from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from inference_gateway.benchmark.models import BenchmarkRecord, TokenUsageRecord
from inference_gateway.costing import CostEngine, PrivateRunInputs, load_cost_config
from inference_gateway.costing.scenario_grid import build_scenario_grid, required_replicas
from inference_gateway.models import UsageSource

ROOT = Path(__file__).resolve().parents[2]


def _record(cost: Decimal | None) -> BenchmarkRecord:
    now = datetime(2026, 8, 15, tzinfo=UTC)
    return BenchmarkRecord(
        run_id="run",
        repeat_index=1,
        request_id="request",
        workload="classification",
        dataset_item_id="item",
        difficulty="easy",
        route="route",
        provider="managed-economy",
        model_alias="lab-economy",
        started_at=now,
        completed_at=now,
        e2e_ms=1,
        usage=TokenUsageRecord(
            input_tokens=10,
            input_tokens_source=UsageSource.PROVIDER_REPORTED,
            visible_output_tokens=2,
            visible_output_tokens_source=UsageSource.PROVIDER_REPORTED,
            billed_input_tokens=10,
            billed_input_tokens_source=UsageSource.PROVIDER_REPORTED,
            billed_output_tokens=2,
            billed_output_tokens_source=UsageSource.PROVIDER_REPORTED,
        ),
        http_status=200,
        error_class=None,
        fallback_count=0,
        task_correct=True,
        quality_score={},
        managed_inference_cost_usd=cost,
    )


def test_managed_aggregation_tracks_missing_usage_without_inventing_cost() -> None:
    aggregate = CostEngine.aggregate_managed([_record(Decimal("0.01")), _record(None)])
    assert aggregate.total_usd == Decimal("0.01")
    assert aggregate.priced_requests == 1
    assert aggregate.missing_usage_requests == 1
    assert aggregate.estimated


def test_private_view_a_and_zero_correct_task_math() -> None:
    config, _ = load_cost_config(ROOT / "config/cost.local.yaml")
    engine = CostEngine(config)
    inputs = PrivateRunInputs(
        gpu_node_billed_hours="2",
        cpu_node_billed_hours="3",
        model_storage_billed_hours="4",
        model_transfer_gb="5",
        node_lifecycle_billed_hours="6",
        shared_platform_billed_hours="1",
    )
    assert engine.private_view_a(inputs) == Decimal("2.34")
    zero = engine.view(Decimal("2.34"), request_count=10, correct_count=0)
    assert zero.cost_per_request_usd == Decimal("0.234")
    assert zero.cost_per_correct_task_usd is None


def test_view_b_requires_and_orders_operations_sensitivity() -> None:
    config, _ = load_cost_config(ROOT / "config/cost.local.yaml")
    views = CostEngine(config).run_views(
        Decimal("1"),
        PrivateRunInputs(gpu_node_billed_hours="1", shared_platform_billed_hours="1"),
        10,
        5,
    )
    sensitivities = views["view_b"]["operations_sensitivity"]
    assert set(sensitivities) == {"low", "typical", "high"}
    totals = [sensitivities[name]["private"].total_usd for name in ("low", "typical", "high")]
    assert totals[0] < totals[1] < totals[2]


def test_stepwise_replica_capacity_and_grid_views() -> None:
    assert required_replicas(100_000, 100_000) == 1
    assert required_replicas(100_001, 100_000) == 2
    config, _ = load_cost_config(ROOT / "config/cost.local.yaml")
    rows = build_scenario_grid(
        config,
        managed_input_per_1m=Decimal("1"),
        managed_output_per_1m=Decimal("5"),
        managed_quality_rate=Decimal("0.9"),
        private_quality_rate=Decimal("0.8"),
    )
    assert {row["view"] for row in rows} == {"view_a", "view_b"}
    assert {row["operations_sensitivity"] for row in rows if row["view"] == "view_b"} == {
        "low",
        "typical",
        "high",
    }
    assert all(isinstance(row["managed_total_usd"], str) for row in rows)
