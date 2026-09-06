import asyncio

import httpx

from f1_pitwall.core.config import Settings
from f1_pitwall.core.exceptions import NotFound, ProviderError
from f1_pitwall.domain.models import DataSourceStatus, Home
from f1_pitwall.providers.fastf1 import FastF1Provider
from f1_pitwall.providers.http import ProviderHTTP
from f1_pitwall.providers.jolpica import Jolpica
from f1_pitwall.providers.news import RSSProvider
from f1_pitwall.providers.openf1 import OpenF1
from f1_pitwall.services.analysis import AnalysisService
from f1_pitwall.services.calendar import CalendarService
from f1_pitwall.services.news import NewsService
from f1_pitwall.services.replay import ReplayService
from f1_pitwall.services.results import ResultsService
from f1_pitwall.services.season import SeasonService
from f1_pitwall.services.standings import StandingsService


class Hub:
    def __init__(self, client: httpx.AsyncClient, settings: Settings):
        self.jolpica = Jolpica(ProviderHTTP("jolpica", client, settings))
        self.openf1 = OpenF1(ProviderHTTP("openf1", client, settings))
        self.seasons = SeasonService(self.jolpica)
        self.calendar = CalendarService(self.seasons, self.jolpica, self.openf1)
        self.results = ResultsService(self.seasons, self.calendar, self.jolpica, self.openf1)
        self.standings = StandingsService(self.seasons, self.jolpica)
        self.replay = ReplayService(
            self.seasons, self.jolpica, FastF1Provider(settings), settings.replay_cache_size
        )
        self.analysis = AnalysisService(self.replay)
        self.news = NewsService(
            [
                RSSProvider(ProviderHTTP(name, client, settings), url)
                for name, url in settings.news_feeds.items()
            ]
        )

    def provider_status(self) -> list[DataSourceStatus]:
        return [
            self.jolpica.http.status,
            self.openf1.http.status,
            *(p.http.status for p in self.news.providers),
        ]

    async def home(self) -> Home:
        home = Home()

        async def section(name, call):
            try:
                return await call()
            except (ProviderError, NotFound) as exc:
                home.errors[name] = str(exc)
                return None

        season, events, news = await asyncio.gather(
            section("active_season", self.seasons.get_active_season),
            section("calendar", self.calendar.relevant_events),
            section("news", lambda: self.news.get_latest_news(8)),
        )
        home.active_season = season
        if news:
            home.latest_news = news.articles
        if events is not None:
            current = await section(
                "current_event", lambda: self.calendar.get_current_event(events)
            )
            next_event = await section("next_event", lambda: self.calendar.get_next_event(events))
            home.current_or_next_event = current or next_event
            home.next_session = await section(
                "next_session", lambda: self.calendar.get_next_session(events)
            )
            home.weekend_status = "off_season"
            event = home.current_or_next_event
            if event:
                home.weekend_schedule = event.sessions
                home.weekend_status = (
                    await section(
                        "weekend_status",
                        lambda: self.calendar.event_state(event, self.calendar.clock()),
                    )
                    or "unavailable"
                )
                grid = await section(
                    "grid", lambda: self.results.get_starting_grid(event.year, event.round)
                )
                if grid:
                    home.grid_event, home.recent_or_available_grid = event, grid
        if season:
            drivers, teams = await asyncio.gather(
                section(
                    "driver_standings", lambda: self.standings.get_driver_standings(season.year)
                ),
                section(
                    "constructor_standings",
                    lambda: self.standings.get_constructor_standings(season.year),
                ),
            )
            home.driver_standings_top = (drivers or [])[:5]
            home.constructor_standings_top = (teams or [])[:5]
            if not home.recent_or_available_grid:
                latest = await section("latest_results", self.results.get_latest_results)
                if latest and latest[0]:
                    event = latest[0]
                    grid = await section(
                        "recent_grid",
                        lambda: self.results.get_starting_grid(event.year, event.round),
                    )
                    if grid:
                        home.grid_event, home.recent_or_available_grid = event, grid
        home.provider_status = self.provider_status()
        return home
