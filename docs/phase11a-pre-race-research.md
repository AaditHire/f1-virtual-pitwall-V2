# Phase 11A — Pre-Race Simulation Research

**STATUS: COMPLETE — RESEARCH ONLY**

No production service, API, frontend, strategy policy, or tactical-kernel code was changed.

## Pre-race information boundary

The cutoff is the scheduled target-race start. Features may use final grid/qualifying metadata and outcomes from strictly earlier events. Target-race laps, stints, stops, weather, incidents, pace and results are labels only and never enter the feature matrix.

## Data inventory

| Feature | Classification | Finding |
| --- | --- | --- |
| Starting grid | AVAILABLE PRE-RACE | Jolpica race entry; includes applied grid changes. |
| Qualifying position/Q1-Q2-Q3 | AVAILABLE PRE-RACE | Jolpica qualifying classification and recorded session times. |
| Driver/team/circuit | AVAILABLE PRE-RACE | Calendar, roster and qualifying metadata. |
| Prior races / season-to-date | AVAILABLE PRE-RACE | Chronological aggregates built only after each earlier event. |
| Prior-season circuit history | AVAILABLE PRE-RACE | Earlier event labels at the same circuit only. |
| Grid penalties | AVAILABLE PRE-RACE | Final grid vs qualifying position delta; penalty reason is not consistently available. |
| Practice long runs | AVAILABLE PRE-RACE | FastF1 sessions exist, but coverage/clean-run normalization is not production-ready; excluded here. |
| Forecast weather | UNAVAILABLE | No historically archived point-in-time forecast in current providers. |
| Race-session weather | AVAILABLE BUT LEAKY | FastF1 race weather is observed during/after the target race. |
| Per-driver tyre allocation | UNAVAILABLE | Not exposed consistently by the inspected provider interfaces. |
| Historical stint patterns | AVAILABLE PRE-RACE | Only aggregates from strictly earlier races; target-race stints are labels. |
| Target race pace/tyres/pits/incidents | AVAILABLE ONLY AFTER RACE START | Labels only; prohibited from model features. |
| Final result/status | AVAILABLE BUT LEAKY | Target labels only. |

## Chronological dataset

- Training: 2021–2023, 17 races / 339 driver-race rows.
- Development: 2024, 7 races / 140 rows.
- Untouched holdout: 2025, 11 races / 219 rows.
- Candidate selection used development only. Selected candidates were refit on 2021–2024 before final 2025 evaluation.
- These are the repository's 35 pre-existing evaluation archives, not every race on each calendar. That limits coverage claims and makes this a feasibility screen rather than production validation.
- Grid coverage is 100.0%; qualifying-position coverage is 99.9%; same-phase qualifying-gap coverage is 99.3%.
- Classified-finisher pit targets cover 85.8% / 92.1% / 87.2% of train/development/holdout rows.

## Targets, baselines and models

- Finishing rank: event-level re-ranking of expected finish scores. Baselines are starting grid, recent-three-race ordering and season-form ordering; candidates are Ridge and shallow gradient boosting. This never emits duplicate ranks.
- Finishing band: TOP 5 / positions 6–15 / 16+. Baseline is the smoothed historical distribution for the corresponding grid band; candidate is multinomial logistic regression.
- Pit-stop count: 1 / 2 / 3+ among classified finishers. Baseline is the circuit historical distribution; candidate is multinomial logistic regression. No classified zero-stop example existed in this sample, so zero-stop probability is not estimable here.
- First stop: lap and broad uncertainty window among classified finishers. Baseline is circuit historical median; candidates are Ridge and shallow gradient boosting on normalized race distance.
- Strategy family: one-stop long/other and multi-stop short/other, using fixed first-stint fractions. Baseline is circuit historical mode/distribution; candidate is multinomial logistic regression.
- Incident/survival: classified versus DNF. Baseline is smoothed circuit history; candidate is logistic regression. This is deliberately separate from pace/rank.

## Results

| Target | Development baseline → model | Holdout baseline → model | Result |
| --- | --- | --- | --- |
| Finishing rank | 2.214 → 2.371 position MAE | 3.425 → 3.342 | baseline wins |
| Finishing band | 0.624 → 0.790 log loss | 0.838 → 0.857 | baseline wins |
| Pit-stop count (classified) | 1.107 → 1.163 | 0.996 → 0.992 | baseline wins |
| First-stop lap (classified) | 9.271 → 10.060 lap MAE | 10.639 → 10.536 | baseline wins |
| Strategy family (classified) | 1.302 → 1.462 | 1.137 → 1.380 | baseline wins |
| Incident/survival | 0.310 → 0.656 | 0.394 → 0.659 | baseline wins |

Full metrics, class counts, Brier scores, calibration error, ranking correlation, pairwise accuracy, intervals and coverage are in `docs/phase11a-pre-race-research.json`.

## Leakage and failure analysis

- Leakage audit: PASS; forbidden feature intersections: none; chronological violations: 0.
- DNFs, safety cars, red flags, weather changes and penalties remain irreducible/unmodelled event uncertainty. Incident probability is evaluated separately rather than being treated as deterministic pace.
- Circuit identity is sparse: several holdout circuits have only one or no earlier cached race, so circuit baselines frequently fall back to the global historical distribution.
- Holdout rank-model circuit slice: 119 rows with prior-circuit evidence at 2.840 position MAE; 100 rows without it at 3.940 MAE.
- Stop targets are restricted to classified finishers; early retirements mechanically truncate strategy and would confound pit-behaviour prediction.
- Qualifying gap compares a driver only with the fastest time in the same reached qualifying phase; it is not treated as a cross-session pace delta.
- Jolpica's `Lapped` status was explicitly normalized as classified across every split. A regression test protects that provider semantic; no holdout-driven model or parameter change was made.

## Decision

**NO-GO for Phase 11B.** Components that beat their appropriate baseline on both development and holdout primary criteria: none.

The evidence does not justify a production pre-race simulator. Do not proceed by adding heuristic layers or longer tactical rollouts.

Research runtime: 4.5s (excluding first-time provider metadata download).
