# Phase 6E strategic stint value model

## Architecture

The model evaluates a small set of PIT-now and delayed-stop actions at one common terminal leader
lap, normally 25 laps ahead or the remaining race distance when shorter. Every action pays its
owed stop before that terminal point. An additional stop is charged when the median observed
competitive stint length cannot reach the terminal.

The first five laps come from the frozen Phase 5E stochastic tactical kernel. After lap five the
model uses one coarse event-level stint segment; it does not recursively run the tactical kernel.
Terminal values are relative-time costs, where lower is better. Terminal physical and net
position ranges remain unavailable because the validated position model ends at the tactical
handoff and the pit-cycle estimate was rejected in Phase 6C.

The structured `StrategicPitComparison` returns PIT-now, two-lap delay, five-lap delay, and an
empirical stint-window delay where supported. It includes tactical +5 values, common-horizon
cumulative values at +5/+10/+15/+20, uncertainty ranges, owed stops, approximate break-even,
traffic state, evidence provenance, and a best strategic action. It does not modify the public
Pit Wall recommendation.

## Historical stint priors

The frozen artifact contains 723 robust stint segments from 17 cached 2021–2023 races. A segment's
pace is its median clean lap time relative to the contemporaneous leave-one-driver-out field
median. Completed-stint length is treated as an observed strategy prior, not tyre failure life.
Raw degradation slopes are unused.

| Compound | Pace segments | Median relative pace | Pace IQR | Completed lengths | Median length | Length IQR |
|---|---:|---:|---:|---:|---:|---:|
| SOFT | 205 | -0.154 s/lap | -0.927 to +0.493 | 148 | 18 laps | 15–24 |
| MEDIUM | 281 | +0.006 s/lap | -0.664 to +0.480 | 220 | 20 laps | 16–24 |
| HARD | 237 | +0.075 s/lap | -0.501 to +0.446 | 137 | 22 laps | 19–26 |

At a decision checkpoint, compound evidence follows this causal hierarchy: same-race observations,
earlier same-circuit races, broader 2022–2023 regulation-era evidence, then insufficient. The
compound effect is added to the driver's current normalized pace so historical car performance is
not copied directly. Historical samples are filtered to dates strictly before the evaluated event.

## Chronological validation

The untouched holdout used 2024 Bahrain, Barcelona, Monza, and Singapore. Six factual green stops
per race were sampled across grid regions at N-5, N-3, and N-1, yielding 72 checkpoints from 24
stops. Future +10/+15/+20 gaps, positions, recovery laps, and split-stop outcomes were labels only.
No parameter was changed after the holdout was read.

The model returned a best action for 65 of 72 checkpoints. Seven early Singapore checkpoints had
no causal current-race or earlier-circuit pit-loss estimate and remained insufficient. Evidence
sources across returned compound priors were same-race 53 times, earlier same-circuit 25, and
regulation-era 12.

Best delay distribution was PIT now 42, delay five 17, delay seven 2, delay eight 3, and delay ten
1. At N-1 it chose PIT now in 16 of 22 usable checkpoints; at N-3, 15 of 22; at N-5, 11 of 21.
The median terminal spread between actions was 21.78 seconds. PIT now beat the best delayed action
42 times and lost 23 times, with no comparison equivalent within two seconds. This resolves the
automatic short-horizon pit-loss disadvantage, but the lack of equivalence is too decisive for the
observed uncertainty.

Only 12 checkpoints had both measurable actual gap recovery and a predicted break-even. Median
absolute break-even error was 2.5 laps and mean error was 3.58 laps. This sample is too small for
calibration claims.

## Forecast baselines

Timing forecast MAE against later observed gap change was:

| Model | +10 | +15 | +20 |
|---|---:|---:|---:|
| Generic fixed 1.1 s/lap fresh advantage | 10.96 | 7.45 | 14.65 |
| Fixed five-lap delay | 18.40 | 19.75 | 14.42 |
| Historical compound/circuit median | 15.98 | 17.13 | 14.01 |
| Strategic hybrid | 14.84 | 15.85 | 14.64 |

The hybrid improves on the two historical/delay baselines at +10 and +15, but it is materially
worse than the generic fresh-advantage baseline. At +20 it does not beat the strongest baseline.

Two selected same-compound, same-grid-region split-stop comparisons were measurable. The model
predicted an earlier-stop advantage in both. Hamilton versus Russell at Singapore evolved toward
the later stopper by 5.81 seconds; Alonso versus Perez was equivalent within 0.66 seconds. These
labels remain confounded by driver/car pace and are not counterfactual ground truth, but neither
supports the predicted direction.

## Decision

The architecture is useful: it has a clean tactical-to-strategic handoff, equal terminal horizons,
causal compound priors, explicit uncertainty, and complete owed-stop accounting. The empirical
stint value is not validated well enough to guide the Pit Wall. Its forecasts do not beat a simple
generic baseline, its choices are overly separated, split-stop direction was unsupported, and
Singapore lacks early pit-loss evidence.

The Phase 6E failure rule therefore applies. No weights or priors were tuned to the holdout, and
the strategic result remains separate from public recommendations. Hybrid Pit Wall integration is
NO-GO until stronger chronological compound/stint evidence and a validated long-horizon relative
time target are available.
