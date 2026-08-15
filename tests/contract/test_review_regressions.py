"""Regression coverage for the M1 adversarial-review findings."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import anthropic
import httpx
import pytest

from inference_gateway.adapters import (
    AnthropicManagedAdapter,
    MockBehaviorKind,
    MockProviderAdapter,
    OpenAICompatAdapter,
)
from inference_gateway.api import create_app
from inference_gateway.api.app import build_adapters
from inference_gateway.config import GatewayConfig, RoutingPolicy, TimeoutConfig
from inference_gateway.config.pricing import PricingEngine
from inference_gateway.models import (
    CanonicalChatRequest,
    CanonicalContentPart,
    CanonicalMessage,
    DataClass,
    ErrorClass,
    MessageRole,
    NormalizedUsage,
    ProviderCapabilities,
    ProviderChunk,
    ProviderError,
    ProviderHealth,
    ProviderResult,
    QualityTier,
    RequestContext,
    RequestMetadata,
)
from inference_gateway.routing import FallbackExecutor, RouteDecision

_CTX = RequestContext(
    request_id="req-regress",
    started_at=datetime.now(UTC),
    deadline_at=datetime.now(UTC) + timedelta(seconds=30),
)


def _request(stream: bool = False) -> CanonicalChatRequest:
    return CanonicalChatRequest(
        messages=[
            CanonicalMessage(
                role=MessageRole.USER,
                content=[CanonicalContentPart(type="text", text="hi")],
            )
        ],
        model="lab-default",
        temperature=1.7,
        stream=stream,
        metadata=RequestMetadata(
            workload="generic",
            data_class=DataClass.INTERNAL,
            quality_tier=QualityTier.ECONOMY,
            request_id="req-regress",
        ),
    )


def _mock_adapters() -> dict[str, MockProviderAdapter]:
    adapters = {
        "private-vllm": MockProviderAdapter(),
        "managed-economy": MockProviderAdapter(),
        "managed-premium": MockProviderAdapter(),
    }
    for name, adapter in adapters.items():
        adapter.name = name
    return adapters


def _client(gateway_config, auth_config, adapters) -> httpx.AsyncClient:
    app = create_app(gateway_config, auth_config, adapters=adapters)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway.test")


def _headers(lab_api_key: str, **extra: str) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {lab_api_key}",
        "X-Gateway-Data-Class": "internal",
        "X-Gateway-Quality-Tier": "economy",
        "X-Gateway-Workload": "generic",
    }
    headers.update(extra)
    return headers


# Finding 1: user-controlled model values must not mint metric series.
async def test_unknown_model_alias_is_bounded_in_metrics(
    gateway_config, auth_config, lab_api_key
) -> None:
    secret_alias = "sk.secret.model.value"
    async with _client(gateway_config, auth_config, _mock_adapters()) as client:
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": secret_alias,
                "messages": [{"role": "user", "content": "x"}],
            },
            headers=_headers(lab_api_key),
        )
        metrics = await client.get("/metrics")
    assert response.status_code == 200
    assert secret_alias not in metrics.text
    assert 'model_alias="other"' in metrics.text


async def test_configured_alias_keeps_its_metric_label(
    gateway_config, auth_config, lab_api_key
) -> None:
    async with _client(gateway_config, auth_config, _mock_adapters()) as client:
        await client.post(
            "/v1/chat/completions",
            json={
                "model": "lab-default",
                "messages": [{"role": "user", "content": "x"}],
            },
            headers=_headers(lab_api_key),
        )
        metrics = await client.get("/metrics")
    assert 'model_alias="lab-default"' in metrics.text


# Findings 2 and 3: production Anthropic client must not retry and must
# carry the configured timeouts.
def test_build_adapters_pins_anthropic_retries_and_timeouts(
    gateway_config: GatewayConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRIVATE_VLLM_BASE_URL", "http://vllm.internal.test")
    monkeypatch.setenv("MANAGED_PRIMARY_API_KEY", "test-key")
    adapters = build_adapters(
        gateway_config,
        PricingEngine(gateway_config.providers),
        connect_timeout=2.0,
        response_header_timeout=17.0,
    )
    managed = adapters["managed-premium"]
    client = managed._client  # noqa: SLF001 - asserting construction parameters
    assert client.max_retries == 0
    assert client.timeout.connect == 2.0
    assert client.timeout.read == 17.0


# Finding 4: streaming attempts must honor per_attempt_timeout.
class _SlowStreamAdapter:
    name = "private-vllm"
    capabilities = ProviderCapabilities(
        streaming=True, reports_usage=False, reports_streaming_usage=False
    )

    async def chat(self, request: CanonicalChatRequest, ctx: RequestContext) -> ProviderResult:
        raise NotImplementedError

    async def stream(
        self, request: CanonicalChatRequest, ctx: RequestContext
    ) -> AsyncIterator[ProviderChunk]:
        sequence = 0
        while True:
            await asyncio.sleep(0.03)
            yield ProviderChunk(
                provider=self.name,
                model=request.model,
                sequence=sequence,
                delta=[CanonicalContentPart(type="text", text="t")],
            )
            sequence += 1

    async def health(self) -> ProviderHealth:
        return ProviderHealth(healthy=True, checked_at=datetime.now(UTC))

    def price(self, usage: NormalizedUsage, model: str) -> None:
        return None


async def test_stream_per_attempt_timeout_bounds_slow_drip(
    routing_policy: RoutingPolicy,
) -> None:
    policy = routing_policy.model_copy(
        update={
            "timeouts": TimeoutConfig(
                connect_timeout=0.5,
                response_header_timeout=0.5,
                stream_idle_timeout=0.5,
                per_attempt_timeout=0.15,
                global_request_deadline=5.0,
            )
        }
    )
    executor = FallbackExecutor({"private-vllm": _SlowStreamAdapter()}, policy)
    decision = RouteDecision(rule_name="r", primary="private-vllm")
    received = 0
    with pytest.raises(ProviderError) as excinfo:
        async for _ in executor.stream(_request(True), _CTX, decision):
            received += 1
    assert excinfo.value.error.error_class is ErrorClass.STREAM_STARTED_FAILURE
    assert 1 <= received <= 10


# Finding 5: Anthropic in-band stream errors normalize to a fallback-eligible class.
async def test_anthropic_in_band_stream_error_normalizes(
    gateway_config: GatewayConfig,
) -> None:
    body = (
        'event: error\ndata: {"type":"error","error":'
        '{"type":"overloaded_error","message":"busy"}}\n\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    adapter = AnthropicManagedAdapter(
        name="managed-premium",
        upstream_model="model-x",
        model_alias="lab-premium",
        client=anthropic.AsyncAnthropic(
            api_key="k",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            max_retries=0,
        ),
        pricing=PricingEngine(gateway_config.providers),
        provider_config_name="managed-primary",
    )
    with pytest.raises(ProviderError) as excinfo:
        async for _ in adapter.stream(_request(True), _CTX):
            pass
    assert excinfo.value.error.error_class in (
        ErrorClass.PROVIDER_5XX,
        ErrorClass.RATE_LIMITED,
    )


# Finding 6: JSON-valid but structurally invalid SSE events normalize.
async def test_openai_stream_non_object_event_is_malformed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text="data: []\n\n", headers={"content-type": "text/event-stream"}
        )

    adapter = OpenAICompatAdapter(
        name="private-vllm",
        upstream_model="m",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://u.test"),
    )
    with pytest.raises(ProviderError) as excinfo:
        async for _ in adapter.stream(_request(True), _CTX):
            pass
    assert excinfo.value.error.error_class is ErrorClass.MALFORMED_RESPONSE


# Finding 7: pre-stream provider failure keeps a real HTTP error status.
async def test_streaming_pre_stream_failure_returns_http_error(
    gateway_config, auth_config, lab_api_key
) -> None:
    adapters = _mock_adapters()
    adapters["private-vllm"] = MockProviderAdapter(MockBehaviorKind.RATE_LIMITED_429)
    adapters["managed-economy"] = MockProviderAdapter(MockBehaviorKind.RATE_LIMITED_429)
    for name, adapter in adapters.items():
        adapter.name = name
    async with _client(gateway_config, auth_config, adapters) as client:
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "lab-default",
                "messages": [{"role": "user", "content": "x"}],
                "stream": True,
            },
            headers=_headers(lab_api_key),
        )
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"


# Finding 9: temperature is forwarded when the model supports sampling.
async def test_temperature_forwarded_and_clamped_when_supported(
    gateway_config: GatewayConfig,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "msg",
                "type": "message",
                "role": "assistant",
                "model": "m",
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    def adapter(supports_sampling: bool) -> AnthropicManagedAdapter:
        return AnthropicManagedAdapter(
            name="managed-economy",
            upstream_model="m",
            model_alias="lab-economy",
            client=anthropic.AsyncAnthropic(
                api_key="k",
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                max_retries=0,
            ),
            pricing=PricingEngine(gateway_config.providers),
            provider_config_name="managed-primary",
            supports_sampling=supports_sampling,
        )

    await adapter(True).chat(_request(), _CTX)
    body = captured["body"]
    assert body["temperature"] == 1.0  # 1.7 clamped to Anthropic's [0, 1]
    await adapter(False).chat(_request(), _CTX)
    assert "temperature" not in captured["body"]


# Finding 10: reasoning tokens split billed vs visible output.
async def test_openai_reasoning_tokens_are_separated() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "a"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 100,
                    "completion_tokens_details": {"reasoning_tokens": 80},
                },
            },
        )

    adapter = OpenAICompatAdapter(
        name="private-vllm",
        upstream_model="m",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://u.test"),
    )
    result = await adapter.chat(_request(), _CTX)
    assert result.usage.billed_output_tokens == 100
    assert result.usage.visible_output_tokens == 20
    assert result.usage.reasoning_or_special_tokens == 80


# Finding 11 (reduced): tool-role messages are explicitly rejected in v1.
async def test_tool_role_is_rejected(gateway_config, auth_config, lab_api_key) -> None:
    async with _client(gateway_config, auth_config, _mock_adapters()) as client:
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "lab-default",
                "messages": [
                    {"role": "user", "content": "x"},
                    {"role": "tool", "content": "result"},
                ],
            },
            headers=_headers(lab_api_key),
        )
    assert response.status_code == 400


# Finding 12: terminal fallback failures attribute every attempt.
async def test_terminal_fallback_failure_attributes_all_attempts(
    gateway_config, auth_config, lab_api_key
) -> None:
    adapters = _mock_adapters()
    adapters["private-vllm"] = MockProviderAdapter(MockBehaviorKind.RATE_LIMITED_429)
    adapters["managed-economy"] = MockProviderAdapter(MockBehaviorKind.SERVER_500)
    for name, adapter in adapters.items():
        adapter.name = name
    async with _client(gateway_config, auth_config, adapters) as client:
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "lab-default",
                "messages": [{"role": "user", "content": "x"}],
            },
            headers=_headers(lab_api_key),
        )
        metrics = await client.get("/metrics")
    assert response.status_code == 502
    text = metrics.text
    assert (
        'gateway_provider_errors_total{error_class="rate_limited",provider="private-vllm"}' in text
    )
    assert (
        'gateway_provider_errors_total{error_class="provider_5xx",provider="managed-economy"}'
        in text
    )
    assert 'from_provider="private-vllm"' in text
    assert 'to_provider="managed-economy"' in text
    assert 'outcome="provider_5xx"' in text
