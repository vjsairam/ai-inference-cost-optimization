from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from inference_gateway.models import (
    Money,
    NormalizedUsage,
    ProviderHealth,
    RequestContext,
    UsageSource,
)


def test_money_uses_exact_decimal_arithmetic() -> None:
    subtotal = Money(amount=Decimal("0.10"), currency="USD")
    fee = Money(amount=Decimal("0.20"), currency="USD")

    assert subtotal + fee == Money(amount=Decimal("0.30"), currency="USD")
    assert subtotal * Decimal("2.5") == Money(amount=Decimal("0.250"), currency="USD")


def test_money_rejects_float_and_mixed_currency_arithmetic() -> None:
    with pytest.raises(ValidationError, match="must not be a float"):
        Money(amount=0.1, currency="USD")

    with pytest.raises(ValueError, match="different currencies"):
        _ = Money(amount=Decimal("1"), currency="USD") + Money(amount=Decimal("1"), currency="EUR")

    with pytest.raises(TypeError, match="must not be a float"):
        _ = Money(amount=Decimal("1"), currency="USD") * 1.5  # type: ignore[operator]


def test_request_context_preserves_opaque_id_and_serializes_utc() -> None:
    started = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    context = RequestContext(
        request_id="req_opaque-value.7",
        started_at=started,
        deadline_at=started + timedelta(seconds=90),
    )

    serialized = context.model_dump(mode="json")
    assert context.request_id == "req_opaque-value.7"
    assert serialized["started_at"].endswith("Z")
    assert serialized["deadline_at"].endswith("Z")


def test_timestamp_models_reject_naive_or_non_utc_values() -> None:
    with pytest.raises(ValidationError, match="timezone-aware UTC"):
        ProviderHealth(healthy=True, checked_at=datetime(2026, 8, 15))

    offset = timezone(timedelta(hours=1))
    with pytest.raises(ValidationError, match="timezone-aware UTC"):
        ProviderHealth(healthy=True, checked_at=datetime(2026, 8, 15, tzinfo=offset))


def test_request_context_rejects_reversed_deadline() -> None:
    started = datetime(2026, 8, 15, tzinfo=UTC)
    with pytest.raises(ValidationError, match="must not precede"):
        RequestContext(
            request_id="opaque",
            started_at=started,
            deadline_at=started - timedelta(seconds=1),
        )


def test_usage_requires_per_field_provenance() -> None:
    usage = NormalizedUsage(
        visible_output_tokens=3,
        visible_output_tokens_source=UsageSource.TOKENIZER_ESTIMATED,
    )
    assert usage.visible_output_tokens_source is UsageSource.TOKENIZER_ESTIMATED
    assert usage.billed_input_tokens_source is UsageSource.UNAVAILABLE

    with pytest.raises(ValidationError, match="cannot have a value"):
        NormalizedUsage(billed_input_tokens=2)

    with pytest.raises(ValidationError, match="needs a value"):
        NormalizedUsage(billed_output_tokens_source=UsageSource.PROVIDER_REPORTED)
