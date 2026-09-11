import copy
import json
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.export_phase12c_annotation_xlsx import create_annotation_pack
from scripts.phase12c_benchmark import IncompleteAnnotations, ReferenceDataset, validate_complete
from scripts.phase12c_xlsx import (
    EXPECTED_SELECTION_HASH,
    FORBIDDEN_WORKBOOK_TEXT,
    HEADERS,
    REFERENCE_PATH,
    SPEAKER_VALUES,
    USABILITY_VALUES,
    WORKBOOK_NAME,
    audio_formula,
    import_completed_workbook,
    inspect_workbook,
    load_frozen_manifest,
    validate_workbook_data,
    workbook_row,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def exported_workbook():
    with tempfile.TemporaryDirectory(dir=ROOT) as directory:
        pack_dir = Path(directory) / "pack"
        result = create_annotation_pack(pack_dir, download_audio=False)
        workbook_path = pack_dir / WORKBOOK_NAME
        yield workbook_path, inspect_workbook(workbook_path), result


def completed_data(data: dict) -> dict:
    result = copy.deepcopy(data)
    for row_index in range(1, 31):
        result["annotations_values"][row_index][10] = "Human words [inaudible]"
        result["annotations_values"][row_index][11] = "CLEAR"
        result["annotations_values"][row_index][12] = "UNKNOWN"
    return result


def test_export_has_exactly_30_frozen_rows(exported_workbook):
    _, data, _ = exported_workbook
    assert data["annotations_values"][0] == HEADERS
    assert len([row for row in data["annotations_values"][1:31] if row[1]]) == 30
    assert not any(data["annotations_values"][31])


def test_clip_ids_and_order_match_frozen_manifest(exported_workbook):
    _, data, _ = exported_workbook
    manifest = load_frozen_manifest()
    observed = [row[1] for row in data["annotations_values"][1:31]]
    assert observed == [clip.clip_id for clip in manifest.clips]
    assert [row[0] for row in data["annotations_values"][1:31]] == list(range(1, 31))


def test_frozen_selection_hash_is_validated(exported_workbook):
    _, data, _ = exported_workbook
    manifest = load_frozen_manifest()
    assert manifest.selection_hash == EXPECTED_SELECTION_HASH
    assert data["instructions_values"][14][1] == EXPECTED_SELECTION_HASH


def test_workbook_contains_no_machine_or_recovery_fields(exported_workbook):
    workbook_path, data, _ = exported_workbook
    serialized = json.dumps(data, ensure_ascii=False).casefold()
    with zipfile.ZipFile(workbook_path) as archive:
        xlsx_text = "\n".join(
            archive.read(name).decode("utf-8", errors="ignore")
            for name in archive.namelist()
            if name.endswith(".xml")
        ).casefold()
    for token in FORBIDDEN_WORKBOOK_TEXT:
        assert token not in serialized
        assert token not in xlsx_text
    assert "racefans" not in serialized
    assert "verified_likely" not in serialized


def test_human_transcripts_and_optional_inputs_start_blank(exported_workbook):
    _, data, _ = exported_workbook
    for row in data["annotations_values"][1:31]:
        assert row[10:15] == [None, None, None, None, None]


def test_external_recovered_transcripts_are_not_inserted(exported_workbook):
    _, data, _ = exported_workbook
    workbook_text = json.dumps(data, ensure_ascii=False)
    external = json.loads(
        (ROOT / "docs/phase12c-radio-external-references.json").read_text(encoding="utf-8")
    )
    candidates = [
        record["recovered_transcript"]
        for record in external["records"]
        if record.get("recovered_transcript")
    ]
    assert candidates
    assert all(candidate not in workbook_text for candidate in candidates)


def test_metadata_mapping_is_exact(exported_workbook):
    _, data, _ = exported_workbook
    manifest = load_frozen_manifest()
    for row, clip in zip(data["annotations_values"][1:31], manifest.clips, strict=True):
        assert row[:9] == workbook_row(clip)[:9]


def test_audio_hyperlinks_correspond_to_each_frozen_clip(exported_workbook):
    _, data, _ = exported_workbook
    manifest = load_frozen_manifest()
    formulas = [row[9] for row in data["annotations_formulas"][1:31]]
    assert formulas == [audio_formula(clip) for clip in manifest.clips]


def test_audio_formula_caches_are_valid_friendly_strings(exported_workbook):
    workbook_path, _, _ = exported_workbook
    with zipfile.ZipFile(workbook_path) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert sheet.count("<x:v>Open audio</x:v>") == 30
    assert 't="e"><x:f>HYPERLINK' not in sheet


def test_workbook_contains_usability_and_speaker_dropdowns(exported_workbook):
    workbook_path, _, _ = exported_workbook
    with zipfile.ZipFile(workbook_path) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert "dataValidations" in sheet
    for value in USABILITY_VALUES | SPEAKER_VALUES:
        assert value in sheet


@pytest.mark.parametrize("value", ["GOOD", "clearish", "N/A"])
def test_importer_rejects_invalid_usability(exported_workbook, value):
    _, data, _ = exported_workbook
    modified = completed_data(data)
    modified["annotations_values"][1][11] = value
    report = validate_workbook_data(modified, load_frozen_manifest())
    assert report.invalid


@pytest.mark.parametrize("value", ["TEAM", "RADIO", "N/A"])
def test_importer_rejects_invalid_speaker(exported_workbook, value):
    _, data, _ = exported_workbook
    modified = completed_data(data)
    modified["annotations_values"][1][12] = value
    report = validate_workbook_data(modified, load_frozen_manifest())
    assert report.invalid


def test_importer_maps_completed_rows_by_clip_id(exported_workbook):
    _, data, _ = exported_workbook
    report = validate_workbook_data(completed_data(data), load_frozen_manifest())
    assert report.valid_for_import
    assert list(report.rows) == [clip.clip_id for clip in load_frozen_manifest().clips]


def test_duplicate_clip_ids_are_rejected(exported_workbook):
    _, data, _ = exported_workbook
    modified = copy.deepcopy(data)
    modified["annotations_values"][2][1] = modified["annotations_values"][1][1]
    with pytest.raises(ValueError, match="duplicate Clip IDs"):
        validate_workbook_data(modified, load_frozen_manifest())


def test_unknown_clip_ids_are_rejected(exported_workbook):
    _, data, _ = exported_workbook
    modified = copy.deepcopy(data)
    modified["annotations_values"][1][1] = "unknown-clip"
    with pytest.raises(ValueError, match="unknown Clip IDs"):
        validate_workbook_data(modified, load_frozen_manifest())


def test_incomplete_transcripts_are_reported_without_import(exported_workbook):
    _, data, _ = exported_workbook
    report = validate_workbook_data(data, load_frozen_manifest())
    assert len(report.incomplete) == 30
    assert not report.completed and not report.valid_for_import


def test_inaudible_survives_import_mapping(exported_workbook):
    _, data, _ = exported_workbook
    report = validate_workbook_data(completed_data(data), load_frozen_manifest())
    first_id = load_frozen_manifest().clips[0].clip_id
    assert report.rows[first_id]["reference_transcript"] == "Human words [inaudible]"


def test_canonical_overwrite_protection_remains_active(exported_workbook):
    workbook_path, data, _ = exported_workbook
    manifest = load_frozen_manifest()
    with tempfile.TemporaryDirectory(dir=ROOT) as directory:
        reference_path = Path(directory) / "references.json"
        reference_path.write_text(
            ReferenceDataset(selection_hash=manifest.selection_hash).model_dump_json(),
            encoding="utf-8",
        )
        with patch("scripts.phase12c_xlsx.inspect_workbook", return_value=completed_data(data)):
            imported = import_completed_workbook(
                workbook_path,
                reference_path=reference_path,
                confirm_import=True,
            )
            assert len(imported.imported) == 30
            with pytest.raises(ValueError, match="refusing silent canonical overwrite"):
                import_completed_workbook(
                    workbook_path,
                    reference_path=reference_path,
                    confirm_import=True,
                )


def test_existing_evaluator_remains_blocked_before_complete_import():
    manifest = load_frozen_manifest()
    references = ReferenceDataset.model_validate_json(REFERENCE_PATH.read_text(encoding="utf-8"))
    with pytest.raises(IncompleteAnnotations, match="30 human reference"):
        validate_complete(manifest, references)
