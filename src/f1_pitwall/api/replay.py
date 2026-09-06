from typing import Annotated

from fastapi import APIRouter, Path

from f1_pitwall.api.routes import H, Round, Year
from f1_pitwall.domain.replay import AvailableLaps, DriverRaceState, RaceState

router = APIRouter(prefix="/api/v1/replay", tags=["Historical replay"])
Lap = Annotated[int, Path(ge=1)]


@router.get("/{year}/{round}/laps", response_model=AvailableLaps)
async def laps(year: Year, round: Round, h: H):
    return await h.replay.get_available_laps(year, round)


@router.get("/{year}/{round}/{lap}", response_model=RaceState)
async def state(year: Year, round: Round, lap: Lap, h: H):
    return await h.replay.get_race_state(year, round, lap)


@router.get("/{year}/{round}/{lap}/drivers/{driver_id}", response_model=DriverRaceState)
async def driver(year: Year, round: Round, lap: Lap, driver_id: str, h: H):
    return await h.replay.get_driver_state(year, round, lap, driver_id)
