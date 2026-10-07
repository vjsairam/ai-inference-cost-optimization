from __future__ import annotations

import importlib.util
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts/decision-brief-charts.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("decision_brief_charts_script", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CHARTS = _load_script()


def test_committed_charts_match_published_runs() -> None:
    rendered = CHARTS.render(ROOT / "results/published")

    assert set(rendered) == {"quality-cost.svg", "monthly-cost.svg"}
    for name, svg in rendered.items():
        ElementTree.fromstring(svg)
        committed = (ROOT / "docs/decision-brief" / name).read_text(encoding="utf-8")
        assert svg == committed, f"{name} is stale; rerun scripts/decision-brief-charts.py"


def test_brief_runs_cover_both_workloads() -> None:
    points = CHARTS.measured_points(ROOT / "results/published")

    assert [(point["workload"], point["treatment"]) for point in points] == [
        ("classification", "t0"),
        ("classification", "t1"),
        ("classification", "t3"),
        ("structured-extraction", "t0"),
        ("structured-extraction", "t1"),
    ]


def test_private_cost_steps_up_when_a_replica_fills() -> None:
    path = CHARTS.private_steps(500_000, 579.456, 10_000, 1_000_000)

    assert [volume for volume, _ in path] == [10_000, 500_000, 500_000, 1_000_000]
    assert [cost for _, cost in path] == pytest.approx([579.456, 579.456, 1158.912, 1158.912])
    assert len(CHARTS.private_steps(100_000, 1.0, 10_000, 1_000_000)) == 2 + 2 * 9


def test_monthly_series_refuses_a_changed_cost_config(tmp_path: Path) -> None:
    changed = tmp_path / "cost.yaml"
    changed.write_text(CHARTS.COST_CONFIG.read_text(encoding="utf-8") + "# edited\n")

    with pytest.raises(SystemExit, match="no longer matches"):
        CHARTS.monthly_series(ROOT / "results/published", changed)
