"""Gateway application factory and adapter wiring (M1)."""

from __future__ import annotations

import os
from dataclasses import dataclass

import anthropic
import httpx
from fastapi import FastAPI
from prometheus_client import CollectorRegistry

from inference_gateway.adapters.anthropic_managed import AnthropicManagedAdapter
from inference_gateway.adapters.base import ProviderAdapter
from inference_gateway.adapters.openai_compat import OpenAICompatAdapter
from inference_gateway.config import ConfigurationError, GatewayConfig
from inference_gateway.config.pricing import PricingEngine
from inference_gateway.routing import FallbackExecutor
from inference_gateway.security import AuthConfig
from inference_gateway.telemetry import GatewayMetrics

_MANAGED_ADAPTERS = ("anthropic",)


@dataclass(frozen=True)
class GatewayState:
    config: GatewayConfig
    auth: AuthConfig
    adapters: dict[str, ProviderAdapter]
    executor: FallbackExecutor
    metrics: GatewayMetrics
    pricing: PricingEngine
    model_aliases: frozenset[str]


def known_model_aliases(config: GatewayConfig) -> frozenset[str]:
    """Configured aliases; bounds the model_alias metric label (spec §13.1)."""
    aliases: set[str] = set(config.providers.route_aliases)
    for provider in config.providers.providers.values():
        aliases.update(provider.models)
    return frozenset(aliases)


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigurationError(f"required environment variable {name} is not set")
    return value


def _single_model_alias(models: dict[str, object], provider_name: str) -> str:
    if len(models) != 1:
        raise ConfigurationError(
            f"provider {provider_name!r} has multiple models; "
            "the routing entry must name a model_alias"
        )
    return next(iter(models))


def build_adapters(
    config: GatewayConfig,
    pricing: PricingEngine,
    connect_timeout: float,
    response_header_timeout: float,
) -> dict[str, ProviderAdapter]:
    """Construct one adapter per routing provider entry; fail fast on gaps."""
    adapters: dict[str, ProviderAdapter] = {}
    for route_name, route in config.routing.providers.items():
        provider_name = route.provider or route_name
        provider = config.providers.providers[provider_name]
        model_alias = route.model_alias or _single_model_alias(dict(provider.models), provider_name)
        upstream_model = provider.models[model_alias].upstream_model
        if route.type == "openai-compatible":
            base_url = _require_env(provider.base_url_env or "")
            api_key = os.environ.get(provider.api_key_env) if provider.api_key_env else None
            timeout = httpx.Timeout(response_header_timeout, connect=connect_timeout)
            adapters[route_name] = OpenAICompatAdapter(
                name=route_name,
                upstream_model=upstream_model,
                client=httpx.AsyncClient(base_url=base_url, timeout=timeout),
                api_key=api_key,
            )
        elif provider.adapter in _MANAGED_ADAPTERS:
            api_key = _require_env(provider.api_key_env or "")
            managed_base_url = (
                _require_env(provider.base_url_env) if provider.base_url_env is not None else None
            )
            adapters[route_name] = AnthropicManagedAdapter(
                name=route_name,
                upstream_model=upstream_model,
                model_alias=model_alias,
                # Retries stay with the fallback executor so the configured
                # attempt bound is real; timeouts mirror the gateway config.
                client=anthropic.AsyncAnthropic(
                    api_key=api_key,
                    base_url=managed_base_url,
                    max_retries=0,
                    timeout=httpx.Timeout(response_header_timeout, connect=connect_timeout),
                ),
                pricing=pricing,
                provider_config_name=provider_name,
                supports_sampling=provider.models[model_alias].supports_sampling,
            )
        else:
            raise ConfigurationError(
                f"provider {provider_name!r} names unknown adapter {provider.adapter!r}"
            )
    return adapters


def create_app(
    config: GatewayConfig,
    auth: AuthConfig,
    adapters: dict[str, ProviderAdapter] | None = None,
    registry: CollectorRegistry | None = None,
) -> FastAPI:
    """Build the gateway app; tests inject adapters, production builds them."""
    from inference_gateway.api.routes import register_routes

    pricing = PricingEngine(config.providers)
    timeouts = config.routing.timeouts
    if adapters is None:
        adapters = build_adapters(
            config,
            pricing,
            connect_timeout=timeouts.connect_timeout,
            response_header_timeout=timeouts.response_header_timeout,
        )
    missing = set(config.routing.providers).difference(adapters)
    if missing:
        raise ConfigurationError(
            f"no adapter constructed for routing providers: {', '.join(sorted(missing))}"
        )
    metrics = GatewayMetrics(registry or CollectorRegistry())
    state = GatewayState(
        config=config,
        auth=auth,
        adapters=adapters,
        executor=FallbackExecutor(adapters, config.routing),
        metrics=metrics,
        pricing=pricing,
        model_aliases=known_model_aliases(config),
    )
    app = FastAPI(title="inference-gateway", docs_url=None, redoc_url=None)
    app.state.gateway = state
    register_routes(app)
    return app
