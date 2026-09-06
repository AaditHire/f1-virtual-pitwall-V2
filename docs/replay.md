# Historical race replay

## Source and architecture

`FastF1Provider → HistoricalRace → RaceStateBuilder → ReplayService → FastAPI`

The existing `Driver`, `Constructor`, `Event` and `Session` models are reused. New replay records live in `domain/replay.py`. Services never receive DataFrames or raw upstream dictionaries.

FastF1's processed `Laps` table can repair earlier laps with later information and generate retirement laps. For strict causal replay, the adapter instead uses FastF1's cached original **TimingData, TimingAppData, DriverList, SessionStatus, TrackStatus, LapCount and RaceControlMessages streams**. It does not load or consult final results, final driver classifications, telemetry or weather. Tests independently compare participants and aligned tyre observations with FastF1's public session data.

This uses a small isolated private-API boundary for FastF1's archive URLs/parser; FastF1 is constrained to the tested 3.8 release family. See [FastF1 source](https://github.com/theOehrly/Fast-F1/blob/master/fastf1/_api.py) and [core processing](https://github.com/theOehrly/Fast-F1/blob/master/fastf1/core.py).

## Cutoff and future independence

**Lap N means the first reported completion of that lap by any driver**, a common archive-time cutoff for the entire field. Trailing cars will usually have completed N−1 laps at that instant. That alone does not mean they have been lapped.

Every changing fact carries its archive publication time. The builder only consumes observations at/before the cutoff. Lap-time revisions also carry a separate availability time; a later correction to an earlier lap stays invisible until published. An unnumbered ambiguous lap-time update is not backfilled. Numbered TimingAppData lap-time updates are accepted at their own publication time.

Roster identity comes from that race's pre-start DriverList, never the season list. IDs use the stable feed `Reference` (`f1:MAXVER01`, for example), with a name-based fallback when no reference is supplied. They are independent of race numbers and distinct from Jolpica's namespace; use the ID returned by replay endpoints. Grid positions come from timestamped GridPos observations. No final result fields are read.

The archive must be completed to load as a historical race. That availability check is outside snapshot reconstruction: the builder never receives final classification or a final-distance-derived scheduled lap count. Total scheduled laps comes from LapCount updates available by the cutoff, including known reductions. Published future archive laps are listed only by the separate `/laps` metadata endpoint.

## Snapshot fields

- Event/session, requested lap, scheduled laps, archive time, elapsed race time, all session drivers, track/SC/VSC/red-flag state and data quality.
- Each driver: reported position, starting grid slot, completed laps, leader/ahead/behind gaps, compound, reported tyre age, stint, completed pit visits, latest completed lap time, descriptive recent pace, status/active/retired and lapped state.
- Gaps are seconds from timestamped live timing observations, never final results or subtraction of unequal-lap clocks. Seconds gaps older than 30 seconds are unknown. Intervals following a reported position change are withheld until refreshed. Lap-valued gaps remain lap deficits; unchanged delta-stream values persist. No interpolation or invented exact gaps.
- Tyre age is the last reported TotalLaps for the observed set, including pre-race used-tyre life. `tyre_age_observed_at` exposes its publication time. It can lag the latest crossing; it is not guessed from future stints or incremented using assumptions. A delayed update to an old stint cannot replace the current stint.
- Pit visits count only after a recorded exit; pre-race garage movements are excluded. An open stop with an exit after the cutoff is still incomplete. A tyre change under a stoppage need not count as a pit visit.
- Pace is the median of up to three most recent clean completed laps; `pace_laps` lists the exact inputs. Both observed crossings must exist, duration must be positive and plausible between them, and the lap cannot intersect a pit entry/exit or unknown/non-green track condition. Yellow, SC, VSC and red-flag periods are excluded conservatively. This is descriptive, not a prediction or degradation model.
- Retirement requires an explicit timestamped Retired flag. Stopped does not imply retired. DNS/DSQ require explicit source evidence, including car-specific race-control messages; absent evidence stays unknown. A pre-race pit-lane presence alone does not prove starting. No retirement is inferred from the final row of a driver's historical lap table.

## API and caching

| GET endpoint | Response |
| --- | --- |
| `/api/v1/replay/{year}/{round}/laps` | Available observed lap numbers and session participant count |
| `/api/v1/replay/{year}/{round}/{lap}` | Full `RaceState` |
| `/api/v1/replay/{year}/{round}/{lap}/drivers/{driver_id}` | One driver from that same snapshot |

Invalid years/rounds/laps have the existing 422/404 semantics; provider/schema/unfinished-archive failures return a clean 503. All endpoints return Pydantic models. No replay streaming is implemented.

FastF1 caches downloaded archives on disk. A bounded in-memory LRU reuses normalized sessions across snapshots and driver endpoints. Concurrent loads are serialized; blocking library work runs off the async event loop. Evicted sessions are reparsed from the disk cache. Failed loads are not cached as valid races. Only archive GETs are retried for transient failures, with explicit timeouts. FastF1 manages its own schedule lookup and disk-cache policy.

## Limits and verification

This is **the state knowable from the archive**, not omniscient physical ground truth. Publication delay, asynchronous position/gap updates, missing timing packets and later official corrections constrain precision. Conflicting positions are flagged. A stopped car may remain `stopped`/unknown when no retirement flag was supplied. Post-race disqualifications must not appear in earlier snapshots.

`timestamp` is null because no trustworthy absolute clock alignment is available without additional data. `session_time_seconds` and `elapsed_race_seconds` are explicit relative times; the scheduled UTC start is not used to manufacture an actual timestamp. No future weather or race-control state enters the snapshot.

Live acceptance covered Bahrain 2021 (20 participants), Bahrain 2023 (20), Bahrain 2024 (20), and dynamically discovered Dutch 2026 (22). At mid-race snapshots, 17/19/19/19 respectively had independently aligned FastF1 tyre/stint/life observations. Piastri in Bahrain 2023 was active at lap 5; the archived retirement flag at 5222.391 archive seconds was absent at the lap-14 cutoff and present at lap 15.

Leakage tests remove/mutate future laps, positions, tyres, pit stops, control changes and retirements, including later corrections to earlier laps. A real 2023 archive was also truncated at lap 20 **before normalization**; its rebuilt full snapshot matched the complete archive's snapshot exactly. A real Uvicorn socket test exercises full and individual snapshots plus available laps. Phase 1 regressions remain included.

See [Bahrain 2024 lap 25](replay-sample.md) for the inspected complete-grid table. Ages/gaps there are explicitly last-reported values. Phase 3 is not implemented.

Final local validation: **61 offline tests passed** (34 existing Phase 1 + 27 replay tests), **7 real historical replay integration tests passed**, Ruff lint/format passed, dependency consistency passed, and the actual Uvicorn replay smoke passed. No static type checker is configured. FastF1's processed-source comparison emits upstream NumPy timedelta deprecation warnings; replay does not use that processing path.

The final combined network run passed 14/16 tests. Two existing Phase 1 OpenF1-dependent tests were externally blocked by HTTP 401: OpenF1 explicitly restricts all unauthenticated API access during a live F1 session, including historical queries. Phase 1 tests/code were not weakened to hide this. Full live regression sign-off remains pending public-access restoration or authenticated access; all Phase 2 acceptance gates passed. The combined homepage/replay socket smoke had also passed before the restriction began.
