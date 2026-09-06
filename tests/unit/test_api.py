from unittest.mock import AsyncMock

import httpx
import pytest

from f1_pitwall.core.config import Settings
from f1_pitwall.core.exceptions import ProviderError
from f1_pitwall.domain.models import Driver, RaceResult
from f1_pitwall.main import create_app


@pytest.fixture
async def client():
    def handler(request):
        if request.url.path.endswith("seasons.json"):
            return httpx.Response(
                200,
                json={"MRData": {"total": "1", "SeasonTable": {"Seasons": [{"season": "2030"}]}}},
            )
        return httpx.Response(503)

    app = create_app(Settings(retries=0), httpx.MockTransport(handler))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client


async def test_health_openapi_and_validation(client):
    assert (await client.get("/health")).json()["status"] == "ok"
    paths = (await client.get("/openapi.json")).json()["paths"]
    assert "/api/v1/home" in paths and "/api/v1/sessions/next" in paths
    assert (await client.get("/api/v1/seasons/latest")).json()["year"] == 2030
    assert (await client.get("/api/v1/seasons/2031")).status_code == 404
    assert (await client.get("/api/v1/seasons/2020/drivers")).status_code == 422
    assert (await client.get("/api/v1/events/2030/0")).status_code == 422
    assert (await client.get("/api/v1/sessions/next?timezone=invalid/zone")).status_code == 422
    assert (await client.get("/api/v1/news?limit=0")).status_code == 422
    assert (await client.get("/api/v1/news?limit=101")).status_code == 422


async def test_clean_error_and_provider_status(client):
    response = await client.get("/api/v1/seasons/2030/drivers")
    assert response.status_code == 503
    assert response.json()["provider"] == "jolpica"
    status = (await client.get("/api/v1/providers/status")).json()
    assert status[0]["status"] == "degraded" and status[0]["errors"]
    assert status[1]["status"] == "not_checked"


async def test_home_outage_is_explicit(client):
    home = (await client.get("/api/v1/home")).json()
    assert home["current_or_next_event"] is None
    assert home["errors"]["active_season"] and home["errors"]["calendar"]
    assert home["weekend_status"] == "unavailable"
    assert not home["latest_news"]
    assert any(p["status"] == "degraded" for p in home["provider_status"])


async def test_grid_penalties_pit_lane_unknown_and_no_qualifying_fallback():
    from test_calendar import weekend

    from f1_pitwall.domain.models import Constructor
    from f1_pitwall.services.results import ResultsService

    calendar, jolpica, openf1, seasons = AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock()
    event = weekend()
    calendar.get_event.return_value = event
    svc = ResultsService(seasons, calendar, jolpica, openf1)
    jolpica.results.return_value = [
        RaceResult(
            driver=Driver(id="test", first_name="Test", last_name="Driver"),
            constructor=Constructor(id="test_team", name="Test Team"),
            grid=0,
        )
    ]
    assert (await svc.get_starting_grid(2030, 1))[0].pit_lane is None
    jolpica.results.return_value[0].grid = 15
    assert (await svc.get_starting_grid(2030, 1))[0].position == 15
    jolpica.results.return_value = []
    assert await svc.get_starting_grid(2030, 1) == []
    jolpica.qualifying.assert_not_called()
    event.sessions[-1].provider_id = 2
    event.sessions[-2].provider_id = 1
    openf1.grid.side_effect = [[], []]
    assert await svc.get_starting_grid(2030, 1) == []
    assert [call.args[0].provider_id for call in openf1.grid.call_args_list] == [2, 1]
    openf1.grid.side_effect = ProviderError("openf1", "HTTP 503")
    with pytest.raises(ProviderError):
        await svc.get_starting_grid(2030, 1)


async def test_latest_results_crosses_offseason_without_future_season():
    from test_calendar import weekend

    from f1_pitwall.domain.models import Season
    from f1_pitwall.services.results import ResultsService

    seasons, provider = AsyncMock(), AsyncMock()
    seasons.get_active_season.return_value = Season(year=2031)
    seasons.get_seasons.return_value = [Season(year=y) for y in (2030, 2031, 2032)]
    provider.latest_completed.side_effect = [None, weekend()]
    provider.results.return_value = []
    service = ResultsService(seasons, AsyncMock(), provider, AsyncMock())
    event, _ = await service.get_latest_results()
    assert event.year == 2030
    assert [call.args[0] for call in provider.latest_completed.call_args_list] == [2031, 2030]
