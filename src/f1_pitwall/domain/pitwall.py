"""Frontend-safe rolling Virtual Pit Wall response models."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from f1_pitwall.domain.analysis import DriverAnalysis, TrafficLevel
from f1_pitwall.domain.models import Driver, Event
from f1_pitwall.domain.simulation import RolloutDistribution

DecisionState = Literal["ACTIONABLE", "CAUTION", "COARSE_ONLY", "INSUFFICIENT_DATA"]


class PitWallRival(BaseModel):
    driver: Driver
    position: int | None = None
    gap_seconds: float | None = None
    relative_pace_seconds_per_lap: float | None = None
    pit_stops_completed: int = 0
    status: str


class PitWallAlert(BaseModel):
    kind: Literal[
        "UNDERCUT_THREAT",
        "CLEAR_AIR_WINDOW",
        "REJOIN_TRAFFIC_RISK",
        "PIT_WINDOW_OPEN",
        "PIT_WINDOW_CLOSED",
        "STRATEGY_MODEL_UNCERTAIN",
        "RIVAL_STOPPED",
        "POSITION_AT_RISK",
    ]
    driver_id: str
    rival_id: str | None = None
    detail: str


class PitWallAction(BaseModel):
    action: str
    kind: Literal["PIT_NOW", "EXTEND"]
    policy_score: float | None = None
    outcomes: list[RolloutDistribution] = Field(default_factory=list)


class PitWallDriver(BaseModel):
    driver: Driver
    status: str
    current_position: int | None = None
    gap_kind: Literal["TIME", "LAP_DEFICIT", "UNKNOWN"] = "UNKNOWN"
    gap_to_leader_seconds: float | None = None
    laps_behind: int | None = None
    compound: str | None = None
    tyre_age: float | None = None
    recent_pace_seconds_per_lap: float | None = None
    traffic: TrafficLevel = "UNKNOWN"
    pit_cycle_position: int | None = None
    recommendation: str | None = None
    alternative: str | None = None
    decision_state: DecisionState = "INSUFFICIENT_DATA"
    operating_envelope: Literal["SHORT_HORIZON_ONLY"] = "SHORT_HORIZON_ONLY"
    horizons_laps: list[int] = Field(default_factory=lambda: [1, 3, 5])
    policy_recommendation: str | None = None
    model_disagreement: bool = False
    evaluated_action_count: int = 0
    decision_margin_seconds: float | None = None
    uncertainty_overlap: bool | None = None
    main_risk: str | None = None
    main_opportunity: str | None = None
    relevant_rivals: list[PitWallRival] = Field(default_factory=list)
    alerts: list[PitWallAlert] = Field(default_factory=list)
    data_quality: dict[str, Any] = Field(default_factory=dict)
    actions: list[PitWallAction] = Field(default_factory=list)
    engineering_analysis: DriverAnalysis | None = None


class PitWallSnapshot(BaseModel):
    event: Event
    lap: int
    cutoff_seconds: float
    race_status: str
    reanchored_to_observed_lap: bool = True
    trajectory_count: int
    operating_envelope: Literal["SHORT_HORIZON_ONLY"] = "SHORT_HORIZON_ONLY"
    horizons_laps: list[int] = Field(default_factory=lambda: [1, 3, 5])
    drivers: list[PitWallDriver]
    alerts: list[PitWallAlert] = Field(default_factory=list)
    elapsed_seconds: float | None = None


class PitWallTimelineEntry(BaseModel):
    lap: int
    observed_position: int | None = None
    gap_kind: Literal["TIME", "LAP_DEFICIT", "UNKNOWN"] = "UNKNOWN"
    laps_behind: int | None = None
    pit_cycle_position: int | None = None
    traffic: TrafficLevel = "UNKNOWN"
    recommendation: str | None
    decision_state: DecisionState
    model_disagreement: bool = False
    recommendation_age: int = 1
    persistence: int = 1
    changed: bool = False
    change_reasons: list[str] = Field(default_factory=list)
    unsupported_flip_suppressed: bool = False


class PitWallDriverTimeline(BaseModel):
    driver: Driver
    entries: list[PitWallTimelineEntry]


class PitWallTimeline(BaseModel):
    event: Event
    start_lap: int
    end_lap: int
    reanchored_laps: list[int]
    drivers: list[PitWallDriverTimeline]
    metrics: dict[str, Any] = Field(default_factory=dict)
