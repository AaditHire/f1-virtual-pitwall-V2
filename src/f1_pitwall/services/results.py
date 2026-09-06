from f1_pitwall.domain.enums import SessionType
from f1_pitwall.domain.models import Event, GridEntry, QualifyingResult, RaceResult
from f1_pitwall.providers.jolpica import Jolpica
from f1_pitwall.providers.openf1 import OpenF1
from f1_pitwall.services.calendar import CalendarService
from f1_pitwall.services.season import SeasonService


class ResultsService:
    def __init__(
        self, seasons: SeasonService, calendar: CalendarService, jolpica: Jolpica, openf1: OpenF1
    ):
        self.seasons, self.calendar, self.jolpica, self.openf1 = seasons, calendar, jolpica, openf1

    async def get_qualifying_results(self, year: int, round: int) -> list[QualifyingResult]:
        await self.calendar.get_event(year, round)
        return await self.jolpica.qualifying(year, round)

    async def get_race_results(self, year: int, round: int) -> list[RaceResult]:
        await self.calendar.get_event(year, round)
        return await self.jolpica.results(year, round)

    async def get_latest_results(
        self, year: int | None = None
    ) -> tuple[Event | None, list[RaceResult]]:
        if year is not None:
            await self.seasons.get_season(year)
            years = [year]
        else:
            active = await self.seasons.get_active_season()
            years = [
                s.year for s in reversed(await self.seasons.get_seasons()) if s.year <= active.year
            ]
        for candidate in years:
            event = await self.jolpica.latest_completed(candidate)
            if event:
                return event, await self.jolpica.results(candidate, event.round)
        return None, []

    async def get_starting_grid(self, year: int, round: int) -> list[GridEntry]:
        event = await self.calendar.get_event(year, round)
        results = await self.jolpica.results(year, round)
        if results:
            # Published race results include the actual starting slots after penalties.
            # Grid=0 cannot distinguish a pit-lane start from non-start: keep it unknown.
            return sorted(
                [
                    GridEntry(
                        position=r.grid,
                        driver=r.driver,
                        constructor=r.constructor,
                        pit_lane=False if r.grid and r.grid > 0 else None,
                        source="jolpica",
                    )
                    for r in results
                ],
                key=lambda g: g.position if g.position else float("inf"),
            )
        race = next((s for s in event.sessions if s.type == SessionType.RACE), None)
        if race and race.provider_id:
            drivers = await self.seasons.get_drivers(year)
            grid = await self.openf1.grid(race, drivers)
            if grid:
                return grid
            # OpenF1 also publishes starting_grid records under the qualifying key.
            # This still reads starting_grid, never the qualifying classification.
            qualifying = next((s for s in event.sessions if s.type == SessionType.QUALIFYING), None)
            if qualifying and qualifying.provider_id:
                return await self.openf1.grid(qualifying, drivers)
        # Qualifying order is deliberately never presented as a confirmed starting grid.
        return []
