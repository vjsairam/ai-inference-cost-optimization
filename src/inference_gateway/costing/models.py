"""Exact-money cost configuration and billed-resource inputs."""

from __future__ import annotations

import hashlib
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError, model_validator


def exact_decimal(value: object) -> Decimal:
    if isinstance(value, float):
        raise ValueError("cost values must be quoted decimals")
    if isinstance(value, (str, int, Decimal)) and not isinstance(value, bool):
        try:
            return Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("cost value must be a decimal") from exc
    raise ValueError("cost value must be a decimal string or integer")


MoneyDecimal = Annotated[Decimal, BeforeValidator(exact_decimal), Field(ge=0)]
PositiveDecimal = Annotated[Decimal, BeforeValidator(exact_decimal), Field(gt=0)]
FractionDecimal = Annotated[Decimal, BeforeValidator(exact_decimal), Field(gt=0, le=1)]


class OperationsAllocation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    hours_per_month: PositiveDecimal
    hourly_rate_usd: PositiveDecimal
    allocation_fraction: FractionDecimal
    basis: str = Field(min_length=1)

    @property
    def monthly_cost(self) -> Decimal:
        return self.hours_per_month * self.hourly_rate_usd * self.allocation_fraction


class SharedPlatformRates(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    gateway_hourly_usd: MoneyDecimal
    network_nat_hourly_usd: MoneyDecimal
    control_plane_hourly_usd: MoneyDecimal
    shared_storage_hourly_usd: MoneyDecimal
    observability_hourly_usd: MoneyDecimal


class PrivateRates(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    gpu_node_hourly_usd: MoneyDecimal
    cpu_node_hourly_usd: MoneyDecimal
    model_storage_hourly_usd: MoneyDecimal
    model_transfer_per_gb_usd: MoneyDecimal
    node_lifecycle_hourly_usd: MoneyDecimal
    minimum_billing_seconds: int = Field(ge=1)


class TokenProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class UtilizationTier(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requests_per_replica_month: int = Field(gt=0)
    replica_hours_per_month: PositiveDecimal


class ScenarioGridConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    monthly_requests: list[int] = Field(min_length=2)
    token_profiles: dict[str, TokenProfile] = Field(min_length=2)
    utilization_tiers: dict[Literal["low", "typical", "high"], UtilizationTier]
    quality_sensitivity_points: list[int] = Field(min_length=3)

    @model_validator(mode="after")
    def required_grid_shape(self) -> Self:
        if any(volume <= 0 for volume in self.monthly_requests):
            raise ValueError("monthly request points must be positive")
        if set(self.utilization_tiers) != {"low", "typical", "high"}:
            raise ValueError("utilization_tiers must define low, typical, and high")
        if 0 not in self.quality_sensitivity_points:
            raise ValueError("quality sensitivity must include the measured-rate point 0")
        return self


class CostConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(min_length=1)
    currency: Literal["USD"]
    effective_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    private: PrivateRates
    shared: SharedPlatformRates
    operations: dict[Literal["low", "typical", "high"], OperationsAllocation]
    scenario_grid: ScenarioGridConfig

    @model_validator(mode="after")
    def required_operations_sensitivity(self) -> Self:
        if set(self.operations) != {"low", "typical", "high"}:
            raise ValueError("operations must define non-zero low, typical, and high allocations")
        return self


class PrivateRunInputs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    gpu_node_billed_hours: MoneyDecimal
    cpu_node_billed_hours: MoneyDecimal = Decimal(0)
    model_storage_billed_hours: MoneyDecimal = Decimal(0)
    model_transfer_gb: MoneyDecimal = Decimal(0)
    node_lifecycle_billed_hours: MoneyDecimal = Decimal(0)
    shared_platform_billed_hours: MoneyDecimal = Decimal(0)


def load_cost_config(path: str | Path) -> tuple[CostConfig, str]:
    config_path = Path(path)
    try:
        content = config_path.read_bytes()
        raw = yaml.safe_load(content.decode("utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot load cost configuration {config_path}: {exc}") from exc
    try:
        config = CostConfig.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"invalid cost configuration {config_path}:\n{exc}") from exc
    return config, hashlib.sha256(content).hexdigest()
