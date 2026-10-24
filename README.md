# F1 Virtual Pit Wall

Replay any Formula 1 race since 2021 lap by lap, seeing only what the pit wall could have known at
that moment, with tyre, pace, traffic and pit-window analysis for every car.

- **Web app:** [f1-virtual-pitwall.vercel.app](https://f1-virtual-pitwall.vercel.app)
- **API:** [Swagger UI](https://f1-virtual-pitwall-api.vercel.app/docs) ·
  [health](https://f1-virtual-pitwall-api.vercel.app/health) · base URL
  `https://f1-virtual-pitwall-api.vercel.app`

## What it does

- **Historical replay.** Pick a race and a leader lap. The whole field is rebuilt from the
  timestamped FastF1 archive using only data published by that instant, so nothing from the future
  leaks in. Team radio is included where the archive has it.
- **Pit wall analysis.** Clean-air pace, tyre-stint trends, observed pit loss, rejoin position,
  traffic, and undercut/overcut margins, each with its evidence and a confidence level. Missing
  evidence stays empty instead of being guessed.
- **Experimental strategy.** Paired pit-versus-extend simulations over 1, 3 and 5 laps for the full
  grid. Clearly labelled experimental; the evaluations behind that label are in the
  [research log](docs/research-log.md).
- **Race weekend hub.** Calendar, session times in your own timezone, qualifying, grid, results,
  standings, weather, race control and deduplicated news.
- **Live pit wall.** The same analysis on current OpenF1 timing during a session (needs an OpenF1
  sponsor account for real-time data; see [Configure](#configure)).

Built with FastAPI and Pydantic on Python 3.12, and Next.js 16 with React 19. Data comes from
[Jolpica](https://github.com/jolpica/jolpica-f1), [OpenF1](https://openf1.org),
[FastF1](https://github.com/theOehrly/Fast-F1) archives and public RSS feeds.

## Quick start

Backend:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m uvicorn f1_pitwall.main:app --reload
```

Frontend, in a second terminal:

```powershell
cd frontend
npm install
$env:API_BASE_URL = "http://127.0.0.1:8000"
npm run dev
```

Open [localhost:3000](http://localhost:3000) for the app and
[localhost:8000/docs](http://127.0.0.1:8000/docs) for the API. On macOS/Linux use
`.venv/bin/python` and `export API_BASE_URL=...`. Internet access is needed for provider data;
`/health` does not call providers.

## Configure

Every setting is optional and read from `F1_*` environment variables at startup.

| Variable | Default | Purpose |
| --- | --- | --- |
| `F1_OPENF1_USERNAME`, `F1_OPENF1_PASSWORD` | unset | OpenF1 sponsor login. Without it, live mode shows historical data only. |
| `F1_REPLAY_SNAPSHOT_URL` | this repo's `replays` release | Where prebuilt replay files are read from (HTTP base URL or local folder). |
| `F1_REPLAY_CACHE_DIR` | `.cache/fastf1` | FastF1 download cache; falls back to the temp folder when not writable. |
| `F1_REPLAY_CACHE_SIZE` | `4` | Normalized races kept in memory per process. |
| `F1_TIMEOUT`, `F1_RETRIES` | `15`, `2` | Provider request timeout (seconds) and transient retries. |
| `F1_LONG_TTL`, `F1_MEDIUM_TTL`, `F1_SHORT_TTL` | `3600`, `300`, `60` | Cache lifetimes in seconds; `0` disables caching. |
| `F1_CACHE_SIZE` | `256` | Cached responses per provider. |
| `F1_NEWS_FEEDS` | BBC Sport, Autosport | JSON object of source name to RSS URL. |
| `F1_JOLPICA_URL`, `F1_OPENF1_URL` | public APIs | Provider base URLs. |

## Deployment

Both the frontend and the API run on Vercel's free plan. Serverless functions start cold and
cannot keep a FastF1 cache, so replays are prebuilt: the
[Replay snapshots](.github/workflows/replay-snapshots.yml) workflow converts every completed race
since 2021 into a small gzipped file (about 300–400 KB) and publishes it on the `replays` GitHub
release every Monday and Wednesday. The API loads a race from that file in well under a second and
falls back to FastF1 when a race has no snapshot yet. Run the workflow by hand from the Actions tab
to rebuild a season.

## API

All data routes live under `/api/v1`. Invalid input returns 422, unknown seasons or events 404, and
provider failures 503. `/home` and `/news` return partial data with explicit error fields rather
than failing whole.

| GET path (after `/api/v1`) | Data |
| --- | --- |
| `/seasons`, `/seasons/latest`, `/seasons/active`, `/seasons/{year}` | Seasons from 2021 |
| `/seasons/{year}/drivers`, `/seasons/{year}/constructors`, `/seasons/{year}/calendar` | Season participants and calendar |
| `/events/current`, `/events/next`, `/sessions/next` | Current or next weekend and session |
| `/events/{year}/{round}` and `/sessions`, `/qualifying`, `/grid`, `/results` | One event's schedule and classifications |
| `/results/latest` | Latest completed race |
| `/standings/drivers/{year}`, `/standings/constructors/{year}` | Championship standings |
| `/news?limit=10&query=Ferrari` | Recent news with an optional text filter |
| `/home`, `/weekend/current` | Aggregated home page and weekend views |
| `/replay/{year}/{round}/laps`, `/replay/{year}/{round}/{lap}` | Replay laps and full-field state |
| `/replay/{year}/{round}/{lap}/drivers/{driver_id}` and `/radio` | One driver's state and team radio |
| `/analysis/{year}/{round}/{lap}/drivers/{driver_id}` | Pace, tyre and traffic analysis |
| `/pitwall/{year}/{round}/{lap}`, `/pitwall/{year}/{round}/timeline` | Full-grid pit wall (timeline limited to 10 laps) |
| `/live/status`, `/live/race`, `/live/pitwall`, `/live/weather`, `/live/race-control` | Current session |
| `/providers/status` | Provider health, including OpenF1 authentication |

Timestamps are UTC. Calendar, event, session, home and live routes also accept
`?timezone=<IANA zone>`, which adds `*_local` companion fields. Driver IDs use the official timing
reference (for example `f1:MAXVER01`) in both replay and live mode.

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/replay/2024/1/25'
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/pitwall/2024/1/25/drivers/f1:MAXVER01'
```

## Test

```powershell
.venv\Scripts\python -m pytest -q
.venv\Scripts\ruff check .
.venv\Scripts\ruff format --check .
cd frontend; npm run lint; npm test; npm run build
```

CI runs the same checks on every push to `main`. Default tests skip the network; run
`pytest -m network` to exercise the real providers. Research tests for phases 12 and up need
`pip install -e ".[dev,research]"`, and the two Phase 12C workbook test files also need the local
spreadsheet runtime, so CI skips them.

## Docs

- [Architecture](docs/architecture.md), [data sources and limits](docs/data_sources.md),
  [replay semantics](docs/replay.md), [analysis methods](docs/analysis.md),
  [pit wall limits](docs/pitwall.md)
- [Frontend guide](frontend/README.md) and [design system](frontend/docs/design-system.md)
- [Research log](docs/research-log.md): phases 1 to 13D, including the negative results and the
  current NO-GO for a public chatbot. [PROJECT_STATE.md](docs/PROJECT_STATE.md) is the detailed
  handoff.
