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
from f1_pitwall.services.live import LiveService, freshness
from f1_pitwall.services.news import NewsService
from f1_pitwall.services.pitwall import PitWallService
from f1_pitwall.services.replay import ReplayService
from f1_pitwall.services.results import ResultsService
from f1_pitwall.services.season import SeasonService
from f1_pitwall.services.simulation import SimulationService
from f1_pitwall.services.standings import StandingsService
from f1_pitwall.services.strategy import StrategyService
from f1_pitwall.services.weekend import CurrentWeekendService


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
        self.strategy = StrategyService(self.replay)
        self.simulation = SimulationService(self.replay)
        self.pitwall = PitWallService(self.replay)
        self.news = NewsService(
            [
                RSSProvider(ProviderHTTP(name, client, settings), url)
                for name, url in settings.news_feeds.items()
            ]
        )
        self.live = LiveService(self.openf1, self.calendar, self.results, self.news)
        self.weekend = CurrentWeekendService(
            self.seasons,
            self.calendar,
            self.results,
            self.standings,
            self.news,
            self.live,
            self.provider_status,
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
            home.driver_standings_top = (drivers or [])[:10]
            home.constructor_standings_top = (teams or [])[:10]
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
        current_weekend, live_status = await asyncio.gather(
            section("current_weekend", self.weekend.current),
            section(
                "live_status",
                lambda: self.live.status(home.next_session),
            ),
        )
        if current_weekend:
            home.current_weekend = current_weekend.model_dump()
        if live_status:
            home.live_status = live_status.model_dump()
            if live_status.live:
                race = await section("current_race_state", self.live_race)
                if race:
                    home.current_race_state = race.model_dump()
        home.navigation = {
            "current_weekend": "/api/v1/weekend/current",
            "next_session": "/api/v1/sessions/next",
            "live_race": "/api/v1/live/race",
            "live_pitwall": "/api/v1/live/pitwall",
        }
        return home

    async def live_race(self):
        async def safe(call, default):
            try:
                return await call()
            except (ProviderError, NotFound):
                return default

        next_session, latest, feed = await asyncio.gather(
            safe(self.calendar.get_next_session, None),
            safe(self.results.get_latest_results, (None, [])),
            safe(lambda: self.news.get_latest_news(8), None),
        )
        return await self.live.race(
            next_session=next_session,
            latest_results=latest[1],
            news=feed.articles if feed else [],
        )

    async def live_status(self):
        try:
            next_session = await self.calendar.get_next_session()
        except (ProviderError, NotFound):
            next_session = None
        return await self.live.status(next_session)

    async def live_weather(self):
        try:
            bundle = await self.live.weather_bundle()
        except ProviderError as exc:
            return {
                "available": False,
                "weather": None,
                "freshness": freshness("openf1", self.live.clock(), None),
                "reason": exc.message,
            }
        return {
            "available": bool(bundle and bundle.weather),
            "weather": self.live.weather(bundle) if bundle else None,
            "freshness": self.live.metadata(bundle)
            if bundle
            else freshness("openf1", self.live.clock(), None),
            "reason": None
            if bundle and bundle.weather
            else (
                next(iter(bundle.errors.values()))
                if bundle and bundle.errors
                else "Weather is not available"
            ),
        }

    async def live_control(self):
        try:
            bundle = await self.live.control_bundle()
        except ProviderError as exc:
            return {
                "available": False,
                "track_status": "UNKNOWN",
                "messages": [],
                "freshness": freshness("openf1", self.live.clock(), None),
                "reason": exc.message,
            }
        if bundle:
            feed = self.live.control(bundle)
            return {
                "available": bool(bundle.track_status or bundle.race_control),
                **feed.model_dump(),
                "reason": None
                if bundle.track_status or bundle.race_control
                else (
                    next(iter(bundle.errors.values()))
                    if bundle.errors
                    else "Race control is not available"
                ),
            }
        return {
            "available": False,
            "track_status": "UNKNOWN",
            "messages": [],
            "freshness": freshness("openf1", self.live.clock(), None),
            "reason": "No provider session is available",
        }
