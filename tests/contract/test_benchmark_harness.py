from __future__ import annotations

import json
from pathlib import Path

import httpx

from inference_gateway.adapters import MockBehaviorKind, MockProviderAdapter
from inference_gateway.api import create_app
from inference_gateway.benchmark.datasets import load_dataset
from inference_gateway.benchmark.harness import BenchmarkHarness
from inference_gateway.benchmark.local import local_adapters
from inference_gateway.benchmark.manifest import RepositoryState
from inference_gateway.benchmark.models import BenchmarkRecord, load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO_PATH = ROOT / "benchmark/scenarios/classification-local.yaml"


def _scenario(**updates):
    defaults = {
        "requests": 2,
        "warmup_requests": 1,
        "repeats": 1,
        "bootstrap_iterations": 100,
    }
    defaults.update(updates)
    return load_scenario(SCENARIO_PATH).model_copy(update=defaults)


def _harness(tmp_path: Path, client: httpx.AsyncClient) -> BenchmarkHarness:
    return BenchmarkHarness(
        repository_root=ROOT,
        scenario_path=SCENARIO_PATH,
        base_url="http://gateway.test",
        api_key="unused-by-fixture",
        client=client,
        results_root=tmp_path,
        repository_state=RepositoryState("a" * 40, True, "test"),
    )


def _records(run_dir: Path) -> list[BenchmarkRecord]:
    return [
        BenchmarkRecord.model_validate_json(line)
        for line in (run_dir / "records.jsonl").read_text().splitlines()
    ]


async def test_harness_happy_path_records_gateway_headers(
    tmp_path: Path, gateway_config, auth_config, lab_api_key: str
) -> None:
    dataset = load_dataset("benchmark/datasets/synthetic/classification-v1", root=ROOT)
    app = create_app(gateway_config, auth_config, adapters=local_adapters(dataset))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway.test"
    ) as client:
        harness = _harness(tmp_path, client)
        harness.api_key = lab_api_key
        run_dir = await harness.run(_scenario(stream=False))
    records = _records(run_dir)
    assert len(records) == 2
    assert all(record.provider == "private-vllm" for record in records)
    assert all(record.route == "balanced-default" for record in records)
    assert all(record.task_correct for record in records)
    assert all(
        record.usage.billed_input_tokens_source.value == "provider_reported" for record in records
    )


async def test_harness_provider_error_is_normalized_and_false_for_quality(
    tmp_path: Path, gateway_config, auth_config, lab_api_key: str
) -> None:
    adapters = {
        name: MockProviderAdapter(MockBehaviorKind.SERVER_500)
        for name in gateway_config.routing.providers
    }
    app = create_app(gateway_config, auth_config, adapters=adapters)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway.test"
    ) as client:
        harness = _harness(tmp_path, client)
        harness.api_key = lab_api_key
        run_dir = await harness.run(_scenario(stream=False, requests=1, warmup_requests=0))
    record = _records(run_dir)[0]
    assert record.http_status == 502
    assert record.error_class is not None
    assert record.error_class.value == "provider_5xx"
    assert record.task_correct is False
    assert record.provider == "managed-premium"
    assert record.fallback_count == 1


async def test_harness_streaming_captures_ttft(
    tmp_path: Path, gateway_config, auth_config, lab_api_key: str
) -> None:
    dataset = load_dataset("benchmark/datasets/synthetic/classification-v1", root=ROOT)
    app = create_app(gateway_config, auth_config, adapters=local_adapters(dataset))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway.test"
    ) as client:
        harness = _harness(tmp_path, client)
        harness.api_key = lab_api_key
        run_dir = await harness.run(_scenario(stream=True, requests=1, warmup_requests=0))
    record = _records(run_dir)[0]
    assert record.ttft_ms is not None and record.ttft_ms > 0
    assert record.task_correct is True


def test_record_json_matches_committed_schema_contract() -> None:
    schema = json.loads((ROOT / "results/schema/benchmark-record-v1.schema.json").read_text())
    model_fields = set(BenchmarkRecord.model_fields)
    assert set(schema["required"]) == model_fields
    assert set(schema["properties"]) == model_fields
    usage_fields = set(BenchmarkRecord.model_fields["usage"].annotation.model_fields)
    assert set(schema["properties"]["usage"]["required"]) == usage_fields
