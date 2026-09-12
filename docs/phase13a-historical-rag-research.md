# Phase 13A — Historical F1 retrieval research

## Decision

**CONDITIONAL GO for a Phase 13B retrieval-hardening phase. NO-GO for a production chatbot or
answer generator.**

The bounded benchmark supports a provenance-first historical retrieval layer. Metadata-filtered
BM25 was the simplest best method: Recall@1 93.75%, Recall@3 95.00%, Recall@5 95.00%, MRR 0.950.
The local dense and hybrid methods tied the filtered top-k result but did not improve it and were
roughly an order of magnitude slower. They should not become the default.

The evidence is not yet broad enough for generation. The corpus covers only four circuits over five
seasons plus seven official explanatory documents. Two multi-event comparison questions missed all
expected event documents in the top five. Phase 13B, if approved, should broaden the human-verified
benchmark and structured metadata coverage before adding any answer generation.

## Allowed knowledge domains

| Domain | Intended use | Boundary |
| --- | --- | --- |
| Event history | Historical narrative, incidents and context | Exact classifications and numerical results route to structured data |
| Driver/team history | Team membership and prior-event context | Exact positions, points and results route to structured data |
| Regulations/terminology | Rule and technical explanations with version/date provenance | Never used to manufacture live race state |
| Circuit/history | Characteristics, historical context and prior winners | Exact winner lists should prefer structured records |

Current gaps, lap times, positions, tyres, pit stops, pace, strategy deltas and simulations remain
the responsibility of the deterministic stack. Phase 12 radio and every ASR artifact are excluded.

## Source inventory and data discipline

| Source class | Authority / coverage | Phase 13A treatment | Storage and access concern |
| --- | --- | --- | --- |
| Jolpica F1 structured results | Structured Ergast-compatible race classifications; broad historical depth | Ingested for 2021–2025 at Bahrain, Monaco, Monza and Yas Marina | [Jolpica terms](https://github.com/jolpica/jolpica-f1/blob/main/TERMS.md) apply CC BY-NC-SA 4.0 to data, restrict commercial use without permission, and describe a volunteer service with rate limits/no correctness guarantee |
| FIA regulations and notices | Primary regulatory source with dated rules | Three concise research summaries with source URL and section context | Official PDFs/articles are linked, not mirrored; redistribution rights were not assumed |
| Formula1.com glossary/circuit editorial | Official explanatory and event material | Four concise factual paraphrases with URLs | Copyrighted editorial text is not mirrored; only minimal research summaries and metadata are stored |
| Existing FastF1 archives | Authoritative project input for deterministic timing/replay | Not ingested as RAG prose | RaceState and analysis services remain authoritative |
| Reputable motorsport journalism | Potential narrative coverage | Inventoried but not ingested | Licensing, stable access and provenance require source-by-source review |
| Team radio / Phase 12 ASR | Untrusted machine transcripts | Explicitly excluded | Phase 12C concluded NO-GO for ungated automation |

The fetch is bounded to 20 targeted Jolpica circuit-season requests. Cached provider payloads stay
under ignored `.cache/phase13a/`; no crawler or article mirror is created.

## Corpus and document model

The frozen research corpus is `docs/phase13a-knowledge-corpus.json`:

- 45 documents total.
- 38 structured documents: 20 event classifications, 14 selected driver-season summaries and four
  circuit-history summaries.
- Seven official documents: FIA 2021 Safety Car/VSC rules, FIA 2022 technical/ground-effect rules,
  FIA's 2022 porpoising response, the F1 glossary, and official Monaco, Monza and Yas Marina context.
- Five seasons (2021–2025), four circuits and a deliberately bounded set of drivers/teams.

Every normalized document carries a deterministic ID, source key/name/URL/type, authority tier,
title, concise text, available dates, season/event/entity/topic tags, provenance and retrieval time.
Fields may be absent when the source does not support them. Pydantic forbids undeclared fields.

## Chunking comparison

Two deterministic strategies were evaluated:

- **Section/paragraph aware:** preserves `##` headings and groups paragraphs to at most 180 words.
  Structured result records remain whole. Result: 53 chunks.
- **Fixed-window baseline:** 120 words with 20-word overlap. Structured result records remain whole.
  Result: 46 chunks.

Section parsing initially exposed a defect that discarded a heading's attached paragraph; a focused
test now prevents recurrence. After the fix, both strategies reached the same filtered retrieval
metrics. Section-aware chunking remains preferable because it preserves explanatory headings without
hurting quality.

## Frozen retrieval benchmark

`docs/phase13a-retrieval-benchmark.json` contains 40 deterministic, human-inspectable questions:

| Category | Questions |
| --- | ---: |
| Event history | 20 |
| Driver/team | 6 |
| Circuit/history | 6 |
| Regulations/terminology | 5 |
| Multi-document context | 3 |

Expected source keys and key facts come from frozen structured records or manually reviewed official
topic documents. No LLM generated answers or judged relevance. Twenty exact result/classification
questions are labelled `STRUCTURED_FACT`; the remaining 20 are `KNOWLEDGE_RETRIEVAL`. The conservative
boundary classifier matched all 40 labels. Filters exercise season, event, driver, circuit and topic
identity, including same-event/different-season ambiguity and drivers changing teams.

Recall@k is the mean fraction of labelled source documents present in the first k results; `any
relevant evidence` is the question-level hit rate. MRR uses the first relevant result.

## Retrieval results

### Section-aware chunks

| Method | Filters | R@1 | R@3 | R@5 | MRR | Any evidence | Median query |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| BM25 | No | 71.25% | 85.00% | 95.00% | 0.8075 | 95.00% | 0.18 ms |
| BM25 | Yes | **93.75%** | **95.00%** | **95.00%** | **0.9500** | **95.00%** | **0.06 ms** |
| Dense LSA | No | 83.75% | 95.00% | 95.00% | 0.8833 | 95.00% | 0.66 ms |
| Dense LSA | Yes | 93.75% | 95.00% | 95.00% | 0.9500 | 95.00% | 0.65 ms |
| Hybrid RRF | No | 76.25% | 85.00% | 95.00% | 0.8375 | 95.00% | 0.90 ms |
| Hybrid RRF | Yes | 93.75% | 95.00% | 95.00% | 0.9500 | 95.00% | 0.75 ms |

### Fixed-window baseline

| Method | Filters | R@1 | R@3 | R@5 | MRR |
| --- | --- | ---: | ---: | ---: | ---: |
| BM25 | No | 76.25% | 85.00% | 95.00% | 0.8325 |
| BM25 | Yes | 93.75% | 95.00% | 95.00% | 0.9500 |
| Dense LSA | No | 83.75% | 95.00% | 95.00% | 0.8917 |
| Dense LSA | Yes | 93.75% | 95.00% | 95.00% | 0.9500 |
| Hybrid RRF | No | 76.25% | 90.00% | 95.00% | 0.8417 |
| Hybrid RRF | Yes | 93.75% | 95.00% | 95.00% | 0.9500 |

The dense method is local TF-IDF followed by deterministic truncated SVD, capped by corpus rank:
52 dimensions for the section corpus and 45 for fixed windows. This is a modest latent-semantic
baseline, not a claim that a neural embedding model was evaluated. The section index built in about
43 ms, occupies about 1.13 MB as a local joblib research cache, and required no external API.

Metadata filtering improved section BM25 Recall@1 by 22.5 percentage points, from 71.25% to 93.75%.
It controlled most same-event/different-year ambiguity. Dense retrieval improved unfiltered Recall@1
but added no filtered recall. Hybrid fusion did not beat filtered BM25. **Filtered BM25 is selected.**

## Failure analysis

The two complete top-five misses were deliberately multi-event questions: comparing full Bahrain
podiums in 2021/2024 and Monaco non-finish context in 2021/2023. Generic circuit-history summaries
and other entity documents outranked the two required event documents. This is both an entity/event
confusion and a query-planning limitation: a single unconstrained top-k search is weak for a request
that names two distinct temporal scopes.

Observed failure categories and cautions:

- Same-circuit/different-year evidence competes strongly without metadata constraints.
- Multi-period questions need explicit decomposition or multi-value metadata retrieval; Phase 13A
  does not build an autonomous router/planner.
- Driver-season summaries are derived from only four sampled circuits and must not be presented as a
  complete season history.
- Missing corpus coverage must be distinguished from retrieval failure. The benchmark intentionally
  cannot establish quality for unrepresented circuits, older seasons, broad technical history or
  secondary-source narratives.
- Team renames and similar surnames are represented in the metadata model but not covered deeply
  enough here to claim resolution across F1 history.

## Structured data versus RAG

Exact questions such as winners, finishing positions, grids, podiums, classifications, points and
counts should query normalized structured data. Text retrieval is appropriate for explanations,
historical narrative, regulation context, circuit characteristics and multi-source qualitative
context. This boundary is explicit and test-covered; it is not a production autonomous router.

## Performance and reproducibility

- First provider-backed corpus build: approximately 13.15 seconds for 20 respectful targeted
  requests; observed cached rebuilds were approximately 0.06–0.30 seconds.
- Section index build: approximately 0.04 seconds.
- Best-method median/P90 query latency: approximately 0.06/0.10 ms on this workstation.
- Local research index: 1,131,127 bytes (about 1.08 MiB), ignored under `.cache/phase13a/`.
- Rebuild: `.venv\Scripts\python -m scripts.research_phase13a`; add `--refresh` only when an explicit
  provider refresh is wanted.
- Machine-readable experiment output: `docs/phase13a-retrieval-results.json`.

Timings are workstation measurements on a tiny corpus and are not production capacity estimates.

## Recommendation

Phase 13B is justified only as retrieval hardening: expand authoritative coverage, add manually
verified ambiguity cases, support explicit multi-season/event query decomposition, and re-run a
sealed benchmark. Keep filtered BM25 as the reference method. Do not add generation, a chatbot,
agents, MCP, PostgreSQL/pgvector, Redis or radio content until a broader holdout preserves evidence
quality and provenance.
