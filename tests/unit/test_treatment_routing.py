"""M6 treatment policies use one provider and disable fallback."""

from __future__ import annotations

from pathlib import Path

import pytest

from inference_gateway.benchmark.models import BenchmarkScenario, load_scenario
from inference_gateway.config import GatewayConfig, load_gateway_config
from inference_gateway.models import DataClass, QualityTier
from inference_gateway.routing import select_route

ROOT = Path(__file__).resolve().parents[2]
PROVIDERS = ROOT / "config/providers.example.yaml"


def _load_treatment(name: str) -> tuple[BenchmarkScenario, GatewayConfig]:
    scenario = load_scenario(ROOT / f"benchmark/scenarios/cloud/{name}-baseline.yaml")
    config = load_gateway_config(PROVIDERS, ROOT / scenario.timeout_config)
    return scenario, config


@pytest.mark.parametrize(
    ("name", "expected_provider"),
    [
        ("t0-managed", "managed-premium"),
        ("t1-private", "private-vllm"),
    ],
)
def test_treatment_scenario_routes_only_to_named_provider(
    name: str, expected_provider: str
) -> None:
    scenario, config = _load_treatment(name)

    decision = select_route(
        config.routing,
        scenario.data_class,
        scenario.workload,
        scenario.quality_tier,
    )

    assert decision.providers == (expected_provider,)
    assert config.routing.fallback.max_attempts == 1


@pytest.mark.parametrize("name", ["t0-managed", "t1-private"])
@pytest.mark.parametrize("quality_tier", list(QualityTier))
def test_treatment_restricted_routes_never_include_managed_provider(
    name: str, quality_tier: QualityTier
) -> None:
    _, config = _load_treatment(name)

    decision = select_route(
        config.routing,
        DataClass.RESTRICTED,
        "classification",
        quality_tier,
    )

    assert decision.providers == ("private-vllm",)
    assert all(not config.routing.providers[name].external for name in decision.providers)
