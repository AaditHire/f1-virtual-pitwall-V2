import json
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts.phase12c_benchmark import (
    CriticalTerm,
    FrozenClip,
    FrozenManifest,
    HumanAnnotation,
    IncompleteAnnotations,
    ReferenceDataset,
    SemanticReview,
    SemanticReviewDataset,
    annotation_payload,
    cer,
    evaluate_dataset,
    keyword_score,
    save_reference,
    selection_hash,
    validate_complete,
    wer,
)
from scripts.prepare_phase12c import freeze_manifest

ROOT = Path(__file__).resolve().parents[2]


def one_clip():
    return FrozenClip(
        sequence=1,
        clip_id="clip-1",
        year=2024,
        round=1,
        event="Test Grand Prix",
        session_id="f1:2024:1:race",
        driver_id="f1:TEST01",
        available_at=100,
        leader_lap=1,
        audio_url="https://livetiming.formula1.com/static/race/TeamRadio/test.mp3",
    )


def manifest_with_30():
    clips = [
        one_clip().model_copy(update={"sequence": index + 1, "clip_id": f"clip-{index + 1}"})
        for index in range(30)
    ]
    return FrozenManifest(
        source_prediction_artifact="predictions.json",
        source_artifact_sha256="source",
        selection_hash=selection_hash(clips),
        clips=clips,
    )


def complete_annotation(clip_id="clip-1"):
    return HumanAnnotation(
        clip_id=clip_id,
        reference_transcript="box this lap",
        usability="CLEAR",
        speaker_type="ENGINEER",
        critical_terms=[CriticalTerm(text="box", category="BOX_PIT")],
        complete=True,
    )


def test_frozen_manifest_is_exact_phase12b_sample():
    manifest = FrozenManifest.model_validate_json(
        (ROOT / "docs/phase12c-radio-annotation-manifest.json").read_text(encoding="utf-8")
    )
    predictions = json.loads((ROOT / "docs/phase12b-radio-asr.json").read_text(encoding="utf-8"))
    predicted_ids = []
    for row in predictions["results"]:
        if not row["vad_filter"] and row["record_id"] not in predicted_ids:
            predicted_ids.append(row["record_id"])
    assert len(manifest.clips) == 30
    assert [clip.clip_id for clip in manifest.clips] == predicted_ids
    assert selection_hash(manifest.clips) == manifest.selection_hash


def test_existing_frozen_manifest_cannot_be_silently_replaced():
    with tempfile.TemporaryDirectory(dir=ROOT) as directory:
        path = Path(directory) / "manifest.json"
        original = manifest_with_30()
        freeze_manifest(path, original)
        changed = original.model_copy(update={"source_artifact_sha256": "changed"})
        with pytest.raises(RuntimeError, match="already frozen"):
            freeze_manifest(path, changed)
        assert FrozenManifest.model_validate_json(path.read_text(encoding="utf-8")) == original


def test_annotation_serialization_and_literal_reference_preservation():
    annotation = complete_annotation()
    assert HumanAnnotation.model_validate_json(annotation.model_dump_json()) == annotation
    assert annotation.reference_transcript == "box this lap"
    assert annotation.annotation_source == "human"


def test_incomplete_annotations_block_evaluation():
    manifest = manifest_with_30()
    references = ReferenceDataset(selection_hash=manifest.selection_hash)
    with pytest.raises(IncompleteAnnotations, match="30 human reference"):
        validate_complete(manifest, references)


def test_annotation_payload_is_blind_to_asr_and_race_context():
    manifest = manifest_with_30()
    references = ReferenceDataset(selection_hash=manifest.selection_hash)
    payload = annotation_payload(manifest, references, "clip-1")
    encoded = json.dumps(payload)
    for hidden in ("small.en", "medium.en", "raw_transcript", "driver_id", "leader_lap"):
        assert hidden not in encoded


def test_reference_payload_rejects_asr_fields_and_nonhuman_source():
    manifest = manifest_with_30()
    references = ReferenceDataset(selection_hash=manifest.selection_hash)
    with pytest.raises(ValidationError):
        save_reference(
            references,
            manifest,
            "clip-1",
            {"reference_transcript": "box", "raw_transcript": "ASR suggestion"},
        )
    with pytest.raises(ValidationError):
        HumanAnnotation(clip_id="clip-1", annotation_source="asr")


def test_wer_and_cer_are_reproducible():
    assert wer("box this lap", "boss this lap") == pytest.approx(1 / 3)
    assert cer("box", "boss") == pytest.approx(2 / 3)
    assert wer("thank you", "thanks") == 1


def test_inaudible_marker_excludes_unknown_hypothesis_span():
    assert wer("box [inaudible] this lap", "box anything at all this lap") == 0
    assert cer("box [inaudible] lap", "box noisy speech lap") == 0
    assert wer("[inaudible]", "arbitrary words") is None


def test_keyword_scoring_uses_only_human_labelled_terms():
    terms = [
        CriticalTerm(text="box", category="BOX_PIT"),
        CriticalTerm(text="DRS", category="DRS"),
    ]
    score = keyword_score(terms, "Boss this lap, DRS enabled")
    assert score["labelled_terms"] == 2
    assert score["matched_terms"] == 1
    assert score["exact_recovery"] == 0.5


def test_complete_annotation_requires_human_fields_and_reference_terms():
    with pytest.raises(ValidationError):
        HumanAnnotation(clip_id="clip-1", complete=True)
    with pytest.raises(ValidationError, match="critical terms"):
        HumanAnnotation(
            clip_id="clip-1",
            reference_transcript="stay out",
            usability="CLEAR",
            speaker_type="ENGINEER",
            critical_terms=[CriticalTerm(text="box", category="BOX_PIT")],
            complete=True,
        )


def test_evaluation_artifact_generation_with_human_labels():
    manifest = manifest_with_30()
    references = ReferenceDataset(
        selection_hash=manifest.selection_hash,
        annotations={clip.clip_id: complete_annotation(clip.clip_id) for clip in manifest.clips},
    )
    semantic = SemanticReviewDataset(
        selection_hash=manifest.selection_hash,
        reviews={
            clip.clip_id: {
                model: SemanticReview(
                    clip_id=clip.clip_id,
                    model_id=model,
                    label="MATERIAL_ERROR" if model == "small.en" else "SAFE_EQUIVALENT",
                )
                for model in ("small.en", "medium.en")
            }
            for clip in manifest.clips
        },
    )
    results = []
    for clip in manifest.clips:
        for model, text in (("small.en", "boss this lap"), ("medium.en", "box this lap")):
            results.append(
                {
                    "record_id": clip.clip_id,
                    "model_id": model,
                    "vad_filter": False,
                    "raw_transcript": text,
                    "audio_duration_seconds": 2,
                    "average_log_probability": -0.2,
                    "maximum_no_speech_probability": 0.1,
                    "suspicious_flags": [],
                    "processing_seconds": 1,
                }
            )
    artifact = evaluate_dataset(manifest, references, semantic, {"results": results})
    assert len(artifact["rows"]) == 60
    assert artifact["rows"][0]["wer"] == pytest.approx(1 / 3)
    assert artifact["rows"][1]["keyword_score"]["exact_recovery"] == 1
    assert artifact["scoring"]["inaudible"].startswith("[inaudible]")
