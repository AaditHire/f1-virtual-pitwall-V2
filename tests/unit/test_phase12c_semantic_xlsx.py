import copy
import tempfile
import zipfile
from pathlib import Path

import pytest

from scripts.phase12c_benchmark import (
    IncompleteAnnotations,
    SemanticReviewDataset,
    validate_complete,
)
from scripts.phase12c_semantic_xlsx import (
    HEADERS,
    VERDICTS,
    WORKBOOK_NAME,
    create_semantic_review_workbook,
    import_completed_workbook,
    inspect_workbook,
    load_inputs,
    validate_workbook_data,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def exported_semantic_workbook():
    with tempfile.TemporaryDirectory(dir=ROOT) as directory:
        output = Path(directory) / "semantic"
        result = create_semantic_review_workbook(output)
        workbook = output / WORKBOOK_NAME
        yield workbook, inspect_workbook(workbook), result


def completed_data(data):
    result = copy.deepcopy(data)
    for row in result["review_values"][1:61]:
        row[6] = "SAFE_EQUIVALENT"
    return result


def test_export_contains_exact_60_blank_reviews(exported_semantic_workbook):
    _, data, _ = exported_semantic_workbook
    assert data["review_values"][0] == HEADERS
    assert len(data["review_values"][1:61]) == 60
    assert all(row[6:8] == [None, None] for row in data["review_values"][1:61])
    assert not any(data["review_values"][61])


def test_export_mapping_and_source_text_are_exact(exported_semantic_workbook):
    _, data, _ = exported_semantic_workbook
    manifest, references, predictions = load_inputs()
    expected = []
    number = 1
    for clip in manifest.clips:
        for model in ("small.en", "medium.en"):
            expected.append(
                [
                    number,
                    clip.sequence,
                    clip.clip_id,
                    model,
                    references.annotations[clip.clip_id].reference_transcript,
                    predictions[(clip.clip_id, model)]["raw_transcript"],
                ]
            )
            number += 1
    assert [row[:6] for row in data["review_values"][1:61]] == expected


def test_verdict_dropdown_is_present(exported_semantic_workbook):
    workbook, _, _ = exported_semantic_workbook
    with zipfile.ZipFile(workbook) as archive:
        xml = "\n".join(
            archive.read(name).decode("utf-8", errors="ignore")
            for name in archive.namelist()
            if name.endswith(".xml")
        )
    assert all(value in xml for value in VERDICTS)


def test_incomplete_reviews_are_reported(exported_semantic_workbook):
    _, data, _ = exported_semantic_workbook
    report = validate_workbook_data(data, *load_inputs())
    assert len(report.incomplete) == 60
    assert not report.valid_for_import


def test_invalid_verdict_is_rejected(exported_semantic_workbook):
    _, data, _ = exported_semantic_workbook
    changed = completed_data(data)
    changed["review_values"][1][6] = "GOOD"
    report = validate_workbook_data(changed, *load_inputs())
    assert report.invalid


def test_duplicate_or_reordered_mapping_is_rejected(exported_semantic_workbook):
    _, data, _ = exported_semantic_workbook
    changed = copy.deepcopy(data)
    changed["review_values"][2][2:4] = changed["review_values"][1][2:4]
    with pytest.raises(ValueError, match="duplicate"):
        validate_workbook_data(changed, *load_inputs())


def test_protected_reference_or_prediction_edit_is_rejected(exported_semantic_workbook):
    _, data, _ = exported_semantic_workbook
    changed = copy.deepcopy(data)
    changed["review_values"][1][4] = "edited reference"
    with pytest.raises(ValueError, match="protected semantic-review source data changed"):
        validate_workbook_data(changed, *load_inputs())


def test_controlled_import_and_overwrite_protection(exported_semantic_workbook, monkeypatch):
    workbook, data, _ = exported_semantic_workbook
    manifest, _, _ = load_inputs()
    with tempfile.TemporaryDirectory(dir=ROOT) as directory:
        target = Path(directory) / "semantic.json"
        target.write_text(
            SemanticReviewDataset(selection_hash=manifest.selection_hash).model_dump_json(),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "scripts.phase12c_semantic_xlsx.inspect_workbook", lambda _: completed_data(data)
        )
        report = import_completed_workbook(workbook, confirm_import=True, semantic_path=target)
        assert len(report.imported) == 60
        with pytest.raises(ValueError, match="refusing silent semantic-review overwrite"):
            import_completed_workbook(workbook, confirm_import=True, semantic_path=target)


def test_evaluator_remains_blocked_until_all_60_semantic_reviews_exist():
    manifest, references, _ = load_inputs()
    semantic = SemanticReviewDataset(selection_hash=manifest.selection_hash)
    with pytest.raises(IncompleteAnnotations, match="60 human semantic reviews remain"):
        validate_complete(manifest, references, semantic)
