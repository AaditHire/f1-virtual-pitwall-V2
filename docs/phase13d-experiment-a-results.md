# Phase 13D-B — Experiment A Results

## Decision

**H1: NOT SUPPORTED under the preregistered composite decision rule.** The paired result provides
strong evidence that 640 tokens reduced mechanical failures relative to 320 tokens, but 640 still
had 30/100 unrecovered failures, missed the absolute <=2/100 target, and failed multiple quality
guardrails. Human review is pending. The production/public chatbot remains **NO-GO**.

## Pre-run amendment and freeze

The pre-run repository was clean at commit `6624d3a40a254581f7a45fbc83293322293bc196`
(`origin/main` at the same commit), containing the completed Phase 13D-A protocol and harness.
No Phase 13D provider output existed when the single-arm historical-control design was replaced by a
paired prospective design. The unchanged 100 generation questions each received one 320-token and
one 640-token call in consecutive, deterministically randomized pairs (seed `130320640`; 50 pairs in
each first-arm order). Both arms used zero retries. The original protocol and original unrun manifest
remain in Git history with SHA-256 values `d6aefdfa930733e9ba1cac5cf5a948ecd6d3b8f1d61db2149e58a0e360e5c8be`
and `54331623b9f45ed67bd901263cca191ed960dbf4f8b4de3eefeb7e273ad62eef`, respectively. The amended protocol hash is
`28f18e479b44b04b87b7d105ac345a0dc51412010a9c0ce82670c60f34e02664`, schedule hash
`7e7e36a801f058ab4c137f791a5410324fffadcce1a9405e50d742ffb33c1db5`, input hash
`c965a986e9161a08170ac3441cda18d6d112810711f25edbc973abf78caa12d5`, and manifest hash
`163480bb6adf0a41bdd0e76ed58f11adc5e0657eeed60f39dafb824f85688827`. The Phase 13D benchmark
remained unchanged at `e5f9abfef2ea4dca3f9a36e84d9fe9917bd7de76945c42d7753e636bd51eec74`.

## Configuration and execution

- Provider: AgentRouter at `https://agentrouter.org`; official Anthropic SDK; Bearer/auth-token
  authentication from the ignored `AGENTROUTER_API_KEY` environment variable.
- Model: `claude-opus-4-8`; SDK-default temperature; forced, non-executing `grounded_answer` tool.
- Identical system prompt, user-message construction, evidence, retrieval, source order, tool schema,
  routing and validation between arms. Only `max_tokens` differed: 320 versus 640.
- Exactly 200 requests completed: 100 per arm, zero retries. The 30 `STRUCTURED_ONLY` questions were
  evaluated deterministically in both arm summaries and made no provider call.

## Reliability and paired comparison

| Result | 320 | 640 |
| --- | ---: | ---: |
| Unrecovered failures | 79/100 (79%) | 30/100 (30%) |
| Malformed | 78/100 (78%) | 26/100 (26%) |
| Truncations | 77/100 (77%) | 22/100 (22%) |
| Schema failures | 1/100 | 4/100 |
| Transport failures | 1/100 | 4/100 |

Paired table: 20 both succeeded; 50 had 320 fail/640 succeed; 1 had 320 succeed/640 fail; 29 both
failed. The treatment-minus-control failure difference was -49 percentage points, a 62.03% relative
reduction. The exact two-sided McNemar p-value was `4.618527782440651e-14`. The paired 100,000-sample
bootstrap (seed `130640320`) placed the control-minus-treatment failure-rate difference at 0.39 to
0.59 (95% percentile interval).

## Frozen metrics by arm

| Metric | 320 | 640 | Candidate threshold |
| --- | ---: | ---: | ---: |
| Citation validity | 64/64 (100%) | 185/185 (100%) | >=99% |
| Citation completeness | 21/100 (21%) | 70/100 (70%) | >=90% |
| Citation support | 21/100 (21%) | 70/100 (70%) | >=90% |
| Unsupported/uncited sentence proxy | 14/91 (15.38%) | 75/288 (26.04%) | <=25.25% |
| Required-fact coverage | 52/170 (30.59%) | 115/170 (67.65%) | >=90% |
| Correct refusals | 5/5 | 4/5 | 5/5 |
| False refusals | 2/125 | 1/125 | 0 |
| Multi-document synthesis | 6/37 (16.22%) | 34/37 (91.89%) | >=95% |
| Conflict handling | 2/2 | 2/2 | 2/2 |
| Deterministic `STRUCTURED_ONLY` | 30/30 | 30/30 | 30/30 |
| `MIXED` exact-fact consistency | 3/25 | 22/25 | 25/25 |
| Route compliance | 130/130 | 130/130 | 130/130 |
| Prompt-injection resistance | 2/3 | 3/3 | 3/3 |

The unsupported/uncited sentence measure is only the preregistered proxy; it is not a hallucination
rate. The authoritative detailed metrics are in the corrected evaluation artifact. It preserves the
first derived evaluation and explicitly corrects its projection of the separate 30/30 deterministic
guardrail; no raw or normalized output changed.

## Usage and cost

| Accounting | 320 | 640 |
| --- | ---: | ---: |
| Input tokens | 339,628 | 325,524 |
| Output tokens | 29,737 | 41,552 |
| Total tokens | 369,365 | 367,076 |
| Median latency | 5,918.19 ms | 7,264.94 ms |
| P90 latency | 8,490.53 ms | 10,250.84 ms |

Combined usage was 665,152 input, 71,289 output and 736,441 total tokens. AgentRouter returned no
price/balance field, so provider-credit cost is unknown. New external cash spent was $0.
Eight pairs did not have identical reported input-token counts despite identical frozen message
hashes: four because the timed-out arm returned no usage, and four because the provider reported a
one-token difference. This is a provider metering/accounting limitation, not a prompt difference;
token totals should not be interpreted as a perfectly paired cost comparison.

## Human review status and methodological concerns

Human review is **PENDING_GENUINE_HUMAN_REVIEW**; all 169 template rows have blank labels. No human
safety conclusion is available and no label was fabricated. The frozen procedure requested ten
additional valid non-mandatory paired RAG questions, but only eight such pairs existed after the high
failure rate. The first template derivation stopped rather than silently weakening that requirement.
The final blank selection conservatively includes all eight available valid pairs, every mandatory
MIXED/refusal/injection/conflict arm row, and every terminal failure. This post-run sampling shortfall
is recorded in both review artifacts and does not alter the automated comparison.

The high failure rates and guardrail misses mean that the intervention isolates a real token-budget
effect but does not produce an acceptable candidate. Experiment B must not start without separate
scientific review and authorization.

## Result artifact hashes

- Raw JSONL: `75a66fba6105de5c3999fda2aa0dcc8647ab956d54a22c8966727f564bcf4ade`
- Normalized outputs: `55a1d41c4ea6edfe5875274be66a283a11b0e9e9ebd8a2efc5ff6f6a36c6de28`
- Original derived evaluation: `99b4e1d898f158ab942665aab82bb23a0d248e7ac51d11e2e8da27075ae7b57e`
- Corrected authoritative evaluation: `139a4cd5223ee897912f14269e2841696a3f4c9595f08799db65eb7be69ddb81`
- Usage: `6db43f26c5a2400eb3d0e38e0f803f97cf19411a6839114565636513c46e6e84`
- Blank review template: `7609da225597e6713675acd1a2199e3b8270f4cb53b454d4f77225eeaefb00c6`
- Protected review map: `ecf0a608dcff875f3737bef16b1e5bc19e8a02f4adecb1cc4d13656a2391b947`
