import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from f1_pitwall.core.exceptions import NotFound, ProviderError
from f1_pitwall.providers.fastf1 import (
    gap,
    normalize_lap_revisions,
    normalize_timing,
    normalize_tyres,
    roster,
)
from f1_pitwall.services.replay import ReplayService


@pytest.mark.parametrize(
    "value,expected",
    [
        ("+3.405", (3.405, 0)),
        ("+1 LAP", (None, 1)),
        ("+2 LAPS", (None, 2)),
        ("1 L", (None, 1)),
        ("LAP 25", (None, None)),
        ("", (None, None)),
        (None, (None, None)),
    ],
)
def test_gap_parser(value, expected):
    assert gap(value) == expected


def test_normalize_timing_uses_atomic_completed_laps_and_timed_retirement():
    records = [
        ("00:00:00.000", {"Lines": {"9": {"InPit": True, "Retired": False}}}),
        ("00:00:02.000", {"Lines": {"9": {"InPit": False}}}),
        ("00:01:40.000", {"Lines": {"9": {"NumberOfLaps": 1}}}),
        ("00:01:42.000", {"Lines": {"9": {"LastLapTime": {"Value": "1:39.000"}}}}),
        (
            "00:03:20.000",
            {"Lines": {"9": {"NumberOfLaps": 2, "LastLapTime": {"Value": "1:40.000"}}}},
        ),
        ("00:03:25.000", {"Lines": {"9": {"InPit": True}}}),
        ("00:03:40.000", {"Lines": {"9": {"InPit": False}}}),
        ("00:05:00.000", {"Lines": {"9": {"Retired": True}}}),
    ]
    timing, laps, pits = normalize_timing(records, {"9": "stable"}, 1)
    assert laps[0].lap_time_seconds is None  # Ambiguous later packet must not repair history.
    assert laps[1].lap_time_seconds == 100
    assert len(pits) == 1 and pits[0].exited_at == 220
    assert next(t.at for t in timing if t.retired) == 300


def test_tyre_observations_keep_used_life_and_do_not_backfill():
    records = [
        ("00:00:01.000", {"Lines": {"9": {"Stints": [{"Compound": "SOFT", "TotalLaps": 3}]}}}),
        ("00:00:10.000", {"Lines": {"9": {"Stints": {"0": {"TotalLaps": 4}}}}}),
        ("00:00:20.000", {"Lines": {"9": {"Stints": {"1": {"Compound": "HARD", "TotalLaps": 0}}}}}),
    ]
    stints, _ = normalize_tyres(records, {"9": "stable"})
    assert [(s.number, s.compound, s.tyre_age) for s in stints] == [
        (1, "SOFT", 3),
        (1, "SOFT", 4),
        (2, "HARD", 0),
    ]


def test_numbered_lap_revision_is_only_available_when_published():
    from f1_pitwall.domain.replay import LapData

    crossing = LapData(driver_id="stable", number=1, completed_at=100, available_at=100)
    updates = [
        (
            "00:01:42.000",
            {"Lines": {"9": {"Stints": {"0": {"LapNumber": 1, "LapTime": "1:39.000"}}}}},
        )
    ]
    revisions = normalize_lap_revisions(updates, {"9": "stable"}, [crossing])
    assert crossing.lap_time_seconds is None
    assert revisions[0].lap_time_seconds == 99 and revisions[0].available_at == 102


def test_roster_is_session_specific_prerace_and_identity_not_number():
    rows = [
        (
            "00:00:01.000",
            {"99": {"FirstName": "Test", "LastName": "Driver", "Reference": "TEST01"}},
        ),
        ("00:10:00.000", {"2": {"FirstName": "Future", "LastName": "Entry", "Reference": "OTHER"}}),
    ]
    participants, ids = roster(rows, 10)
    assert len(participants) == 1 and ids == {"99": "f1:TEST01"}


async def test_service_single_load_and_missing_driver():
    from test_replay import race as fixture

    race = fixture.__wrapped__()
    seasons, jolpica, provider = AsyncMock(), AsyncMock(), Mock()
    jolpica.calendar.return_value = [race.event]
    provider.load.return_value = race
    service = ReplayService(seasons, jolpica, provider)
    states = await asyncio.gather(*(service.get_race_state(2030, 1, 20) for _ in range(3)))
    assert states[0] == states[1] == states[2]
    provider.load.assert_called_once()
    with pytest.raises(NotFound):
        await service.get_driver_state(2030, 1, 20, "not_a_participant")
    provider.load.assert_called_once()


async def test_failed_load_is_not_cached():
    from test_replay import race as fixture

    race = fixture.__wrapped__()
    seasons, jolpica, provider = AsyncMock(), AsyncMock(), Mock()
    jolpica.calendar.return_value = [race.event]
    provider.load.side_effect = [ProviderError("fastf1", "temporary failure"), race]
    service = ReplayService(seasons, jolpica, provider)
    with pytest.raises(ProviderError):
        await service.load_race(2030, 1)
    assert await service.load_race(2030, 1) == race
    assert provider.load.call_count == 2
