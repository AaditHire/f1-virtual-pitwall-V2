# Phase 5D: PIT reliability envelope

Phase 5D keeps the Phase 5C PIT point model and adds a validated operating envelope. It
does not add whole-race, Safety Car, weather, reliability, or Monte Carlo simulation.

## Dataset and chronology

The dataset contains 751 factual PIT transitions from 23 races:

- 452 transitions from 13 races in 2021–2023 for training.
- 118 transitions from five 2024 races for residual-model selection, applicability
  validation, traffic-gate selection, and conformal calibration.
- 181 transitions from five 2025 races for the frozen final holdout.

The 2025 files were not opened by the selection command. Model type, features, risk
models, gates, and interval quantiles were persisted before the final command ran.
Horizons crossing a non-green race-control period are excluded because Safety Car and
red-flag dynamics are outside the declared model domain. The final valid timed sample
counts are 59/45/30 for +1/+3/+5.

Counts by year, circuit, grid region, traffic level, and compound transition are stored
in `phase5d-selection.json` and `phase5d-final-holdout.json`.

## Residual-model selection

Separate ridge and histogram-gradient-boosting residual models were tested at each
horizon against the Phase 5C point estimate. Features are causal state, evidence, rejoin,
traffic, pit-cycle, and circuit-prior measurements. Future compound, actual stop time,
actual rejoin, future traffic, positions, and lap times are forbidden.

Phase 5C remained best at +1 and +3. At +5, seven-leaf histogram boosting improved 2024
MAE from 5.207 s to 4.992 s and P90 from 17.005 s to 11.616 s. It was rejected because
leave-circuit-out weighted MAE regressed from 6.056 s to 6.252 s. The final point model is
therefore Phase 5C at every horizon.

## Reliability and intervals

The risk model predicts absolute error rather than strategy outcome. A histogram-boosted
risk model was selected for +1; regularized ridge risk models were selected for +3/+5.
2024 risk quantiles define `RELIABLE`, `USABLE`, `WEAK`, and `OUT_OF_DOMAIN`. Labels were
validated by observed error bands. Wide rejoin ranges gate +1 out of domain; wide rejoin
ranges plus heavy/unknown traffic gate +3. No +5 traffic flag passed the pre-frozen
development threshold.

Intervals use the 2024 quantiles of absolute residual divided by predicted risk. They are
symmetric conformal-style intervals and make no Gaussian probability claim.

| Horizon | 2025 MAE | Bias | P90 | 80% coverage | 90% coverage |
| --- | ---: | ---: | ---: | ---: | ---: |
| +1 | 2.882 s | +0.568 s | 7.313 s | 74.6% | 88.1% |
| +3 | 3.616 s | +1.001 s | 8.542 s | 68.9% | 88.9% |
| +5 | 6.247 s | +1.106 s | 22.413 s | 63.3% | 83.3% |

The +1/+3 90% intervals are close to target, although 80% intervals under-cover. The +5
interval misses both targets and does not provide a trustworthy general envelope.

## Selective prediction

| Horizon | Reliable + usable coverage | MAE | Bias | P90 |
| --- | ---: | ---: | ---: | ---: |
| +1 | 74.6% | 2.285 s | -0.280 s | 4.299 s |
| +3 | 42.2% | 2.604 s | +0.499 s | 5.628 s |
| +5 | 73.3% | 7.001 s | +0.715 s | 22.413 s |

The +5 `USABLE` band failed on the holdout. The narrower `RELIABLE` band covered 56.7%
with 3.373 s MAE, 6.565 s P90, and 94.1% coverage for its nominal 90% interval. This is
useful diagnostic evidence but does not validate the full label hierarchy.

## Generalization and coverage

At +5, P11–P15 improved from the Phase 5C 2024 weakness to 3.716 s MAE on ten valid 2025
samples, but P16+ had too few valid +3/+5 time samples to report a group metric. Bahrain
remained weak at +5 (10.769 s MAE), while Catalunya was 4.064 s, Monaco 4.499 s, and Monza
7.487 s. Albert Park was absent from training and contributed 52 transitions, but no
valid same-lap green-window timed PIT sample; unseen-circuit timing generalization remains
unproven.

Lapped states retain `TIME`, `LAP_DEFICIT`, and `UNKNOWN` separately. When seconds are
unavailable, the API now returns an out-of-domain, broad track-order range based on lap
deficit, laps completed, elapsed race time, and current track order. It never fabricates
a seconds gap.

## Policy and EXTEND

On the 415-snapshot 2024 common cohort, the mechanical absolute PIT/EXTEND time separation
exceeds the sum of calibrated uncertainty for 95.4% of all snapshots: 96.2% for P1–P5,
96.0% for P6–P10, 95.4% for P11–P15, and 92.3% for P16+. This largely reflects immediate
pit loss versus staying out over a short horizon and does not establish policy superiority.
Phase 4 decision coverage remains 5.3%, and the 2025 interval under-coverage makes the
mechanical separation rate overconfident outside the validated subset.

Phase 4 remains unchanged. Its policy uses PIT in 1.4% of common snapshots, while always
EXTEND, pit when legal, and the age threshold are retained as comparison policies. No
policy superiority is declared.

EXTEND code was unchanged. On valid green-window 2025 cases its +1/+3/+5 MAE was
0.922/2.030/2.875 s, compared with 0.526/1.272/2.011 s in 2024. This is a material
out-of-season regression even though Phase 5D did not alter the model.

## Decision

The reliability envelope identifies useful +1 and +3 subsets and correctly widens or
refuses some difficult predictions. It does not calibrate +5 reliably, does not prove
unseen-circuit timing performance, has sparse backmarker timing, and cannot support a
policy claim under the final holdout. Whole-race simulation is not justified.
