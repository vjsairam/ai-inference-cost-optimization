"""Async benchmark runner for streaming and non-streaming gateway calls."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from inference_gateway.benchmark.datasets import DatasetBundle, DatasetItem, load_dataset
from inference_gateway.benchmark.evaluators import evaluate_classification, evaluate_extraction
from inference_gateway.benchmark.manifest import (
    RepositoryState,
    build_manifest,
    complete_manifest,
    write_manifest,
)
from inference_gateway.benchmark.models import (
    BenchmarkRecord,
    BenchmarkScenario,
    TokenUsageRecord,
)
from inference_gateway.benchmark.slo import load_slo
from inference_gateway.config import load_gateway_config
from inference_gateway.config.pricing import PricingEngine
from inference_gateway.models import (
    DataClass,
    ErrorClass,
    NormalizedUsage,
    QualityTier,
    UsageSource,
)


class BenchmarkRunError(RuntimeError):
    """The load run cannot produce valid evidence."""


def _error_class(status: int, payload: dict[str, Any] | None) -> ErrorClass | None:
    if 200 <= status < 300:
        return None
    code = None
    if payload and isinstance(payload.get("error"), dict):
        code = payload["error"].get("code")
    try:
        return ErrorClass(str(code))
    except (TypeError, ValueError):
        if status == 429:
            return ErrorClass.RATE_LIMITED
        if status == 504:
            return ErrorClass.TIMEOUT
        if status >= 500:
            return ErrorClass.PROVIDER_5XX
        return ErrorClass.INVALID_REQUEST


def _quality(
    workload: str, output: str, target: str | dict[str, object] | None
) -> tuple[bool | None, dict[str, Any] | None]:
    if workload == "generation":
        return None, None
    if workload == "classification" and isinstance(target, str):
        result = evaluate_classification(output, target)
    elif workload == "structured-extraction" and isinstance(target, dict):
        result = evaluate_extraction(output, target)
    else:
        raise BenchmarkRunError("dataset target does not match workload evaluator")
    return result.task_correct, result.score


async def _stream_text(
    response: httpx.Response,
) -> tuple[str, dict[str, int], str | None, float | None]:
    output: list[str] = []
    usage: dict[str, int] = {}
    stream_error: str | None = None
    first_content_at: float | None = None
    async for line in response.aiter_lines():
        if not line.startswith("data: "):
            continue
        data = line.removeprefix("data: ")
        if data == "[DONE]":
            continue
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            stream_error = ErrorClass.MALFORMED_RESPONSE.value
            continue
        if "error" in event:
            stream_error = str(event["error"].get("code", ErrorClass.MALFORMED_RESPONSE.value))
            continue
        choices = event.get("choices", [])
        if choices:
            text = choices[0].get("delta", {}).get("content", "")
            if text:
                if first_content_at is None:
                    first_content_at = time.perf_counter()
                output.append(str(text))
        if isinstance(event.get("usage"), dict):
            usage = {key: int(value) for key, value in event["usage"].items()}
    return "".join(output), usage, stream_error, first_content_at


class BenchmarkHarness:
    def __init__(
        self,
        *,
        repository_root: Path,
        scenario_path: Path,
        base_url: str,
        api_key: str,
        client: httpx.AsyncClient | None = None,
        results_root: Path | None = None,
        repository_state: RepositoryState | None = None,
        allow_dirty: bool = False,
    ) -> None:
        self.repository_root = repository_root
        self.scenario_path = scenario_path
        self.base_url = base_url
        self.api_key = api_key
        self.client = client
        self.results_root = results_root or repository_root / "results" / "local"
        self.repository_state = repository_state or RepositoryState.detect(repository_root)
        self.allow_dirty = allow_dirty

    async def run(self, scenario: BenchmarkScenario) -> Path:
        dataset = load_dataset(scenario.dataset, root=self.repository_root)
        slo_document, slo_hash = load_slo(self.repository_root / scenario.slo_config)
        manifest = build_manifest(
            repository_root=self.repository_root,
            scenario_path=self.scenario_path,
            scenario=scenario,
            dataset=dataset,
            slo_document=slo_document,
            slo_hash=slo_hash,
            repository=self.repository_state,
            allow_dirty=self.allow_dirty,
        )
        run_dir = self.results_root / str(manifest["run_id"])
        run_id = str(manifest["run_id"])
        manifest_path = run_dir / "manifest.yaml"
        write_manifest(manifest_path, manifest)
        records_path = run_dir / "records.jsonl"
        selected = self._select_items(dataset, scenario)
        owns_client = self.client is None
        client = self.client or httpx.AsyncClient(base_url=self.base_url, timeout=120)
        records: list[BenchmarkRecord] = []
        try:
            for repeat_index in range(1, scenario.repeats + 1):
                for warmup_index in range(scenario.warmup_requests):
                    item = selected[warmup_index % len(selected)]
                    await self._request(
                        client,
                        scenario,
                        item,
                        repeat_index,
                        run_id,
                        request_index=warmup_index,
                        measured=False,
                    )
                semaphore = asyncio.Semaphore(scenario.concurrency)
                records.extend(
                    await asyncio.gather(
                        *(
                            self._bounded_request(
                                semaphore,
                                client,
                                scenario,
                                item,
                                repeat_index,
                                run_id,
                                request_index,
                            )
                            for request_index, item in enumerate(selected)
                        )
                    )
                )
        finally:
            if owns_client:
                await client.aclose()
        records_path.write_text(
            "".join(record.json_line() + "\n" for record in records), encoding="utf-8"
        )
        complete_manifest(manifest_path, datetime.now(UTC), len(records))
        return run_dir

    @staticmethod
    def _select_items(
        dataset: DatasetBundle, scenario: BenchmarkScenario
    ) -> tuple[DatasetItem, ...]:
        if scenario.requests > len(dataset.items):
            raise BenchmarkRunError(
                f"scenario requests {scenario.requests} exceed dataset size {len(dataset.items)}"
            )
        import random

        rng = random.Random(scenario.seed)
        return tuple(rng.sample(dataset.items, scenario.requests))

    async def _bounded_request(
        self,
        semaphore: asyncio.Semaphore,
        client: httpx.AsyncClient,
        scenario: BenchmarkScenario,
        item: DatasetItem,
        repeat_index: int,
        run_id: str,
        request_index: int,
    ) -> BenchmarkRecord:
        async with semaphore:
            record = await self._request(
                client,
                scenario,
                item,
                repeat_index,
                run_id,
                request_index=request_index,
                measured=True,
            )
        if record is None:
            raise BenchmarkRunError("measured request did not produce a record")
        return record

    async def _request(
        self,
        client: httpx.AsyncClient,
        scenario: BenchmarkScenario,
        item: DatasetItem,
        repeat_index: int,
        run_id: str,
        request_index: int,
        *,
        measured: bool,
    ) -> BenchmarkRecord | None:
        request_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{scenario.id}:{repeat_index}:{item.item_id}:{'measured' if measured else 'warmup'}",
        ).hex
        data_class, quality_tier = self._request_profile(scenario, request_index)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "X-Gateway-Request-Id": request_id,
            "X-Gateway-Workload": scenario.workload,
            "X-Gateway-Data-Class": data_class.value,
            "X-Gateway-Quality-Tier": quality_tier.value,
        }
        body = {
            "model": scenario.model,
            "messages": [
                {
                    "role": "user",
                    "content": f"[dataset_item_id={item.item_id}]\n{item.prompt}",
                }
            ],
            "temperature": scenario.temperature,
            "max_tokens": scenario.max_tokens,
            "stream": scenario.stream,
        }
        started_at = datetime.now(UTC)
        dispatch = time.perf_counter()
        output = ""
        usage: dict[str, int] = {}
        payload: dict[str, Any] | None = None
        stream_error: str | None = None
        ttft_ms: float | None = None
        if scenario.stream:
            async with client.stream(
                "POST", "/v1/chat/completions", json=body, headers=headers
            ) as response:
                output, usage, stream_error, first_content_at = await _stream_text(response)
                if first_content_at is not None:
                    ttft_ms = (first_content_at - dispatch) * 1000
                status = response.status_code
                response_headers = response.headers
                if status >= 300:
                    try:
                        payload = json.loads(output)
                    except json.JSONDecodeError:
                        payload = None
        else:
            response = await client.post("/v1/chat/completions", json=body, headers=headers)
            status = response.status_code
            response_headers = response.headers
            try:
                payload = response.json()
            except json.JSONDecodeError:
                payload = None
            if status < 300 and payload:
                output = str(payload["choices"][0]["message"]["content"])
                if isinstance(payload.get("usage"), dict):
                    usage = {key: int(value) for key, value in payload["usage"].items()}
        completed_at = datetime.now(UTC)
        if not measured:
            return None
        error = _error_class(status, payload)
        if stream_error is not None:
            try:
                error = ErrorClass(stream_error)
            except ValueError:
                error = ErrorClass.MALFORMED_RESPONSE
        task_correct: bool | None
        score: dict[str, Any] | None
        if error is not None:
            task_correct = None if scenario.workload == "generation" else False
            score = None
        else:
            task_correct, score = _quality(scenario.workload, output, item.target)
        provider = response_headers.get("X-Gateway-Provider")
        model_alias = str(payload.get("model", scenario.model)) if payload else scenario.model
        usage_record = self._usage_record(usage)
        cost = self._managed_cost(provider, model_alias, usage_record, scenario)
        return BenchmarkRecord(
            run_id=run_id,
            repeat_index=repeat_index,
            request_id=request_id,
            workload=scenario.workload,
            dataset_item_id=item.item_id,
            difficulty=item.difficulty,
            data_class=data_class,
            quality_tier=quality_tier,
            route=response_headers.get("X-Gateway-Route"),
            provider=provider,
            model_alias=model_alias,
            started_at=started_at,
            completed_at=completed_at,
            ttft_ms=ttft_ms,
            e2e_ms=(time.perf_counter() - dispatch) * 1000,
            usage=usage_record,
            http_status=status,
            error_class=error,
            fallback_count=int(response_headers.get("X-Gateway-Fallback-Count", "0")),
            task_correct=task_correct,
            quality_score=score,
            managed_inference_cost_usd=cost,
        )

    @staticmethod
    def _request_profile(
        scenario: BenchmarkScenario, request_index: int
    ) -> tuple[DataClass, QualityTier]:
        if not scenario.request_mix:
            return scenario.data_class, scenario.quality_tier
        slot = request_index % sum(profile.weight for profile in scenario.request_mix)
        for profile in scenario.request_mix:
            if slot < profile.weight:
                return profile.data_class, profile.quality_tier
            slot -= profile.weight
        raise AssertionError("weighted request profile selection exhausted")

    @staticmethod
    def _usage_record(usage: dict[str, int]) -> TokenUsageRecord:
        available = UsageSource.PROVIDER_REPORTED if usage else UsageSource.UNAVAILABLE
        return TokenUsageRecord(
            input_tokens=usage.get("prompt_tokens"),
            input_tokens_source=available,
            visible_output_tokens=usage.get("completion_tokens"),
            visible_output_tokens_source=available,
            billed_input_tokens=usage.get("prompt_tokens"),
            billed_input_tokens_source=available,
            billed_output_tokens=usage.get("completion_tokens"),
            billed_output_tokens_source=available,
            reasoning_or_special_tokens=None,
        )

    def _managed_cost(
        self,
        provider: str | None,
        model_alias: str,
        usage: TokenUsageRecord,
        scenario: BenchmarkScenario,
    ) -> Decimal | None:
        if provider is None or not provider.startswith("managed"):
            return None
        gateway_config = load_gateway_config(
            self.repository_root / scenario.pricing_config,
            self.repository_root / scenario.timeout_config,
        )
        route = gateway_config.routing.providers.get(provider)
        provider_name = (route.provider if route else None) or provider
        price_alias = (route.model_alias if route else None) or model_alias
        normalized = NormalizedUsage(
            visible_output_tokens=usage.visible_output_tokens,
            visible_output_tokens_source=usage.visible_output_tokens_source,
            billed_input_tokens=usage.billed_input_tokens,
            billed_input_tokens_source=usage.billed_input_tokens_source,
            billed_output_tokens=usage.billed_output_tokens,
            billed_output_tokens_source=usage.billed_output_tokens_source,
        )
        money = PricingEngine(gateway_config.providers).price(
            provider_name, price_alias, normalized
        )
        return money.amount if money is not None else None
