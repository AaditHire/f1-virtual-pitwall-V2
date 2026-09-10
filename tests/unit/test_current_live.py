import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from f1_pitwall.core.exceptions import ProviderError
from f1_pitwall.domain.live import LiveSession, LiveTimingBundle
from f1_pitwall.domain.pitwall import PitWallSnapshot
from f1_pitwall.services.live import LiveService, freshness


def live_fixture(now: datetime, *, tyres=True, weather=True, positions=True):
    session = LiveSession(
        session_key=99,
        meeting_key=9,
        name="Race",
        type="Race",
        start=now - timedelta(minutes=8),
        end=now + timedelta(hours=1),
        year=2030,
        location="Test",
    )
    drivers = [
        {
            "driver_number": number,
            "full_name": name,
            "name_acronym": code,
            "team_name": "Test Team",
        }
        for number, name, code in ((1, "One Driver", "ONE"), (2, "Two Driver", "TWO"))
    ]
    laps = []
    for lap in range(1, 5):
        for number, offset in ((1, 0), (2, 2)):
            laps.append(
                {
                    "driver_number": number,
                    "lap_number": lap,
                    "date_start": now - timedelta(seconds=(5 - lap) * 90 + offset + 10),
                    "lap_duration": 88 + offset,
                }
            )
    stamp = now - timedelta(seconds=12)
    return LiveTimingBundle(
        session=session,
        retrieved_at=now,
        drivers=drivers,
        positions=[
            {"driver_number": 1, "position": 1, "date": stamp},
            {"driver_number": 2, "position": 2, "date": stamp},
        ]
        if positions
        else [],
        intervals=[
            {"driver_number": 1, "gap_to_leader": 0, "interval": None, "date": stamp},
            {"driver_number": 2, "gap_to_leader": 2, "interval": 2, "date": stamp},
        ],
        laps=laps,
        stints=[
            {
                "driver_number": number,
                "stint_number": 1,
                "lap_start": 1,
                "compound": "MEDIUM",
                "tyre_age_at_start": 0,
            }
            for number in (1, 2)
        ]
        if tyres
        else [],
        track_status=[{"date": now - timedelta(seconds=5), "status": "AllClear"}],
        weather=[
            {
                "date": now - timedelta(seconds=20),
                "air_temperature": 25,
                "track_temperature": 36,
                "humidity": 55,
                "rainfall": 0,
                "wind_speed": 3,
            }
        ]
        if weather
        else [],
        race_control=[{"date": now - timedelta(seconds=5), "category": "Flag", "flag": "GREEN"}],
    )


def service(now, bundle):
    from test_calendar import weekend

    event = weekend()
    event.year = 2030
    event.sessions[-1].provider_id = 99
    calendar = AsyncMock()
    calendar.get_calendar.return_value = [event]
    provider = AsyncMock()
    provider.name = "test-live"
    provider.get_current_session.return_value = bundle.session if bundle else None
    if bundle:
        for name in (
            "drivers",
            "positions",
            "intervals",
            "laps",
            "stints",
            "pit_stops",
            "track_status",
            "weather",
            "race_control",
        ):
            getattr(provider, f"get_{name}").return_value = getattr(bundle, name)
    return LiveService(provider, calendar, AsyncMock(), AsyncMock(), clock=lambda: now)


def test_freshness_boundaries_and_no_scheduled_live_inference():
    now = datetime(2030, 6, 7, 10, tzinfo=UTC)
    assert freshness("test", now, now - timedelta(seconds=30)).state == "FRESH"
    assert freshness("test", now, now - timedelta(seconds=31)).state == "DELAYED"
    assert freshness("test", now, now - timedelta(seconds=121)).state == "STALE"
    bundle = live_fixture(now)
    bundle.positions = bundle.intervals = bundle.laps = bundle.track_status = []
    bundle.weather = bundle.race_control = []
    assert service(now, bundle).session_status(bundle) == "UNKNOWN"


async def test_live_normalizes_observed_full_grid_without_fabrication():
    now = datetime(2030, 6, 7, 10, tzinfo=UTC)
    bundle = live_fixture(now)
    live = service(now, bundle)
    history = await live.history(bundle)
    assert history and {p.driver.id for p in history.participants} == {"one", "two"}
    response = await live.race()
    assert response.live and response.availability == "LIVE_AVAILABLE"
    assert response.lap == 4 and len(response.drivers) == 2
    assert response.race_state.drivers[0].position == 1
    assert response.race_state.drivers[0].compound == "MEDIUM"
    assert response.weather.air_temperature_c == 25
    assert response.track_status == "GREEN"
    assert response.freshness.provider_timestamp == now - timedelta(seconds=5)


@pytest.mark.parametrize("missing", ["tyres", "weather", "positions"])
async def test_partial_live_data_is_explicit(missing):
    now = datetime(2030, 6, 7, 10, tzinfo=UTC)
    bundle = live_fixture(
        now,
        tyres=missing != "tyres",
        weather=missing != "weather",
        positions=missing != "positions",
    )
    live = service(now, bundle)
    race = await live.race()
    if missing == "weather":
        assert race.weather is None
    elif missing == "positions":
        assert "positions" in race.missing_requirements
        assert all(row.position is None for row in race.race_state.drivers)
    else:
        assert all(row.compound is None for row in race.race_state.drivers)
        pitwall = await live.current_pitwall(AsyncMock())
        assert pitwall.analysis_status == "PARTIAL" and "stints" in pitwall.missing_requirements


async def test_session_not_live_and_provider_unavailable_fail_closed():
    now = datetime(2030, 6, 7, 10, tzinfo=UTC)
    bundle = live_fixture(now)
    bundle.session.end = now - timedelta(minutes=20)
    bundle.track_status[0]["date"] = now - timedelta(minutes=10)
    live = service(now, bundle)
    status = await live.status()
    assert not status.live and status.availability == "HISTORICAL_ONLY"
    assert (await live.current_pitwall(AsyncMock())).analysis_status != "AVAILABLE"
    live.provider.get_current_session.side_effect = ProviderError("test-live", "HTTP 403")
    status = await live.status()
    assert status.availability == "UNAVAILABLE" and "403" in status.reason


async def test_current_pitwall_uses_existing_service():
    now = datetime(2030, 6, 7, 10, tzinfo=UTC)
    live = service(now, live_fixture(now))
    pitwall = AsyncMock()
    pitwall.snapshot_history.return_value = PitWallSnapshot(
        event=(await live.history(live_fixture(now))).event,
        lap=4,
        cutoff_seconds=1,
        race_status="active",
        trajectory_count=100,
        drivers=[],
    )
    result = await live.current_pitwall(pitwall)
    assert result.analysis_status == "AVAILABLE"
    assert result.strategy_status == "EXPERIMENTAL"
    pitwall.snapshot_history.assert_awaited_once()


def test_recorded_recent_live_archive_compatibility():
    path = Path(__file__).parents[2] / "docs/phase7-live-historical-consistency.json"
    report = json.loads(path.read_text())
    assert report["event"] == "Italian Grand Prix" and report["season"] == 2026
    assert report["common_drivers"] == report["live_drivers"] == report["archive_drivers"] == 22
    for field in ("position", "laps_completed", "compound", "stint_number", "pit_stops_completed"):
        assert report["matches"][field] == report["common_drivers"]
        assert report["missing_live"][field] == 0
