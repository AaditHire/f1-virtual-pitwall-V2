from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from f1_pitwall.knowledge.chunking import chunk_documents
from f1_pitwall.knowledge.entities import CONSTRUCTOR_LINEAGES, EntityCatalog
from f1_pitwall.knowledge.hardening import (
    FailureCategory,
    RouteType,
    classify_hardening_route,
    decompose_explicit_query,
    represent_conflict,
    retrieve_evidence,
)
from f1_pitwall.knowledge.models import EvidenceScope, KnowledgeDocument, MetadataFilter, stable_id
from f1_pitwall.knowledge.retrieval import BM25Retriever

ROOT = Path(__file__).resolve().parents[2]


def doc(key: str, text: str, **overrides) -> KnowledgeDocument:
    values = {
        "source_key": key,
        "source_name": "Fixture",
        "source_url": f"https://example.test/{key}",
        "source_type": "STRUCTURED_RESULT",
        "authority_tier": "STRUCTURED_AUTHORITATIVE",
        "license_classification": "RESEARCH_ACCEPTABLE",
        "license_notes": "Synthetic test fixture.",
        "title": key,
        "text": text,
        "season": None,
        "event": None,
        "driver_tags": [],
        "constructor_tags": [],
        "circuit_tags": [],
        "topic_tags": [],
        "provenance": "Synthetic test fixture.",
        "retrieved_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    values.update(overrides)
    values["document_id"] = stable_id(
        "doc", {name: values[name] for name in ("source_key", "source_name", "source_type")}
    )
    return KnowledgeDocument.model_validate(values)


def test_phase13a_artifacts_remain_frozen():
    expected = {
        "phase13a-knowledge-corpus.json": (
            "4b8d4215686574094f19ef5c6b97a313de8ae62cf30525a96909a8ec59804251"
        ),
        "phase13a-retrieval-benchmark.json": (
            "c29baf78c380ba2e749d8712dbbaeb8895c1167bda012c1d48a0824fbe4d1363"
        ),
        "phase13a-retrieval-results.json": (
            "9b331773513e68399f0fef82985cb95d09793f2b8566e066fa231272ec7bca27"
        ),
    }
    for name, digest in expected.items():
        assert hashlib.sha256((ROOT / "docs" / name).read_bytes()).hexdigest() == digest


def test_expanded_corpus_has_full_season_coverage_and_provenance():
    corpus = json.loads((ROOT / "docs/phase13b-knowledge-corpus.json").read_text(encoding="utf-8"))
    event_docs = [row for row in corpus if row["source_key"].startswith("event:")]
    assert len(corpus) == 309
    assert len(event_docs) == 114
    assert {row["season"] for row in event_docs} == {2021, 2022, 2023, 2024, 2025}
    assert len({row["document_id"] for row in corpus}) == len(corpus)
    assert all(row["source_url"] and row["provenance"] for row in corpus)
    assert all(row["license_classification"] for row in corpus)


def test_driver_aliases_are_explicit_and_ambiguous_aliases_fail_closed():
    catalog = EntityCatalog(
        driver_aliases={"hamilton": ("Lewis Hamilton", "HAM"), "other": ("Hamilton",)},
        constructor_aliases={},
    )
    assert catalog.resolve_driver("HAM") == "hamilton"
    assert catalog.resolve_driver("Hamilton") is None
    assert catalog.resolve_driver("invented") is None


def test_constructor_identity_is_distinct_from_lineage():
    catalog = EntityCatalog(
        driver_aliases={},
        constructor_aliases={"renault": ("Renault",), "alpine": ("Alpine F1 Team",)},
    )
    assert catalog.resolve_constructor("Renault") == "renault"
    assert catalog.resolve_constructor("Alpine F1 Team") == "alpine"
    assert catalog.resolve_constructor("Renault") != catalog.resolve_constructor("Alpine F1 Team")
    assert CONSTRUCTOR_LINEAGES["enstone"] == ("renault", "alpine")


def test_repeated_grand_prix_names_are_separated_by_season_filter():
    retriever = BM25Retriever(
        chunk_documents(
            [
                doc("b21", "Hamilton won Bahrain.", season=2021, event="Bahrain Grand Prix"),
                doc("b24", "Verstappen won Bahrain.", season=2024, event="Bahrain Grand Prix"),
            ]
        )
    )
    rows = retriever.search(
        "Who won Bahrain?",
        metadata=MetadataFilter(season=2024, event="Bahrain Grand Prix"),
    )
    assert [row.chunk.source_key for row in rows] == ["b24"]


def test_wrong_event_is_excluded_even_in_same_season():
    retriever = BM25Retriever(
        chunk_documents(
            [
                doc("b", "Winner result.", season=2024, event="Bahrain Grand Prix"),
                doc("m", "Winner result.", season=2024, event="Monaco Grand Prix"),
            ]
        )
    )
    rows = retriever.search(
        "winner", metadata=MetadataFilter(season=2024, event="Monaco Grand Prix")
    )
    assert [row.chunk.source_key for row in rows] == ["m"]


def test_query_decomposition_preserves_year_event_and_rejects_invention():
    scopes = [
        EvidenceScope(
            query="2021 Bahrain Grand Prix result",
            metadata_filter=MetadataFilter(season=2021, event="Bahrain Grand Prix"),
        ),
        EvidenceScope(
            query="2024 Bahrain Grand Prix result",
            metadata_filter=MetadataFilter(season=2024, event="Bahrain Grand Prix"),
        ),
    ]
    assert decompose_explicit_query("Compare Bahrain in 2021 and 2024", scopes) == scopes
    bad = [EvidenceScope(query="2023 Bahrain", metadata_filter=MetadataFilter(season=2023))]
    with pytest.raises(ValueError, match="year is not present"):
        decompose_explicit_query("Compare Bahrain in 2021 and 2024", bad)
    with pytest.raises(ValueError, match="ambiguous"):
        decompose_explicit_query("Compare Bahrain in 2021 and 2024", [])


def test_multi_document_merge_and_deduplication_complete_the_bundle():
    retriever = BM25Retriever(
        chunk_documents(
            [
                doc("b21", "Bahrain 2021 result", season=2021, event="Bahrain Grand Prix"),
                doc("b24", "Bahrain 2024 result", season=2024, event="Bahrain Grand Prix"),
            ]
        )
    )
    scopes = [
        EvidenceScope(query="Bahrain Grand Prix 2021", metadata_filter=MetadataFilter(season=2021)),
        EvidenceScope(query="Bahrain Grand Prix 2024", metadata_filter=MetadataFilter(season=2024)),
    ]
    bundle = retrieve_evidence(
        retriever,
        "Compare Bahrain 2021 and 2024",
        RouteType.STRUCTURED_ONLY,
        ["b21", "b24"],
        scopes=scopes,
        limit=3,
    )
    assert bundle.evidence_coverage == 1
    assert bundle.complete
    assert len({row.chunk.source_key for row in bundle.retrieved}) == len(bundle.retrieved)


def test_route_classification_structured_rag_and_mixed():
    assert classify_hardening_route("Who won Bahrain 2024?") == RouteType.STRUCTURED_ONLY
    assert classify_hardening_route("Why is Monaco difficult?") == RouteType.RAG_ONLY
    assert (
        classify_hardening_route("Who won Monaco 2024 and why was it notable?") == RouteType.MIXED
    )


def test_empty_and_ambiguous_query_handling():
    with pytest.raises(ValueError, match="empty"):
        decompose_explicit_query("", [])
    with pytest.raises(ValueError, match="ambiguous"):
        decompose_explicit_query("Compare 2021 and 2025", [])


def test_corpus_coverage_failure_is_not_mislabeled_retrieval_failure():
    retriever = BM25Retriever(chunk_documents([doc("known", "Known evidence")]))
    bundle = retrieve_evidence(
        retriever,
        "Unknown private incident cause",
        RouteType.RAG_ONLY,
        [],
        evidence_available=False,
    )
    assert bundle.failure_category == FailureCategory.CORPUS_COVERAGE_FAILURE


def test_source_conflict_retains_authority_and_does_not_choose():
    conflict = represent_conflict(
        [
            {"source_key": "fia", "authority_tier": "OFFICIAL", "value": "A"},
            {"source_key": "other", "authority_tier": "REPUTABLE_SECONDARY", "value": "B"},
        ],
        "value",
    )
    assert conflict is not None
    assert conflict.values == ("A", "B")
    assert conflict.source_keys == ("fia", "other")
    assert "no value was selected" in conflict.reason


def test_phase13b_benchmark_and_metrics_cover_required_cases():
    benchmark = json.loads(
        (ROOT / "docs/phase13b-retrieval-benchmark.json").read_text(encoding="utf-8")
    )
    results = json.loads(
        (ROOT / "docs/phase13b-retrieval-results.json").read_text(encoding="utf-8")
    )
    assert len(benchmark) == 125
    assert len({row["question_id"] for row in benchmark}) == 125
    assert sum(row["evidence_requirement"] == "ALL_REQUIRED" for row in benchmark) == 20
    assert {row["route_type"] for row in benchmark} == {"STRUCTURED_ONLY", "RAG_ONLY", "MIXED"}
    assert results["evaluations"]["decomposed"]["evidence_coverage_at_3"] == 1
    assert results["evaluations"]["decomposed"]["complete_evidence_at_5"] == 1
    assert results["route_accuracy"] == 1
