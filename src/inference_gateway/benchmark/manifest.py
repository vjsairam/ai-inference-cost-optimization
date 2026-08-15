"""Pre-run immutable manifest construction and publishability gates."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import yaml

from inference_gateway.benchmark.datasets import DatasetBundle
from inference_gateway.benchmark.models import BenchmarkScenario
from inference_gateway.benchmark.slo import SLODocument, SLOTarget


class PublishabilityError(ValueError):
    """A run marked publishable lacks immutable evidence inputs."""


@dataclass(frozen=True, slots=True)
class RepositoryState:
    sha: str
    dirty: bool
    dirty_detection: str

    @classmethod
    def detect(cls, repository_root: Path) -> RepositoryState:
        """Read repository identity without invoking a version-control command.

        Dirty state must be supplied by the CI/operator environment. Unknown state
        fails closed as dirty, which is safe for publishable evidence.
        """
        sha = _read_head(repository_root / ".git")
        dirty_value = os.environ.get("BENCHMARK_TREE_DIRTY")
        if dirty_value is None:
            return cls(sha=sha, dirty=True, dirty_detection="unknown-fail-closed")
        normalized = dirty_value.strip().casefold()
        if normalized not in {"true", "false", "1", "0"}:
            raise PublishabilityError("BENCHMARK_TREE_DIRTY must be true or false")
        return cls(
            sha=sha,
            dirty=normalized in {"true", "1"},
            dirty_detection="operator-environment",
        )


def _read_head(git_dir: Path) -> str:
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref: "):
            ref = head.removeprefix("ref: ")
            candidate = (git_dir / ref).read_text(encoding="utf-8").strip()
        else:
            candidate = head
    except OSError:
        return "unknown"
    return candidate if re.fullmatch(r"[0-9a-f]{40,64}", candidate) else "unknown"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_deploy_manifest(repository_root: Path) -> tuple[Path, dict[str, Any]] | None:
    """Load the immutable runtime selection captured by scripts/deploy.sh."""
    configured_path = os.environ.get("DEPLOY_MANIFEST")
    if not configured_path:
        return None
    path = Path(configured_path)
    if not path.is_absolute():
        path = repository_root / path
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PublishabilityError(f"cannot load deploy manifest {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise PublishabilityError(f"deploy manifest {path} must contain a YAML mapping")
    manifest = cast(dict[str, Any], raw)
    model = manifest.get("model")
    runtime = manifest.get("runtime")
    if not isinstance(model, dict) or not re.fullmatch(
        r"[0-9a-f]{40}", str(model.get("revision", ""))
    ):
        raise PublishabilityError("deploy manifest model revision must be an immutable commit SHA")
    if not isinstance(runtime, dict) or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", str(runtime.get("image_digest", ""))
    ):
        raise PublishabilityError("deploy manifest runtime image_digest must be immutable")
    return path, manifest


def utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def make_run_id(started_at: datetime, sha: str, treatment: str) -> str:
    timestamp = started_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    short_sha = sha[:8] if sha != "unknown" else "unknown"
    return f"{timestamp}-{short_sha}-{treatment}"


def validate_publishability(
    scenario: BenchmarkScenario,
    repository: RepositoryState,
    dataset: DatasetBundle,
    slo: SLODocument,
    *,
    allow_dirty: bool,
) -> SLOTarget:
    if not dataset.checksum:
        raise PublishabilityError("dataset checksum is missing")
    try:
        target = slo.require_cell(scenario.slo_cell)
    except ValueError as exc:
        if scenario.publishable:
            raise PublishabilityError(str(exc)) from exc
        raise
    if scenario.publishable and repository.dirty and not allow_dirty:
        raise PublishabilityError(
            "publishable runs require a clean tree or an explicit dirty override"
        )
    return target


def build_manifest(
    *,
    repository_root: Path,
    scenario_path: Path,
    scenario: BenchmarkScenario,
    dataset: DatasetBundle,
    slo_document: SLODocument,
    slo_hash: str,
    repository: RepositoryState,
    allow_dirty: bool = False,
    started_at: datetime | None = None,
    operator_notes: list[str] | None = None,
) -> dict[str, Any]:
    """Build the manifest before any warm-up or measured request is sent."""
    target = validate_publishability(
        scenario, repository, dataset, slo_document, allow_dirty=allow_dirty
    )
    started = started_at or datetime.now(UTC)
    run_id = make_run_id(started, repository.sha, scenario.treatment)
    pricing_path = repository_root / scenario.pricing_config
    timeout_path = repository_root / scenario.timeout_config
    cost_path = repository_root / scenario.cost_config
    pricing_raw = yaml.safe_load(pricing_path.read_text(encoding="utf-8"))
    effective_dates = sorted(
        {
            str(model["effective_date"])
            for provider in pricing_raw.get("pricing", {}).values()
            for model in provider.values()
        }
    )
    manifest: dict[str, Any] = {
        "run_id": run_id,
        "started_at": utc_text(started),
        "completed_at": None,
        "publishable": scenario.publishable,
        "publishability": {
            "dirty_override": bool(repository.dirty and allow_dirty),
            "dataset_checksum_verified": True,
            "slo_cell_present": True,
        },
        "git": {
            "sha": repository.sha,
            "dirty": repository.dirty,
            "dirty_detection": repository.dirty_detection,
        },
        "scenario": scenario.model_dump(mode="json", by_alias=True),
        "scenario_path": str(scenario_path.relative_to(repository_root)),
        "environment": {
            "cloud": None,
            "region": None,
            "availability_zone": None,
            "kubernetes_version": None,
            "node_os": None,
        },
        "harness": {
            "location": "local-mock",
            "namespace": None,
            "workload_kind": "local-process",
            "image": None,
            "build": repository.sha,
            "pod": None,
            "node": "local",
            "node_group": None,
            "availability_zone": None,
            "network_path": "in-process client -> gateway ASGI -> deterministic adapter",
            "gateway_access": "in-process",
            "tls_mode": "none",
        },
        "compute": {
            "instance_type": None,
            "gpu_model": None,
            "gpu_count": 0,
            "driver_cuda": None,
            "purchase_option": None,
        },
        "runtime": {"server_version": None, "image_digest": None},
        "model": {
            "id": scenario.model,
            "revision": "local-mock-v1",
            "license_note": "local deterministic fixture",
            "dtype": None,
            "quantization": None,
            "tensor_parallel": None,
        },
        "workload": {
            "dataset": dataset.name,
            "dataset_version": dataset.version,
            "dataset_sha256": dataset.checksum,
            "concurrency": scenario.concurrency,
            "request_count_per_repeat": scenario.requests,
            "request_rate_schedule": scenario.request_rate_schedule,
            "stream": scenario.stream,
            "structured_output_mode": scenario.structured_output_mode,
            "input_output_token_profile": scenario.sampling_parameters.get(
                "token_profile", "dataset-defined"
            ),
            "temperature": scenario.temperature,
            "max_tokens": scenario.max_tokens,
            "sampling_parameters": scenario.sampling_parameters,
            "warmup_rule": {
                "requests_per_repeat": scenario.warmup_requests,
                "excluded_from_measurement": True,
            },
            "cache_state": "adapter-default",
            "autoscaling": {"enabled": False, "min": 1, "max": 1},
            "failure_injection": False,
        },
        "pricing": {
            "version": scenario.pricing_version,
            "config_sha256": sha256_file(pricing_path),
            "effective_dates": effective_dates,
        },
        "cost": {
            "config_sha256": sha256_file(cost_path),
            "config_path": scenario.cost_config,
        },
        "slo": {
            "version": slo_document.version,
            "config_sha256": slo_hash,
            "cell": scenario.slo_cell,
            "target": target.model_dump(mode="json"),
        },
        "timeouts": {
            "config_sha256": sha256_file(timeout_path),
            "config_path": scenario.timeout_config,
        },
        "statistics": {
            "repeat_group_id": run_id,
            "repeat_count": scenario.repeats,
            "repeat_indices": list(range(1, scenario.repeats + 1)),
            "execution_order": "fixed local treatment; frozen item blocks",
            "analysis_version": "m2-v1",
            "bootstrap_iterations": scenario.bootstrap_iterations,
            "bootstrap_seed": scenario.seed,
        },
        "measurement": {
            "planned_sample_count": scenario.requests * scenario.repeats,
            "actual_sample_count": None,
            "duration_seconds": None,
        },
        "policy": {"config_sha256": sha256_file(timeout_path)},
        "operator_notes": operator_notes or ([scenario.notes] if scenario.notes else []),
    }
    captured_deployment = load_deploy_manifest(repository_root)
    if captured_deployment is not None:
        deploy_path, deployment = captured_deployment
        for section in ("environment", "compute", "runtime", "model"):
            values = deployment.get(section)
            if isinstance(values, dict):
                manifest[section].update(values)
        manifest["deployment_manifest"] = {
            "path": str(deploy_path),
            "sha256": sha256_file(deploy_path),
            "schema_version": deployment.get("schema_version"),
        }
        manifest["charts"] = deployment.get("charts", {})
        manifest["gateway"] = deployment.get("gateway", {})
    return manifest


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=False)
    path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")


def complete_manifest(path: Path, completed_at: datetime, actual_sample_count: int) -> None:
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    manifest["completed_at"] = utc_text(completed_at)
    started_at = datetime.fromisoformat(str(manifest["started_at"]).replace("Z", "+00:00"))
    manifest["measurement"]["actual_sample_count"] = actual_sample_count
    manifest["measurement"]["duration_seconds"] = (
        completed_at.astimezone(UTC) - started_at.astimezone(UTC)
    ).total_seconds()
    path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
