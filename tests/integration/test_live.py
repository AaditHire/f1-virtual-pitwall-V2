"""All provider traffic is real. ASGITransport only calls our own FastAPI routes."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio

from f1_pitwall.core.config import Settings
from f1_pitwall.domain.enums import SessionType
from f1_pitwall.main import create_app

pytestmark = [pytest.mark.network, pytest.mark.asyncio(loop_scope="module")]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def live():
    app = create_app(Settings.from_env())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as api:
            yield api, app.state.hub


async def get(api, path):
    response = await api.get(path)
    assert response.status_code == 200, response.text
    return response.json()


async def total(hub, path):
    raw = await hub.jolpica.http.get(f"{hub.jolpica.base}/{path}.json", 60, limit=1)
    return int(json.loads(raw)["MRData"]["total"])


@pytest.mark.parametrize("requested", [2021, 2023, "latest"])
async def test_a_b_seasons_complete_calendars_and_results(live, requested):
    api, hub = live
    year = (
        (await get(api, "/api/v1/seasons/latest"))["year"] if requested == "latest" else requested
    )
    drivers = await get(api, f"/api/v1/seasons/{year}/drivers")
    teams = await get(api, f"/api/v1/seasons/{year}/constructors")
    calendar = await get(api, f"/api/v1/seasons/{year}/calendar")
    assert len(drivers) == await total(hub, f"{year}/drivers") > 0
    assert len(teams) == await total(hub, f"{year}/constructors") > 0
    assert len(calendar) == await total(hub, str(year)) > 0
    assert len({d["id"] for d in drivers}) == len(drivers)
    assert len({t["id"] for t in teams}) == len(teams)
    assert len({e["round"] for e in calendar}) == len(calendar)
    latest, results = await hub.results.get_latest_results(year)
    assert latest is not None
    assert len(results) == await total(hub, f"{year}/{latest.round}/results") > 0
    assert {r.driver.id for r in results} <= {d["id"] for d in drivers}
    print(
        json.dumps(
            {
                "season": year,
                "drivers": len(drivers),
                "constructors": len(teams),
                "events": len(calendar),
                "latest_completed_round": latest.round,
                "results": len(results),
            }
        )
    )


async def test_c_actual_next_session(live):
    api, hub = live
    result = await get(api, "/api/v1/sessions/next?timezone=Asia/Kolkata")
    # Acceptance deliberately fails in off-season with no published future schedule.
    assert result is not None, "No next session published by providers"
    start = datetime.fromisoformat(result["session"]["start"])
    local = datetime.fromisoformat(result["session"]["start_local"])
    assert start > datetime.now(UTC) and start == local
    assert local.utcoffset() == timedelta(hours=5, minutes=30)
    candidates = [
        s.start
        for e in await hub.calendar.relevant_events()
        for s in e.sessions
        if s.start and s.start > datetime.now(UTC) and not s.cancelled
    ]
    assert start == min(candidates)
    print(
        json.dumps(
            {
                "next_event": result["event"]["name"],
                "session": result["session"]["name"],
                "utc": result["session"]["start"],
                "kolkata": result["session"]["start_local"],
            }
        )
    )


async def test_d_sprint_weekend_and_openf1_live_schema(live):
    api, hub = live
    year = (await hub.seasons.get_latest_season()).year
    weekends = await hub.openf1.weekends(year)
    assert weekends and any(w.sessions for w in weekends)
    calendar = await hub.calendar.get_calendar(year)
    sprint = next(e for e in calendar if any(s.type == SessionType.SPRINT for s in e.sessions))
    starts = [s.start for s in sprint.sessions if s.start]
    assert starts == sorted(starts)
    assert any(s.type == SessionType.SPRINT_QUALIFYING for s in sprint.sessions)
    # Directly exercise OpenF1 starting_grid even if finalized grids use Jolpica.
    for e in reversed(calendar):
        race = next(s for s in e.sessions if s.type == SessionType.RACE)
        if race.provider_id and race.start and race.start < datetime.now(UTC):
            drivers = await hub.seasons.get_drivers(year)
            grid = await hub.openf1.grid(race, drivers)
            if not grid:
                qualifying = next(s for s in e.sessions if s.type == SessionType.QUALIFYING)
                grid = await hub.openf1.grid(qualifying, drivers)
            if grid:
                assert len({g.driver.id for g in grid}) == len(grid)
                print(json.dumps({"openf1_grid_event": e.name, "entries": len(grid)}))
                break
    else:
        pytest.fail("No populated OpenF1 grid could be verified")
    print(json.dumps({"sprint_event": sprint.name, "sessions": [s.name for s in sprint.sessions]}))


async def test_e_qualifying_grid_results(live):
    api, hub = live
    year = (await hub.seasons.get_active_season()).year
    event, _ = await hub.results.get_latest_results(year)
    prefix = f"/api/v1/events/{year}/{event.round}"
    qualifying = await get(api, f"{prefix}/qualifying")
    grid = await get(api, f"{prefix}/grid")
    results = await get(api, f"{prefix}/results")
    assert len(qualifying) == await total(hub, f"{year}/{event.round}/qualifying") > 0
    assert len(results) == await total(hub, f"{year}/{event.round}/results") > 0
    assert {g["driver"]["id"] for g in grid} == {r["driver"]["id"] for r in results}
    assert all(
        g["position"] == next(r["grid"] for r in results if r["driver"]["id"] == g["driver"]["id"])
        for g in grid
    )
    print(
        json.dumps(
            {
                "completed_event": event.name,
                "qualifying": len(qualifying),
                "grid": len(grid),
                "results": len(results),
            }
        )
    )


async def test_f_complete_standings(live):
    api, hub = live
    year = (await hub.seasons.get_active_season()).year
    drivers = await get(api, f"/api/v1/standings/drivers/{year}")
    teams = await get(api, f"/api/v1/standings/constructors/{year}")
    assert len(drivers) == await total(hub, f"{year}/driverStandings") > 0
    assert len(teams) == await total(hub, f"{year}/constructorStandings") > 0
    assert len({d["driver"]["id"] for d in drivers}) == len(drivers)
    assert all(isinstance(d["points"], (int, float)) for d in drivers)
    print(json.dumps({"driver_standings": len(drivers), "constructor_standings": len(teams)}))


async def test_g_recent_real_news(live):
    api, _ = live
    news = await get(api, "/api/v1/news?limit=5")
    assert news["articles"]
    assert any(
        datetime.fromisoformat(a["published_at"]) > datetime.now(UTC) - timedelta(days=7)
        for a in news["articles"]
        if a["published_at"]
    )
    assert all(
        a["source"] and a["url"].startswith("http") and a["published_at"] for a in news["articles"]
    )
    print(
        json.dumps(
            {
                "news": [
                    {k: a[k] for k in ("headline", "source", "published_at", "url")}
                    for a in news["articles"][:2]
                ]
            }
        )
    )


async def test_h_home_coherence_and_endpoints(live):
    api, _ = live
    assert (await get(api, "/health"))["status"] == "ok"
    home = await get(api, "/api/v1/home?timezone=Asia/Kolkata")
    assert not home["errors"], home["errors"]
    assert home["current_or_next_event"] and home["next_session"]
    assert home["weekend_schedule"] == home["current_or_next_event"]["sessions"]
    assert home["driver_standings_top"] and home["constructor_standings_top"]
    assert home["latest_news"] and home["recent_or_available_grid"] and home["grid_event"]
    assert all(p["status"] == "ok" for p in home["provider_status"]), home["provider_status"]
    next_event = await get(api, "/api/v1/events/next?timezone=Asia/Kolkata")
    current = await get(api, "/api/v1/events/current?timezone=Asia/Kolkata")
    assert home["current_or_next_event"] == (current or next_event)
    assert await get(api, "/api/v1/results/latest")
    assert await get(api, "/api/v1/providers/status")
    print(
        json.dumps(
            {
                "home_event": home["current_or_next_event"]["name"],
                "weekend_status": home["weekend_status"],
                "grid_event": home["grid_event"]["name"],
                "provider_status": home["provider_status"],
            }
        )
    )
