"""One-stop aggregation of existing current-season services."""

import asyncio
from datetime import UTC, datetime

from pydantic import ValidationError

from f1_pitwall.core.exceptions import NotFound, ProviderError
from f1_pitwall.domain.enums import SessionType
from f1_pitwall.domain.live import SessionState, WeekendState


class CurrentWeekendService:
    def __init__(
        self, seasons, calendar, results, standings, news, live, provider_status, clock=None
    ):
        self.seasons, self.calendar, self.results = seasons, calendar, results
        self.standings, self.news, self.live = standings, news, live
        self.provider_status = provider_status
        self.clock = clock or (lambda: datetime.now(UTC))

    async def current(self) -> WeekendState:
        result = WeekendState()

        async def optional(name, call, default=None):
            try:
                return await call()
            except (ProviderError, NotFound, ValidationError) as exc:
                result.errors[name] = str(exc)
                return default

        season = await optional("season", self.seasons.get_active_season)
        result.season = season.year if season else None
        events = await optional("calendar", self.calendar.relevant_events, [])
        next_session = await optional(
            "next_session", lambda: self.calendar.get_next_session(events), None
        )
        live_status = await self.live.status(next_session)
        event = live_status.event if live_status.session_status in {"LIVE", "DELAYED"} else None
        if event is None:
            event = await optional("current_event", lambda: self.calendar.get_current_event(events))
        if event is None:
            event = await optional("next_event", lambda: self.calendar.get_next_event(events))
        result.event, result.next_session = event, next_session
        if event is None:
            result.provider_status = self.provider_status()
            return result
        result.circuit = event.circuit.name
        result.country = event.circuit.country
        result.round = event.round
        result.weekend_format = (
            "SPRINT" if any(s.type == SessionType.SPRINT for s in event.sessions) else "STANDARD"
        )
        now = self.clock()
        states = []
        for session in event.sessions:
            if (
                live_status.session
                and session.provider_id == live_status.session.provider_id
                and live_status.session_status in {"LIVE", "DELAYED"}
            ):
                status = live_status.session_status
            elif session.end and now >= session.end:
                status = "COMPLETED"
            elif session.start and now < session.start:
                status = "UPCOMING"
            else:
                status = "UNKNOWN"
            states.append(SessionState(session=session, status=status))
        result.session_schedule = states
        result.completed_sessions = [row.session for row in states if row.status == "COMPLETED"]
        result.active_session = next(
            (row.session for row in states if row.status in {"LIVE", "DELAYED"}), None
        )
        result.status = (
            live_status.session_status
            if result.active_session
            else "UPCOMING"
            if next_session and next_session.event.round == event.round
            else "COMPLETED"
            if states and all(row.status == "COMPLETED" for row in states)
            else "UNKNOWN"
        )
        if result.completed_sessions:
            result.session_results = await optional(
                "session_results",
                lambda: self.live.weekend_session_results(result.completed_sessions),
                {},
            )
        calls = await asyncio.gather(
            optional(
                "qualifying",
                lambda: self.results.get_qualifying_results(event.year, event.round),
                [],
            ),
            optional("grid", lambda: self.results.get_starting_grid(event.year, event.round), []),
            optional("latest_results", self.results.get_latest_results, (None, [])),
            optional("drivers", lambda: self.standings.get_driver_standings(event.year), []),
            optional(
                "constructors", lambda: self.standings.get_constructor_standings(event.year), []
            ),
            optional("news", lambda: self.news.get_latest_news(8, event.name), None),
        )
        result.qualifying, result.grid = calls[0], calls[1]
        result.latest_results = calls[2][1]
        result.driver_standings, result.constructor_standings = calls[3], calls[4]
        event_news = calls[5]
        if not event_news or not event_news.articles:
            event_news = await optional("news", lambda: self.news.get_latest_news(8), None)
        result.news_summary = event_news.articles if event_news else []
        if result.active_session:
            try:
                result.weather_available = await self.live.weather_available()
            except ProviderError as exc:
                result.errors["weather"] = str(exc)
        result.provider_status = self.provider_status()
        return result
