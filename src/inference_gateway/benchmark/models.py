"""Typed scenario and per-request evidence records."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from inference_gateway.models import DataClass, ErrorClass, QualityTier, UsageSource

Workload = Literal["classification", "structured-extraction", "generation"]
EconomicView = Literal["view_a", "view_b"]


class RequestProfile(BaseModel):
    """One weighted policy input in a deterministic hybrid request mix."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    data_class: DataClass
    quality_tier: QualityTier
    weight: int = Field(default=1, ge=1, le=1000)


class BenchmarkScenario(BaseModel):
    """Frozen load shape and evidence inputs for one treatment cell."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    treatment: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,31}$")
    workload: Workload
    provider_mode: Literal["managed", "private", "hybrid", "local-mock"]
    stream: bool
    concurrency: int = Field(ge=1, le=1024)
    requests: int = Field(ge=1)
    temperature: float = Field(ge=0, le=2)
    max_tokens: int = Field(ge=1, le=8192)
    data_class: DataClass
    quality_tier: QualityTier
    structured_output_mode: str = Field(min_length=1)
    warmup_requests: int = Field(ge=0)
    repeats: int = Field(
        default=1,
        ge=1,
        validation_alias=AliasChoices("repeats", "repeat_count"),
    )
    dataset: str = Field(min_length=1)
    slo_config: str = Field(min_length=1)
    slo_cell: str = Field(min_length=1)
    pricing_config: str = Field(min_length=1)
    timeout_config: str = Field(min_length=1)
    cost_config: str = Field(min_length=1)
    model: str = Field(default="lab-default", min_length=1)
    publishable: bool = False
    economic_views: list[EconomicView] = Field(default=["view_a", "view_b"], min_length=2)
    pricing_version: str = Field(min_length=1)
    bootstrap_iterations: int = Field(default=2000, ge=100)
    seed: int = 20260815
    request_rate_schedule: str = "closed-loop"
    sampling_parameters: dict[str, int | float | str | bool | None] = Field(default_factory=dict)
    request_mix: list[RequestProfile] = Field(default_factory=list)
    notes: str = ""

    @model_validator(mode="after")
    def validate_evidence_shape(self) -> Self:
        if len(set(self.economic_views)) != 2:
            raise ValueError("economic_views must contain view_a and view_b exactly once")
        expected = {
            "classification": "WL-01",
            "structured-extraction": "WL-02",
            "generation": "WL-03",
        }[self.workload]
        if self.slo_cell != f"{expected}/{self.quality_tier.value}":
            raise ValueError("slo_cell must match workload and quality_tier")
        if self.provider_mode == "hybrid" and len(self.request_mix) < 2:
            raise ValueError("hybrid scenarios require at least two request_mix profiles")
        if self.provider_mode != "hybrid" and self.request_mix:
            raise ValueError("request_mix is only valid for hybrid scenarios")
        return self

    def slo_cells_used(self) -> tuple[str, ...]:
        """Return every workload and quality-tier SLO cell that can carry traffic."""
        workload_id = {
            "classification": "WL-01",
            "structured-extraction": "WL-02",
            "generation": "WL-03",
        }[self.workload]
        tiers = (
            {profile.quality_tier.value for profile in self.request_mix}
            if self.request_mix
            else {self.quality_tier.value}
        )
        return tuple(f"{workload_id}/{tier}" for tier in sorted(tiers))


def load_scenario(path: str | Path) -> BenchmarkScenario:
    scenario_path = Path(path)
    try:
        raw = yaml.safe_load(scenario_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot load benchmark scenario {scenario_path}: {exc}") from exc
    return BenchmarkScenario.model_validate(raw)


class TokenUsageRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int | None = Field(default=None, ge=0)
    input_tokens_source: UsageSource = UsageSource.UNAVAILABLE
    visible_output_tokens: int | None = Field(default=None, ge=0)
    visible_output_tokens_source: UsageSource = UsageSource.UNAVAILABLE
    billed_input_tokens: int | None = Field(default=None, ge=0)
    billed_input_tokens_source: UsageSource = UsageSource.UNAVAILABLE
    billed_output_tokens: int | None = Field(default=None, ge=0)
    billed_output_tokens_source: UsageSource = UsageSource.UNAVAILABLE
    reasoning_or_special_tokens: dict[str, int | str] | None = None


class BenchmarkRecord(BaseModel):
    """Appendix B.1 request record with explicit provenance."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    repeat_index: int = Field(ge=1)
    request_id: str = Field(min_length=1)
    workload: Workload
    dataset_item_id: str = Field(min_length=1)
    difficulty: Literal["easy", "medium", "hard"] | None
    data_class: DataClass | None = None
    quality_tier: QualityTier | None = None
    route: str | None
    provider: str | None
    model_alias: str = Field(min_length=1)
    started_at: datetime
    completed_at: datetime
    ttft_ms: float | None = Field(default=None, ge=0)
    e2e_ms: float = Field(ge=0)
    usage: TokenUsageRecord
    http_status: int = Field(ge=100, le=599)
    finish_reason: str | None = None
    error_class: ErrorClass | None
    fallback_count: int = Field(ge=0)
    task_correct: bool | None
    quality_score: dict[str, Any] | None
    managed_inference_cost_usd: Decimal | None

    @model_validator(mode="before")
    @classmethod
    def reject_float_money(cls, value: object) -> object:
        if isinstance(value, dict) and isinstance(value.get("managed_inference_cost_usd"), float):
            raise ValueError("managed_inference_cost_usd must not be a float")
        return value

    @model_validator(mode="after")
    def timestamps_are_utc_and_ordered(self) -> Self:
        for name, timestamp in (
            ("started_at", self.started_at),
            ("completed_at", self.completed_at),
        ):
            if timestamp.tzinfo is None or timestamp.utcoffset() != UTC.utcoffset(timestamp):
                raise ValueError(f"{name} must be timezone-aware UTC")
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must not precede started_at")
        if self.workload == "generation" and self.task_correct is not None:
            raise ValueError("generation records do not have task_correct")
        return self

    def json_line(self) -> str:
        return self.model_dump_json(exclude_none=False)


DecimalRate = Annotated[Decimal, Field(ge=0)]
