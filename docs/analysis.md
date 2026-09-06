# Deterministic analysis — Phase 3

The engine answers engineering questions at an observed replay instant. It does not rank strategies, recommend a stop, predict finishing positions, simulate a race, or use ML/LLMs. All seconds are race/archive-relative, and positive pair margins mean the named driver would be ahead **under the stated assumptions**.

## Boundary and architecture

`AnalysisService` loads one normalized session through the existing `ReplayService` cache. Each request builds an `AnalysisContext`: a deep-copied publication-time prefix at the first reported leader-lap completion. Laps require both completion and availability at/before cutoff; late revisions, control, timing, tyre and deletion messages are filtered. Future pit exits become unknown. Every calculator receives that prefix or its `RaceState`, never raw FastF1 objects. CPU calculations run off the API event loop.

Phase 1 services and Phase 2 snapshot semantics are retained. The only normalized-history extension is optional timestamped `LapValidity` records. The archive adapter recognizes explicit car/lap deletion/reinstatement messages; when a message identifies only a lap time, it requires a unique already-observed matching lap. The message's race-clock `Lap` field is **not** assumed to be the offending driver's lap. Unresolvable messages do not fabricate a deletion. Phase 3 applies these records; Phase 2 snapshot outputs are unchanged.

All public subresults have a method, confidence, components and warnings. Unavailable numbers are null. Aggregate responses also include the cutoff, lap, and original driver snapshot. Helpers are split into `pace.py`, `pit_analysis.py`, `traffic.py`, and `pair_analysis.py`.

## Clean laps and pace

- Require a positive finite duration and consecutive observed crossings; drop lap one/start effects.
- Exclude any lap overlapping a known pit visit, including its in/out portions. Open visits remain open until the exit is observed.
- Require known green track status throughout the crossing interval. Yellow, SC, VSC, red and unknown conditions are conservatively excluded.
- Apply the latest explicitly published deletion/reinstatement. Missing deletion evidence is not proof that a lap is valid.
- Exclude impossible durations above the crossing interval +2s broadcast-jitter allowance, or below half that interval. Do not discard a plausible lap merely because it is slow; robust statistics absorb moderate outliers.
- Stint membership requires the same observed stint at both crossings. Late updates to an old stint cannot revert the current tyre.

`get_recent_pace`: median of up to three current-stint clean laps among the driver's last five completed laps. It never silently reuses old-stint pace. `get_stint_pace`: median of all clean laps in the current stint. Both expose the actual selected laps and durations.

## Tyres

For clean laps `(n_i, t_i)` in the current stint:

`slope = median((t_j - t_i) / (n_j - n_i)), j > i`

`intercept = median(t_i - slope * n_i)`

Residual MAD is the median absolute residual. Require at least five clean points spanning four laps. Recent trend fits the last five; recent delta is last-three median minus first-three median. The reported tyre age is the source observation, not an extrapolated age.

This is a **net pace trend**, not identified physical tyre degradation. Fuel mass/burn, weather and clean-air pace are unmeasured. A field slope would also remove other drivers' tyre degradation, so no assumed fuel constant or field correction is subtracted. Negative slopes remain negative. Traffic and driver management can still dominate the estimate.

Competitive life is optional and illustrative: total tyre age when recent pace loss reaches +2s versus early-stint pace. Remaining laps are `(2 - recent_delta) / slope`. Report only with MEDIUM evidence, positive lower-quartile pairwise slope, a recent age observation (at most one crossing old), and remaining distance between zero and the observed fit span. Otherwise return null. This threshold is not tyre failure or a recommended stint length.

## Pit loss

Only completed, post-start stops can supply evidence. Identify the consecutive lap window spanning pit entry/exit; require at least two clean laps on each side, within five laps of the window (up to three each). All evidence must already be published. Require green conditions through the stop and post-stop reference window.

`baseline_pace = (median(pre_clean) + median(post_clean)) / 2`

`stop_loss = sum(affected_lap_times) - affected_lap_count * baseline_pace`

Accept positive residuals below 0.6 reference laps and lane elapsed time below 0.75 reference laps. With at least four samples, discard residual outliers farther than `max(5s, 3*MAD)` from the session median. These broad rules reject obvious repairs/penalties; they cannot identify every abnormal stop. Use the median remaining loss as the normal-stop estimate. There is no circuit-specific constant or future-session fallback.

Pit-lane elapsed time is exposed separately. It contains time spent stationary and time moving through the lane; it is **not** race-time loss. Transit and stationary components remain null because this archive does not separate them. The residual also includes tyre/warm-up/traffic effects, and is therefore an approximate proxy, not independent stop ground truth.

## Rejoin, traffic and pit window

For an active same-lap car under green conditions, insert its current leader gap plus pit loss into the other active cars' known second-valued gaps. Remove the original car before insertion. Return approximate position, nearest known cars, intervals and drivers within ±5s.

Lapped cars, pitting cars, unknown status and missing/stale gaps are not assigned invented second-valued gaps. If any such car could affect the ordering, suppress the point position and return the conservative rank range `[known_cars_ahead + 1, known_cars_ahead + unknown_cars + 1]`. Neighbours then refer only to the known subset. Unknown cars prevent a clear-air claim. A lapped subject, non-green track or unavailable loss yields insufficient evidence. Race-order neighbours cannot locate all lapped cars physically on track.

Traffic thresholds are fixed engineering heuristics, not circuit-tuned probabilities. Apply in priority order:

| Label | Rule |
|---|---|
| HEAVY_TRAFFIC | Ahead within 1s, or at least 3 known cars within ±5s |
| MODERATE_TRAFFIC | Ahead within 2s, or at least 2 known cars within ±5s |
| UNKNOWN | Incomplete geometry without sufficient positive congestion evidence |
| LIGHT_TRAFFIC | Ahead within 5s or behind within 2s |
| CLEAR_AIR | Complete measured geometry satisfying none of the above |

Current traffic uses reported adjacent intervals (density counts the immediate neighbours only). Relative pace is `neighbour_median - driver_median`: positive means the neighbour is slower. Flag likely slower-car blockage only with a car ahead within 2s and an observed advantage greater than 0.3s/lap. All inputs remain visible. Rejoin density covers every car with a known gap in ±5s.

`find_pit_window` wraps the **current** rejoin, density, traffic risk and clear-air opportunity. Projection distance is zero laps. It makes no claim about next lap or an opening later in the race.

## Undercut and overcut

Both require distinct active same-lap cars, with the named driver behind the target, usable current pace and complete rejoin geometry. Let `g` be their current gap, `p_d` driver pace and `p_t` target pace.

Fresh-used evidence is the median pre/post clean-lap improvement at already-observed qualified stops whose old compound matches the relevant car's current compound. Prefer that driver's most recent matching stop; otherwise use matching peers. Expose drivers, compounds, ages and evidence timestamps. The new stint is read at its first clean post-stop crossing to tolerate delayed compound packets. This transferable difference also contains fuel, compound and traffic changes; it is not a controlled tyre experiment. Warm-up remains unknown.

For a one-lap no-overtake approximation with assumed 1s following headway, let `a = max(0, ahead_pace - own_pace)`. Traffic penalty is `min(a, max(0, a + 1 - gap_ahead))`. Missing neighbour pace does not become zero. This is a conditional obstruction estimate, not an overtaking or DRS model.

- Undercut fresh pace `f_d = p_d - observed_fresh_gain`; margin `p_t - f_d - rejoin_penalty - g`.
- Overcut opponent fresh pace `f_t = p_t - opponent_fresh_gain`; margin `f_t - p_d + opponent_rejoin_penalty - driver_current_penalty - g`.

Both assume equal normal-stop costs separated by one racing lap, so pit-loss difference cancels to zero; each cost still determines rejoin traffic. These are **conditional clean-lap margins**, not measured out-lap margins. Unequal stops, extra warm-up, overtakes or neutralisation can reverse them. YES above +0.5s, NO below −0.5s, MARGINAL in between, UNKNOWN when required evidence is missing. Pair confidence is capped at LOW. No recommendation is produced.

## Confidence

| Result | Evidence rule |
|---|---|
| Pace | MEDIUM for ≥3 selected clean laps; LOW for 1–2; otherwise INSUFFICIENT |
| Tyres | INSUFFICIENT below 5 points/4-lap span; MEDIUM at ≥8 points/7-lap span, residual MAD ≤0.7s, latest clean lap ≤2 laps old; otherwise LOW |
| Pit loss | MEDIUM with ≥3 qualified stops and residual MAD ≤3s; LOW with fewer/noisier observations; otherwise INSUFFICIENT |
| Rejoin | MEDIUM with complete gaps and MEDIUM loss; LOW for partial geometry/LOW loss; INSUFFICIENT without prerequisite evidence |
| Traffic | MEDIUM with complete intervals and available pace; LOW with some intervals; otherwise INSUFFICIENT |
| Pair | LOW when the conditional calculation is possible; otherwise INSUFFICIENT |

HIGH is part of the shared vocabulary but no predictive model here claims it. Thresholds are transparent heuristics, not calibrated coverage percentages. Confidence does not guarantee a correct traffic label or forecast.

## Historical evaluation (2026-09-06)

Reproduce with `python scripts/evaluate_analysis.py --output docs/analysis-validation.json`. `--cached` optionally reuses normalized evaluation archives in `.cache/`; serialized timing deltas retain unset-field semantics. The runtime never imports evaluation code. The [recorded report](analysis-validation.json) contains sample-level inputs, outcomes and errors, including failed/insufficient pair cases.

At laps 10/20/30/40, forecast the median of the next three clean same-stint laps within five completed laps of cutoff. Anchor at the current recent median, advance by fitted slope times lap distance, and compare to the zero-slope recent-median baseline. These 128 chronological holdouts use no parameter fitting. They overlap within sessions and are not a statistically independent benchmark.

| Race | Tyre holdouts | Trend MAE (s) | Zero-slope MAE (s) | Exact rejoin cases | Rejoin MAE (positions) | Fixed +5-position baseline MAE |
|---|---:|---:|---:|---:|---:|---:|
| Bahrain 2023 | 33 | 0.406 | 0.336 | 12 | 0.917 | 1.750 |
| Bahrain 2024 | 41 | 0.351 | 0.266 | 0 | unavailable | unavailable |
| Monza 2023 | 54 | 0.354 | 0.313 | 15 | 0.800 | 1.533 |

**The tyre trend is worse than the zero-slope baseline in all three races.** It remains a descriptive diagnostic, not a validated forecasting improvement. Exact rejoin improves on the deliberately simple fixed-five-position baseline on the two available subsets; this says nothing about missing-gap cases or general circuit performance.

Rejoin is evaluated at the last leader-lap cutoff before each actual stop, against the first cutoff after exit. This can be almost a lap before entry/after exit; other cars can stop or change order in between. Bahrain 2024 has no exact-rank cases because every usable pre-stop snapshot contains incomplete/lapped/pitting-car geometry. Including rank ranges, there are 34/24/21 cases, with mean distance outside the predicted range 1.088/0.250/0.667 positions respectively. Range width and each timing delay are recorded; low range error is not precise point accuracy. Example exact cases: Bahrain 2023 LEC lap 13 P7 → actual P5; Monza 2023 ALB lap 15 P13 → actual P13.

| Race | Future stop residuals | Causal pit-loss MAE (s) | Full-session median MAE (s) |
|---|---:|---:|---:|
| Bahrain 2023 | 24 | 0.992 | 0.783 |
| Bahrain 2024 | 25 | 0.941 | 0.924 |
| Monza 2023 | 20 | 0.923 | 0.841 |

The full-session median is a **hindsight reference**, unavailable to runtime and containing the evaluated samples. Labels are residual proxies using the same methodology, not independent stationary/transit measurements. No superiority claim is supported.

Pair evaluation selects adjacent cars with actual stops one leader lap apart. There are 36 cases: four calculable conditional margins, only three with a subsequent usable time gap. Most are insufficient because fresh-stop evidence or complete geometry does not yet exist. Examples:

| Moment | Analysis | Margin (s) | Later observed margin (s) | Observation |
|---|---|---:|---:|---|
| Bahrain 2023 lap 13, ALO vs SAI | Overcut NO | −9.016 | −8.716 | ALO remained behind |
| Bahrain 2023 lap 25, GAS vs TSU | Undercut YES | +2.309 | −0.069 | **False positive**; GAS remained behind |
| Monza 2023 lap 19, LEC vs SAI | Overcut NO | −1.266 | −0.489 | LEC remained behind |

The fourth numeric case is Bahrain 2023 lap 26 HUL vs DEV (undercut NO, −1.566s); the subsequent time gap is unavailable, but HUL remained behind. Three timed cases cannot establish reliability. Their error versus the no-gain margin baseline is mixed: Bahrain 2023 1.339s versus 1.760s (n=2), Monza 0.777s versus 0.133s (n=1). No general improvement is claimed.

Observed traffic examples include clear rejoin for LAW at Monza lap 30 and congested rejoin for LEC at Monza lap 20. They validate documented geometry rules, not overtaking outcomes.

## Acceptance and checks

- A/B: multiple real driver/stint estimates; young stints return insufficient, not fabricated slopes.
- C/D: all pit evidence timestamps precede cutoff; real pre-stop point/range errors reported above.
- E/F/G: observed clear/congested geometry plus real structured undercut/overcut cases, including unavailable and incorrect outcomes.
- H: unit removal/mutation of future laps, late revisions, positions, tyres, control, deletion messages and pit exits leaves every aggregate and pair output unchanged. A real Bahrain 2024 raw-stream prefix reproduces all Phase 3 driver and pair outputs.
- I: all 61 original Phase 1/2 offline tests pass, plus 21 Phase 3 unit cases (82 total).
- J: a real temporary Uvicorn socket returns all five Phase 3 endpoints, and focused tyre/traffic responses match the aggregate. Unknown drivers/laps and invalid input are covered offline.

Final checks: **82 offline tests passed; all 5 Phase 3 historical integration tests passed; Ruff lint/format and pip dependency checks passed; real Phase 3 Uvicorn smoke passed.**

Recorded network regression: 14 original Phase 1/2 tests passed; two existing Phase 1 tests (OpenF1 sprint/grid and homepage) failed with HTTP 401 because OpenF1 restricts unauthenticated access during the live session. Their tests and service behavior were not weakened. The existing FastF1 processed-source comparison tests emit 109 upstream NumPy timedelta deprecation warnings; the new runtime does not consume that processed table.

## API and sample

Routes under `/api/v1/analysis/{year}/{round}/{lap}`: `/drivers/{driver_id}`, `/drivers/{driver_id}/tyres`, `/drivers/{driver_id}/traffic`, `/undercut?attacker=X&target=Y`, `/overcut?driver=X&target=Y`. Use replay IDs, not Jolpica identifiers. Unknown race/driver/lap returns 404; invalid path/query shape 422; unavailable provider 503. No strategy endpoint exists.

Run `python scripts/analysis_sample.py 2023 14 19 LEC SAI`:

```text
LEC | Monza 2023 | Lap 19
MEDIUM, reported age 18, 17 clean stint laps
Net tyre trend: +0.095 s/lap (MEDIUM)
Recent pace: 87.498 s
Normal pit loss: 25.338 s (MEDIUM, 7 observed stops)
Projected rejoin: P10, HEAVY_TRAFFIC
Current traffic: HEAVY_TRAFFIC
Undercut vs SAI: +0.036 s, MARGINAL (LOW)
Overcut vs SAI: −1.266 s, NO (LOW)
```

The small undercut margin is not actionable evidence: warm-up and stop variation are unmeasured. The overcut's negative sign agrees with the later order in this example, but its magnitude is approximate. No Phase 4 work is included.
