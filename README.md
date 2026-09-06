# F1 Virtual Pit Wall — Phases 1–2

Python 3.12+ / FastAPI backend for seasons from **2021 through the latest provider-published season**. Calendars, session times, qualifying, starting grids, results, standings and RSS news. No frontend, telemetry, strategy, AI or database.

## Install and run

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m uvicorn f1_pitwall.main:app --reload
```

On macOS/Linux use `.venv/bin/python`. Open [interactive API docs](http://127.0.0.1:8000/docs). No API keys are required for these public providers. Internet access is needed for provider data; `/health` does not call providers.

## Configure

Environment variables are optional; no `.env` loader is required:

```powershell
$env:F1_TIMEOUT = "15"
$env:F1_LONG_TTL = "3600"
$env:F1_MEDIUM_TTL = "300"
$env:F1_SHORT_TTL = "60"
```

Other settings: `F1_RETRIES` (2), `F1_CACHE_SIZE` (256 entries per provider), `F1_JOLPICA_URL`, `F1_OPENF1_URL`, and `F1_NEWS_FEEDS` (JSON object mapping source names to RSS URLs). Configuration is validated at startup. TTLs are seconds; zero disables caching. The cache belongs to one process and clears on restart.

## API

All data routes use `/api/v1`; `/health` is process liveness. Invalid input returns 422, unsupported seasons/events 404, and provider failure 503. Empty result arrays mean no published records. `/home` and `/news` can return partial data with explicit error/status fields.

| GET path (after `/api/v1`) | Data |
| --- | --- |
| `/seasons` | Available seasons from 2021 |
| `/seasons/latest`, `/seasons/active`, `/seasons/{year}` | Latest published, provider-current, or requested season |
| `/seasons/{year}/drivers` | All provider-listed season participants |
| `/seasons/{year}/constructors` | Participating constructors |
| `/seasons/{year}/calendar` | Complete calendar and dynamic sessions |
| `/events/current`, `/events/next` | Current weekend / next unfinished event |
| `/sessions/next` | Next session and request-time seconds until start |
| `/events/{year}/{round}` | Event |
| `/events/{year}/{round}/sessions` | Weekend schedule |
| `/events/{year}/{round}/qualifying` | Q1/Q2/Q3 classification |
| `/events/{year}/{round}/grid` | Published starting grid |
| `/events/{year}/{round}/results` | Race classification |
| `/results/latest` | Latest completed race, including prior season during off-season |
| `/standings/drivers/{year}` | Full driver standings |
| `/standings/constructors/{year}` | Full constructor standings |
| `/news?limit=10&query=Ferrari` | Recent news; optional simple text filter |
| `/providers/status` | Observed health, errors and unavailable resources |
| `/home` | Homepage aggregation with explicitly identified grid event |

Calendar/event/session/home/latest-result routes accept `?timezone=Asia/Kolkata`. Canonical timestamps remain UTC (`start: "...Z"`). A non-UTC zone adds `start_local`, `end_local`, and corresponding timestamp companions with ISO offsets. These extension fields are documented here; OpenAPI describes the base UTC models. IANA zones support daylight saving; unknown zones return 422. Missing times remain `null` with the known date preserved.

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/seasons/latest'
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/seasons/2021/drivers'
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/sessions/next?timezone=Asia/Kolkata'
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/home?timezone=Asia/Kolkata'
```

## Test

```powershell
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m pytest -m network -s -q -p no:cacheprovider
.venv\Scripts\ruff check src tests scripts
.venv\Scripts\ruff format --check src tests scripts
.venv\Scripts\python scripts/smoke.py
```

Default tests exclude network. Network tests call Jolpica, OpenF1 and both RSS feeds, compare counts against provider totals, and exercise acceptance A–H. They fail if required live data is unavailable; next-session acceptance also fails during an off-season with no published future schedule. The smoke script starts a temporary loopback Uvicorn server, exercises HTTP routes, prints a JSON snapshot and stops it. No static type checker is configured.

See [architecture](docs/architecture.md), [provider priorities and limitations](docs/data_sources.md), and [recorded acceptance](docs/acceptance.md).

## Historical replay (Phase 2)

FastF1 supplies cached, timestamped race archives. Replay returns the complete session field at the first reported completion of leader lap N, using only facts published by that instant. Phase 1 endpoints remain unchanged.

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/replay/2024/1/laps'
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/replay/2024/1/25'
# Use a driver.id returned by the snapshot:
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/replay/2024/1/25/drivers/f1:MAXVER01'
```

Optional configuration: `F1_REPLAY_CACHE_DIR` (default `.cache/fastf1`) and `F1_REPLAY_CACHE_SIZE` (default 4 normalized sessions per process). First loads require network access; later requests reuse the normalized session. Downloaded archives use FastF1's disk cache.

```powershell
.venv\Scripts\python -m pytest -q -p no:cacheprovider
.venv\Scripts\python -m pytest -m network -q -p no:cacheprovider
.venv\Scripts\python scripts/smoke.py --replay 2024 1 25 > replay-output.json
.venv\Scripts\python scripts/replay_table.py replay-output.json
```

Add `--replay-only` to the smoke command to exercise only health and replay endpoints. This avoids unrelated homepage providers; the full regression tests still run separately. OpenF1 may require authentication globally during live F1 sessions, which can temporarily block existing Phase 1 network checks.

See [replay semantics and limitations](docs/replay.md) and the [full-grid example](docs/replay-sample.md).

## Deterministic analysis (Phase 3)

Analysis uses the same cached replay, filtered at the requested leader-lap cutoff. It exposes clean pace, current-stint tyre trends, observed pit loss, approximate rejoin geometry, traffic, and conditional undercut/overcut margins with evidence and confidence. Missing evidence returns null and warnings. No strategy recommendations or future-race simulation are included.

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/analysis/2023/14/19/drivers/f1:CHALEC01'
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/analysis/2023/14/19/drivers/f1:CHALEC01/tyres'
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/analysis/2023/14/19/drivers/f1:CHALEC01/traffic'
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/analysis/2023/14/19/undercut?attacker=f1:CHALEC01&target=f1:CARSAI01'
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/analysis/2023/14/19/overcut?driver=f1:CHALEC01&target=f1:CARSAI01'
.venv\Scripts\python scripts/analysis_sample.py 2023 14 19 LEC SAI
.venv\Scripts\python scripts/smoke.py --analysis 2023 14 19
.venv\Scripts\python scripts/evaluate_analysis.py --output docs/analysis-validation.json
```

See [analysis methods, confidence and validation](docs/analysis.md). Historical evaluation uses later laps only as labels, outside the runtime engine. The tyre trend did **not** outperform the zero-slope baseline in the recorded evaluation.
