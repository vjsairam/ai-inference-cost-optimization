"""Validated provider, pricing, routing, and timeout configuration schemas."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PositiveFloat,
    PositiveInt,
    model_validator,
)

from prospera_gateway.models import DataClass, ErrorClass, QualityTier


def _exact_decimal(value: object) -> Decimal:
    if isinstance(value, float):
        raise ValueError("prices must be quoted decimals, not floating-point YAML values")
    if isinstance(value, (str, int, Decimal)) and not isinstance(value, bool):
        try:
            return Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("price must be a decimal") from exc
    raise ValueError("price must be a decimal string or integer")


PriceDecimal = Annotated[Decimal, BeforeValidator(_exact_decimal), Field(ge=0)]


class ProviderModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upstream_model: str = Field(min_length=1)


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["openai_compatible", "managed"]
    external: bool
    models: dict[str, ProviderModelConfig] = Field(min_length=1)
    base_url_env: str | None = None
    adapter: str | None = None
    api_key_env: str | None = None

    @model_validator(mode="after")
    def required_adapter_fields(self) -> Self:
        if self.kind == "openai_compatible" and not self.base_url_env:
            raise ValueError("openai_compatible provider requires base_url_env")
        if self.kind == "managed" and not self.adapter:
            raise ValueError("managed provider requires adapter")
        return self


class RouteAliasConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)


class PricingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str = Field(pattern=r"^[A-Z]{3}$")
    effective_date: date
    input_per_1m: PriceDecimal
    output_per_1m: PriceDecimal
    cached_input_per_1m: PriceDecimal | None
    source_url: str = Field(min_length=1)


class ProvidersDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: dict[str, ProviderConfig] = Field(min_length=1)
    route_aliases: dict[str, RouteAliasConfig] = Field(default_factory=dict)
    pricing: dict[str, dict[str, PricingConfig]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def references_and_managed_prices_exist(self) -> Self:
        for alias_name, alias in self.route_aliases.items():
            provider = self.providers.get(alias.provider)
            if provider is None:
                raise ValueError(
                    f"route alias {alias_name!r} references unknown provider {alias.provider!r}"
                )
            if alias.model not in provider.models:
                raise ValueError(
                    f"route alias {alias_name!r} references unknown model {alias.model!r}"
                )

        for provider_name, model_prices in self.pricing.items():
            provider = self.providers.get(provider_name)
            if provider is None:
                raise ValueError(f"pricing references unknown provider {provider_name!r}")
            unknown_models = set(model_prices).difference(provider.models)
            if unknown_models:
                names = ", ".join(sorted(unknown_models))
                raise ValueError(
                    f"pricing for {provider_name!r} references unknown models: {names}"
                )

        for provider_name, provider in self.providers.items():
            if provider.kind != "managed":
                continue
            prices = self.pricing.get(provider_name, {})
            missing = set(provider.models).difference(prices)
            if missing:
                names = ", ".join(sorted(missing))
                raise ValueError(
                    f"managed provider {provider_name!r} is missing prices for: {names}"
                )
        return self


class RoutingProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["openai-compatible", "managed"]
    external: bool
    provider: str | None = None
    model_alias: str | None = None

    @model_validator(mode="after")
    def managed_route_fields(self) -> Self:
        if self.type == "managed" and (not self.provider or not self.model_alias):
            raise ValueError("managed routing provider requires provider and model_alias")
        return self


class RuleMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_class: list[DataClass] | None = None
    workload: list[str] | None = None
    quality_tier: list[QualityTier] | None = None


class RoutingRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    when: RuleMatch
    route: list[str] = Field(min_length=1)


AllowedFallbackError = Literal[
    ErrorClass.TIMEOUT,
    ErrorClass.RATE_LIMITED,
    ErrorClass.PROVIDER_5XX,
]


class FallbackConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    on: list[AllowedFallbackError] = Field(min_length=1)
    max_attempts: PositiveInt
    never_cross_data_policy: bool


class TimeoutConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connect_timeout: PositiveFloat = 5.0
    response_header_timeout: PositiveFloat = 30.0
    stream_idle_timeout: PositiveFloat = 30.0
    per_attempt_timeout: PositiveFloat = 60.0
    global_request_deadline: PositiveFloat = 90.0

    @model_validator(mode="after")
    def deadline_covers_each_timeout(self) -> Self:
        timeout_fields = (
            "connect_timeout",
            "response_header_timeout",
            "stream_idle_timeout",
            "per_attempt_timeout",
        )
        for field_name in timeout_fields:
            if getattr(self, field_name) > self.global_request_deadline:
                raise ValueError(f"{field_name} must not exceed global_request_deadline")
        return self


class RoutingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    providers: dict[str, RoutingProviderConfig] = Field(min_length=1)
    rules: list[RoutingRule] = Field(min_length=1)
    fallback: FallbackConfig
    timeouts: TimeoutConfig = Field(default_factory=TimeoutConfig)
    workloads: list[str] = Field(
        default=["classification", "structured-extraction", "generation", "generic"],
        min_length=1,
        description="Allowed workload identifiers; bounds the workload metric label",
    )

    @model_validator(mode="after")
    def workload_identifiers_are_bounded(self) -> Self:
        for workload in self.workloads:
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", workload):
                raise ValueError(f"invalid workload identifier: {workload!r}")
        rule_workloads = {
            w for rule in self.rules if rule.when.workload for w in rule.when.workload
        }
        unknown = rule_workloads.difference(self.workloads)
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ValueError(f"rules reference workloads missing from allowlist: {names}")
        return self

    @model_validator(mode="after")
    def routes_are_known_and_restricted_is_private(self) -> Self:
        for rule in self.rules:
            unknown = set(rule.route).difference(self.providers)
            if unknown:
                names = ", ".join(sorted(unknown))
                raise ValueError(f"rule {rule.name!r} references unknown providers: {names}")

            matches_restricted = (
                rule.when.data_class is None or DataClass.RESTRICTED in rule.when.data_class
            )
            if matches_restricted:
                external = [name for name in rule.route if self.providers[name].external]
                if external:
                    names = ", ".join(external)
                    raise ValueError(
                        f"rule {rule.name!r} can route restricted data externally via: {names}"
                    )
        return self


class GatewayConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    providers: ProvidersDocument
    routing: RoutingPolicy

    @model_validator(mode="after")
    def routing_matches_provider_document(self) -> Self:
        for route_name, route in self.routing.providers.items():
            provider_name = route.provider or route_name
            provider = self.providers.providers.get(provider_name)
            if provider is None:
                raise ValueError(
                    f"routing provider {route_name!r} references unknown provider {provider_name!r}"
                )
            if route.external != provider.external:
                raise ValueError(f"routing provider {route_name!r} has inconsistent external flag")
            if route.model_alias and route.model_alias not in provider.models:
                raise ValueError(
                    f"routing provider {route_name!r} references unknown model "
                    f"{route.model_alias!r}"
                )
        return self
