from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from f1_pitwall.core.exceptions import NotFound, ProviderError
from f1_pitwall.domain.enums import SessionType
from f1_pitwall.domain.models import Event, NextSession, Session
from f1_pitwall.providers.jolpica import Jolpica
from f1_pitwall.providers.openf1 import OpenF1
from f1_pitwall.services.season import SeasonService


class CalendarService:
    def __init__(
        self,
        seasons: SeasonService,
        jolpica: Jolpica,
        openf1: OpenF1,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.seasons, self.jolpica, self.openf1, self.clock = seasons, jolpica, openf1, clock

    async def get_calendar(self, year: int) -> list[Event]:
        await self.seasons.get_season(year)
        events = await self.jolpica.calendar(year)
        try:
            weekends = await self.openf1.weekends(year)
        except ProviderError as exc:
            for event in events:
                event.warnings.append(f"OpenF1 enrichment unavailable: {exc.message}")
            return events
        return [self.openf1.enrich(event, weekends) for event in events]

    async def get_event(self, year: int, round: int) -> Event:
        for event in await self.get_calendar(year):
            if event.round == round:
                return event
        raise NotFound(f"Event {year}/{round} is not available")

    async def get_event_schedule(self, year: int, round: int) -> list[Session]:
        return (await self.get_event(year, round)).sessions

    async def relevant_events(self) -> list[Event]:
        now = self.clock()
        available = await self.seasons.get_seasons()
        # Include the previous year at the boundary, and every published future season.
        years = [s.year for s in available if s.year >= (now - timedelta(days=7)).year]
        events = []
        for year in years:
            events.extend(await self.get_calendar(year))
        return sorted(events, key=lambda e: (e.race_date, e.round))

    async def event_state(self, event: Event, now: datetime) -> str:
        race = next((s for s in event.sessions if s.type == SessionType.RACE), None)
        if race and race.cancelled:
            return "cancelled"
        if race and race.end and now >= race.end:
            return "finished"
        starts = [s.start for s in event.sessions if s.start and not s.cancelled]
        dates = [s.date for s in event.sessions if not s.cancelled]
        if dates and now.date() < min(dates):
            return "upcoming"
        if starts and now < min(starts):
            return "upcoming"
        if race and race.start and now >= race.start:
            if await self.jolpica.results(event.year, event.round):
                return "finished"
            if race.end:
                return "in_progress"
            return "end_unknown"
        if starts and now >= min(starts):
            return "in_progress"
        return "schedule_unknown"

    async def get_current_event(self, events: list[Event] | None = None) -> Event | None:
        now = self.clock()
        for event in events if events is not None else await self.relevant_events():
            dates = [s.date for s in event.sessions]
            # Date window is a candidate filter, never a fabricated session end.
            if dates and min(dates) <= now.date() <= event.race_date + timedelta(days=1):
                state = await self.event_state(event, now)
                if state in ("in_progress", "end_unknown"):
                    return event
        return None

    async def get_next_event(self, events: list[Event] | None = None) -> Event | None:
        now = self.clock()
        for event in events if events is not None else await self.relevant_events():
            if event.race_date < now.date():
                continue
            if await self.event_state(event, now) in ("upcoming", "in_progress", "end_unknown"):
                return event
        return None

    async def get_next_session(self, events: list[Event] | None = None) -> NextSession | None:
        now = self.clock()
        candidates = [
            (s.start, e, s)
            for e in (events if events is not None else await self.relevant_events())
            if not any(s.type == SessionType.RACE and s.cancelled for s in e.sessions)
            for s in e.sessions
            if s.start and s.start > now and not s.cancelled
        ]
        if not candidates:
            return None
        start, event, session = min(candidates, key=lambda value: value[0])
        return NextSession(
            event=event, session=session, seconds_until_start=(start - now).total_seconds()
        )
