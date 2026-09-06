from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from f1_pitwall.api.routes import present
from f1_pitwall.domain.enums import SessionType
from f1_pitwall.domain.models import Circuit, Event, Session
from f1_pitwall.services.calendar import CalendarService


def weekend(sprint=False, shift=0):
    start = datetime(2030, 6, 7, 10, tzinfo=UTC) + timedelta(days=shift)
    types = [SessionType.PRACTICE_1, SessionType.QUALIFYING, SessionType.RACE]
    if sprint:
        types = [
            SessionType.PRACTICE_1,
            SessionType.SPRINT_QUALIFYING,
            SessionType.SPRINT,
            SessionType.QUALIFYING,
            SessionType.RACE,
        ]
    sessions = [
        Session(
            type=kind,
            name=kind.value,
            date=(start + timedelta(hours=i * 6)).date(),
            start=start + timedelta(hours=i * 6),
            end=start + timedelta(hours=i * 6 + 1),
            source="test",
        )
        for i, kind in enumerate(types)
    ]
    return Event(
        year=2030,
        round=shift + 1,
        name="Test Grand Prix",
        circuit=Circuit(id="test", name="Test Circuit"),
        race_date=sessions[-1].date,
        sessions=sessions,
    )


def service(now):
    return CalendarService(AsyncMock(), AsyncMock(), AsyncMock(), lambda: now)


@pytest.mark.parametrize("sprint", [False, True])
async def test_next_session_at_every_boundary(sprint):
    event = weekend(sprint)
    for index, session in enumerate(event.sessions):
        svc = service(session.start - timedelta(seconds=1))
        upcoming = await svc.get_next_session([event])
        assert upcoming.session == session
        assert upcoming.seconds_until_start == 1
        svc.clock = lambda session=session: session.end
        upcoming = await svc.get_next_session([event])
        assert (upcoming.session if upcoming else None) == (
            event.sessions[index + 1] if index + 1 < len(event.sessions) else None
        )


async def test_current_next_and_finished_weekend():
    event, following = weekend(), weekend(shift=7)
    svc = service(event.sessions[1].end)
    assert await svc.get_current_event([event, following]) == event
    assert await svc.get_next_event([event, following]) == event
    assert (await svc.get_next_session([event, following])).session.type == SessionType.RACE
    svc.clock = lambda: event.sessions[-1].end
    assert await svc.get_current_event([event, following]) is None
    assert await svc.get_next_event([event, following]) == following


async def test_in_progress_session_not_next_and_cancelled_skipped():
    event = weekend(True)
    event.sessions[2].cancelled = True
    svc = service(event.sessions[1].start)
    assert (await svc.get_next_session([event])).session.type == SessionType.QUALIFYING


async def test_unknown_end_and_published_results():
    event = weekend()
    race = event.sessions[-1]
    race.end = None
    svc = service(race.start + timedelta(minutes=30))
    svc.jolpica.results.return_value = []
    assert await svc.event_state(event, svc.clock()) == "end_unknown"
    svc.jolpica.results.return_value = [object()]
    assert await svc.event_state(event, svc.clock()) == "finished"


async def test_no_fabricated_time_and_offseason():
    event = weekend()
    for session in event.sessions:
        session.start = session.end = None
    svc = service(datetime(2030, 1, 1, tzinfo=UTC))
    assert await svc.get_next_session([event]) is None
    assert await svc.event_state(event, svc.clock()) == "upcoming"
    assert await svc.get_next_event([event]) == event
    svc.clock = lambda: datetime.combine(event.race_date, datetime.min.time(), tzinfo=UTC)
    assert await svc.event_state(event, svc.clock()) == "schedule_unknown"
    assert await svc.get_current_event([]) is None
    assert await svc.get_next_event([]) is None


async def test_cancelled_event_has_no_next_session():
    event = weekend()
    event.sessions[-1].cancelled = True
    svc = service(event.sessions[0].start - timedelta(days=1))
    assert await svc.get_next_session([event]) is None
    assert await svc.get_next_event([event]) is None


async def test_published_future_season_is_considered():
    svc = service(datetime(2030, 12, 31, tzinfo=UTC))
    from f1_pitwall.domain.models import Season

    svc.seasons.get_seasons.return_value = [Season(year=2021), Season(year=2030), Season(year=2031)]
    svc.get_calendar = AsyncMock(return_value=[])
    await svc.relevant_events()
    assert [call.args[0] for call in svc.get_calendar.call_args_list] == [2030, 2031]


def test_timezone_conversion_keeps_utc_and_handles_dst():
    event = weekend()
    output = present(event, ZoneInfo("Asia/Kolkata"))
    assert output["sessions"][0]["start"].endswith("Z")
    assert output["sessions"][0]["start_local"].endswith("+05:30")
    assert event.sessions[0].start.utcoffset() == timedelta(0)
    assert present(event, ZoneInfo("Europe/London"))["sessions"][0]["start_local"].endswith(
        "+01:00"
    )


def test_naive_time_rejected_and_offsets_normalized():
    with pytest.raises(ValidationError):
        Session(
            type="Race", name="Race", date="2030-01-01", start="2030-01-01T12:00:00", source="test"
        )
    s = Session(
        type="Race",
        name="Race",
        date="2030-01-01",
        start="2030-01-01T12:00:00+05:30",
        source="test",
    )
    assert s.start.hour == 6 and s.start.minute == 30
