"""FR-008: exact-decimal cost estimation from date-stamped pricing config."""

from __future__ import annotations

from decimal import Decimal

from prospera_gateway.config import GatewayConfig
from prospera_gateway.config.pricing import PricingEngine
from prospera_gateway.models import NormalizedUsage, UsageSource


def _usage(input_tokens: int, output_tokens: int) -> NormalizedUsage:
    return NormalizedUsage(
        billed_input_tokens=input_tokens,
        billed_input_tokens_source=UsageSource.PROVIDER_REPORTED,
        billed_output_tokens=output_tokens,
        billed_output_tokens_source=UsageSource.PROVIDER_REPORTED,
    )


def test_price_is_exact_decimal(gateway_config: GatewayConfig) -> None:
    engine = PricingEngine(gateway_config.providers)
    money = engine.price("managed-primary", "prospera-economy", _usage(1_000_000, 1_000_000))
    assert money is not None
    pricing = gateway_config.providers.pricing["managed-primary"]["prospera-economy"]
    assert money.amount == pricing.input_per_1m + pricing.output_per_1m
    assert money.currency == "USD"


def test_small_usage_keeps_precision(gateway_config: GatewayConfig) -> None:
    engine = PricingEngine(gateway_config.providers)
    money = engine.price("managed-primary", "prospera-premium", _usage(4, 2))
    assert money is not None
    pricing = gateway_config.providers.pricing["managed-primary"]["prospera-premium"]
    expected = (Decimal(4) * pricing.input_per_1m + Decimal(2) * pricing.output_per_1m) / Decimal(
        1_000_000
    )
    assert money.amount == expected


def test_missing_usage_returns_none(gateway_config: GatewayConfig) -> None:
    engine = PricingEngine(gateway_config.providers)
    assert (
        engine.price("managed-primary", "prospera-economy", NormalizedUsage.unavailable()) is None
    )


def test_unpriced_provider_or_model_returns_none(gateway_config: GatewayConfig) -> None:
    engine = PricingEngine(gateway_config.providers)
    assert engine.price("private-vllm", "prospera-private", _usage(10, 10)) is None
    assert engine.price("managed-primary", "unknown-model", _usage(10, 10)) is None
