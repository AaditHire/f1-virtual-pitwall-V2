# Phase 7 current/live F1 backend

## Scope and public status

Phase 7 adds current-weekend aggregation and provider-independent live timing without changing
the Phase 3–6E analysis, simulation, or strategy models. Current Pit Wall responses always publish
`strategy_status: EXPERIMENTAL`. A live recommendation is short-horizon model output and is not an
operational strategy instruction.

## Data flow

```text
OpenF1 (LiveTimingProvider) ─┐
                            ├─ timestamped HistoricalRace ─ RaceState ─ PitWallService
FastF1 timing archive ──────┘
```

`LiveTimingProvider` defines separate capabilities for session, driver, position, interval, lap,
stint, pit, track, weather, and race-control observations. `LiveService` is the only component that
knows how those observations become the internal causal history. The existing `AnalysisContext`,
`RaceStateBuilder`, and `PitWallService` then run unchanged. The live adapter never supplies final
race results to the timing state.

## Session and availability semantics

Scheduled time alone never produces `LIVE`. Recent provider observations are required. Fresh data
within the session window produces `LIVE`; observations 31–120 seconds old produce `DELAYED`.
Completed provider sessions return `HISTORICAL_ONLY`; missing sessions, unknown active state, or
provider failure return `UNAVAILABLE`.

Freshness is based on the newest timestamp in timing, laps, pits, weather, track, or race-control
data. Every current payload reports the source, retrieval time, provider time, age, and one of
`FRESH`, `DELAYED`, `STALE`, or `UNKNOWN`.

## Missing data

The adapter retains missing positions, gaps, intervals, compounds, tyre ages, weather, and track
state as null or `UNKNOWN`. `/live/pitwall` requires an active Race, drivers, positions, laps,
stints, track evidence, and at least three reported laps. If any requirement is absent, it returns
partial state where possible plus `missing_requirements`; it does not run a recommendation.

Track status accepts a dedicated provider feed or recognized race-control flags. The latter is
needed for the recorded 2026 Italian Grand Prix, whose OpenF1 `track_status` response was empty.
Race-control messages are limited to the most recent 50 normalized messages. Raw telemetry is not
returned.

## Current weekend and home

`CurrentWeekendService` composes the existing calendar, results, standings, and news services with
live evidence. It returns the schedule and normalized status of every session, the active and next
session, qualifying, confirmed grid, latest race results, full standings, event news, weather
availability, provider health, and section errors. A failed optional provider does not remove the
other sections.

`/home` retains its Phase 1 fields and adds `current_weekend`, `live_status`, an optional current
race state, and stable API navigation paths. Event-specific news uses the existing deduplicating
aggregator and falls back to the latest F1 stories when no event match is found.

## Caching and polling

OpenF1 calls use the existing bounded in-process TTL cache. Live resources use `short_ttl`
(60 seconds by default); calendars remain long-lived and standings/news retain their established
service policies. Endpoints fetch on request and do not start background polling.

## Validation

The recorded compatibility check compares OpenF1's live-style completed-session records with the
FastF1 archive for the 2026 Italian Grand Prix at lap 53. All 22 common drivers matched on position,
completed laps, compound, stint number, and completed pit count. This final checkpoint does not
prove intermediate publication-time equality: OpenF1 stint rows do not include a trustworthy
publication timestamp, so the adapter makes them visible at the current snapshot cutoff only.
See `phase7-live-historical-consistency.json` for the machine-readable result.

Runtime measurements are in `phase7-performance.json`. Cold current-weekend time includes public
network latency. Warm provider reads reuse the bounded cache. The Phase 7 historical path delegates
to the same Pit Wall implementation; no strategy or simulation calculation was changed.

