"""Managed provider adapter for the Anthropic Claude API (ADR-010, FR-003, issue #7).

Translates canonical requests to the Messages API via the official SDK and
normalizes responses, usage, and errors. Sampling parameters are not forwarded:
current Claude models reject temperature/top_p/top_k, so determinism is a
recorded benchmark limitation for this provider rather than a request knob.
Billed usage comes only from provider-reported fields; visible output tokens are
reported unavailable because billed output may include non-visible reasoning
tokens (spec FR-007).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, cast

import anthropic

from prospera_gateway.config.pricing import PricingEngine
from prospera_gateway.models import (
    CanonicalChatRequest,
    CanonicalContentPart,
    ErrorClass,
    MessageRole,
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


def _flatten_text(parts: list[CanonicalContentPart]) -> str:
    return "".join(part.text or "" for part in parts)


def _split_messages(
    request: CanonicalChatRequest,
) -> tuple[str | None, list[dict[str, str]]]:
    system_texts: list[str] = []
    wire: list[dict[str, str]] = []
    for message in request.messages:
        text = _flatten_text(message.content)
        if message.role in (MessageRole.SYSTEM, MessageRole.DEVELOPER):
            system_texts.append(text)
        elif message.role is MessageRole.MODEL:
            wire.append({"role": "assistant", "content": text})
        else:
            wire.append({"role": "user", "content": text})
    system = "\n\n".join(system_texts) if system_texts else None
    return system, wire


def _usage(input_tokens: int | None, output_tokens: int | None) -> NormalizedUsage:
    if input_tokens is None and output_tokens is None:
        return NormalizedUsage.unavailable()
    kwargs: dict[str, object] = {}
    if input_tokens is not None:
        kwargs["billed_input_tokens"] = input_tokens
        kwargs["billed_input_tokens_source"] = UsageSource.PROVIDER_REPORTED
    if output_tokens is not None:
        kwargs["billed_output_tokens"] = output_tokens
        kwargs["billed_output_tokens_source"] = UsageSource.PROVIDER_REPORTED
    return NormalizedUsage.model_validate(kwargs)


_FINISH_REASONS = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_calls",
    "refusal": "content_filter",
}


def _finish_reason(stop_reason: str | None) -> str:
    if stop_reason is None:
        return "stop"
    return _FINISH_REASONS.get(stop_reason, "stop")


def _normalize_error(name: str, exc: Exception) -> ProviderError:
    if isinstance(exc, anthropic.APITimeoutError):
        return ProviderError(normalized_error(ErrorClass.TIMEOUT, f"{name} request timed out"))
    if isinstance(exc, anthropic.APIConnectionError):
        return ProviderError(normalized_error(ErrorClass.TIMEOUT, f"{name} connection failed"))
    if isinstance(exc, anthropic.APIStatusError):
        retry_after: float | None = None
        raw = exc.response.headers.get("retry-after")
        if raw is not None:
            try:
                retry_after = max(0.0, float(raw))
            except ValueError:
                retry_after = None
        if 400 <= exc.status_code < 600:
            return ProviderError(
                normalize_http_error(
                    exc.status_code,
                    f"{name} returned HTTP {exc.status_code}",
                    retry_after_seconds=retry_after,
                )
            )
        # In-band stream errors (e.g. overloaded_error) surface on an HTTP 200
        # response; treat them as server-side failures so fallback applies.
        return ProviderError(
            normalized_error(
                ErrorClass.PROVIDER_5XX,
                f"{name} reported an in-band error",
                retry_after_seconds=retry_after,
            )
        )
    if isinstance(exc, anthropic.APIResponseValidationError):
        return ProviderError(
            normalized_error(ErrorClass.MALFORMED_RESPONSE, f"{name} sent an invalid payload")
        )
    if isinstance(exc, anthropic.AnthropicError):
        return ProviderError(
            normalized_error(ErrorClass.PROVIDER_5XX, f"{name} client reported a failure")
        )
    return ProviderError(normalized_error(ErrorClass.MALFORMED_RESPONSE, f"{name} client failure"))


class AnthropicManagedAdapter:
    """ProviderAdapter implementation for the Anthropic Messages API."""

    def __init__(
        self,
        name: str,
        upstream_model: str,
        model_alias: str,
        client: anthropic.AsyncAnthropic,
        pricing: PricingEngine,
        provider_config_name: str,
        supports_sampling: bool = False,
    ) -> None:
        self.name = name
        self._upstream_model = upstream_model
        self._model_alias = model_alias
        self._client = client
        self._pricing = pricing
        self._provider_config_name = provider_config_name
        self._supports_sampling = supports_sampling
        self.capabilities = ProviderCapabilities(
            streaming=True,
            reports_usage=True,
            reports_streaming_usage=True,
        )

    def _sampling_kwargs(self, request: CanonicalChatRequest) -> dict[str, Any]:
        if not self._supports_sampling:
            return {}
        # Anthropic accepts temperature in [0, 1]; the public surface allows
        # up to 2.0, so clamp rather than fail the request.
        return {"temperature": min(request.temperature, 1.0)}

    async def chat(
        self,
        request: CanonicalChatRequest,
        ctx: RequestContext,
    ) -> ProviderResult:
        del ctx
        system, messages = _split_messages(request)
        try:
            response = await self._client.messages.create(
                model=self._upstream_model,
                max_tokens=request.max_tokens,
                system=system if system is not None else anthropic.omit,
                messages=cast(Any, messages),
                **self._sampling_kwargs(request),
            )
        except Exception as exc:  # noqa: BLE001 - normalized below
            raise _normalize_error(self.name, exc) from exc
        text = "".join(block.text for block in response.content if block.type == "text")
        return ProviderResult(
            provider=self.name,
            model=request.model,
            output=[CanonicalContentPart(type="text", text=text)],
            finish_reason=_finish_reason(response.stop_reason),
            usage=_usage(response.usage.input_tokens, response.usage.output_tokens),
        )

    async def stream(
        self,
        request: CanonicalChatRequest,
        ctx: RequestContext,
    ) -> AsyncIterator[ProviderChunk]:
        del ctx
        system, messages = _split_messages(request)
        sequence = 0
        input_tokens: int | None = None
        output_tokens: int | None = None
        finish_reason: str | None = None
        try:
            stream = await self._client.messages.create(
                model=self._upstream_model,
                max_tokens=request.max_tokens,
                system=system if system is not None else anthropic.omit,
                messages=cast(Any, messages),
                stream=True,
                **self._sampling_kwargs(request),
            )
            async for event in stream:
                if isinstance(event, anthropic.types.RawMessageStartEvent):
                    input_tokens = event.message.usage.input_tokens
                elif isinstance(event, anthropic.types.RawContentBlockDeltaEvent):
                    delta = event.delta
                    if isinstance(delta, anthropic.types.TextDelta) and delta.text:
                        yield ProviderChunk(
                            provider=self.name,
                            model=request.model,
                            sequence=sequence,
                            delta=[CanonicalContentPart(type="text", text=delta.text)],
                        )
                        sequence += 1
                elif isinstance(event, anthropic.types.RawMessageDeltaEvent):
                    if event.usage.output_tokens is not None:
                        output_tokens = event.usage.output_tokens
                    if event.delta.stop_reason is not None:
                        finish_reason = event.delta.stop_reason
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalized below
            raise _normalize_error(self.name, exc) from exc
        yield ProviderChunk(
            provider=self.name,
            model=request.model,
            sequence=sequence,
            is_final=True,
            finish_reason=_finish_reason(finish_reason),
            usage=_usage(input_tokens, output_tokens),
        )

    async def health(self) -> ProviderHealth:
        try:
            await self._client.models.retrieve(self._upstream_model)
        except Exception as exc:  # noqa: BLE001 - health is diagnostic only
            return ProviderHealth(
                healthy=False,
                checked_at=datetime.now(UTC),
                detail=exc.__class__.__name__,
            )
        return ProviderHealth(healthy=True, checked_at=datetime.now(UTC), detail="ok")

    def price(self, usage: NormalizedUsage, model: str) -> Money | None:
        del model
        return self._pricing.price(self._provider_config_name, self._model_alias, usage)
