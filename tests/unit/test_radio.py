from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from f1_pitwall.core.config import Settings
from f1_pitwall.domain.replay import LapData, RadioRecord
from f1_pitwall.main import create_app
from f1_pitwall.providers import fastf1 as fastf1_provider
from f1_pitwall.providers.fastf1 import normalize_radio, radio_audio_url
from f1_pitwall.services.replay import ReplayService


@pytest.fixture
def race():
    from test_replay import race as fixture

    return fixture.__wrapped__()


def radio_record(driver_id: str, available_at: float, leader_lap: int | None = None):
    return RadioRecord(
        session_id="f1:2030:1:race",
        driver_id=driver_id,
        available_at=available_at,
        leader_lap=leader_lap,
        race_elapsed_seconds=available_at,
        audio_url=f"https://livetiming.formula1.com/static/race/TeamRadio/{driver_id}-{available_at}.mp3",
    )


def test_fastf1_radio_parsing_identity_order_lap_and_malformed_rows(race):
    records = [
        ("bad", {"Captures": []}),
        (
            "00:03:20.001",
            {
                "Captures": {
                    "2": {
                        "Utc": "2030-01-01T12:03:20.001Z",
                        "RacingNumber": "2",
                        "Path": "TeamRadio/second.mp3",
                    }
                }
            },
        ),
        (
            "00:01:39.999",
            {
                "Captures": [
                    {
                        "Utc": "2030-01-01T12:01:39.999Z",
                        "RacingNumber": "1",
                        "Path": "TeamRadio/first.mp3",
                    },
                    {"RacingNumber": "99", "Path": "TeamRadio/unknown.mp3"},
                    {"RacingNumber": "1", "Path": "../secret.mp3"},
                    {"RacingNumber": "1"},
                ]
            },
        ),
    ]
    result = normalize_radio(
        records,
        {"1": "stable-driver-one", "2": "stable-driver-two"},
        race.event,
        "/static/2030/race/",
        50,
        [
            LapData(driver_id="stable-driver-one", number=1, completed_at=100, available_at=100),
            LapData(driver_id="stable-driver-one", number=2, completed_at=200, available_at=200),
        ],
    )
    assert [(row.driver_id, row.available_at, row.leader_lap) for row in result] == [
        ("stable-driver-one", 99.999, None),
        ("stable-driver-two", 200.001, 2),
    ]
    assert result[0].session_id == "f1:2030:1:race"
    assert result[0].provider_timestamp.isoformat() == "2030-01-01T12:01:39.999000+00:00"
    assert result[0].race_elapsed_seconds == pytest.approx(49.999)
    assert result[0].audio_url.endswith("/static/2030/race/TeamRadio/first.mp3")


def test_radio_url_accepts_only_official_relative_mp3_reference():
    path = "/static/2024/event/Race/"
    assert radio_audio_url(path, "TeamRadio/clip.mp3") == (
        "https://livetiming.formula1.com/static/2024/event/Race/TeamRadio/clip.mp3"
    )
    for unsafe in (
        "https://evil.example/clip.mp3",
        "/TeamRadio/clip.mp3",
        "TeamRadio/../clip.mp3",
        "TeamRadio/clip.wav",
        None,
    ):
        assert radio_audio_url(path, unsafe) is None


def test_missing_optional_radio_stream_does_not_destroy_replay(race, monkeypatch):
    import fastf1

    session = SimpleNamespace(date=datetime(2030, 1, 1), api_path="/static/test/race/")
    monkeypatch.setattr(fastf1, "get_session", lambda *args: session)
    monkeypatch.setattr(fastf1.Cache, "enable_cache", lambda path: None)
    streams = {
        "driver_list": [
            (
                "00:00:00.000",
                {
                    "1": {
                        "FirstName": "Test",
                        "LastName": "Driver",
                        "Reference": "TEST01",
                        "RacingNumber": "1",
                    }
                },
            )
        ],
        "session_status": [
            ("00:00:10.000", {"Status": "Started"}),
            ("00:03:00.000", {"Status": "Finished"}),
        ],
        "timing_data": [("00:01:40.000", {"Lines": {"1": {"NumberOfLaps": 1}}})],
        "timing_app_data": [],
        "track_status": [("00:00:10.000", {"Status": "1"})],
        "lap_count": [("00:00:10.000", {"TotalLaps": 1})],
        "race_control_messages": [],
    }

    def fetch(path, topic, settings):
        if topic == "team_radio":
            raise ValueError("not published")
        return streams[topic]

    monkeypatch.setattr(fastf1_provider, "fetch_stream", fetch)
    provider = fastf1_provider.FastF1Provider(Settings(replay_cache_dir=".cache/fastf1", retries=0))
    result = provider.load(race.event)
    assert result.radio == [] and len(result.laps) == 1


def service_for(race):
    seasons, jolpica, provider = AsyncMock(), AsyncMock(), Mock()
    jolpica.calendar.return_value = [race.event]
    provider.load.return_value = race
    return ReplayService(seasons, jolpica, provider)


async def test_radio_cutoff_filter_driver_limit_and_empty_state(race):
    race.radio = [
        radio_record("d1", 1999.999, 19),
        radio_record("d2", 2000, 20),
        radio_record("d1", 2000.001, 20),
    ]
    service = service_for(race)
    feed = await service.get_radio(2030, 1, 20)
    assert [row.available_at for row in feed.messages] == [1999.999, 2000]
    assert feed.causal_cutoff == 2000
    assert feed.total_available == 2
    assert (await service.get_radio(2030, 1, 20, "d1")).messages == [race.radio[0]]
    assert (await service.get_radio(2030, 1, 20, limit=1)).messages == [race.radio[1]]
    race.radio = []
    assert (await service.get_radio(2030, 1, 20)).messages == []


async def test_radio_future_mutation_and_removal_cannot_change_earlier_feed(race):
    race.radio = [radio_record("d1", 1900, 19), radio_record("d2", 2100, 21)]
    service = service_for(race)
    baseline = await service.get_radio(2030, 1, 20)
    race.radio[1].available_at = 99999
    assert await service.get_radio(2030, 1, 20) == baseline
    race.radio.pop()
    assert await service.get_radio(2030, 1, 20) == baseline


async def test_radio_api_routes_validation_and_race_state_is_independent(race):
    race.radio = [radio_record("d1", 1900, 19), radio_record("d2", 1950, 19)]
    app = create_app()
    async with app.router.lifespan_context(app):
        app.state.hub.replay.load_race = AsyncMock(return_value=race)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            all_feed = await client.get("/api/v1/replay/2030/1/20/radio?limit=1")
            assert all_feed.status_code == 200
            assert [row["driver_id"] for row in all_feed.json()["messages"]] == ["d2"]
            driver_feed = await client.get("/api/v1/replay/2030/1/20/drivers/d1/radio")
            assert driver_feed.status_code == 200
            assert driver_feed.json()["driver_id"] == "d1"
            assert len(driver_feed.json()["messages"]) == 1
            missing = await client.get("/api/v1/replay/2030/1/20/drivers/missing/radio")
            assert missing.status_code == 404
            assert (await client.get("/api/v1/replay/2030/1/999/radio")).status_code == 404
            assert (await client.get("/api/v1/replay/2030/1/20/radio?limit=0")).status_code == 422
            state = await client.get("/api/v1/replay/2030/1/20")
            assert state.status_code == 200 and state.json()["current_lap"] == 20
