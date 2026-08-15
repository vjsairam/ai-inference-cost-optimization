"""Grafana PromQL must use only series emitted by the real gateway registry."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from prometheus_client import CollectorRegistry

from inference_gateway.telemetry import GatewayMetrics

ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_ROOT = ROOT / "observability/grafana/dashboards"
GATEWAY_NAME = re.compile(r"\b(gateway_[a-zA-Z0-9_:]+)")
DCGM_NAME = re.compile(r"\b(DCGM_[A-Z0-9_]+)")
ALLOWED_DCGM = {
    "DCGM_FI_DEV_GPU_UTIL",
    "DCGM_FI_DEV_FB_USED",
    "DCGM_FI_DEV_FB_FREE",
    "DCGM_FI_DEV_POWER_USAGE",
}


def _expressions(value: Any) -> list[str]:
    expressions: list[str] = []
    if isinstance(value, dict):
        if isinstance(value.get("expr"), str):
            expressions.append(value["expr"])
        for child in value.values():
            expressions.extend(_expressions(child))
    elif isinstance(value, list):
        for child in value:
            expressions.extend(_expressions(child))
    return expressions


def test_all_dashboards_are_valid_and_reference_registered_metrics() -> None:
    paths = sorted(DASHBOARD_ROOT.glob("*.json"))
    assert {path.name for path in paths} == {
        "executive-economics.json",
        "gpu-efficiency.json",
        "inference-slo.json",
        "routing-failure.json",
    }
    registry = CollectorRegistry()
    GatewayMetrics(registry)
    registered = set(registry._names_to_collectors)  # noqa: SLF001 - registry validation
    for path in paths:
        dashboard = json.loads(path.read_text(encoding="utf-8"))
        assert dashboard["title"]
        assert dashboard["uid"]
        expressions = _expressions(dashboard)
        for expression in expressions:
            referenced = set(GATEWAY_NAME.findall(expression))
            assert referenced <= registered, (
                f"{path.name}: unknown gateway series {referenced - registered}"
            )
            dcgm = set(DCGM_NAME.findall(expression))
            assert dcgm <= ALLOWED_DCGM, f"{path.name}: unknown DCGM series {dcgm - ALLOWED_DCGM}"
