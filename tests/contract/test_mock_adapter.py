import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from inference_gateway.adapters import (
    MockBehavior,
    MockBehaviorKind,
    MockProviderAdapter,
    ProviderAdapter,
)
from inference_gateway.models import (
    CanonicalChatRequest,
    CanonicalContentPart,
    CanonicalMessage,
    DataClass,
    ErrorClass,
    MessageRole,
    ProviderChunk,
    ProviderError,
    QualityTier,
    RequestContext,
    RequestMetadata,
    UsageSource,
)


@pytest.fixture
def chat_request() -> CanonicalChatRequest:
    return CanonicalChatRequest(
        messages=[
            CanonicalMessage(
                role=MessageRole.USER,
                content=[CanonicalContentPart(type="text", text="fixture prompt")],
            )
        ],
        model="lab-test",
        temperature=0,
        max_tokens=32,
        metadata=RequestMetadata(
            workload="contract-fixture",
            data_class=DataClass.PUBLIC,
            quality_tier=QualityTier.BALANCED,
            request_id="opaque-contract-id",
        ),
    )


@pytest.fixture
def context() -> RequestContext:
    started = datetime(2026, 8, 15, tzinfo=UTC)
    return RequestContext(
        request_id="opaque-contract-id",
        started_at=started,
        deadline_at=started + timedelta(seconds=90),
    )


def as_contract(adapter: ProviderAdapter) -> ProviderAdapter:
    return adapter


async def collect(stream: AsyncIterator[ProviderChunk]) -> list[ProviderChunk]:
    return [chunk async for chunk in stream]


@pytest.mark.asyncio
async def test_non_streaming_ok_implements_contract(
    chat_request: CanonicalChatRequest,
    context: RequestContext,
) -> None:
    adapter = as_contract(MockProviderAdapter())

    result = await adapter.chat(chat_request, context)

    assert result.output[0].text == "mock response"
    assert result.usage.billed_input_tokens == 4
    assert result.usage.billed_input_tokens_source is UsageSource.PROVIDER_REPORTED
    assert adapter.price(result.usage, chat_request.model) == adapter.price(result.usage, "ignored")
    price = adapter.price(result.usage, chat_request.model)
    assert price is not None
    assert price.amount == Decimal("0.000025")
    assert (await adapter.health()).healthy is True


@pytest.mark.asyncio
async def test_stream_ok_has_chunked_content_and_final_usage(
    chat_request: CanonicalChatRequest,
    context: RequestContext,
) -> None:
    adapter = MockProviderAdapter(MockBehaviorKind.STREAM_OK)

    chunks = await collect(adapter.stream(chat_request, context))

    assert [part.text for chunk in chunks for part in chunk.delta] == ["mock ", "response"]
    assert [chunk.sequence for chunk in chunks] == [0, 1, 2]
    assert chunks[-1].is_final is True
    assert chunks[-1].usage is not None
    assert chunks[-1].usage.visible_output_tokens_source is UsageSource.PROVIDER_REPORTED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("behavior", "expected_class", "status"),
    [
        (MockBehavior.rate_limited(2.0), ErrorClass.RATE_LIMITED, 429),
        (MockBehaviorKind.SERVER_500, ErrorClass.PROVIDER_5XX, 500),
        (MockBehaviorKind.MALFORMED_RESPONSE, ErrorClass.MALFORMED_RESPONSE, 502),
    ],
)
async def test_non_streaming_faults_are_normalized(
    chat_request: CanonicalChatRequest,
    context: RequestContext,
    behavior: MockBehavior | MockBehaviorKind,
    expected_class: ErrorClass,
    status: int,
) -> None:
    adapter = MockProviderAdapter(behavior)

    with pytest.raises(ProviderError) as caught:
        await adapter.chat(chat_request, context)

    assert caught.value.error.error_class is expected_class
    assert caught.value.error.http_status == status
    if status == 429:
        assert caught.value.error.retry_after_seconds == 2.0


@pytest.mark.asyncio
async def test_timeout_is_async_and_cancellable(
    chat_request: CanonicalChatRequest,
    context: RequestContext,
) -> None:
    adapter = MockProviderAdapter(MockBehaviorKind.TIMEOUT, timeout_seconds=60)
    task = asyncio.create_task(adapter.chat(chat_request, context))

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(task, timeout=0.01)

    assert task.cancelled()


@pytest.mark.asyncio
async def test_stream_timeout_is_async_and_cancellable(
    chat_request: CanonicalChatRequest,
    context: RequestContext,
) -> None:
    adapter = MockProviderAdapter(MockBehaviorKind.TIMEOUT, timeout_seconds=60)
    stream = adapter.stream(chat_request, context)
    task = asyncio.create_task(anext(stream))

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(task, timeout=0.01)

    assert task.cancelled()


@pytest.mark.asyncio
async def test_missing_usage_is_explicitly_unavailable(
    chat_request: CanonicalChatRequest,
    context: RequestContext,
) -> None:
    adapter = MockProviderAdapter(include_usage=False)

    result = await adapter.chat(chat_request, context)
    chunks = await collect(adapter.stream(chat_request, context))

    assert result.usage.billed_input_tokens is None
    assert result.usage.billed_input_tokens_source is UsageSource.UNAVAILABLE
    assert chunks[-1].usage is not None
    assert chunks[-1].usage.visible_output_tokens_source is UsageSource.UNAVAILABLE
    assert adapter.price(result.usage, chat_request.model) is None


@pytest.mark.asyncio
async def test_delayed_behavior_returns_deterministically(
    chat_request: CanonicalChatRequest,
    context: RequestContext,
) -> None:
    adapter = MockProviderAdapter(MockBehavior.delayed(1))
    result = await adapter.chat(chat_request, context)
    assert result.finish_reason == "stop"


@pytest.mark.asyncio
async def test_script_consumes_behaviors_in_order(
    chat_request: CanonicalChatRequest,
    context: RequestContext,
) -> None:
    adapter = MockProviderAdapter(
        script=[MockBehaviorKind.SERVER_500, MockBehaviorKind.OK],
    )

    with pytest.raises(ProviderError):
        await adapter.chat(chat_request, context)
    assert (await adapter.chat(chat_request, context)).finish_reason == "stop"


@pytest.mark.asyncio
async def test_stream_failure_after_content_is_not_fallback_eligible(
    chat_request: CanonicalChatRequest,
    context: RequestContext,
) -> None:
    adapter = MockProviderAdapter(MockBehaviorKind.STREAM_FAIL_AFTER_FIRST_CHUNK)
    stream = adapter.stream(chat_request, context)

    first = await anext(stream)
    assert first.delta[0].text == "mock "

    with pytest.raises(ProviderError) as caught:
        await anext(stream)

    assert caught.value.error.error_class is ErrorClass.STREAM_STARTED_FAILURE
    assert caught.value.error.retry_eligible is False
    assert caught.value.error.fallback_eligible is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "behavior",
    [
        MockBehaviorKind.RATE_LIMITED_429,
        MockBehaviorKind.SERVER_500,
        MockBehaviorKind.MALFORMED_RESPONSE,
    ],
)
async def test_stream_faults_before_first_chunk(
    chat_request: CanonicalChatRequest,
    context: RequestContext,
    behavior: MockBehaviorKind,
) -> None:
    adapter = MockProviderAdapter(behavior)
    with pytest.raises(ProviderError):
        await anext(adapter.stream(chat_request, context))
