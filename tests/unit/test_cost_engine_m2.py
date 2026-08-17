from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from inference_gateway.benchmark.models import BenchmarkRecord, TokenUsageRecord
from inference_gateway.costing import CostEngine, PrivateRunInputs, load_cost_config
from inference_gateway.costing.models import CostConfig
from inference_gateway.costing.scenario_grid import build_scenario_grid, required_replicas
from inference_gateway.models import UsageSource

ROOT = Path(__file__).resolve().parents[2]


def _hand_computed_config() -> CostConfig:
    return CostConfig.model_validate(
        {
            "version": "hand-computed-v1",
            "currency": "USD",
            "effective_date": "2026-08-17",
            "private": {
                "gpu_node_hourly_usd": "7",
                "cpu_node_hourly_usd": "0",
                "model_storage_hourly_usd": "0",
                "model_transfer_per_gb_usd": "0",
                "node_lifecycle_hourly_usd": "0",
                "minimum_billing_seconds": 1,
            },
            "shared": {
                "gateway_hourly_usd": "1",
                "network_nat_hourly_usd": "2",
                "control_plane_hourly_usd": "3",
                "shared_storage_hourly_usd": "4",
                "observability_hourly_usd": "5",
            },
            "operations": {
                "low": {
                    "hours_per_month": "6",
                    "hourly_rate_usd": "1",
                    "allocation_fraction": "1",
                    "basis": "hand-computed low",
                },
                "typical": {
                    "hours_per_month": "12",
                    "hourly_rate_usd": "1",
                    "allocation_fraction": "1",
                    "basis": "hand-computed typical",
                },
                "high": {
                    "hours_per_month": "18",
                    "hourly_rate_usd": "1",
                    "allocation_fraction": "1",
                    "basis": "hand-computed high",
                },
            },
            "scenario_grid": {
                "monthly_requests": [10, 20],
                "token_profiles": {
                    "one-million": {"input_tokens": 100000, "output_tokens": 0},
                    "zero-usage": {"input_tokens": 0, "output_tokens": 0},
                },
                "utilization_tiers": {
                    "low": {
                        "requests_per_replica_month": 100,
                        "replica_hours_per_month": "10",
                    },
                    "typical": {
                        "requests_per_replica_month": 100,
                        "replica_hours_per_month": "10",
                    },
                    "high": {
                        "requests_per_replica_month": 100,
                        "replica_hours_per_month": "10",
                    },
                },
                "quality_sensitivity_points": [-5, 0, 5],
            },
        }
    )


def _grid_row(config: CostConfig, *, view: str, token_profile: str = "one-million"):
    rows = build_scenario_grid(
        config,
        managed_input_per_1m=Decimal("2"),
        managed_output_per_1m=Decimal("11"),
        managed_quality_rate=Decimal("1"),
        private_quality_rate=Decimal("1"),
    )
    return next(
        row
        for row in rows
        if row["monthly_requests"] == 10
        and row["token_profile"] == token_profile
        and row["utilization_tier"] == "typical"
        and row["quality_sensitivity_path"] == "measured"
        and row["view"] == view
        and row["operations_sensitivity"] in {"low", "not_applicable"}
    )


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


def test_grid_view_b_hand_computed_components_and_token_costs() -> None:
    row = _grid_row(_hand_computed_config(), view="view_b")

    assert row["gateway_runtime_usd"] == "10"
    assert row["network_nat_usd"] == "20"
    assert row["control_plane_usd"] == "30"
    assert row["shared_storage_usd"] == "40"
    assert row["observability_usd"] == "50"
    assert row["operations_engineering_allocation_usd"] == "6"
    assert row["managed_total_usd"] == "158"
    assert row["private_total_usd"] == "226"
    assert row["managed_cost_per_1m_provider_billed_tokens_usd"] == "158"
    assert row["private_cost_per_1m_normalized_tokens_usd"] == "226"
    assert row["managed_token_definition"] == "provider-billed input + output tokens"
    assert row["private_token_definition"] == "normalized input + visible output tokens"


def test_grid_shared_price_change_affects_both_view_b_paths_only() -> None:
    config = _hand_computed_config()
    changed = config.model_copy(
        update={"shared": config.shared.model_copy(update={"gateway_hourly_usd": Decimal("1.5")})}
    )

    base_a = _grid_row(config, view="view_a")
    changed_a = _grid_row(changed, view="view_a")
    base_b = _grid_row(config, view="view_b")
    changed_b = _grid_row(changed, view="view_b")

    assert changed_a["managed_total_usd"] == base_a["managed_total_usd"]
    assert changed_a["private_total_usd"] == base_a["private_total_usd"]
    assert Decimal(changed_b["managed_total_usd"]) - Decimal(
        base_b["managed_total_usd"]
    ) == Decimal("5.0")
    assert Decimal(changed_b["private_total_usd"]) - Decimal(
        base_b["private_total_usd"]
    ) == Decimal("5.0")


def test_grid_zero_usage_omits_per_token_outputs() -> None:
    row = _grid_row(_hand_computed_config(), view="view_b", token_profile="zero-usage")

    assert row["managed_cost_per_1m_provider_billed_tokens_usd"] is None
    assert row["private_cost_per_1m_normalized_tokens_usd"] is None
    assert row["managed_token_definition"] is None
    assert row["private_token_definition"] is None


def test_grid_view_b_additions_are_symmetric_between_architectures() -> None:
    config = _hand_computed_config()
    view_a = _grid_row(config, view="view_a")
    view_b = _grid_row(config, view="view_b")

    managed_addition = Decimal(view_b["managed_total_usd"]) - Decimal(view_a["managed_total_usd"])
    private_addition = Decimal(view_b["private_total_usd"]) - Decimal(view_a["private_total_usd"])
    assert managed_addition == Decimal("156")
    assert private_addition == Decimal("156")


def test_observed_views_report_exact_per_token_cost_and_component_symmetry() -> None:
    config = _hand_computed_config()
    engine = CostEngine(config)
    views = engine.run_views(
        Decimal("2"),
        PrivateRunInputs(gpu_node_billed_hours="10", shared_platform_billed_hours="720"),
        10,
        10,
        managed_token_count=1_000_000,
        private_token_count=2_000_000,
    )

    view_a = views["view_a"]
    low = views["view_b"]["operations_sensitivity"]["low"]
    assert view_a["managed"].cost_per_1m_tokens_usd == Decimal("2")
    assert view_a["private"].cost_per_1m_tokens_usd == Decimal("35")
    assert low["platform_components_usd"] == {
        "gateway_runtime_usd": Decimal("720"),
        "network_nat_usd": Decimal("1440"),
        "control_plane_usd": Decimal("2160"),
        "shared_storage_usd": Decimal("2880"),
        "observability_usd": Decimal("3600"),
        "operations_engineering_allocation_usd": Decimal("6"),
    }
    assert low["managed"].total_usd - view_a["managed"].total_usd == Decimal("10806")
    assert low["private"].total_usd - view_a["private"].total_usd == Decimal("10806")
