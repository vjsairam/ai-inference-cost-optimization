"""Managed aggregation and symmetric View A/View B run economics."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from inference_gateway.benchmark.models import BenchmarkRecord
from inference_gateway.costing.models import CostConfig, PrivateRunInputs

_MONTH_HOURS = Decimal(30 * 24)
_MILLION = Decimal(1_000_000)

MANAGED_TOKEN_DEFINITION = "provider-billed input + output tokens"
PRIVATE_TOKEN_DEFINITION = "normalized input + visible output tokens"


@dataclass(frozen=True, slots=True)
class ManagedCostAggregate:
    total_usd: Decimal
    priced_requests: int
    missing_usage_requests: int
    estimated: bool


@dataclass(frozen=True, slots=True)
class ViewCost:
    total_usd: Decimal
    cost_per_request_usd: Decimal | None
    cost_per_correct_task_usd: Decimal | None
    cost_per_1m_tokens_usd: Decimal | None
    token_count: int | None
    token_definition: str | None


class CostEngine:
    def __init__(self, config: CostConfig) -> None:
        self.config = config

    @staticmethod
    def aggregate_managed(records: list[BenchmarkRecord]) -> ManagedCostAggregate:
        total = Decimal(0)
        priced = 0
        missing = 0
        for record in records:
            if record.provider is None or not record.provider.startswith("managed"):
                continue
            if record.managed_inference_cost_usd is None:
                missing += 1
                continue
            total += record.managed_inference_cost_usd
            priced += 1
        return ManagedCostAggregate(
            total_usd=total,
            priced_requests=priced,
            missing_usage_requests=missing,
            estimated=priced > 0,
        )

    def private_view_a(self, inputs: PrivateRunInputs) -> Decimal:
        rates = self.config.private
        return (
            inputs.gpu_node_billed_hours * rates.gpu_node_hourly_usd
            + inputs.cpu_node_billed_hours * rates.cpu_node_hourly_usd
            + inputs.model_storage_billed_hours * rates.model_storage_hourly_usd
            + inputs.model_transfer_gb * rates.model_transfer_per_gb_usd
            + inputs.node_lifecycle_billed_hours * rates.node_lifecycle_hourly_usd
        )

    def shared_platform_cost(self, billed_hours: Decimal) -> Decimal:
        return sum(
            self.shared_platform_components(billed_hours).values(),
            Decimal(0),
        )

    def shared_platform_components(self, billed_hours: Decimal) -> dict[str, Decimal]:
        shared = self.config.shared
        return {
            "gateway_runtime_usd": billed_hours * shared.gateway_hourly_usd,
            "network_nat_usd": billed_hours * shared.network_nat_hourly_usd,
            "control_plane_usd": billed_hours * shared.control_plane_hourly_usd,
            "shared_storage_usd": billed_hours * shared.shared_storage_hourly_usd,
            "observability_usd": billed_hours * shared.observability_hourly_usd,
        }

    def operations_cost(self, billed_hours: Decimal, sensitivity: str) -> Decimal:
        allocation = self.config.operations[sensitivity]  # type: ignore[index]
        return allocation.monthly_cost * billed_hours / _MONTH_HOURS

    @staticmethod
    def view(
        total: Decimal,
        request_count: int,
        correct_count: int | None,
        token_count: int | None = None,
        token_definition: str | None = None,
    ) -> ViewCost:
        per_request = total / request_count if request_count else None
        per_correct = total / correct_count if correct_count else None
        if token_count is not None and token_count > 0:
            per_million_tokens = total * _MILLION / Decimal(token_count)
            reported_token_count = token_count
            reported_token_definition = token_definition
        else:
            per_million_tokens = None
            reported_token_count = None
            reported_token_definition = None
        return ViewCost(
            total_usd=total,
            cost_per_request_usd=per_request,
            cost_per_correct_task_usd=per_correct,
            cost_per_1m_tokens_usd=per_million_tokens,
            token_count=reported_token_count,
            token_definition=reported_token_definition,
        )

    @staticmethod
    def managed_provider_billed_tokens(records: list[BenchmarkRecord]) -> int | None:
        managed = [record for record in records if (record.provider or "").startswith("managed")]
        if not managed:
            return None
        total = 0
        for record in managed:
            usage = record.usage
            if usage.billed_input_tokens is None or usage.billed_output_tokens is None:
                return None
            total += usage.billed_input_tokens + usage.billed_output_tokens
        return total

    @staticmethod
    def private_normalized_tokens(records: list[BenchmarkRecord]) -> int | None:
        private = [record for record in records if (record.provider or "").startswith("private")]
        if not private:
            return None
        total = 0
        for record in private:
            usage = record.usage
            if usage.input_tokens is None or usage.visible_output_tokens is None:
                return None
            total += usage.input_tokens + usage.visible_output_tokens
        return total

    def run_views(
        self,
        managed_cost: Decimal,
        private_inputs: PrivateRunInputs,
        request_count: int,
        correct_count: int | None,
        *,
        managed_token_count: int | None = None,
        private_token_count: int | None = None,
    ) -> dict[str, object]:
        private_a = self.private_view_a(private_inputs)
        view_a = {
            "label": "View A - inference service economics (marginal)",
            "managed": self.view(
                managed_cost,
                request_count,
                correct_count,
                managed_token_count,
                MANAGED_TOKEN_DEFINITION,
            ),
            "private": self.view(
                private_a,
                request_count,
                correct_count,
                private_token_count,
                PRIVATE_TOKEN_DEFINITION,
            ),
        }
        shared_components = self.shared_platform_components(
            private_inputs.shared_platform_billed_hours
        )
        shared = sum(shared_components.values(), Decimal(0))
        view_b: dict[str, object] = {
            "label": "View B - full-platform TCO",
            "operations_sensitivity": {},
        }
        sensitivity_rows: dict[str, object] = {}
        for sensitivity in ("low", "typical", "high"):
            additions = shared + self.operations_cost(
                private_inputs.shared_platform_billed_hours, sensitivity
            )
            components = {
                **shared_components,
                "operations_engineering_allocation_usd": self.operations_cost(
                    private_inputs.shared_platform_billed_hours, sensitivity
                ),
            }
            sensitivity_rows[sensitivity] = {
                "managed": self.view(
                    managed_cost + additions,
                    request_count,
                    correct_count,
                    managed_token_count,
                    MANAGED_TOKEN_DEFINITION,
                ),
                "private": self.view(
                    private_a + additions,
                    request_count,
                    correct_count,
                    private_token_count,
                    PRIVATE_TOKEN_DEFINITION,
                ),
                "platform_components_usd": components,
                "operations_monthly_allocation_usd": self.config.operations[
                    sensitivity
                ].monthly_cost,
            }
        view_b["operations_sensitivity"] = sensitivity_rows
        return {"view_a": view_a, "view_b": view_b}
