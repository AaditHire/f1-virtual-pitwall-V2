# Phase 13D-D — Experiment C Results

STATUS: PAUSED — HUMAN REVIEW REQUIRED; PRODUCTION/PUBLIC CHATBOT NO-GO

Experiment C is development/diagnostic evidence. The reused benchmark is not an untouched prospective holdout and cannot establish production readiness.

## Truncation recovery

- 1280 fallback: 22/22 (100.00%); unrecovered 0/22.
- Historical 640 retry: 3/22 (13.64%); descriptive difference 86.36% percentage points.
- The executions occurred at different times and were not concurrent randomized arms.

## Candidate policy

- Final unrecovered: 0/100; <=2/100 target met: True.
- Citation validity 225/225; completeness 100/100; support 100/100.
- Required facts 143/170; unsupported/uncited sentence proxy 91/372.
- Refusals 5/5; false refusals 1/125; multi-document 37/37.
- Conflicts 2/2; prompt injection 3/3; route 130/130.
- STRUCTURED_ONLY 30/30; MIXED exact facts 25/25.

## Defect-aware sensitivity

The authoritative metrics above remain frozen. Excluding only the known FIA evidence defect yields required facts 143/168 and false refusals 0/125.

## Usage

- 1280 calls: 22; input/output/total tokens 46,694/17,343/64,037.
- Mean/median output tokens 788.3/814.5; median/P90 latency 9204.4/11108.5 ms.
- The historical 640-token truncation retry used mean/median output 613.6/640.0 tokens and
  median/P90 latency 7108.1/7819.9 ms. The 1280 fallback therefore increased both generated tokens
  and latency for the truncation-only path.
- Provider cost/balance was unavailable; recorded external cash spend is $0.00.

## Human safety

All 22 successfully parsed 1280 fallback answers require genuine human review. No semantic labels were fabricated.

## Interpretation boundary

A successful diagnostic result could justify freezing a candidate policy for a new untouched prospective holdout. It does not authorize production deployment. Experiment C is paused for human review; no later experiment has started.
