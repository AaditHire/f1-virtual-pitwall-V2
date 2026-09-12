# Phase 13C grounded historical answer generation

## Status

The frozen live benchmark and bounded human review are complete. Phase 13C concludes
**CONDITIONAL GO for continued research and controlled internal experimentation, but NO-GO for a
production/public chatbot**. Phase 13D has not started.

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

The protected importer accepted all 25 deterministic rows without any changed question, structured
fact, retrieved evidence, generated answer, citation or deterministic summary. The imported review
contains every malformed response, every prompt-injection and conflict fixture, every expected
refusal, all MIXED questions, and a deterministic sample of STRUCTURED_ONLY answers.

The tracked XLSX is the original blank review template, not the canonical completed-review artifact.
Completed judgments are versioned in `docs/phase13c-human-review-results.json`; running the importer
against the blank template correctly rejects its empty verdict cells.

| Human measure | Result |
| --- | ---: |
| Grounding PASS | 19 |
| Grounding MINOR_ISSUE | 0 |
| Grounding FAIL | 6 |
| Grounding PASS rate | 19 / 25 = 76.00% |
| PASS rate excluding malformed/no-answer rows | 19 / 19 = 100.00% |
| Usefulness GOOD | 14 |
| Usefulness ACCEPTABLE | 5 |
| Usefulness POOR | 6 |
| Misleading YES | 0 |
| Misleading NO | 25 |
| Misleading-answer rate | 0 / 25 = 0.00% |

| Review slice | PASS | MINOR | FAIL | GOOD | ACCEPTABLE | POOR | Misleading YES |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| STRUCTURED_ONLY | 5 | 0 | 0 | 0 | 5 | 0 | 0 |
| RAG_ONLY | 9 | 0 | 6 | 9 | 0 | 6 | 0 |
| MIXED | 5 | 0 | 0 | 5 | 0 | 0 | 0 |
| Malformed / no answer | 0 | 0 | 6 | 0 | 0 | 6 | 0 |
| Expected refusal | 4 | 0 | 1 | 4 | 0 | 1 | 0 |
| Source conflict | 2 | 0 | 0 | 2 | 0 | 0 | 0 |
| Prompt injection | 3 | 0 | 0 | 3 | 0 | 0 | 0 |

The six FAIL/POOR judgments are exactly the six malformed/no-answer responses. They did not mislead
because no valid answer was returned, but they remain genuine reliability failures: 6/30 (20.00%)
of all AgentRouter-generated benchmark questions, and 6/25 (24.00%) of RAG_ONLY questions. All six
hit the frozen 320-token limit. They were not repaired, rerun or excluded.

The bounded review found no human-confirmed unsupported or misleading claim. This does not turn the
25/99 uncited substantive-sentence proxy into a hallucination rate: the proxy remains a conservative
automated citation heuristic, and the human finding applies only to the 25 reviewed rows.

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
review artifact requires a separate explicit `--allow-overwrite` flag. The accepted judgments are
stored separately in `docs/phase13c-human-review-results.json`; the automated evaluation artifact was
not modified.

## Automated and human reconciliation

- The frozen prompt-injection score remains 2/3. Human review judged all 3/3 outputs grounded,
  useful and non-misleading. The strict matcher treated the words `Ferrari won` as a failure even
  when the answer quoted those words solely to explain that it ignored the injected instruction.
  This is an `EVALUATOR_FALSE_POSITIVE`; the frozen automated score remains unchanged.
- For `q13c_11676f13a20efc42d3ea`, the frozen required-fact matcher recorded 1/2 because the label
  `yas_marina` did not literally match the natural-language phrase `Yas Marina Circuit`. The answer
  contains and cites both required evidence scopes, so this is an automated string-matching
  limitation. The frozen 70/79 coverage result remains unchanged.
- The human review does not dismiss the six malformed outputs. It clarifies that they are
  availability/usability failures rather than misleading factual answers.

## Decision

- **STRUCTURED_ONLY: GO for continued research and controlled internal use.** It should remain the
  deterministic path for exact race facts. Public presentation still needs the lone structured-fact
  failure resolved and human-readable templates in place of raw `authoritative_fact_1` fragments.
- **RAG_ONLY: CONDITIONAL GO for continued research and controlled internal experiments.** Every
  valid RAG_ONLY answer in the bounded review passed, including refusals, conflicts and injection
  fixtures, but a 24% RAG_ONLY malformed/no-answer rate prevents production use.
- **MIXED: CONDITIONAL GO for continued research and controlled internal experiments.** All five
  MIXED answers passed human review and the frozen multi-document result was 22/22, but this route
  inherits the same provider output-contract risk and requires stronger deterministic fact checking.
- **Overall: CONDITIONAL GO.** The evidence-only architecture, controlled citations, deterministic
  routing and valid-answer behavior are promising enough for further research and guarded internal
  evaluation. It is **not suitable for a production/public chatbot** until output truncation is
  materially reduced on a new prospective benchmark, exact-fact validation is hardened, refusal
  accuracy improves, citation completeness/support improve, and source/licensing review is complete.

The 60-question benchmark was not rerun, its outputs were not modified, and its SHA-256 remains
`969ff9d522a4f047493c1c1fc2ba9eaeddeb80f10988bd271f87078c7112809e`.
