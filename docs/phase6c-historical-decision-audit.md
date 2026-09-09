# Phase 6C historical decision audit

## Frozen audit design

Phase 6B's equivalence bands, 0.55 OPEN threshold, 0.70 STRONG threshold,
regret calculation, pit-cycle position logic, uncertainty labels, and component-change
hysteresis were frozen before this audit. The audit did not optimize action frequencies or
historical team-stop agreement.

The frozen run evaluated every usable normal-green driver state from lap 6 through five laps
before the finish in 27 dry races. It covered 2021–2025 and seven circuit profiles: Bahrain,
Barcelona, Monaco, Monza, Suzuka, Singapore, and Austin. Detailed probes were exhaustive at
821 factual green stops and deterministically sampled at PIT-run starts and quality states.
Later observations were evaluation labels only.

The exact frozen results are in `phase6c-frozen-audit.json`. The narrowly changed model was
then evaluated without retuning on four races that had not appeared in the frozen audit:
2025 Mexico City, Las Vegas, Qatar, and Abu Dhabi. Those results are in
`phase6c-fix-validation.json`.

## Frozen decision distribution

The broad audit covered 26,281 green driver-lap states.

| Result | Count | Rate |
|---|---:|---:|
| PIT_NOW | 2,115 | 8.05% |
| EXTEND | 8,495 | 32.32% |
| HOLD_NO_CLEAR_ADVANTAGE | 13,841 | 52.67% |
| INSUFFICIENT_DATA | 1,830 | 6.96% |
| PIT_WINDOW_CLOSED | 11,364 | 43.24% |
| PIT_WINDOW_OPEN | 3,538 | 13.46% |
| PIT_WINDOW_STRONG | 2,122 | 8.07% |
| PIT_WINDOW_UNCERTAIN | 9,257 | 35.22% |

The distribution was not uniformly PIT-heavy, but the provenance of strong calls was
pathological: the short-horizon time comparison almost never recovered normal pit loss, while
the less accurate pit-cycle estimate independently promoted windows to STRONG.

## Lead time and persistence

Among 821 factual green stops, OPEN appeared earlier in the stint for 476, STRONG for 303, and
PIT_NOW for 300. When present, median first lead times were 13 laps for OPEN and 11 laps for both
STRONG and PIT_NOW. PIT_NOW appeared one lap before 16 stops, two laps before 19, three to five
laps before 36, and more than five laps before 229. It never appeared before 521 stops.

There were 1,332 window episodes. Median duration was two laps, mean duration 4.25, and p90
duration ten. Of those episodes, 244 survived until a stop and 663 closed before a later stop.
Observed closure components were pit-cycle change (631), rival-set or rival-stop change (588),
position change (500), traffic change (484), uncertainty change (260), and compound change (62).
Another 248 closures had no supported component change and were classified as unsupported model
fluctuation.

The audit found 269 isolated one-lap PIT calls. Every isolated call coincided with at least one
named state change, most commonly pit-cycle position, position, rivals, or traffic. This rules out
random-number instability, but it does not validate the underlying pit-cycle approximation.

## Non-pit and post-pit controls

There were 2,015 PIT states after excluding states followed by a stop on the next observed lap.
Of these, 89 preceded a stop within two laps, 85 on lap three, 167 on laps four or five, 1,167
preceded a later stop more than five laps away, and 507 had no later stop. Median PIT-run duration
was four to six laps in the near groups, six laps in the later-than-five group, and four laps when
there was no later stop. The long lead times and 25.2% no-later-stop share are consistent with an
over-eager pit-cycle gate rather than simple next-lap prediction.

The decisive defect was post-stop behavior. Within laps 0–3 after 821 stops, the frozen model
produced 589 OPEN/STRONG states, including 216 STRONG states and 217 PIT recommendations. It also
produced 122 PIT recommendations on laps 4–5. A normal green stop should not immediately create
another ordinary stop recommendation.

## Compound audit

Destination compound was usable for 800 of 821 factual stops. The recommended best compound
matched the team choice in 396 cases and differed in 404; 21 were insufficient. Team choice is
not an optimality label. All generated recommendations were dry-race compounds observed by the
causal cutoff and differed from the current compound. No tyre-set inventory was inferred.

The evaluator frequently preferred SOFT while meaningful race distance remained outside its
five-lap operating envelope. Such actions can be legal as part of a later stop, but the system
cannot claim stint-to-finish plausibility without whole-race strategy or tyre inventory.

## Pit-cycle validation

At 579 factual-stop checkpoints with a +5 observed position, physical post-stop position had
1.42-place MAE and pit-cycle-adjusted position had 2.21-place MAE. For the 287 checkpoints where
recommended and observed compounds matched, the respective MAEs were 1.43 and 1.98. There were
193 adjustments of at least three places. Some adjustments improved the checkpoint, but the
aggregate estimate did not converge better than physical position.

The untouched fix validation confirmed the defect on 81 more checkpoints: physical MAE was 1.86
and net-position MAE was 2.76; on 28 matched-compound cases they were 1.57 and 2.36. Pit-cycle
position is therefore retained as cautious OPEN/HOLD context but no longer independently produces
STRONG/PIT_NOW.

## Grid regions and missing timing

| Region | States | PIT rate | EXTEND | HOLD | INSUFFICIENT | Mean window duration | Isolated PIT rate |
|---|---:|---:|---:|---:|---:|---:|---:|
| P1–P5 | 6,595 | 1.77% | 47.96% | 49.11% | 1.15% | 2.58 | 0.41% |
| P6–P10 | 6,595 | 9.08% | 38.32% | 52.36% | 0.24% | 4.26 | 1.26% |
| P11–P15 | 6,595 | 13.60% | 26.26% | 59.55% | 0.59% | 4.72 | 1.64% |
| P16+ | 6,496 | 7.73% | 16.52% | 49.60% | 26.15% | 4.74 | 0.79% |

P16+ contained 1,555 lap-deficit and 1,640 unknown-gap states. No seconds gap was fabricated.
Same-lap, lap-deficit, unknown-gap, alternate-cycle, and long-first-stint cases remain in the JSON
artifact. Lap-deficit and unknown-gap examples correctly returned COARSE_ONLY or INSUFFICIENT_DATA.

## Cross-season and circuit stability

Frozen PIT rates by year were 0.60% in 2021, 10.17% in 2022, 5.95% in 2023, 9.97% in 2024, and
12.46% in 2025. The shift is too large to call stable without a source/model drift study.

PIT rates by circuit were Austin 4.07%, Bahrain 9.29%, Barcelona 5.95%, Monaco 4.00%, Monza
16.20%, Singapore 10.86%, and Suzuka 5.47%. Monaco's high HOLD rate of 61.39% is directionally
plausible for a track-position circuit. Monza's 16.20% PIT rate and Singapore's 10.86% are
outliers requiring investigation; no circuit normalization was applied.

## Quality states and rivals

Sampled ACTIONABLE states had complete seconds timing and a 4.59-second median +5 90% interval.
CAUTION had complete timing and 5.05 seconds. COARSE_ONLY had 92.9% seconds timing and a much wider
13.21-second interval. INSUFFICIENT_DATA had only 56.3% seconds timing and no usable paired
interval. ACTIONABLE and CAUTION therefore describe stronger evidence than COARSE_ONLY, although
ACTIONABLE's sample was only 65 states and should not be described as calibrated accuracy.

Among 355 sampled PIT states, relevant rivals included the direct car ahead 347 times, direct car
behind 348 times, a usable relative-pace comparison 355 times, and an alternate pit-cycle rival
118 times. One same-lap driver probe included a lapped rival. Rival selection is mostly local, but
lap-class filtering should be tightened before live use.

## Narrow defect fix and untouched validation

Only two production changes were made:

1. PIT actions are unavailable for three leader laps after an observed normal pit entry. The
   public response is explicitly EXTEND with a CLOSED cooldown reason.
2. Pit-cycle position may open a HOLD window but cannot independently promote a window to STRONG.
   STRONG now requires the unchanged empirical +5 time band and 0.70 simulation-frequency gate.

No equivalence band or frequency threshold changed. On 3,860 green states from the four untouched
late-2025 races, the fix produced 1,687 EXTEND, 1,882 HOLD, 291 INSUFFICIENT, 786 OPEN windows,
1,034 UNCERTAIN windows, and zero STRONG/PIT_NOW. Across 98 green stops, laps 0–3 contained zero
OPEN/STRONG windows and zero PIT calls. This validates the cooldown and confirms that the previous
PIT calls depended on the defective position-only promotion. Thresholds were not lowered to force
PIT output.

## Performance and decision

After one warm-up, three 2025 Bahrain lap-25 full-grid runs had median times of 1.46 seconds at
100 trajectories, 2.48 seconds at 500, and 3.40 seconds at 1,000. Output was stable across all
three repetitions at each count. The detailed 27-race audit took 5,470.5 seconds because it
replayed every lap and re-evaluated factual stops; this is offline evaluation cost, not snapshot
latency.

Phase 7 is **NO-GO**. The post-stop pathology is fixed, but the pit-cycle estimator failed its
historical convergence check, the frozen model exhibits material season/circuit drift, and the
defensible time-only gate produced zero PIT calls on the untouched validation races. The next
development phase should improve and independently validate pit-cycle position or the validated
short-horizon decision signal before current/live data is connected.
