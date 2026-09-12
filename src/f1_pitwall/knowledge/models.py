"""Normalized, provenance-first knowledge and benchmark schemas."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AuthorityTier = Literal["STRUCTURED_AUTHORITATIVE", "OFFICIAL", "REPUTABLE_SECONDARY"]
SourceType = Literal["STRUCTURED_RESULT", "OFFICIAL_REGULATION", "OFFICIAL_EDITORIAL"]
QuestionCategory = Literal[
    "EVENT_HISTORY",
    "DRIVER_TEAM",
    "REGULATION_TERMINOLOGY",
    "CIRCUIT_HISTORY",
    "MULTI_DOCUMENT",
]
QueryRoute = Literal["STRUCTURED_FACT", "KNOWLEDGE_RETRIEVAL"]
HardeningRoute = Literal["STRUCTURED_ONLY", "RAG_ONLY", "MIXED"]
EvidenceRequirement = Literal["ANY_RELEVANT", "ALL_REQUIRED"]


def stable_id(prefix: str, payload: dict) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()[:20]}"


class KnowledgeDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    source_key: str = Field(min_length=1)
    source_name: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    source_type: SourceType
    authority_tier: AuthorityTier
    license_classification: Literal["RESEARCH_ACCEPTABLE", "PRODUCTION_REVIEW_REQUIRED"] = (
        "PRODUCTION_REVIEW_REQUIRED"
    )
    license_notes: str | None = None
    title: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=5000)
    publication_date: date | None = None
    event_date: date | None = None
    season: int | None = None
    event: str | None = None
    driver_tags: list[str] = Field(default_factory=list)
    constructor_tags: list[str] = Field(default_factory=list)
    circuit_tags: list[str] = Field(default_factory=list)
    topic_tags: list[str] = Field(default_factory=list)
    provenance: str = Field(min_length=1)
    retrieved_at: datetime

    @model_validator(mode="after")
    def deterministic_identity(self):
        expected = stable_id(
            "doc",
            {
                "source_key": self.source_key,
                "source_name": self.source_name,
                "source_type": self.source_type,
            },
        )
        if self.document_id != expected:
            raise ValueError("document_id is not deterministic for the source identity")
        return self


class KnowledgeChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    document_id: str
    source_key: str
    source_name: str
    source_url: str
    source_type: SourceType
    authority_tier: AuthorityTier
    license_classification: Literal["RESEARCH_ACCEPTABLE", "PRODUCTION_REVIEW_REQUIRED"] = (
        "PRODUCTION_REVIEW_REQUIRED"
    )
    license_notes: str | None = None
    title: str
    heading: str | None = None
    text: str = Field(min_length=1, max_length=5000)
    season: int | None = None
    event: str | None = None
    driver_tags: list[str] = Field(default_factory=list)
    constructor_tags: list[str] = Field(default_factory=list)
    circuit_tags: list[str] = Field(default_factory=list)
    topic_tags: list[str] = Field(default_factory=list)
    provenance: str
    ordinal: int = Field(ge=0)


class MetadataFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    season: int | None = None
    event: str | None = None
    driver: str | None = None
    constructor: str | None = None
    circuit: str | None = None
    topic: str | None = None


class BenchmarkQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str
    query: str = Field(min_length=1)
    category: QuestionCategory
    expected_route: QueryRoute
    relevant_source_keys: list[str] = Field(min_length=1)
    key_facts: list[str] = Field(min_length=1)
    metadata_filter: MetadataFilter = Field(default_factory=MetadataFilter)

    @model_validator(mode="after")
    def deterministic_identity(self):
        expected = stable_id(
            "q",
            {
                "query": self.query,
                "category": self.category,
                "relevant_source_keys": self.relevant_source_keys,
            },
        )
        if self.question_id != expected:
            raise ValueError("question_id is not deterministic")
        return self


class EvidenceScope(BaseModel):
    """One explicit retrieval scope; no entity or time value may be inferred."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    metadata_filter: MetadataFilter = Field(default_factory=MetadataFilter)


class HardeningBenchmarkQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str
    query: str = Field(min_length=1)
    category: str = Field(min_length=1)
    route_type: HardeningRoute
    evidence_requirement: EvidenceRequirement
    relevant_source_keys: list[str] = Field(default_factory=list)
    key_facts: list[str] = Field(default_factory=list)
    metadata_filter: MetadataFilter = Field(default_factory=MetadataFilter)
    decomposition_scopes: list[EvidenceScope] = Field(default_factory=list)
    evidence_available: bool = True

    @model_validator(mode="after")
    def validate_identity_and_evidence(self):
        expected = stable_id(
            "q13b",
            {
                "query": self.query,
                "category": self.category,
                "relevant_source_keys": self.relevant_source_keys,
            },
        )
        if self.question_id != expected:
            raise ValueError("question_id is not deterministic")
        if self.evidence_available and not self.relevant_source_keys:
            raise ValueError("supported questions require relevant evidence")
        if not self.evidence_available and self.relevant_source_keys:
            raise ValueError("coverage failures cannot claim relevant evidence")
        if self.evidence_requirement == "ALL_REQUIRED" and len(self.relevant_source_keys) < 2:
            raise ValueError("ALL_REQUIRED questions need at least two evidence documents")
        return self
