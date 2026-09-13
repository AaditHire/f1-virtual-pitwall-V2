# Phase 13D-C — Experiment B: One Bounded Mechanical Retry

## Status before execution

FROZEN PROTOCOL — NOT YET RUN

Experiment B evaluates a policy applied to the original Phase 13D-B Experiment A 640-token arm.
The original 70 successful generated answers are retained unchanged. Each of the 30 original
mechanical failures receives exactly one new, independent attempt. This is a policy-recovery
experiment, not a fresh independent 100-question prospective validation.

## Hypothesis

For the 640-token candidate configuration, allowing one identical retry after an explicitly
detected mechanical generation failure will materially reduce the final unrecovered failure rate
without introducing misleading or ungrounded recovered answers.

## Frozen intervention

The only changed operational behavior is:

```text
mechanical failure -> one additional independent attempt
```

Eligibility is restricted to original Experiment A `TREATMENT_640` results classified as:

- `TRUNCATED_RESPONSE`
- `SCHEMA_FAILURE`
- `TIMEOUT`

Valid parsed answers, quality failures, fact misses, citation failures, refusals, and human-review
judgments do not trigger a retry. There is no third attempt.

The retry uses the same AgentRouter provider, `claude-opus-4-8` model, official Anthropic Messages
SDK, 640-token allowance, system prompt, user message, evidence bundle and ordering, source IDs,
retrieval, routing, forced `grounded_answer` tool schema, SDK-default sampling, parser, and
validation as Experiment A. SDK and transport automatic retries remain disabled.

## Evaluation

The final policy retains each original success and substitutes a retry result only when that retry
parses successfully. Mechanical recovery, final unrecovered failures out of the original 100
generation questions, recovery by original category, token/latency accounting, all frozen
automated guardrails, and recovered MIXED exact facts are reported separately.

The absolute reliability target remains no more than 2/100 unrecovered failures, with 0/100
preferred. All successfully recovered retry answers require genuine human review. Experiment B is
`PAUSED — HUMAN REVIEW REQUIRED` until that review is imported. Any human-confirmed misleading
recovered answer is a serious safety failure.

The frozen Experiment A evaluation, outputs, raw responses, benchmark, and Phase 13C benchmark are
never modified. The known FIA porpoising evidence defect for `q13d_8fd5589debc7a772c1ed` remains a
separate diagnostic annotation and is not repaired during Experiment B.

A successful Experiment B result cannot establish production readiness. The production/public
chatbot remains **NO-GO**.
