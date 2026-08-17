from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from inference_gateway.benchmark.datasets import load_dataset
from inference_gateway.benchmark.manifest import (
    PublishabilityError,
    RepositoryState,
    build_manifest,
)
from inference_gateway.benchmark.models import load_scenario
from inference_gateway.benchmark.slo import SLODocument, load_slo

ROOT = Path(__file__).resolve().parents[2]
PLACEMENT_ENV = (
    "BENCHMARK_LOCATION",
    "BENCHMARK_NODE",
    "BENCHMARK_NODE_GROUP",
    "BENCHMARK_WORKLOAD_KIND",
    "BENCHMARK_AZ",
    "BENCHMARK_NETWORK_PATH",
)


def _write_deploy_manifest(path: Path) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "m5-v1",
                "environment": {"cloud": "aws", "region": "us-east-1"},
                "compute": {"gpu_count": 1},
                "runtime": {
                    "server_version": "0.27.1",
                    "image_digest": f"sha256:{'b' * 64}",
                },
                "model": {
                    "id": "Qwen/Qwen2.5-7B-Instruct-AWQ",
                    "revision": "c" * 40,
                    "license_note": "Apache-2.0",
                    "quantization": "awq",
                },
                "charts": {"vllm": "0.1.0"},
                "gateway": {
                    "image_repository": "ghcr.io/owner/inference-gateway",
                    "image_digest": f"sha256:{'d' * 64}",
                    "image": f"ghcr.io/owner/inference-gateway@sha256:{'d' * 64}",
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _set_cloud_placement(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {
        "BENCHMARK_LOCATION": "aws-eks-us-east-1",
        "BENCHMARK_NODE": "ip-10-0-1-12.ec2.internal",
        "BENCHMARK_NODE_GROUP": "system-20260815",
        "BENCHMARK_WORKLOAD_KIND": "kubernetes-job",
        "BENCHMARK_AZ": "us-east-1a",
        "BENCHMARK_NETWORK_PATH": "runner Pod -> gateway ClusterIP -> provider",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_cloud_treatment_scenarios_load_frozen_inputs_and_slo_cells() -> None:
    scenario_paths = sorted((ROOT / "benchmark/scenarios/cloud").glob("*.yaml"))

    assert [path.name for path in scenario_paths] == [
        "t0-managed-baseline.yaml",
        "t1-private-baseline.yaml",
        "t3-hybrid.yaml",
        "t4-failure.yaml",
    ]
    for scenario_path in scenario_paths:
        scenario = load_scenario(scenario_path)
        dataset = load_dataset(scenario.dataset, root=ROOT)
        slo, _ = load_slo(ROOT / scenario.slo_config)

        assert scenario.publishable is True
        assert scenario.repeats >= 3
        assert scenario.requests >= 200
        assert scenario.warmup_requests > 0
        assert dataset.version == "1.0.0"
        assert dataset.checksum
        assert slo.require_cell(scenario.slo_cell)


def test_slo_loader_has_spec_defaults_and_wl03_omits_quality() -> None:
    slo, checksum = load_slo(ROOT / "config/slo.example.yaml")
    assert len(checksum) == 64
    assert slo.require_cell("WL-01/balanced").p95_e2e_ms == 5000
    assert str(slo.require_cell("WL-02/premium").min_quality_rate) == "0.92"
    assert slo.require_cell("WL-03/balanced").min_quality_rate is None


def test_slo_lookup_fails_closed_when_cell_is_missing() -> None:
    slo, _ = load_slo(ROOT / "config/slo.example.yaml")
    with pytest.raises(ValueError, match="missing SLO target"):
        slo.require_cell("WL-01/not-defined")


def test_slo_rejects_quality_target_for_generation() -> None:
    with pytest.raises(ValueError, match="WL-03 cells must omit"):
        SLODocument.model_validate(
            {
                "version": "x",
                "effective_date": "2026-08-15",
                "cells": {
                    "WL-03/economy": {
                        "p95_ttft_ms": 1,
                        "p95_e2e_ms": 2,
                        "max_error_rate": "0.1",
                        "min_quality_rate": "0.1",
                    }
                },
            }
        )


def test_manifest_refuses_dirty_publishable_run_and_records_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deploy_path = tmp_path / "deploy-manifest.yaml"
    _write_deploy_manifest(deploy_path)
    monkeypatch.setenv("DEPLOY_MANIFEST", str(deploy_path))
    _set_cloud_placement(monkeypatch)
    scenario_path = ROOT / "benchmark/scenarios/classification-local.yaml"
    scenario = load_scenario(scenario_path).model_copy(update={"publishable": True})
    dataset = load_dataset(scenario.dataset, root=ROOT)
    slo, slo_hash = load_slo(ROOT / scenario.slo_config)
    repository = RepositoryState("a" * 40, True, "test")
    arguments = {
        "repository_root": ROOT,
        "scenario_path": scenario_path,
        "scenario": scenario,
        "dataset": dataset,
        "slo_document": slo,
        "slo_hash": slo_hash,
        "repository": repository,
        "started_at": datetime(2026, 8, 15, tzinfo=UTC),
    }
    with pytest.raises(PublishabilityError, match="clean tree"):
        build_manifest(**arguments)
    manifest = build_manifest(**arguments, allow_dirty=True)
    assert manifest["git"]["dirty"] is True
    assert manifest["publishability"]["dirty_override"] is True
    assert manifest["run_id"] == "20260815T000000Z-aaaaaaaa-t1-local-mock"


def test_publishable_manifest_refuses_missing_slo_cell() -> None:
    scenario_path = ROOT / "benchmark/scenarios/classification-local.yaml"
    scenario = load_scenario(scenario_path).model_copy(update={"publishable": True})
    dataset = load_dataset(scenario.dataset, root=ROOT)
    full_slo, slo_hash = load_slo(ROOT / scenario.slo_config)
    cells = dict(full_slo.cells)
    del cells[scenario.slo_cell]
    incomplete = full_slo.model_copy(update={"cells": cells})
    with pytest.raises(PublishabilityError, match="missing SLO target"):
        build_manifest(
            repository_root=ROOT,
            scenario_path=scenario_path,
            scenario=scenario,
            dataset=dataset,
            slo_document=incomplete,
            slo_hash=slo_hash,
            repository=RepositoryState("a" * 40, False, "test"),
        )


def test_manifest_consumes_immutable_deploy_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deploy_path = tmp_path / "deploy-manifest.yaml"
    _write_deploy_manifest(deploy_path)
    monkeypatch.setenv("DEPLOY_MANIFEST", str(deploy_path))
    _set_cloud_placement(monkeypatch)
    scenario_path = ROOT / "benchmark/scenarios/classification-local.yaml"
    scenario = load_scenario(scenario_path).model_copy(update={"publishable": True})
    dataset = load_dataset(scenario.dataset, root=ROOT)
    slo, slo_hash = load_slo(ROOT / scenario.slo_config)
    manifest = build_manifest(
        repository_root=ROOT,
        scenario_path=scenario_path,
        scenario=scenario,
        dataset=dataset,
        slo_document=slo,
        slo_hash=slo_hash,
        repository=RepositoryState("a" * 40, False, "test"),
    )

    assert manifest["model"]["revision"] == "c" * 40
    assert manifest["runtime"]["image_digest"] == f"sha256:{'b' * 64}"
    assert manifest["environment"]["cloud"] == "aws"
    assert len(manifest["deployment_manifest"]["sha256"]) == 64
    assert manifest["gateway"]["image_digest"] == f"sha256:{'d' * 64}"


def test_publishable_manifest_requires_deploy_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DEPLOY_MANIFEST", raising=False)
    _set_cloud_placement(monkeypatch)
    scenario_path = ROOT / "benchmark/scenarios/classification-local.yaml"
    scenario = load_scenario(scenario_path).model_copy(update={"publishable": True})
    dataset = load_dataset(scenario.dataset, root=ROOT)
    slo, slo_hash = load_slo(ROOT / scenario.slo_config)

    with pytest.raises(PublishabilityError, match="scripts/deploy.sh.*DEPLOY_MANIFEST"):
        build_manifest(
            repository_root=ROOT,
            scenario_path=scenario_path,
            scenario=scenario,
            dataset=dataset,
            slo_document=slo,
            slo_hash=slo_hash,
            repository=RepositoryState("a" * 40, False, "test"),
        )


def test_publishable_manifest_requires_non_local_placement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deploy_path = tmp_path / "deploy-manifest.yaml"
    _write_deploy_manifest(deploy_path)
    monkeypatch.setenv("DEPLOY_MANIFEST", str(deploy_path))
    for name in PLACEMENT_ENV:
        monkeypatch.delenv(name, raising=False)
    scenario_path = ROOT / "benchmark/scenarios/classification-local.yaml"
    scenario = load_scenario(scenario_path).model_copy(update={"publishable": True})
    dataset = load_dataset(scenario.dataset, root=ROOT)
    slo, slo_hash = load_slo(ROOT / scenario.slo_config)

    with pytest.raises(PublishabilityError) as captured:
        build_manifest(
            repository_root=ROOT,
            scenario_path=scenario_path,
            scenario=scenario,
            dataset=dataset,
            slo_document=slo,
            slo_hash=slo_hash,
            repository=RepositoryState("a" * 40, False, "test"),
        )

    message = str(captured.value)
    assert "BENCHMARK_LOCATION" in message
    assert "BENCHMARK_NODE" in message
    assert "BENCHMARK_WORKLOAD_KIND" in message


def test_publishable_manifest_records_placement_environment_verbatim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deploy_path = tmp_path / "deploy-manifest.yaml"
    _write_deploy_manifest(deploy_path)
    monkeypatch.setenv("DEPLOY_MANIFEST", str(deploy_path))
    _set_cloud_placement(monkeypatch)
    scenario_path = ROOT / "benchmark/scenarios/classification-local.yaml"
    scenario = load_scenario(scenario_path).model_copy(update={"publishable": True})
    dataset = load_dataset(scenario.dataset, root=ROOT)
    slo, slo_hash = load_slo(ROOT / scenario.slo_config)

    manifest = build_manifest(
        repository_root=ROOT,
        scenario_path=scenario_path,
        scenario=scenario,
        dataset=dataset,
        slo_document=slo,
        slo_hash=slo_hash,
        repository=RepositoryState("a" * 40, False, "test"),
    )

    assert manifest["harness"] == {
        "location": "aws-eks-us-east-1",
        "namespace": None,
        "workload_kind": "kubernetes-job",
        "image": None,
        "build": "a" * 40,
        "pod": None,
        "node": "ip-10-0-1-12.ec2.internal",
        "node_group": "system-20260815",
        "availability_zone": "us-east-1a",
        "network_path": "runner Pod -> gateway ClusterIP -> provider",
        "gateway_access": "in-process",
        "tls_mode": "none",
    }
    assert manifest["environment"]["availability_zone"] == "us-east-1a"


def test_nonpublishable_manifest_keeps_local_placement_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in PLACEMENT_ENV:
        monkeypatch.delenv(name, raising=False)
    scenario_path = ROOT / "benchmark/scenarios/classification-local.yaml"
    scenario = load_scenario(scenario_path)
    dataset = load_dataset(scenario.dataset, root=ROOT)
    slo, slo_hash = load_slo(ROOT / scenario.slo_config)

    manifest = build_manifest(
        repository_root=ROOT,
        scenario_path=scenario_path,
        scenario=scenario,
        dataset=dataset,
        slo_document=slo,
        slo_hash=slo_hash,
        repository=RepositoryState("a" * 40, False, "test"),
    )

    assert manifest["harness"]["location"] == "local-mock"
    assert manifest["harness"]["node"] == "local"
    assert manifest["harness"]["node_group"] is None
    assert manifest["harness"]["workload_kind"] == "local-process"
    assert manifest["harness"]["availability_zone"] is None
    assert (
        manifest["harness"]["network_path"]
        == "in-process client -> gateway ASGI -> deterministic adapter"
    )


def test_manifest_records_effective_provider_sampling() -> None:
    scenario_path = ROOT / "benchmark/scenarios/classification-local.yaml"
    scenario = load_scenario(scenario_path)
    dataset = load_dataset(scenario.dataset, root=ROOT)
    slo, slo_hash = load_slo(ROOT / scenario.slo_config)
    manifest = build_manifest(
        repository_root=ROOT,
        scenario_path=scenario_path,
        scenario=scenario,
        dataset=dataset,
        slo_document=slo,
        slo_hash=slo_hash,
        repository=RepositoryState("a" * 40, False, "test"),
    )

    economy = manifest["providers"]["managed-primary"]["models"]["lab-economy"]
    premium = manifest["providers"]["managed-primary"]["models"]["lab-premium"]
    assert economy["supports_sampling"] is True
    assert economy["effective_sampling"]["temperature"] == scenario.temperature
    assert premium["supports_sampling"] is False
    assert premium["effective_sampling"]["temperature"] is None
    assert "omitted" in premium["effective_sampling"]["note"]


def test_hybrid_manifest_embeds_every_traffic_slo_cell() -> None:
    scenario_path = ROOT / "benchmark/scenarios/hybrid-local.yaml"
    scenario = load_scenario(scenario_path)
    dataset = load_dataset(scenario.dataset, root=ROOT)
    slo, slo_hash = load_slo(ROOT / scenario.slo_config)

    manifest = build_manifest(
        repository_root=ROOT,
        scenario_path=scenario_path,
        scenario=scenario,
        dataset=dataset,
        slo_document=slo,
        slo_hash=slo_hash,
        repository=RepositoryState("a" * 40, False, "test"),
    )

    assert set(manifest["slo"]["cells"]) == {
        "WL-01/economy",
        "WL-01/balanced",
        "WL-01/premium",
    }
