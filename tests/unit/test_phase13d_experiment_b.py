from __future__ import annotations

import copy
import json
from collections import Counter
from pathlib import Path

import pytest

from f1_pitwall.knowledge.reliability import sha256
from scripts import import_phase13d_experiment_b_human_review_xlsx as review_import
from scripts.phase13d_experiment_b_human_review import build_payload
from scripts.prepare_phase13d_experiment_b import (
    ELIGIBLE,
    EXPECTED_COUNTS,
    FREEZE,
    MANIFEST,
    eligible_rows,
    source_checks,
    validate_manifest,
)

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"


def test_retry_population_is_exactly_the_frozen_mechanical_failures():
    source_checks()
    rows = eligible_rows()
    assert len(rows) == 30
    assert Counter(row["classification"] for row in rows) == EXPECTED_COUNTS
    assert all(row["classification"] in ELIGIBLE for row in rows)
    assert all(row["arm"] == "TREATMENT_640" and row["retry_count"] == 0 for row in rows)


def test_retry_manifest_and_freeze_are_exact():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
    validate_manifest(manifest)
    assert sha256(MANIFEST) == freeze["manifest_sha256"]
    assert len(manifest["retry_schedule"]) == 30
    assert [row["retry_sequence"] for row in manifest["retry_schedule"]] == list(range(1, 31))
    assert manifest["configuration"]["max_output_tokens"] == 640
    assert manifest["configuration"]["third_attempts"] == 0


def test_raw_run_has_one_and_only_one_request_per_eligible_failure():
    raw = [
        json.loads(line)
        for line in (DOCS / "phase13d-experiment-b-raw.jsonl").read_text("utf-8").splitlines()
    ]
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert len(raw) == 30
    assert [row["question_id"] for row in raw] == [
        row["question_id"] for row in manifest["retry_schedule"]
    ]
    assert all(row["request_count"] == 1 and row["retry_attempt_number"] == 1 for row in raw)
    assert all(row["max_output_tokens"] == 640 for row in raw)


def test_policy_reuses_original_successes_and_reports_observed_recovery():
    original = json.loads(
        (DOCS / "phase13d-experiment-a-outputs.json").read_text(encoding="utf-8")
    )["arms"]["TREATMENT_640"]
    policy_artifact = json.loads(
        (DOCS / "phase13d-experiment-b-policy-outputs.json").read_text(encoding="utf-8")
    )
    policy = policy_artifact["outputs"]
    provenance = {
        row["question_id"]: row["selected_attempt"] for row in policy_artifact["provenance"]
    }
    retry_ids = {
        row["question_id"]
        for row in json.loads(MANIFEST.read_text(encoding="utf-8"))["retry_schedule"]
    }
    for before, after in zip(original, policy, strict=True):
        if before["question_id"] not in retry_ids:
            assert after == before
            assert provenance[before["question_id"]] == "ORIGINAL_640"
        else:
            assert after["request_count"] == 2
            assert after["retry_count"] == 1
            assert provenance[before["question_id"]] == "RETRY"
    evaluation = json.loads(
        (DOCS / "phase13d-experiment-b-evaluation.json").read_text(encoding="utf-8")
    )
    assert evaluation["mechanical_recovery"]["recovered"] == 11
    assert evaluation["absolute_reliability"]["final_unrecovered_failures"] == 19
    assert evaluation["retry_cardinality"] == {
        "eligible": 30,
        "new_provider_calls": 30,
        "successful_original_answers_retried": 0,
        "third_attempts": 0,
    }


def completed_values() -> list[list]:
    payload = build_payload()
    return [payload["headers"]] + [
        [*row[:10], "PASS", "GOOD", "NO", "Reviewed."] for row in payload["rows"]
    ]


def test_recovered_answer_review_packet_is_complete_and_blank():
    payload = build_payload()
    assert payload["review_rows"] == 11
    assert payload["review_all_recovered"] is True
    assert len({row[0] for row in payload["rows"]}) == 11
    assert all(row[9] == "SUCCESS" for row in payload["rows"])
    assert all(row[10:14] == [None, None, None, None] for row in payload["rows"])


def test_completed_retry_review_validation_is_exact(monkeypatch):
    values = completed_values()
    monkeypatch.setattr(review_import, "inspect_workbook", lambda _path: {"review_values": values})
    assert len(review_import.validate(Path("review.xlsx"))) == 11

    changed = copy.deepcopy(values)
    changed[1][6] = "tampered answer"
    monkeypatch.setattr(review_import, "inspect_workbook", lambda _path: {"review_values": changed})
    with pytest.raises(ValueError, match="protected review context changed"):
        review_import.validate(Path("review.xlsx"))

    missing = copy.deepcopy(values)
    missing[1][10] = ""
    monkeypatch.setattr(review_import, "inspect_workbook", lambda _path: {"review_values": missing})
    with pytest.raises(ValueError, match="invalid or missing grounding"):
        review_import.validate(Path("review.xlsx"))


def test_canonical_retry_review_results_match_final_human_decision():
    artifact = json.loads(
        (DOCS / "phase13d-experiment-b-human-review-results.json").read_text(encoding="utf-8")
    )
    reviews = artifact["reviews"]
    assert artifact["status"] == "COMPLETE_HUMAN_REVIEW"
    assert len(reviews) == 11
    assert Counter(row["grounding"] for row in reviews) == {"PASS": 10, "MINOR": 1}
    assert Counter(row["usefulness"] for row in reviews) == {"GOOD": 11}
    assert Counter(row["misleading"] for row in reviews) == {"NO": 11}
    minor = [row for row in reviews if row["grounding"] == "MINOR"]
    assert [row["question_id"] for row in minor] == ["q13d_f5d058442ae24723b194"]
