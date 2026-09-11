"""Freeze Phase 12B's exact clip set and initialize empty human-owned labels."""

import json
from pathlib import Path

from scripts.phase12c_benchmark import (
    SCHEMA_VERSION,
    FrozenClip,
    FrozenManifest,
    ReferenceDataset,
    SemanticReviewDataset,
    atomic_write,
    file_sha256,
    selection_hash,
)

PREDICTIONS = Path("docs/phase12b-radio-asr.json")
MANIFEST = Path("docs/phase12c-radio-annotation-manifest.json")
REFERENCES = Path("docs/phase12c-radio-human-references.json")
SEMANTIC = Path("docs/phase12c-radio-semantic-review.json")


def frozen_clips(predictions: dict) -> list[FrozenClip]:
    seen, clips = set(), []
    for row in predictions["results"]:
        clip_id = row["record_id"]
        if row.get("vad_filter") or clip_id in seen:
            continue
        seen.add(clip_id)
        clips.append(
            FrozenClip(
                sequence=len(clips) + 1,
                clip_id=clip_id,
                year=row["year"],
                round=row["round"],
                event=row["event"],
                session_id=row["session_id"],
                driver_id=row["driver_id"],
                available_at=row["available_at"],
                leader_lap=row["leader_lap"],
                audio_url=row["audio_url"],
            )
        )
    return clips


def initialize(path: Path, value) -> None:
    if path.exists():
        return
    atomic_write(path, value)


def freeze_manifest(path: Path, manifest: FrozenManifest) -> None:
    """Create the manifest once and refuse to silently change the benchmark."""
    if not path.exists():
        atomic_write(path, manifest)
        return
    existing = FrozenManifest.model_validate_json(path.read_text(encoding="utf-8"))
    if existing != manifest:
        raise RuntimeError(
            "Phase 12C manifest is already frozen and differs from Phase 12B. "
            "Do not overwrite it; investigate the source artifact change."
        )


def main() -> None:
    predictions = json.loads(PREDICTIONS.read_text(encoding="utf-8"))
    clips = frozen_clips(predictions)
    digest = selection_hash(clips)
    manifest = FrozenManifest(
        schema_version=SCHEMA_VERSION,
        source_prediction_artifact=str(PREDICTIONS).replace("\\", "/"),
        source_artifact_sha256=file_sha256(PREDICTIONS),
        selection_hash=digest,
        clips=clips,
    )
    freeze_manifest(MANIFEST, manifest)
    initialize(REFERENCES, ReferenceDataset(selection_hash=digest))
    initialize(SEMANTIC, SemanticReviewDataset(selection_hash=digest))
    print(f"Frozen {len(clips)} clips; selection_hash={digest}")


if __name__ == "__main__":
    main()
