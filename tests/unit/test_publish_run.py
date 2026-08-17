from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts/publish-run.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("publish_run_script", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


PUBLISH_RUN = _load_script()


def _fake_run(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    run_dir = tmp_path / "completed-run"
    run_dir.mkdir()
    manifest: dict[str, Any] = {
        "run_id": "run-001",
        "git": {"sha": "abc123"},
        "scenario": {"publishable": True},
        "harness": {"location": "aws-eks"},
    }
    summary = {
        "run_id": "run-001",
        "treatment": "t1-private-baseline",
        "workload": "classification",
        "sample_size": 2,
        "quality": {"quality_rate": 0.5},
        "slo": {"slo_eligible": True},
        "harness": {
            "location": "aws-eks",
            "node_group": "system",
            "availability_zone": "us-east-1b",
        },
        "cost": {
            "observed_path_applicability": {"managed": False, "private": True},
            "views": {
                "view_a": {
                    "managed": {"cost_per_correct_task_usd": None},
                    "private": {"cost_per_correct_task_usd": "0.0001234567890123456789"},
                }
            },
            "hybrid_combined_view_a": {"cost_per_correct_task_usd": "0.0001234567890123456789"},
        },
        "repeats": {
            "values": [
                {
                    "repeat_index": 1,
                    "quality_rate": 0.5,
                    "view_a_managed_cost_per_correct_task_usd": None,
                    "view_a_private_cost_per_correct_task_usd": ("0.0001234567890123456789"),
                },
                {
                    "repeat_index": 2,
                    "quality_rate": None,
                    "view_a_managed_cost_per_correct_task_usd": None,
                    "view_a_private_cost_per_correct_task_usd": None,
                },
            ]
        },
        "limitations": [
            "Measured in one region.",
            "No treatment comparison was run.",
        ],
    }
    (run_dir / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    (run_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (run_dir / "quality.json").write_bytes(b'{"quality": "verbatim"}\n')
    (run_dir / "cost.json").write_bytes(b'{"cost": "verbatim"}\n')
    (run_dir / "comparison.csv").write_bytes(b"metric,value\nquality,0.5\n")
    (run_dir / "records.jsonl").write_bytes(b'{"record": 1}\n{"record": 2}\n')
    return run_dir, manifest


def test_publish_run_assembles_expected_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir, _ = _fake_run(tmp_path)
    comparison = tmp_path / "source-comparison.json"
    comparison.write_bytes(b'{"schema_version": "v1"}\n')
    output_root = tmp_path / "published"

    result = PUBLISH_RUN.main(
        [str(run_dir), "--output", str(output_root), "--comparison", str(comparison)]
    )

    assert result == 0
    destination = output_root / "run-001"
    assert {
        path.relative_to(destination).as_posix()
        for path in destination.rglob("*")
        if path.is_file()
    } == {
        "README.md",
        "charts/cost.svg",
        "charts/quality.svg",
        "comparison.csv",
        "comparison.json",
        "cost.json",
        "manifest.yaml",
        "quality.json",
        "raw-reference.yaml",
        "summary.json",
    }
    assert not (destination / "records.jsonl").exists()
    for name in ("manifest.yaml", "summary.json", "quality.json", "cost.json", "comparison.csv"):
        assert (destination / name).read_bytes() == (run_dir / name).read_bytes()
    assert (destination / "comparison.json").read_bytes() == comparison.read_bytes()

    reference = yaml.safe_load((destination / "raw-reference.yaml").read_text(encoding="utf-8"))
    records = (run_dir / "records.jsonl").read_bytes()
    assert reference["records_file"] == "records.jsonl"
    assert reference["sha256"] == hashlib.sha256(records).hexdigest()
    assert reference["record_count"] == 2
    assert reference["record_schema_version"] == "benchmark-record-v1.schema.json"
    assert reference["storage_location"] == (
        "repository archive under results/raw/run-001/ (operator retained)"
    )
    assert reference["access_note"]

    readme = (destination / "README.md").read_text(encoding="utf-8")
    assert "## Interpretation\n\nWritten by the operator during review." in readme
    assert "Measured in one region.\n\nNo treatment comparison was run." in readme
    assert (
        "- Placement: location aws-eks; node group system; availability zone us-east-1b" in readme
    )
    quality_chart = (destination / "charts/quality.svg").read_text(encoding="utf-8")
    cost_chart = (destination / "charts/cost.svg").read_text(encoding="utf-8")
    assert 'width="600" height="300"' in quality_chart
    assert "Repeat 1" in quality_chart
    assert "Overall" in quality_chart
    assert "not computed" in quality_chart
    assert "0.0001234567890123456789" in cost_chart
    assert "$0.000123456789..." in cost_chart
    assert "not computed" in cost_chart

    output = capsys.readouterr().out
    assert f"Created: {destination}" in output
    assert "  charts/cost.svg" in output
    assert "  raw-reference.yaml" in output


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            lambda manifest: manifest["scenario"].update(publishable=False),
            "scenario.publishable must be true",
        ),
        (
            lambda manifest: manifest["git"].update(sha="unknown"),
            "manifest git.sha must be known",
        ),
        (
            lambda manifest: manifest["harness"].update(location="local-mock"),
            "manifest harness.location must not be local-mock",
        ),
    ],
)
def test_publish_run_refuses_ineligible_manifest(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    change: Callable[[dict[str, Any]], None],
    message: str,
) -> None:
    run_dir, manifest = _fake_run(tmp_path)
    change(manifest)
    (run_dir / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    output_root = tmp_path / "published"

    assert PUBLISH_RUN.main([str(run_dir), "--output", str(output_root)]) == 1

    assert message in capsys.readouterr().err
    assert not (output_root / "run-001").exists()


def test_publish_run_refuses_existing_destination(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir, _ = _fake_run(tmp_path)
    destination = tmp_path / "published" / "run-001"
    destination.mkdir(parents=True)
    marker = destination / "keep.txt"
    marker.write_text("operator content\n", encoding="utf-8")

    assert PUBLISH_RUN.main([str(run_dir), "--output", str(destination.parent)]) == 1

    assert "destination already exists" in capsys.readouterr().err
    assert marker.read_text(encoding="utf-8") == "operator content\n"
