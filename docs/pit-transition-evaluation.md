# Phase 5B pit-transition evaluation

This evaluation preserves the Phase 4 policy and compares the retained Phase 5 formula
with the Phase 5B transition model on the same factual cases. Development races are 2023
rounds 1, 6, 7 and 14. The untouched holdout is 2024 rounds 1, 4, 8, 10 and 16. Production
simulation receives only the `AnalysisContext` prefix at lap N. Future compounds, pit exits,
gaps and positions are used only to create evaluator labels.

## Diagnosis

The complete per-stop records and grouped slices are in
`counterfactual-validation-phase5b.json`. They contain predicted and actual stop loss,
rejoin position and gaps, first clean post-stop lap, and +1/+3/+5 time and position.

On 105 paired holdout stops, the causal pit-loss estimate has 1.82 s MAE, +0.38 s bias,
0.74 s median absolute error and 4.89 s P90. The first-clean-lap warm-up estimate has
0.91 s MAE and +0.31 s bias on 83 pairs. These components are substantially more accurate
than the complete +5 transition, which has 6.38 s MAE and +5.56 s bias. The residual error
therefore accumulates after exit and in evolving traffic. The worst slices are midfield,
backmarker, moderate/unknown traffic, and long decision-to-entry delays.

The evaluation now anchors rejoin to the first published leader cutoff after pit exit.
This fixes the former +1 label, which could still describe a pre-exit or in-pit state. A
remaining limitation is that the causal decision snapshot may precede factual pit entry by
part of a lap. +5 MAE is 4.41 s for the six timed cases entering within 15 seconds, compared
with 5.99 s at 16–30 seconds, 6.02 s at 31–60 seconds and 17.06 s beyond 60 seconds.

## Transition model

The stop-lap residual remains the only identifiable race-time loss in the normalized
archive. Pit entry and exit timestamps expose pit-lane elapsed time, but that interval
includes stationary time and is not race-time loss. Entry loss, lane transit loss and
stationary time are returned as null with component-specific availability reasons. The
combined observed stop-lap residual is returned separately, so missing pieces are not hidden
inside invented constants.

Post-stop clean laps now form an empirical three-step curve: first full lap, second full lap,
and third-or-later steady value. Compound-specific curves require at least three completed
chronological matching stops; otherwise the model uses the pooled same-old-compound curve
and lowers confidence. The out-lap remains inside the observed stop-lap residual.

Nearby cars within five positions or 15 seconds move once per simulated lap using their
causal relative pace. Distant cars remain outside the interaction model. Crossings inside a
circuit-derived overtake threshold preserve the prior order and widen the position range.
Circuit pit loss and position-change frequency come only from earlier historical races.

Physical track position and `net_race_position_estimate` are separate outputs. Net position
normalizes local competitors to the same observed pit-cycle count using the causal pit-loss
estimate. It is an estimate and retains an explicit range.

## Held-out results

| Action | Horizon | Phase 5 MAE | Phase 5B MAE | Phase 5B bias | Median AE | P90 AE | Position MAE |
|---|---:|---:|---:|---:|---:|---:|---:|
| EXTEND | +1 | 0.52 s | 0.53 s | +0.05 s | 0.31 s | 1.31 s | 0.12 |
| EXTEND | +3 | 1.28 s | 1.27 s | +0.27 s | 0.84 s | 2.84 s | 0.57 |
| EXTEND | +5 | 2.01 s | 2.01 s | +0.50 s | 1.34 s | 4.92 s | 0.88 |
| PIT | +1 | 4.02 s | 3.29 s | +1.98 s | 0.92 s | 13.73 s | 1.29 |
| PIT | +3 | 5.59 s | 4.99 s | +4.40 s | 2.28 s | 16.52 s | 1.73 |
| PIT | +5 | 6.48 s | 6.38 s | +5.56 s | 3.72 s | 17.91 s | 2.10 |

The Phase 5B cohort is larger than the original Phase 5 report because the documented pace
fallbacks and chronological circuit pit-loss prior admit more cases. On the identical
expanded cohort, the EXTEND timing formula is unchanged at +5 and improves by 0.003 s at +3;
the original report's smaller-cohort reference was 1.09 s at +3 and 1.76 s at +5.

## Policy and coverage

The common holdout cohort grows from 348 to 415 snapshots. P16+ grows from 36 to 52. Missing
recent pace and missing causal pit loss no longer exclude held-out P16+ cases; 34 still lack a
non-fabricated same-lap gap.

Always EXTEND_3 averages 28.56 s relative loss at +5. Pit-when-legal averages 23.90 s, a
modeled advantage of 4.66 s, but PIT factual MAE is 6.38 s before adding EXTEND uncertainty.
That advantage is not demonstrated. Phase 4 averages 28.50 s and pits in 1.45% of common
snapshots. Phase 4 remains unchanged.

The simulator compares the expected action gap against both actions' empirical error bands.
Overlapping actions return `HOLD_NO_CLEAR_ADVANTAGE`; no probability distribution is claimed.

## Decision

Phase 5B satisfies the structural gates and improves coverage, +1 PIT timing and dynamic
position behavior. It does not make +5 PIT timing reliable and does not preserve rejoin
accuracy well enough to justify whole-race simulation. Work should remain at short horizon
until a better time-aligned pit-entry state and calibrated multi-lap post-exit traffic model
materially reduce PIT bias and P90 error.
