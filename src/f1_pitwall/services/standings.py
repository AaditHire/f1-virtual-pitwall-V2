from f1_pitwall.domain.models import ConstructorStanding, DriverStanding
from f1_pitwall.providers.jolpica import Jolpica
from f1_pitwall.services.season import SeasonService


class StandingsService:
    def __init__(self, seasons: SeasonService, provider: Jolpica):
        self.seasons, self.provider = seasons, provider

    async def get_driver_standings(self, year: int) -> list[DriverStanding]:
        await self.seasons.get_season(year)
        return await self.provider.driver_standings(year)

    async def get_constructor_standings(self, year: int) -> list[ConstructorStanding]:
        await self.seasons.get_season(year)
        return await self.provider.constructor_standings(year)
