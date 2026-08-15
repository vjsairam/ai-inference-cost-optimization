"""OpenAI-compatible public wire schemas and canonical translation (FR-001, §8.1)."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from prospera_gateway.models import (
    CanonicalChatRequest,
    CanonicalContentPart,
    CanonicalMessage,
    DataClass,
    MessageRole,
    NormalizedUsage,
    ProviderResult,
    QualityTier,
    RequestMetadata,
)

_WIRE_TO_ROLE: dict[str, MessageRole] = {
    "system": MessageRole.SYSTEM,
    "developer": MessageRole.DEVELOPER,
    "user": MessageRole.USER,
    "assistant": MessageRole.MODEL,
    "tool": MessageRole.TOOL,
}

MAX_MESSAGES = 128
MAX_CONTENT_CHARS = 200_000


class WireMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    content: str = Field(max_length=MAX_CONTENT_CHARS)
    name: str | None = None


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1, max_length=64)
    messages: list[WireMessage] = Field(min_length=1, max_length=MAX_MESSAGES)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(default=256, gt=0, le=8192)
    stream: bool = False


class RequestValidationFailed(ValueError):
    """Wire request is structurally valid JSON but violates the contract."""


def new_request_id() -> str:
    return uuid.uuid4().hex


def to_canonical(
    wire: ChatCompletionRequest,
    workload: str,
    data_class: DataClass,
    quality_tier: QualityTier,
    request_id: str,
) -> CanonicalChatRequest:
    messages: list[CanonicalMessage] = []
    for message in wire.messages:
        role = _WIRE_TO_ROLE.get(message.role)
        if role is None:
            raise RequestValidationFailed(f"unsupported message role: {message.role!r}")
        messages.append(
            CanonicalMessage(
                role=role,
                content=[CanonicalContentPart(type="text", text=message.content)],
                name=message.name,
            )
        )
    return CanonicalChatRequest(
        messages=messages,
        model=wire.model,
        temperature=wire.temperature,
        max_tokens=wire.max_tokens,
        stream=wire.stream,
        metadata=RequestMetadata(
            workload=workload,
            data_class=data_class,
            quality_tier=quality_tier,
            request_id=request_id,
        ),
    )


def _usage_payload(usage: NormalizedUsage) -> dict[str, int] | None:
    if usage.billed_input_tokens is None or usage.billed_output_tokens is None:
        return None
    return {
        "prompt_tokens": usage.billed_input_tokens,
        "completion_tokens": usage.billed_output_tokens,
        "total_tokens": usage.billed_input_tokens + usage.billed_output_tokens,
    }


def completion_response(
    request_id: str,
    created_at_epoch: int,
    model_alias: str,
    result: ProviderResult,
) -> dict[str, object]:
    text = "".join(part.text or "" for part in result.output)
    payload: dict[str, object] = {
        "id": f"chatcmpl-{request_id}",
        "object": "chat.completion",
        "created": created_at_epoch,
        "model": model_alias,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": result.finish_reason or "stop",
            }
        ],
    }
    usage = _usage_payload(result.usage)
    if usage is not None:
        payload["usage"] = usage
    return payload


def completion_chunk(
    request_id: str,
    created_at_epoch: int,
    model_alias: str,
    delta_text: str | None,
    finish_reason: str | None = None,
    usage: NormalizedUsage | None = None,
) -> dict[str, object]:
    delta: dict[str, str] = {}
    if delta_text:
        delta["content"] = delta_text
    payload: dict[str, object] = {
        "id": f"chatcmpl-{request_id}",
        "object": "chat.completion.chunk",
        "created": created_at_epoch,
        "model": model_alias,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    if usage is not None:
        usage_payload = _usage_payload(usage)
        if usage_payload is not None:
            payload["usage"] = usage_payload
    return payload
