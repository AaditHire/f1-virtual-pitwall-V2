# Causal strategy decision engine

Phase 4 ranks immediate pit and short extension actions for every driver whose causal race
state is `active`. It is a three-lap decision aid, not a whole-race simulator. Every input is
materialized from the publication-time prefix at the requested leader lap.

## Actions and eligibility

The generator creates `EXTEND_1`, `EXTEND_2`, and `EXTEND_3` while enough scheduled laps
remain. When a dry-tyre change may still be required, it leaves one lap for that change.
`PIT_NOW_<COMPOUND>` is available only on a green track and only for another compound in the
same dry or wet family already observed in the race. This is deliberately narrower than the
full sporting regulations because the archive has no driver tyre-set inventory. The API calls
these variants regulation-compatible; it cannot guarantee physical set availability.

Drivers in the pit, stopped, retired, DNS, DSQ, or unknown state receive no actions. The
full-grid response reports them under `excluded`. Active lapped drivers remain in the grid,
but generally return `INSUFFICIENT_DATA` because the current rejoin model requires a
second-valued same-lap gap.

## Common terminal comparison

All actions are compared after a normal stop within a three-lap horizon. For an extension of
`n` laps, `fresh_laps = 3 - n`. The estimated elapsed time is:

```text
pit loss
+ n * (recent clean pace + current blockage)
+ fresh_laps * (recent clean pace - bounded fresh delta + rejoin blockage)
```

`PIT_NOW` uses `n = 0`. `EXTEND_3` places the normal stop at the terminal boundary. Delayed
stops retain today's frozen rejoin geometry; their growing uncertainty is penalized rather
than disguised as a future prediction.

The production tyre forecast is always the Phase 3B zero-slope model. Raw and normalized
diagnostic slopes never enter action generation, timing, scoring, reasons, or confidence.

## Weak signals and uncertainty

The current-race fresh-tyre estimate has LOW confidence and 0.911 seconds held-out MAE. A
one-MAE dead band is applied, followed by a one-MAE cap. A raw estimate must first exceed its
known error, and the surviving contribution can never exceed 0.911 seconds per lap.

Tyre and fresh-tyre uncertainty use their Phase 3B held-out errors (0.506 and 0.911 seconds)
in a root-sum-square calculation over the laps exposed to each source. Unknown blockage adds
one tyre-forecast MAE per missing current/rejoin traffic input. Delay in frozen rejoin geometry
adds the zero-slope MAE by the square root of extension length.

An adjacent-car undercut signal affects `PIT_NOW`; an overcut signal affects extension. Each
is capped to ±0.25 seconds-equivalent. The cap is below the decision margin, so pair evidence
cannot make a decision by itself.

## Driver objective and score

The same objective works across the grid:

```text
track_position_priority = 1 + (field_size - position) / (field_size - 1)
risk_weight = track_position_priority + adjacent_points_loss / 25
action_score = -estimated_time - uncertainty * risk_weight + capped_pair_signal
```

The points term uses the standard top-ten race-points vector as a relevance signal. It does
not create per-position rules. Every position retains a base track-position value, while a
front-running or points-boundary driver carries more downside weight. A backmarker therefore
receives the same action analysis with less protection bias and can still trigger a pit for a
measured traffic opportunity.

All returned scores are seconds-equivalent and higher is better. The response exposes recent
pace, stop loss, current/rejoin traffic costs, raw and bounded fresh delta, empirical error,
uncertainty, objective weights, capped pair evidence, and the exact score.

## Decision rule

The best pit variant is compared with the best extension. A margin below **0.75
seconds-equivalent** returns `HOLD_NO_CLEAR_ADVANTAGE`. This threshold is slightly more
conservative than the 0.708-second median absolute fresh-tyre error observed during Phase 3B
validation and was fixed before the Phase 4 holdout run.

Even above that margin, `PIT_NOW` requires known rejoin traffic and at least 0.75 seconds of
measured three-lap traffic benefit. A fresh-tyre or pair estimate cannot trigger a pit alone.
All decisive outputs remain LOW confidence because fresh pace and stop-offset evidence remain
LOW. Missing recent pace, pit loss, rejoin geometry, a legal compound family, or a comparable
pit/extend pair returns `INSUFFICIENT_DATA`.

## API

```text
GET /api/v1/strategy/{year}/{round}/{lap}/drivers/{driver_id}
GET /api/v1/strategy/{year}/{round}/{lap}/all
GET /api/v1/strategy/{year}/{round}/{lap}/drivers/{driver_id}/actions
```

Each request loads one replay object and builds one causal context. The full-grid route shares
one context and one pit-loss calculation across its driver decisions.

## Historical validation

Run `python scripts/evaluate_strategy.py`. The checked-in report is
`docs/strategy-validation.json`. Four 2023 development races and five later 2024 validation
races match the Phase 3B chronological split. Decisions are frozen every tenth leader lap
from lap 10 where +3 and +5 outcomes exist. This produced 425 development and 476 validation
driver snapshots.

Future records are used only for observed pit entry and +3/+5 position and gap-to-leader
trajectories. The actual team action is reported as a comparison, never a correctness label.
Because history exposes only the chosen action, policy outcome means include only samples
where a policy agreed with the observed immediate action. Those cohorts are selection-biased
and do not identify a counterfactual winner.

On validation, the engine made 33 decisive recommendations from 476 samples (6.9% coverage),
returned HOLD 66.2%, and returned INSUFFICIENT_DATA 26.9%. Its recommendations were 6
`PIT_NOW` and 27 `EXTEND`. Twenty-seven decisive calls aligned with the observed immediate
action horizon. Their mean position change was +0.33 at +3 and +0.63 at +5; two aligned
extension calls had an adverse +5 position outcome under the documented proxy. No aligned pit call had
an adverse proxy outcome, but the aligned pit cohort was too small for a strong claim.

For actual stops, frozen rejoin predictions had 119 usable validation samples. Thirty were
exact ranks with 1.37-position MAE; uncertain ranges missed by 0.67 positions on average.

## Baselines and measured failure

Baseline A always extends three laps. Baseline B pits immediately whenever the action generator
finds a regulation-compatible compound. Baseline C pits at tyre age 17, the rounded median of
159 pre-stop ages across the complete development set. The threshold was derived globally and
was not changed by race or validation result.

The engine does **not** establish superiority over the baselines. On observed-action-aligned
validation cohorts, its +5 mean position change was +0.63 positions, versus +0.33 for always
extend and +0.34 for the age-17 threshold. The cohorts differ sharply (24 engine-aligned rows
versus 422 and 315), so those differences are not causal policy comparisons and do not
establish superiority. The engine's relative-time trajectory was -3.27 seconds, versus -4.23
for always extend and -5.36 for the age threshold. Immediate-pit baseline coverage was high
but aligned outcome was poor (-0.58 positions at +5). The conservative engine trades coverage
for fewer unsupported decisions.

## Grid regions and backmarkers

Validation coverage was 9.2% in P1–P5, 10.8% in P6–P10, 4.6% in P11–P15, and 1.2% in P16+.
The lower coverage reflects missing same-lap gaps and rejoin geometry, not a hard-coded rule.

Two validation decisions at P15 or lower cleared the conservative gate, both at 2024 round 16
lap 30. Colapinto at P15 and Stroll at P17 received `PIT_NOW_MEDIUM` because current traffic
was moderate/heavy and the frozen rejoin was clear. Both teams extended; the drivers gained
two and three positions by +5 respectively. These observations do not reveal the pit
counterfactual, but they provide no evidence that the recommendations were better. The result
is reported as a weakness rather than tuned away.

## Leakage protection

Unit tests compare every full-grid strategy output with a race whose entire post-cutoff suffix
is removed and with one whose future laps, timing, stint, pit, and deletion records are
mutated. The output at Lap N must be byte-for-byte equivalent after model serialization.
Production strategy code receives only `AnalysisContext`, whose constructor deep-copies the
publication-time prefix.

## Limitations

- The three-lap terminal model does not simulate overtakes, future stops, weather, safety cars,
  red flags, fuel effects, or the rest of the race.
- Delayed-stop rejoin uses current gap geometry. Uncertainty grows with delay, but future order
  is not predicted.
- The archive does not expose each driver's remaining physical tyre sets.
- Lapped cars and stale/missing gaps often lack a scoreable rejoin.
- Fresh-tyre transfer and stop-offset evidence remain LOW confidence even after bounding.
- Historical aligned-policy cohorts are observational and unsuitable for causal claims.
- Backmarker evidence is sparse, and the two decisive validation cases did not show an
  observed advantage over the teams' extension paths.
