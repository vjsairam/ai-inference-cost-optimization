"""Generate local M3 fault evidence through the gateway's real adapters."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import anthropic
import anthropic._base_client
import httpx
from fastapi import FastAPI
from prometheus_client.parser import text_string_to_metric_families

from inference_gateway.adapters import AnthropicManagedAdapter, OpenAICompatAdapter
from inference_gateway.adapters.base import ProviderAdapter
from inference_gateway.api import create_app
from inference_gateway.config import GatewayConfig, TimeoutConfig, load_gateway_config
from inference_gateway.config.pricing import PricingEngine
from inference_gateway.faultmock import FaultMockConfig, FaultScenario, create_faultmock_app
from inference_gateway.security import ApiKeyEntry, AuthConfig, generate_api_key, hash_api_key

_COUNTERS = {
    "gateway_requests_total",
    "gateway_fallback_total",
    "gateway_provider_errors_total",
    "gateway_policy_denied_total",
}


@dataclass(frozen=True, slots=True)
class FaultCase:
    fault: FaultScenario
    wire_format: str
    stream: bool
    data_class: str
    quality_tier: str
    expected_status: int
    expected_provider: str
    expected_fallbacks: int
    expected_error: str | None


@dataclass(frozen=True, slots=True)
class FaultService:
    base_url: str
    app: FastAPI | None
    transport_label: str

    def client(self, **kwargs: Any) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=self.app) if self.app is not None else None
        return httpx.AsyncClient(
            transport=transport,
            base_url=self.base_url,
            **kwargs,
        )


_CASES = (
    FaultCase(
        FaultScenario.RATE_LIMITED,
        "anthropic",
        False,
        "public",
        "premium",
        200,
        "private-vllm",
        1,
        None,
    ),
    FaultCase(
        FaultScenario.SERVER_ERROR,
        "anthropic",
        False,
        "public",
        "premium",
        200,
        "private-vllm",
        1,
        None,
    ),
    FaultCase(
        FaultScenario.TIMEOUT,
        "anthropic",
        False,
        "public",
        "premium",
        200,
        "private-vllm",
        1,
        None,
    ),
    FaultCase(
        FaultScenario.MALFORMED_JSON,
        "openai-compatible",
        False,
        "internal",
        "economy",
        502,
        "private-vllm",
        0,
        "malformed_response",
    ),
    FaultCase(
        FaultScenario.STREAM_FAIL_AFTER_FIRST_CHUNK,
        "openai-compatible",
        True,
        "internal",
        "economy",
        200,
        "private-vllm",
        0,
        "stream_started_failure",
    ),
    FaultCase(
        FaultScenario.IN_BAND_ERROR,
        "anthropic",
        True,
        "public",
        "premium",
        200,
        "private-vllm",
        1,
        None,
    ),
)


def _short_timeout_config(config: GatewayConfig) -> GatewayConfig:
    timeouts = TimeoutConfig(
        connect_timeout=0.1,
        response_header_timeout=0.15,
        stream_idle_timeout=0.15,
        per_attempt_timeout=0.2,
        global_request_deadline=1.6,
    )
    routing = config.routing.model_copy(update={"timeouts": timeouts})
    return config.model_copy(update={"routing": routing})


def _real_adapters(
    config: GatewayConfig,
    service: FaultService,
    case: FaultCase,
) -> tuple[dict[str, ProviderAdapter], httpx.AsyncClient, anthropic.AsyncAnthropic]:
    private_scenario = (
        case.fault.value if case.wire_format == "openai-compatible" else FaultScenario.OK.value
    )
    managed_scenario = (
        case.fault.value if case.wire_format == "anthropic" else FaultScenario.OK.value
    )
    private_client = service.client(
        headers={"X-Fault-Scenario": private_scenario},
        timeout=2,
    )
    managed_http = service.client(
        headers={"X-Fault-Scenario": managed_scenario},
        timeout=2,
    )
    managed_client = anthropic.AsyncAnthropic(
        api_key="local-faultmock",
        base_url=service.base_url,
        http_client=managed_http,
        max_retries=0,
    )
    providers = config.providers.providers
    pricing = PricingEngine(config.providers)

    def managed(route: str, alias: str) -> AnthropicManagedAdapter:
        return AnthropicManagedAdapter(
            name=route,
            upstream_model=providers["managed-primary"].models[alias].upstream_model,
            model_alias=alias,
            client=managed_client,
            pricing=pricing,
            provider_config_name="managed-primary",
        )

    return (
        {
            "private-vllm": OpenAICompatAdapter(
                name="private-vllm",
                upstream_model=providers["private-vllm"].models["lab-private"].upstream_model,
                client=private_client,
                api_key="local-faultmock",
            ),
            "managed-economy": managed("managed-economy", "lab-economy"),
            "managed-premium": managed("managed-premium", "lab-premium"),
        },
        private_client,
        managed_client,
    )


def _counter_snapshot(text: str) -> dict[tuple[str, tuple[tuple[str, str], ...]], float]:
    snapshot: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
    for family in text_string_to_metric_families(text):
        for sample in family.samples:
            if sample.name in _COUNTERS:
                snapshot[(sample.name, tuple(sorted(sample.labels.items())))] = float(sample.value)
    return snapshot


def _metric_delta(
    before: dict[tuple[str, tuple[tuple[str, str], ...]], float],
    after: dict[tuple[str, tuple[tuple[str, str], ...]], float],
) -> list[dict[str, object]]:
    rows = []
    for (name, labels), value in sorted(after.items()):
        delta = value - before.get((name, labels), 0.0)
        if delta > 0:
            rows.append({"metric": name, "labels": dict(labels), "delta": delta})
    return rows


def _stream_observation(text: str) -> tuple[str, str | None, bool]:
    content: list[str] = []
    error: str | None = None
    saw_done = False
    for line in text.splitlines():
        if not line.startswith("data: "):
            continue
        data = line.removeprefix("data: ")
        if data == "[DONE]":
            saw_done = True
            continue
        event = json.loads(data)
        if isinstance(event.get("error"), dict):
            error = str(event["error"].get("code"))
        choices = event.get("choices", [])
        if choices:
            chunk = choices[0].get("delta", {}).get("content")
            if chunk:
                content.append(str(chunk))
    return "".join(content), error, saw_done


def _has_delta(deltas: list[dict[str, object]], metric: str, **labels: str) -> bool:
    return any(
        row["metric"] == metric
        and isinstance(row["labels"], dict)
        and all(row["labels"].get(key) == value for key, value in labels.items())
        for row in deltas
    )


async def _run_case(
    config: GatewayConfig, api_key: str, case: FaultCase, service: FaultService
) -> dict[str, object]:
    async with service.client(timeout=2) as diagnostic:
        reset = await diagnostic.post("/__faultmock/reset")
        reset.raise_for_status()
    adapters, private_client, managed_client = _real_adapters(config, service, case)
    auth = AuthConfig(keys=[ApiKeyEntry(sha256=hash_api_key(api_key), team="fault-evidence")])
    gateway = create_app(config, auth, adapters=adapters)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "X-Gateway-Workload": "generic",
        "X-Gateway-Data-Class": case.data_class,
        "X-Gateway-Quality-Tier": case.quality_tier,
    }
    body = {
        "model": "lab-default",
        "messages": [{"role": "user", "content": "fault evidence"}],
        "max_tokens": 32,
        "stream": case.stream,
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway), base_url="http://gateway.local"
    ) as client:
        before = _counter_snapshot((await client.get("/metrics")).text)
        started = time.perf_counter()
        response = await client.post("/v1/chat/completions", json=body, headers=headers)
        elapsed_ms = (time.perf_counter() - started) * 1000
        after = _counter_snapshot((await client.get("/metrics")).text)
    async with service.client(timeout=2) as diagnostic:
        upstream_counts = (await diagnostic.get("/__faultmock/state")).json()["counts"]
    await private_client.aclose()
    await managed_client.close()

    partial_content = ""
    stream_error: str | None = None
    saw_done = False
    error_code: str | None = None
    if case.stream:
        partial_content, stream_error, saw_done = _stream_observation(response.text)
        error_code = stream_error
    elif response.status_code >= 300:
        error_code = str(response.json()["error"]["code"])
    deltas = _metric_delta(before, after)
    observed = {
        "http_status": response.status_code,
        "provider": response.headers.get("X-Gateway-Provider"),
        "fallback_count": int(response.headers.get("X-Gateway-Fallback-Count", "0")),
        "error_code": error_code,
        "partial_content": partial_content,
        "saw_done": saw_done,
        "elapsed_ms": round(elapsed_ms, 3),
        "upstream_scenario_counts": upstream_counts,
    }
    passed = (
        observed["http_status"] == case.expected_status
        and observed["provider"] == case.expected_provider
        and observed["fallback_count"] == case.expected_fallbacks
        and observed["error_code"] == case.expected_error
    )
    expected_failure_metric = {
        FaultScenario.RATE_LIMITED: ("managed-premium", "rate_limited"),
        FaultScenario.SERVER_ERROR: ("managed-premium", "provider_5xx"),
        FaultScenario.TIMEOUT: ("managed-premium", "timeout"),
        FaultScenario.MALFORMED_JSON: ("private-vllm", "malformed_response"),
        FaultScenario.STREAM_FAIL_AFTER_FIRST_CHUNK: (
            "private-vllm",
            "malformed_response",
        ),
        FaultScenario.IN_BAND_ERROR: ("managed-premium", "provider_5xx"),
    }[case.fault]
    passed = passed and _has_delta(
        deltas,
        "gateway_provider_errors_total",
        provider=expected_failure_metric[0],
        error_class=expected_failure_metric[1],
    )
    fallback_delta = _has_delta(deltas, "gateway_fallback_total")
    passed = passed and (fallback_delta == (case.expected_fallbacks == 1))
    if case.fault is FaultScenario.TIMEOUT:
        passed = passed and elapsed_ms < 600
    if case.fault is FaultScenario.STREAM_FAIL_AFTER_FIRST_CHUNK:
        passed = (
            passed
            and partial_content == "fault mock"
            and not saw_done
            and upstream_counts == {case.fault.value: 1}
        )
    if case.fault is FaultScenario.IN_BAND_ERROR:
        passed = passed and upstream_counts == {case.fault.value: 1, FaultScenario.OK.value: 1}
    return {
        "fault": case.fault.value,
        "wire_format": case.wire_format,
        "request": {
            "stream": case.stream,
            "data_class": case.data_class,
            "quality_tier": case.quality_tier,
        },
        "expected": {
            "http_status": case.expected_status,
            "provider": case.expected_provider,
            "fallback_count": case.expected_fallbacks,
            "error_code": case.expected_error,
        },
        "observed": observed,
        "metric_deltas": deltas,
        "passed": passed,
    }


@asynccontextmanager
async def _fault_service(repository_root: Path) -> Any:
    """Prefer a loopback uvicorn process; retain a sandbox-safe HTTP fallback."""
    port = 20_000 + int(uuid.uuid4().hex[:4], 16) % 20_000
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "inference_gateway.faultmock",
        "--port",
        str(port),
        cwd=repository_root,
        env={**os.environ, "FAULTMOCK_CONFIG": "config/local/fault-sequence.yaml"},
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    ready = False
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=0.1) as client:
            for _ in range(20):
                if process.returncode is not None:
                    break
                try:
                    response = await client.get("/health/live")
                    ready = response.status_code == 200
                except httpx.HTTPError:
                    ready = False
                if ready:
                    break
                await asyncio.sleep(0.05)
        if ready:
            yield FaultService(base_url, None, "loopback HTTP to a uvicorn fault service")
        else:
            app = create_faultmock_app(
                FaultMockConfig(timeout_seconds=2, sequence=[FaultScenario.OK])
            )
            yield FaultService(
                "http://faultmock.local",
                app,
                "HTTP ASGI fallback because loopback sockets are unavailable",
            )
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except TimeoutError:
                process.kill()
                await process.wait()


async def generate_fault_evidence(
    repository_root: Path,
    *,
    results_root: Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    # The local Python 3.13 runtime cannot schedule the SDK's one-time platform
    # probe in an executor. Run that pure metadata lookup inline for this local
    # command, matching the repository test harness.
    def inline_asyncify(function: Any) -> Any:
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            return function(*args, **kwargs)

        return wrapper

    base_client = cast(Any, anthropic._base_client)
    base_client.asyncify = inline_asyncify
    config = _short_timeout_config(
        load_gateway_config(
            repository_root / "config/local/providers.yaml",
            repository_root / "config/local/routing.yaml",
        )
    )
    api_key = generate_api_key()
    async with _fault_service(repository_root) as service:
        scenarios = [await _run_case(config, api_key, case, service) for case in _CASES]
    if not all(case["passed"] for case in scenarios):
        failed = [case["fault"] for case in scenarios if not case["passed"]]
        raise RuntimeError(f"fault evidence expectations failed: {failed}")
    generated_at = datetime.now(UTC)
    run_id = f"{generated_at.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    output_root = results_root or repository_root / "results/local"
    run_dir = output_root / f"{run_id}-fault"
    run_dir.mkdir(parents=True, exist_ok=False)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "evidence_kind": "local mock fault evidence",
        "run_id": run_id,
        "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
        "transport": service.transport_label,
        "publishable_performance_evidence": False,
        "headline": {
            "faults_injected": len(scenarios),
            "expectations_passed": sum(bool(case["passed"]) for case in scenarios),
            "fallback_faults": [
                FaultScenario.RATE_LIMITED.value,
                FaultScenario.SERVER_ERROR.value,
                FaultScenario.TIMEOUT.value,
                FaultScenario.IN_BAND_ERROR.value,
            ],
            "non_replayed_stream_failures": 1,
        },
        "scenarios": scenarios,
    }
    (run_dir / "fault-evidence.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return run_dir, payload


def main() -> None:
    run_dir, payload = asyncio.run(generate_fault_evidence(Path.cwd().resolve()))
    print(
        json.dumps(
            {
                "run_dir": str(run_dir),
                **payload["headline"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
