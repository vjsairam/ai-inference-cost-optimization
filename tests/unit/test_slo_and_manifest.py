from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from inference_gateway.benchmark.datasets import load_dataset
from inference_gateway.benchmark.manifest import (
    PublishabilityError,
    RepositoryState,
    build_manifest,
)
from inference_gateway.benchmark.models import load_scenario
from inference_gateway.benchmark.slo import SLODocument, load_slo

ROOT = Path(__file__).resolve().parents[2]


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


def test_manifest_refuses_dirty_publishable_run_and_records_override() -> None:
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
