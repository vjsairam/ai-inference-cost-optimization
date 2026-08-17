"""Closed-set answer extraction and fenced-JSON handling.

These rules exist because real managed models wrap correct answers in
markdown, restate them in sentences, or fence JSON, and scoring must stay
deterministic and provider-neutral.
"""

from __future__ import annotations

from inference_gateway.benchmark.evaluators import (
    evaluate_classification,
    evaluate_extraction,
)

LABELS = (
    "access",
    "billing",
    "delivery",
    "hardware",
    "network",
    "security",
    "software",
    "storage",
)


def test_plain_answer_still_exact() -> None:
    result = evaluate_classification("hardware", "hardware", LABELS)
    assert result.task_correct
    assert result.score["extraction_method"] == "exact"


def test_markdown_wrapped_answer_matches() -> None:
    result = evaluate_classification("**hardware**", "hardware", LABELS)
    assert result.task_correct


def test_sentence_answer_extracts_label() -> None:
    result = evaluate_classification("The correct category is network.", "network", LABELS)
    assert result.task_correct
    assert result.score["extraction_method"] == "closed-set-line-extraction"


def test_multi_line_answer_uses_final_line() -> None:
    output = "The subject mentions storage, but the issue is different.\n\n**network**"
    result = evaluate_classification(output, "network", LABELS)
    assert result.task_correct


def test_ambiguous_final_line_fails() -> None:
    output = "It could be network or storage."
    result = evaluate_classification(output, "network", LABELS)
    assert not result.task_correct


def test_wrong_label_still_wrong() -> None:
    result = evaluate_classification("The category is billing.", "network", LABELS)
    assert not result.task_correct


def test_empty_output_fails() -> None:
    result = evaluate_classification("", "network", LABELS)
    assert not result.task_correct


def test_no_labels_keeps_strict_behavior() -> None:
    result = evaluate_classification("The correct category is network.", "network")
    assert not result.task_correct


def test_extraction_accepts_fenced_json() -> None:
    output = '```json\n{"name": "alice", "team": "core"}\n```'
    result = evaluate_extraction(output, {"name": "alice", "team": "core"})
    assert result.task_correct
    assert result.score["json_valid"]


def test_extraction_accepts_prefixed_fenced_json() -> None:
    output = 'Here is the record:\n```json\n{"name": "alice"}\n```\nDone.'
    result = evaluate_extraction(output, {"name": "alice"})
    assert result.task_correct


def test_extraction_plain_json_unchanged() -> None:
    result = evaluate_extraction('{"name": "alice"}', {"name": "alice"})
    assert result.task_correct


def test_extraction_invalid_json_still_fails() -> None:
    result = evaluate_extraction("not json at all", {"name": "alice"})
    assert not result.task_correct
    assert not result.score["json_valid"]
