"""Conservative boundary classifier; not a production autonomous router."""

from __future__ import annotations

import re
from enum import StrEnum


class QueryRoute(StrEnum):
    STRUCTURED_FACT = "STRUCTURED_FACT"
    KNOWLEDGE_RETRIEVAL = "KNOWLEDGE_RETRIEVAL"


STRUCTURED_PATTERNS = [
    r"\bwho (?:won|finished|started)\b",
    r"\bwhat (?:position|place|grid position)\b",
    r"\b(?:winner|podium|classification|result)\b",
    r"\bhow many (?:points|wins|laps|pit stops)\b",
    r"\bfinished p\d+\b",
]


def classify_query_boundary(query: str) -> QueryRoute:
    normalized = " ".join(query.casefold().split())
    if any(re.search(pattern, normalized) for pattern in STRUCTURED_PATTERNS):
        return QueryRoute.STRUCTURED_FACT
    return QueryRoute.KNOWLEDGE_RETRIEVAL
