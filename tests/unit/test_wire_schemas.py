"""FR-001: OpenAI-compatible wire translation to canonical models."""

from __future__ import annotations

import pytest

from prospera_gateway.api import schemas
from prospera_gateway.models import (
    CanonicalContentPart,
    DataClass,
    MessageRole,
    NormalizedUsage,
    ProviderResult,
    QualityTier,
    UsageSource,
)


def _wire(**overrides: object) -> schemas.ChatCompletionRequest:
    payload: dict[str, object] = {
        "model": "prospera-default",
        "messages": [
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ],
    }
    payload.update(overrides)
    return schemas.ChatCompletionRequest.model_validate(payload)


def test_roles_map_to_canonical() -> None:
    canonical = schemas.to_canonical(
        _wire(), "generic", DataClass.INTERNAL, QualityTier.ECONOMY, "req-1"
    )
    assert [m.role for m in canonical.messages] == [
        MessageRole.SYSTEM,
        MessageRole.USER,
        MessageRole.MODEL,
    ]
    assert canonical.metadata.request_id == "req-1"
    assert canonical.metadata.data_class is DataClass.INTERNAL


def test_unknown_role_is_rejected() -> None:
    wire = _wire(messages=[{"role": "narrator", "content": "x"}])
    with pytest.raises(schemas.RequestValidationFailed):
        schemas.to_canonical(wire, "generic", DataClass.PUBLIC, QualityTier.ECONOMY, "r")


def test_extra_body_fields_are_rejected() -> None:
    with pytest.raises(ValueError, match="extra_forbidden|Extra inputs"):
        schemas.ChatCompletionRequest.model_validate(
            {"model": "m", "messages": [{"role": "user", "content": "x"}], "tools": []}
        )


def _result(usage: NormalizedUsage) -> ProviderResult:
    return ProviderResult(
        provider="p",
        model="prospera-default",
        output=[CanonicalContentPart(type="text", text="answer")],
        finish_reason="stop",
        usage=usage,
    )


def test_completion_response_includes_reported_usage() -> None:
    usage = NormalizedUsage(
        billed_input_tokens=7,
        billed_input_tokens_source=UsageSource.PROVIDER_REPORTED,
        billed_output_tokens=3,
        billed_output_tokens_source=UsageSource.PROVIDER_REPORTED,
    )
    payload = schemas.completion_response("rid", 1, "prospera-default", _result(usage))
    assert payload["usage"] == {
        "prompt_tokens": 7,
        "completion_tokens": 3,
        "total_tokens": 10,
    }


def test_completion_response_omits_unavailable_usage() -> None:
    """Never invent billing tokens (spec §8.2)."""
    payload = schemas.completion_response(
        "rid", 1, "prospera-default", _result(NormalizedUsage.unavailable())
    )
    assert "usage" not in payload
