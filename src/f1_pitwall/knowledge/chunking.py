"""Small deterministic chunking strategies for historical knowledge research."""

from __future__ import annotations

import re

from f1_pitwall.knowledge.models import KnowledgeChunk, KnowledgeDocument, stable_id


def _chunk(document: KnowledgeDocument, text: str, ordinal: int, heading: str | None):
    return KnowledgeChunk(
        chunk_id=stable_id(
            "chunk",
            {"document_id": document.document_id, "ordinal": ordinal, "text": text},
        ),
        document_id=document.document_id,
        source_key=document.source_key,
        source_name=document.source_name,
        source_url=document.source_url,
        source_type=document.source_type,
        authority_tier=document.authority_tier,
        title=document.title,
        heading=heading,
        text=text,
        season=document.season,
        event=document.event,
        driver_tags=document.driver_tags,
        constructor_tags=document.constructor_tags,
        circuit_tags=document.circuit_tags,
        topic_tags=document.topic_tags,
        provenance=document.provenance,
        ordinal=ordinal,
    )


def section_chunks(document: KnowledgeDocument, max_words: int = 180) -> list[KnowledgeChunk]:
    # Split heading markers from their following body. A heading and first paragraph
    # commonly share one blank-line block, so paragraph-only splitting can discard it.
    sections = re.split(r"(?m)^##\s+(.+?)\s*$", document.text)
    candidates: list[tuple[str | None, str]] = []
    preamble = sections[0].strip()
    if preamble:
        candidates.append((None, preamble))
    for index in range(1, len(sections), 2):
        heading = sections[index].strip()
        body = sections[index + 1].strip() if index + 1 < len(sections) else ""
        if body:
            candidates.append((heading, body))
    if not candidates:
        return []
    groups: list[tuple[str | None, list[str]]] = []
    for heading, body in candidates:
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", body) if part.strip()]
        current: list[str] = []
        words = 0
        for paragraph in paragraphs:
            count = len(paragraph.split())
            if current and words + count > max_words:
                groups.append((heading, current))
                current, words = [], 0
            current.append(paragraph)
            words += count
        if current:
            groups.append((heading, current))
    return [
        _chunk(document, "\n\n".join(parts), ordinal, section)
        for ordinal, (section, parts) in enumerate(groups)
    ]


def fixed_window_chunks(
    document: KnowledgeDocument, window_words: int = 120, overlap_words: int = 20
) -> list[KnowledgeChunk]:
    if window_words <= overlap_words or overlap_words < 0:
        raise ValueError("fixed window must be larger than overlap")
    words = document.text.split()
    chunks = []
    start = 0
    ordinal = 0
    while start < len(words):
        text = " ".join(words[start : start + window_words])
        chunks.append(_chunk(document, text, ordinal, None))
        if start + window_words >= len(words):
            break
        start += window_words - overlap_words
        ordinal += 1
    return chunks


def chunk_documents(
    documents: list[KnowledgeDocument], strategy: str = "section"
) -> list[KnowledgeChunk]:
    if strategy not in {"section", "fixed"}:
        raise ValueError(f"unknown chunking strategy: {strategy}")
    chunks = []
    for document in documents:
        if document.source_type == "STRUCTURED_RESULT":
            selected = [_chunk(document, document.text, 0, "Structured event record")]
        elif strategy == "section":
            selected = section_chunks(document)
        else:
            selected = fixed_window_chunks(document)
        chunks.extend(selected)
    return chunks
