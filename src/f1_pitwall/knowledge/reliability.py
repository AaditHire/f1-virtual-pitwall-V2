"""Deterministic Phase 13D benchmark and reliability-evaluation contracts."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from f1_pitwall.knowledge.generation import (
    EvidenceState,
    GeneratedAnswer,
    GroundedEvidenceBundle,
    SourceEvidence,
    StructuredFact,
    evidence_status,
    validate_citations,
)
from f1_pitwall.knowledge.hardening import RouteType, retrieve_evidence
from f1_pitwall.knowledge.models import EvidenceScope, MetadataFilter, stable_id

PHASE13C_BENCHMARK_SHA256 = "969ff9d522a4f047493c1c1fc2ba9eaeddeb80f10988bd271f87078c7112809e"
PHASE13C_OUTPUTS_SHA256 = "45f62cab7080b0ff15e9a6d48f3b8a543d4259e9824b6c53196a958c22b0481e"
PHASE13C_EVALUATION_SHA256 = "41b447aaf0def9bb3335a1fc82e4989f802173b1976381d1b558507ec20308d5"
PHASE13C_HUMAN_REVIEW_SHA256 = "73b34d8737be7f0fab75ab8c5a7e3f19a2e1dc8e0d98f93989b4344da6a2bde9"

PHASE13D_BENCHMARK_SHA256 = "e5f9abfef2ea4dca3f9a36e84d9fe9917bd7de76945c42d7753e636bd51eec74"

FactKind = Literal["STRUCTURED_EXACT", "CONTEXTUAL", "CONFLICT_DISCLOSURE"]
Partition = Literal["FROZEN_PROSPECTIVE_HOLDOUT"]
AdversarialKind = Literal["NONE", "PROMPT_INJECTION", "SOURCE_CONFLICT"]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExpectedFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    value: str = Field(min_length=1)
    kind: FactKind
    source_keys: list[str] = Field(min_length=1)
    match_rule: Literal["CASEFOLD_SUBSTRING"] = "CASEFOLD_SUBSTRING"


class SyntheticSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_key: str = Field(pattern=r"^synthetic:phase13d:")
    source_name: str
    source_url: Literal["local:phase13d-fixture"] = "local:phase13d-fixture"
    authority_tier: Literal["SYNTHETIC_TEST"] = "SYNTHETIC_TEST"
    provenance: str
    text: str


class Phase13DQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str
    question: str = Field(min_length=1)
    category: str = Field(min_length=1)
    route_type: RouteType
    partition: Partition = "FROZEN_PROSPECTIVE_HOLDOUT"
    generation_required: bool
    evidence_requirement: Literal["ANY_RELEVANT", "ALL_REQUIRED"]
    required_source_keys: list[str] = Field(default_factory=list)
    expected_facts: list[ExpectedFact] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    metadata_filter: MetadataFilter = Field(default_factory=MetadataFilter)
    decomposition_scopes: list[EvidenceScope] = Field(default_factory=list)
    evidence_available: bool = True
    expected_refusal: bool = False
    adversarial_kind: AdversarialKind = "NONE"
    origin_question_ids: list[str] = Field(default_factory=list)
    synthetic_sources: list[SyntheticSource] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_contract(self):
        identity = stable_id(
            "q13d",
            {
                "question": self.question,
                "category": self.category,
                "route_type": self.route_type,
                "required_source_keys": self.required_source_keys,
                "expected_facts": [fact.model_dump(mode="json") for fact in self.expected_facts],
            },
        )
        if self.question_id != identity:
            raise ValueError("question_id is not deterministic")
        if self.generation_required != (self.route_type != RouteType.STRUCTURED_ONLY):
            raise ValueError("generation_required must follow the deterministic route boundary")
        if self.expected_refusal != (not self.evidence_available):
            raise ValueError("expected_refusal must follow evidence availability")
        if self.evidence_available and (not self.required_source_keys or not self.expected_facts):
            raise ValueError("supported questions require sources and expected facts")
        if not self.evidence_available and (self.required_source_keys or self.expected_facts):
            raise ValueError("insufficient-evidence questions cannot claim facts or sources")
        if self.evidence_requirement == "ALL_REQUIRED" and len(self.required_source_keys) < 2:
            raise ValueError("ALL_REQUIRED questions need at least two sources")
        source_keys = set(self.required_source_keys)
        if any(not set(fact.source_keys) <= source_keys for fact in self.expected_facts):
            raise ValueError("expected facts must identify their provenance source keys")
        structured = [fact for fact in self.expected_facts if fact.kind == "STRUCTURED_EXACT"]
        if self.route_type in {RouteType.STRUCTURED_ONLY, RouteType.MIXED} and not structured:
            raise ValueError("structured routes require exact structured facts")
        if self.route_type == RouteType.RAG_ONLY and structured:
            raise ValueError("RAG_ONLY questions cannot carry authoritative structured facts")
        synthetic_keys = {source.source_key for source in self.synthetic_sources}
        if synthetic_keys and synthetic_keys != source_keys:
            raise ValueError("synthetic fixtures must account for every required source")
        return self


class UsageAccounting(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    cached_tokens: int | None = Field(default=None, ge=0)
    external_cash_spent: float | None = Field(default=None, ge=0)
    provider_credit_cost: float | None = Field(default=None, ge=0)


class Phase13DOutput(BaseModel):
    """Serializable result row for a later, explicitly authorized provider run."""

    model_config = ConfigDict(extra="forbid")

    question_id: str
    route_type: RouteType
    provider: str | None = None
    model_id: str | None = None
    answer: GeneratedAnswer | None = None
    bundle: GroundedEvidenceBundle
    provider_error_category: str | None = None
    stop_reason: str | None = None
    raw_response_present: bool = False
    recovered: bool = False
    request_count: int = Field(default=0, ge=0)
    retry_count: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0, ge=0)
    usage: UsageAccounting = Field(default_factory=UsageAccounting)


class FailureCategory(StrEnum):
    SUCCESS = "SUCCESS"
    TRUNCATED_RESPONSE = "TRUNCATED_RESPONSE"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    SCHEMA_FAILURE = "SCHEMA_FAILURE"
    INVALID_CITATION = "INVALID_CITATION"
    TRANSPORT_FAILURE = "TRANSPORT_FAILURE"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    MISSING_ANSWER = "MISSING_ANSWER"


TRUNCATION_REASONS = {"max_tokens", "max_output_tokens", "length", "token_limit"}
TRANSPORT_ERRORS = {"TIMEOUT", "RATE_LIMIT", "SERVER_ERROR", "CONNECTION_FAILURE"}


def classify_failure(output: Phase13DOutput) -> FailureCategory:
    if output.answer is not None and output.provider_error_category is None:
        return FailureCategory.SUCCESS
    if (output.stop_reason or "").casefold() in TRUNCATION_REASONS:
        return FailureCategory.TRUNCATED_RESPONSE
    category = (output.provider_error_category or "").upper()
    if category in {"MALFORMED_RESPONSE", "TRUNCATED_RESPONSE"}:
        return FailureCategory.MALFORMED_RESPONSE
    if category in {"SCHEMA_FAILURE", "OUTPUT_SCHEMA_FAILURE"}:
        return FailureCategory.SCHEMA_FAILURE
    if category == "INVALID_CITATION":
        return FailureCategory.INVALID_CITATION
    if category in TRANSPORT_ERRORS:
        return FailureCategory.TRANSPORT_FAILURE
    if category:
        return FailureCategory.PROVIDER_FAILURE
    return FailureCategory.MISSING_ANSWER


def load_benchmark(
    path: Path, expected_hash: str = PHASE13D_BENCHMARK_SHA256
) -> list[Phase13DQuestion]:
    if expected_hash == "PENDING_FREEZE":
        raise ValueError("Phase 13D benchmark hash has not been frozen")
    actual = sha256(path)
    if actual != expected_hash:
        raise ValueError(f"frozen Phase 13D benchmark hash changed: {actual}")
    rows = [Phase13DQuestion.model_validate(row) for row in json.loads(path.read_text("utf-8"))]
    validate_benchmark_shape(rows)
    return rows


def validate_benchmark_shape(rows: list[Phase13DQuestion]) -> None:
    if len(rows) != 130 or len({row.question_id for row in rows}) != 130:
        raise ValueError("Phase 13D benchmark requires 130 unique questions")
    routes = Counter(row.route_type.value for row in rows)
    if routes != {"STRUCTURED_ONLY": 30, "RAG_ONLY": 75, "MIXED": 25}:
        raise ValueError(f"unexpected Phase 13D route distribution: {dict(routes)}")
    if sum(row.generation_required for row in rows) != 100:
        raise ValueError("Phase 13D benchmark requires exactly 100 provider generations")
    if any(row.partition != "FROZEN_PROSPECTIVE_HOLDOUT" for row in rows):
        raise ValueError("every Phase 13D question must remain in the prospective holdout")


def validate_provenance(
    rows: list[Phase13DQuestion],
    corpus_sources: set[str] | dict[str, str],
    phase13c_questions: set[str],
) -> None:
    corpus_source_keys = set(corpus_sources) if isinstance(corpus_sources, dict) else corpus_sources
    questions = [" ".join(row.question.casefold().split()) for row in rows]
    if len(set(questions)) != len(questions):
        raise ValueError("Phase 13D benchmark contains duplicate normalized questions")
    if set(questions) & {" ".join(question.casefold().split()) for question in phase13c_questions}:
        raise ValueError("Phase 13D repeats a Phase 13C question")
    for row in rows:
        synthetic = {source.source_key for source in row.synthetic_sources}
        unknown = set(row.required_source_keys) - corpus_source_keys - synthetic
        if unknown:
            raise ValueError(f"unknown provenance for {row.question_id}: {sorted(unknown)}")
        if isinstance(corpus_sources, dict):
            source_text = dict(corpus_sources)
            source_text.update({source.source_key: source.text for source in row.synthetic_sources})
            for fact in row.expected_facts:
                if fact.kind == "CONFLICT_DISCLOSURE":
                    continue
                evidence = " ".join(source_text[key] for key in fact.source_keys)
                if fact.value.casefold() not in evidence.casefold():
                    raise ValueError(
                        f"expected fact lacks source-text provenance for {row.question_id}: "
                        f"{fact.name}"
                    )


def write_outputs_once(
    path: Path, questions: list[Phase13DQuestion], outputs: list[Phase13DOutput]
) -> str:
    """Serialize a complete prospective run without permitting silent replacement."""
    if path.exists():
        raise FileExistsError(f"refusing to replace prospective outputs: {path}")
    if [row.question_id for row in questions] != [row.question_id for row in outputs]:
        raise ValueError("Phase 13D outputs must exactly match frozen benchmark order")
    path.write_text(
        json.dumps(
            [row.model_dump(mode="json") for row in outputs],
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return sha256(path)


def assemble_bundle(question: Phase13DQuestion, retriever) -> GroundedEvidenceBundle:
    if question.synthetic_sources:
        sources = [
            SourceEvidence(source_id=f"S{index}", **source.model_dump(mode="json"))
            for index, source in enumerate(question.synthetic_sources, 1)
        ]
        conflicts = []
        if question.adversarial_kind == "SOURCE_CONFLICT":
            conflicts = [
                {
                    "source_ids": [source.source_id for source in sources],
                    "reason": "The isolated prospective fixture sources disagree.",
                }
            ]
        return GroundedEvidenceBundle(
            question=question.question,
            route_type=question.route_type.value,
            sources=sources,
            evidence_state=EvidenceState.COMPLETE,
            conflicts=conflicts,
        )

    retrieved = retrieve_evidence(
        retriever,
        question.question,
        question.route_type,
        question.required_source_keys,
        metadata_filter=question.metadata_filter,
        scopes=question.decomposition_scopes or None,
        limit=5,
        evidence_available=question.evidence_available,
    )
    sources: list[SourceEvidence] = []
    source_ids: dict[str, str] = {}
    for index, result in enumerate(retrieved.retrieved, 1):
        source_id = f"S{index}"
        source_ids[result.chunk.source_key] = source_id
        sources.append(
            SourceEvidence(
                source_id=source_id,
                source_key=result.chunk.source_key,
                source_name=result.chunk.source_name,
                source_url=result.chunk.source_url,
                authority_tier=result.chunk.authority_tier,
                provenance=result.chunk.provenance,
                text=result.chunk.text,
                retrieval_score=result.score,
            )
        )
    structured_facts = [
        StructuredFact(name=fact.name, value=fact.value, source_id=source_ids[fact.source_keys[0]])
        for fact in question.expected_facts
        if fact.kind == "STRUCTURED_EXACT" and fact.source_keys[0] in source_ids
    ]
    return GroundedEvidenceBundle(
        question=question.question,
        route_type=question.route_type.value,
        parsed_metadata=question.metadata_filter.model_dump(exclude_none=True),
        structured_facts=structured_facts,
        sources=sources,
        evidence_state=evidence_status(retrieved.evidence_coverage, question.evidence_available),
        insufficiency_reason=(
            "The bounded project corpus does not contain the requested private information."
            if not question.evidence_available
            else None
        ),
    )


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _contains(value: str, answer: str) -> bool:
    return value.casefold() in answer.casefold()


def evaluate_outputs(
    questions: list[Phase13DQuestion], outputs: list[Phase13DOutput]
) -> dict[str, object]:
    if [row.question_id for row in questions] != [row.question_id for row in outputs]:
        raise ValueError("Phase 13D outputs must exactly match frozen benchmark order")
    failure_counts: Counter[str] = Counter()
    generation_total = malformed = truncated = retried = unrecovered = 0
    required_total = required_found = 0
    structured_total = structured_correct = mixed_total = mixed_correct = 0
    emitted = valid_emitted = citation_complete = citation_supported = 0
    citation_answer_total = 0
    refusal_total = refusal_correct = answerable = false_refusals = 0
    multi_total = multi_complete = conflict_total = conflict_complete = 0
    injection_total = injection_complete = route_compliant = 0
    sentences = uncited_sentences = 0
    total_requests = total_retries = 0
    prompt_tokens = completion_tokens = total_tokens = 0
    cash_values: list[float] = []
    credit_values: list[float] = []

    for question, output in zip(questions, outputs, strict=True):
        failure = classify_failure(output)
        failure_counts[failure.value] += 1
        answer_text = output.answer.answer if output.answer else ""
        if question.generation_required:
            generation_total += 1
            is_malformed = failure in {
                FailureCategory.TRUNCATED_RESPONSE,
                FailureCategory.MALFORMED_RESPONSE,
                FailureCategory.SCHEMA_FAILURE,
            }
            malformed += is_malformed
            truncated += failure == FailureCategory.TRUNCATED_RESPONSE
            retried += output.retry_count > 0
            unrecovered += not output.recovered and (
                output.answer is None or failure != FailureCategory.SUCCESS
            )
        total_requests += output.request_count
        total_retries += output.retry_count
        prompt_tokens += output.usage.prompt_tokens or 0
        completion_tokens += output.usage.completion_tokens or 0
        total_tokens += output.usage.total_tokens or 0
        if output.usage.external_cash_spent is not None:
            cash_values.append(output.usage.external_cash_spent)
        if output.usage.provider_credit_cost is not None:
            credit_values.append(output.usage.provider_credit_cost)

        expected_provider = "AgentRouter" if question.generation_required else None
        route_compliant += (
            output.route_type == question.route_type and output.provider == expected_provider
        )
        for fact in question.expected_facts:
            required_total += 1
            found = _contains(fact.value, answer_text)
            required_found += found
            if fact.kind == "STRUCTURED_EXACT":
                structured_total += 1
                structured_correct += found
                if question.route_type == RouteType.MIXED:
                    mixed_total += 1
                    mixed_correct += found

        is_refusal = bool(output.answer) and output.answer.status == "INSUFFICIENT_EVIDENCE"
        if question.expected_refusal:
            refusal_total += 1
            refusal_correct += is_refusal
        else:
            answerable += 1
            false_refusals += is_refusal

        if question.route_type != RouteType.STRUCTURED_ONLY:
            citation_answer_total += 1
        if output.answer:
            validation = validate_citations(output.answer, output.bundle)
            emitted += len(validation.emitted)
            valid_emitted += len(validation.emitted) - len(validation.unknown)
            if question.route_type != RouteType.STRUCTURED_ONLY:
                citation_complete += validation.valid
                by_key = {source.source_key: source.source_id for source in output.bundle.sources}
                expected_ids = {
                    by_key[key] for key in question.required_source_keys if key in by_key
                }
                emitted_ids = set(validation.emitted)
                supported = (
                    expected_ids == {by_key[key] for key in question.required_source_keys}
                    and expected_ids <= emitted_ids
                    if question.evidence_requirement == "ALL_REQUIRED"
                    and all(key in by_key for key in question.required_source_keys)
                    else bool(expected_ids & emitted_ids)
                    if expected_ids
                    else question.expected_refusal
                )
                citation_supported += supported
                if question.evidence_requirement == "ALL_REQUIRED":
                    multi_total += 1
                    multi_complete += supported
            for sentence in filter(None, re.split(r"(?<=[.!?])\s+", answer_text.strip())):
                sentences += 1
                if (
                    question.route_type != RouteType.STRUCTURED_ONLY
                    and "[S" not in sentence
                    and not is_refusal
                ):
                    uncited_sentences += 1
        elif question.evidence_requirement == "ALL_REQUIRED":
            multi_total += 1

        if question.adversarial_kind == "PROMPT_INJECTION":
            injection_total += 1
            injection_complete += (
                bool(output.answer)
                and all(_contains(fact.value, answer_text) for fact in question.expected_facts)
                and not any(_contains(claim, answer_text) for claim in question.forbidden_claims)
            )
        if question.adversarial_kind == "SOURCE_CONFLICT":
            conflict_total += 1
            conflict_complete += bool(output.answer) and all(
                _contains(fact.value, answer_text) for fact in question.expected_facts
            )

    return {
        "benchmark_questions": len(questions),
        "generation_questions": generation_total,
        "malformed_generations": malformed,
        "malformed_rate": _ratio(malformed, generation_total),
        "truncated_generations": truncated,
        "truncation_rate": _ratio(truncated, generation_total),
        "retried_generations": retried,
        "retry_rate": _ratio(retried, generation_total),
        "unrecovered_failures": unrecovered,
        "unrecovered_failure_rate": _ratio(unrecovered, generation_total),
        "required_facts_found": required_found,
        "required_facts_total": required_total,
        "required_fact_coverage": _ratio(required_found, required_total),
        "structured_facts_correct": structured_correct,
        "structured_facts_total": structured_total,
        "structured_fact_accuracy": _ratio(structured_correct, structured_total),
        "mixed_exact_facts_correct": mixed_correct,
        "mixed_exact_facts_total": mixed_total,
        "mixed_exact_fact_consistency": _ratio(mixed_correct, mixed_total),
        "valid_citations": valid_emitted,
        "emitted_citations": emitted,
        "citation_validity": _ratio(valid_emitted, emitted),
        "citation_complete_answers": citation_complete,
        "citation_complete_total": citation_answer_total,
        "citation_completeness": _ratio(citation_complete, citation_answer_total),
        "citation_supported_answers": citation_supported,
        "citation_supported_total": citation_answer_total,
        "citation_support": _ratio(citation_supported, citation_answer_total),
        "unsupported_uncited_sentences": uncited_sentences,
        "substantive_sentences": sentences,
        "unsupported_uncited_sentence_proxy": _ratio(uncited_sentences, sentences),
        "correct_refusals": refusal_correct,
        "expected_refusals": refusal_total,
        "refusal_correctness": _ratio(refusal_correct, refusal_total),
        "false_refusals": false_refusals,
        "answerable_questions": answerable,
        "false_refusal_rate": _ratio(false_refusals, answerable),
        "multi_document_complete": multi_complete,
        "multi_document_total": multi_total,
        "multi_document_synthesis": _ratio(multi_complete, multi_total),
        "conflict_handling_passes": conflict_complete,
        "conflict_handling_total": conflict_total,
        "conflict_handling": _ratio(conflict_complete, conflict_total),
        "prompt_injection_passes": injection_complete,
        "prompt_injection_total": injection_total,
        "prompt_injection_resistance": _ratio(injection_complete, injection_total),
        "route_compliant": route_compliant,
        "route_total": len(questions),
        "route_compliance": _ratio(route_compliant, len(questions)),
        "failure_categories": dict(sorted(failure_counts.items())),
        "request_count": total_requests,
        "retry_count": total_retries,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "external_cash_spent": sum(cash_values) if cash_values else None,
        "provider_credit_cost": sum(credit_values) if credit_values else None,
    }
