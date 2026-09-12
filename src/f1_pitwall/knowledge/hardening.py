"""Deterministic Phase 13B routing, decomposition, and evidence bundles."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from f1_pitwall.knowledge.models import EvidenceScope, MetadataFilter
from f1_pitwall.knowledge.retrieval import SearchResult


class RouteType(StrEnum):
    STRUCTURED_ONLY = "STRUCTURED_ONLY"
    RAG_ONLY = "RAG_ONLY"
    MIXED = "MIXED"


class FailureCategory(StrEnum):
    RETRIEVAL_FAILURE = "RETRIEVAL_FAILURE"
    CORPUS_COVERAGE_FAILURE = "CORPUS_COVERAGE_FAILURE"
    QUERY_AMBIGUITY = "QUERY_AMBIGUITY"
    ROUTING_FAILURE = "ROUTING_FAILURE"
    MULTI_DOCUMENT_FAILURE = "MULTI_DOCUMENT_FAILURE"
    SOURCE_CONFLICT = "SOURCE_CONFLICT"


@dataclass(frozen=True)
class SourceConflict:
    source_keys: tuple[str, ...]
    field: str
    values: tuple[str, ...]
    authority_tiers: tuple[str, ...]
    reason: str


@dataclass
class EvidenceBundle:
    query: str
    route_type: RouteType
    parsed_scopes: list[EvidenceScope]
    retrieved: list[SearchResult]
    structured_facts: list[dict] = field(default_factory=list)
    conflicts: list[SourceConflict] = field(default_factory=list)
    required_source_keys: list[str] = field(default_factory=list)
    evidence_coverage: float = 0.0
    complete: bool = False
    failure_category: FailureCategory | None = None


STRUCTURED = re.compile(
    r"\b(who won|where did .+ finish|what (?:position|place|grid)|podium|classification|"
    r"results?|points|standings)\b",
    re.IGNORECASE,
)
CONTEXT = re.compile(
    r"\b(why|explain|context|notable|characteristics|restrict|mean|changed|difficult)\b",
    re.IGNORECASE,
)


def classify_hardening_route(query: str) -> RouteType:
    normalized = " ".join(query.split())
    has_structured = bool(STRUCTURED.search(normalized))
    has_context = bool(CONTEXT.search(normalized))
    if has_structured and has_context:
        return RouteType.MIXED
    if has_structured:
        return RouteType.STRUCTURED_ONLY
    return RouteType.RAG_ONLY


def decompose_explicit_query(
    query: str,
    scopes: list[EvidenceScope],
) -> list[EvidenceScope]:
    """Validate benchmark/entity-parser scopes without guessing omitted values."""
    if not query.strip():
        raise ValueError("query cannot be empty")
    if not scopes:
        years = sorted(set(re.findall(r"\b(?:19|20)\d{2}\b", query)))
        if len(years) > 1:
            raise ValueError("multi-period query is ambiguous without explicit scopes")
        return [EvidenceScope(query=query, metadata_filter=MetadataFilter())]
    for scope in scopes:
        metadata = scope.metadata_filter
        if metadata.season is not None and str(metadata.season) not in query:
            raise ValueError("decomposition scope year is not present in the query")
        scope_context = f"{query} {scope.query}".casefold()
        if metadata.event and metadata.event.casefold() not in scope_context:
            # Event names may be shared once in a comparison query; never invent a different one.
            base = metadata.event.casefold().replace(" grand prix", "")
            if base not in scope_context:
                raise ValueError("decomposition scope event is not present in the query")
    return scopes


def deduplicate_results(
    rows: list[SearchResult], limit: int, *, preserve_order: bool = False
) -> list[SearchResult]:
    """Keep the best chunk per source so overlapping chunks cannot fill top-k."""
    best = {}
    for row in rows:
        current = best.get(row.chunk.source_key)
        if current is None or row.score > current.score:
            best[row.chunk.source_key] = row
    if preserve_order:
        seen = set()
        ordered = []
        for row in rows:
            if row.chunk.source_key not in seen:
                seen.add(row.chunk.source_key)
                ordered.append(row)
        ordered = ordered[:limit]
    else:
        ordered = sorted(best.values(), key=lambda row: (-row.score, row.chunk.source_key))[:limit]
    return [
        SearchResult(chunk=row.chunk, score=row.score, rank=index)
        for index, row in enumerate(ordered, 1)
    ]


def retrieve_evidence(
    retriever,
    query: str,
    route_type: RouteType,
    required_source_keys: list[str],
    *,
    metadata_filter: MetadataFilter | None = None,
    scopes: list[EvidenceScope] | None = None,
    limit: int = 5,
    evidence_available: bool = True,
) -> EvidenceBundle:
    parsed = (
        decompose_explicit_query(query, scopes)
        if scopes is not None
        else [EvidenceScope(query=query, metadata_filter=metadata_filter or MetadataFilter())]
    )
    if scopes is not None:
        per_scope = [
            retriever.search(scope.query, limit=limit, metadata=scope.metadata_filter)
            for scope in parsed
        ]
        # Round-robin preserves at least one slot for each explicit time/entity scope.
        merged = []
        for rank in range(limit):
            for rows in per_scope:
                if rank < len(rows):
                    merged.append(rows[rank])
    else:
        merged = retriever.search(query, limit=limit, metadata=metadata_filter)
    retrieved = deduplicate_results(merged, limit, preserve_order=scopes is not None)
    required = set(required_source_keys)
    found = required.intersection(row.chunk.source_key for row in retrieved)
    coverage = len(found) / len(required) if required else 0.0
    complete = bool(required) and len(found) == len(required)
    failure = None
    if not evidence_available:
        failure = FailureCategory.CORPUS_COVERAGE_FAILURE
    elif required and not found:
        failure = FailureCategory.RETRIEVAL_FAILURE
    elif required and not complete:
        failure = FailureCategory.MULTI_DOCUMENT_FAILURE
    return EvidenceBundle(
        query=query,
        route_type=route_type,
        parsed_scopes=parsed,
        retrieved=retrieved,
        required_source_keys=required_source_keys,
        evidence_coverage=coverage,
        complete=complete,
        failure_category=failure,
    )


def represent_conflict(rows: list[dict], field: str) -> SourceConflict | None:
    values = {str(row[field]) for row in rows if row.get(field) is not None}
    if len(values) < 2:
        return None
    return SourceConflict(
        source_keys=tuple(row["source_key"] for row in rows),
        field=field,
        values=tuple(sorted(values)),
        authority_tiers=tuple(row["authority_tier"] for row in rows),
        reason="Sources disagree; no value was selected automatically.",
    )
