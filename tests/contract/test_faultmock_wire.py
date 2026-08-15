"""Wire contracts exposed by the deterministic fault service."""

from __future__ import annotations

import json

import httpx

from inference_gateway.faultmock import create_faultmock_app


async def test_openai_non_stream_and_final_stream_usage() -> None:
    app = create_faultmock_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://faultmock.test"
    ) as client:
        response = await client.post(
            "/v1/chat/completions", json={"model": "m", "messages": [], "stream": False}
        )
        stream = await client.post(
            "/v1/chat/completions",
            json={"model": "m", "messages": [], "stream": True},
            headers={"X-Fault-Scenario": "stream_ok"},
        )
    assert response.status_code == 200
    assert response.json()["usage"] == {
        "prompt_tokens": 7,
        "completion_tokens": 3,
        "total_tokens": 10,
    }
    events = [
        json.loads(line.removeprefix("data: "))
        for line in stream.text.splitlines()
        if line.startswith("data: {")
    ]
    assert events[-1]["usage"]["total_tokens"] == 10
    assert "data: [DONE]" in stream.text


async def test_anthropic_stream_reports_start_and_delta_usage() -> None:
    app = create_faultmock_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://faultmock.test"
    ) as client:
        response = await client.post(
            "/v1/messages",
            json={"model": "m", "messages": [], "stream": True},
            headers={"X-Fault-Scenario": "stream_ok"},
        )
        health = await client.get("/v1/models/m")
    assert response.status_code == 200
    assert '"input_tokens": 7' in response.text
    assert '"output_tokens": 3' in response.text
    assert "event: message_start" in response.text
    assert "event: message_delta" in response.text
    assert health.status_code == 200


async def test_fault_headers_select_status_and_in_band_error() -> None:
    app = create_faultmock_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://faultmock.test"
    ) as client:
        limited = await client.post(
            "/v1/messages",
            json={"model": "m", "messages": []},
            headers={"X-Fault-Scenario": "rate_limited_429"},
        )
        overloaded = await client.post(
            "/v1/messages",
            json={"model": "m", "messages": [], "stream": True},
            headers={"X-Fault-Scenario": "in_band_error"},
        )
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "1"
    assert overloaded.status_code == 200
    assert "overloaded_error" in overloaded.text
