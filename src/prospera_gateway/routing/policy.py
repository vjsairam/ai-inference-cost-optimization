"""Deterministic policy router (FR-004, FR-005).

Rules are evaluated in declaration order; the first rule whose match block
accepts the request's data_class, workload, and quality_tier wins. A None field
in a match block is a wildcard. Restricted data fails closed: external providers
are stripped from any selected route as a runtime invariant, independent of the
load-time validation, and an empty resulting route is a policy denial.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from prospera_gateway.config import RoutingPolicy, RoutingRule
from prospera_gateway.models import DataClass, QualityTier


class RouteDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_name: str
    primary: str
    fallbacks: tuple[str, ...] = Field(default=())

    @property
    def providers(self) -> tuple[str, ...]:
        return (self.primary, *self.fallbacks)


class PolicyDenied(Exception):
    """No rule permits this request; the gateway fails closed."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _matches(
    rule: RoutingRule,
    data_class: DataClass,
    workload: str,
    quality_tier: QualityTier,
) -> bool:
    when = rule.when
    if when.data_class is not None and data_class not in when.data_class:
        return False
    if when.workload is not None and workload not in when.workload:
        return False
    return not (when.quality_tier is not None and quality_tier not in when.quality_tier)


def select_route(
    policy: RoutingPolicy,
    data_class: DataClass,
    workload: str,
    quality_tier: QualityTier,
) -> RouteDecision:
    for rule in policy.rules:
        if not _matches(rule, data_class, workload, quality_tier):
            continue
        route = list(rule.route)
        if data_class is DataClass.RESTRICTED:
            route = [name for name in route if not policy.providers[name].external]
        if not route:
            raise PolicyDenied(
                f"rule {rule.name!r} leaves no permitted provider for restricted data"
            )
        return RouteDecision(rule_name=rule.name, primary=route[0], fallbacks=tuple(route[1:]))
    raise PolicyDenied(
        f"no routing rule matches data_class={data_class} "
        f"workload={workload!r} quality_tier={quality_tier}"
    )
