"""Public short-horizon counterfactual state and outcome models."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from f1_pitwall.domain.analysis import Confidence, TrafficLevel
from f1_pitwall.domain.models import Driver


class ShortHorizonState(BaseModel):
    driver: Driver
    current_position: int | None = None
    field_size: int
    gap_to_leader: float | None = None
    compound: str | None = None
    tyre_age: float | None = None
    relative_pace_seconds_per_lap: float | None = None
    relative_pace_laps: list[int] = Field(default_factory=list)
    pit_state: Literal["ON_TRACK", "IN_PIT", "TERMINAL", "UNKNOWN"]
    traffic: TrafficLevel = "UNKNOWN"
    laps_completed: int | None = None
    active: bool
    data_quality: dict[str, Any] = Field(default_factory=dict)


class HorizonOutcome(BaseModel):
    horizon_laps: Literal[1, 3, 5]
    expected_delta_time_seconds: float | None = None
    uncertainty_seconds: float | None = None
    expected_position: int | None = None
    position_range: tuple[int, int] | None = None
    expected_position_change: float | None = None
    physical_track_position: int | None = None
    net_race_position_estimate: int | None = None
    net_race_position_range: tuple[int, int] | None = None
    confidence: Confidence = "INSUFFICIENT"
    components: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class CounterfactualActionOutcome(BaseModel):
    action: str
    kind: Literal["PIT_NOW", "EXTEND"]
    compound: str | None = None
    extension_laps: int | None = None
    outcomes: list[HorizonOutcome]
    confidence: Confidence = "INSUFFICIENT"
    warnings: list[str] = Field(default_factory=list)


class CounterfactualComparison(BaseModel):
    year: int
    round: int
    lap: int
    cutoff_seconds: float
    state: ShortHorizonState
    actions: list[CounterfactualActionOutcome]
    preferred_action: str | None = None
    decision: Literal["PIT_NOW", "EXTEND", "HOLD_NO_CLEAR_ADVANTAGE", "INSUFFICIENT_DATA"]
    outcome_margin_seconds: float | None = None
    uncertainty_overlap: bool | None = None
    confidence: Confidence = "INSUFFICIENT"
    warnings: list[str] = Field(default_factory=list)


class ShortHorizonRequest(BaseModel):
    year: int = Field(ge=2021)
    round: int = Field(ge=1)
    lap: int = Field(ge=1)
    driver_id: str
    action: str
