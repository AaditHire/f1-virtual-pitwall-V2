"""Public, JSON-safe short-horizon strategy decisions."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from f1_pitwall.domain.analysis import Confidence, RejoinAnalysis, TrafficLevel
from f1_pitwall.domain.models import Driver

Recommendation = Literal[
    "PIT_NOW", "EXTEND", "HOLD_NO_CLEAR_ADVANTAGE", "INSUFFICIENT_DATA"
]
ActionKind = Literal["PIT_NOW", "EXTEND"]


class StrategyAction(BaseModel):
    id: str
    kind: ActionKind
    compound: str | None = None
    extension_laps: int | None = None
    horizon_laps: int = 3
    legal: bool = True
    action_score: float | None = None
    estimated_total_time_seconds: float | None = None
    expected_rejoin: RejoinAnalysis | None = None
    traffic_status: TrafficLevel = "UNKNOWN"
    confidence: Confidence = "INSUFFICIENT"
    score_components: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class StrategyDecision(BaseModel):
    year: int
    round: int
    lap: int
    cutoff_seconds: float
    driver: Driver
    eligible: bool
    current_position: int | None = None
    current_compound: str | None = None
    tyre_age: float | None = None
    recommended_action: Recommendation | None = None
    recommended_compound: str | None = None
    recommended_extension_laps: int | None = None
    alternative_action: str | None = None
    expected_rejoin: RejoinAnalysis | None = None
    traffic_status: TrafficLevel = "UNKNOWN"
    decision_score: float | None = None
    alternative_score: float | None = None
    decision_margin: float | None = None
    minimum_decision_margin: float = 0.75
    confidence: Confidence = "INSUFFICIENT"
    main_reasons: list[str] = Field(default_factory=list)
    main_risks: list[str] = Field(default_factory=list)
    data_quality: dict[str, Any] = Field(default_factory=dict)
    actions: list[StrategyAction] = Field(default_factory=list)


class ExcludedDriver(BaseModel):
    driver: Driver
    status: str
    reason: str


class FullGridStrategy(BaseModel):
    year: int
    round: int
    lap: int
    cutoff_seconds: float
    active_count: int
    decisions: list[StrategyDecision]
    excluded: list[ExcludedDriver] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ActionComparison(BaseModel):
    year: int
    round: int
    lap: int
    driver: Driver
    recommended_action: Recommendation | None = None
    decision_margin: float | None = None
    minimum_decision_margin: float = 0.75
    actions: list[StrategyAction]
