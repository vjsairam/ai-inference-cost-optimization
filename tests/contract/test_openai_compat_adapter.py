"""FR-003/FR-007: generic OpenAI-compatible adapter against a mock transport."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from prospera_gateway.adapters import OpenAICompatAdapter
from prospera_gateway.models import (
    CanonicalChatRequest,
    CanonicalContentPart,
    CanonicalMessage,
    DataClass,
    ErrorClass,
    MessageRole,
    ProviderError,
    QualityTier,
    RequestContext,
    RequestMetadata,
)

_CTX = RequestContext(
    request_id="req-oai",
    started_at=datetime.now(UTC),
    deadline_at=datetime.now(UTC) + timedelta(seconds=30),
)


def _request(stream: bool = False) -> CanonicalChatRequest:
    return CanonicalChatRequest(
        messages=[
            CanonicalMessage(
                role=MessageRole.USER,
                content=[CanonicalContentPart(type="text", text="hello upstream")],
            )
        ],
        model="prospera-default",
        stream=stream,
        metadata=RequestMetadata(
            workload="generic",
            data_class=DataClass.INTERNAL,
            quality_tier=QualityTier.ECONOMY,
            request_id="req-oai",
        ),
    )


def _adapter(handler) -> OpenAICompatAdapter:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://upstream.test"
    )
    return OpenAICompatAdapter(
        name="private-vllm",
        upstream_model="pinned-model",
        client=client,
        api_key="upstream-key",
    )


def _completion_payload(usage: dict[str, int] | None) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "cmpl-1",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "upstream answer"},
                "finish_reason": "stop",
            }
        ],
    }
    if usage is not None:
        payload["usage"] = usage
    return payload


async def test_chat_success_normalizes_usage() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200, json=_completion_payload({"prompt_tokens": 11, "completion_tokens": 5})
        )

    result = await _adapter(handler).chat(_request(), _CTX)
    assert result.output[0].text == "upstream answer"
    assert result.usage.billed_input_tokens == 11
    assert result.usage.billed_output_tokens == 5
    assert result.usage.visible_output_tokens == 5
    body = captured["body"]
    assert body["model"] == "pinned-model"
    assert body["messages"] == [{"role": "user", "content": "hello upstream"}]
    assert captured["auth"] == "Bearer upstream-key"


async def test_missing_usage_is_reported_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion_payload(None))

    result = await _adapter(handler).chat(_request(), _CTX)
    assert result.usage.billed_input_tokens is None
    assert result.usage.billed_output_tokens is None


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (429, ErrorClass.RATE_LIMITED),
        (500, ErrorClass.PROVIDER_5XX),
        (503, ErrorClass.PROVIDER_5XX),
        (401, ErrorClass.AUTH),
        (400, ErrorClass.INVALID_REQUEST),
    ],
)
async def test_http_errors_are_normalized(status: int, expected: ErrorClass) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        headers = {"retry-after": "7"} if status == 429 else {}
        return httpx.Response(status, json={"error": "upstream"}, headers=headers)

    with pytest.raises(ProviderError) as excinfo:
        await _adapter(handler).chat(_request(), _CTX)
    assert excinfo.value.error.error_class is expected
    if status == 429:
        assert excinfo.value.error.retry_after_seconds == 7.0


async def test_malformed_completion_payload_is_normalized() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    with pytest.raises(ProviderError) as excinfo:
        await _adapter(handler).chat(_request(), _CTX)
    assert excinfo.value.error.error_class is ErrorClass.MALFORMED_RESPONSE


def _sse_body() -> str:
    events = [
        {"choices": [{"index": 0, "delta": {"content": "up"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"content": "stream"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        {
            "choices": [],
            "usage": {"prompt_tokens": 9, "completion_tokens": 2},
        },
    ]
    lines = [f"data: {json.dumps(event)}" for event in events]
    lines.append("data: [DONE]")
    return "\n\n".join(lines) + "\n\n"


async def test_stream_success_yields_deltas_then_final_usage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, text=_sse_body(), headers={"content-type": "text/event-stream"})

    chunks = [chunk async for chunk in _adapter(handler).stream(_request(True), _CTX)]
    texts = ["".join(p.text or "" for p in c.delta) for c in chunks if not c.is_final]
    assert texts == ["up", "stream"]
    final = chunks[-1]
    assert final.is_final
    assert final.finish_reason == "stop"
    assert final.usage is not None
    assert final.usage.billed_input_tokens == 9
    assert final.usage.billed_output_tokens == 2


async def test_stream_http_error_is_normalized_before_start() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "slow down"})

    with pytest.raises(ProviderError) as excinfo:
        async for _ in _adapter(handler).stream(_request(True), _CTX):
            pass
    assert excinfo.value.error.error_class is ErrorClass.RATE_LIMITED


async def test_stream_invalid_event_is_malformed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="data: {not json}\n\n",
            headers={"content-type": "text/event-stream"},
        )

    with pytest.raises(ProviderError) as excinfo:
        async for _ in _adapter(handler).stream(_request(True), _CTX):
            pass
    assert excinfo.value.error.error_class is ErrorClass.MALFORMED_RESPONSE
