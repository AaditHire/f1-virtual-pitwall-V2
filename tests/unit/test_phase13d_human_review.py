from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts import import_phase13d_experiment_a_human_review_xlsx as review_import
from scripts.phase13d_experiment_a_human_review import (
    EXPECTED_ROWS,
    GROUNDING_VALUES,
    HEADERS,
    MISLEADING_VALUES,
    USEFULNESS_VALUES,
    build_payload,
)


def completed_values() -> list[list]:
    payload = build_payload()
    values = [payload["headers"]]
    for row in payload["rows"]:
        values.append([*row[:10], "PASS", "GOOD", "NO", "Reviewed."])
    return values


def install_inspection(monkeypatch, values: list[list]) -> None:
    monkeypatch.setattr(
        review_import,
        "inspect_workbook",
        lambda _path: {"review_values": values},
    )


def test_frozen_review_packet_is_exact_complete_and_semantically_blank():
    payload = build_payload()
    assert payload["review_rows"] == EXPECTED_ROWS == 169
    assert payload["headers"] == HEADERS
    assert payload["sampling_shortfall"] is True
    assert payload["available_additional_valid_pairs"] == 8
    assert payload["composition"]["routes"] == {"RAG_ONLY": 119, "MIXED": 50}
    assert payload["composition"]["blinded_arms"] == {"ARM_A": 100, "ARM_B": 69}
    assert len({row[0] for row in payload["rows"]}) == EXPECTED_ROWS
    assert all(row[10:14] == [None, None, None, None] for row in payload["rows"])
    rendered = json.dumps(payload["rows"])
    assert "CONTROL_320" not in rendered
    assert "TREATMENT_640" not in rendered
    assert "max_output_tokens" not in rendered


def test_completed_review_validation_accepts_only_exact_population(monkeypatch):
    values = completed_values()
    install_inspection(monkeypatch, values)
    reviews = review_import.validate(Path("review.xlsx"))
    assert len(reviews) == EXPECTED_ROWS
    assert all(row["grounding"] in GROUNDING_VALUES for row in reviews)
    assert all(row["usefulness"] in USEFULNESS_VALUES for row in reviews)
    assert all(row["misleading"] in MISLEADING_VALUES for row in reviews)


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        (10, "", "invalid or missing grounding"),
        (10, "MINOR_ISSUE", "invalid or missing grounding"),
        (11, "USEFUL", "invalid or missing usefulness"),
        (12, "MAYBE", "invalid or missing misleading"),
    ],
)
def test_review_validation_rejects_missing_or_invalid_verdicts(monkeypatch, column, value, message):
    values = completed_values()
    values[1][column] = value
    install_inspection(monkeypatch, values)
    with pytest.raises(ValueError, match=message):
        review_import.validate(Path("review.xlsx"))


def test_review_validation_rejects_duplicate_ids(monkeypatch):
    values = completed_values()
    values[2][0] = values[1][0]
    install_inspection(monkeypatch, values)
    with pytest.raises(ValueError, match="duplicate Phase 13D review ID"):
        review_import.validate(Path("review.xlsx"))


def test_review_validation_rejects_missing_and_extra_rows(monkeypatch):
    missing = completed_values()[:-1]
    install_inspection(monkeypatch, missing)
    with pytest.raises(ValueError, match="expected 169 review rows, got 168"):
        review_import.validate(Path("review.xlsx"))

    extra = completed_values()
    extra.append(copy.deepcopy(extra[-1]))
    extra[-1][0] = "R999"
    install_inspection(monkeypatch, extra)
    with pytest.raises(ValueError, match="expected 169 review rows, got 170"):
        review_import.validate(Path("review.xlsx"))


def test_review_validation_rejects_reordering_and_question_mismatch(monkeypatch):
    reordered = completed_values()
    reordered[1], reordered[2] = reordered[2], reordered[1]
    install_inspection(monkeypatch, reordered)
    with pytest.raises(ValueError, match="row ordering changed"):
        review_import.validate(Path("review.xlsx"))

    changed = completed_values()
    changed[1][1] = "unauthorized_question"
    install_inspection(monkeypatch, changed)
    with pytest.raises(ValueError, match="protected review context changed"):
        review_import.validate(Path("review.xlsx"))


def test_review_validation_rejects_other_protected_context_changes(monkeypatch):
    values = completed_values()
    values[1][7] = "tampered answer"
    install_inspection(monkeypatch, values)
    with pytest.raises(ValueError, match="protected review context changed"):
        review_import.validate(Path("review.xlsx"))
