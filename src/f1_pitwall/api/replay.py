from typing import Annotated

from fastapi import APIRouter, Path, Query

from f1_pitwall.api.routes import H, Round, Year
from f1_pitwall.domain.replay import AvailableLaps, DriverRaceState, RaceState, RadioFeed

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


@router.get("/{year}/{round}/{lap}/radio", response_model=RadioFeed)
async def radio(
    year: Year,
    round: Round,
    lap: Lap,
    h: H,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    return await h.replay.get_radio(year, round, lap, limit=limit)


@router.get("/{year}/{round}/{lap}/drivers/{driver_id}/radio", response_model=RadioFeed)
async def driver_radio(
    year: Year,
    round: Round,
    lap: Lap,
    driver_id: str,
    h: H,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    return await h.replay.get_radio(year, round, lap, driver_id=driver_id, limit=limit)
