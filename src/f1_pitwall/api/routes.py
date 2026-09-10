from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel

from f1_pitwall.domain.models import (
    Constructor,
    ConstructorStanding,
    DataSourceStatus,
    Driver,
    DriverStanding,
    Event,
    GridEntry,
    Home,
    NewsFeed,
    NextSession,
    QualifyingResult,
    RaceResult,
    Season,
    Session,
)
from f1_pitwall.services.hub import Hub

router = APIRouter(prefix="/api/v1")


def hub(request: Request) -> Hub:
    return request.app.state.hub


H = Annotated[Hub, Depends(hub)]
Year = Annotated[int, Path(ge=2021)]
Round = Annotated[int, Path(ge=1)]


def timezone_query(timezone: str = "UTC") -> ZoneInfo:
    try:
        return ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, "Unknown IANA timezone") from None


TZ = Annotated[ZoneInfo, Depends(timezone_query)]


def present(value, timezone: ZoneInfo):
    """Keep canonical UTC fields; add local companions only when explicitly requested."""
    from datetime import date, datetime

    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, BaseModel):
        result = value.model_dump(mode="json")
        for key in type(value).model_fields:
            field = getattr(value, key)
            if isinstance(field, datetime):
                if timezone.key != "UTC":
                    result[f"{key}_local"] = field.astimezone(timezone).isoformat()
            elif isinstance(field, (BaseModel, list, dict)):
                result[key] = present(field, timezone)
        return result
    if isinstance(value, list):
        return [present(v, timezone) for v in value]
    if isinstance(value, dict):
        return {k: present(v, timezone) for k, v in value.items()}
    return value


def timed(value, tz):
    # JSONResponse preserves documented *_local extension fields on the base response models.
    from fastapi.responses import JSONResponse

    return JSONResponse(present(value, tz))


@router.get("/seasons", response_model=list[Season])
async def seasons(h: H):
    return await h.seasons.get_seasons()


@router.get("/seasons/latest", response_model=Season)
async def latest_season(h: H):
    return await h.seasons.get_latest_season()


@router.get("/seasons/active", response_model=Season)
async def active_season(h: H):
    return await h.seasons.get_active_season()


@router.get("/seasons/{year}", response_model=Season)
async def season(year: Year, h: H):
    return await h.seasons.get_season(year)


@router.get("/seasons/{year}/drivers", response_model=list[Driver])
async def drivers(year: Year, h: H):
    return await h.seasons.get_drivers(year)


@router.get("/seasons/{year}/constructors", response_model=list[Constructor])
async def constructors(year: Year, h: H):
    return await h.seasons.get_constructors(year)


@router.get("/seasons/{year}/calendar", response_model=list[Event])
async def calendar(year: Year, h: H, tz: TZ):
    return timed(await h.calendar.get_calendar(year), tz)


@router.get("/events/current", response_model=Event | None)
async def current_event(h: H, tz: TZ):
    return timed(await h.calendar.get_current_event(), tz)


@router.get("/events/next", response_model=Event | None)
async def next_event(h: H, tz: TZ):
    return timed(await h.calendar.get_next_event(), tz)


@router.get("/sessions/next", response_model=NextSession | None)
async def next_session(h: H, tz: TZ):
    return timed(await h.calendar.get_next_session(), tz)


@router.get("/events/{year}/{round}", response_model=Event)
async def event(year: Year, round: Round, h: H, tz: TZ):
    return timed(await h.calendar.get_event(year, round), tz)


@router.get("/events/{year}/{round}/sessions", response_model=list[Session])
async def sessions(year: Year, round: Round, h: H, tz: TZ):
    return timed(await h.calendar.get_event_schedule(year, round), tz)


@router.get("/events/{year}/{round}/qualifying", response_model=list[QualifyingResult])
async def qualifying(year: Year, round: Round, h: H):
    return await h.results.get_qualifying_results(year, round)


@router.get("/events/{year}/{round}/grid", response_model=list[GridEntry])
async def grid(year: Year, round: Round, h: H):
    return await h.results.get_starting_grid(year, round)


@router.get("/events/{year}/{round}/results", response_model=list[RaceResult])
async def results(year: Year, round: Round, h: H):
    return await h.results.get_race_results(year, round)


@router.get("/results/latest")
async def latest_results(h: H, tz: TZ):
    event, results = await h.results.get_latest_results()
    return timed({"event": event, "results": results}, tz)


@router.get("/standings/drivers/{year}", response_model=list[DriverStanding])
async def driver_standings(year: Year, h: H):
    return await h.standings.get_driver_standings(year)


@router.get("/standings/constructors/{year}", response_model=list[ConstructorStanding])
async def constructor_standings(year: Year, h: H):
    return await h.standings.get_constructor_standings(year)


@router.get("/news", response_model=NewsFeed)
async def news(
    h: H,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    query: Annotated[str | None, Query(max_length=100)] = None,
):
    return await h.news.get_latest_news(limit, query)


@router.get("/providers/status", response_model=list[DataSourceStatus])
async def status(h: H):
    return h.provider_status()


@router.get("/home", response_model=Home)
async def home(h: H, tz: TZ):
    return timed(await h.home(), tz)
