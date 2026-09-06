import asyncio
from collections import OrderedDict

from f1_pitwall.core.exceptions import NotFound
from f1_pitwall.domain.replay import AvailableLaps, DriverRaceState, HistoricalRace, RaceState
from f1_pitwall.providers.fastf1 import FastF1Provider
from f1_pitwall.providers.jolpica import Jolpica
from f1_pitwall.services.race_state import RaceStateBuilder
from f1_pitwall.services.season import SeasonService


class ReplayService:
    def __init__(
        self,
        seasons: SeasonService,
        jolpica: Jolpica,
        provider: FastF1Provider,
        cache_size: int = 4,
    ):
        self.seasons, self.jolpica, self.provider = seasons, jolpica, provider
        self.cache_size = cache_size
        self._cache: OrderedDict[tuple[int, int], HistoricalRace] = OrderedDict()
        self._lock = asyncio.Lock()
        self.builder = RaceStateBuilder()

    async def load_race(self, year: int, round: int) -> HistoricalRace:
        key = (year, round)
        # One shared load, including simultaneous requests for different drivers.
        async with self._lock:
            if key not in self._cache:
                await self.seasons.get_season(year)
                event = next(
                    (e for e in await self.jolpica.calendar(year) if e.round == round), None
                )
                if event is None:
                    raise NotFound(f"Event {year}/{round} is unavailable")
                self._cache[key] = await asyncio.to_thread(self.provider.load, event)
                while len(self._cache) > self.cache_size:
                    self._cache.popitem(last=False)
            self._cache.move_to_end(key)
            return self._cache[key]

    async def get_race_state(self, year: int, round: int, lap: int) -> RaceState:
        race = await self.load_race(year, round)
        return await asyncio.to_thread(self.builder.build, race, lap)

    async def get_driver_state(
        self, year: int, round: int, lap: int, driver_id: str
    ) -> DriverRaceState:
        state = await self.get_race_state(year, round, lap)
        for driver in state.drivers:
            if driver.driver.id == driver_id:
                return driver
        raise NotFound(f"Driver {driver_id} is not in this race session")

    async def get_available_laps(self, year: int, round: int) -> AvailableLaps:
        race = await self.load_race(year, round)
        return AvailableLaps(
            year=year,
            round=round,
            laps=sorted({r.number for r in race.laps}),
            participants=len(race.participants),
        )
