from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
HELM = shutil.which("helm")

pytestmark = pytest.mark.skipif(
    HELM is None,
    reason="helm is not installed; IaC CI installs the pinned Helm release",
)


def _render(chart: str, *extra_args: str) -> list[dict[str, Any]]:
    chart_dir = ROOT / "infra" / "helm" / chart
    subprocess.run(
        [str(HELM), "lint", str(chart_dir), "--values", str(chart_dir / "values-lab.yaml")],
        check=True,
        capture_output=True,
        text=True,
    )
    completed = subprocess.run(
        [
            str(HELM),
            "template",
            chart,
            str(chart_dir),
            "--values",
            str(chart_dir / "values-lab.yaml"),
            *extra_args,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    documents = [document for document in yaml.safe_load_all(completed.stdout) if document]
    assert documents
    assert all(isinstance(document, dict) for document in documents)
    return documents


def _one(documents: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    matches = [document for document in documents if document.get("kind") == kind]
    assert len(matches) == 1
    return matches[0]


def test_gateway_chart_renders_required_workloads_and_config() -> None:
    documents = _render("gateway")
    assert {document["kind"] for document in documents} >= {
        "Namespace",
        "ConfigMap",
        "Deployment",
        "Service",
        "ServiceMonitor",
    }
    service = _one(documents, "Service")
    assert service["spec"]["type"] == "ClusterIP"
    assert service["spec"]["ports"][0]["port"] == 8080
    config = _one(documents, "ConfigMap")["data"]
    assert {"providers.yaml", "routing.yaml", "auth.yaml", "slo.yaml"} <= set(config)
    container = _one(documents, "Deployment")["spec"]["template"]["spec"]["containers"][0]
    assert container["readinessProbe"]["httpGet"]["path"] == "/health/ready"
    assert container["livenessProbe"]["httpGet"]["path"] == "/health/live"


def test_gateway_chart_renders_digest_pinned_image() -> None:
    digest = f"sha256:{'a' * 64}"
    documents = _render("gateway", "--set-string", f"image.digest={digest}")
    container = _one(documents, "Deployment")["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == f"ghcr.io/example/inference-gateway@{digest}"


def test_gateway_chart_omits_empty_managed_primary_base_url() -> None:
    documents = _render("gateway")
    config = _one(documents, "ConfigMap")["data"]
    providers = yaml.safe_load(config["providers.yaml"])["providers"]
    container = _one(documents, "Deployment")["spec"]["template"]["spec"]["containers"][0]

    assert "base_url_env" not in providers["managed-primary"]
    assert "MANAGED_PRIMARY_BASE_URL" not in {entry["name"] for entry in container["env"]}


def test_gateway_chart_wires_configured_managed_primary_base_url() -> None:
    base_url = "http://faultmock.model-serving.svc.cluster.local:9401"
    documents = _render("gateway", "--set-string", f"config.managedPrimaryBaseUrl={base_url}")
    config = _one(documents, "ConfigMap")["data"]
    providers = yaml.safe_load(config["providers.yaml"])["providers"]
    container = _one(documents, "Deployment")["spec"]["template"]["spec"]["containers"][0]
    environment = {entry["name"]: entry for entry in container["env"]}

    assert providers["managed-primary"]["base_url_env"] == "MANAGED_PRIMARY_BASE_URL"
    assert environment["MANAGED_PRIMARY_BASE_URL"]["value"] == base_url


def test_gateway_chart_omits_empty_routing_override() -> None:
    documents = _render("gateway", "--set-string", "config.routingOverrideConfigMap=")
    config = _one(documents, "ConfigMap")["data"]
    pod_spec = _one(documents, "Deployment")["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]
    environment = {entry["name"]: entry for entry in container["env"]}

    assert config["GATEWAY_ROUTING_CONFIG"] == "/etc/gateway/routing.yaml"
    assert environment["GATEWAY_ROUTING_CONFIG"]["valueFrom"]["configMapKeyRef"] == {
        "name": "gateway-config",
        "key": "GATEWAY_ROUTING_CONFIG",
    }
    assert {entry["name"] for entry in container["volumeMounts"]} == {"config"}
    assert {entry["name"] for entry in pod_spec["volumes"]} == {"config"}


def test_gateway_chart_mounts_routing_override_config_map() -> None:
    override_name = "gateway-routing-run-0123456789ab"
    documents = _render(
        "gateway", "--set-string", f"config.routingOverrideConfigMap={override_name}"
    )
    config = _one(documents, "ConfigMap")["data"]
    pod_spec = _one(documents, "Deployment")["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]
    environment = {entry["name"]: entry for entry in container["env"]}
    volume_mounts = {entry["name"]: entry for entry in container["volumeMounts"]}
    volumes = {entry["name"]: entry for entry in pod_spec["volumes"]}

    assert config["GATEWAY_ROUTING_CONFIG"] == "/etc/gateway-treatment/routing.yaml"
    assert "value" not in environment["GATEWAY_ROUTING_CONFIG"]
    assert environment["GATEWAY_ROUTING_CONFIG"]["valueFrom"]["configMapKeyRef"] == {
        "name": "gateway-config",
        "key": "GATEWAY_ROUTING_CONFIG",
    }
    assert volume_mounts["routing-override"] == {
        "name": "routing-override",
        "mountPath": "/etc/gateway-treatment",
        "readOnly": True,
    }
    assert volumes["routing-override"]["configMap"]["name"] == override_name


def test_vllm_chart_is_private_and_requests_one_tolerated_gpu() -> None:
    documents = _render("vllm")
    assert {document["kind"] for document in documents} >= {
        "Namespace",
        "Deployment",
        "Service",
        "ServiceMonitor",
    }
    service = _one(documents, "Service")
    assert service["spec"]["type"] == "ClusterIP"
    pod_spec = _one(documents, "Deployment")["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]
    assert container["resources"]["requests"]["nvidia.com/gpu"] == 1
    assert container["resources"]["limits"]["nvidia.com/gpu"] == 1
    assert any(item["key"] == "nvidia.com/gpu" for item in pod_spec["tolerations"])
    assert "livenessProbe" not in container
    assert container["startupProbe"]["failureThreshold"] >= 60
    assert "--model" in container["args"]
    assert "--revision" in container["args"]
