"""Shared fixtures: repo lab configuration and authenticated test identity."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import anthropic._base_client
import pytest

from inference_gateway.config import GatewayConfig, RoutingPolicy, load_gateway_config
from inference_gateway.security import ApiKeyEntry, AuthConfig, generate_api_key, hash_api_key

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _inline_anthropic_platform_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid a Python 3.13 executor-shutdown deadlock in sandboxed test runs."""

    def inline_asyncify(function: Any) -> Any:
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            return function(*args, **kwargs)

        return wrapper

    monkeypatch.setattr(anthropic._base_client, "asyncify", inline_asyncify)


@pytest.fixture(scope="session")
def gateway_config() -> GatewayConfig:
    return load_gateway_config(
        REPO_ROOT / "config" / "providers.example.yaml",
        REPO_ROOT / "policy" / "routing.yaml",
    )


@pytest.fixture(scope="session")
def routing_policy(gateway_config: GatewayConfig) -> RoutingPolicy:
    return gateway_config.routing


@pytest.fixture(scope="session")
def lab_api_key() -> str:
    return generate_api_key()


@pytest.fixture(scope="session")
def auth_config(lab_api_key: str) -> AuthConfig:
    return AuthConfig(keys=[ApiKeyEntry(sha256=hash_api_key(lab_api_key), team="platform-lab")])
