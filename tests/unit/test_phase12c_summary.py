import json
from pathlib import Path

import pytest

from scripts.phase12c_benchmark import SemanticReviewDataset, validate_complete
from scripts.phase12c_semantic_xlsx import load_inputs
from scripts.summarize_phase12c import build_summary

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def summary():
    evaluation = json.loads(
        (ROOT / "docs/phase12c-radio-evaluation.json").read_text(encoding="utf-8")
    )
    phase12b = json.loads((ROOT / "docs/phase12b-radio-asr.json").read_text(encoding="utf-8"))
    paths = {
        "manifest_sha256": ROOT / "docs/phase12c-radio-annotation-manifest.json",
        "phase12b_sha256": ROOT / "docs/phase12b-radio-asr.json",
        "human_references_sha256": ROOT / "docs/phase12c-radio-human-references.json",
        "semantic_reviews_sha256": ROOT / "docs/phase12c-radio-semantic-review.json",
    }
    return build_summary(evaluation, phase12b, paths)


def test_final_human_dataset_is_complete():
    manifest, references, _ = load_inputs()
    semantic = SemanticReviewDataset.model_validate_json(
        (ROOT / "docs/phase12c-radio-semantic-review.json").read_text(encoding="utf-8")
    )
    validate_complete(manifest, references, semantic)
    assert len(references.annotations) == 30
    assert sum(len(models) for models in semantic.reviews.values()) == 60
    assert {
        row.reviewer_source for models in semantic.reviews.values() for row in models.values()
    } == {"human"}


def test_summary_preserves_human_semantic_distribution(summary):
    overall = summary["reviews"]["overall"]
    assert overall["MATERIAL_ERROR"]["count"] == 34
    assert overall["MINOR_ERROR"]["count"] == 14
    assert overall["SAFE_EQUIVALENT"]["count"] == 12
    assert summary["reviews"]["models"]["small.en"]["semantic"]["MATERIAL_ERROR"]["count"] == 21
    assert summary["reviews"]["models"]["medium.en"]["semantic"]["MATERIAL_ERROR"]["count"] == 13


def test_summary_reports_corpus_rates_and_zero_keyword_denominator(summary):
    small = summary["reviews"]["models"]["small.en"]
    medium = summary["reviews"]["models"]["medium.en"]
    assert small["overall"]["wer"]["errors"] == 145
    assert small["overall"]["wer"]["reference_units"] == 595
    assert small["overall"]["wer"]["rate"] == pytest.approx(145 / 595)
    assert medium["overall"]["cer"]["errors"] == 346
    assert medium["overall"]["cer"]["reference_units"] == 2479
    assert medium["overall"]["cer"]["rate"] == pytest.approx(346 / 2479)
    assert small["keywords"]["labelled_terms"] == 0
    assert small["keywords"]["exact_recovery"] is None
    assert medium["keywords"]["labelled_terms"] == 0


def test_summary_keeps_unusable_and_short_slices_separate(summary):
    for model in ("small.en", "medium.en"):
        slices = summary["reviews"]["models"][model]["slices"]
        assert slices["CLEAR"]["rows"] == 28
        assert slices["UNUSABLE"]["rows"] == 2
        assert slices["SHORT_LT_3S"]["rows"] == 3
        assert slices["PARTIAL"]["rows"] == 0
        assert slices["POOR"]["rows"] == 0


def test_disagreement_partition_uses_frozen_phase12b_threshold(summary):
    assert summary["disagreement"]["meaningful"]["clips"] == 11
    assert summary["disagreement"]["not_meaningful"]["clips"] == 19
    assert summary["disagreement"]["meaningful"]["review_rows"] == 22
    assert summary["disagreement"]["not_meaningful"]["review_rows"] == 38
