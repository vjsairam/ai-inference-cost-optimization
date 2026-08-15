"""Ephemeral local-stack smoke check using the real wire adapters."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

import anthropic
import httpx
from fastapi import FastAPI

from inference_gateway.adapters import AnthropicManagedAdapter, OpenAICompatAdapter
from inference_gateway.adapters.base import ProviderAdapter
from inference_gateway.api import create_app
from inference_gateway.config import GatewayConfig, load_gateway_config
from inference_gateway.config.pricing import PricingEngine
from inference_gateway.faultmock import FaultMockConfig, FaultScenario, create_faultmock_app
from inference_gateway.security import generate_api_key, hash_api_key, load_auth_config


def _adapters(
    config: GatewayConfig, fault_app: FastAPI
) -> tuple[dict[str, ProviderAdapter], list[httpx.AsyncClient]]:
    pricing = PricingEngine(config.providers)
    transport = httpx.ASGITransport(app=fault_app)
    private_client = httpx.AsyncClient(transport=transport, base_url="http://faultmock.local")
    managed_http = httpx.AsyncClient(transport=transport, base_url="http://faultmock.local")
    managed_client = anthropic.AsyncAnthropic(
        api_key="local-faultmock",
        base_url="http://faultmock.local",
        http_client=managed_http,
        max_retries=0,
    )
    providers = config.providers.providers

    def managed(route: str, alias: str) -> AnthropicManagedAdapter:
        return AnthropicManagedAdapter(
            name=route,
            upstream_model=providers["managed-primary"].models[alias].upstream_model,
            model_alias=alias,
            client=managed_client,
            pricing=pricing,
            provider_config_name="managed-primary",
        )

    adapters: dict[str, ProviderAdapter] = {
        "private-vllm": OpenAICompatAdapter(
            name="private-vllm",
            upstream_model=providers["private-vllm"].models["lab-private"].upstream_model,
            client=private_client,
            api_key="local-faultmock",
        ),
        "managed-economy": managed("managed-economy", "lab-economy"),
        "managed-premium": managed("managed-premium", "lab-premium"),
    }
    return adapters, [private_client, managed_http]


def _headers(api_key: str, data_class: str, quality_tier: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "X-Gateway-Workload": "generic",
        "X-Gateway-Data-Class": data_class,
        "X-Gateway-Quality-Tier": quality_tier,
    }


async def _exercise(
    client: httpx.AsyncClient, api_key: str, transport_label: str
) -> dict[str, object]:
    body = {
        "model": "lab-default",
        "messages": [{"role": "user", "content": "local smoke"}],
        "max_tokens": 32,
    }
    auth_failure = await client.post(
        "/v1/chat/completions",
        json=body,
        headers=_headers("wrong-key", "internal", "economy"),
    )
    happy = await client.post(
        "/v1/chat/completions",
        json=body,
        headers=_headers(api_key, "internal", "economy"),
    )
    stream = await client.post(
        "/v1/chat/completions",
        json={**body, "stream": True},
        headers=_headers(api_key, "internal", "economy"),
    )
    restricted = await client.post(
        "/v1/chat/completions",
        json=body,
        headers=_headers(api_key, "restricted", "premium"),
    )
    metrics = await client.get("/metrics")
    checks = {
        "auth": auth_failure.status_code,
        "non_stream": happy.status_code,
        "stream": stream.status_code,
        "restricted": restricted.status_code,
        "restricted_provider": restricted.headers.get("X-Gateway-Provider"),
        "metrics": "gateway_requests_total" in metrics.text,
    }
    if checks != {
        "auth": 401,
        "non_stream": 200,
        "stream": 200,
        "restricted": 200,
        "restricted_provider": "private-vllm",
        "metrics": True,
    }:
        raise RuntimeError(f"local smoke failed: {checks}")
    if happy.json()["choices"][0]["message"]["content"] != "fault mock completion":
        raise RuntimeError("local smoke non-stream payload mismatch")
    if "fault mock" not in stream.text or "data: [DONE]" not in stream.text:
        raise RuntimeError("local smoke stream payload mismatch")
    return {"transport": transport_label, **checks}


async def _stop(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=2)
    except TimeoutError:
        process.kill()
        await process.wait()


async def _tcp_smoke(
    repository_root: Path, auth_path: Path, api_key: str
) -> dict[str, object] | None:
    seed = int(uuid.uuid4().hex[:4], 16)
    gateway_port = 20_000 + seed % 10_000
    faultmock_port = 30_000 + seed % 10_000
    stack_env = {
        **os.environ,
        "GATEWAY_PROVIDERS_CONFIG": "config/local/providers.yaml",
        "GATEWAY_ROUTING_CONFIG": "config/local/routing.yaml",
        "GATEWAY_AUTH_CONFIG": str(auth_path),
        "FAULTMOCK_CONFIG": "config/local/fault-sequence.yaml",
        "PRIVATE_VLLM_BASE_URL": f"http://127.0.0.1:{faultmock_port}",
        "PRIVATE_VLLM_API_KEY": "local-faultmock",
        "MANAGED_PRIMARY_BASE_URL": f"http://127.0.0.1:{faultmock_port}",
        "MANAGED_PRIMARY_API_KEY": "local-faultmock",
    }
    fault_process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "inference_gateway.faultmock",
        "--port",
        str(faultmock_port),
        cwd=repository_root,
        env=stack_env,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    gateway_process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "uvicorn",
        "--factory",
        "inference_gateway.main:build_app",
        "--host",
        "127.0.0.1",
        "--port",
        str(gateway_port),
        cwd=repository_root,
        env=stack_env,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{gateway_port}", timeout=2
        ) as client:
            ready = False
            for _ in range(120):
                if fault_process.returncode is not None or gateway_process.returncode is not None:
                    break
                try:
                    gateway_ready = await client.get("/health/ready")
                    async with httpx.AsyncClient(timeout=0.1) as probe:
                        fault_ready = await probe.get(
                            f"http://127.0.0.1:{faultmock_port}/health/live"
                        )
                    ready = gateway_ready.status_code == 200 and fault_ready.status_code == 200
                except httpx.HTTPError:
                    ready = False
                if ready:
                    break
                await asyncio.sleep(0.25)
            if not ready:
                return None
            return await _exercise(client, api_key, "loopback HTTP with two uvicorn processes")
    finally:
        await _stop(gateway_process)
        await _stop(fault_process)


async def run_smoke(repository_root: Path) -> dict[str, object]:
    config = load_gateway_config(
        repository_root / "config/local/providers.yaml",
        repository_root / "config/local/routing.yaml",
    )
    api_key = generate_api_key()
    with tempfile.TemporaryDirectory(prefix="gateway-smoke-") as directory:
        auth_path = Path(directory) / "auth.yaml"
        auth_path.write_text(
            f'keys:\n  - sha256: "{hash_api_key(api_key)}"\n    team: smoke\n',
            encoding="utf-8",
        )
        tcp_result = await _tcp_smoke(repository_root, auth_path, api_key)
        if tcp_result is not None:
            return tcp_result
        fault_app = create_faultmock_app(
            FaultMockConfig(sequence=[FaultScenario.OK, FaultScenario.STREAM_OK, FaultScenario.OK])
        )
        adapters, upstream_clients = _adapters(config, fault_app)
        app = create_app(config, load_auth_config(auth_path), adapters=adapters)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://gateway.local"
        ) as client:
            result = await _exercise(
                client,
                api_key,
                "in-process fallback: TCP stack did not become ready",
            )
        for upstream in upstream_clients:
            await upstream.aclose()
        return result


def main() -> None:
    result = asyncio.run(run_smoke(Path.cwd().resolve()))
    print(f"local-smoke: {json.dumps(result, sort_keys=True)}")


if __name__ == "__main__":
    main()
