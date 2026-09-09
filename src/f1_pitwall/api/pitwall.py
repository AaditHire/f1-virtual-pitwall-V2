from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from f1_pitwall.api.replay import Lap
from f1_pitwall.api.routes import H, Round, Year
from f1_pitwall.domain.pitwall import PitWallDriver, PitWallSnapshot, PitWallTimeline

router = APIRouter(tags=["Rolling Virtual Pit Wall"])
TrajectoryCount = Annotated[int, Query(ge=100, le=1000)]


def checked_trajectory_count(value: int) -> int:
    if value not in {100, 500, 1000}:
        raise HTTPException(422, "trajectory_count must be 100, 500, or 1000")
    return value


@router.get("/api/v1/pitwall/{year}/{round}/timeline", response_model=PitWallTimeline)
async def timeline(
    year: Year,
    round: Round,
    h: H,
    start_lap: Annotated[int, Query(ge=1)],
    end_lap: Annotated[int, Query(ge=1)],
    driver: str | None = None,
    trajectory_count: TrajectoryCount = 100,
):
    if end_lap < start_lap:
        raise HTTPException(422, "end_lap must be greater than or equal to start_lap")
    return await h.pitwall.timeline(
        year,
        round,
        start_lap,
        end_lap,
        driver,
        checked_trajectory_count(trajectory_count),
    )


@router.get("/api/v1/pitwall/{year}/{round}/{lap}", response_model=PitWallSnapshot)
async def snapshot(
    year: Year,
    round: Round,
    lap: Lap,
    h: H,
    trajectory_count: TrajectoryCount = 100,
):
    return await h.pitwall.snapshot(year, round, lap, checked_trajectory_count(trajectory_count))


@router.get(
    "/api/v1/pitwall/{year}/{round}/{lap}/drivers/{driver_id}",
    response_model=PitWallDriver,
)
async def driver_detail(
    year: Year,
    round: Round,
    lap: Lap,
    driver_id: str,
    h: H,
    trajectory_count: TrajectoryCount = 100,
):
    return await h.pitwall.driver(
        year, round, lap, driver_id, checked_trajectory_count(trajectory_count)
    )
