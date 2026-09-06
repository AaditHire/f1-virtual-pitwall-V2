# F1 Virtual Pit Wall — Phase 1

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
