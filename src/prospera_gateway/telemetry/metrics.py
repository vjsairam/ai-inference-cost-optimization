"""Prometheus metrics with bounded-cardinality labels (FR-009, spec §13.1).

Labels carry only enum-like values: provider names, model aliases, workloads and
teams from validated configuration, outcome/error classes, and routing rule
names. Request IDs, prompt content, and other unbounded values never become
labels.
"""

from __future__ import annotations

from decimal import Decimal

from prometheus_client import CollectorRegistry, Counter, Histogram

_LATENCY_BUCKETS = (
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
    60.0,
    90.0,
)
_TTFT_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0)


class GatewayMetrics:
    """All gateway series from spec §13.1, bound to one registry."""

    def __init__(self, registry: CollectorRegistry) -> None:
        self.registry = registry
        self.requests_total = Counter(
            "prospera_requests_total",
            "Gateway requests by outcome",
            ("provider", "model_alias", "workload", "team", "outcome"),
            registry=registry,
        )
        self.request_latency_seconds = Histogram(
            "prospera_request_latency_seconds",
            "End-to-end gateway latency",
            ("provider", "workload"),
            buckets=_LATENCY_BUCKETS,
            registry=registry,
        )
        self.ttft_seconds = Histogram(
            "prospera_ttft_seconds",
            "Time to first streamed content event",
            ("provider", "workload"),
            buckets=_TTFT_BUCKETS,
            registry=registry,
        )
        self.input_tokens_total = Counter(
            "prospera_input_tokens_total",
            "Billed input tokens where provider-reported",
            ("provider", "model_alias", "team"),
            registry=registry,
        )
        self.output_tokens_total = Counter(
            "prospera_output_tokens_total",
            "Billed output tokens where provider-reported",
            ("provider", "model_alias", "team"),
            registry=registry,
        )
        self.estimated_managed_cost_usd_total = Counter(
            "prospera_estimated_managed_cost_usd_total",
            "Estimated managed API cost in USD from date-stamped pricing config",
            ("provider", "model_alias", "team"),
            registry=registry,
        )
        self.routing_decisions_total = Counter(
            "prospera_routing_decisions_total",
            "Routing decisions by rule",
            ("route", "reason"),
            registry=registry,
        )
        self.fallback_total = Counter(
            "prospera_fallback_total",
            "Fallback transitions",
            ("from_provider", "to_provider", "reason"),
            registry=registry,
        )
        self.provider_errors_total = Counter(
            "prospera_provider_errors_total",
            "Normalized provider errors",
            ("provider", "error_class"),
            registry=registry,
        )
        self.policy_denied_total = Counter(
            "prospera_policy_denied_total",
            "Requests denied by policy",
            ("reason",),
            registry=registry,
        )

    def record_cost(self, provider: str, model_alias: str, team: str, amount_usd: Decimal) -> None:
        self.estimated_managed_cost_usd_total.labels(
            provider=provider, model_alias=model_alias, team=team
        ).inc(float(amount_usd))
