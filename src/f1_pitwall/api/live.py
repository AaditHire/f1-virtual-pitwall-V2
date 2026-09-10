from typing import Annotated

from fastapi import APIRouter, Query

from f1_pitwall.api.pitwall import checked_trajectory_count
from f1_pitwall.api.routes import TZ, H, timed
from f1_pitwall.domain.live import (
    CurrentPitWallResponse,
    LiveRaceResponse,
    LiveStatus,
    WeekendState,
)

router = APIRouter(prefix="/api/v1", tags=["Current F1 weekend"])


@router.get("/weekend/current", response_model=WeekendState)
async def current_weekend(h: H, tz: TZ):
    return timed(await h.weekend.current(), tz)


@router.get("/live/status", response_model=LiveStatus)
async def live_status(h: H, tz: TZ):
    return timed(await h.live_status(), tz)


@router.get("/live/race", response_model=LiveRaceResponse)
async def live_race(h: H, tz: TZ):
    return timed(await h.live_race(), tz)


@router.get("/live/pitwall", response_model=CurrentPitWallResponse)
async def live_pitwall(
    h: H, tz: TZ, trajectory_count: Annotated[int, Query(ge=100, le=1000)] = 100
):
    value = await h.live.current_pitwall(
        h.pitwall, trajectory_count=checked_trajectory_count(trajectory_count)
    )
    return timed(value, tz)


@router.get("/live/pitwall/drivers/{driver_id}", response_model=CurrentPitWallResponse)
async def live_driver(
    driver_id: str,
    h: H,
    tz: TZ,
    trajectory_count: Annotated[int, Query(ge=100, le=1000)] = 100,
):
    value = await h.live.current_pitwall(
        h.pitwall,
        driver_id=driver_id,
        trajectory_count=checked_trajectory_count(trajectory_count),
    )
    return timed(value, tz)


@router.get("/live/weather")
async def live_weather(h: H, tz: TZ):
    return timed(await h.live_weather(), tz)


@router.get("/live/race-control")
async def live_race_control(h: H, tz: TZ):
    return timed(await h.live_control(), tz)
