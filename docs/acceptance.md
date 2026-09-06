# Phase 1 real-data acceptance

Verified September 6, 2026, approximately 08:32–08:37 UTC. These are observations from live providers, not application constants or fallback fixtures. Counts can change as providers update data.

| Check | Observed result |
| --- | --- |
| A — 2021 | 21 drivers, 10 constructors, 22 events; latest completed race returned 20 results |
| Additional historical — 2023 | 22 drivers, 10 constructors, 22 events; latest completed race returned 20 results |
| B — Dynamic latest | Discovered 2026; 32 season-listed drivers, 11 constructors, 23 calendar events; latest completed race returned 22 results |
| C — Actual next session | Italian Grand Prix, Race, September 6 at **13:00 UTC / 18:30 Asia/Kolkata** |
| D — Sprint | Chinese Grand Prix: Practice 1 → Sprint Qualifying → Sprint → Qualifying → Race; also verified populated OpenF1 Dutch grid (22 entries) |
| E — Completed event | Dutch Grand Prix: 22 qualifying entries, 22 starting-grid entries, 22 race results |
| F — Complete standings | 23 driver standings and 11 constructor standings, checked against provider totals |
| G — News | Recent real BBC Sport F1 and Autosport F1 headlines, with timestamps and URLs |
| H — Home | Italian weekend `in_progress`, next Race session, full weekend schedule, Italian OpenF1 grid (22), championship leaders, news and all four providers healthy |

Full driver/calendar/result and standings counts were compared against Jolpica's reported totals; driver IDs were checked for uniqueness. The 32 season participants are not a 32-car race grid. UTC/local timestamps were checked for equal instants and the correct +05:30 offset.

The live suite initially exposed OpenF1's `No results found` response on race-key grids. Investigation confirmed populated `starting_grid` records under qualifying keys. The adapter now handles both keys and distinguishes explicit no records from an actual broken endpoint. These unavailable resources remain visible in provider status.

Final validation passed: **34 offline tests**, **9 live integration cases** covering A–H plus the additional historical season, Ruff lint/format, FastAPI lifespan, and an actual temporary Uvicorn socket. The smoke script exercised health, latest/active seasons, current/next event, next session, complete participant/calendar/standings responses, news, home and provider status. It shut the server down after validation. No static type checker is configured.

Reproduce with the commands in the README. Local `acceptance-output.json` contains the socket-test snapshot and is git-ignored; no operational data snapshot is used by the application.
