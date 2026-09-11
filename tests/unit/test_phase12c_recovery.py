import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from scripts import recover_phase12c_references as recovery_runner
from scripts.phase12c_benchmark import (
    FrozenManifest,
    IncompleteAnnotations,
    ReferenceDataset,
    validate_complete,
)
from scripts.phase12c_recovery import (
    EXPECTED_SELECTION_HASH,
    ExternalReferenceRecord,
    MatchEvidence,
    PromotionDataset,
    RecoveryReviewDataset,
    SearchAttempt,
    file_sha256,
    load_external_references,
    promote_reference,
    save_review,
    validate_against_manifest,
)
from scripts.review_phase12c_recovery import RecoveryReviewApplication

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "docs/phase12c-radio-annotation-manifest.json"
EXTERNAL_PATH = ROOT / "docs/phase12c-radio-external-references.json"
PREDICTIONS_PATH = ROOT / "docs/phase12b-radio-asr.json"


def artifacts():
    manifest = FrozenManifest.model_validate_json(MANIFEST_PATH.read_text(encoding="utf-8"))
    external = load_external_references(EXTERNAL_PATH)
    reviews = RecoveryReviewDataset(selection_hash=manifest.selection_hash)
    references = ReferenceDataset(selection_hash=manifest.selection_hash)
    promotions = PromotionDataset(selection_hash=manifest.selection_hash)
    return manifest, external, reviews, references, promotions


def exact_record(record, transcript="Thank you"):
    values = record.model_dump(mode="json")
    values.update(
        source_name="Editorial Source",
        source_url="https://example.com/human-transcript",
        source_authorship="HUMAN_EDITORIAL",
        quote_kind="LITERAL",
        recovered_transcript=transcript,
        clip_coverage="FULL",
        verification_status="VERIFIED_EXACT",
        matching_evidence=MatchEvidence(
            event_match=True,
            session_match=True,
            driver_match=True,
            temporal_match=True,
            unique_message_match=True,
            evidence=["provider timestamp and unique message identifier"],
        ).model_dump(mode="json"),
        verification_reason="Exact independent metadata association.",
        review_status="PENDING",
    )
    return ExternalReferenceRecord.model_validate(values)


def replace_record(external, record):
    return external.model_copy(
        update={
            "records": [
                record if item.clip_id == record.clip_id else item for item in external.records
            ]
        }
    )


def test_frozen_manifest_and_hashes_are_unchanged():
    manifest, external, *_ = artifacts()
    validate_against_manifest(external, manifest, MANIFEST_PATH)
    assert manifest.selection_hash == EXPECTED_SELECTION_HASH
    assert len(manifest.clips) == len({clip.clip_id for clip in manifest.clips}) == 30
    assert external.phase12b_predictions_sha256_before_recovery == file_sha256(PREDICTIONS_PATH)


def test_recovery_artifact_covers_all_clips_in_frozen_order():
    manifest, external, *_ = artifacts()
    assert [record.clip_id for record in external.records] == [
        clip.clip_id for clip in manifest.clips
    ]
    assert sum(record.verification_status == "VERIFIED_LIKELY" for record in external.records) == 4
    assert sum(record.verification_status == "NO_REFERENCE" for record in external.records) == 26


def test_recovery_input_rejects_machine_prediction_fields():
    raw = json.loads(EXTERNAL_PATH.read_text(encoding="utf-8"))
    raw["records"][0]["raw_transcript"] = "must never enter recovery"
    with tempfile.TemporaryDirectory(dir=ROOT) as directory:
        path = Path(directory) / "contaminated.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(ValueError, match="machine-generated field forbidden"):
            load_external_references(path)


def test_recovery_runner_hashes_but_never_parses_prediction_artifact():
    with tempfile.TemporaryDirectory(dir=ROOT) as directory:
        root = Path(directory)
        prediction_path = root / "intentionally-not-json.bin"
        output_path = root / "external.json"
        reviews_path = root / "reviews.json"
        promotions_path = root / "promotions.json"
        prediction_path.write_bytes(b"\x00prediction artifact is opaque to recovery\xff")
        with (
            patch.object(recovery_runner, "PREDICTION_PATH", prediction_path),
            patch.object(recovery_runner, "OUTPUT_PATH", output_path),
            patch.object(recovery_runner, "REVIEWS_PATH", reviews_path),
            patch.object(recovery_runner, "PROMOTIONS_PATH", promotions_path),
        ):
            recovery_runner.main()
        recovered = load_external_references(output_path)
        assert recovered.phase12b_predictions_sha256_before_recovery == file_sha256(prediction_path)
        assert len(recovered.records) == 30


def test_source_provenance_and_review_state_round_trip():
    _, external, reviews, *_ = artifacts()
    candidate = next(
        record for record in external.records if record.verification_status == "VERIFIED_LIKELY"
    )
    restored = ExternalReferenceRecord.model_validate_json(candidate.model_dump_json())
    assert restored.source_name == "RaceFans"
    assert restored.source_url.startswith("https://www.racefans.net/")
    decision = save_review(
        reviews,
        external,
        candidate.clip_id,
        {
            "action": "NEEDS_MANUAL_TRANSCRIPTION",
            "notes": "Exact clip identity remains uncertain.",
        },
    )
    assert (
        RecoveryReviewDataset.model_validate_json(reviews.model_dump_json()).decisions[
            candidate.clip_id
        ]
        == decision
    )


def test_review_interface_payload_is_blind_to_machine_outputs():
    manifest, _, reviews, *_ = artifacts()
    with tempfile.TemporaryDirectory(dir=ROOT) as directory:
        reviews_path = Path(directory) / "reviews.json"
        reviews_path.write_text(reviews.model_dump_json(), encoding="utf-8")
        application = RecoveryReviewApplication(MANIFEST_PATH, EXTERNAL_PATH, reviews_path)
        payload = application.clip(application.index()[0]["clip_id"])
    forbidden = {
        "asr",
        "prediction",
        "predictions",
        "small.en",
        "medium.en",
        "disagreement",
        "suspicious_flags",
    }
    assert forbidden.isdisjoint(payload)
    assert payload["clip_id"] in {clip.clip_id for clip in manifest.clips}


def test_all_likely_candidates_are_flagged_strategy_critical():
    _, external, *_ = artifacts()
    candidates = [
        record for record in external.records if record.verification_status == "VERIFIED_LIKELY"
    ]
    assert len(candidates) == 4
    assert all(record.strategy_critical for record in candidates)


def test_strategy_critical_acceptance_requires_human_owned_term_labels():
    _, external, reviews, *_ = artifacts()
    record = next(
        item for item in external.records if item.verification_status == "VERIFIED_LIKELY"
    )
    with pytest.raises(ValueError, match="critical-term labels"):
        save_review(
            reviews,
            external,
            record.clip_id,
            {
                "action": "ACCEPT_REFERENCE",
                "accepted_transcript": record.recovered_transcript,
                "usability": "CLEAR",
                "speaker_type": "ENGINEER",
                "critical_terms": [],
            },
        )


def test_exact_classification_requires_full_unique_literal_human_match():
    _, external, *_ = artifacts()
    record = external.records[0]
    with pytest.raises(ValidationError, match="complete, unique"):
        ExternalReferenceRecord.model_validate(
            {
                **record.model_dump(mode="json"),
                "source_name": "Editorial Source",
                "source_url": "https://example.com/human",
                "source_authorship": "HUMAN_EDITORIAL",
                "quote_kind": "PARTIAL",
                "recovered_transcript": "Thank you",
                "clip_coverage": "PARTIAL",
                "verification_status": "VERIFIED_EXACT",
            }
        )


def test_likely_and_no_reference_cannot_auto_promote():
    manifest, external, reviews, references, promotions = artifacts()
    likely = next(
        record for record in external.records if record.verification_status == "VERIFIED_LIKELY"
    )
    with pytest.raises(ValueError, match="human acceptance"):
        promote_reference(
            manifest=manifest,
            references=references,
            external=external,
            reviews=reviews,
            provenance=promotions,
            clip_id=likely.clip_id,
            explicit_confirmation=True,
        )
    no_reference = next(
        record for record in external.records if record.verification_status == "NO_REFERENCE"
    )
    with pytest.raises(ValueError, match="NO_REFERENCE cannot be promoted"):
        promote_reference(
            manifest=manifest,
            references=references,
            external=external,
            reviews=reviews,
            provenance=promotions,
            clip_id=no_reference.clip_id,
            explicit_confirmation=True,
        )


def test_strategy_critical_exact_requires_human_review():
    manifest, external, reviews, references, promotions = artifacts()
    record = exact_record(external.records[0], "Box now")
    assert record.strategy_critical and record.requires_human_review
    with pytest.raises(ValueError, match="human acceptance"):
        promote_reference(
            manifest=manifest,
            references=references,
            external=replace_record(external, record),
            reviews=reviews,
            provenance=promotions,
            clip_id=record.clip_id,
            explicit_confirmation=True,
        )


def test_noncritical_exact_has_explicit_controlled_promotion():
    manifest, external, reviews, references, promotions = artifacts()
    record = exact_record(external.records[0])
    external = replace_record(external, record)
    assert not record.strategy_critical and not record.requires_human_review
    with pytest.raises(ValueError, match="explicit confirmation"):
        promote_reference(
            manifest=manifest,
            references=references,
            external=external,
            reviews=reviews,
            provenance=promotions,
            clip_id=record.clip_id,
            explicit_confirmation=False,
        )
    promoted = promote_reference(
        manifest=manifest,
        references=references,
        external=external,
        reviews=reviews,
        provenance=promotions,
        clip_id=record.clip_id,
        explicit_confirmation=True,
        usability="CLEAR",
        speaker_type="ENGINEER",
    )
    assert references.annotations[record.clip_id].reference_transcript == "Thank you"
    assert promoted.reference_origin == "EXTERNAL_HUMAN_SOURCE"


def test_existing_canonical_reference_is_never_silently_replaced():
    manifest, external, reviews, references, promotions = artifacts()
    record = exact_record(external.records[0])
    external = replace_record(external, record)
    promote_reference(
        manifest=manifest,
        references=references,
        external=external,
        reviews=reviews,
        provenance=promotions,
        clip_id=record.clip_id,
        explicit_confirmation=True,
        usability="CLEAR",
        speaker_type="ENGINEER",
    )
    with pytest.raises(ValueError, match="refusing silent overwrite"):
        promote_reference(
            manifest=manifest,
            references=references,
            external=external,
            reviews=reviews,
            provenance=promotions,
            clip_id=record.clip_id,
            explicit_confirmation=True,
            usability="CLEAR",
            speaker_type="ENGINEER",
        )


def test_conflict_missing_source_and_malformed_source_handling():
    _, external, *_ = artifacts()
    record = external.records[0]
    conflict = ExternalReferenceRecord.model_validate(
        {
            **record.model_dump(mode="json"),
            "verification_status": "REFERENCE_CONFLICT",
            "competing_candidate_references": ["candidate one", "candidate two"],
            "verification_reason": "Two human sources disagree.",
            "review_status": "PENDING",
        }
    )
    assert conflict.requires_human_review
    unavailable = SearchAttempt(
        source_family="RACEFANS",
        query="metadata-only query",
        metadata_used=["event", "driver"],
        outcome="SOURCE_UNAVAILABLE",
        notes="HTTP 429; absence not inferred.",
    )
    assert unavailable.outcome == "SOURCE_UNAVAILABLE"
    with pytest.raises(ValidationError):
        SearchAttempt.model_validate(
            {
                "source_family": "UNKNOWN_BLOG",
                "query": "query",
                "metadata_used": ["event"],
                "outcome": "NO_MATCH",
            }
        )


def test_no_reference_routes_to_manual_fallback_and_evaluation_still_blocks():
    manifest, external, reviews, references, _ = artifacts()
    record = next(item for item in external.records if item.verification_status == "NO_REFERENCE")
    decision = save_review(
        reviews,
        external,
        record.clip_id,
        {"action": "NEEDS_MANUAL_TRANSCRIPTION", "notes": "Use blind annotator."},
    )
    assert decision.action == "NEEDS_MANUAL_TRANSCRIPTION"
    with pytest.raises(IncompleteAnnotations, match="30 human reference"):
        validate_complete(manifest, references)


def test_human_acceptance_can_promote_likely_but_not_replace_existing():
    manifest, external, reviews, references, promotions = artifacts()
    record = next(
        item for item in external.records if item.verification_status == "VERIFIED_LIKELY"
    )
    save_review(
        reviews,
        external,
        record.clip_id,
        {
            "action": "ACCEPT_REFERENCE",
            "accepted_transcript": record.recovered_transcript,
            "usability": "CLEAR",
            "speaker_type": "ENGINEER",
            "critical_terms": [{"category": "SAFETY_CAR", "text": "Safety Car"}],
            "notes": "Human listened and confirmed complete match.",
        },
    )
    promotion = promote_reference(
        manifest=manifest,
        references=references,
        external=external,
        reviews=reviews,
        provenance=promotions,
        clip_id=record.clip_id,
        explicit_confirmation=True,
    )
    assert promotion.human_confirmed
    assert references.annotations[record.clip_id].annotation_source == "human"
