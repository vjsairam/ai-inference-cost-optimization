"""Gateway HTTP contract (FR-001/002/005/009/010 and spec §8.6)."""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from prospera_gateway.adapters import MockBehaviorKind, MockProviderAdapter
from prospera_gateway.api import create_app
from prospera_gateway.config import GatewayConfig
from prospera_gateway.security import AuthConfig

SENTINEL_PROMPT = "SENTINEL-PROMPT-BODY-9f31c2"


def _mock_adapters(
    private: MockProviderAdapter | None = None,
    economy: MockProviderAdapter | None = None,
    premium: MockProviderAdapter | None = None,
) -> dict[str, MockProviderAdapter]:
    adapters = {
        "private-vllm": private or MockProviderAdapter(),
        "managed-economy": economy or MockProviderAdapter(),
        "managed-premium": premium or MockProviderAdapter(),
    }
    for name, adapter in adapters.items():
        adapter.name = name
    return adapters


def _client(
    gateway_config: GatewayConfig,
    auth_config: AuthConfig,
    adapters: dict[str, MockProviderAdapter],
) -> httpx.AsyncClient:
    app = create_app(gateway_config, auth_config, adapters=adapters)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway.test")


def _headers(
    lab_api_key: str,
    data_class: str = "internal",
    quality_tier: str = "economy",
    **extra: str,
) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {lab_api_key}",
        "X-Prospera-Data-Class": data_class,
        "X-Prospera-Quality-Tier": quality_tier,
        "X-Prospera-Workload": "generic",
    }
    headers.update(extra)
    return headers


def _body(stream: bool = False) -> dict[str, object]:
    return {
        "model": "prospera-default",
        "messages": [{"role": "user", "content": SENTINEL_PROMPT}],
        "max_tokens": 64,
        "stream": stream,
    }


async def test_missing_key_is_401(gateway_config, auth_config, lab_api_key) -> None:
    async with _client(gateway_config, auth_config, _mock_adapters()) as client:
        response = await client.post("/v1/chat/completions", json=_body())
    assert response.status_code == 401


async def test_wrong_key_is_401(gateway_config, auth_config) -> None:
    async with _client(gateway_config, auth_config, _mock_adapters()) as client:
        response = await client.post(
            "/v1/chat/completions", json=_body(), headers=_headers("plab_wrong")
        )
    assert response.status_code == 401


async def test_team_assertion_mismatch_is_403(gateway_config, auth_config, lab_api_key) -> None:
    async with _client(gateway_config, auth_config, _mock_adapters()) as client:
        response = await client.post(
            "/v1/chat/completions",
            json=_body(),
            headers=_headers(lab_api_key, **{"X-Prospera-Team": "another-team"}),
        )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "team_mismatch"


async def test_missing_metadata_is_422(gateway_config, auth_config, lab_api_key) -> None:
    async with _client(gateway_config, auth_config, _mock_adapters()) as client:
        response = await client.post(
            "/v1/chat/completions",
            json=_body(),
            headers={"Authorization": f"Bearer {lab_api_key}"},
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "missing_metadata"


async def test_invalid_metadata_is_422(gateway_config, auth_config, lab_api_key) -> None:
    async with _client(gateway_config, auth_config, _mock_adapters()) as client:
        response = await client.post(
            "/v1/chat/completions",
            json=_body(),
            headers=_headers(lab_api_key, data_class="secret"),
        )
    assert response.status_code == 422


async def test_unknown_workload_is_422(gateway_config, auth_config, lab_api_key) -> None:
    async with _client(gateway_config, auth_config, _mock_adapters()) as client:
        response = await client.post(
            "/v1/chat/completions",
            json=_body(),
            headers=_headers(lab_api_key, **{"X-Prospera-Workload": "not-allowed"}),
        )
    assert response.status_code == 422


async def test_happy_path_returns_openai_shape_and_route_headers(
    gateway_config, auth_config, lab_api_key
) -> None:
    async with _client(gateway_config, auth_config, _mock_adapters()) as client:
        response = await client.post(
            "/v1/chat/completions", json=_body(), headers=_headers(lab_api_key)
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["object"] == "chat.completion"
    assert payload["choices"][0]["message"]["content"] == "mock response"
    assert payload["usage"]["prompt_tokens"] == 4
    assert response.headers["X-Prospera-Provider"] == "private-vllm"
    assert response.headers["X-Prospera-Route"] == "economy-default"
    assert response.headers["X-Prospera-Request-Id"]


async def test_client_request_id_is_echoed(gateway_config, auth_config, lab_api_key) -> None:
    async with _client(gateway_config, auth_config, _mock_adapters()) as client:
        response = await client.post(
            "/v1/chat/completions",
            json=_body(),
            headers=_headers(lab_api_key, **{"X-Prospera-Request-Id": "client-id-1"}),
        )
    assert response.headers["X-Prospera-Request-Id"] == "client-id-1"


async def test_restricted_with_private_down_fails_closed(
    gateway_config, auth_config, lab_api_key
) -> None:
    """Spec §8.6: never fail open to a managed external provider."""
    economy = MockProviderAdapter(script=[MockBehaviorKind.OK])
    premium = MockProviderAdapter(script=[MockBehaviorKind.OK])
    adapters = _mock_adapters(
        private=MockProviderAdapter(MockBehaviorKind.SERVER_500),
        economy=economy,
        premium=premium,
    )
    async with _client(gateway_config, auth_config, adapters) as client:
        response = await client.post(
            "/v1/chat/completions",
            json=_body(),
            headers=_headers(lab_api_key, data_class="restricted", quality_tier="premium"),
        )
    assert response.status_code == 502
    assert len(economy._script) == 1 and len(premium._script) == 1


async def test_fallback_on_429_uses_next_provider(gateway_config, auth_config, lab_api_key) -> None:
    adapters = _mock_adapters(private=MockProviderAdapter(MockBehaviorKind.RATE_LIMITED_429))
    async with _client(gateway_config, auth_config, adapters) as client:
        response = await client.post(
            "/v1/chat/completions", json=_body(), headers=_headers(lab_api_key)
        )
    assert response.status_code == 200
    assert response.headers["X-Prospera-Provider"] == "managed-economy"


async def test_provider_429_maps_to_429_with_retry_after(
    gateway_config, auth_config, lab_api_key
) -> None:
    adapters = _mock_adapters(
        private=MockProviderAdapter(MockBehaviorKind.RATE_LIMITED_429),
        economy=MockProviderAdapter(MockBehaviorKind.RATE_LIMITED_429),
    )
    async with _client(gateway_config, auth_config, adapters) as client:
        response = await client.post(
            "/v1/chat/completions", json=_body(), headers=_headers(lab_api_key)
        )
    assert response.status_code == 429
    assert response.headers.get("Retry-After") == "1"


async def test_streaming_happy_path_emits_sse_and_done(
    gateway_config, auth_config, lab_api_key
) -> None:
    adapters = _mock_adapters(private=MockProviderAdapter(MockBehaviorKind.STREAM_OK))
    async with (
        _client(gateway_config, auth_config, adapters) as client,
        client.stream(
            "POST",
            "/v1/chat/completions",
            json=_body(stream=True),
            headers=_headers(lab_api_key),
        ) as response,
    ):
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        lines = [line async for line in response.aiter_lines() if line]
    assert lines[-1] == "data: [DONE]"
    events = [json.loads(line[len("data: ") :]) for line in lines[:-1]]
    texts = [
        event["choices"][0]["delta"].get("content", "") for event in events if event.get("choices")
    ]
    assert "".join(texts) == "mock response"
    final = events[-1]
    assert final["choices"][0]["finish_reason"] == "stop"
    assert final["usage"]["prompt_tokens"] == 4


async def test_stream_failure_after_start_surfaces_error_no_replay(
    gateway_config, auth_config, lab_api_key
) -> None:
    economy = MockProviderAdapter(script=[MockBehaviorKind.STREAM_OK])
    adapters = _mock_adapters(
        private=MockProviderAdapter(MockBehaviorKind.STREAM_FAIL_AFTER_FIRST_CHUNK),
        economy=economy,
    )
    async with (
        _client(gateway_config, auth_config, adapters) as client,
        client.stream(
            "POST",
            "/v1/chat/completions",
            json=_body(stream=True),
            headers=_headers(lab_api_key),
        ) as response,
    ):
        lines = [line async for line in response.aiter_lines() if line]
    assert not any(line == "data: [DONE]" for line in lines)
    last = json.loads(lines[-1][len("data: ") :])
    assert last["error"]["code"] == "stream_started_failure"
    assert len(economy._script) == 1  # no replay to another provider


async def test_metrics_endpoint_exposes_gateway_series(
    gateway_config, auth_config, lab_api_key
) -> None:
    async with _client(gateway_config, auth_config, _mock_adapters()) as client:
        await client.post("/v1/chat/completions", json=_body(), headers=_headers(lab_api_key))
        metrics = await client.get("/metrics")
    text = metrics.text
    assert "prospera_requests_total" in text
    assert 'outcome="success"' in text
    assert "prospera_routing_decisions_total" in text
    assert SENTINEL_PROMPT not in text


async def test_health_endpoints(gateway_config, auth_config) -> None:
    async with _client(gateway_config, auth_config, _mock_adapters()) as client:
        live = await client.get("/health/live")
        ready = await client.get("/health/ready")
        providers = await client.get("/health/providers")
    assert live.status_code == 200
    assert ready.status_code == 200
    assert set(ready.json()["providers"]) == {
        "private-vllm",
        "managed-economy",
        "managed-premium",
    }
    report = providers.json()["providers"]
    assert all(entry["healthy"] for entry in report.values())


async def test_prompt_bodies_never_reach_logs(
    gateway_config, auth_config, lab_api_key, caplog: pytest.LogCaptureFixture
) -> None:
    """Spec §15.3: grep the captured logs for the synthetic sentinel string."""
    with caplog.at_level(logging.DEBUG):
        async with _client(gateway_config, auth_config, _mock_adapters()) as client:
            await client.post("/v1/chat/completions", json=_body(), headers=_headers(lab_api_key))
    for record in caplog.records:
        assert SENTINEL_PROMPT not in record.getMessage()
        assert SENTINEL_PROMPT not in str(getattr(record, "args", ""))
    request_logs = [r for r in caplog.records if r.name == "prospera_gateway.access"]
    assert request_logs, "expected a structured access log record"
    assert getattr(request_logs[0], "outcome", None) == "success"
