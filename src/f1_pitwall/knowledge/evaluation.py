"""Retrieval-only benchmark metrics and failure records."""

from __future__ import annotations

import statistics
from time import perf_counter

from f1_pitwall.knowledge.models import BenchmarkQuestion


def evaluate_retriever(retriever, questions: list[BenchmarkQuestion], use_filters: bool) -> dict:
    rows = []
    latencies = []
    for question in questions:
        started = perf_counter()
        results = retriever.search(
            question.query,
            limit=5,
            metadata=question.metadata_filter if use_filters else None,
        )
        latencies.append((perf_counter() - started) * 1000)
        relevant = set(question.relevant_source_keys)
        ranks = [row.rank for row in results if row.chunk.source_key in relevant]
        first_rank = min(ranks) if ranks else None
        retrieved_keys = [row.chunk.source_key for row in results]
        recall_by_k = {
            k: len(relevant.intersection(retrieved_keys[:k])) / len(relevant) for k in (1, 3, 5)
        }
        rows.append(
            {
                "question_id": question.question_id,
                "query": question.query,
                "category": question.category,
                "expected_route": question.expected_route,
                "relevant_source_keys": question.relevant_source_keys,
                "retrieved_source_keys": retrieved_keys,
                "first_relevant_rank": first_rank,
                "recall_at_1": recall_by_k[1],
                "recall_at_3": recall_by_k[3],
                "recall_at_5": recall_by_k[5],
            }
        )
    count = len(rows)
    return {
        "questions": count,
        "recall_at_1": sum(row["recall_at_1"] for row in rows) / count if count else 0,
        "recall_at_3": sum(row["recall_at_3"] for row in rows) / count if count else 0,
        "recall_at_5": sum(row["recall_at_5"] for row in rows) / count if count else 0,
        "mrr": sum(1 / row["first_relevant_rank"] for row in rows if row["first_relevant_rank"])
        / count
        if count
        else 0,
        "any_relevant_evidence": sum(row["first_relevant_rank"] is not None for row in rows) / count
        if count
        else 0,
        "median_query_ms": statistics.median(latencies) if latencies else 0,
        "p90_query_ms": sorted(latencies)[max(0, int(len(latencies) * 0.9) - 1)]
        if latencies
        else 0,
        "rows": rows,
    }
