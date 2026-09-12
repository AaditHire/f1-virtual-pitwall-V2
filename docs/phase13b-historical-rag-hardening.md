# Phase 13B — Historical F1 retrieval hardening

## Decision

**GO for a narrowly scoped Phase 13C grounded-answer experiment.** This is not approval for a
production chatbot, autonomous routing, agents, MCP, radio ingestion or production vector
infrastructure.

Metadata-filtered BM25 generalized usefully from 45 to 309 documents. Its supported-query Recall@5
was 87.50%. Adding deterministic multi-scope decomposition raised Recall@5 to 100%, multi-document
Evidence Coverage@5 from 25% to 100%, and Complete Evidence Success@5 from 15% to 100%. All five
remaining benchmark failures are intentional corpus-coverage diagnostics for private retirement
causes that the sources do not establish.

## Frozen Phase 13A baseline

The Phase 13A artifacts were verified before every Phase 13B build and were not rewritten:

| Artifact | SHA-256 |
| --- | --- |
| Phase 13A corpus | `4b8d4215686574094f19ef5c6b97a313de8ae62cf30525a96909a8ec59804251` |
| Phase 13A benchmark | `c29baf78c380ba2e749d8712dbbaeb8895c1167bda012c1d48a0824fbe4d1363` |
| Phase 13A results | `9b331773513e68399f0fef82985cb95d09793f2b8566e066fa231272ec7bca27` |

## Expanded source inventory and coverage

The bounded corpus covers every Jolpica-listed Grand Prix in 2021–2025:

| Season | Events |
| --- | ---: |
| 2021 | 22 |
| 2022 | 22 |
| 2023 | 22 |
| 2024 | 24 |
| 2025 | 24 |

Total coverage is 114 events, 35 canonical driver IDs, 12 season-specific constructor IDs and 28
circuits. The corpus contains 309 documents and 317 section-aware chunks:

- 114 complete event-classification records.
- 105 driver-season summaries.
- 55 constructor-season summaries.
- 28 circuit/event-history summaries.
- Seven concise official FIA/Formula1.com regulation, terminology and circuit-context documents
  preserved from the Phase 13A source set.

Qualifying and championship-standing documents were not added because the benchmark did not need
duplicate representations of exact classifications. Those remain structured-source candidates for
a future bounded expansion, not prose to be invented by RAG.

No secondary journalism, full copyrighted article, radio record or machine transcript was ingested.

## Licensing and provenance

Every document retains deterministic ID, source key/name/URL/type, authority tier, dates where
available, season/event, canonical and display entity tags, topic tags, concise text, provenance,
retrieval time, `license_classification`, and `license_notes`.

- Jolpica-derived documents are marked `PRODUCTION_REVIEW_REQUIRED`. Its data terms identify CC
  BY-NC-SA 4.0 and non-commercial restrictions; this report makes no legal conclusion.
- FIA and Formula1.com summaries are also `PRODUCTION_REVIEW_REQUIRED`. Only concise factual
  paraphrases and source links are stored; redistribution rights are not assumed.
- The current source set is acceptable for this bounded research workflow, but production use needs
  explicit source/licensing review.

## Entity and temporal normalization

Retrieval uses canonical Jolpica driver and constructor identifiers plus exact aliases for display
name, surname and driver code. Ambiguous aliases fail closed rather than selecting an entity.

Season-specific constructor identity remains distinct from team lineage. For example, `renault` and
`alpine`, `racing_point` and `aston_martin`, and `toro_rosso` / `alphatauri` / `rb` /
`racing_bulls` may have documented lineage relationships, but they are never silently merged into
one historical constructor ID. The 2021–2025 corpus itself includes `alpine`, `aston_martin`,
`alphatauri` and `rb`; older lineage names are explicit catalog knowledge only where needed.

Repeated Grand Prix names remain separated by season and event metadata. Circuits may have multiple
event names, while circuit IDs stay canonical. No fuzzy correction changes query meaning.

## New frozen benchmark

`docs/phase13b-retrieval-benchmark.json` contains 125 deterministic questions with SHA-256:

`f12ad25afa06439a4630f1b9c2fc779723c3498f3738b365db914d8b7ffb72ad`

| Category | Count |
| --- | ---: |
| Single-event history | 50 |
| Driver history | 20 |
| Team history | 15 |
| Circuit history | 10 |
| Regulations / terminology | 5 |
| Multi-event | 10 |
| Team-transfer ambiguity | 5 |
| Mixed structured/context | 5 |
| Explicit corpus-coverage diagnostics | 5 |

Twenty questions require all evidence from two documents. Each benchmark record specifies its route
(`STRUCTURED_ONLY`, `RAG_ONLY`, or `MIXED`), evidence requirement, relevant source keys, independent
metadata filters, explicit decomposition scopes, and whether the evidence actually exists. Labels
are deterministic and human-inspectable; no LLM generated or judged them.

## Query decomposition and evidence bundles

Multi-year/event questions are decomposed only from explicit benchmark/entity-parser scopes. Each
scope retains its year, event and driver/team identity. A missing or invented year/event is rejected;
an unscoped query naming multiple years fails as ambiguous.

Each scope is retrieved independently with filtered BM25. Results are merged round-robin, then
deduplicated to one best/provenance-bearing chunk per source. Scores from different subqueries are not
treated as directly comparable. The deterministic evidence bundle records query, route, parsed
scopes, retrieved chunks, structured-fact slots, provenance, conflicts, required source keys,
coverage, completeness and failure category.

Conflicting source values retain both source keys, authority tiers and values. The system never
selects a winner automatically. No actual conflict appeared in the bounded corpus; the representation
and fail-safe behavior are unit-tested.

## Retrieval results

Metrics exclude the five known corpus-coverage questions from retrieval denominators. Recall@K and
Evidence Coverage@K are the fraction of required source documents retrieved, not merely a question
hit rate.

| BM25 approach | Recall@1 | Recall@3 | Recall@5 | MRR | Evidence Coverage@3 | Evidence Coverage@5 | Complete Evidence@3 | Complete Evidence@5 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Unfiltered | 7.50% | 28.75% | 32.50% | 0.2037 | 17.50% | 25.00% | 5.00% | 15.00% |
| Metadata-filtered | 84.17% | 86.25% | 87.50% | 0.8697 | 17.50% | 25.00% | 5.00% | 15.00% |
| Filtered + decomposition | **91.67%** | **100.00%** | **100.00%** | **1.0000** | **100.00%** | **100.00%** | **100.00%** | **100.00%** |

Recall@1 for decomposed questions is intentionally below 100% because one top-one result cannot
cover both required documents. Complete Evidence Success@10 is also 100%.

Filtered dense LSA remained diagnostic only: Recall@1 85.42%, Recall@3 86.25%, Recall@5 87.50%, MRR
0.8824. It did not solve multi-document composition and did not justify neural embeddings, reranking
or a vector database.

## Metadata ablation

Small one-filter removals were run only where the filter applied:

| Removed filter | Questions | Full top-1 | Ablated top-1 | Wrong-scope top-1 |
| --- | ---: | ---: | ---: | ---: |
| Season | 85 | 100% | 100% | 0% |
| Event | 50 | 100% | 4% | 96% |
| Driver | 20 | 100% | 100% | 0% |
| Circuit | 10 | 100% | 100% | 0% |

Event filtering is essential for repeated-event disambiguation. In this templated benchmark, explicit
years, names, driver-history topics and circuit names were already strong lexical features, so the
individual season/driver/circuit filters did not change top-one accuracy. They remain guardrails and
are independently tested for wrong-year/event exclusion; this result must not be generalized to
free-form user queries.

## Routing, ambiguity and failure diagnosis

The deterministic structured/RAG/MIXED classifier matched all 125 labels:

- Exact winners, results, positions, grids, points, standings and season-specific team/result
  comparisons are `STRUCTURED_ONLY`.
- Explanations, terminology and circuit characteristics are `RAG_ONLY`.
- Questions combining an exact result with official context are `MIXED` and return both evidence
  components without generating an answer.

At top five, the selected decomposed approach produced:

| Failure category | Count |
| --- | ---: |
| `CORPUS_COVERAGE_FAILURE` | 5 |
| `RETRIEVAL_FAILURE` | 0 |
| `QUERY_AMBIGUITY` | 0 |
| `ROUTING_FAILURE` | 0 |
| `MULTI_DOCUMENT_FAILURE` | 0 |
| `SOURCE_CONFLICT` | 0 |

The five coverage failures ask for private mechanical causes behind every retirement. Jolpica status
fields do not prove those narratives, and no official source in the corpus supplies them. They are
reported as insufficient coverage rather than retrieval errors.

## Performance

- First uncached provider pass: approximately 28 seconds for bounded, paginated full-season data.
- Cached corpus rebuild: approximately 0.10–0.31 seconds.
- BM25 plus diagnostic dense-index build: approximately 0.20 seconds.
- Decomposed median/P90 query latency: approximately 0.41/1.24 ms.
- Corpus artifact: 946,773 bytes.
- Benchmark artifact: 90,655 bytes.
- Results artifact: approximately 337 KB.
- Ignored local BM25 index: 1,522,707 bytes.

The cache and joblib index remain research-only under `.cache/phase13b/`.

## Recommendation for Phase 13C

Phase 13C may proceed only as a small, citation-enforced grounded-answer experiment over this defined
2021–2025 scope. It should consume deterministic evidence bundles, route exact facts to structured
data, refuse incomplete evidence, expose conflicts and citations, and retain filtered BM25 plus
explicit decomposition as the retrieval reference.

This result does **not** approve a product chatbot, autonomous browsing, radio transcripts, agents,
MCP, Redis, PostgreSQL/pgvector, a production vector store or frontend integration.
