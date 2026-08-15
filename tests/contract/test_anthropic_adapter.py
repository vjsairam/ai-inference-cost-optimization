"""ADR-010: Anthropic managed adapter against a mocked Messages API."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import anthropic
import httpx
import pytest

from prospera_gateway.adapters import AnthropicManagedAdapter
from prospera_gateway.config import GatewayConfig
from prospera_gateway.config.pricing import PricingEngine
from prospera_gateway.models import (
    CanonicalChatRequest,
    CanonicalContentPart,
    CanonicalMessage,
    DataClass,
    ErrorClass,
    MessageRole,
    NormalizedUsage,
    ProviderError,
    QualityTier,
    RequestContext,
    RequestMetadata,
    UsageSource,
)

_CTX = RequestContext(
    request_id="req-anthropic",
    started_at=datetime.now(UTC),
    deadline_at=datetime.now(UTC) + timedelta(seconds=30),
)


def _request() -> CanonicalChatRequest:
    return CanonicalChatRequest(
        messages=[
            CanonicalMessage(
                role=MessageRole.SYSTEM,
                content=[CanonicalContentPart(type="text", text="be terse")],
            ),
            CanonicalMessage(
                role=MessageRole.USER,
                content=[CanonicalContentPart(type="text", text="hello claude")],
            ),
        ],
        model="prospera-premium",
        metadata=RequestMetadata(
            workload="generic",
            data_class=DataClass.INTERNAL,
            quality_tier=QualityTier.PREMIUM,
            request_id="req-anthropic",
        ),
    )


def _adapter(handler, gateway_config: GatewayConfig) -> AnthropicManagedAdapter:
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = anthropic.AsyncAnthropic(api_key="test-key", http_client=http_client, max_retries=0)
    return AnthropicManagedAdapter(
        name="managed-premium",
        upstream_model="claude-opus-5",
        model_alias="prospera-premium",
        client=client,
        pricing=PricingEngine(gateway_config.providers),
        provider_config_name="managed-primary",
    )


def _message_payload() -> dict[str, object]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5",
        "content": [{"type": "text", "text": "claude answer"}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 13, "output_tokens": 6},
    }


async def test_chat_translates_wire_format_and_usage(
    gateway_config: GatewayConfig,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_message_payload())

    result = await _adapter(handler, gateway_config).chat(_request(), _CTX)
    body = captured["body"]
    assert captured["path"] == "/v1/messages"
    assert body["model"] == "claude-opus-5"
    assert body["system"] == "be terse"
    assert body["messages"] == [{"role": "user", "content": "hello claude"}]
    assert "temperature" not in body
    assert result.output[0].text == "claude answer"
    assert result.finish_reason == "stop"  # end_turn translated to OpenAI vocabulary
    assert result.usage.billed_input_tokens == 13
    assert result.usage.billed_output_tokens == 6
    assert result.usage.billed_output_tokens_source is UsageSource.PROVIDER_REPORTED
    assert result.usage.visible_output_tokens is None


async def test_rate_limit_normalizes_with_retry_after(
    gateway_config: GatewayConfig,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"type": "error", "error": {"type": "rate_limit_error", "message": "slow"}},
            headers={"retry-after": "12"},
        )

    with pytest.raises(ProviderError) as excinfo:
        await _adapter(handler, gateway_config).chat(_request(), _CTX)
    assert excinfo.value.error.error_class is ErrorClass.RATE_LIMITED
    assert excinfo.value.error.retry_after_seconds == 12.0


async def test_server_error_normalizes(gateway_config: GatewayConfig) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500, json={"type": "error", "error": {"type": "api_error", "message": "boom"}}
        )

    with pytest.raises(ProviderError) as excinfo:
        await _adapter(handler, gateway_config).chat(_request(), _CTX)
    assert excinfo.value.error.error_class is ErrorClass.PROVIDER_5XX


def _sse(event: str, payload: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


def _stream_body() -> str:
    return "".join(
        [
            _sse(
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg_1",
                        "type": "message",
                        "role": "assistant",
                        "model": "claude-opus-5",
                        "content": [],
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 13, "output_tokens": 1},
                    },
                },
            ),
            _sse(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
            ),
            _sse(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "claude "},
                },
            ),
            _sse(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "stream"},
                },
            ),
            _sse(
                "content_block_stop",
                {"type": "content_block_stop", "index": 0},
            ),
            _sse(
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": 6},
                },
            ),
            _sse("message_stop", {"type": "message_stop"}),
        ]
    )


async def test_stream_yields_deltas_and_final_usage(
    gateway_config: GatewayConfig,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(
            200, text=_stream_body(), headers={"content-type": "text/event-stream"}
        )

    chunks = [chunk async for chunk in _adapter(handler, gateway_config).stream(_request(), _CTX)]
    texts = ["".join(p.text or "" for p in c.delta) for c in chunks if not c.is_final]
    assert texts == ["claude ", "stream"]
    final = chunks[-1]
    assert final.is_final
    assert final.finish_reason == "stop"  # end_turn translated to OpenAI vocabulary
    assert final.usage is not None
    assert final.usage.billed_input_tokens == 13
    assert final.usage.billed_output_tokens == 6


async def test_price_uses_dated_pricing_config(gateway_config: GatewayConfig) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_message_payload())

    adapter = _adapter(handler, gateway_config)
    usage = NormalizedUsage(
        billed_input_tokens=1_000_000,
        billed_input_tokens_source=UsageSource.PROVIDER_REPORTED,
        billed_output_tokens=0,
        billed_output_tokens_source=UsageSource.PROVIDER_REPORTED,
    )
    money = adapter.price(usage, "prospera-premium")
    assert money is not None
    pricing = gateway_config.providers.pricing["managed-primary"]["prospera-premium"]
    assert money.amount == Decimal(pricing.input_per_1m)
