"""Generic OpenAI-compatible chat-completions adapter (FR-003, issue #6).

Speaks the OpenAI chat-completions wire format over httpx. Used for the private
vLLM endpoint and any other OpenAI-compatible upstream. Never exposes upstream
errors unnormalized and never invents billing tokens (FR-007).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx

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

_ROLE_TO_WIRE: dict[MessageRole, str] = {
    MessageRole.SYSTEM: "system",
    MessageRole.DEVELOPER: "developer",
    MessageRole.USER: "user",
    MessageRole.MODEL: "assistant",
    MessageRole.TOOL: "tool",
}


def _flatten_text(parts: list[CanonicalContentPart]) -> str:
    return "".join(part.text or "" for part in parts)


def _wire_messages(request: CanonicalChatRequest) -> list[dict[str, str]]:
    return [
        {"role": _ROLE_TO_WIRE[message.role], "content": _flatten_text(message.content)}
        for message in request.messages
    ]


def _usage_from_payload(payload: object) -> NormalizedUsage:
    if not isinstance(payload, dict):
        return NormalizedUsage.unavailable()
    prompt = payload.get("prompt_tokens")
    completion = payload.get("completion_tokens")
    if not isinstance(prompt, int) or not isinstance(completion, int):
        return NormalizedUsage.unavailable()
    details = payload.get("completion_tokens_details")
    reasoning = details.get("reasoning_tokens") if isinstance(details, dict) else None
    kwargs: dict[str, Any] = {
        "billed_input_tokens": prompt,
        "billed_input_tokens_source": UsageSource.PROVIDER_REPORTED,
        "billed_output_tokens": completion,
        "billed_output_tokens_source": UsageSource.PROVIDER_REPORTED,
    }
    if isinstance(reasoning, int) and 0 <= reasoning <= completion:
        kwargs.update(
            visible_output_tokens=completion - reasoning,
            visible_output_tokens_source=UsageSource.PROVIDER_REPORTED,
            reasoning_or_special_tokens=reasoning,
            reasoning_or_special_tokens_source=UsageSource.PROVIDER_REPORTED,
        )
    else:
        kwargs.update(
            visible_output_tokens=completion,
            visible_output_tokens_source=UsageSource.PROVIDER_REPORTED,
        )
    return NormalizedUsage.model_validate(kwargs)


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


class OpenAICompatAdapter:
    """Adapter for any OpenAI-compatible /v1/chat/completions upstream."""

    def __init__(
        self,
        name: str,
        upstream_model: str,
        client: httpx.AsyncClient,
        api_key: str | None = None,
    ) -> None:
        self.name = name
        self._upstream_model = upstream_model
        self._client = client
        self._api_key = api_key
        self.capabilities = ProviderCapabilities(
            streaming=True,
            reports_usage=True,
            reports_streaming_usage=True,
        )

    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        if self._api_key:
            headers["authorization"] = f"Bearer {self._api_key}"
        return headers

    def _body(self, request: CanonicalChatRequest, stream: bool) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self._upstream_model,
            "messages": _wire_messages(request),
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "stream": stream,
        }
        if stream:
            body["stream_options"] = {"include_usage": True}
        return body

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        raise ProviderError(
            normalize_http_error(
                response.status_code,
                f"{self.name} returned HTTP {response.status_code}",
                retry_after_seconds=_retry_after_seconds(response),
            )
        )

    async def chat(
        self,
        request: CanonicalChatRequest,
        ctx: RequestContext,
    ) -> ProviderResult:
        del ctx
        try:
            response = await self._client.post(
                "/v1/chat/completions",
                json=self._body(request, stream=False),
                headers=self._headers(),
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(
                normalized_error(ErrorClass.TIMEOUT, f"{self.name} request timed out")
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                normalized_error(ErrorClass.TIMEOUT, f"{self.name} connection failed")
            ) from exc
        self._raise_for_status(response)
        try:
            payload = response.json()
            if not isinstance(payload, dict):
                raise TypeError("completion payload is not an object")
            choice = payload["choices"][0]
            content = choice["message"].get("content") or ""
            finish_reason = choice.get("finish_reason")
            if finish_reason is not None and not isinstance(finish_reason, str):
                raise TypeError("finish_reason is not a string")
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            raise ProviderError(
                normalized_error(
                    ErrorClass.MALFORMED_RESPONSE,
                    f"{self.name} returned a malformed completion payload",
                )
            ) from exc
        return ProviderResult(
            provider=self.name,
            model=request.model,
            output=[CanonicalContentPart(type="text", text=content)],
            finish_reason=finish_reason,
            usage=_usage_from_payload(payload.get("usage")),
        )

    async def stream(
        self,
        request: CanonicalChatRequest,
        ctx: RequestContext,
    ) -> AsyncIterator[ProviderChunk]:
        del ctx
        sequence = 0
        usage = NormalizedUsage.unavailable()
        finish_reason: str | None = None
        try:
            async with self._client.stream(
                "POST",
                "/v1/chat/completions",
                json=self._body(request, stream=True),
                headers=self._headers(),
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    self._raise_for_status(response)
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:") :].strip()
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except ValueError as exc:
                        raise ProviderError(
                            normalized_error(
                                ErrorClass.MALFORMED_RESPONSE,
                                f"{self.name} sent an invalid stream event",
                            )
                        ) from exc
                    if not isinstance(event, dict):
                        raise ProviderError(
                            normalized_error(
                                ErrorClass.MALFORMED_RESPONSE,
                                f"{self.name} sent a non-object stream event",
                            )
                        )
                    if event.get("usage") is not None:
                        usage = _usage_from_payload(event["usage"])
                    choices = event.get("choices") or []
                    first = choices[0] if isinstance(choices, list) and choices else None
                    if not isinstance(first, dict):
                        continue
                    delta = first.get("delta")
                    delta_text = delta.get("content") if isinstance(delta, dict) else None
                    raw_finish = first.get("finish_reason")
                    if isinstance(raw_finish, str):
                        finish_reason = raw_finish
                    if isinstance(delta_text, str) and delta_text:
                        yield ProviderChunk(
                            provider=self.name,
                            model=request.model,
                            sequence=sequence,
                            delta=[CanonicalContentPart(type="text", text=delta_text)],
                        )
                        sequence += 1
        except httpx.TimeoutException as exc:
            raise ProviderError(
                normalized_error(ErrorClass.TIMEOUT, f"{self.name} stream timed out")
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                normalized_error(ErrorClass.TIMEOUT, f"{self.name} stream connection failed")
            ) from exc
        yield ProviderChunk(
            provider=self.name,
            model=request.model,
            sequence=sequence,
            is_final=True,
            finish_reason=finish_reason or "stop",
            usage=usage,
        )

    async def health(self) -> ProviderHealth:
        try:
            response = await self._client.get("/v1/models", headers=self._headers())
        except httpx.HTTPError as exc:
            return ProviderHealth(
                healthy=False, checked_at=datetime.now(UTC), detail=str(exc.__class__.__name__)
            )
        return ProviderHealth(
            healthy=response.status_code < 500,
            checked_at=datetime.now(UTC),
            detail=f"HTTP {response.status_code}",
        )

    def price(self, usage: NormalizedUsage, model: str) -> Money | None:
        del usage, model
        return None
