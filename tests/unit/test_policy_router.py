"""FR-004/FR-005 and spec §8.6: every data_class × quality_tier combination."""

from __future__ import annotations

import pytest

from inference_gateway.config import RoutingPolicy
from inference_gateway.models import DataClass, QualityTier
from inference_gateway.routing import PolicyDenied, select_route

# Expected (primary, fallbacks) for every cell of the lab policy, per §8.6.
EXPECTED: dict[tuple[DataClass, QualityTier], tuple[str, tuple[str, ...]]] = {
    (DataClass.PUBLIC, QualityTier.ECONOMY): ("private-vllm", ("managed-economy",)),
    (DataClass.PUBLIC, QualityTier.BALANCED): ("private-vllm", ("managed-premium",)),
    (DataClass.PUBLIC, QualityTier.PREMIUM): ("managed-premium", ("private-vllm",)),
    (DataClass.INTERNAL, QualityTier.ECONOMY): ("private-vllm", ("managed-economy",)),
    (DataClass.INTERNAL, QualityTier.BALANCED): ("private-vllm", ("managed-premium",)),
    (DataClass.INTERNAL, QualityTier.PREMIUM): ("managed-premium", ("private-vllm",)),
    (DataClass.CONFIDENTIAL, QualityTier.ECONOMY): ("private-vllm", ()),
    (DataClass.CONFIDENTIAL, QualityTier.BALANCED): ("private-vllm", ()),
    (DataClass.CONFIDENTIAL, QualityTier.PREMIUM): ("managed-premium", ("private-vllm",)),
    (DataClass.RESTRICTED, QualityTier.ECONOMY): ("private-vllm", ()),
    (DataClass.RESTRICTED, QualityTier.BALANCED): ("private-vllm", ()),
    (DataClass.RESTRICTED, QualityTier.PREMIUM): ("private-vllm", ()),
}


@pytest.mark.parametrize(("data_class", "quality_tier"), sorted(EXPECTED))
def test_every_cell_routes_as_specified(
    routing_policy: RoutingPolicy,
    data_class: DataClass,
    quality_tier: QualityTier,
) -> None:
    decision = select_route(routing_policy, data_class, "generic", quality_tier)
    primary, fallbacks = EXPECTED[(data_class, quality_tier)]
    assert decision.primary == primary
    assert decision.fallbacks == fallbacks


@pytest.mark.parametrize("quality_tier", list(QualityTier))
def test_restricted_never_routes_external(
    routing_policy: RoutingPolicy, quality_tier: QualityTier
) -> None:
    decision = select_route(routing_policy, DataClass.RESTRICTED, "generic", quality_tier)
    for provider in decision.providers:
        assert not routing_policy.providers[provider].external


def test_restricted_with_only_external_route_fails_closed(
    routing_policy: RoutingPolicy,
) -> None:
    """Runtime invariant: even a mis-built rule cannot leak restricted data."""
    bad_rule = routing_policy.rules[0].model_copy(update={"route": ["managed-premium"]})
    policy = routing_policy.model_copy(update={"rules": [bad_rule]})
    with pytest.raises(PolicyDenied):
        select_route(policy, DataClass.RESTRICTED, "generic", QualityTier.ECONOMY)


def test_no_matching_rule_is_denied(routing_policy: RoutingPolicy) -> None:
    policy = routing_policy.model_copy(update={"rules": routing_policy.rules[:1]})
    with pytest.raises(PolicyDenied):
        select_route(policy, DataClass.PUBLIC, "generic", QualityTier.ECONOMY)


def test_workload_match_is_respected(routing_policy: RoutingPolicy) -> None:
    scoped_rule = routing_policy.rules[1].model_copy(
        update={
            "when": routing_policy.rules[1].when.model_copy(update={"workload": ["classification"]})
        }
    )
    policy = routing_policy.model_copy(update={"rules": [scoped_rule]})
    decision = select_route(policy, DataClass.PUBLIC, "classification", QualityTier.ECONOMY)
    assert decision.rule_name == scoped_rule.name
    with pytest.raises(PolicyDenied):
        select_route(policy, DataClass.PUBLIC, "generation", QualityTier.ECONOMY)


def test_first_matching_rule_wins(routing_policy: RoutingPolicy) -> None:
    decision = select_route(routing_policy, DataClass.RESTRICTED, "generic", QualityTier.PREMIUM)
    assert decision.rule_name == "restricted-private-only"
