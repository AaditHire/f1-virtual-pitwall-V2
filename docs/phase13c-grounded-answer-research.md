# Phase 13C grounded historical answer generation

## Status

The frozen live benchmark is complete. The final Phase 13C decision remains pending the bounded
human review. Phase 13D has not started.

## Frozen experiment

- Provider: AgentRouter
- Protocol: Anthropic-compatible Messages
- SDK: official `anthropic` Python SDK
- Base URL: `https://agentrouter.org`
- Model: `claude-opus-4-8`
- Authentication: SDK `auth_token` / Bearer from `AGENTROUTER_API_KEY`
- Output contract: one forced, non-executing `grounded_answer` tool
- Output schema: non-empty `answer`, enumerated `status`, and source-ID `citations`
- Maximum output: 320 tokens
- Benchmark: 60 questions
- Benchmark SHA-256: `969ff9d522a4f047493c1c1fc2ba9eaeddeb80f10988bd271f87078c7112809e`

The system did not use model-side retrieval, tools with external effects, web access, filesystem
access or hidden reasoning. `STRUCTURED_ONLY` answers remained deterministic.

## Run composition and usage

| Measure | Result |
| --- | ---: |
| STRUCTURED_ONLY | 30 |
| RAG_ONLY | 25 |
| MIXED | 5 |
| AgentRouter requests | 30 |
| Retries | 0 |
| Rejected generated outputs | 6 |
| Input tokens | 73,124 |
| Output tokens | 7,629 |
| Total tokens | 80,753 |
| Cached tokens | 0 |
| Reasoning tokens reported | 0 |
| AgentRouter median latency | 4,123.58 ms |
| AgentRouter P90 latency | 5,550.57 ms |
| Total pipeline median | 1,372.11 ms |
| Total pipeline P90 | 5,066.91 ms |

AgentRouter returned no request-cost or balance field. Existing AgentRouter credit was consumed, but
its monetary value cannot be derived from the response. No new external cash purchase was made for
this run, and the dashboard was not scraped.

## Deterministic evaluation

| Metric | Numerator / denominator | Rate |
| --- | ---: | ---: |
| Structured fact correctness | 54 / 55 | 98.18% |
| Required fact coverage | 70 / 79 | 88.61% |
| Uncited substantive-sentence proxy | 25 / 99 | 25.25% |
| Citation validity | 79 / 79 | 100.00% |
| Citation completeness | 54 / 60 | 90.00% |
| Citation support | 55 / 60 | 91.67% |
| Refusal accuracy | 4 / 5 | 80.00% |
| False refusals | 0 / 55 | 0.00% |
| Multi-document synthesis | 22 / 22 | 100.00% |
| Conflict handling | 2 / 2 | 100.00% |
| Prompt-injection strict fixture | 2 / 3 | 66.67% |
| Route compliance | 60 / 60 | 100.00% |

The unsupported-claim rate is a deterministic sentence-level citation proxy, not a human judgment
that every flagged sentence is factually unsupported. Human review is required to interpret it.

The strict prompt-injection failure occurred because one otherwise-correct answer repeated the
forbidden injected claim while explicitly explaining that it had ignored it. The frozen deterministic
test still counts the literal forbidden phrase as failure; the result was not manually changed.

## Failures

| Category | Count |
| --- | ---: |
| MALFORMED_RESPONSE | 6 |
| MISSING_REQUIRED_FACT | 9 |
| MISSING_CITATION | 5 |
| STRUCTURED_FACT_FAILURE | 1 |
| FAILED_REFUSAL | 1 |
| PROMPT_INJECTION_FAILURE | 1 |
| UNSUPPORTED_CLAIM | 1 |

All six malformed responses reached exactly 320 output tokens, the frozen maximum. Their usage,
latency, request count and error category were preserved. They were not repaired, retried or replaced.
The pattern is consistent with output-contract truncation, but the rejected content was not treated as
a valid answer.

## Human review

The review workbook contains 25 deterministic rows. Selection prioritizes all malformed responses,
all prompt-injection and conflict fixtures, expected-refusal questions, all MIXED questions, and a
deterministic sample of STRUCTURED_ONLY passes. Verdict fields start blank.

Export:

```powershell
.venv\Scripts\python.exe -m scripts.export_phase13c_human_review_xlsx
```

Validate completed workbook:

```powershell
.venv\Scripts\python.exe -m scripts.import_phase13c_human_review_xlsx outputs\phase13c_human_review\phase13c_human_review.xlsx
```

Import after validation:

```powershell
.venv\Scripts\python.exe -m scripts.import_phase13c_human_review_xlsx outputs\phase13c_human_review\phase13c_human_review.xlsx --confirm
```

The importer rejects missing/invalid judgments and any change to the frozen question, structured
facts, retrieved evidence, generated answer, citations or deterministic summary. Replacing an existing
review artifact requires a separate explicit `--allow-overwrite` flag.

## Decision

No final Phase 13C GO / CONDITIONAL GO / NO-GO decision is made before the human review is returned
and imported.
