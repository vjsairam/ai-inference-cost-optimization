from __future__ import annotations

from inference_gateway.benchmark.evaluators import (
    classification_metrics,
    evaluate_classification,
    evaluate_extraction,
)


def test_classification_normalized_exact_match_and_rejects_extra_text() -> None:
    assert evaluate_classification("  SÉCURITY. ", "sécurity").task_correct
    assert not evaluate_classification("security issue", "security").task_correct


def test_classification_precision_recall_f1_and_confusion() -> None:
    metrics = classification_metrics(
        ["access", "access", "billing", "billing"],
        ["access", "billing", "billing", "billing"],
        ["access", "billing"],
    )
    assert metrics["accuracy"] == 0.75
    assert metrics["per_class"]["access"]["recall"] == 0.5
    assert metrics["per_class"]["billing"]["precision"] == 2 / 3
    assert metrics["confusion"]["access"]["billing"] == 1


def test_extraction_normalization_missing_and_extra_fields() -> None:
    expected = {
        "incident_id": "INC-1",
        "severity": "High",
        "service": "CheckOut",
        "opened_at": "2026-08-15T00:00:00Z",
        "owner": None,
    }
    normalized = evaluate_extraction(
        '{"incident_id":" inc-1 ","severity":"HIGH","service":"checkout",'
        '"opened_at":"2026-08-15T00:00:00Z","owner":null}',
        expected,
    )
    assert normalized.task_correct
    assert normalized.score["whole_record_correct"] is True
    extra = evaluate_extraction(
        '{"incident_id":"INC-1","severity":"high","service":"checkout",'
        '"opened_at":"2026-08-15T00:00:00Z","owner":null,"invoice":"x"}',
        expected,
    )
    assert extra.task_correct
    assert extra.score["whole_record_correct"] is False
    missing = evaluate_extraction('{"incident_id":"INC-1"}', expected)
    assert not missing.task_correct
    assert 0 < missing.score["field_f1"] < 1


def test_extraction_invalid_json_scores_zero() -> None:
    result = evaluate_extraction("not-json", {"incident_id": "INC-1"})
    assert not result.task_correct
    assert result.score == {
        "json_valid": False,
        "required_field_exact_match": False,
        "field_f1": 0.0,
        "whole_record_correct": False,
        "field_matches": {},
    }
