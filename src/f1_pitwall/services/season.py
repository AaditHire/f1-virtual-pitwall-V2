from f1_pitwall.core.exceptions import NotFound
from f1_pitwall.domain.models import Constructor, Driver, Season
from f1_pitwall.providers.jolpica import Jolpica


class SeasonService:
    def __init__(self, provider: Jolpica):
        self.provider = provider

    async def get_seasons(self) -> list[Season]:
        return await self.provider.seasons()

    async def get_season(self, year: int) -> Season:
        for season in await self.get_seasons():
            if season.year == year:
                return season
        raise NotFound(f"Season {year} is not available (supported range begins at 2021)")

    async def get_latest_season(self) -> Season:
        seasons = await self.get_seasons()
        if not seasons:
            raise NotFound("No supported seasons available")
        return seasons[-1]

    async def get_active_season(self) -> Season:
        return await self.get_season((await self.provider.active_season()).year)

    async def get_drivers(self, year: int) -> list[Driver]:
        await self.get_season(year)
        return await self.provider.drivers(year)

    async def get_constructors(self, year: int) -> list[Constructor]:
        await self.get_season(year)
        return await self.provider.constructors(year)
