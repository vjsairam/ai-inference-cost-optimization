"""Deterministic workload evaluators with auditable normalization."""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Any


def normalize_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value)).strip().casefold()
    return re.sub(r"\s+", " ", text)


@dataclass(frozen=True, slots=True)
class Evaluation:
    task_correct: bool
    score: dict[str, Any]


_TOKEN_STRIP = " .,:;!?\"'`*_#()[]{}<>"
_NEGATION_PATTERN = re.compile(
    r"\b(not|no|never|none|is not|isn't|was not|wasn't|ruled out|rather than|instead of|except)\b"
)


def _extract_label(output: str, labels: tuple[str, ...]) -> str | None:
    """Deterministic closed-set answer extraction.

    Walk lines from the end of the response. The first non-empty line that
    mentions exactly one known label yields that label, unless the line also
    contains a negation cue and is not the bare label. A line mentioning
    several labels is ambiguous and stops extraction. Applies identically to
    every provider.
    """
    label_set = {normalize_text(label) for label in labels}
    for raw_line in reversed(str(output).splitlines()):
        line = normalize_text(raw_line)
        if not line:
            continue
        words = {word.strip(_TOKEN_STRIP) for word in line.split(" ")}
        found = words & label_set
        if len(found) == 1:
            label = found.pop()
            if line.strip(_TOKEN_STRIP) != label and _NEGATION_PATTERN.search(line):
                return None
            return label
        if found:
            return None
    return None


def evaluate_classification(
    output: str, expected: str, labels: tuple[str, ...] | None = None
) -> Evaluation:
    predicted = normalize_text(output).strip(_TOKEN_STRIP)
    truth = normalize_text(expected)
    method = "bare"
    if labels and predicted not in {normalize_text(label) for label in labels}:
        extracted = _extract_label(output, labels)
        if extracted is not None:
            predicted = extracted
            method = "closed-set-line-extraction"
        else:
            method = "no-label-found"
    correct = predicted == truth
    return Evaluation(
        task_correct=correct,
        score={
            "normalized_prediction": predicted,
            "expected": truth,
            "exact_match": correct and method == "bare",
            "extraction_method": method,
            "raw_output_prefix": str(output)[:240],
        },
    )


def classification_metrics(
    expected: list[str], predicted: list[str], labels: list[str] | None = None
) -> dict[str, Any]:
    if len(expected) != len(predicted):
        raise ValueError("expected and predicted lengths differ")
    truths = [normalize_text(value) for value in expected]
    guesses = [normalize_text(value).strip(" .,:;\"'`") for value in predicted]
    effective_labels = labels or sorted(set(truths) | set(guesses))
    confusion: dict[str, dict[str, int]] = {
        label: {other: 0 for other in effective_labels} for label in effective_labels
    }
    unknown: Counter[str] = Counter()
    for truth, guess in zip(truths, guesses, strict=True):
        if guess in confusion[truth]:
            confusion[truth][guess] += 1
        else:
            unknown[guess] += 1
    per_class: dict[str, dict[str, float | int]] = {}
    for label in effective_labels:
        true_positive = confusion[label][label]
        false_positive = sum(
            confusion[other][label] for other in effective_labels if other != label
        )
        false_negative = sum(
            confusion[label][other] for other in effective_labels if other != label
        )
        precision = (
            true_positive / (true_positive + false_positive)
            if true_positive + false_positive
            else 0.0
        )
        recall = (
            true_positive / (true_positive + false_negative)
            if true_positive + false_negative
            else 0.0
        )
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[label] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": truths.count(label),
        }
    correct = sum(truth == guess for truth, guess in zip(truths, guesses, strict=True))
    return {
        "accuracy": correct / len(truths) if truths else 0.0,
        "per_class": per_class,
        "confusion": confusion,
        "unknown_predictions": dict(unknown),
    }


def _normalize_value(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, str):
        return normalize_text(value)
    if isinstance(value, (int, bool)):
        return value
    return normalize_text(value)


_FENCE_PATTERN = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def _strip_code_fence(output: str) -> str:
    """Return the body of the last fenced block that parses as JSON.

    The last parsing block wins so that a stated final answer beats earlier
    drafts, matching the classification rule of scoring the end of the
    response. Without any parsing fenced block the text is returned unchanged.
    """
    for body in reversed(_FENCE_PATTERN.findall(output)):
        try:
            json.loads(body)
        except json.JSONDecodeError:
            continue
        return str(body)
    return output


def evaluate_extraction(output: str, expected: dict[str, object]) -> Evaluation:
    try:
        parsed = json.loads(_strip_code_fence(output))
    except json.JSONDecodeError:
        return Evaluation(
            task_correct=False,
            score={
                "json_valid": False,
                "required_field_exact_match": False,
                "field_f1": 0.0,
                "whole_record_correct": False,
                "field_matches": {},
            },
        )
    if not isinstance(parsed, dict):
        return Evaluation(
            task_correct=False,
            score={
                "json_valid": True,
                "required_field_exact_match": False,
                "field_f1": 0.0,
                "whole_record_correct": False,
                "field_matches": {},
            },
        )
    field_matches = {
        key: key in parsed and _normalize_value(parsed[key]) == _normalize_value(value)
        for key, value in expected.items()
    }
    true_positive = sum(field_matches.values())
    false_negative = len(expected) - true_positive
    false_positive = sum(
        1 for key in parsed if key not in expected or not field_matches.get(key, False)
    )
    precision = (
        true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    )
    recall = (
        true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
    )
    field_f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    required_exact = all(field_matches.values())
    whole_record = required_exact and set(parsed) == set(expected)
    return Evaluation(
        task_correct=required_exact,
        score={
            "json_valid": True,
            "required_field_exact_match": required_exact,
            "field_precision": precision,
            "field_recall": recall,
            "field_f1": field_f1,
            "whole_record_correct": whole_record,
            "field_matches": field_matches,
        },
    )
