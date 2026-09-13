# Phase 13D-C — Experiment B Results

STATUS: PAUSED — HUMAN REVIEW REQUIRED; PRODUCTION/PUBLIC CHATBOT NO-GO

Experiment B is a bounded policy-recovery experiment over the original Experiment A 640-token first attempts, not a fresh independent 100-question prospective validation.

## Mechanical recovery

- Recovered: 11/30 (36.67%).
- Original schema failures: 4/4 recovered (100%).
- Original timeouts: 4/4 recovered (100%).
- Original truncations: 3/22 recovered (13.64%).
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

All 11 successfully recovered answers require genuine human review. No human verdict was fabricated. Experiment B is not complete until those labels are imported.

## Evidence diagnostic

The frozen FIA porpoising question `q13d_8fd5589debc7a772c1ed` still lacks the two expected safeguards in its supplied evidence. The retained answer declined to fabricate them. Frozen Experiment A scores and evidence remain unchanged.

## Interpretation boundaries

Mechanical recovery, absolute reliability, automated quality preservation, human safety, and architectural justification are separate decisions. Even a perfect retry result is insufficient for a production/public chatbot GO.

The observed retry is promising for the small timeout and schema-failure strata, which recovered
8/8, but it is not an adequate general reliability mechanism: only 3/22 truncations recovered and
the final policy missed the absolute and composite quality targets. Human review is still required
before judging recovered-answer safety. Experiment C must not begin without scientific review.
