# Phase 5C: post-pit transition model

Phase 5C remains a 1/3/5-lap deterministic transition model. It does not add a
whole-race, safety-car, weather, reliability, or Monte Carlo simulator.

## Chronology and diagnosis

The evaluator reconstructs factual gaps directly from timing records at rejoin and
post-stop laps 1–5. Nine 2021–2022 races provide priors, four 2023 races provide model
development and chronological regularization selection, and five 2024 races form the
untouched holdout.

The Phase 5B pessimistic bias accumulated throughout the transition: +1.955 s at rejoin,
+2.623 s after one post-stop lap, +4.316 s after two, +4.633 s after three, +5.141 s after
four, and +5.787 s after five. The largest mean increments were rejoin (+1.955 s), the
first post-stop lap (+0.950 s), and the second (+1.017 s). This implicates both the stop
transition and repeated pace extrapolation rather than one isolated warm-up term.

## Candidate components

The interpretable pace candidate uses recent normalized clean pace, current-stint pace,
driver-first team shrinkage, field-relative fallback, compound, and traffic state. It
returns its expected pace, uncertainty, lap evidence, and teammate evidence. It was not
adopted because its held-out +3/+5 MAE was worse than Phase 5B.

Historical traffic measurement found median losses of 0.359 s inside 1 s, 0.135 s at
1–2 s, and 0.029 s at 2–5 s. The corresponding MAD values were 0.669 s, 0.636 s, and
0.717 s. Adding supported empirical bins still worsened held-out transition error, so the
measurements remain diagnostic evidence and uncertainty rather than a point correction.

A smoothed empirical overtaking-bin model was fit on 113 development examples and tested
on 107 held-out examples. Its raw accuracy rose from 54.2% to 69.2% and Brier score fell
from 0.458 to 0.201, but it predicted an overtake in 106 of 107 cases: specificity fell
from 85.3% to 2.9% and balanced accuracy fell from 62.5% to 51.5%. It was rejected, so
the existing conservative uncertain-crossing behavior remains.

## Selected model

After the interpretable candidates reached a ceiling, a ridge correction was evaluated.
Its six causal features are Phase 5B raw delta, recent relative pace times horizon,
causal pit-loss estimate, final simulated-lap warm-up gain, final simulated-lap traffic
loss, and current position. Alpha was selected using later 2023 development races and
the model was refit on all 2023 cases before one 2024 evaluation. No future pit-entry
interval, future tyre state, actual pit loss, or future result is a feature.

The correction is applied only to PIT_NOW. EXTEND retains the Phase 5B model. Parameters
are fixed and implemented without a runtime machine-learning dependency.

| PIT horizon | Phase 5B MAE | Phase 5C MAE | Phase 5C median AE | Phase 5C bias | Phase 5C P90 AE | Phase 5C position MAE |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| +1 | 3.224 | 2.736 | 1.272 | +0.722 | 5.948 | 0.686 |
| +3 | 4.934 | 3.732 | 1.897 | +1.629 | 8.278 | 1.105 |
| +5 | 6.789 | 5.207 | 2.835 | +1.924 | 17.005 | 1.427 |

EXTEND remains unchanged at 0.526/1.272/2.011 s MAE for +1/+3/+5, with biases
+0.051/+0.271/+0.498 s.

At +5 the selected model improves over Phase 5B in every circuit and grid region with
enough observations in the ridge ablation. Absolute +5 MAE remains poor at Suzuka
(8.056 s), P11–P15 (8.032 s), and P16+ (6.954 s, four timed samples).

## Field, pit cycle, and backmarkers

Nearby cars advance each simulated lap and expose relative pace, empirical pace
uncertainty, compound, tyre age, traffic state, completed stops, and remaining stop
obligation. Outcomes retain both physical track position and terminal net
pit-cycle-adjusted position with ranges. No exact opponent pit lap is predicted.

The public state now distinguishes `TIME`, `LAP_DEFICIT`, and `UNKNOWN` gaps and exposes
`laps_behind`. Direct timing reconstruction increases legitimate factual timing coverage,
but 34 held-out P16+ policy snapshots still lack a defensible same-lap seconds gap and
remain excluded. No seconds value is invented for a lapped car.

## Remaining limit

The worst 20 selected +5 cases contain 18 traffic interactions, 12 position/overtaking
failures, nine multi-lap pace failures, three backmarker/lapped interactions, and two
unusual pit losses; causes overlap. None was removed. The +5 P90 remains 17.005 s, Monaco
has only one timed +5 case, and only 5.3% of held-out Phase 4 policy snapshots produce a
decision whose PIT/EXTEND separation clears the uncertainty rule. Whole-race simulation
is therefore not justified.

Machine-readable evidence is in `pit-transition-diagnosis-phase5c.json`,
`traffic-effects-phase5c.json`, `pit-transition-ablation-phase5c.json`,
`pit-transition-ridge-phase5c.json`, and `counterfactual-validation-phase5c.json`.
