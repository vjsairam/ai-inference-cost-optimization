"""Stepwise capacity and break-even region tables."""

from __future__ import annotations

import math
from decimal import Decimal
from typing import TypedDict

from inference_gateway.costing.models import CostConfig

_MILLION = Decimal(1_000_000)


class GridRow(TypedDict):
    view: str
    operations_sensitivity: str
    monthly_requests: int
    token_profile: str
    utilization_tier: str
    quality_sensitivity_path: str
    quality_delta_points: int
    managed_quality_rate: str
    private_quality_rate: str
    replicas: int
    managed_total_usd: str
    private_total_usd: str
    gateway_runtime_usd: str | None
    network_nat_usd: str | None
    control_plane_usd: str | None
    shared_storage_usd: str | None
    observability_usd: str | None
    operations_engineering_allocation_usd: str | None
    managed_cost_per_1m_provider_billed_tokens_usd: str | None
    managed_token_definition: str | None
    private_cost_per_1m_normalized_tokens_usd: str | None
    private_token_definition: str | None
    managed_cost_per_correct_task_usd: str | None
    private_cost_per_correct_task_usd: str | None
    lower_cost_path: str


def required_replicas(monthly_requests: int, requests_per_replica_month: int) -> int:
    if monthly_requests < 1 or requests_per_replica_month < 1:
        raise ValueError("capacity inputs must be positive")
    return math.ceil(monthly_requests / requests_per_replica_month)


def build_scenario_grid(
    config: CostConfig,
    *,
    managed_input_per_1m: Decimal,
    managed_output_per_1m: Decimal,
    managed_quality_rate: Decimal,
    private_quality_rate: Decimal,
) -> list[GridRow]:
    rows: list[GridRow] = []
    quality_cases: list[tuple[str, int, Decimal, Decimal]] = []
    for quality_delta in config.scenario_grid.quality_sensitivity_points:
        delta = Decimal(quality_delta) / Decimal(100)
        if quality_delta == 0:
            quality_cases.append(
                ("measured", quality_delta, managed_quality_rate, private_quality_rate)
            )
            continue
        quality_cases.extend(
            (
                (
                    "managed",
                    quality_delta,
                    min(Decimal(1), max(Decimal(0), managed_quality_rate + delta)),
                    private_quality_rate,
                ),
                (
                    "private",
                    quality_delta,
                    managed_quality_rate,
                    min(Decimal(1), max(Decimal(0), private_quality_rate + delta)),
                ),
            )
        )
    for volume in config.scenario_grid.monthly_requests:
        for profile_name, profile in config.scenario_grid.token_profiles.items():
            managed_inference = (
                Decimal(volume)
                * (
                    Decimal(profile.input_tokens) * managed_input_per_1m
                    + Decimal(profile.output_tokens) * managed_output_per_1m
                )
                / _MILLION
            )
            tokens_per_request = profile.input_tokens + profile.output_tokens
            monthly_tokens = volume * tokens_per_request
            for tier_name, tier in config.scenario_grid.utilization_tiers.items():
                replicas = required_replicas(volume, tier.requests_per_replica_month)
                replica_hours = Decimal(replicas) * tier.replica_hours_per_month
                private_inference = replica_hours * config.private.gpu_node_hourly_usd
                for (
                    sensitivity_path,
                    quality_delta,
                    managed_quality,
                    private_quality,
                ) in quality_cases:
                    managed_correct = Decimal(volume) * managed_quality
                    private_correct = Decimal(volume) * private_quality
                    for view in ("view_a", "view_b"):
                        sensitivities = (
                            ("not_applicable",)
                            if view == "view_a"
                            else (
                                "low",
                                "typical",
                                "high",
                            )
                        )
                        for sensitivity in sensitivities:
                            if view == "view_a":
                                managed_total = managed_inference
                                private_total = private_inference
                                components: dict[str, Decimal] | None = None
                            else:
                                hours = tier.replica_hours_per_month
                                components = {
                                    "gateway_runtime_usd": (
                                        config.shared.gateway_hourly_usd * hours
                                    ),
                                    "network_nat_usd": (
                                        config.shared.network_nat_hourly_usd * hours
                                    ),
                                    "control_plane_usd": (
                                        config.shared.control_plane_hourly_usd * hours
                                    ),
                                    "shared_storage_usd": (
                                        config.shared.shared_storage_hourly_usd * hours
                                    ),
                                    "observability_usd": (
                                        config.shared.observability_hourly_usd * hours
                                    ),
                                    "operations_engineering_allocation_usd": (
                                        config.operations[sensitivity].monthly_cost  # type: ignore[index]
                                    ),
                                }
                                shared = sum(components.values(), Decimal(0))
                                managed_total = managed_inference + shared
                                private_total = private_inference + shared
                            managed_per_million = (
                                managed_total * _MILLION / Decimal(monthly_tokens)
                                if monthly_tokens
                                else None
                            )
                            private_per_million = (
                                private_total * _MILLION / Decimal(monthly_tokens)
                                if monthly_tokens
                                else None
                            )
                            managed_cpc = (
                                managed_total / managed_correct if managed_correct else None
                            )
                            private_cpc = (
                                private_total / private_correct if private_correct else None
                            )
                            if managed_cpc is None or private_cpc is None:
                                lower = "indeterminate"
                            elif managed_cpc < private_cpc:
                                lower = "managed"
                            elif private_cpc < managed_cpc:
                                lower = "private"
                            else:
                                lower = "equal"
                            rows.append(
                                {
                                    "view": view,
                                    "operations_sensitivity": sensitivity,
                                    "monthly_requests": volume,
                                    "token_profile": profile_name,
                                    "utilization_tier": tier_name,
                                    "quality_sensitivity_path": sensitivity_path,
                                    "quality_delta_points": quality_delta,
                                    "managed_quality_rate": str(managed_quality),
                                    "private_quality_rate": str(private_quality),
                                    "replicas": replicas,
                                    "managed_total_usd": str(managed_total),
                                    "private_total_usd": str(private_total),
                                    "gateway_runtime_usd": (
                                        str(components["gateway_runtime_usd"])
                                        if components is not None
                                        else None
                                    ),
                                    "network_nat_usd": (
                                        str(components["network_nat_usd"])
                                        if components is not None
                                        else None
                                    ),
                                    "control_plane_usd": (
                                        str(components["control_plane_usd"])
                                        if components is not None
                                        else None
                                    ),
                                    "shared_storage_usd": (
                                        str(components["shared_storage_usd"])
                                        if components is not None
                                        else None
                                    ),
                                    "observability_usd": (
                                        str(components["observability_usd"])
                                        if components is not None
                                        else None
                                    ),
                                    "operations_engineering_allocation_usd": (
                                        str(components["operations_engineering_allocation_usd"])
                                        if components is not None
                                        else None
                                    ),
                                    "managed_cost_per_1m_provider_billed_tokens_usd": (
                                        str(managed_per_million)
                                        if managed_per_million is not None
                                        else None
                                    ),
                                    "managed_token_definition": (
                                        "provider-billed input + output tokens"
                                        if managed_per_million is not None
                                        else None
                                    ),
                                    "private_cost_per_1m_normalized_tokens_usd": (
                                        str(private_per_million)
                                        if private_per_million is not None
                                        else None
                                    ),
                                    "private_token_definition": (
                                        "normalized input + visible output tokens"
                                        if private_per_million is not None
                                        else None
                                    ),
                                    "managed_cost_per_correct_task_usd": (
                                        str(managed_cpc) if managed_cpc is not None else None
                                    ),
                                    "private_cost_per_correct_task_usd": (
                                        str(private_cpc) if private_cpc is not None else None
                                    ),
                                    "lower_cost_path": lower,
                                }
                            )
    return rows
