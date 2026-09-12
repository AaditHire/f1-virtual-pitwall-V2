from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from f1_pitwall.knowledge.chunking import chunk_documents, fixed_window_chunks, section_chunks
from f1_pitwall.knowledge.evaluation import evaluate_retriever
from f1_pitwall.knowledge.models import (
    BenchmarkQuestion,
    KnowledgeDocument,
    MetadataFilter,
    stable_id,
)
from f1_pitwall.knowledge.retrieval import BM25Retriever, DenseLSARetriever, HybridRetriever
from f1_pitwall.knowledge.routing import QueryRoute, classify_query_boundary

ROOT = Path(__file__).resolve().parents[2]


def document(source_key: str, text: str, **overrides) -> KnowledgeDocument:
    values = {
        "source_key": source_key,
        "source_name": "Test authority",
        "source_url": f"https://example.test/{source_key}",
        "source_type": "OFFICIAL_EDITORIAL",
        "authority_tier": "OFFICIAL",
        "title": source_key,
        "text": text,
        "season": None,
        "event": None,
        "driver_tags": [],
        "constructor_tags": [],
        "circuit_tags": [],
        "topic_tags": [],
        "provenance": "Test fixture with explicit provenance.",
        "retrieved_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    values.update(overrides)
    values["document_id"] = stable_id(
        "doc",
        {
            "source_key": source_key,
            "source_name": values["source_name"],
            "source_type": values["source_type"],
        },
    )
    return KnowledgeDocument.model_validate(values)


def question(source_key: str, query: str = "Why is Monaco difficult?") -> BenchmarkQuestion:
    values = {
        "query": query,
        "category": "CIRCUIT_HISTORY",
        "expected_route": "KNOWLEDGE_RETRIEVAL",
        "relevant_source_keys": [source_key],
    }
    identity = {
        "query": query,
        "category": values["category"],
        "relevant_source_keys": values["relevant_source_keys"],
    }
    return BenchmarkQuestion(
        question_id=stable_id("q", identity),
        key_facts=["narrow"],
        metadata_filter=MetadataFilter(),
        **values,
    )


def test_document_identity_is_deterministic_and_provenance_is_retained():
    first = document("monaco", "A narrow street circuit.")
    second = document("monaco", "Updated concise text.")
    assert first.document_id == second.document_id
    assert first.provenance == "Test fixture with explicit provenance."
    with pytest.raises(ValidationError, match="document_id is not deterministic"):
        first.model_copy(update={"document_id": "wrong"}).model_validate(
            {**first.model_dump(), "document_id": "wrong"}
        )


def test_section_chunking_preserves_heading_body_and_provenance():
    source = document(
        "rules",
        "## Safety Car\nCars follow prescribed signals.\n\n"
        "## Restart\nOvertaking remains restricted.",
    )
    chunks = section_chunks(source)
    assert [(row.heading, row.text) for row in chunks] == [
        ("Safety Car", "Cars follow prescribed signals."),
        ("Restart", "Overtaking remains restricted."),
    ]
    assert all(row.source_url == source.source_url for row in chunks)
    assert all(row.provenance == source.provenance for row in chunks)


def test_fixed_chunking_is_deterministic_and_validates_overlap():
    source = document("long", " ".join(f"word{index}" for index in range(30)))
    first = fixed_window_chunks(source, window_words=10, overlap_words=2)
    second = fixed_window_chunks(source, window_words=10, overlap_words=2)
    assert [row.chunk_id for row in first] == [row.chunk_id for row in second]
    assert len(first) == 4
    with pytest.raises(ValueError, match="larger than overlap"):
        fixed_window_chunks(source, window_words=10, overlap_words=10)


def test_structured_document_remains_one_complete_chunk():
    source = document(
        "result",
        "one two three four five six",
        source_type="STRUCTURED_RESULT",
        authority_tier="STRUCTURED_AUTHORITATIVE",
    )
    assert len(chunk_documents([source], "section")) == 1
    assert len(chunk_documents([source], "fixed")) == 1


def test_lexical_dense_and_hybrid_retrieve_relevant_evidence():
    documents = [
        document(
            "monaco", "Monaco is narrow and overtaking is difficult.", circuit_tags=["monaco"]
        ),
        document(
            "monza", "Monza rewards low drag and straight-line speed.", circuit_tags=["monza"]
        ),
        document(
            "rules", "Parc ferme restricts substantial setup changes.", topic_tags=["parc ferme"]
        ),
    ]
    chunks = chunk_documents(documents, "fixed")
    lexical = BM25Retriever(chunks)
    dense = DenseLSARetriever(chunks, dimensions=2)
    hybrid = HybridRetriever(lexical, dense)
    for retriever in (lexical, dense, hybrid):
        result = retriever.search("Why is overtaking difficult at Monaco?", 2)
        assert "monaco" in [row.chunk.source_key for row in result]


def test_metadata_filters_control_season_event_driver_and_circuit():
    chunks = chunk_documents(
        [
            document(
                "bahrain-2021",
                "Bahrain winner context.",
                season=2021,
                event="Bahrain Grand Prix",
                driver_tags=["Lewis Hamilton"],
                circuit_tags=["bahrain"],
            ),
            document(
                "bahrain-2024",
                "Bahrain winner context.",
                season=2024,
                event="Bahrain Grand Prix",
                driver_tags=["Max Verstappen"],
                circuit_tags=["bahrain"],
            ),
        ],
        "fixed",
    )
    retriever = BM25Retriever(chunks)
    selected = retriever.search(
        "Bahrain winner",
        metadata=MetadataFilter(
            season=2021,
            event="Bahrain Grand Prix",
            driver="Lewis Hamilton",
            circuit="bahrain",
        ),
    )
    assert [row.chunk.source_key for row in selected] == ["bahrain-2021"]


def test_retrieval_does_not_mutate_source_documents_or_chunks():
    docs = [
        document("source", "Ground effect creates floor downforce."),
        document("other", "Monaco is a narrow street circuit."),
    ]
    chunks = chunk_documents(docs)
    before_docs = deepcopy(docs)
    before_chunks = deepcopy(chunks)
    BM25Retriever(chunks).search("ground effect")
    DenseLSARetriever(chunks).search("floor downforce")
    assert docs == before_docs
    assert chunks == before_chunks


def test_empty_corpus_and_query_without_evidence_are_clean():
    assert BM25Retriever([]).search("anything") == []
    assert DenseLSARetriever([]).search("anything") == []
    retriever = BM25Retriever(chunk_documents([document("source", "Monaco circuit")]))
    assert retriever.search("quantum bananas") == []
    metrics = evaluate_retriever(retriever, [question("missing", "quantum bananas")], False)
    assert metrics["recall_at_5"] == 0


def test_missing_optional_metadata_is_supported_but_unknown_fields_are_rejected():
    source = document("minimal", "A concise supported fact.")
    assert source.season is None
    with pytest.raises(ValidationError):
        KnowledgeDocument.model_validate({**source.model_dump(), "unknown": "leak"})


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Who finished P4 at Bahrain 2024?", QueryRoute.STRUCTURED_FACT),
        ("What was the podium at Monaco 2023?", QueryRoute.STRUCTURED_FACT),
        ("Why is overtaking difficult at Monaco?", QueryRoute.KNOWLEDGE_RETRIEVAL),
        ("What is parc ferme?", QueryRoute.KNOWLEDGE_RETRIEVAL),
    ],
)
def test_structured_query_boundary(query, expected):
    assert classify_query_boundary(query) == expected


def test_frozen_research_artifacts_are_bounded_provenance_first_and_radio_free():
    corpus = json.loads((ROOT / "docs/phase13a-knowledge-corpus.json").read_text(encoding="utf-8"))
    benchmark = json.loads(
        (ROOT / "docs/phase13a-retrieval-benchmark.json").read_text(encoding="utf-8")
    )
    assert 30 <= len(corpus) <= 200
    assert len(benchmark) == 40
    assert len({row["document_id"] for row in corpus}) == len(corpus)
    assert len({row["question_id"] for row in benchmark}) == len(benchmark)
    assert all(row["source_url"] and row["provenance"] for row in corpus)
    assert all(row["source_type"] != "RADIO" for row in corpus)
    serialized = json.dumps(corpus).casefold()
    assert "small.en" not in serialized
    assert "medium.en" not in serialized
    assert "radio transcript" not in serialized


def test_benchmark_filters_cover_temporal_and_entity_ambiguity():
    rows = json.loads((ROOT / "docs/phase13a-retrieval-benchmark.json").read_text(encoding="utf-8"))
    assert sum(row["metadata_filter"].get("season") is not None for row in rows) >= 20
    assert sum(row["metadata_filter"].get("event") is not None for row in rows) >= 20
    assert sum(row["metadata_filter"].get("driver") is not None for row in rows) >= 5
    assert {row["expected_route"] for row in rows} == {
        "STRUCTURED_FACT",
        "KNOWLEDGE_RETRIEVAL",
    }
