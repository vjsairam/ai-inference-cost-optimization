"""FR-006 / spec §8.4: bounded fallback, deadlines, and streaming safety."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from inference_gateway.adapters import MockBehavior, MockBehaviorKind, MockProviderAdapter
from inference_gateway.config import RoutingPolicy, TimeoutConfig
from inference_gateway.models import (
    CanonicalChatRequest,
    CanonicalContentPart,
    CanonicalMessage,
    DataClass,
    ErrorClass,
    MessageRole,
    ProviderError,
    QualityTier,
    RequestContext,
    RequestMetadata,
)
from inference_gateway.routing import FallbackExecutor, RouteDecision


def _request(data_class: DataClass = DataClass.INTERNAL) -> CanonicalChatRequest:
    return CanonicalChatRequest(
        messages=[
            CanonicalMessage(
                role=MessageRole.USER,
                content=[CanonicalContentPart(type="text", text="hello")],
            )
        ],
        model="lab-default",
        metadata=RequestMetadata(
            workload="generic",
            data_class=data_class,
            quality_tier=QualityTier.ECONOMY,
            request_id="req-fallback",
        ),
    )


def _ctx(deadline_seconds: float = 5.0) -> RequestContext:
    now = datetime.now(UTC)
    started = now if deadline_seconds >= 0 else now + timedelta(seconds=deadline_seconds - 1)
    return RequestContext(
        request_id="req-fallback",
        started_at=started,
        deadline_at=now + timedelta(seconds=deadline_seconds),
    )


def _adapter(name: str, *behaviors: MockBehavior | MockBehaviorKind) -> MockProviderAdapter:
    adapter = MockProviderAdapter(script=list(behaviors) or None)
    adapter.name = name
    return adapter


def _executor(
    routing_policy: RoutingPolicy,
    adapters: dict[str, MockProviderAdapter],
    **timeout_overrides: float,
) -> FallbackExecutor:
    policy = routing_policy
    if timeout_overrides:
        policy = routing_policy.model_copy(update={"timeouts": TimeoutConfig(**timeout_overrides)})
    return FallbackExecutor(adapters, policy)


DECISION = RouteDecision(
    rule_name="economy-default", primary="private-vllm", fallbacks=("managed-economy",)
)


async def test_rate_limit_falls_back_to_next_provider(
    routing_policy: RoutingPolicy,
) -> None:
    adapters = {
        "private-vllm": _adapter("private-vllm", MockBehaviorKind.RATE_LIMITED_429),
        "managed-economy": _adapter("managed-economy"),
    }
    outcome = await _executor(routing_policy, adapters).chat(_request(), _ctx(), DECISION)
    assert outcome.provider == "managed-economy"
    assert outcome.fallback_count == 1
    assert outcome.attempts[0].error_class is ErrorClass.RATE_LIMITED
    assert outcome.attempts[0].retry_after_seconds is not None


async def test_ineligible_error_never_falls_back(routing_policy: RoutingPolicy) -> None:
    adapters = {
        "private-vllm": _adapter("private-vllm", MockBehaviorKind.MALFORMED_RESPONSE),
        "managed-economy": _adapter("managed-economy"),
    }
    with pytest.raises(ProviderError) as excinfo:
        await _executor(routing_policy, adapters).chat(_request(), _ctx(), DECISION)
    assert excinfo.value.error.error_class is ErrorClass.MALFORMED_RESPONSE


async def test_max_attempts_bounds_the_route(routing_policy: RoutingPolicy) -> None:
    """A three-provider route is cut to the configured max_attempts=2."""
    decision = RouteDecision(
        rule_name="wide",
        primary="private-vllm",
        fallbacks=("managed-economy", "managed-premium"),
    )
    third = _adapter("managed-premium", MockBehaviorKind.OK)
    adapters = {
        "private-vllm": _adapter("private-vllm", MockBehaviorKind.SERVER_500),
        "managed-economy": _adapter("managed-economy", MockBehaviorKind.SERVER_500),
        "managed-premium": third,
    }
    with pytest.raises(ProviderError) as excinfo:
        await _executor(routing_policy, adapters).chat(_request(), _ctx(), decision)
    assert excinfo.value.error.error_class is ErrorClass.PROVIDER_5XX
    assert len(third._script) == 1  # third provider was never attempted


async def test_slow_attempt_times_out_and_falls_back(
    routing_policy: RoutingPolicy,
) -> None:
    adapters = {
        "private-vllm": _adapter("private-vllm", MockBehavior.delayed(500)),
        "managed-economy": _adapter("managed-economy"),
    }
    executor = _executor(
        routing_policy,
        adapters,
        connect_timeout=0.05,
        response_header_timeout=0.05,
        stream_idle_timeout=0.05,
        per_attempt_timeout=0.05,
        global_request_deadline=5.0,
    )
    outcome = await executor.chat(_request(), _ctx(), DECISION)
    assert outcome.provider == "managed-economy"
    assert outcome.attempts[0].error_class is ErrorClass.TIMEOUT


async def test_exhausted_deadline_fails_without_attempts(
    routing_policy: RoutingPolicy,
) -> None:
    adapters = {
        "private-vllm": _adapter("private-vllm"),
        "managed-economy": _adapter("managed-economy"),
    }
    with pytest.raises(ProviderError) as excinfo:
        await _executor(routing_policy, adapters).chat(
            _request(), _ctx(deadline_seconds=-1.0), DECISION
        )
    assert excinfo.value.error.error_class is ErrorClass.TIMEOUT


async def test_restricted_strips_external_providers_in_depth(
    routing_policy: RoutingPolicy,
) -> None:
    """Defense in depth: a mis-built decision cannot send restricted data out."""
    managed = _adapter("managed-economy", MockBehaviorKind.OK)
    adapters = {
        "private-vllm": _adapter("private-vllm", MockBehaviorKind.SERVER_500),
        "managed-economy": managed,
    }
    with pytest.raises(ProviderError):
        await _executor(routing_policy, adapters).chat(
            _request(DataClass.RESTRICTED), _ctx(), DECISION
        )
    assert len(managed._script) == 1  # external provider was never attempted


async def test_stream_falls_back_before_first_chunk(
    routing_policy: RoutingPolicy,
) -> None:
    adapters = {
        "private-vllm": _adapter("private-vllm", MockBehaviorKind.RATE_LIMITED_429),
        "managed-economy": _adapter("managed-economy", MockBehaviorKind.STREAM_OK),
    }
    chunks = [
        item
        async for item in _executor(routing_policy, adapters).stream(_request(), _ctx(), DECISION)
    ]
    providers = {provider for provider, _, _ in chunks}
    assert providers == {"managed-economy"}
    assert all(index == 1 for _, index, _ in chunks)
    assert chunks[-1][2].is_final


async def test_stream_failure_after_start_never_replays(
    routing_policy: RoutingPolicy,
) -> None:
    fallback_target = _adapter("managed-economy", MockBehaviorKind.STREAM_OK)
    adapters = {
        "private-vllm": _adapter("private-vllm", MockBehaviorKind.STREAM_FAIL_AFTER_FIRST_CHUNK),
        "managed-economy": fallback_target,
    }
    received = []
    with pytest.raises(ProviderError) as excinfo:
        async for item in _executor(routing_policy, adapters).stream(_request(), _ctx(), DECISION):
            received.append(item)
    assert excinfo.value.error.error_class is ErrorClass.STREAM_STARTED_FAILURE
    assert len(received) == 1
    assert len(fallback_target._script) == 1  # no replay to another provider
