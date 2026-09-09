# Phase 6B paired Pit Wall

## Why pairing was needed

Phase 6 compared independently calibrated PIT and EXTEND marginal intervals. Shared uncertainty
appeared twice, so ordinary dry-race decisions almost always overlapped. The paired evaluator now
generates matched trajectory `i` for every legal PIT compound and a common `EXTEND_5` control,
then calculates PIT minus EXTEND directly.

`EXTEND_5` means staying on the current tyre for the complete validated envelope. It pays no pit
loss within those five laps. This definition matters: an early Phase 6B experiment compared PIT
with Phase 4's delayed-stop actions. At +5 every delayed branch had also stopped, making PIT_NOW
appear better almost everywhere because it received more fresh-tyre laps. The experiment produced
82% strong PIT windows in sampled development states and was rejected as structurally PIT-heavy.
No decision weight or threshold was changed to hide that failure.

The final decision uses the +5 paired endpoint because the +3 endpoint is commonly still dominated
by immediate pit loss. The +1 and +3 distributions remain public so entry, rejoin, and warm-up risk
is visible. Nothing is projected beyond five laps.

## Shared and action-specific uncertainty

Matched actions share the same trajectory quantiles for driver/team latent pace, normal-running
lap variation, field evolution, and position variation. This is a common-random-number design:
the corresponding draw has the same rank in each action's empirical marginal pool.

Pit transit, rejoin, warm-up, fresh-tyre residuals, and post-stop traffic use PIT-specific draws.
Delayed-stop transition draws are EXTEND-specific. Different PIT compounds share pit-event draws,
so a compound cannot win merely because it received a luckier pit-loss sample. Their deterministic
compound and fresh-tyre effects still differ. Existing marginal calibration factors and position
padding remain applied to each action's public marginal distributions.

For each 1-, 3-, and 5-lap horizon the response includes median and mean paired time delta,
50/80/90 intervals, track-position delta, pit-cycle-adjusted position delta, PIT/EXTEND/equivalence
frequencies, expected regret, and 90th-percentile downside. Frequencies are labelled matched-
trajectory simulation frequencies; they are not presented as globally calibrated probabilities.

## Frozen decision gates

The practical time-equivalence bands are 0.948s at +1, 1.791s at +3, and 2.482s at +5. They are
the chronological factual MAEs from Phase 5F's 2024 evaluation after fitting on 2021–2023 races.
The position-equivalence band is one place, matching the Phase 5F median absolute position error at
+3 and +5. A two-place estimated advantage is therefore required before position alone opens a
window.

Five probability-gate pairs were evaluated on 908 sampled driver states from four 2024 races with
three matched seeds. PIT frequency was excluded from selection. The least restrictive candidate
within one percentage point of the best seed unanimity was selected, then strong-signal instability
was minimized. The frozen gates are 0.55 for open and 0.70 for strong time evidence. They achieved
100% repeated-seed window-state unanimity and zero strong-signal instability in that development
sample. See `phase6b-paired-calibration.json`.

## Decision and regret

Time delta is PIT minus EXTEND, so a negative value favors PIT. Expected PIT regret is the mean
amount by which PIT loses after removing the equivalence band; EXTEND regret is defined symmetrically.
The downside fields are their 90th-percentile losses.

A time-based strong PIT window requires a paired advantage beyond the +5 equivalence band and a PIT-
better frequency of at least 0.70. Position can also make the window strong when the estimated
pit-cycle gain exceeds one place and the PIT 80% net-position range clears the stay-out range. A
point advantage beyond the position band opens a window even when physical post-stop position and
five-lap timing are temporarily worse. These time costs remain visible in regret and downside.

Multiple legal PIT compounds are each paired against the same stay-out control. The final compound
is selected using window strength, PIT regret, net position, then paired time. Phase 4 policy remains
secondary evidence and disagreement stays explicit.

## Pit-window and rolling state

The structured states are `PIT_WINDOW_CLOSED`, `PIT_WINDOW_UNCERTAIN`, `PIT_WINDOW_OPEN`, and
`PIT_WINDOW_STRONG`. The object includes best compound, paired advantage, net-position advantage,
traffic, rejoin traffic, uncertainty state, and a deterministic reason. The window does not itself
constitute a recommendation: OPEN returns HOLD, STRONG returns PIT_NOW, and CLOSED returns EXTEND.

Rolling history tracks window age across OPEN-to-STRONG transitions, recommendation age, window
change reason, and decision-input changes. An unsupported raw recommendation flip retains the prior
effective recommendation. A strong PIT signal already clears either the empirical time band or the
position range gate, so it is not suppressed merely for lasting one lap. One-lap calls are reported
separately.

## Frozen 2025 validation

Thresholds were frozen before evaluating the 2025 Bahrain, Spanish, and Italian Grands Prix. The
run covered 146 consecutive eligible laps and 2,864 driver-lap states:

| Result | Count |
|---|---:|
| PIT_WINDOW_OPEN | 643 |
| PIT_WINDOW_STRONG | 499 |
| PIT_NOW | 497 |
| EXTEND | 1,190 |
| HOLD_NO_CLEAR_ADVANTAGE | 901 |
| INSUFFICIENT_DATA | 276 |

Among 79 factual green-flag stops, the preceding five observed laps contained an open window for
47, a strengthening window for 18, and a PIT recommendation for 24. Five were uncertain immediately
before the stop. Historical team stops are behavioral references only; they do not prove PIT was the
optimal counterfactual.

There were 2,785 states without a stop on the next observed lap. PIT appeared in 488, or 17.5%.
Some are persistent windows preceding later stops, so this is not a measurable false-positive rate.
The run contained 51 isolated one-lap PIT calls. Mean PIT recommendation age was 5.28 laps and mean
active-window age was 6.63 laps. Development seed unanimity indicates these were not random-number
oscillations, but state and pit-cycle sensitivity still needs broader validation.

By observed grid group, PIT counts were 14/730 for P1–P5, 149/730 for P6–P10, 195/730 for P11–P15,
and 139/674 for P16+. Backmarkers included 75 lap-deficit and 116 unknown-gap states. No seconds gap
was fabricated. Lapped and incomplete-timing decisions remain coarse when only position ranges are
defensible.

Phase 4 policy disagreement was 74.4%, confirming that the paired pit-cycle decision layer changes
the operating behavior materially. This does not establish counterfactual superiority.

## Performance

The frozen 2025 Bahrain lap-25 benchmark evaluated 20 active drivers, 120 action marginals including
the stay-out controls, and 500 matched trajectories per action in 4.73 seconds. The Phase 6 reference
was 22.04 seconds for 80 independently rolled actions. Shared draws, cached empirical pools, shared
position geometry, and one state/direct-model pass per candidate set provide the reduction.

## API and limits

Existing Pit Wall endpoints remain unchanged. Each active driver now adds `paired_comparison`,
`best_pit_compound`, and `pit_window`; driver detail also includes every PIT-compound comparison.
Timeline entries add pit-window state, age, and change reason.

The system still excludes whole-race simulation, Safety Car/VSC, red flags, rain, failures, live
paid timing, and pre-race prediction. PIT-cycle position estimates remain the dominant source of
strong midfield and backmarker calls. Broader chronological validation is required before treating
the 17.5% non-next-lap-stop PIT frequency or the 51 one-lap calls as production-ready behavior.
