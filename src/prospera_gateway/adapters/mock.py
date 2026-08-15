"""Deterministic in-process provider with reproducible failure modes."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from prospera_gateway.models import (
    CanonicalChatRequest,
    CanonicalContentPart,
    ErrorClass,
    Money,
    NormalizedUsage,
    ProviderCapabilities,
    ProviderChunk,
    ProviderError,
    ProviderHealth,
    ProviderResult,
    RequestContext,
    UsageSource,
    normalize_http_error,
    normalized_error,
)


class MockBehaviorKind(StrEnum):
    OK = "ok"
    RATE_LIMITED_429 = "rate_limited_429"
    SERVER_500 = "server_500"
    TIMEOUT = "timeout"
    MALFORMED_RESPONSE = "malformed_response"
    DELAYED = "delayed"
    STREAM_OK = "stream_ok"
    STREAM_FAIL_AFTER_FIRST_CHUNK = "stream_fail_after_first_chunk"


@dataclass(frozen=True, slots=True)
class MockBehavior:
    kind: MockBehaviorKind
    delay_ms: int = 0
    retry_after_seconds: float = 1.0

    def __post_init__(self) -> None:
        if self.delay_ms < 0:
            raise ValueError("delay_ms must be non-negative")
        if self.retry_after_seconds < 0:
            raise ValueError("retry_after_seconds must be non-negative")
        if self.kind is not MockBehaviorKind.DELAYED and self.delay_ms:
            raise ValueError("delay_ms is only valid for delayed behavior")

    @classmethod
    def delayed(cls, milliseconds: int) -> MockBehavior:
        return cls(MockBehaviorKind.DELAYED, delay_ms=milliseconds)

    @classmethod
    def rate_limited(cls, retry_after_seconds: float = 1.0) -> MockBehavior:
        return cls(
            MockBehaviorKind.RATE_LIMITED_429,
            retry_after_seconds=retry_after_seconds,
        )


class MockProviderAdapter:
    """A contract-compatible adapter whose behavior is selected per call."""

    name = "managed-fault-mock"

    def __init__(
        self,
        behavior: MockBehavior | MockBehaviorKind = MockBehaviorKind.OK,
        *,
        script: Iterable[MockBehavior | MockBehaviorKind] | None = None,
        include_usage: bool = True,
        timeout_seconds: float = 3600.0,
        input_price_per_1m: Decimal = Decimal("1.25"),
        output_price_per_1m: Decimal = Decimal("10.00"),
    ) -> None:
        self._default_behavior = self._coerce_behavior(behavior)
        self._script = deque(self._coerce_behavior(item) for item in (script or ()))
        self._include_usage = include_usage
        self._timeout_seconds = timeout_seconds
        self._input_price_per_1m = input_price_per_1m
        self._output_price_per_1m = output_price_per_1m
        self.capabilities = ProviderCapabilities(
            streaming=True,
            reports_usage=include_usage,
            reports_streaming_usage=include_usage,
        )

    @staticmethod
    def _coerce_behavior(value: MockBehavior | MockBehaviorKind) -> MockBehavior:
        if isinstance(value, MockBehavior):
            return value
        return MockBehavior(value)

    def _next_behavior(self) -> MockBehavior:
        return self._script.popleft() if self._script else self._default_behavior

    async def _before_response(self, behavior: MockBehavior) -> None:
        if behavior.kind is MockBehaviorKind.DELAYED:
            await asyncio.sleep(behavior.delay_ms / 1000)
        elif behavior.kind is MockBehaviorKind.TIMEOUT:
            await asyncio.sleep(self._timeout_seconds)
        elif behavior.kind is MockBehaviorKind.RATE_LIMITED_429:
            raise ProviderError(
                normalize_http_error(
                    429,
                    "mock provider rate limit",
                    retry_after_seconds=behavior.retry_after_seconds,
                )
            )
        elif behavior.kind is MockBehaviorKind.SERVER_500:
            raise ProviderError(normalize_http_error(500, "mock provider server error"))
        elif behavior.kind is MockBehaviorKind.MALFORMED_RESPONSE:
            raise ProviderError(
                normalized_error(
                    ErrorClass.MALFORMED_RESPONSE,
                    "mock provider returned a malformed response",
                    http_status=502,
                )
            )

    def _usage(self) -> NormalizedUsage:
        if not self._include_usage:
            return NormalizedUsage.unavailable()
        return NormalizedUsage(
            visible_output_tokens=2,
            visible_output_tokens_source=UsageSource.PROVIDER_REPORTED,
            billed_input_tokens=4,
            billed_input_tokens_source=UsageSource.PROVIDER_REPORTED,
            billed_output_tokens=2,
            billed_output_tokens_source=UsageSource.PROVIDER_REPORTED,
            reasoning_or_special_tokens=0,
            reasoning_or_special_tokens_source=UsageSource.PROVIDER_REPORTED,
        )

    async def chat(
        self,
        request: CanonicalChatRequest,
        ctx: RequestContext,
    ) -> ProviderResult:
        del ctx
        behavior = self._next_behavior()
        await self._before_response(behavior)
        if behavior.kind is MockBehaviorKind.STREAM_FAIL_AFTER_FIRST_CHUNK:
            raise ProviderError(
                normalized_error(
                    ErrorClass.MALFORMED_RESPONSE,
                    "stream-only mock behavior used for a non-streaming call",
                )
            )
        return ProviderResult(
            provider=self.name,
            model=request.model,
            output=[CanonicalContentPart(type="text", text="mock response")],
            finish_reason="stop",
            usage=self._usage(),
        )

    async def stream(
        self,
        request: CanonicalChatRequest,
        ctx: RequestContext,
    ) -> AsyncIterator[ProviderChunk]:
        del ctx
        behavior = self._next_behavior()
        await self._before_response(behavior)
        yield ProviderChunk(
            provider=self.name,
            model=request.model,
            sequence=0,
            delta=[CanonicalContentPart(type="text", text="mock ")],
        )
        if behavior.kind is MockBehaviorKind.STREAM_FAIL_AFTER_FIRST_CHUNK:
            await asyncio.sleep(0)
            raise ProviderError(
                normalized_error(
                    ErrorClass.STREAM_STARTED_FAILURE,
                    "mock provider failed after streaming began",
                )
            )
        yield ProviderChunk(
            provider=self.name,
            model=request.model,
            sequence=1,
            delta=[CanonicalContentPart(type="text", text="response")],
        )
        yield ProviderChunk(
            provider=self.name,
            model=request.model,
            sequence=2,
            is_final=True,
            finish_reason="stop",
            usage=self._usage(),
        )

    async def health(self) -> ProviderHealth:
        return ProviderHealth(healthy=True, checked_at=datetime.now(UTC), detail="mock")

    def price(self, usage: NormalizedUsage, model: str) -> Money | None:
        del model
        if usage.billed_input_tokens is None or usage.billed_output_tokens is None:
            return None
        million = Decimal(1_000_000)
        amount = (
            Decimal(usage.billed_input_tokens) * self._input_price_per_1m
            + Decimal(usage.billed_output_tokens) * self._output_price_per_1m
        ) / million
        return Money(amount=amount, currency="USD")
