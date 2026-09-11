"""Protected XLSX workflow for the 60 Phase 12C human semantic reviews."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from scripts.phase12c_benchmark import (
    MODELS,
    ReferenceDataset,
    SemanticReview,
    SemanticReviewDataset,
    atomic_write,
    prediction_rows,
    validate_complete,
)
from scripts.phase12c_xlsx import (
    MANIFEST_PATH,
    REFERENCE_PATH,
    file_sha256,
    load_frozen_manifest,
    run_artifact_tool,
)

PREDICTION_PATH = Path("docs/phase12b-radio-asr.json")
SEMANTIC_PATH = Path("docs/phase12c-radio-semantic-review.json")
DEFAULT_OUTPUT_DIR = Path("outputs/phase12c_semantic_review")
WORKBOOK_NAME = "phase12c_semantic_review.xlsx"
SCHEMA_VERSION = "1.0"
HEADERS = [
    "Review Number",
    "Clip Number",
    "Clip ID",
    "Model",
    "Human Reference",
    "ASR Prediction",
    "Semantic Verdict",
    "Notes",
]
VERDICTS = {"SAFE_EQUIVALENT", "MINOR_ERROR", "MATERIAL_ERROR"}


def load_inputs(
    manifest_path: Path = MANIFEST_PATH,
    reference_path: Path = REFERENCE_PATH,
    prediction_path: Path = PREDICTION_PATH,
):
    manifest = load_frozen_manifest(manifest_path)
    references = ReferenceDataset.model_validate_json(reference_path.read_text(encoding="utf-8"))
    validate_complete(manifest, references)
    if file_sha256(prediction_path) != manifest.source_artifact_sha256:
        raise ValueError("frozen Phase 12B prediction artifact hash changed")
    predictions = json.loads(prediction_path.read_text(encoding="utf-8"))
    indexed = prediction_rows(predictions)
    expected = {(clip.clip_id, model) for clip in manifest.clips for model in MODELS}
    if set(indexed) != expected:
        missing = sorted(expected - set(indexed))
        extra = sorted(set(indexed) - expected)
        raise ValueError(
            f"frozen prediction mapping differs from manifest: missing={missing}, extra={extra}"
        )
    return manifest, references, indexed


def export_payload(manifest, references, predictions) -> dict:
    rows = []
    review_number = 1
    for clip in manifest.clips:
        reference = references.annotations[clip.clip_id].reference_transcript
        for model in MODELS:
            rows.append(
                [
                    review_number,
                    clip.sequence,
                    clip.clip_id,
                    model,
                    reference,
                    predictions[(clip.clip_id, model)]["raw_transcript"],
                    None,
                    None,
                ]
            )
            review_number += 1
    return {
        "schema_version": SCHEMA_VERSION,
        "selection_hash": manifest.selection_hash,
        "headers": HEADERS,
        "rows": rows,
        "verdicts": sorted(VERDICTS),
    }


def inspect_workbook(workbook_path: Path) -> dict:
    inspection_path = Path(".cache/phase12c-xlsx-runtime/semantic-inspection.json").resolve()
    inspection_path.parent.mkdir(parents=True, exist_ok=True)
    run_artifact_tool("inspect-semantic", workbook_path, inspection_path)
    return json.loads(inspection_path.read_text(encoding="utf-8"))


@dataclass
class SemanticImportReport:
    completed: list[str] = field(default_factory=list)
    incomplete: list[str] = field(default_factory=list)
    invalid: dict[str, list[str]] = field(default_factory=dict)
    rows: dict[tuple[str, str], SemanticReview] = field(default_factory=dict)
    imported: list[str] = field(default_factory=list)

    @property
    def valid_for_import(self) -> bool:
        return len(self.completed) == 60 and not self.incomplete and not self.invalid

    def as_dict(self) -> dict:
        return {
            "completed_count": len(self.completed),
            "incomplete_count": len(self.incomplete),
            "invalid_count": len(self.invalid),
            "completed": self.completed,
            "incomplete": self.incomplete,
            "invalid": self.invalid,
            "imported": self.imported,
            "valid_for_import": self.valid_for_import,
        }


def validate_workbook_data(data: dict, manifest, references, predictions) -> SemanticImportReport:
    values = data.get("review_values")
    instructions = data.get("instructions_values")
    if not isinstance(values, list) or len(values) < 62 or values[0] != HEADERS:
        raise ValueError("Semantic Reviews sheet/schema is missing or malformed")
    if not isinstance(instructions, list) or len(instructions) < 9:
        raise ValueError("Instructions sheet/schema is missing or malformed")
    if instructions[6][1] != 60 or instructions[7][1] != manifest.selection_hash:
        raise ValueError("workbook frozen review count or selection hash does not match")
    if instructions[8][1] != SCHEMA_VERSION:
        raise ValueError("workbook semantic-review schema version is unsupported")
    if any(value not in (None, "") for value in values[61]):
        raise ValueError("Semantic Reviews sheet contains rows beyond the 60 frozen reviews")

    expected_keys = [(clip.clip_id, model) for clip in manifest.clips for model in MODELS]
    observed_keys = [(str(values[i][2] or ""), str(values[i][3] or "")) for i in range(1, 61)]
    if len(set(observed_keys)) != 60:
        raise ValueError("duplicate Clip ID/model review rows in workbook")
    if observed_keys != expected_keys:
        raise ValueError("workbook Clip ID/model rows do not preserve frozen mapping and order")

    report = SemanticImportReport()
    for row_number, key in enumerate(expected_keys, 1):
        clip_id, model = key
        clip = manifest.clips[(row_number - 1) // len(MODELS)]
        row = values[row_number]
        expected = [
            row_number,
            clip.sequence,
            clip_id,
            model,
            references.annotations[clip_id].reference_transcript,
            predictions[key]["raw_transcript"],
        ]
        if row[:6] != expected:
            raise ValueError(f"protected semantic-review source data changed for {clip_id}/{model}")
        review_id = f"{clip_id}/{model}"
        verdict = str(row[6] or "").strip().upper()
        notes = str(row[7] or "").strip()
        if not verdict:
            report.incomplete.append(review_id)
            continue
        if verdict not in VERDICTS:
            report.invalid.setdefault(review_id, []).append(f"invalid semantic verdict: {verdict}")
            continue
        report.completed.append(review_id)
        report.rows[key] = SemanticReview(
            clip_id=clip_id,
            model_id=model,
            label=verdict,
            notes=notes,
            reviewer_source="human",
        )
    return report


def create_semantic_review_workbook(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    *,
    overwrite_empty_workbook: bool = False,
) -> dict:
    manifest, references, predictions = load_inputs()
    output_dir = output_dir.resolve()
    workbook_path = output_dir / WORKBOOK_NAME
    if workbook_path.exists() and not overwrite_empty_workbook:
        raise FileExistsError(
            f"refusing to overwrite an existing semantic-review workbook: {workbook_path}"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    payload_path = Path(".cache/phase12c-xlsx-runtime/semantic-payload.json").resolve()
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    payload_path.write_text(
        json.dumps(export_payload(manifest, references, predictions), indent=2), encoding="utf-8"
    )
    preview_dir = Path(".cache/phase12c-semantic-xlsx-previews").resolve()
    build = run_artifact_tool("build-semantic", payload_path, workbook_path, preview_dir)
    inspected = inspect_workbook(workbook_path)
    report = validate_workbook_data(inspected, manifest, references, predictions)
    if report.completed or len(report.incomplete) != 60 or report.invalid:
        raise RuntimeError("exported semantic workbook did not preserve 60 blank review rows")
    return {
        "workbook_path": str(workbook_path),
        "workbook_sha256": file_sha256(workbook_path),
        "builder_output": build.stdout,
    }


def import_completed_workbook(
    workbook_path: Path,
    *,
    confirm_import: bool = False,
    override_existing: bool = False,
    semantic_path: Path = SEMANTIC_PATH,
) -> SemanticImportReport:
    manifest, references, predictions = load_inputs()
    report = validate_workbook_data(
        inspect_workbook(workbook_path), manifest, references, predictions
    )
    if not report.valid_for_import or not confirm_import:
        return report
    semantic = SemanticReviewDataset.model_validate_json(semantic_path.read_text(encoding="utf-8"))
    if semantic.selection_hash != manifest.selection_hash:
        raise ValueError("canonical semantic-review selection hash differs from frozen manifest")
    existing = sorted(
        f"{clip_id}/{model}"
        for clip_id, model in report.rows
        if model in semantic.reviews.get(clip_id, {})
    )
    if existing and not override_existing:
        raise ValueError(f"refusing silent semantic-review overwrite: {existing}")
    candidate = semantic.model_copy(deep=True)
    for (clip_id, model), review in report.rows.items():
        candidate.reviews.setdefault(clip_id, {})[model] = review
    atomic_write(semantic_path, candidate)
    report.imported = report.completed.copy()
    return report
