"""Bounded fallback execution (FR-006, spec §8.4, §8.7).

Fallback is attempted only for configured error classes, only while attempts and
the global deadline remain, and never after streaming output has begun. Retry-After
hints are honored only when the global deadline still leaves time for another
attempt. Restricted data never reaches an external provider even if a decision were
somehow mis-built (defense in depth on top of the router and load-time validation).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from inference_gateway.adapters.base import ProviderAdapter
from inference_gateway.config import RoutingPolicy
from inference_gateway.models import (
    AttemptOutcome,
    CanonicalChatRequest,
    DataClass,
    ErrorClass,
    ProviderChunk,
    ProviderError,
    ProviderResult,
    RequestContext,
    normalized_error,
)
from inference_gateway.routing.policy import RouteDecision


class FallbackResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    result: ProviderResult
    provider: str
    fallback_count: int = Field(ge=0)
    attempts: tuple[AttemptOutcome, ...]


def _remaining_seconds(ctx: RequestContext) -> float:
    return (ctx.deadline_at - datetime.now(UTC)).total_seconds()


def _deadline_error() -> ProviderError:
    return ProviderError(normalized_error(ErrorClass.TIMEOUT, "global request deadline exhausted"))


def _with_attempts(error: ProviderError, attempts: list[AttemptOutcome]) -> ProviderError:
    error.attempts = tuple(attempts)
    return error


async def _wait_for_retry_after(error: ProviderError, ctx: RequestContext) -> bool:
    hint = error.error.retry_after_seconds
    if hint is None:
        return True
    remaining = _remaining_seconds(ctx)
    delay = min(hint, remaining)
    if remaining - delay <= 0:
        return False
    await asyncio.sleep(delay)
    return True


class FallbackExecutor:
    """Runs a route decision against adapters with bounded fallback."""

    def __init__(
        self,
        adapters: Mapping[str, ProviderAdapter],
        policy: RoutingPolicy,
    ) -> None:
        self._adapters = adapters
        self._policy = policy

    def _permitted_providers(self, decision: RouteDecision, data_class: DataClass) -> list[str]:
        providers = list(decision.providers)
        if self._policy.fallback.never_cross_data_policy and data_class is DataClass.RESTRICTED:
            providers = [name for name in providers if not self._policy.providers[name].external]
        return providers[: self._policy.fallback.max_attempts]

    def _fallback_eligible(self, error: ProviderError) -> bool:
        return error.error.fallback_eligible and error.error.error_class in self._policy.fallback.on

    async def chat(
        self,
        request: CanonicalChatRequest,
        ctx: RequestContext,
        decision: RouteDecision,
    ) -> FallbackResult:
        providers = self._permitted_providers(decision, request.metadata.data_class)
        attempts: list[AttemptOutcome] = []
        timeouts = self._policy.timeouts
        for index, provider_name in enumerate(providers):
            remaining = _remaining_seconds(ctx)
            if remaining <= 0:
                raise _with_attempts(_deadline_error(), attempts)
            adapter = self._adapters[provider_name]
            budget = min(timeouts.per_attempt_timeout, remaining)
            try:
                async with asyncio.timeout(budget):
                    result = await adapter.chat(request, ctx)
            except TimeoutError as exc:
                error = ProviderError(
                    normalized_error(ErrorClass.TIMEOUT, "provider attempt timed out")
                )
                error.__cause__ = exc
            except ProviderError as exc:
                error = exc
            else:
                attempts.append(AttemptOutcome(provider=provider_name))
                return FallbackResult(
                    result=result,
                    provider=provider_name,
                    fallback_count=index,
                    attempts=tuple(attempts),
                )
            attempts.append(
                AttemptOutcome(
                    provider=provider_name,
                    error_class=error.error.error_class,
                    retry_after_seconds=error.error.retry_after_seconds,
                )
            )
            is_last = index == len(providers) - 1
            if is_last or not self._fallback_eligible(error):
                raise _with_attempts(error, attempts)
            if not await _wait_for_retry_after(error, ctx):
                raise _with_attempts(error, attempts)
        raise _with_attempts(_deadline_error(), attempts)

    async def stream(
        self,
        request: CanonicalChatRequest,
        ctx: RequestContext,
        decision: RouteDecision,
        attempt_log: list[AttemptOutcome] | None = None,
    ) -> AsyncIterator[tuple[str, int, ProviderChunk]]:
        """Yield (provider, fallback_count, chunk); never replays after first chunk.

        ``attempt_log``, when provided, is appended in place with one record per
        provider attempt so callers can attribute failures and fallbacks even
        while the response is being streamed.
        """
        providers = self._permitted_providers(decision, request.metadata.data_class)
        attempts = attempt_log if attempt_log is not None else []
        timeouts = self._policy.timeouts
        loop = asyncio.get_running_loop()
        for index, provider_name in enumerate(providers):
            remaining = _remaining_seconds(ctx)
            if remaining <= 0:
                raise _with_attempts(_deadline_error(), attempts)
            adapter = self._adapters[provider_name]
            iterator = adapter.stream(request, ctx)
            started = False
            error: ProviderError | None = None
            attempt_deadline = loop.time() + timeouts.per_attempt_timeout
            try:
                while True:
                    idle_budget = (
                        timeouts.response_header_timeout
                        if not started
                        else timeouts.stream_idle_timeout
                    )
                    budget = min(
                        idle_budget,
                        _remaining_seconds(ctx),
                        attempt_deadline - loop.time(),
                    )
                    if budget <= 0:
                        raise ProviderError(
                            normalized_error(
                                ErrorClass.TIMEOUT, "provider attempt budget exhausted"
                            )
                        )
                    try:
                        async with asyncio.timeout(budget):
                            chunk = await anext(iterator)
                    except StopAsyncIteration:
                        attempts.append(AttemptOutcome(provider=provider_name))
                        return
                    started = True
                    yield provider_name, index, chunk
            except TimeoutError as exc:
                error = ProviderError(
                    normalized_error(ErrorClass.TIMEOUT, "provider stream timed out")
                )
                error.__cause__ = exc
            except ProviderError as exc:
                error = exc
            finally:
                aclose = getattr(iterator, "aclose", None)
                if aclose is not None:
                    await aclose()
            if error is None:
                return
            attempts.append(
                AttemptOutcome(
                    provider=provider_name,
                    error_class=error.error.error_class,
                    retry_after_seconds=error.error.retry_after_seconds,
                )
            )
            if started:
                raise _with_attempts(
                    ProviderError(
                        normalized_error(
                            ErrorClass.STREAM_STARTED_FAILURE,
                            "provider failed after streaming began; no replay",
                        )
                    ),
                    attempts,
                ) from error
            is_last = index == len(providers) - 1
            if is_last or not self._fallback_eligible(error):
                raise _with_attempts(error, attempts)
            if not await _wait_for_retry_after(error, ctx):
                raise _with_attempts(error, attempts)
        raise _with_attempts(_deadline_error(), attempts)
