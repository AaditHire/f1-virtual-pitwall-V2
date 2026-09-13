# Phase 13D-C — Experiment B Results

STATUS: COMPLETE — MECHANICAL RECOVERY SUPPORTED BUT HETEROGENEOUS; ABSOLUTE RELIABILITY AND AUTOMATED QUALITY FAILED; HUMAN SAFETY PASS WITH MINOR QUALITY QUALIFICATION; PRODUCTION/PUBLIC CHATBOT NO-GO

Experiment B is a bounded policy-recovery experiment over the original Experiment A 640-token first attempts, not a fresh independent 100-question prospective validation.

## Mechanical recovery

- Recovered: 11/30 (36.67%).
- Original schema failures: 4/4 recovered (100%).
- Original timeouts: 4/4 recovered (100%).
- Original truncations: 3/22 recovered (13.64%).
- Original truncations remaining unrecovered: 19/22; truncation is the dominant unresolved
  mechanical failure mode after one retry.
- Retry outcomes: 11 SUCCESS, 18 TRUNCATED_RESPONSE, 1 SCHEMA_FAILURE.
- Final unrecovered failures: 19/100.
- Absolute <=2/100 target met: False.
- Automated guardrails passed: 9/14.
- New provider calls: 30; automatic retries: 0; third attempts: 0.

## Policy guardrails

- PASS: citation validity 206/206; unsupported/uncited sentence proxy 83/336 (24.70%);
  refusal correctness 5/5; multi-document synthesis 37/37; conflict handling 2/2;
  STRUCTURED_ONLY 30/30; MIXED exact facts 25/25; route compliance 130/130; prompt injection 3/3.
- FAIL: unrecovered reliability 19/100; citation completeness 81/100; citation support 81/100;
  required-fact coverage 124/170; false refusals 1/125.
- Three MIXED retry answers recovered and all three preserved their deterministic structured fact.

## Usage

- New AgentRouter calls: 30.
- Input/output/total tokens: 78,483 / 16,594 / 95,077.
- Median/P90 latency: 6,794.7 / 7,819.9 ms.
- AgentRouter returned no provider credit-cost or balance field. Recorded new external cash spend:
  $0.00.

## Human safety

All 11 successfully recovered answers received genuine human review. Grounding was 10 PASS, one
MINOR and zero FAIL; usefulness was 11 GOOD, zero ACCEPTABLE and zero POOR; misleading was zero YES
and 11 NO. Human safety is **PASS WITH MINOR QUALITY QUALIFICATION**.

The sole MINOR case is `q13d_f5d058442ae24723b194`. The individual Leclerc race facts are
supported, but the response says there are "three" troubled/retirement races and then lists four
(Monaco, Hungary, French GP, and Russian GP). This is an internal counting/summarization error, not
an unsupported individual race fact. Canonical results are in
`docs/phase13d-experiment-b-human-review-results.json`.

## Evidence diagnostic

The frozen FIA porpoising question `q13d_8fd5589debc7a772c1ed` still lacks the two expected safeguards in its supplied evidence. The retained answer declined to fabricate them. Frozen Experiment A scores and evidence remain unchanged.

## Interpretation boundaries

Mechanical recovery, absolute reliability, automated quality preservation, human safety, and architectural justification are separate decisions. Even a perfect retry result is insufficient for a production/public chatbot GO.

The final decisions are deliberately separate:

- **Mechanical recovery: SUPPORTED, but heterogeneous.** Overall recovery was 11/30 (36.67%):
  TIMEOUT 4/4, SCHEMA_FAILURE 4/4, and TRUNCATED_RESPONSE 3/22.
- **Absolute reliability: FAILED.** The final policy had 19/100 unrecovered failures against the
  <=2/100 target.
- **Automated quality guardrails: FAILED.** Nine of 14 passed; all frozen automated metrics above
  remain authoritative and unchanged.
- **Human safety: PASS WITH MINOR QUALITY QUALIFICATION.** There were no human-confirmed misleading
  recovered answers and no Grounding FAILs; 10 answers were PASS and one was MINOR.
- **Architectural interpretation:** one bounded retry is supported as a useful internal/research
  recovery mechanism for clearly mechanical TIMEOUT and SCHEMA_FAILURE conditions. It is not
  supported as an adequate general solution for truncation: 19/22 original truncation failures
  remained unrecovered.

This experiment does not establish production readiness. Experiment C has not started and must not
begin without scientific review. The production/public chatbot remains **NO-GO**.
