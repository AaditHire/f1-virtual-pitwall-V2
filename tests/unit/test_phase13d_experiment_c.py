from __future__ import annotations

import copy
import json
from collections import Counter
from pathlib import Path

import pytest

from f1_pitwall.knowledge.reliability import sha256
from scripts import import_phase13d_experiment_c_human_review_xlsx as review_import
from scripts.phase13d_experiment_c_human_review import build_payload
from scripts.prepare_phase13d_experiment_c import (
    FREEZE,
    MANIFEST,
    eligible_rows,
    source_checks,
    validate_manifest,
)

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"


def test_population_is_all_and_only_original_640_truncations():
    source_checks()
    rows = eligible_rows()
    assert len(rows) == 22
    assert Counter(row["classification"] for row in rows) == {"TRUNCATED_RESPONSE": 22}
    assert all(row["arm"] == "TREATMENT_640" and row["retry_count"] == 0 for row in rows)


def test_manifest_and_freeze_are_exact():
    manifest = json.loads(MANIFEST.read_text("utf-8"))
    freeze = json.loads(FREEZE.read_text("utf-8"))
    validate_manifest(manifest)
    assert sha256(MANIFEST) == freeze["manifest_sha256"]
    assert manifest["configuration"]["max_output_tokens"] == 1280
    assert manifest["configuration"]["prompts_changed"] is False
    assert manifest["configuration"]["second_fallbacks"] == 0
    assert manifest["methodological_status"] == "DEVELOPMENT_DIAGNOSTIC_NOT_UNTOUCHED_HOLDOUT"


def test_raw_run_has_exactly_one_call_per_original_truncation():
    raw = [
        json.loads(line)
        for line in (DOCS / "phase13d-experiment-c-raw.jsonl").read_text("utf-8").splitlines()
    ]
    manifest = json.loads(MANIFEST.read_text("utf-8"))
    assert len(raw) == 22
    assert [row["question_id"] for row in raw] == [
        row["question_id"] for row in manifest["fallback_schedule"]
    ]
    assert all(row["request_count"] == 1 and row["fallback_attempt_number"] == 1 for row in raw)
    assert all(row["max_output_tokens"] == 1280 for row in raw)
    assert all(row["original_failure_category"] == "TRUNCATED_RESPONSE" for row in raw)


def test_candidate_policy_reconstruction_excludes_experiment_b_truncation_retries():
    artifact = json.loads((DOCS / "phase13d-experiment-c-policy-outputs.json").read_text("utf-8"))
    provenance = Counter(row["selected_attempt"] for row in artifact["provenance"])
    assert provenance == {
        "DETERMINISTIC_STRUCTURED_ONLY": 30,
        "ORIGINAL_640": 70,
        "EXPERIMENT_B_640_RETRY": 8,
        "EXPERIMENT_C_1280_FALLBACK": 22,
    }
    assert len(artifact["outputs"]) == 130


def test_evaluation_preserves_authoritative_and_sensitivity_layers():
    result = json.loads((DOCS / "phase13d-experiment-c-evaluation.json").read_text("utf-8"))
    assert result["call_cardinality"] == {
        "eligible_original_truncations": 22,
        "new_provider_calls": 22,
        "non_truncation_calls": 0,
        "second_fallbacks": 0,
    }
    assert result["truncation_recovery"]["recovered"] == 22
    assert result["candidate_policy_reliability"]["final_unrecovered_failures"] == 0
    authoritative = result["authoritative_frozen_metric_result"]["metrics"]
    sensitivity = result["defect_aware_sensitivity"]["metrics"]
    assert (authoritative["required_facts_found"], authoritative["required_facts_total"]) == (
        143,
        170,
    )
    assert authoritative["false_refusals"] == 1
    assert (sensitivity["required_facts_found"], sensitivity["required_facts_total"]) == (143, 168)
    assert sensitivity["false_refusals"] == 0
    assert authoritative["mixed_exact_facts_correct"] == 25


def completed_values() -> list[list]:
    payload = build_payload()
    return [payload["headers"]] + [
        [*row[:10], "PASS", "GOOD", "NO", "Reviewed."] for row in payload["rows"]
    ]


def test_review_packet_is_complete_protected_and_blank():
    payload = build_payload()
    assert payload["review_rows"] == 22
    assert payload["review_all_recovered"] is True
    assert len({row[0] for row in payload["rows"]}) == 22
    assert all(row[9] == "SUCCESS" for row in payload["rows"])
    assert all(row[10:14] == [None, None, None, None] for row in payload["rows"])


def test_completed_review_validation_is_exact(monkeypatch):
    values = completed_values()
    monkeypatch.setattr(review_import, "inspect_workbook", lambda _path: {"review_values": values})
    assert len(review_import.validate(Path("review.xlsx"))) == 22

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
