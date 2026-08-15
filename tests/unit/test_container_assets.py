"""Static checks for the gateway image and in-cluster benchmark Job."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_dockerfile_is_versioned_and_non_root() -> None:
    dockerfile = ROOT / "Dockerfile"
    assert dockerfile.is_file()
    contents = dockerfile.read_text(encoding="utf-8")

    assert not re.search(r"^\s*ADD\s", contents, flags=re.MULTILINE | re.IGNORECASE)
    assert "FROM python:3.13-slim" in contents
    assert ":latest" not in contents.lower()

    users = re.findall(r"^\s*USER\s+(\S+)", contents, flags=re.MULTILINE | re.IGNORECASE)
    assert users
    assert users[-1].lower() not in {"0", "root", "0:0", "root:root"}


def test_dockerignore_excludes_repository_and_generated_results() -> None:
    entries = {
        line.strip().rstrip("/")
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert ".git" in entries
    assert "results/local" in entries
    assert "results/raw" in entries


def test_benchmark_runner_job_uses_required_namespace_and_placement() -> None:
    manifest = ROOT / "infra" / "k8s" / "benchmark-runner.yaml"
    documents: list[dict[str, Any]] = [
        document
        for document in yaml.safe_load_all(manifest.read_text(encoding="utf-8"))
        if document is not None
    ]

    namespace = next(document for document in documents if document["kind"] == "Namespace")
    job = next(document for document in documents if document["kind"] == "Job")

    assert namespace["metadata"]["name"] == "benchmark-jobs"
    assert job["metadata"]["namespace"] == "benchmark-jobs"
    pod_spec = job["spec"]["template"]["spec"]
    assert pod_spec["nodeSelector"] == {"workload": "system"}
    assert pod_spec["restartPolicy"] == "Never"
