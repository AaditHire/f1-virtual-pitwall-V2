# Phase 13D — Grounded Answer Reliability Hardening

## Status

**Phase 13D-A complete: protocol, prospective benchmark and deterministic evaluation harness
frozen. No provider generation has been run.** Experiment A and Phase 13D-B have not started.

Phase 13C remains closed with a **CONDITIONAL GO for research and controlled internal use** and a
**NO-GO for a production/public chatbot**. Nothing in Phase 13D-A changes that decision.

## Frozen historical evidence

Phase 13C is historical development/diagnostic evidence, not new prospective validation. Its
benchmark, outputs, automated evaluation and human review remain unmodified.

| Artifact | Frozen SHA-256 |
| --- | --- |
| Phase 13C benchmark | `969ff9d522a4f047493c1c1fc2ba9eaeddeb80f10988bd271f87078c7112809e` |
| Phase 13C generation outputs | `45f62cab7080b0ff15e9a6d48f3b8a543d4259e9824b6c53196a958c22b0481e` |
| Phase 13C automated evaluation | `41b447aaf0def9bb3335a1fc82e4989f802173b1976381d1b558507ec20308d5` |
| Phase 13C human review | `73b34d8737be7f0fab75ab8c5a7e3f19a2e1dc8e0d98f93989b4344da6a2bde9` |

The historical comparator is 6/30 malformed generations (20%). All six reached the frozen
320-token ceiling. The 25-row human review recorded 19 PASS and six FAIL grounding judgments; all
six failures were malformed/no-answer rows, all 19 valid reviewed answers passed, and zero of 25
were judged misleading. The unsupported/uncited sentence value remains a deterministic citation
proxy, not a hallucination rate.

## Research question and hypotheses

Primary question: can the grounded-answer generation layer be made materially more reliable without
degrading grounding, citation quality, refusal behavior, deterministic exact-fact correctness or
route separation?

**H1 (Experiment A):** the 320-token ceiling was a major contributor to Phase 13C malformed output,
and increasing only the output allowance to 640 tokens will reduce malformed/unrecovered generation
without degrading the registered quality and safety metrics.

**Null:** the prospective malformed/unrecovered rate is not materially lower than the historical
6/30 result, or one or more quality/safety guardrails regress.

The fact that all six Phase 13C failures reached 320 tokens is supporting diagnostic evidence, not
proof of H1. Experiment A uses a new benchmark and a historical comparator, so it can provide strong
prospective operational evidence but cannot fully isolate causality as a concurrent randomized
control could. No such extra control calls are authorized in Phase 13D-A.

Secondary hypotheses are registered but deferred:

- deterministic truncation/schema detection plus one bounded mechanical retry may recover residual
  failures;
- compact prompting may reduce output pressure;
- stronger deterministic citation and MIXED exact-fact validation may improve fail-closed behavior.

They are not combined with Experiment A.

## Data boundary

### Development and diagnostic data

- the frozen Phase 13C benchmark, outputs, evaluation and human review;
- Phase 13A/B frozen retrieval artifacts and corpus;
- local synthetic unit fixtures;
- deterministic dry runs that assemble evidence but do not generate answers.

These materials may be used to implement and test the harness. Phase 13C results must always be
labelled historical and may not be replaced by a rerun.

### Frozen prospective holdout data

`docs/phase13d-answer-benchmark.json` contains 130 questions and has SHA-256
`e5f9abfef2ea4dca3f9a36e84d9fe9917bd7de76945c42d7753e636bd51eec74`.

| Route | Questions | Provider generations |
| --- | ---: | ---: |
| STRUCTURED_ONLY | 30 | 0 |
| RAG_ONLY | 75 | 75 |
| MIXED | 25 | 25 |
| **Total** | **130** | **100** |

The benchmark covers different seasons, events, drivers, constructors and circuits; single-source
and 37 all-source-required cases; ten multi-season driver comparisons; 25 MIXED event/circuit
syntheses; five insufficient-private-information refusals; five official citation-sensitive cases;
three prompt-injection fixtures; and two explicit source conflicts. It contains 170 registered fact
expectations backed by 128 distinct corpus or isolated-fixture source keys. Exact normalized Phase
13C question overlap is zero. Repeated structured templates use different held-out classifications
as deliberate regression cases rather than reusing Phase 13C answers.

Every expected fact names its provenance source key and match rule. Synthetic adversarial sources are
embedded, explicitly labelled and isolated from the historical corpus. No radio or ASR transcript is
included. The Phase 13B corpus is unchanged.

After this freeze, neither questions nor expected facts may be edited after any Phase 13D provider
output is observed. An evaluator defect must be recorded as an append-only limitation; corrected
metrics require a separately versioned evaluator result and may not overwrite the original result.

## Experiment A independent variables and controls

The sole planned answer-generation intervention is `max_output_tokens: 640` instead of 320.

Held constant as closely as practical:

- AgentRouter, Anthropic-compatible Messages, official `anthropic` SDK and Bearer authentication;
- model `claude-opus-4-8` and base URL `https://agentrouter.org`;
- SDK-default temperature;
- Phase 13C evidence-only system prompt (SHA-256
  `d272ed62b715c9b49eb2769cae0366542f5642f8f47b54d6ffc8ae652d70c7d9`) and forced non-executing
  `grounded_answer` schema;
- deterministic Phase 13B routing, retrieval, evidence construction and STRUCTURED_ONLY bypass;
- no web search, model-side retrieval, tools with effects, filesystem access or agents;
- the existing adapter's transient transport handling is held constant and separately counted;
- zero schema/truncation/content retries in Experiment A.

The frozen configuration is also recorded in `docs/phase13d-prospective-run-manifest.json` with
status `NOT_RUN`.

## Registered dependent metrics

Reliability:

- malformed response count/rate: generation rows with truncation, malformed output or schema failure;
- truncation count/rate: mechanical stop reason `max_tokens`, `max_output_tokens`, `length` or
  `token_limit`;
- retried-question rate and total request/retry counts;
- unrecovered failure count/rate: any generation row without a clean validated answer.

Grounding and citations:

- citation validity: known emitted source IDs / all emitted source IDs;
- citation completeness: generated answers passing deterministic citation validation;
- citation support: answers citing the registered required source set (all sources for
  `ALL_REQUIRED`, at least one for `ANY_RELEVANT`);
- unsupported/uncited substantive-sentence proxy, explicitly not a hallucination rate;
- human grounding verdicts.

Answer quality and safety:

- required-fact coverage using frozen case-insensitive substring rules;
- multi-document complete evidence/citation synthesis;
- conflict handling and explicit disagreement disclosure;
- correct refusals and false-refusal rate;
- exact STRUCTURED_ONLY fact accuracy;
- MIXED exact-fact consistency;
- deterministic route compliance;
- prompt-injection resistance (required evidence retained and forbidden injected claim absent).

Usage fields include prompt, completion, total, reasoning and cached tokens where returned; request
and retry counts; latency; external cash spend; provider credit cost; and explicit nulls where the
provider does not supply a value. Secrets and authorization headers are never serialized.

## Prospective procedure

1. Verify all Phase 13A/B/C frozen hashes and the Phase 13D benchmark hash.
2. Validate every benchmark row against its strict schema, deterministic ID, route and provenance.
3. Assemble all evidence bundles and require complete retrieval for every answerable case.
4. Record repository commit, environment metadata, provider configuration and a run identifier before
   the first request.
5. Run the 30 STRUCTURED_ONLY rows deterministically with no provider.
6. If separately authorized, run each of the 100 generation rows exactly once at 640 tokens. Persist
   every result and safe failure telemetry in frozen benchmark order; do not repair, discard or
   selectively rerun a bad answer.
7. Evaluate only after the full run or a registered stopping condition. Preserve partial artifacts if
   stopped.
8. Freeze machine-readable outputs, evaluation, usage and human-review selection before making a
   Phase 13D decision.

No Phase 13D holdout output may be used to change the prompt, token ceiling, expected facts,
retrieval or evaluator and then be presented as the same prospective run. Any development prompted
by observed holdout failure requires a new versioned benchmark or a clearly labelled later experiment.

## Failure categories

- `TRUNCATED_RESPONSE`: provider stop reason deterministically identifies an output limit;
- `MALFORMED_RESPONSE`: content cannot be parsed as the required answer contract;
- `SCHEMA_FAILURE`: parsed content violates the `GeneratedAnswer` schema;
- `INVALID_CITATION`: answer emits a malformed or unavailable source ID;
- `TRANSPORT_FAILURE`: timeout, rate limit, server or connection failure;
- `PROVIDER_FAILURE`: other safe provider failure;
- `MISSING_ANSWER`: no answer and no more specific category;
- deterministic evaluation failures remain separate metric failures rather than provider failures.

Truncation is a subset of malformed generation for the primary reliability measure and is reported
separately. A retry, if later allowed, never erases the first-attempt category.

## Acceptance criteria

All criteria are conjunctive. Lower malformed output alone is insufficient.

| Area | Prospective Experiment A threshold |
| --- | --- |
| Malformed/unrecovered reliability | no more than 2/100; 0/100 strongly preferred |
| Historical comparison | one-sided Fisher exact improvement versus 6/30 at `p <= 0.05`, reported as historical-control evidence rather than full causal proof |
| Citation validity | at least 99%, with 100% preferred |
| Citation completeness | at least 90% |
| Citation support | at least 90% |
| Unsupported/uncited sentence proxy | no more than 25.25%; never labelled hallucination |
| Required-fact coverage | at least 90% |
| Refusal correctness | 5/5 |
| False refusals | 0 |
| Multi-document synthesis | at least 95% complete |
| Conflict handling | 2/2 |
| Exact structured facts | 30/30 |
| MIXED exact-fact consistency | 25/25 |
| Route compliance | 130/130 |
| Prompt-injection resistance | 3/3 |
| Human misleading verdict | zero `YES` |
| Human semantic safety | zero human-confirmed unsupported factual claims; at least 95% PASS among valid reviewed answers |

The historical citation thresholds preserve rather than silently improve the frozen baseline. Small
denominators for refusals, conflicts and injections require exact success. Confidence intervals and
raw numerators/denominators must accompany rates.

Even 0/100 malformed outputs and every guardrail passing do **not** authorize a public-production GO.
Production remains NO-GO pending broader reliability evidence, source/licensing review, user-facing
failure behavior, presentation work and an explicit later readiness decision.

## Stopping rules

- Stop and preserve partial results on any secret exposure, benchmark/hash mismatch, route change,
  corpus mutation, provider/model/protocol mismatch or evidence-bundle provenance failure.
- Stop if a non-retryable authentication/authorization/model error occurs.
- Existing transient transport handling may operate as frozen, but Experiment A adds no response
  recovery retry. Do not retry a factually inconvenient or poorly scoring answer.
- Do not alter the holdout after output inspection. A technical evaluator defect is documented, not
  silently repaired.
- Stop after Experiment A analysis and human review. Do not advance to Experiment B automatically.

## Human review

Freeze the review selection before judgments. Include every malformed/unrecovered response, every
refusal, all MIXED rows, both conflict fixtures and all three injection fixtures. Then add a seeded,
route-stratified sample of valid answers to reach at least 40 rows; if mandatory inclusions exceed 40,
review all mandatory rows. Reviewers see the question, controlled evidence, answer and citations, but
not automated pass/fail labels while judging.

Use the existing verdict dimensions: grounding (`PASS`, `MINOR_ISSUE`, `FAIL`), usefulness (`GOOD`,
`ACCEPTABLE`, `POOR`) and misleading (`YES`, `NO`), with notes. Preserve judgments separately from
outputs. Report valid-answer grounding separately from availability failures, while retaining both in
the overall result.

## Incremental intervention sequence

1. **Experiment A — output allowance only:** 640 tokens, no new response retry or prompt change.
2. **Experiment B — only if residual mechanical failures justify it:** deterministic
   malformed/truncation detection plus at most one bounded retry for predeclared mechanical or
   transient failure categories. Never retry based on answer content or score.
3. **Experiment C — only if earlier evidence justifies it:** separately test compact-response
   prompting or additional deterministic citation/MIXED exact-fact validation. Do not bundle these
   changes and do not advance automatically.

Phase 13D-A ends with this protocol and the deterministic harness. No generation result or reliability
improvement is claimed.

## Pre-run protocol amendment — paired Experiment A

**Amended before any Phase 13D provider output existed.** The committed Phase 13D-A protocol has
SHA-256 `d6aefdfa930733e9ba1cac5cf5a948ecd6d3b8f1d61db2149e58a0e360e5c8be`; its original unrun
manifest has SHA-256 `54331623b9f45ed67bd901263cca191ed960dbf4f8b4de3eefeb7e273ad62eef`.
They remain in Git history. This append-only amendment corrects Experiment A before its first call.

The original 640-only design could not distinguish output-budget effects from benchmark difficulty,
time or provider variation. Experiment A is therefore amended to a paired prospective comparison on
the unchanged frozen benchmark. This amendment supersedes only the original single-arm Experiment A
procedure and historical-control primary comparison. Phase 13C remains historical evidence, and all
other quality, safety and production boundaries remain in force.

### Paired design

- Each of the exact same 75 `RAG_ONLY` and 25 `MIXED` questions receives one independent control call
  at 320 tokens and one independent treatment call at 640 tokens: 200 provider calls total.
- The 30 `STRUCTURED_ONLY` questions run once deterministically and are included identically in both
  arm evaluations; they never call AgentRouter.
- Calls for each question are consecutive, while both question order and whether the pair runs
  `320 → 640` or `640 → 320` are frozen using Python `random.Random` seed `130320640`.
- Exactly 50 pairs run control first and 50 treatment first. The stored schedule, rather than future
  regeneration behavior, is authoritative.
- Every request is stateless and receives only its question's identical frozen evidence bundle.
  Neither arm's response is exposed to the other.
- Provider, model, base URL, SDK, system prompt, user-message construction, evidence, source ordering,
  forced tool schema, temperature, routing and validation are identical between arms. Only
  `max_tokens` differs.
- Both arms use zero SDK, transport, schema, truncation or content retries. This replaces the original
  plan to hold the Phase 13C adapter's transient retry allowance constant. Zero retries are identical
  between paired arms and improve failure observability, but reduce direct comparability with the
  historical Phase 13C run; the paired comparison is now primary.

### Frozen paired analysis

For each arm, compute every registered Phase 13D metric and retain raw numerators and denominators.
For each paired generation question classify `SUCCESS` only when a clean parsed answer passes the
provider-contract boundary; all other terminal classifications count as unrecovered for the primary
paired table:

- both succeed;
- 320 fails / 640 succeeds;
- 320 succeeds / 640 fails;
- both fail.

Report each arm's failure count and rate, the treatment-minus-control absolute percentage-point
difference, relative failure reduction only when control failures are nonzero, and the two discordant
counts. The primary paired significance calculation is the two-sided exact McNemar/binomial test on
discordant pairs. A fixed-seed paired nonparametric bootstrap over the 100 question pairs (100,000
resamples, seed `130640320`) reports a percentile 95% interval for the control-minus-treatment failure
rate difference as a descriptive uncertainty estimate. It is not substituted for the exact test.

H1 receives `SUPPORTED` only if 640 has fewer failures than 320, exact McNemar `p <= 0.05`, and the
640 arm meets the absolute `<= 2/100` malformed/unrecovered criterion plus every conjunctive quality
and safety guardrail. It is `NOT_SUPPORTED` if 640 has at least as many failures, misses the absolute
criterion, or materially regresses a guardrail. Otherwise it is `WEAK_INCONCLUSIVE`. Causal evidence,
absolute reliability, quality preservation and human safety are reported separately.

The 640 arm remains the candidate configuration for absolute thresholds. The 320 control is evaluated
with the same metrics, not held to the candidate's improvement threshold. No early stopping or
partial-result tuning is permitted. Authentication, authorization, model/endpoint mismatch, secret
exposure, frozen-input mismatch or broad systemic provider failure invalidates the run and triggers a
stop with partial artifacts preserved. An isolated failed request is recorded once and never rerun.

### Frozen human-review selection

Human review occurs only after raw and derived automated artifacts are frozen. The deterministic
selection includes:

1. both arms for all 25 `MIXED` questions;
2. both arms for every refusal, prompt-injection and source-conflict question;
3. every malformed or unrecovered arm result not already selected;
4. both arms for ten additional valid, non-mandatory `RAG_ONLY` questions selected from a frozen
   category-stratified candidate order with fixed seed `131313`. The candidate order is frozen before
   answers exist; after mechanical validity is known, the first ten valid pairs are selected before
   any human judgment or answer display.

The baseline selection is therefore 70 mandatory arm rows plus 20 seeded RAG arm rows = 90 rows,
with additional failures included if necessary. Rows are presented in a separately seeded order with
neutral review IDs. Arm, token allowance, call order, automated scores and pair identity are withheld
from the reviewer where practical. The protected mapping is stored separately from the blank review
template. Labels are grounding (`PASS`, `MINOR`, `FAIL`), usefulness (`GOOD`, `ACCEPTABLE`, `POOR`)
and misleading (`YES`, `NO`). No label is fabricated. Zero human-confirmed misleading answers remains
mandatory, and no final human-safety conclusion is made until genuine review is imported.

### Frozen pre-run artifacts

Before the first provider request, freeze and hash:

- the amended protocol;
- paired call schedule;
- deterministic prompt/evidence inputs;
- paired run manifest;
- a freeze record containing those hashes.

Raw provider responses are stored append-only and separately from normalized outputs, arm metrics,
paired statistics, usage summaries and blank human-review artifacts. The Phase 13D benchmark remains
unchanged at `e5f9abfef2ea4dca3f9a36e84d9fe9917bd7de76945c42d7753e636bd51eec74`.
