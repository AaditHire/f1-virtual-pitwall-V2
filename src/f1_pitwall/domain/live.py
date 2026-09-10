"""Provider-independent current-weekend and live-session contracts."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from f1_pitwall.domain.models import (
    ConstructorStanding,
    DataSourceStatus,
    DriverStanding,
    Event,
    GridEntry,
    NewsArticle,
    NextSession,
    QualifyingResult,
    RaceResult,
    Session,
    UTCTime,
)
from f1_pitwall.domain.pitwall import PitWallDriver, PitWallSnapshot
from f1_pitwall.domain.replay import DriverRaceState, RaceState

SessionStatus = Literal["UPCOMING", "LIVE", "COMPLETED", "DELAYED", "UNKNOWN"]
LiveAvailability = Literal["LIVE_AVAILABLE", "DELAYED_AVAILABLE", "HISTORICAL_ONLY", "UNAVAILABLE"]
FreshnessState = Literal["FRESH", "DELAYED", "STALE", "UNKNOWN"]


class Freshness(BaseModel):
    source: str
    retrieved_at: UTCTime
    provider_timestamp: UTCTime | None = None
    data_age_seconds: float | None = None
    state: FreshnessState = "UNKNOWN"


class SessionState(BaseModel):
    session: Session
    status: SessionStatus


class SessionClassificationEntry(BaseModel):
    driver_number: int
    position: int | None = None
    laps: int | None = None
    duration_seconds: float | list[float | None] | None = None
    gap_to_leader: float | str | list[float | None] | None = None
    dns: bool | None = None
    dnf: bool | None = None
    dsq: bool | None = None


class LiveSession(BaseModel):
    session_key: int
    meeting_key: int | None = None
    name: str
    type: str
    start: UTCTime
    end: UTCTime | None = None
    year: int
    circuit_name: str | None = None
    location: str | None = None
    country: str | None = None
    provider_timestamp: UTCTime | None = None


class LiveTimingBundle(BaseModel):
    session: LiveSession
    drivers: list[dict[str, Any]] = Field(default_factory=list)
    positions: list[dict[str, Any]] = Field(default_factory=list)
    intervals: list[dict[str, Any]] = Field(default_factory=list)
    laps: list[dict[str, Any]] = Field(default_factory=list)
    stints: list[dict[str, Any]] = Field(default_factory=list)
    pit_stops: list[dict[str, Any]] = Field(default_factory=list)
    track_status: list[dict[str, Any]] = Field(default_factory=list)
    weather: list[dict[str, Any]] = Field(default_factory=list)
    race_control: list[dict[str, Any]] = Field(default_factory=list)
    retrieved_at: UTCTime
    errors: dict[str, str] = Field(default_factory=dict)


class WeatherState(BaseModel):
    air_temperature_c: float | None = None
    track_temperature_c: float | None = None
    humidity_percent: float | None = None
    rainfall: float | bool | None = None
    wind_speed: float | None = None
    wind_direction_degrees: float | None = None
    observed_at: UTCTime | None = None


class RaceControlMessage(BaseModel):
    timestamp: UTCTime | None = None
    category: str | None = None
    flag: str | None = None
    scope: str | None = None
    message: str | None = None


class RaceControlFeed(BaseModel):
    track_status: Literal["GREEN", "YELLOW", "DOUBLE_YELLOW", "SC", "VSC", "RED", "UNKNOWN"] = (
        "UNKNOWN"
    )
    messages: list[RaceControlMessage] = Field(default_factory=list)
    freshness: Freshness


class LiveStatus(BaseModel):
    live: bool = False
    availability: LiveAvailability
    session_status: SessionStatus = "UNKNOWN"
    event: Event | None = None
    session: Session | None = None
    next_session: NextSession | None = None
    provider: str
    freshness: Freshness
    reason: str | None = None


class LiveRaceResponse(BaseModel):
    live: bool
    availability: LiveAvailability
    event: Event | None = None
    session: Session | None = None
    lap: int | None = None
    status: SessionStatus = "UNKNOWN"
    track_status: str = "UNKNOWN"
    weather: WeatherState | None = None
    drivers: list[DriverRaceState] = Field(default_factory=list)
    race_state: RaceState | None = None
    provider: str
    freshness: Freshness
    next_session: NextSession | None = None
    next_event: Event | None = None
    current_weekend_status: SessionStatus = "UNKNOWN"
    latest_results: list[RaceResult] = Field(default_factory=list)
    news: list[NewsArticle] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)


class CurrentPitWallResponse(BaseModel):
    analysis_status: Literal["AVAILABLE", "PARTIAL", "UNAVAILABLE"]
    strategy_status: Literal["EXPERIMENTAL"] = "EXPERIMENTAL"
    provider: str
    last_update: UTCTime | None = None
    data_freshness: Freshness
    snapshot: PitWallSnapshot | None = None
    driver: PitWallDriver | None = None
    current_state: dict[str, Any] | None = None
    missing_requirements: list[str] = Field(default_factory=list)


class WeekendState(BaseModel):
    season: int | None = None
    event: Event | None = None
    circuit: str | None = None
    country: str | None = None
    round: int | None = None
    weekend_format: Literal["STANDARD", "SPRINT", "UNKNOWN"] = "UNKNOWN"
    status: SessionStatus = "UNKNOWN"
    session_schedule: list[SessionState] = Field(default_factory=list)
    completed_sessions: list[Session] = Field(default_factory=list)
    session_results: dict[str, list[SessionClassificationEntry]] = Field(default_factory=dict)
    active_session: Session | None = None
    next_session: NextSession | None = None
    qualifying: list[QualifyingResult] = Field(default_factory=list)
    grid: list[GridEntry] = Field(default_factory=list)
    latest_results: list[RaceResult] = Field(default_factory=list)
    driver_standings: list[DriverStanding] = Field(default_factory=list)
    constructor_standings: list[ConstructorStanding] = Field(default_factory=list)
    weather_available: bool = False
    news_summary: list[NewsArticle] = Field(default_factory=list)
    provider_status: list[DataSourceStatus] = Field(default_factory=list)
    errors: dict[str, str] = Field(default_factory=dict)
