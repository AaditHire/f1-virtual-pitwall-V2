# Short-horizon counterfactual evaluation

> Phase 5B supersedes the PIT-transition conclusions here. See
> `pit-transition-evaluation.md` and `counterfactual-validation-phase5b.json`.

## Boundary and state

Phase 5 separates policy selection from outcome estimation. The production simulator receives
only an `AnalysisContext` publication-time prefix, a driver, and an action. Phase 4 score,
recommendation, and decision margin are explicitly absent from every transition component.

The minimal state contains driver identity, position, field size, same-lap gap to the leader,
compound, tyre age, recent pace relative to the current leader, pit state, traffic, completed
laps, active status, and missing-input details. It does not copy the full replay state.

## Transition model

The model returns independent outcomes at +3 and +5 laps. `EXTEND_N` stays out for N laps and
then makes a normal stop only when N is inside the requested horizon. `PIT_NOW_<COMPOUND>`
applies the causal session pit loss immediately. Pre-stop laps use recent leader-relative pace
and current blockage; post-stop laps add a reliability-shrunk fresh-tyre offset and frozen
rejoin blockage. The tyre forecast remains zero-slope.

Fresh-tyre evidence is shrunk by `1 - 0.911 / 1.399 = 0.349`, based on Phase 3B held-out MAE
relative to its zero-delta baseline. This is independent of Phase 4's score cap.

Other cars retain their current gaps. The driver's projected leader gap is inserted into the
known same-lap order. Time uncertainty creates a position range, unknown gaps widen its lower
confidence bound, and any stop adds the 1.013-position rejoin-error floor. Lapped drivers or
drivers without a second-valued leader gap return insufficient outcomes.

## Chronological calibration

Four 2023 races form the development split; five 2024 races are untouched holdouts. Median
timing-error bias and mean absolute error are learned globally on 2023 only:

| Action | Horizon | Bias correction | Development MAE |
|---|---:|---:|---:|
| EXTEND | +3 | 0.000s | 2.104s |
| EXTEND | +5 | 0.000s | 2.945s |
| PIT_NOW | +3 | -1.812s | 3.586s |
| PIT_NOW | +5 | -3.583s | 4.888s |

These constants are frozen in production. Earlier historical replays should treat them as a
global fallback because their calibration chronology is not prior to those 2023 events.

## Factual validation

Stay-out cases require the driver to remain out for the entire five-lap window. Pit cases use
the last causal leader-lap snapshot before an observed stop and the factual compound as the
evaluated action label. Future gaps, positions, compounds, stops, and track states exist only
in the evaluation script. Timing labels are omitted if the current leader pits or a
neutralization breaks the gap reference.

Held-out results:

| Action | Horizon | Time MAE | Time bias | Position MAE | Range miss |
|---|---:|---:|---:|---:|---:|
| EXTEND | +3 | 1.09s | +0.09s | 1.16 | 0.79 |
| EXTEND | +5 | 1.76s | +0.19s | 1.78 | 1.31 |
| PIT_NOW | +3 | 4.87s | +3.77s | 1.53 | 0.19 |
| PIT_NOW | +5 | 5.76s | +3.86s | 1.90 | 0.38 |

The PIT timing model generalizes poorly. Its ranges cover position better than its point
estimate, but that breadth limits decisions. Counterfactual intervals that overlap return
`HOLD_NO_CLEAR_ADVANTAGE`; no probabilities are produced.

## Common-snapshot policy comparison

All four policies are evaluated on the same 348 usable held-out snapshots. HOLD operates as
`EXTEND_3` for this decision window. The pit baseline uses the hardest observed
regulation-compatible alternative. The age policy uses the globally derived age-17 threshold.

| Policy | Mean expected P change +5 | Mean expected time +5 | Downside | Pit rate |
|---|---:|---:|---:|---:|
| Phase 4 | -4.52 | +27.38s | 92.0% | 1.7% |
| Always EXTEND_3 | -4.58 | +27.61s | 92.2% | 0.0% |
| Pit when legal | -3.90 | +22.46s | 90.2% | 100.0% |
| Age 17 | -4.29 | +25.61s | 91.4% | 37.6% |

These are model estimates, not observed counterfactual truth. The simulator ranks pit-heavy
policies better, while factual PIT errors are 4.87–5.76 seconds and position estimates retain
positive bias. It therefore cannot validate that ranking or show Phase 4 beats the baselines.
Phase 4 parameters remain unchanged and the engine remains experimental.

## Backmarkers

Thirty-six P16+ snapshots survive the common-cohort gate. All policies have the same modeled
mean position change (-1.19), showing that the current order model cannot distinguish their
position consequences for backmarkers. Phase 4 holds on 97.2% and pits on 2.8% of these rows.

The leading P16+ exclusions are missing same-lap gap (34 candidate rows), missing recent
relative pace (14), and unavailable causal pit loss (11). Each count is repeated per policy in
the JSON report because every policy is rejected together at that snapshot.

## API

```text
POST /api/v1/simulation/short-horizon
GET /api/v1/strategy/{year}/{round}/{lap}/drivers/{driver_id}/counterfactuals
```

The POST body contains `year`, `round`, `lap`, `driver_id`, and one legal action. Each action
returns expected relative-time change, uncertainty, expected position or range, position
change, confidence, components, and warnings at both horizons.

## Reproduction and limitations

Run `python scripts/evaluate_counterfactuals.py`. Full metrics and circuit/grid breakdowns are
in `docs/counterfactual-validation.json`.

- No whole-race path, probabilities, overtaking model, weather, safety car, reliability, fuel,
  or future pit-cycle evolution is simulated.
- Other-car gaps remain static over the short horizon.
- Delayed-stop rejoin traffic uses current geometry.
- The PIT timing model is too inaccurate for confident PIT-versus-EXTEND validation.
- Position estimates are biased toward predicting larger position losses.
- Common-cohort filtering improves fairness but reduces backmarker coverage.
