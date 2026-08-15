from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import httpx

from inference_gateway.api import create_app
from inference_gateway.benchmark.datasets import load_dataset
from inference_gateway.benchmark.harness import BenchmarkHarness
from inference_gateway.benchmark.local import local_adapters
from inference_gateway.benchmark.manifest import RepositoryState
from inference_gateway.benchmark.models import load_scenario
from inference_gateway.benchmark.report import build_report

ROOT = Path(__file__).resolve().parents[2]


async def test_full_local_mock_run_produces_cost_per_correct_report(
    tmp_path: Path, gateway_config, auth_config, lab_api_key: str
) -> None:
    scenario_path = ROOT / "benchmark/scenarios/classification-local.yaml"
    scenario = load_scenario(scenario_path).model_copy(
        update={
            "requests": 3,
            "warmup_requests": 1,
            "repeats": 3,
            "bootstrap_iterations": 100,
        }
    )
    dataset = load_dataset(scenario.dataset, root=ROOT)
    app = create_app(gateway_config, auth_config, adapters=local_adapters(dataset))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway.test"
    ) as client:
        harness = BenchmarkHarness(
            repository_root=ROOT,
            scenario_path=scenario_path,
            base_url="http://gateway.test",
            api_key=lab_api_key,
            client=client,
            results_root=tmp_path,
            repository_state=RepositoryState("b" * 40, True, "test"),
        )
        run_dir = await harness.run(scenario)
    summary = build_report(run_dir, ROOT)
    expected_files = {
        "manifest.yaml",
        "records.jsonl",
        "summary.json",
        "quality.json",
        "cost.json",
        "comparison.csv",
        "scenario-grid.json",
    }
    assert expected_files.issubset({path.name for path in run_dir.iterdir()})
    assert summary["requests"] == 9
    assert summary["quality"]["quality_rate"] == 1.0
    cost = json.loads((run_dir / "cost.json").read_text())
    private = cost["views"]["view_a"]["private"]
    assert Decimal(private["cost_per_correct_task_usd"]) > 0
    assert set(cost["views"]["view_b"]["operations_sensitivity"]) == {
        "low",
        "typical",
        "high",
    }
