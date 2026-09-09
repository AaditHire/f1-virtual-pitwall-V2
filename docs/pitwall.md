# Rolling Virtual Pit Wall

The Phase 6 Pit Wall turns the existing causal analysis, policy, and stochastic transition
services into a full-grid race-engineering view. It is a rolling decision system. It is not an
autonomous whole-race simulator.

## Re-anchored architecture

For observed lap `N`, `PitWallService` loads the normalized historical race once and constructs a
new `AnalysisContext`. That context applies the archived publication-time cutoff for lap `N` and
materializes a causal `RaceState`. The orchestrator then coordinates the existing Phase 3
analysis, Phase 4 candidate policy, and Phase 5E transition kernel.

When lap `N + 1` is evaluated, the service constructs another context from the archive. No
simulated state, sampled trajectory, expected position, or inferred tyre state from lap `N`
crosses this boundary. The new observed state replaces it. This same boundary allows a future
normalized live-timing provider to supply `RaceState` without creating another strategy engine.

`run_pitwall_replay` repeats this process for consecutive observed laps and returns per-driver
history. The replay result records every lap used as a re-anchor.

## Five-lap operating envelope

Each candidate action contains stochastic consequences only at 1, 3, and 5 laps. The API marks
the result `short_horizon_only` and reports a maximum horizon of five laps. It does not expose an
8-, 10-, 15-lap, or whole-race forecast. The last five race laps are excluded from the historical
demonstrations so each evaluated state has the complete validated operating envelope remaining.

Trajectory counts are deliberately bounded to 100, 500, or 1,000 per action. The compact grid
response reports how many actions were evaluated but omits their distributions. Driver detail
returns those distributions.

## Recommendation process

For each active driver the orchestrator:

1. Reuses one causal pit-loss estimate for the complete grid.
2. Obtains the Phase 4 legal PIT_NOW and EXTEND candidates and their policy scores.
3. Runs the Phase 5E frozen stochastic transition kernel for every candidate.
4. Ranks each action using its three-lap median relative time, terminal pit-cycle position, and
   policy score as a final tie-break.
5. Compares the best PIT_NOW and EXTEND 90% intervals.

If those intervals overlap, the final call is `HOLD_NO_CLEAR_ADVANTAGE`. If they separate, the
better evaluated action may be returned. A strong difference from the Phase 4 policy is exposed
as `model_disagreement` and reduces an otherwise actionable recommendation to `CAUTION`. This
makes the independent outcome model a safety check instead of treating the old policy score as
ground truth.

The public uncertainty states are:

- `ACTIONABLE`: supported short-horizon outcomes separate and the policy agrees.
- `CAUTION`: outcomes overlap, or policy and outcome evaluation disagree.
- `COARSE_ONLY`: only limited or weakly applicable timing evidence is available.
- `INSUFFICIENT_DATA`: no defensible action comparison can be made.

These states are operating constraints, not calibrated confidence percentages. Internal Phase
5 reliability labels remain diagnostic inputs and are not presented as proven accuracy ranks.

## Full-grid behavior and rivals

Every participant remains visible. Active drivers receive candidate evaluation when the causal
inputs support it. Retired, stopped, disqualified, or otherwise inactive drivers receive no
strategy action.

The compact snapshot includes observed position, compound, tyre age, recent pace, traffic,
pit-cycle position, final recommendation, alternative, decision state, main risk, opportunity,
alerts, and data quality. It keeps at most four relevant rivals selected from nearby track order
or a legitimate five-second timing neighborhood.

Backmarkers expose `gap_kind`, `laps_behind`, and pit-cycle position. For `LAP_DEFICIT` and
`UNKNOWN` gaps, `gap_to_leader_seconds` remains null. The service never converts a lap deficit
into an invented seconds gap.

## Alerts

Alerts are deterministic response objects with a kind, driver, optional rival, and concrete
detail. Supported kinds are `UNDERCUT_THREAT`, `CLEAR_AIR_WINDOW`, `REJOIN_TRAFFIC_RISK`,
`PIT_WINDOW_OPEN`, `PIT_WINDOW_CLOSED`, `STRATEGY_MODEL_UNCERTAIN`, `RIVAL_STOPPED`, and
`POSITION_AT_RISK`. No language model generates or interprets them.

## History, changes, and stability

The timeline records observed position and gap state alongside recommendation, decision state,
recommendation age, persistence, and policy disagreement. A recommendation change is supported
only when a named component changed: traffic, observed position, compound/pit cycle, predicted
pit-cycle position, uncertainty state, or the relevant rival set and pit state.

If raw output flips without one of those changes, the prior effective recommendation persists.
The entry records `unsupported_flip_suppressed=true` and the reason
`unsupported flip suppressed by hysteresis`. Accepted changes reset recommendation age to one;
otherwise age and persistence increase. PIT_NOW persistence is therefore visible until an
observed pit stop, a closed window, or another recorded race-state change alters the call.

Timeline metrics report recommendation frequency, HOLD and PIT rates, accepted flip rate,
unsupported flip rate, actionability coverage, policy disagreement, mean persistence, and mean
PIT-call age. The recorded multi-race results are in `docs/pitwall-evaluation.json`.

The recorded run covered 189 consecutive eligible laps across the 2024 Japanese, Bahrain,
Italian, and Spanish Grands Prix, producing 3,780 driver-lap observations. Of 3,195 returned
recommendations, 1,304 (40.8%) were HOLD and 1,891 were EXTEND_3. No PIT_NOW call cleared the
interval-overlap and disagreement safeguards. Strict actionability coverage was 2.6%, policy
disagreement was 56.8%, mean persistence was 6.58 laps, accepted flip rate was 17.6%, and
unsupported-flip suppression was 4.3%.

This is a material limitation rather than evidence of strategy accuracy: within five laps the
model rarely shows enough post-stop benefit to offset a stop with separated uncertainty. The
system tracks PIT-call persistence, but this sample provides no final PIT call on which to report
empirical persistence. Widening the horizon to manufacture PIT calls would violate the validated
operating envelope.

For P16+, the run included 945 observations. Recommendation coverage was 48.4%; every returned
call was `COARSE_ONLY` or `CAUTION`, with no `ACTIONABLE` backmarker call. There were 309 explicit
lap-deficit observations and 165 unknown-gap observations. None received a fabricated seconds
gap.

On a 2024 Bahrain lap-25 grid, 20 active drivers and 80 candidate actions at 500 trajectories per
action took 22.04 seconds in the recorded environment.

## API

- `GET /api/v1/pitwall/{year}/{round}/{lap}` returns the compact full-grid snapshot.
- `GET /api/v1/pitwall/{year}/{round}/{lap}/drivers/{driver_id}` adds engineering analysis,
  candidate actions, and 1/3/5-lap distributions.
- `GET /api/v1/pitwall/{year}/{round}/timeline?start_lap=6&end_lap=20` returns rolling history.
- Add `driver={driver_id}` to the timeline query for one driver.
- Add `trajectory_count=100`, `500`, or `1000` to choose the supported sampling budget.

## Limitations

The system has no Safety Car/VSC, red-flag, rain, reliability-failure, paid live-timing, or
whole-race strategy model. Pit loss and rejoin remain limited by the normalized public archive.
Short-horizon calibration is imperfect, especially in traffic, for lapped cars, and in sparse
circuit regimes. A recommendation describes the system's causal short-horizon comparison; it
does not prove that a historical team's different call was wrong.
