"""In-process gateway wiring for local benchmark evidence."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from inference_gateway.adapters.base import ProviderAdapter
from inference_gateway.benchmark.datasets import DatasetBundle
from inference_gateway.models import (
    CanonicalChatRequest,
    CanonicalContentPart,
    Money,
    NormalizedUsage,
    ProviderCapabilities,
    ProviderChunk,
    ProviderHealth,
    ProviderResult,
    RequestContext,
    UsageSource,
)

_ITEM_PATTERN = re.compile(r"\[dataset_item_id=([^\]]+)\]")


class DatasetMockAdapter:
    """Returns frozen targets by item ID; intended only for local plumbing checks."""

    capabilities = ProviderCapabilities(
        streaming=True,
        reports_usage=True,
        reports_streaming_usage=True,
    )

    def __init__(self, dataset: DatasetBundle, name: str = "private-vllm") -> None:
        self.name = name
        self._answers = {item.item_id: item.target for item in dataset.items}

    def _answer(self, request: CanonicalChatRequest) -> str:
        prompt = "".join(
            part.text or "" for message in request.messages for part in message.content
        )
        match = _ITEM_PATTERN.search(prompt)
        if match is None:
            return "local mock completion"
        answer = self._answers.get(match.group(1))
        if isinstance(answer, dict):
            return json.dumps(answer, sort_keys=True, separators=(",", ":"))
        if isinstance(answer, str):
            return answer
        return "local mock completion"

    @staticmethod
    def _usage(request: CanonicalChatRequest, answer: str) -> NormalizedUsage:
        input_tokens = sum(
            len((part.text or "").split())
            for message in request.messages
            for part in message.content
        )
        output_tokens = len(answer.split())
        return NormalizedUsage(
            visible_output_tokens=output_tokens,
            visible_output_tokens_source=UsageSource.PROVIDER_REPORTED,
            billed_input_tokens=input_tokens,
            billed_input_tokens_source=UsageSource.PROVIDER_REPORTED,
            billed_output_tokens=output_tokens,
            billed_output_tokens_source=UsageSource.PROVIDER_REPORTED,
            reasoning_or_special_tokens=0,
            reasoning_or_special_tokens_source=UsageSource.PROVIDER_REPORTED,
        )

    async def chat(self, request: CanonicalChatRequest, ctx: RequestContext) -> ProviderResult:
        del ctx
        answer = self._answer(request)
        await asyncio.sleep(0)
        return ProviderResult(
            provider=self.name,
            model=request.model,
            output=[CanonicalContentPart(type="text", text=answer)],
            finish_reason="stop",
            usage=self._usage(request, answer),
        )

    async def stream(
        self, request: CanonicalChatRequest, ctx: RequestContext
    ) -> AsyncIterator[ProviderChunk]:
        del ctx
        answer = self._answer(request)
        split = max(1, len(answer) // 2)
        for sequence, text in enumerate((answer[:split], answer[split:])):
            if text:
                await asyncio.sleep(0)
                yield ProviderChunk(
                    provider=self.name,
                    model=request.model,
                    sequence=sequence,
                    delta=[CanonicalContentPart(type="text", text=text)],
                )
        yield ProviderChunk(
            provider=self.name,
            model=request.model,
            sequence=2,
            is_final=True,
            finish_reason="stop",
            usage=self._usage(request, answer),
        )

    async def health(self) -> ProviderHealth:
        return ProviderHealth(healthy=True, checked_at=datetime.now(UTC), detail="local mock")

    def price(self, usage: NormalizedUsage, model: str) -> Money | None:
        del usage, model
        return None


def local_adapters(dataset: DatasetBundle) -> dict[str, ProviderAdapter]:
    return {
        name: DatasetMockAdapter(dataset, name)
        for name in ("private-vllm", "managed-economy", "managed-premium")
    }
