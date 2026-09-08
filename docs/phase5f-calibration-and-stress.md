# Phase 5F calibration and stress test

## Calibration decision

Six interval candidates were compared using early 2024 for calibration and late 2024 for
selection, with PIT and EXTEND weighted equally. Scores were: current 0.2830, pooled conformal
0.3470, action 0.3013, action plus reliability 0.3232, action plus pit phase 0.3013, and
hierarchical shrinkage 0.2786. The small late-2024 hierarchy win did not transfer to 2025.
It reduced final aggregate 80% coverage to 61.1%, 58.7%, and 56.7% and PIT coverage to 64.4%,
51.1%, and 60.0%. The hierarchy is rejected and the Phase 5E empirical intervals remain in
production. The artifact records this rejection and carries only the safety policy metadata.

## PIT calibration

The rejected hierarchy's final PIT 50/80/90 coverage was 52.5/64.4/74.6% at +1,
40.0/51.1/57.8% at +3, and 46.7/60.0/73.3% at +5. Mean widths were 4.131/9.969/16.749s,
5.484/10.511/12.677s, and 8.509/15.567/22.805s respectively. Wider intervals did not solve
the transfer failure.

## Position calibration

The evaluated integer-range padding produced 80% coverage of 88.0%, 84.8%, and 81.2% with
mean widths 2.261, 3.075, and 3.722 positions. The 90% figures were 90.6%, 88.7%, and 92.3%
with widths 2.741, 4.002, and 5.746. Median absolute position error was 0, 1, and 1 position;
mean absolute error was 0.746, 1.094, and 1.472. Because the time hierarchy failed final
generalization, this padding is reported as evidence rather than shipped independently.

## Circuit and season generalization

Five circuit-held-out folds refit residuals without the target circuit. Across legitimate
unseen-circuit PIT cases, sample counts were 72, 51, and 36. MAE was 3.049s, 4.545s, and
5.736s; bias was -0.285s, -0.686s, and -0.793s; P90 was 5.975s, 9.210s, and 12.403s.
The 80% coverage was 66.7%, 72.5%, and 77.8%; 90% coverage was 70.8%, 76.5%, and 77.8%.
This establishes real unseen-circuit PIT evidence, but not adequate calibration.

Chronological aggregate +5 MAE moved from 4.145s for 2023 (fit on 2021-2022), to 2.482s for
2024 (fit on 2021-2023), to 3.244s for 2025. Corresponding 80% coverage was 63.7%, 82.9%, and
56.7%, showing material season-to-season calibration decay.

## Long-horizon stability

Evaluation-only fixed-action rollouts used cumulative lap time relative to a contemporaneous
field reference and stopped EXTEND cases at the next factual pit action. At +8/+10/+15,
sample counts were 265/239/163, MAE was 14.286/16.206/21.897s, bias was
+13.913/+15.688/+20.021s, and P90 was 30.312/33.839/48.057s. The 80% coverage was only
17.4/22.6/24.5%, despite widths increasing from 7.733s to 10.809s to 15.894s. Position MAE
grew from 1.636 to 2.069 to 2.851. Error growth is unstable and dominated by positive drift.

## Reliability and selective simulation

The Phase 5D labels do not form a dependable stochastic hierarchy. At +1, RELIABLE MAE was
1.785s versus 1.053s for USABLE. At +3 it was 2.267s versus 2.040s. RELIABLE becomes better
only at +5 (2.718s versus 3.206s), while samples remain small. Accepting RELIABLE only covers
5.0%, 4.9%, and 6.7% of timed cases. RELIABLE plus USABLE covers 95.6%, 90.9%, and 96.8%,
but its 80% coverage is only 59.3%, 58.8%, and 57.0%. Label thresholds are therefore not
rebranded or cosmetically tuned.

The prototype safety policy is: RELIABLE uses normal rollout; USABLE uses calibrated broader
uncertainty when a calibration is validated; WEAK permits only coarse evolution and no precise
strategy conclusion; OUT_OF_DOMAIN stops precise time projection and retains coarse track
order. This policy does not implement a whole-race simulator.

## Sampling stability and failure modes

Across ten seeds, 100 trajectories produced +5 median spans of 0.726s for PIT and 1.068s for
EXTEND. At 500 they fell to 0.636s and 0.303s; at 1,000 they were 0.385s and 0.308s. PIT 80%
bound spans at 500 were 0.807/0.602s versus 0.490/0.407s at 1,000. Five hundred trajectories
is adequate for routine short-horizon use; 1,000 improves PIT bound stability when reporting
precise interval endpoints.

Among the worst 20 final +5 cases, 19 involved heavy or unknown traffic, three included an
uncertain crossing, one had a wide rejoin range, and one remained an unexplained pace shock.
Cases can have multiple labels. No hard case was removed.

## Practical horizon and decision

The practical uninterrupted horizon remains five laps, with PIT-specific caution already
required at +3/+5. Beyond five laps the kernel must be re-anchored to observed state; +8 and
later autonomous continuation is not defensible. Whole-race readiness remains NO-GO.
