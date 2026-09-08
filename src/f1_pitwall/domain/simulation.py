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
    gap_kind: Literal["TIME", "LAP_DEFICIT", "UNKNOWN"] = "UNKNOWN"
    laps_behind: int | None = None
    compound: str | None = None
    tyre_age: float | None = None
    relative_pace_seconds_per_lap: float | None = None
    relative_pace_laps: list[int] = Field(default_factory=list)
    pit_state: Literal["ON_TRACK", "IN_PIT", "TERMINAL", "UNKNOWN"]
    traffic: TrafficLevel = "UNKNOWN"
    laps_completed: int | None = None
    race_progress_fraction: float | None = None
    race_elapsed_seconds: float | None = None
    pit_stops_completed: int = 0
    active: bool
    data_quality: dict[str, Any] = Field(default_factory=dict)


class HorizonOutcome(BaseModel):
    horizon_laps: Literal[1, 3, 5]
    expected_delta_time_seconds: float | None = None
    uncertainty_seconds: float | None = None
    prediction_interval_80: tuple[float, float] | None = None
    prediction_interval_90: tuple[float, float] | None = None
    applicability: Literal["RELIABLE", "USABLE", "WEAK", "OUT_OF_DOMAIN"] | None = None
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


class TransitionKernelRequest(ShortHorizonRequest):
    trajectory_count: Literal[100, 500, 1000] = 100
    seed: int = 0
    error_model: Literal["FROZEN_SELECTED", "INDEPENDENT", "PERSISTENT"] = (
        "FROZEN_SELECTED"
    )


class OneLapTransition(BaseModel):
    lap_index: int
    from_phase: str
    to_phase: str
    transition_phases: list[str] = Field(default_factory=list)
    median_relative_delta_seconds: float | None = None
    interval_50: tuple[float, float] | None = None
    interval_80: tuple[float, float] | None = None
    interval_90: tuple[float, float] | None = None
    median_position: int | None = None
    position_range_80: tuple[int, int] | None = None


class RolloutDistribution(BaseModel):
    horizon_laps: Literal[1, 3, 5]
    trajectory_count: int
    median_relative_delta_seconds: float | None = None
    mean_relative_delta_seconds: float | None = None
    interval_50: tuple[float, float] | None = None
    interval_80: tuple[float, float] | None = None
    interval_90: tuple[float, float] | None = None
    median_position: int | None = None
    position_range_50: tuple[int, int] | None = None
    position_range_80: tuple[int, int] | None = None
    position_range_90: tuple[int, int] | None = None
    median_net_pit_cycle_position: int | None = None
    net_pit_cycle_position_range_80: tuple[int, int] | None = None
    applicability: Literal["RELIABLE", "USABLE", "WEAK", "OUT_OF_DOMAIN"] | None = None


class ProbabilisticRollout(BaseModel):
    action: str
    kind: Literal["PIT_NOW", "EXTEND"]
    seed: int
    trajectory_count: int
    error_model: Literal["INDEPENDENT", "PERSISTENT"]
    state: ShortHorizonState
    transitions: list[OneLapTransition]
    outcomes: list[RolloutDistribution]
    components: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
