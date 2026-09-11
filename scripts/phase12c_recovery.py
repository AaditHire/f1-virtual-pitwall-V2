"""Leakage-safe external human-reference recovery and controlled promotion."""

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scripts.phase12c_benchmark import (
    CriticalTerm,
    FrozenManifest,
    ReferenceDataset,
    normalize_plain,
    save_reference,
)

RECOVERY_SCHEMA_VERSION = "1.0"
EXPECTED_SELECTION_HASH = "111cb5264152d0ba1875363894e9b6c018b4f384cefd75ba03bbca129676c131"

VerificationStatus = Literal[
    "VERIFIED_EXACT", "VERIFIED_LIKELY", "NO_REFERENCE", "REFERENCE_CONFLICT"
]
ReviewStatus = Literal[
    "PENDING", "NOT_REQUIRED", "ACCEPTED", "REJECTED", "NEEDS_MANUAL_TRANSCRIPTION"
]
ReviewAction = Literal[
    "ACCEPT_REFERENCE",
    "REJECT_REFERENCE",
    "DOWNGRADE_TO_LIKELY",
    "MARK_NO_REFERENCE",
    "NEEDS_MANUAL_TRANSCRIPTION",
]
SourceFamily = Literal["RACEFANS", "OFFICIAL_F1", "OTHER_REPUTABLE"]
SearchOutcome = Literal["CANDIDATE_FOUND", "NO_MATCH", "SOURCE_UNAVAILABLE", "DISALLOWED"]
SourceAuthorship = Literal["HUMAN_EDITORIAL", "PARAPHRASE", "MACHINE", "UNCLEAR"]
QuoteKind = Literal["LITERAL", "PARAPHRASE", "PARTIAL", "NONE"]
Coverage = Literal["FULL", "PARTIAL", "UNKNOWN", "NONE"]

FORBIDDEN_MACHINE_KEYS = {
    "asr",
    "prediction",
    "predictions",
    "raw_transcript",
    "small.en",
    "medium.en",
    "average_log_probability",
    "maximum_no_speech_probability",
    "disagreement",
    "suspicious_flags",
}

STRATEGY_CRITICAL = re.compile(
    r"\b(?:box|pit|stay\s+out|soft|medium|hard|intermediate|wet|lap|gap|delta|"
    r"position|strat(?:egy)?|engine|safety\s+car|vsc|damage|puncture|wing|brake|"
    r"rain|weather|fuel|lift(?:\s+and|-)coast|drs|mode|tyre|tire|graining|push|save)\b|\b\d+(?:\.\d+)?\b",
    re.IGNORECASE,
)


class SearchAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_family: SourceFamily
    query: str = Field(min_length=1)
    metadata_used: list[str] = Field(min_length=1)
    outcome: SearchOutcome
    candidate_url: str | None = None
    notes: str = ""


class MatchEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_match: bool
    session_match: bool
    driver_match: bool
    temporal_match: bool
    unique_message_match: bool
    evidence: list[str] = Field(default_factory=list)


class ExternalReferenceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clip_id: str
    season: int
    event: str
    session: str
    driver_id: str
    driver_display_name: str
    causal_leader_lap: int | None
    provider_timestamp: float
    recording_timestamp: str
    audio_identifier: str
    searches: list[SearchAttempt] = Field(min_length=1)
    source_name: str | None = None
    source_url: str | None = None
    source_publication_date: str | None = None
    source_retrieved_at: datetime | None = None
    source_authorship: SourceAuthorship = "UNCLEAR"
    quote_kind: QuoteKind = "NONE"
    recovered_transcript: str | None = None
    source_lap_context: str | None = None
    clip_coverage: Coverage = "NONE"
    verification_status: VerificationStatus
    matching_evidence: MatchEvidence
    verification_reason: str = Field(min_length=1)
    confidence_notes: str = ""
    competing_candidate_references: list[str] = Field(default_factory=list)
    strategy_critical: bool = False
    requires_human_review: bool = True
    review_status: ReviewStatus = "PENDING"

    @model_validator(mode="after")
    def enforce_status_rules(self):
        has_reference = bool(self.source_name and self.source_url and self.recovered_transcript)
        if self.verification_status in {"VERIFIED_EXACT", "VERIFIED_LIKELY"} and not has_reference:
            raise ValueError("verified candidates require source provenance and a transcript")
        if self.verification_status == "NO_REFERENCE" and self.recovered_transcript:
            raise ValueError("NO_REFERENCE cannot contain a recovered transcript")
        if (
            self.verification_status == "REFERENCE_CONFLICT"
            and len(self.competing_candidate_references) < 2
        ):
            raise ValueError("REFERENCE_CONFLICT requires at least two competing candidates")
        if self.verification_status == "VERIFIED_EXACT":
            exact = (
                self.source_authorship == "HUMAN_EDITORIAL"
                and self.quote_kind == "LITERAL"
                and self.clip_coverage == "FULL"
                and self.matching_evidence.event_match
                and self.matching_evidence.session_match
                and self.matching_evidence.driver_match
                and self.matching_evidence.temporal_match
                and self.matching_evidence.unique_message_match
            )
            if not exact:
                raise ValueError(
                    "VERIFIED_EXACT requires complete, unique, metadata-proven matching"
                )
        detected = bool(
            self.recovered_transcript and STRATEGY_CRITICAL.search(self.recovered_transcript)
        )
        self.strategy_critical = detected
        mandatory_review = self.verification_status != "VERIFIED_EXACT" or detected
        self.requires_human_review = mandatory_review
        if mandatory_review and self.review_status == "NOT_REQUIRED":
            raise ValueError("this candidate cannot bypass human review")
        if not mandatory_review and self.review_status == "PENDING":
            self.review_status = "NOT_REQUIRED"
        return self


class ExternalReferenceDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = RECOVERY_SCHEMA_VERSION
    selection_hash: str
    manifest_sha256_before_recovery: str
    phase12b_predictions_sha256_before_recovery: str
    research_method: str
    researched_at: datetime
    records: list[ExternalReferenceRecord]


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clip_id: str
    action: ReviewAction
    accepted_transcript: str | None = None
    usability: Literal["CLEAR", "PARTIAL", "POOR", "UNUSABLE"] | None = None
    speaker_type: Literal["DRIVER", "ENGINEER", "MIXED", "UNKNOWN"] | None = None
    critical_terms: list[CriticalTerm] = Field(default_factory=list)
    notes: str = ""
    reviewer_source: Literal["human"] = "human"
    reviewed_at: datetime

    @model_validator(mode="after")
    def accepted_fields(self):
        if self.action == "ACCEPT_REFERENCE" and (
            not (self.accepted_transcript or "").strip()
            or self.usability is None
            or self.speaker_type is None
        ):
            raise ValueError("acceptance requires transcript, usability and speaker type")
        if self.action == "ACCEPT_REFERENCE":
            reference = normalize_plain(self.accepted_transcript or "")
            for term in self.critical_terms:
                if normalize_plain(term.text) not in reference:
                    raise ValueError("critical terms must appear in the accepted human transcript")
        return self


class RecoveryReviewDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = RECOVERY_SCHEMA_VERSION
    selection_hash: str
    decisions: dict[str, ReviewDecision] = Field(default_factory=dict)


class PromotionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clip_id: str
    reference_origin: Literal["EXTERNAL_HUMAN_SOURCE"] = "EXTERNAL_HUMAN_SOURCE"
    source_name: str
    source_url: str
    verification_status: Literal["VERIFIED_EXACT", "VERIFIED_LIKELY"]
    human_confirmed: bool
    promoted_at: datetime


class PromotionDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = RECOVERY_SCHEMA_VERSION
    selection_hash: str
    promotions: dict[str, PromotionRecord] = Field(default_factory=dict)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assert_no_machine_fields(value, path="root") -> None:
    """Fail closed if recovery input contains prediction/diagnostic-shaped keys."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key.casefold() in FORBIDDEN_MACHINE_KEYS:
                raise ValueError(
                    f"machine-generated field forbidden in recovery input: {path}.{key}"
                )
            assert_no_machine_fields(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            assert_no_machine_fields(child, f"{path}[{index}]")


def load_external_references(path: Path) -> ExternalReferenceDataset:
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert_no_machine_fields(raw)
    return ExternalReferenceDataset.model_validate(raw)


def validate_against_manifest(
    dataset: ExternalReferenceDataset, manifest: FrozenManifest, manifest_path: Path
) -> None:
    if manifest.selection_hash != EXPECTED_SELECTION_HASH:
        raise ValueError("frozen Phase 12C selection hash changed")
    if dataset.selection_hash != manifest.selection_hash:
        raise ValueError("external-reference selection hash differs from frozen manifest")
    if dataset.manifest_sha256_before_recovery != file_sha256(manifest_path):
        raise ValueError("frozen manifest file hash differs from recovery baseline")
    expected = [clip.clip_id for clip in manifest.clips]
    observed = [record.clip_id for record in dataset.records]
    if observed != expected or len(set(observed)) != 30:
        raise ValueError("recovery records must preserve all 30 frozen clips in exact order")


def save_review(
    reviews: RecoveryReviewDataset,
    dataset: ExternalReferenceDataset,
    clip_id: str,
    payload: dict,
) -> ReviewDecision:
    record = next((item for item in dataset.records if item.clip_id == clip_id), None)
    if record is None:
        raise KeyError(clip_id)
    decision = ReviewDecision.model_validate(
        {
            **payload,
            "clip_id": clip_id,
            "reviewer_source": "human",
            "reviewed_at": datetime.now(UTC),
        }
    )
    if record.verification_status == "NO_REFERENCE" and decision.action == "ACCEPT_REFERENCE":
        raise ValueError("NO_REFERENCE has no candidate to accept")
    if (
        record.strategy_critical
        and decision.action == "ACCEPT_REFERENCE"
        and not decision.critical_terms
    ):
        raise ValueError("strategy-critical acceptance requires human critical-term labels")
    reviews.decisions[clip_id] = decision
    return decision


def promote_reference(
    *,
    manifest: FrozenManifest,
    references: ReferenceDataset,
    external: ExternalReferenceDataset,
    reviews: RecoveryReviewDataset,
    provenance: PromotionDataset,
    clip_id: str,
    explicit_confirmation: bool,
    usability: str | None = None,
    speaker_type: str | None = None,
    critical_terms: list[dict] | None = None,
    override_existing: bool = False,
) -> PromotionRecord:
    if not explicit_confirmation:
        raise ValueError("promotion requires an explicit confirmation flag")
    if references.selection_hash != manifest.selection_hash:
        raise ValueError("canonical references differ from frozen manifest")
    if (
        external.selection_hash != manifest.selection_hash
        or reviews.selection_hash != manifest.selection_hash
    ):
        raise ValueError("recovery artifacts differ from frozen manifest")
    if clip_id in references.annotations and not override_existing:
        raise ValueError("canonical human reference already exists; refusing silent overwrite")
    record = next((item for item in external.records if item.clip_id == clip_id), None)
    if record is None:
        raise KeyError(clip_id)
    if record.verification_status in {"NO_REFERENCE", "REFERENCE_CONFLICT"}:
        raise ValueError(f"{record.verification_status} cannot be promoted")
    decision = reviews.decisions.get(clip_id)
    if record.requires_human_review:
        if decision is None or decision.action != "ACCEPT_REFERENCE":
            raise ValueError("candidate requires explicit human acceptance")
        transcript = decision.accepted_transcript
        usability = decision.usability
        speaker_type = decision.speaker_type
        terms = [term.model_dump(mode="json") for term in decision.critical_terms]
        human_confirmed = True
    else:
        transcript = record.recovered_transcript
        terms = critical_terms or []
        human_confirmed = False
    save_reference(
        references,
        manifest,
        clip_id,
        {
            "reference_transcript": transcript,
            "usability": usability,
            "speaker_type": speaker_type,
            "critical_terms": terms,
            "notes": f"Promoted from external human source: {record.source_name}",
            "complete": True,
        },
    )
    promotion = PromotionRecord(
        clip_id=clip_id,
        source_name=record.source_name or "",
        source_url=record.source_url or "",
        verification_status=record.verification_status,
        human_confirmed=human_confirmed,
        promoted_at=datetime.now(UTC),
    )
    provenance.promotions[clip_id] = promotion
    return promotion
