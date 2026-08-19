from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CLOUD_UP = ROOT / "scripts/cloud-up.sh"


def _run_cloud_up(*arguments: str, path: str | None = None) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["ENV"] = "aws-lab"
    if path is not None:
        environment["PATH"] = path
    return subprocess.run(
        [str(CLOUD_UP), *arguments],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_cloud_up_rejects_past_utc_expiry() -> None:
    result = _run_cloud_up("--run-budget-usd", "1", "--expires-at", "2026-08-14")

    assert result.returncode == 2
    assert "EXPIRES_AT must be today or later in UTC" in result.stderr


def test_cloud_up_rejects_budget_above_remaining_spend_envelope() -> None:
    future = (datetime.now(UTC).date() + timedelta(days=1)).isoformat()
    result = _run_cloud_up("--run-budget-usd", "500.01", "--expires-at", future)

    assert result.returncode == 2
    assert "remaining project spend envelope of USD 500.00" in result.stderr


def test_cloud_up_prints_remaining_spend_envelope_in_plan(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for command in ("aws", "terraform"):
        executable = fake_bin / command
        executable.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)
    future = (datetime.now(UTC).date() + timedelta(days=1)).isoformat()

    result = _run_cloud_up(
        "--run-budget-usd",
        "25",
        "--expires-at",
        future,
        "--plan-only",
        path=f"{fake_bin}:{os.environ['PATH']}",
    )

    assert result.returncode == 0, result.stderr
    assert "Remaining project spend envelope: USD 500.00" in result.stdout


def test_cloud_up_requires_explicit_two_gpu_acknowledgement() -> None:
    future = (datetime.now(UTC).date() + timedelta(days=1)).isoformat()

    result = _run_cloud_up(
        "--run-budget-usd",
        "25",
        "--expires-at",
        future,
        "--gpu-node-count",
        "2",
        "--plan-only",
    )

    assert result.returncode == 2
    assert "AUTOSCALE_CAPACITY=acknowledged or --autoscale-capacity" in result.stderr


def test_cloud_up_accepts_acknowledged_two_gpu_plan_and_multiplies_cost(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for command in ("aws", "terraform"):
        executable = fake_bin / command
        executable.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)
    future = (datetime.now(UTC).date() + timedelta(days=1)).isoformat()

    result = _run_cloud_up(
        "--run-budget-usd",
        "25",
        "--expires-at",
        future,
        "--gpu-node-count",
        "2",
        "--autoscale-capacity",
        "--plan-only",
        path=f"{fake_bin}:{os.environ['PATH']}",
    )

    assert result.returncode == 0, result.stderr
    assert "GPU node count: 2" in result.stdout
    assert "Autoscale capacity gate: acknowledged" in result.stdout
    assert "Estimated hourly cost: USD 1.7962" in result.stdout


def test_spend_envelope_is_versioned_and_requires_adr_for_changes() -> None:
    envelope = yaml.safe_load((ROOT / "config/spend-envelope.yaml").read_text(encoding="utf-8"))

    assert envelope == {
        "version": "v1",
        "approved_total_usd": "500",
        "currency": "USD",
        "effective_date": "2026-08-15",
        "spent_to_date_usd": "0",
        "note": "Changing this envelope requires an ADR.",
    }


def test_deploy_requires_and_records_gateway_image_digest() -> None:
    deploy = (ROOT / "scripts/deploy.sh").read_text(encoding="utf-8")

    assert "require_value GATEWAY_IMAGE_REPOSITORY" in deploy
    assert "require_value GATEWAY_IMAGE_DIGEST" in deploy
    assert '--set-string image.digest="$GATEWAY_IMAGE_DIGEST"' in deploy
    assert "image_repository" in deploy
    assert "image_digest" in deploy
    assert "GATEWAY_IMAGE_REPOSITORY:-" not in deploy


def test_deploy_keeps_keda_behind_two_gpu_opt_in() -> None:
    deploy = (ROOT / "scripts/deploy.sh").read_text(encoding="utf-8")
    versions = yaml.safe_load((ROOT / "infra/helm/versions.yaml").read_text(encoding="utf-8"))

    assert versions["charts"]["keda"] == "2.20.2"
    assert "DEPLOY_AUTOSCALE" in deploy
    assert "requires exactly two Ready GPU nodes" in deploy
    assert "helm upgrade --install keda kedacore/keda" in deploy
    assert 'kubectl apply -f "$repo_root/infra/k8s/vllm-scaledobject.yaml"' in deploy


def test_vllm_scaledobject_uses_pinned_queue_contract() -> None:
    scaled_object = yaml.safe_load(
        (ROOT / "infra/k8s/vllm-scaledobject.yaml").read_text(encoding="utf-8")
    )
    spec = scaled_object["spec"]
    metadata = spec["triggers"][0]["metadata"]

    assert scaled_object["metadata"]["namespace"] == "model-serving"
    assert spec["scaleTargetRef"]["name"] == "vllm"
    assert spec["minReplicaCount"] == 1
    assert spec["maxReplicaCount"] == 2
    assert spec["triggers"][0]["metricType"] == "AverageValue"
    assert metadata["serverAddress"] == (
        "http://kube-prometheus-stack-prometheus.monitoring.svc:9090"
    )
    assert "vllm:num_requests_waiting" in metadata["query"]
