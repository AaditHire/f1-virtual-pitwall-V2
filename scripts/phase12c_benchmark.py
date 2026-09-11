"""Schemas and deterministic scoring for the human-grounded Phase 12C benchmark."""

import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "1.0"
INAUDIBLE = "[inaudible]"
MODELS = ("small.en", "medium.en")

Usability = Literal["CLEAR", "PARTIAL", "POOR", "UNUSABLE"]
SpeakerType = Literal["DRIVER", "ENGINEER", "MIXED", "UNKNOWN"]
SemanticLabel = Literal["SAFE_EQUIVALENT", "MINOR_ERROR", "MATERIAL_ERROR"]
CriticalCategory = Literal[
    "DRIVER_NAME",
    "CAR_NUMBER",
    "LAP_NUMBER",
    "TYRE_COMPOUND",
    "BOX_PIT",
    "STAY_OUT",
    "DRS",
    "SAFETY_CAR",
    "VSC",
    "DELTA",
    "BRAKE_BALANCE",
    "DIFFERENTIAL",
    "ENGINE_STRAT_MODE",
    "WING_ADJUSTMENT",
    "OTHER_OPERATIONAL",
]


class FrozenClip(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=1)
    clip_id: str = Field(min_length=1)
    year: int
    round: int = Field(ge=1)
    event: str
    session_id: str
    driver_id: str
    available_at: float = Field(ge=0)
    leader_lap: int | None = Field(default=None, ge=1)
    audio_url: str


class FrozenManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    source_prediction_artifact: str
    source_artifact_sha256: str
    selection_hash: str
    clips: list[FrozenClip]

    @model_validator(mode="after")
    def valid_selection(self):
        if len(self.clips) != 30 or len({clip.clip_id for clip in self.clips}) != 30:
            raise ValueError("Phase 12C requires exactly 30 unique frozen clips")
        if selection_hash(self.clips) != self.selection_hash:
            raise ValueError("frozen clip selection hash does not match")
        return self


class CriticalTerm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=100)
    category: CriticalCategory


class HumanAnnotation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clip_id: str
    reference_transcript: str = Field(default="", max_length=4000)
    usability: Usability | None = None
    speaker_type: SpeakerType | None = None
    critical_terms: list[CriticalTerm] = Field(default_factory=list)
    notes: str = Field(default="", max_length=1000)
    complete: bool = False
    annotation_source: Literal["human"] = "human"
    updated_at: datetime | None = None

    @model_validator(mode="after")
    def complete_fields_and_terms(self):
        if self.complete and (
            not self.reference_transcript.strip()
            or self.usability is None
            or self.speaker_type is None
        ):
            raise ValueError("complete annotations require transcript, usability and speaker type")
        if self.complete:
            reference = normalize_plain(self.reference_transcript)
            for term in self.critical_terms:
                if normalize_plain(term.text) not in reference:
                    raise ValueError("critical terms must appear in the human reference transcript")
        return self


class ReferenceDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    selection_hash: str
    annotations: dict[str, HumanAnnotation] = Field(default_factory=dict)


class SemanticReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clip_id: str
    model_id: Literal["small.en", "medium.en"]
    label: SemanticLabel
    notes: str = Field(default="", max_length=1000)
    reviewer_source: Literal["human"] = "human"


class SemanticReviewDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    selection_hash: str
    reviews: dict[str, dict[str, SemanticReview]] = Field(default_factory=dict)


class IncompleteAnnotations(RuntimeError):
    pass


def canonical_json(value) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def selection_hash(clips: list[FrozenClip]) -> str:
    values = [clip.model_dump(mode="json") for clip in clips]
    return hashlib.sha256(canonical_json(values)).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_plain(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"[^\w']+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def scored_units(value: str, character: bool = False) -> list[str]:
    parts = re.split(r"(\[inaudible\])", value, flags=re.IGNORECASE)
    units = []
    for part in parts:
        if part.casefold() == INAUDIBLE:
            units.append(INAUDIBLE)
            continue
        normalized = normalize_plain(part)
        units.extend(list(normalized.replace(" ", "")) if character else normalized.split())
    return units


def wildcard_edit_distance(reference: list[str], hypothesis: list[str]) -> int:
    """Levenshtein distance where [inaudible] matches any hypothesis span at zero cost."""
    previous = list(range(len(hypothesis) + 1))
    for ref in reference:
        if ref == INAUDIBLE:
            prefix_min, current = previous[0], [previous[0]]
            for index in range(1, len(hypothesis) + 1):
                prefix_min = min(prefix_min, previous[index])
                current.append(prefix_min)
            previous = current
            continue
        current = [previous[0] + 1]
        for index, observed in enumerate(hypothesis, 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[index] + 1,
                    previous[index - 1] + (ref != observed),
                )
            )
        previous = current
    return previous[-1]


def error_rate(reference: str, hypothesis: str, character: bool = False) -> float | None:
    expected = scored_units(reference, character)
    observed = scored_units(hypothesis, character)
    denominator = sum(unit != INAUDIBLE for unit in expected)
    if denominator == 0:
        return None
    return wildcard_edit_distance(expected, observed) / denominator


def wer(reference: str, hypothesis: str) -> float | None:
    return error_rate(reference, hypothesis)


def cer(reference: str, hypothesis: str) -> float | None:
    return error_rate(reference, hypothesis, character=True)


def keyword_score(terms: list[CriticalTerm], hypothesis: str) -> dict:
    normalized = normalize_plain(hypothesis)
    results = [
        {
            "text": term.text,
            "category": term.category,
            "matched": normalize_plain(term.text) in normalized,
        }
        for term in terms
    ]
    matched = sum(row["matched"] for row in results)
    return {
        "labelled_terms": len(results),
        "matched_terms": matched,
        "exact_recovery": matched / len(results) if results else None,
        "terms": results,
    }


def annotation_payload(
    manifest: FrozenManifest, references: ReferenceDataset, clip_id: str
) -> dict:
    """Return only neutral annotation data; predictions and race context are absent."""
    clip = next((row for row in manifest.clips if row.clip_id == clip_id), None)
    if clip is None:
        raise KeyError(clip_id)
    annotation = references.annotations.get(clip_id)
    return {
        "clip_id": clip.clip_id,
        "sequence": clip.sequence,
        "total": len(manifest.clips),
        "audio_path": f"/audio/{clip.clip_id}",
        "annotation": annotation.model_dump(mode="json") if annotation else None,
    }


def save_reference(
    references: ReferenceDataset, manifest: FrozenManifest, clip_id: str, payload: dict
) -> HumanAnnotation:
    if clip_id not in {clip.clip_id for clip in manifest.clips}:
        raise KeyError(clip_id)
    annotation = HumanAnnotation.model_validate(
        {
            **payload,
            "clip_id": clip_id,
            "annotation_source": "human",
            "updated_at": datetime.now(UTC),
        }
    )
    references.annotations[clip_id] = annotation
    return annotation


def validate_complete(
    manifest: FrozenManifest,
    references: ReferenceDataset,
    semantic: SemanticReviewDataset | None = None,
) -> None:
    if references.selection_hash != manifest.selection_hash:
        raise ValueError("reference selection hash differs from frozen manifest")
    missing = [
        clip.clip_id
        for clip in manifest.clips
        if clip.clip_id not in references.annotations
        or not references.annotations[clip.clip_id].complete
    ]
    if missing:
        raise IncompleteAnnotations(f"{len(missing)} human reference annotations remain")
    if semantic is not None:
        if semantic.selection_hash != manifest.selection_hash:
            raise ValueError("semantic-review selection hash differs from frozen manifest")
        missing_reviews = [
            (clip.clip_id, model)
            for clip in manifest.clips
            for model in MODELS
            if model not in semantic.reviews.get(clip.clip_id, {})
        ]
        if missing_reviews:
            raise IncompleteAnnotations(f"{len(missing_reviews)} human semantic reviews remain")


def prediction_rows(predictions: dict) -> dict[tuple[str, str], dict]:
    rows = {}
    for row in predictions["results"]:
        if row.get("vad_filter") or row.get("model_id") not in MODELS:
            continue
        rows[(row["record_id"], row["model_id"])] = row
    return rows


def evaluate_dataset(
    manifest: FrozenManifest,
    references: ReferenceDataset,
    semantic: SemanticReviewDataset,
    predictions: dict,
) -> dict:
    validate_complete(manifest, references, semantic)
    predicted = prediction_rows(predictions)
    rows = []
    for clip in manifest.clips:
        reference = references.annotations[clip.clip_id]
        for model in MODELS:
            prediction = predicted.get((clip.clip_id, model))
            if prediction is None:
                raise ValueError(f"missing frozen prediction for {clip.clip_id}/{model}")
            text = prediction["raw_transcript"]
            rows.append(
                {
                    "clip_id": clip.clip_id,
                    "model_id": model,
                    "usability": reference.usability,
                    "speaker_type": reference.speaker_type,
                    "duration_seconds": prediction["audio_duration_seconds"],
                    "short_clip": prediction["audio_duration_seconds"] < 3,
                    "reference_transcript": reference.reference_transcript,
                    "raw_transcript": text,
                    "wer": wer(reference.reference_transcript, text),
                    "cer": cer(reference.reference_transcript, text),
                    "keyword_score": keyword_score(reference.critical_terms, text),
                    "semantic_label": semantic.reviews[clip.clip_id][model].label,
                    "average_log_probability": prediction["average_log_probability"],
                    "maximum_no_speech_probability": prediction["maximum_no_speech_probability"],
                    "suspicious_flags": prediction["suspicious_flags"],
                    "processing_seconds": prediction["processing_seconds"],
                }
            )
    return {
        "schema_version": SCHEMA_VERSION,
        "selection_hash": manifest.selection_hash,
        "scoring": {
            "normalization": "Unicode NFKC, case-fold, punctuation removed, whitespace collapsed",
            "inaudible": "[inaudible] matches an arbitrary hypothesis span at zero edit cost",
            "keyword": "case-insensitive exact normalized phrase recovery of human labels",
        },
        "rows": rows,
    }


def atomic_write(path: Path, model: BaseModel | dict) -> None:
    value = model.model_dump(mode="json") if isinstance(model, BaseModel) else model
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    if path.exists():
        backup = path.with_suffix(path.suffix + ".bak")
        backup.write_bytes(path.read_bytes())
    temporary.replace(path)
