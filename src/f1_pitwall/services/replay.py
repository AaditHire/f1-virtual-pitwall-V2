import asyncio
from collections import OrderedDict

from f1_pitwall.core.exceptions import NotFound, ProviderError
from f1_pitwall.domain.replay import (
    AvailableLaps,
    DriverRaceState,
    HistoricalRace,
    RaceState,
    RadioFeed,
)
from f1_pitwall.providers.fastf1 import FastF1Provider
from f1_pitwall.providers.jolpica import Jolpica
from f1_pitwall.providers.openf1_archive import OpenF1Archive
from f1_pitwall.providers.replay_snapshots import SnapshotStore
from f1_pitwall.services.race_state import RaceStateBuilder, cutoff_for
from f1_pitwall.services.season import SeasonService


class ReplayService:
    def __init__(
        self,
        seasons: SeasonService,
        jolpica: Jolpica,
        provider: FastF1Provider,
        cache_size: int = 4,
        snapshots: SnapshotStore | None = None,
        archive: OpenF1Archive | None = None,
    ):
        self.seasons, self.jolpica, self.provider = seasons, jolpica, provider
        self.snapshots, self.archive = snapshots, archive
        self.cache_size = cache_size
        self._cache: OrderedDict[tuple[int, int], HistoricalRace] = OrderedDict()
        # One shared load per race; loads of different races never wait for each other.
        self._loading: dict[tuple[int, int], asyncio.Future[HistoricalRace]] = {}
        self.builder = RaceStateBuilder()

    async def load_race(self, year: int, round: int) -> HistoricalRace:
        key = (year, round)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        task = self._loading.get(key)
        if task is None:
            task = asyncio.ensure_future(self._load(key))
            self._loading[key] = task
            task.add_done_callback(lambda done: self._loaded(key, done))
        return await asyncio.shield(task)

    async def _load(self, key: tuple[int, int]) -> HistoricalRace:
        year, round = key
        await self.seasons.get_season(year)
        event = next((e for e in await self.jolpica.calendar(year) if e.round == round), None)
        if event is None:
            raise NotFound(f"Event {year}/{round} is unavailable")
        race = await self._snapshot(event)
        if race is None:
            race = await self._fetch(event)
        self._cache[key] = race
        while len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)
        return race

    async def _snapshot(self, event) -> HistoricalRace | None:
        if self.snapshots is None:
            return None
        data = await self.snapshots.fetch(event)
        if data is None:
            return None
        return await asyncio.to_thread(self.snapshots.decode, event, data)

    async def _fetch(self, event) -> HistoricalRace:
        # F1's own archive needs an F1 TV login since October 2026, so OpenF1 comes first for the
        # seasons it covers; FastF1 still serves 2021-2022 from its public mirror.
        if self.archive is None or not self.archive.covers(event):
            return await asyncio.to_thread(self.provider.load, event)
        try:
            return await self.archive.load(event)
        except ProviderError as first:
            try:
                return await asyncio.to_thread(self.provider.load, event)
            except ProviderError as second:
                raise ProviderError(
                    "replay", f"{first.message} (OpenF1); {second.message} (FastF1)"
                ) from second

    def _loaded(self, key: tuple[int, int], task: asyncio.Future[HistoricalRace]) -> None:
        self._loading.pop(key, None)
        if not task.cancelled():
            task.exception()  # Failed loads are not cached; mark them retrieved.

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

    async def get_radio(
        self,
        year: int,
        round: int,
        lap: int,
        driver_id: str | None = None,
        limit: int = 50,
    ) -> RadioFeed:
        race = await self.load_race(year, round)
        cutoff = cutoff_for(race, lap)
        if driver_id is not None and not any(
            participant.driver.id == driver_id for participant in race.participants
        ):
            raise NotFound(f"Driver {driver_id} is not in this race session")
        messages = [
            record
            for record in race.radio
            if record.available_at <= cutoff
            and (driver_id is None or record.driver_id == driver_id)
        ]
        messages.sort(key=lambda record: (record.available_at, record.driver_id, record.audio_url))
        return RadioFeed(
            session_id=f"f1:{year}:{round}:race",
            year=year,
            round=round,
            leader_lap=lap,
            causal_cutoff=cutoff,
            driver_id=driver_id,
            total_available=len(messages),
            messages=messages[-limit:],
            source=race.source,
        )
