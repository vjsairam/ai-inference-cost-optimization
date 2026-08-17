"""Integration (spec §17): gateway + real adapters against mocked upstreams.

The private path runs the real OpenAI-compatible adapter against a mock vLLM
wire endpoint; the managed path runs the real Anthropic adapter against a mock
Messages API. Only the network transport is faked.
"""

from __future__ import annotations

import json

import anthropic
import httpx
import pytest

from inference_gateway.adapters import AnthropicManagedAdapter, OpenAICompatAdapter
from inference_gateway.adapters.base import ProviderAdapter
from inference_gateway.api import create_app
from inference_gateway.config import GatewayConfig
from inference_gateway.config.pricing import PricingEngine
from inference_gateway.security import AuthConfig


class UpstreamCounters:
    def __init__(self) -> None:
        self.vllm_requests = 0
        self.anthropic_requests = 0


@pytest.fixture()
def counters() -> UpstreamCounters:
    return UpstreamCounters()


def _vllm_handler(counters: UpstreamCounters, fail_first: bool = False):
    def handler(request: httpx.Request) -> httpx.Response:
        counters.vllm_requests += 1
        if fail_first and counters.vllm_requests == 1:
            return httpx.Response(429, json={"error": "busy"}, headers={"retry-after": "1"})
        body = json.loads(request.content)
        assert body["model"] == "lab-private"
        return httpx.Response(
            200,
            json={
                "id": "cmpl-vllm",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "private answer"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 21, "completion_tokens": 8},
            },
        )

    return handler


def _anthropic_handler(counters: UpstreamCounters):
    def handler(request: httpx.Request) -> httpx.Response:
        counters.anthropic_requests += 1
        return httpx.Response(
            200,
            json={
                "id": "msg_e2e",
                "type": "message",
                "role": "assistant",
                "model": "PLACEHOLDER_PREMIUM_MODEL_ID",
                "content": [{"type": "text", "text": "managed answer"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 40, "output_tokens": 10},
            },
        )

    return handler


def _real_adapters(
    gateway_config: GatewayConfig,
    counters: UpstreamCounters,
    vllm_fail_first: bool = False,
) -> dict[str, ProviderAdapter]:
    pricing = PricingEngine(gateway_config.providers)
    providers = gateway_config.providers.providers
    vllm_client = httpx.AsyncClient(
        transport=httpx.MockTransport(_vllm_handler(counters, vllm_fail_first)),
        base_url="http://vllm.internal.test",
    )
    anthropic_transport = httpx.MockTransport(_anthropic_handler(counters))

    def managed(route_name: str, model_alias: str) -> AnthropicManagedAdapter:
        return AnthropicManagedAdapter(
            name=route_name,
            upstream_model=providers["managed-primary"].models[model_alias].upstream_model,
            model_alias=model_alias,
            client=anthropic.AsyncAnthropic(
                api_key="integration-test-key",
                http_client=httpx.AsyncClient(transport=anthropic_transport),
            ),
            pricing=pricing,
            provider_config_name="managed-primary",
        )

    return {
        "private-vllm": OpenAICompatAdapter(
            name="private-vllm",
            upstream_model=providers["private-vllm"].models["lab-private"].upstream_model,
            client=vllm_client,
        ),
        "managed-economy": managed("managed-economy", "lab-economy"),
        "managed-premium": managed("managed-premium", "lab-premium"),
    }


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway.test")


def _headers(lab_api_key: str, data_class: str, quality_tier: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {lab_api_key}",
        "X-Gateway-Data-Class": data_class,
        "X-Gateway-Quality-Tier": quality_tier,
        "X-Gateway-Workload": "generic",
    }


def _body() -> dict[str, object]:
    return {
        "model": "lab-default",
        "messages": [{"role": "user", "content": "integration hello"}],
        "max_tokens": 64,
    }


async def test_economy_request_uses_private_path(
    gateway_config: GatewayConfig,
    auth_config: AuthConfig,
    lab_api_key: str,
    counters: UpstreamCounters,
) -> None:
    app = create_app(gateway_config, auth_config, adapters=_real_adapters(gateway_config, counters))
    async with _client(app) as client:
        response = await client.post(
            "/v1/chat/completions",
            json=_body(),
            headers=_headers(lab_api_key, "internal", "economy"),
        )
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "private answer"
    assert counters.vllm_requests == 1
    assert counters.anthropic_requests == 0


async def test_premium_request_uses_managed_path_and_records_cost(
    gateway_config: GatewayConfig,
    auth_config: AuthConfig,
    lab_api_key: str,
    counters: UpstreamCounters,
) -> None:
    app = create_app(gateway_config, auth_config, adapters=_real_adapters(gateway_config, counters))
    async with _client(app) as client:
        response = await client.post(
            "/v1/chat/completions",
            json=_body(),
            headers=_headers(lab_api_key, "public", "premium"),
        )
        metrics = await client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["X-Gateway-Provider"] == "managed-premium"
    assert response.json()["usage"]["total_tokens"] == 50
    assert counters.anthropic_requests == 1
    text = metrics.text
    assert "gateway_estimated_managed_cost_usd_total" in text
    assert 'provider="managed-premium"' in text


async def test_managed_rate_limit_falls_back_to_private(
    gateway_config: GatewayConfig,
    auth_config: AuthConfig,
    lab_api_key: str,
    counters: UpstreamCounters,
) -> None:
    """Premium rule: managed-premium primary, private-vllm fallback."""
    adapters = _real_adapters(gateway_config, counters)
    rate_limited = httpx.MockTransport(
        lambda request: httpx.Response(
            429,
            json={"type": "error", "error": {"type": "rate_limit_error", "message": "x"}},
            headers={"retry-after": "3"},
        )
    )
    pricing = PricingEngine(gateway_config.providers)
    adapters["managed-premium"] = AnthropicManagedAdapter(
        name="managed-premium",
        upstream_model="PLACEHOLDER_PREMIUM_MODEL_ID",
        model_alias="lab-premium",
        client=anthropic.AsyncAnthropic(
            api_key="integration-test-key",
            http_client=httpx.AsyncClient(transport=rate_limited),
            max_retries=0,
        ),
        pricing=pricing,
        provider_config_name="managed-primary",
    )
    app = create_app(gateway_config, auth_config, adapters=adapters)
    async with _client(app) as client:
        response = await client.post(
            "/v1/chat/completions",
            json=_body(),
            headers=_headers(lab_api_key, "public", "premium"),
        )
    assert response.status_code == 200
    assert response.headers["X-Gateway-Provider"] == "private-vllm"
    assert response.json()["choices"][0]["message"]["content"] == "private answer"


async def test_restricted_never_reaches_managed_upstreams(
    gateway_config: GatewayConfig,
    auth_config: AuthConfig,
    lab_api_key: str,
    counters: UpstreamCounters,
) -> None:
    app = create_app(gateway_config, auth_config, adapters=_real_adapters(gateway_config, counters))
    async with _client(app) as client:
        response = await client.post(
            "/v1/chat/completions",
            json=_body(),
            headers=_headers(lab_api_key, "restricted", "premium"),
        )
    assert response.status_code == 200
    assert response.headers["X-Gateway-Provider"] == "private-vllm"
    assert counters.anthropic_requests == 0
