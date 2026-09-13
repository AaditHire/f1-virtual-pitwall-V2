# Phase 13D-D — Experiment C Protocol

STATUS: DEVELOPMENT / DIAGNOSTIC — NOT AN UNTOUCHED PROSPECTIVE HOLDOUT

The Phase 13D benchmark has been used for development and intervention selection. Experiment C is
therefore diagnostic development evidence and cannot establish production readiness. Any later
production-readiness evaluation requires a new untouched prospective holdout.

## Hypothesis and population

H3 asks whether one otherwise-identical 1280-token fallback recovers a large majority of all 22
questions whose original Experiment A 640-token attempt was `TRUNCATED_RESPONSE`, while preserving
the frozen quality, exact-fact, refusal, routing, citation, and safety boundaries.

All 22 original truncations are eligible, including the three that happened to recover in the later
Experiment B same-budget retry. Execution order is deterministic by question ID. Each receives
exactly one new call. No non-truncation question receives a call and no second fallback is allowed.

## Fixed generation boundary

Only `max_tokens` changes from 640 to 1280. Provider (AgentRouter), model
`claude-opus-4-8`, official Anthropic SDK, system and user prompts, forced `grounded_answer` tool,
answer schema, SDK-default sampling, retrieval, evidence and source ordering, routing, request
construction, parsing, and validation remain unchanged. There is no answer compaction, repair,
JSON repair, citation repair, retrieval change, model-side search, alternate model, or retry.

## Candidate policy

Over the original 100 generation questions, retain every successful original 640-token answer. Use
the Experiment B 640-token retry only for original `TIMEOUT` and `SCHEMA_FAILURE` cases. For every
original `TRUNCATED_RESPONSE`, ignore the Experiment B truncation retry and use the new 1280-token
fallback. The runtime policy therefore contains at most one fallback.

The observed 640 retry result (3/22) and the 1280 result may be compared descriptively. They were
run at different times and are not concurrent randomized arms, so no strict causal superiority or
randomized significance claim is permitted.

## Endpoints and review

Report recovery and remaining truncations out of 22, percentage-point difference from 3/22, final
candidate-policy unrecovered failures out of 100, all frozen quality guardrails, MIXED exact facts,
and usage/latency. The absolute target remains <=2/100, with 0/100 preferred.

Preserve the authoritative frozen metrics. For `q13d_8fd5589debc7a772c1ed`, separately report a
defect-aware sensitivity that excludes its two evidence-absent required facts and does not count its
evidence-faithful refusal as a model false refusal. Never replace the frozen result with this layer.

Every successfully parsed 1280 fallback answer enters a protected, unsampled human-review workbook
with blank Grounding, Usefulness, Misleading, and notes fields. Experiment C pauses after automated
evaluation until genuine human labels are imported.

Phase 13C remains CLOSED — CONDITIONAL GO. Experiment A's narrow token-budget effect remains
supported while full H1 remains unsupported. Experiment B mechanical retry remains supported but
insufficient overall. The production/public chatbot remains NO-GO.
