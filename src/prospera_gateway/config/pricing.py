"""Per-request managed cost estimation from date-stamped pricing config (FR-008)."""

from __future__ import annotations

from decimal import Decimal

from prospera_gateway.config.schema import ProvidersDocument
from prospera_gateway.models import Money, NormalizedUsage

_MILLION = Decimal(1_000_000)


class PricingEngine:
    """Prices provider-reported billed usage; never invents tokens."""

    def __init__(self, providers: ProvidersDocument) -> None:
        self._providers = providers

    def price(
        self,
        provider: str,
        model_alias: str,
        usage: NormalizedUsage,
    ) -> Money | None:
        model_prices = self._providers.pricing.get(provider)
        if model_prices is None:
            return None
        pricing = model_prices.get(model_alias)
        if pricing is None:
            return None
        if usage.billed_input_tokens is None or usage.billed_output_tokens is None:
            return None
        amount = (
            Decimal(usage.billed_input_tokens) * pricing.input_per_1m
            + Decimal(usage.billed_output_tokens) * pricing.output_per_1m
        ) / _MILLION
        return Money(amount=amount, currency=pricing.currency)
