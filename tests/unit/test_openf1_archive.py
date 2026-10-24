from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from f1_pitwall.core.config import Settings
from f1_pitwall.core.exceptions import ProviderError
from f1_pitwall.domain.models import Circuit, Event
from f1_pitwall.providers.http import ProviderHTTP
from f1_pitwall.providers.openf1 import OpenF1
from f1_pitwall.providers.openf1_archive import OpenF1Archive, normalize_openf1_race
from f1_pitwall.services.race_state import RaceStateBuilder
from f1_pitwall.services.replay import ReplayService

ORIGIN = datetime(2026, 10, 11, 12, tzinfo=UTC)
EVENT = Event(
    year=2026,
    round=17,
    name="Singapore Grand Prix",
    race_date=date(2026, 10, 11),
    circuit=Circuit(id="marina_bay", name="Marina Bay Street Circuit"),
)
NOR, HAM, LEC = "f1:LANNOR01", "f1:LEWHAM01", "f1:CHALEC01"


def t(seconds: float) -> str:
    return (ORIGIN + timedelta(seconds=seconds)).isoformat()


def lap(car, number, start, duration, **extra):
    return {
        "driver_number": car,
        "lap_number": number,
        "date_start": t(start),
        "lap_duration": duration,
        **extra,
    }


def control(at, message, category="Other", **extra):
    return {"date": t(at), "category": category, "message": message, **extra}


def rows():
    headshot = "https://media.formula1.com/drivers/L/LANNOR01_Lando_Norris/lannor01.png"
    return {
        "drivers": [
            {
                "driver_number": 1,
                "full_name": "Lando NORRIS",
                "first_name": "Lando",
                "last_name": "Norris",
                "name_acronym": "NOR",
                "team_name": "McLaren",
                "headshot_url": headshot,
            },
            {"driver_number": 44, "first_name": "Lewis", "last_name": "Hamilton"},
            {"driver_number": 16, "first_name": "Charles", "last_name": "Leclerc"},
        ],
        "laps": [
            lap(1, 1, 60, 100),
            lap(1, 2, 160, 95),
            lap(1, 3, 255, 94),
            lap(44, 1, 60, 101),
            lap(44, 2, 161, 110),
            lap(44, 3, 271, 96, is_pit_out_lap=True),
            lap(16, 1, 60, 102),
            lap(16, 2, 162, None),  # stopped on track during lap 2
        ],
        "position": [
            {"driver_number": 44, "date": t(-1000), "position": 1},
            {"driver_number": 1, "date": t(-1000), "position": 2},
            {"driver_number": 16, "date": t(-1000), "position": 3},
            {"driver_number": 1, "date": t(300), "position": 1},
            {"driver_number": 44, "date": t(300), "position": 2},
        ],
        "intervals": [
            {"driver_number": 16, "date": t(150), "gap_to_leader": "+1 LAP", "interval": 5.5},
            {"driver_number": 44, "date": t(300), "gap_to_leader": 3.2, "interval": 3.2},
        ],
        "stints": [
            {
                "driver_number": 44,
                "stint_number": 1,
                "lap_start": 1,
                "lap_end": 2,
                "compound": "MEDIUM",
                "tyre_age_at_start": 0,
            },
            {
                "driver_number": 44,
                "stint_number": 2,
                "lap_start": 3,
                "lap_end": 3,
                "compound": "HARD",
                "tyre_age_at_start": 2,
            },
        ],  # fmt: skip
        "pit": [{"driver_number": 44, "date": t(285), "lap_number": 2, "lane_duration": 20.0}],
        "race_control": [
            control(-2400, "GREEN LIGHT - PIT EXIT OPEN", "Flag", flag="GREEN", scope="Track"),
            control(60, "SESSION STARTED", "SessionStatus"),
            control(
                100, "YELLOW IN TRACK SECTOR 5", "Flag", flag="YELLOW", scope="Sector", sector=5
            ),
            control(110, "CLEAR IN TRACK SECTOR 5", "Flag", flag="CLEAR", scope="Sector", sector=5),
            control(200, "VSC DEPLOYED", "SafetyCar"),
            control(230, "VSC ENDING", "SafetyCar"),
            control(240, "TRACK CLEAR", "Flag", flag="CLEAR", scope="Track"),
            control(
                270,
                "CAR 1 (NOR) TIME 1:35.000 DELETED - TRACK LIMITS AT TURN 4 LAP 2",
                driver_number=1,
            ),
            control(349, "CHEQUERED FLAG", "Flag", flag="CHEQUERED", scope="Track"),
        ],  # fmt: skip
    }


RESULTS = [
    {"driver_number": 1, "position": 1, "number_of_laps": 3},
    {"driver_number": 44, "position": 2, "number_of_laps": 3},
    {"driver_number": 16, "position": None, "number_of_laps": 1, "dnf": True},
]


@pytest.fixture
def race():
    return normalize_openf1_race(EVENT, ORIGIN, rows(), RESULTS)


def test_laps_end_where_the_next_lap_starts_and_unfinished_laps_are_dropped(race):
    assert race.started_at == 60
    assert race.source == "openf1-historical"
    laps = sorted((r.driver_id, r.number, r.completed_at, r.lap_time_seconds) for r in race.laps)
    assert laps == [
        (LEC, 1, 162, 102),
        (NOR, 1, 160, 100),
        (NOR, 2, 255, 95),
        (NOR, 3, 349, 94),
        (HAM, 1, 161, 101),
        (HAM, 2, 271, 110),
        (HAM, 3, 367, 96),
    ]  # fmt: skip
    assert all(r.available_at == r.completed_at for r in race.laps)


def test_roster_grid_gaps_and_retirement(race):
    assert [p.driver.id for p in race.participants] == [LEC, NOR, HAM]
    grid = {s.driver_id: s.grid_position for s in race.timing if s.grid_position is not None}
    assert grid == {HAM: 1, NOR: 2, LEC: 3}
    lapped = next(s for s in race.timing if s.driver_id == LEC and s.laps_behind is not None)
    assert (lapped.laps_behind, lapped.gap_to_leader, lapped.gap_to_ahead) == (1, None, 5.5)
    # Out once overdue on the unfinished lap (start 162 + 1.5 x the 102 s lap), not at lap 1.
    assert [s.at for s in race.timing if s.retired] == [315]


def test_pit_stops_and_tyres_are_dated_on_track(race):
    assert [(p.entered_at, p.exited_at) for p in race.pit_stops] == [(265, 285)]
    assert [(s.number, s.compound, s.observed_at, s.tyre_age) for s in race.stints] == [
        (1, "MEDIUM", 60, 0),
        (1, "MEDIUM", 161, 1),
        (1, "MEDIUM", 271, 2),
        (2, "HARD", 285, 2),
        (2, "HARD", 367, 3),
    ]


def test_track_status_is_rebuilt_from_race_control(race):
    statuses = [(c.at, c.track_status) for c in race.control if c.track_status]
    assert statuses == [(-2400, "1"), (100, "2"), (110, "1"), (200, "6"), (230, "7"), (240, "1")]
    assert [c.total_scheduled_laps for c in race.control if c.total_scheduled_laps] == [3]
    assert [(v.driver_id, v.lap_number, v.at, v.valid) for v in race.lap_validity] == [
        (NOR, 2, 270, False)
    ]


def test_race_state_uses_only_what_was_known_at_each_lap(race):
    builder = RaceStateBuilder()
    lap2 = {d.driver.id: d for d in builder.build(race, 2).drivers}
    assert lap2[LEC].status == "active"  # retirement is not visible before it happened
    assert lap2[HAM].status == "active" and lap2[HAM].compound == "MEDIUM"
    lap3 = builder.build(race, 3)
    drivers = {d.driver.id: d for d in lap3.drivers}
    assert lap3.total_scheduled_laps == 3 and lap3.track.track_status == "1"
    assert drivers[LEC].status == "retired"
    assert drivers[HAM].pit_stops_completed == 1
    assert (drivers[HAM].compound, drivers[HAM].tyre_age) == ("HARD", 2)
    assert [d.driver.id for d in lap3.drivers[:2]] == [NOR, HAM]


def test_gaps_in_openf1_data_are_filled_from_the_laps():
    data = rows()
    data["pit"] = []  # some early-2023 races have no pit rows
    data["laps"][3]["date_start"] = None  # nor a start time on every opening lap
    data["laps"][0]["date_start"] = t(3000)  # and some are misdated
    data["laps"][6:] = [lap(16, 1, 60, None)]  # stopped on lap 1
    data["laps"][2] = lap(1, 3, 255, None)  # and stopped on the final lap
    results = [*RESULTS[1:], {**RESULTS[0], "dnf": True}]
    race = normalize_openf1_race(EVENT, ORIGIN, data, results)
    assert (HAM, 1, 161) in [(r.driver_id, r.number, r.completed_at) for r in race.laps]
    assert [(p.driver_id, p.entered_at, p.exited_at) for p in race.pit_stops] == [(HAM, 271, 271)]
    retired = sorted((s.driver_id, s.at) for s in race.timing if s.retired)
    # Overdue by the field's typical lap, but never shown out after the flag.
    assert retired == [(LEC, 60 + 1.5 * 100), (NOR, 367)]
    lap3 = {d.driver.id: d for d in RaceStateBuilder().build(race, 3).drivers}
    assert lap3[HAM].pit_stops_completed == 1 and lap3[HAM].compound == "HARD"


def test_unfinished_race_is_rejected():
    data = rows()
    data["race_control"] = [r for r in data["race_control"] if "CHEQUERED" not in r["message"]]
    with pytest.raises(ValueError, match="not completed"):
        normalize_openf1_race(EVENT, ORIGIN, data, [])


async def test_archive_finds_the_race_session_by_date(monkeypatch):
    monkeypatch.setattr(ProviderHTTP, "_pace", AsyncMock())
    data, requested = rows(), []

    def handler(request):
        path = request.url.path.rsplit("/", 1)[-1]
        requested.append((path, dict(request.url.params)))
        if path == "sessions":
            return httpx.Response(
                200,
                json=[
                    {"session_key": 1, "date_start": "2026-10-04T07:00:00+00:00"},
                    {"session_key": 11388, "date_start": t(0)},
                ],
            )
        if path == "session_result":
            return httpx.Response(200, json=RESULTS)
        return httpx.Response(200, json=data[path])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        archive = OpenF1Archive(OpenF1(ProviderHTTP("openf1", client, Settings())))
        race = await archive.load(EVENT)
    assert len(race.laps) == 7
    assert requested[0] == ("sessions", {"year": "2026", "session_name": "Race"})
    assert {params.get("session_key") for _, params in requested[1:]} == {"11388"}


async def test_archive_errors_are_provider_errors(monkeypatch):
    monkeypatch.setattr(ProviderHTTP, "_pace", AsyncMock())
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
    async with httpx.AsyncClient(transport=transport) as client:
        archive = OpenF1Archive(OpenF1(ProviderHTTP("openf1", client, Settings())))
        with pytest.raises(ProviderError, match="no race session"):
            await archive.load(EVENT)


def service(archive_result, fastf1_result, year=2026):
    event = EVENT.model_copy(update={"year": year})
    seasons, jolpica, provider, archive = AsyncMock(), AsyncMock(), Mock(), AsyncMock()
    jolpica.calendar.return_value = [event]
    archive.covers = Mock(side_effect=OpenF1Archive.covers)
    for mock, result in ((archive.load, archive_result), (provider.load, fastf1_result)):
        if isinstance(result, Exception):
            mock.side_effect = result
        else:
            mock.return_value = result
    return ReplayService(seasons, jolpica, provider, archive=archive), archive, provider


async def test_replay_prefers_openf1_for_recent_seasons(race):
    replay, archive, provider = service(race, ProviderError("fastf1", "HTTP 401"))
    assert await replay.load_race(2026, 17) == race
    provider.load.assert_not_called()


async def test_replay_falls_back_to_fastf1_and_reports_both_failures(race):
    replay, _, provider = service(ProviderError("openf1", "down"), race)
    assert await replay.load_race(2026, 17) == race
    replay, _, _ = service(ProviderError("openf1", "down"), ProviderError("fastf1", "HTTP 401"))
    with pytest.raises(ProviderError, match=r"down \(OpenF1\); HTTP 401 \(FastF1\)"):
        await replay.load_race(2026, 17)


async def test_older_seasons_skip_openf1(race):
    replay, archive, provider = service(ProviderError("openf1", "unused"), race, year=2022)
    assert await replay.load_race(2022, 17) == race
    archive.load.assert_not_called()
