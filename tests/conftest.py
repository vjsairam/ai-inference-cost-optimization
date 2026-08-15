"""Shared fixtures: repo lab configuration and authenticated test identity."""

from __future__ import annotations

from pathlib import Path

import pytest

from prospera_gateway.config import GatewayConfig, RoutingPolicy, load_gateway_config
from prospera_gateway.security import ApiKeyEntry, AuthConfig, generate_api_key, hash_api_key

REPO_ROOT = Path(__file__).resolve().parents[1]


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
