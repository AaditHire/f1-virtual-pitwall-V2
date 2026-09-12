"""Lexical, dense LSA, and simple hybrid retrieval with metadata filtering."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from time import perf_counter

import numpy as np
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize

from f1_pitwall.knowledge.models import KnowledgeChunk, MetadataFilter

TOKEN_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?", re.IGNORECASE)


@dataclass(frozen=True)
class SearchResult:
    chunk: KnowledgeChunk
    score: float
    rank: int


def tokens(value: str) -> list[str]:
    return TOKEN_RE.findall(value.casefold())


def searchable_text(chunk: KnowledgeChunk) -> str:
    metadata = " ".join(
        [
            str(chunk.season or ""),
            chunk.event or "",
            *chunk.driver_tags,
            *chunk.constructor_tags,
            *chunk.circuit_tags,
            *chunk.topic_tags,
        ]
    )
    return f"{chunk.title} {chunk.heading or ''} {metadata} {chunk.text}"


def _contains(values: list[str], expected: str | None) -> bool:
    if expected is None:
        return True
    needle = expected.casefold()
    return any(needle == value.casefold() for value in values)


def matches_filter(chunk: KnowledgeChunk, metadata: MetadataFilter | None) -> bool:
    if metadata is None:
        return True
    return all(
        [
            metadata.season is None or chunk.season == metadata.season,
            metadata.event is None or (chunk.event or "").casefold() == metadata.event.casefold(),
            _contains(chunk.driver_tags, metadata.driver),
            _contains(chunk.constructor_tags, metadata.constructor),
            _contains(chunk.circuit_tags, metadata.circuit),
            _contains(chunk.topic_tags, metadata.topic),
        ]
    )


class BM25Retriever:
    name = "bm25"

    def __init__(self, chunks: list[KnowledgeChunk], k1: float = 1.5, b: float = 0.75):
        self.chunks = chunks
        self.k1 = k1
        self.b = b
        self.documents = [tokens(searchable_text(chunk)) for chunk in chunks]
        self.lengths = [len(row) for row in self.documents]
        self.average_length = sum(self.lengths) / len(self.lengths) if self.lengths else 0
        self.frequencies = [Counter(row) for row in self.documents]
        document_frequency = Counter()
        for row in self.documents:
            document_frequency.update(set(row))
        count = len(chunks)
        self.idf = {
            term: math.log(1 + (count - frequency + 0.5) / (frequency + 0.5))
            for term, frequency in document_frequency.items()
        }

    def search(
        self, query: str, limit: int = 5, metadata: MetadataFilter | None = None
    ) -> list[SearchResult]:
        if not self.chunks:
            return []
        query_terms = tokens(query)
        scored = []
        for index, chunk in enumerate(self.chunks):
            if not matches_filter(chunk, metadata):
                continue
            score = 0.0
            length = self.lengths[index]
            for term in query_terms:
                frequency = self.frequencies[index].get(term, 0)
                if not frequency:
                    continue
                denominator = frequency + self.k1 * (
                    1 - self.b + self.b * length / max(self.average_length, 1)
                )
                score += self.idf.get(term, 0) * frequency * (self.k1 + 1) / denominator
            if score > 0:
                scored.append((score, chunk.chunk_id, chunk))
        scored.sort(key=lambda row: (-row[0], row[1]))
        return [
            SearchResult(chunk=chunk, score=score, rank=rank)
            for rank, (score, _, chunk) in enumerate(scored[:limit], 1)
        ]


class DenseLSARetriever:
    """Local dense latent-semantic embeddings using TF-IDF plus truncated SVD."""

    name = "dense_lsa"

    def __init__(self, chunks: list[KnowledgeChunk], dimensions: int = 64):
        self.chunks = chunks
        self.vectorizer = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, min_df=1)
        if not chunks:
            self.dimensions = 0
            self.svd = None
            self.embeddings = np.empty((0, 0))
            self.build_seconds = 0.0
            return
        started = perf_counter()
        matrix = self.vectorizer.fit_transform(searchable_text(chunk) for chunk in chunks)
        maximum = min(matrix.shape[0] - 1, matrix.shape[1] - 1)
        self.dimensions = max(1, min(dimensions, maximum))
        self.svd = TruncatedSVD(n_components=self.dimensions, random_state=13)
        self.embeddings = normalize(self.svd.fit_transform(matrix))
        self.build_seconds = perf_counter() - started

    @property
    def storage_bytes(self) -> int:
        return int(self.embeddings.nbytes)

    def search(
        self, query: str, limit: int = 5, metadata: MetadataFilter | None = None
    ) -> list[SearchResult]:
        if not self.chunks or self.svd is None:
            return []
        query_vector = normalize(self.svd.transform(self.vectorizer.transform([query])))[0]
        scores = self.embeddings @ query_vector
        selected = [
            (float(scores[index]), chunk.chunk_id, chunk)
            for index, chunk in enumerate(self.chunks)
            if matches_filter(chunk, metadata) and scores[index] > 0
        ]
        selected.sort(key=lambda row: (-row[0], row[1]))
        return [
            SearchResult(chunk=chunk, score=score, rank=rank)
            for rank, (score, _, chunk) in enumerate(selected[:limit], 1)
        ]


class HybridRetriever:
    """Reciprocal-rank fusion of the lexical and dense result lists."""

    name = "hybrid_rrf"

    def __init__(self, lexical: BM25Retriever, dense: DenseLSARetriever, rrf_k: int = 60):
        if lexical.chunks != dense.chunks:
            raise ValueError("hybrid retrievers must use the same ordered chunks")
        self.chunks = lexical.chunks
        self.lexical = lexical
        self.dense = dense
        self.rrf_k = rrf_k

    def search(
        self, query: str, limit: int = 5, metadata: MetadataFilter | None = None
    ) -> list[SearchResult]:
        candidate_limit = max(limit * 4, 20)
        lists = [
            self.lexical.search(query, candidate_limit, metadata),
            self.dense.search(query, candidate_limit, metadata),
        ]
        scores: dict[str, float] = Counter()
        chunks = {}
        for rows in lists:
            for row in rows:
                chunks[row.chunk.chunk_id] = row.chunk
                scores[row.chunk.chunk_id] += 1 / (self.rrf_k + row.rank)
        ordered = sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))[:limit]
        return [
            SearchResult(chunk=chunks[chunk_id], score=scores[chunk_id], rank=rank)
            for rank, chunk_id in enumerate(ordered, 1)
        ]
