from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from f1_pitwall.knowledge.chunking import chunk_documents
from f1_pitwall.knowledge.generation import (
    GeneratedAnswer,
    GroundedEvidenceBundle,
    deterministic_structured_answer,
)
from f1_pitwall.knowledge.hardening import RouteType, classify_hardening_route
from f1_pitwall.knowledge.models import KnowledgeDocument
from f1_pitwall.knowledge.reliability import (
    PHASE13C_BENCHMARK_SHA256,
    PHASE13C_EVALUATION_SHA256,
    PHASE13C_HUMAN_REVIEW_SHA256,
    PHASE13C_OUTPUTS_SHA256,
    PHASE13D_BENCHMARK_SHA256,
    FailureCategory,
    Phase13DOutput,
    Phase13DQuestion,
    UsageAccounting,
    assemble_bundle,
    classify_failure,
    evaluate_outputs,
    load_benchmark,
    sha256,
    validate_provenance,
    write_outputs_once,
)
from f1_pitwall.knowledge.retrieval import BM25Retriever
from scripts.research_phase13d import BENCHMARK_PATH, build_benchmark

DOCS = Path(__file__).resolve().parents[2] / "docs"


@pytest.fixture(scope="module")
def questions() -> list[Phase13DQuestion]:
    return load_benchmark(BENCHMARK_PATH)


@pytest.fixture(scope="module")
def retriever() -> BM25Retriever:
    documents = [
        KnowledgeDocument.model_validate(row)
        for row in json.loads((DOCS / "phase13b-knowledge-corpus.json").read_text(encoding="utf-8"))
    ]
    return BM25Retriever(chunk_documents(documents, "section"))


def test_legacy_phase13c_artifacts_remain_frozen():
    assert sha256(DOCS / "phase13c-answer-benchmark.json") == PHASE13C_BENCHMARK_SHA256
    assert sha256(DOCS / "phase13c-generation-outputs.json") == PHASE13C_OUTPUTS_SHA256
    assert sha256(DOCS / "phase13c-answer-evaluation.json") == PHASE13C_EVALUATION_SHA256
    assert sha256(DOCS / "phase13c-human-review-results.json") == PHASE13C_HUMAN_REVIEW_SHA256


def test_phase13d_benchmark_is_frozen_and_builder_is_deterministic(questions):
    assert sha256(BENCHMARK_PATH) == PHASE13D_BENCHMARK_SHA256
    assert build_benchmark() == [row.model_dump(mode="json") for row in questions]


def test_phase13d_composition_and_route_boundary(questions):
    assert Counter(row.route_type.value for row in questions) == {
        "STRUCTURED_ONLY": 30,
        "RAG_ONLY": 75,
        "MIXED": 25,
    }
    assert sum(row.generation_required for row in questions) == 100
    assert sum(row.expected_refusal for row in questions) == 5
    assert sum(row.adversarial_kind == "PROMPT_INJECTION" for row in questions) == 3
    assert sum(row.adversarial_kind == "SOURCE_CONFLICT" for row in questions) == 2
    assert all(classify_hardening_route(row.question) == row.route_type for row in questions)


def test_phase13d_questions_are_distinct_and_provenance_bounded(questions):
    corpus = json.loads((DOCS / "phase13b-knowledge-corpus.json").read_text(encoding="utf-8"))
    phase13c = json.loads((DOCS / "phase13c-answer-benchmark.json").read_text(encoding="utf-8"))
    validate_provenance(
        questions,
        {row["source_key"]: row["text"] for row in corpus},
        {row["question"] for row in phase13c},
    )
    serialized = json.dumps([row.model_dump(mode="json") for row in questions]).casefold()
    assert "radio transcript" not in serialized
    assert "small.en" not in serialized
    assert "medium.en" not in serialized
    assert "agentrouter_api_key" not in serialized


def test_all_answerable_questions_assemble_complete_evidence(questions, retriever):
    for question in questions:
        bundle = assemble_bundle(question, retriever)
        if not question.evidence_available:
            assert bundle.evidence_state.value == "INSUFFICIENT"
            continue
        assert {source.source_key for source in bundle.sources} >= set(
            question.required_source_keys
        ), question.question_id
        assert bundle.evidence_state.value == "COMPLETE"


def test_structured_regressions_bypass_provider_and_preserve_exact_facts(questions, retriever):
    structured = [row for row in questions if row.route_type == RouteType.STRUCTURED_ONLY]
    assert len(structured) == 30
    for question in structured:
        bundle = assemble_bundle(question, retriever)
        answer = deterministic_structured_answer(bundle)
        assert all(
            fact.value.casefold() in answer.answer.casefold() for fact in question.expected_facts
        )


@pytest.mark.parametrize(
    ("error", "stop_reason", "answer", "expected"),
    [
        ("MALFORMED_RESPONSE", "max_tokens", None, FailureCategory.TRUNCATED_RESPONSE),
        ("MALFORMED_RESPONSE", None, None, FailureCategory.MALFORMED_RESPONSE),
        ("SCHEMA_FAILURE", None, None, FailureCategory.SCHEMA_FAILURE),
        ("TIMEOUT", None, None, FailureCategory.TRANSPORT_FAILURE),
        (None, None, None, FailureCategory.MISSING_ANSWER),
        (None, None, "supported", FailureCategory.SUCCESS),
    ],
)
def test_failure_classification_is_mechanical(error, stop_reason, answer, expected):
    bundle = GroundedEvidenceBundle(
        question="Fixture",
        route_type="RAG_ONLY",
        evidence_state="INSUFFICIENT",
    )
    output = Phase13DOutput(
        question_id="fixture",
        route_type="RAG_ONLY",
        provider="AgentRouter",
        answer=(GeneratedAnswer(answer=answer, status="SUPPORTED") if answer is not None else None),
        bundle=bundle,
        provider_error_category=error,
        stop_reason=stop_reason,
    )
    assert classify_failure(output) == expected


def test_evaluator_collects_registered_metrics_without_provider_calls(questions, retriever):
    outputs = []
    for question in questions:
        bundle = assemble_bundle(question, retriever)
        if question.route_type == RouteType.STRUCTURED_ONLY:
            answer = deterministic_structured_answer(bundle)
            provider = None
            request_count = 0
        elif question.expected_refusal:
            answer = GeneratedAnswer(
                answer="The available project sources do not establish that.",
                status="INSUFFICIENT_EVIDENCE",
            )
            provider = "AgentRouter"
            request_count = 1
        else:
            by_key = {source.source_key: source.source_id for source in bundle.sources}
            text = "; ".join(
                f"{fact.value} [{by_key[fact.source_keys[0]]}]" for fact in question.expected_facts
            )
            answer = GeneratedAnswer(
                answer=text,
                status="SUPPORTED",
                citations=[by_key[key] for key in question.required_source_keys],
            )
            provider = "AgentRouter"
            request_count = 1
        outputs.append(
            Phase13DOutput(
                question_id=question.question_id,
                route_type=question.route_type,
                provider=provider,
                model_id="claude-opus-4-8" if provider else None,
                answer=answer,
                bundle=bundle,
                raw_response_present=bool(provider),
                request_count=request_count,
                usage=UsageAccounting(
                    prompt_tokens=10 if provider else 0,
                    completion_tokens=5 if provider else 0,
                    total_tokens=15 if provider else 0,
                ),
            )
        )
    metrics = evaluate_outputs(questions, outputs)
    assert metrics["generation_questions"] == 100
    assert metrics["malformed_generations"] == 0
    assert metrics["unrecovered_failures"] == 0
    assert metrics["structured_fact_accuracy"] == 1
    assert metrics["mixed_exact_fact_consistency"] == 1
    assert metrics["citation_validity"] == 1
    assert metrics["citation_support"] == 1
    assert metrics["refusal_correctness"] == 1
    assert metrics["false_refusals"] == 0
    assert metrics["multi_document_synthesis"] == 1
    assert metrics["conflict_handling"] == 1
    assert metrics["prompt_injection_resistance"] == 1
    assert metrics["route_compliance"] == 1
    assert metrics["request_count"] == 100
    assert metrics["total_tokens"] == 1500


def test_malformed_output_remains_in_reliability_and_citation_denominators(questions, retriever):
    question = next(row for row in questions if row.route_type == RouteType.RAG_ONLY)
    output = Phase13DOutput(
        question_id=question.question_id,
        route_type=question.route_type,
        provider="AgentRouter",
        model_id="claude-opus-4-8",
        answer=None,
        bundle=assemble_bundle(question, retriever),
        provider_error_category="MALFORMED_RESPONSE",
        stop_reason="max_tokens",
        raw_response_present=True,
        request_count=1,
    )
    metrics = evaluate_outputs([question], [output])
    assert metrics["malformed_generations"] == 1
    assert metrics["truncated_generations"] == 1
    assert metrics["unrecovered_failures"] == 1
    assert metrics["citation_complete_total"] == 1
    assert metrics["citation_complete_answers"] == 0
    assert metrics["citation_supported_total"] == 1
    assert metrics["citation_supported_answers"] == 0


def test_outputs_must_match_frozen_order(questions):
    with pytest.raises(ValueError, match="exactly match frozen benchmark order"):
        evaluate_outputs(questions, [])


def test_prospective_output_serialization_is_ordered_and_overwrite_protected(questions, retriever):
    question = questions[0]
    output = Phase13DOutput(
        question_id=question.question_id,
        route_type=question.route_type,
        answer=deterministic_structured_answer(assemble_bundle(question, retriever)),
        bundle=assemble_bundle(question, retriever),
    )
    path = Path(".cache/phase13d-test-output.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    try:
        digest = write_outputs_once(path, [question], [output])
        assert digest == sha256(path)
        with pytest.raises(FileExistsError, match="refusing to replace"):
            write_outputs_once(path, [question], [output])
    finally:
        path.unlink(missing_ok=True)


def test_prospective_manifest_is_not_run_and_changes_only_output_allowance():
    manifest = json.loads(
        (DOCS / "phase13d-prospective-run-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "NOT_RUN"
    assert manifest["benchmark"]["sha256"] == PHASE13D_BENCHMARK_SHA256
    assert manifest["benchmark"]["provider_generation_questions"] == 100
    assert manifest["experiment_a"]["max_output_tokens"] == 640
    assert manifest["experiment_a"]["changed_variable"] == "max_output_tokens_only"
    assert manifest["experiment_a"]["system_prompt_sha256"] == (
        "d272ed62b715c9b49eb2769cae0366542f5642f8f47b54d6ffc8ae652d70c7d9"
    )
    assert manifest["experiment_a"]["new_schema_or_truncation_retries"] == 0
    assert manifest["production_decision"] == "NO_GO_UNCHANGED"
