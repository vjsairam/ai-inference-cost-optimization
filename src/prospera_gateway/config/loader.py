"""YAML loading with clear path-aware validation failures."""

from __future__ import annotations

import re
from pathlib import Path
from typing import cast

import yaml
from pydantic import ValidationError

from prospera_gateway.config.schema import GatewayConfig, ProvidersDocument, RoutingPolicy


class ConfigurationError(ValueError):
    """A configuration file could not be read or validated."""


class _ConfigSafeLoader(yaml.SafeLoader):
    """Safe loader with YAML 1.2 boolean words so the `on` policy key stays text."""


_ConfigSafeLoader.yaml_implicit_resolvers = {
    key: list(resolvers) for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
for initial in "OoYyNnTtFf":
    _ConfigSafeLoader.yaml_implicit_resolvers[initial] = [
        resolver
        for resolver in _ConfigSafeLoader.yaml_implicit_resolvers.get(initial, [])
        if resolver[0] != "tag:yaml.org,2002:bool"
    ]
_ConfigSafeLoader.add_implicit_resolver(  # type: ignore[no-untyped-call]
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


def _read_yaml(path: Path) -> dict[str, object]:
    try:
        with path.open(encoding="utf-8") as source:
            raw = yaml.load(source, Loader=_ConfigSafeLoader)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"cannot load configuration {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigurationError(f"configuration {path} must contain a YAML mapping")
    return cast(dict[str, object], raw)


def load_providers(path: str | Path) -> ProvidersDocument:
    config_path = Path(path)
    try:
        return ProvidersDocument.model_validate(_read_yaml(config_path))
    except ValidationError as exc:
        raise ConfigurationError(f"invalid provider configuration {config_path}:\n{exc}") from exc


def load_routing_policy(path: str | Path) -> RoutingPolicy:
    config_path = Path(path)
    try:
        return RoutingPolicy.model_validate(_read_yaml(config_path))
    except ValidationError as exc:
        raise ConfigurationError(f"invalid routing policy {config_path}:\n{exc}") from exc


def load_gateway_config(
    providers_path: str | Path,
    routing_path: str | Path,
) -> GatewayConfig:
    providers = load_providers(providers_path)
    routing = load_routing_policy(routing_path)
    try:
        return GatewayConfig(providers=providers, routing=routing)
    except ValidationError as exc:
        raise ConfigurationError(f"provider/routing configuration mismatch:\n{exc}") from exc
