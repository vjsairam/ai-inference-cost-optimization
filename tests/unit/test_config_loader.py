from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from inference_gateway.config import ConfigurationError, load_gateway_config

ROOT = Path(__file__).parents[2]
PROVIDERS_EXAMPLE = ROOT / "config" / "providers.example.yaml"
ROUTING_EXAMPLE = ROOT / "policy" / "routing.yaml"


@pytest.fixture
def provider_data() -> dict[str, object]:
    loaded = yaml.safe_load(PROVIDERS_EXAMPLE.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


@pytest.fixture
def routing_data() -> dict[str, object]:
    loaded = yaml.safe_load(ROUTING_EXAMPLE.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def write_yaml(path: Path, data: dict[str, object]) -> Path:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def test_loads_example_config_with_decimal_prices_and_timeout_defaults() -> None:
    config = load_gateway_config(PROVIDERS_EXAMPLE, ROUTING_EXAMPLE)

    price = config.providers.pricing["managed-primary"]["lab-economy"]
    assert price.input_per_1m == Decimal("0.150")
    assert isinstance(price.input_per_1m, Decimal)
    assert price.effective_date == date(2026, 8, 15)
    assert config.routing.timeouts.connect_timeout == 5
    assert config.routing.timeouts.global_request_deadline == 90


def test_unknown_provider_in_rule_fails_fast(
    tmp_path: Path,
    routing_data: dict[str, object],
) -> None:
    mutated = deepcopy(routing_data)
    mutated["rules"][0]["route"] = ["missing-provider"]  # type: ignore[index]
    routing_path = write_yaml(tmp_path / "routing.yaml", mutated)

    with pytest.raises(ConfigurationError, match="unknown providers: missing-provider"):
        load_gateway_config(PROVIDERS_EXAMPLE, routing_path)


def test_restricted_external_route_fails_fast(
    tmp_path: Path,
    routing_data: dict[str, object],
) -> None:
    mutated = deepcopy(routing_data)
    mutated["rules"][0]["route"] = ["managed-premium"]  # type: ignore[index]
    routing_path = write_yaml(tmp_path / "routing.yaml", mutated)

    with pytest.raises(ConfigurationError, match="route restricted data externally"):
        load_gateway_config(PROVIDERS_EXAMPLE, routing_path)


def test_rule_without_data_class_cannot_expose_restricted_data(
    tmp_path: Path,
    routing_data: dict[str, object],
) -> None:
    mutated = deepcopy(routing_data)
    mutated["rules"].append(  # type: ignore[union-attr]
        {
            "name": "unsafe-catchall",
            "when": {"quality_tier": ["premium"]},
            "route": ["managed-premium"],
        }
    )
    routing_path = write_yaml(tmp_path / "routing.yaml", mutated)

    with pytest.raises(ConfigurationError, match="unsafe-catchall"):
        load_gateway_config(PROVIDERS_EXAMPLE, routing_path)


def test_missing_pricing_field_fails_fast(
    tmp_path: Path,
    provider_data: dict[str, object],
) -> None:
    mutated = deepcopy(provider_data)
    del mutated["pricing"]["managed-primary"]["lab-economy"]["output_per_1m"]  # type: ignore[index]
    providers_path = write_yaml(tmp_path / "providers.yaml", mutated)

    with pytest.raises(ConfigurationError, match="output_per_1m"):
        load_gateway_config(providers_path, ROUTING_EXAMPLE)


def test_malformed_effective_date_fails_fast(
    tmp_path: Path,
    provider_data: dict[str, object],
) -> None:
    mutated = deepcopy(provider_data)
    mutated["pricing"]["managed-primary"]["lab-economy"]["effective_date"] = (  # type: ignore[index]
        "15-08-2026"
    )
    providers_path = write_yaml(tmp_path / "providers.yaml", mutated)

    with pytest.raises(ConfigurationError, match="effective_date"):
        load_gateway_config(providers_path, ROUTING_EXAMPLE)


def test_negative_price_fails_fast(
    tmp_path: Path,
    provider_data: dict[str, object],
) -> None:
    mutated = deepcopy(provider_data)
    mutated["pricing"]["managed-primary"]["lab-economy"]["input_per_1m"] = (  # type: ignore[index]
        "-0.01"
    )
    providers_path = write_yaml(tmp_path / "providers.yaml", mutated)

    with pytest.raises(ConfigurationError, match="greater than or equal to 0"):
        load_gateway_config(providers_path, ROUTING_EXAMPLE)


def test_native_float_price_is_rejected(
    tmp_path: Path,
    provider_data: dict[str, object],
) -> None:
    mutated = deepcopy(provider_data)
    mutated["pricing"]["managed-primary"]["lab-economy"]["input_per_1m"] = 0.1  # type: ignore[index]
    providers_path = write_yaml(tmp_path / "providers.yaml", mutated)

    with pytest.raises(ConfigurationError, match="quoted decimals"):
        load_gateway_config(providers_path, ROUTING_EXAMPLE)


def test_per_attempt_timeout_above_global_deadline_fails_fast(
    tmp_path: Path,
    routing_data: dict[str, object],
) -> None:
    mutated = deepcopy(routing_data)
    mutated["timeouts"]["per_attempt_timeout"] = 91  # type: ignore[index]
    routing_path = write_yaml(tmp_path / "routing.yaml", mutated)

    with pytest.raises(ConfigurationError, match="per_attempt_timeout"):
        load_gateway_config(PROVIDERS_EXAMPLE, routing_path)


def test_cross_file_unknown_backing_provider_fails_fast(
    tmp_path: Path,
    routing_data: dict[str, object],
) -> None:
    mutated = deepcopy(routing_data)
    mutated["providers"]["managed-premium"]["provider"] = "missing-provider"  # type: ignore[index]
    routing_path = write_yaml(tmp_path / "routing.yaml", mutated)

    with pytest.raises(ConfigurationError, match="references unknown provider"):
        load_gateway_config(PROVIDERS_EXAMPLE, routing_path)
