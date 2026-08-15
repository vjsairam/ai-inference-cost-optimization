"""Provider-neutral request, response, usage, health, and value models."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from prospera_gateway.models.enums import DataClass, MessageRole, QualityTier, UsageSource

NonNegativeTokenCount = Annotated[int, Field(ge=0)]


def _require_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field_name} must be timezone-aware UTC")
    return value


class CanonicalContentPart(BaseModel):
    """A typed content part with room for provider-specific structured fields."""

    model_config = ConfigDict(extra="allow")

    type: str = Field(min_length=1)
    text: str | None = None
    data: dict[str, object] | None = None


class CanonicalMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: MessageRole
    content: list[CanonicalContentPart] = Field(min_length=1)
    name: str | None = None


class RequestMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workload: str = Field(min_length=1)
    data_class: DataClass
    quality_tier: QualityTier
    request_id: str = Field(min_length=1)


class CanonicalChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[CanonicalMessage] = Field(min_length=1)
    model: str = Field(min_length=1, description="Logical model alias")
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(default=256, gt=0)
    stream: bool = False
    metadata: RequestMetadata


class NormalizedUsage(BaseModel):
    """Token counts retain independent provenance for every billing component."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    visible_output_tokens: NonNegativeTokenCount | None = None
    visible_output_tokens_source: UsageSource = UsageSource.UNAVAILABLE
    billed_input_tokens: NonNegativeTokenCount | None = None
    billed_input_tokens_source: UsageSource = UsageSource.UNAVAILABLE
    billed_output_tokens: NonNegativeTokenCount | None = None
    billed_output_tokens_source: UsageSource = UsageSource.UNAVAILABLE
    reasoning_or_special_tokens: NonNegativeTokenCount | None = None
    reasoning_or_special_tokens_source: UsageSource = UsageSource.UNAVAILABLE

    @model_validator(mode="after")
    def validate_sources(self) -> Self:
        pairs = (
            (
                self.visible_output_tokens,
                self.visible_output_tokens_source,
                "visible_output_tokens",
            ),
            (self.billed_input_tokens, self.billed_input_tokens_source, "billed_input_tokens"),
            (self.billed_output_tokens, self.billed_output_tokens_source, "billed_output_tokens"),
            (
                self.reasoning_or_special_tokens,
                self.reasoning_or_special_tokens_source,
                "reasoning_or_special_tokens",
            ),
        )
        for value, source, name in pairs:
            if value is None and source is not UsageSource.UNAVAILABLE:
                raise ValueError(f"{name} needs a value when its source is available")
            if value is not None and source is UsageSource.UNAVAILABLE:
                raise ValueError(f"{name} cannot have a value with an unavailable source")
        return self

    @classmethod
    def unavailable(cls) -> Self:
        return cls()


class ProviderResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    output: list[CanonicalContentPart]
    finish_reason: str | None = None
    usage: NormalizedUsage = Field(default_factory=NormalizedUsage.unavailable)
    extensions: dict[str, object] = Field(default_factory=dict)


class ProviderChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    delta: list[CanonicalContentPart] = Field(default_factory=list)
    is_final: bool = False
    finish_reason: str | None = None
    usage: NormalizedUsage | None = None
    extensions: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def usage_only_on_final_chunk(self) -> Self:
        if self.usage is not None and not self.is_final:
            raise ValueError("stream usage is only valid on a final chunk")
        return self


class ProviderCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    streaming: bool
    reports_usage: bool
    reports_streaming_usage: bool
    tool_calls: bool = False


class ProviderHealth(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    healthy: bool
    checked_at: datetime
    detail: str | None = None

    @model_validator(mode="after")
    def timestamp_is_utc(self) -> Self:
        _require_utc(self.checked_at, "checked_at")
        return self


class RequestContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str = Field(min_length=1)
    started_at: datetime
    deadline_at: datetime

    @model_validator(mode="after")
    def timestamps_are_ordered_utc(self) -> Self:
        _require_utc(self.started_at, "started_at")
        _require_utc(self.deadline_at, "deadline_at")
        if self.deadline_at < self.started_at:
            raise ValueError("deadline_at must not precede started_at")
        return self


class Money(BaseModel):
    """Exact currency amount; binary floating-point inputs are rejected."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    amount: Decimal
    currency: str = Field(pattern=r"^[A-Z]{3}$")

    @model_validator(mode="before")
    @classmethod
    def reject_float_amount(cls, value: object) -> object:
        if isinstance(value, dict) and isinstance(value.get("amount"), float):
            raise ValueError("money amount must not be a float")
        return value

    def __add__(self, other: Money) -> Money:
        if self.currency != other.currency:
            raise ValueError("cannot add money in different currencies")
        return Money(amount=self.amount + other.amount, currency=self.currency)

    def __mul__(self, multiplier: Decimal | int) -> Money:
        if isinstance(multiplier, float):
            raise TypeError("money multiplier must not be a float")
        return Money(amount=self.amount * multiplier, currency=self.currency)

    def __rmul__(self, multiplier: Decimal | int) -> Money:
        return self * multiplier
