"""Causal replay records: every changing fact carries its publication time.

Times are seconds from the archive's time origin, NOT Unix timestamps.
"""

from typing import Literal

from pydantic import BaseModel, Field

from f1_pitwall.domain.models import Constructor, Driver, Event, Session, UTCTime

RaceStatus = Literal["unknown", "active", "in_pit", "stopped", "retired", "dns", "dsq"]


class DataQuality(BaseModel):
    confidence: Literal["observed", "partial"] = "observed"
    fields: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class Participant(BaseModel):
    driver: Driver
    constructor: Constructor | None = None
    # Session roster identity is pre-race metadata, not final classification.
    registered_at: float = 0


class LapData(BaseModel):
    driver_id: str
    number: int = Field(ge=1)
    completed_at: float
    available_at: float
    lap_time_seconds: float | None = None


class TimingSample(BaseModel):
    driver_id: str
    at: float
    position: int | None = None
    laps_completed: int | None = None
    gap_to_leader: float | None = None
    gap_to_ahead: float | None = None
    laps_behind: int | None = None
    grid_position: int | None = None
    retired: bool | None = None
    in_pit: bool | None = None
    stopped: bool | None = None
    explicit_status: RaceStatus | None = None


class Stint(BaseModel):
    driver_id: str
    observed_at: float
    number: int
    compound: str | None = None
    tyre_age: float | None = None
    age_observed_at: float | None = None


class PitStop(BaseModel):
    driver_id: str
    entered_at: float
    exited_at: float | None = None


class ControlSample(BaseModel):
    at: float
    track_status: str | None = None
    total_scheduled_laps: int | None = None


class LapValidity(BaseModel):
    driver_id: str
    lap_number: int = Field(ge=1)
    at: float
    valid: bool


class HistoricalRace(BaseModel):
    event: Event
    session: Session
    started_at: float
    participants: list[Participant]
    laps: list[LapData]
    timing: list[TimingSample]
    stints: list[Stint]
    pit_stops: list[PitStop]
    control: list[ControlSample]
    lap_validity: list[LapValidity] = Field(default_factory=list)
    source: str = "fastf1-live-timing-archive"


class DriverRaceState(BaseModel):
    driver: Driver
    constructor: Constructor | None = None
    position: int | None = None
    grid_position: int | None = None
    laps_completed: int | None = None
    gap_to_leader: float | None = None
    gap_to_ahead: float | None = None
    gap_to_behind: float | None = None
    gap_observed_at: float | None = None
    interval_observed_at: float | None = None
    compound: str | None = None
    tyre_age: float | None = None
    tyre_age_observed_at: float | None = None
    stint_number: int | None = None
    pit_stops_completed: int = 0
    last_lap_time: float | None = None
    last_lap_number: int | None = None
    recent_clean_pace: float | None = None
    pace_laps: list[int] = Field(default_factory=list)
    status: RaceStatus = "unknown"
    active: bool | None = None
    retired: bool | None = None
    lapped: bool | None = None
    laps_behind: int | None = None
    quality: DataQuality = Field(default_factory=DataQuality)


class RaceControlState(BaseModel):
    track_status: str | None = None
    safety_car: bool | None = None
    virtual_safety_car: bool | None = None
    red_flag: bool | None = None


class RaceState(BaseModel):
    event: Event
    session: Session
    current_lap: int
    total_scheduled_laps: int | None = None
    session_time_seconds: float
    elapsed_race_seconds: float
    timestamp: UTCTime | None = None
    drivers: list[DriverRaceState]
    track: RaceControlState
    quality: DataQuality
    source: str


class AvailableLaps(BaseModel):
    year: int
    round: int
    laps: list[int]
    participants: int
    definition: str = "First reported completion of leader lap N; common archive-time cutoff"
