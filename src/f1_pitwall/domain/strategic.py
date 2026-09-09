"""Structured output for coarse event-level strategic stint comparisons."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class StintCompoundPrior(BaseModel):
    compound: Literal["SOFT", "MEDIUM", "HARD"]
    expected_relative_pace_seconds_per_lap: float | None = None
    compound_effect_seconds_per_lap: float | None = None
    pace_iqr_seconds_per_lap: tuple[float, float] | None = None
    median_competitive_stint_laps: float | None = None
    competitive_stint_iqr_laps: tuple[float, float] | None = None
    pace_sample_count: int = 0
    length_sample_count: int = 0
    source: Literal[
        "SAME_RACE_OBSERVED",
        "EARLIER_SAME_CIRCUIT",
        "REGULATION_ERA",
        "INSUFFICIENT",
    ]
    uncertainty_seconds_per_lap: float | None = None
    components: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class StrategicActionValue(BaseModel):
    action: str
    compound: str
    delay_laps: int
    terminal_lap: int
    tactical_action: str
    tactical_handoff_lap: int
    tactical_5_lap_delta_seconds: float | None = None
    tactical_5_lap_interval_90: tuple[float, float] | None = None
    cumulative_value_seconds: dict[int, float | None] = Field(default_factory=dict)
    cumulative_ranges_90: dict[int, tuple[float, float] | None] = Field(default_factory=dict)
    terminal_expected_seconds: float | None = None
    time_loss_vs_best_seconds: float | None = None
    physical_position_range_at_handoff: tuple[int, int] | None = None
    terminal_physical_position_range: tuple[int, int] | None = None
    terminal_net_position_range: tuple[int, int] | None = None
    future_stop_obligations: list[str] = Field(default_factory=list)
    break_even_lap: int | None = None
    break_even_lap_range: tuple[int, int] | None = None
    break_even_time_seconds: float | None = None
    traffic_risk: str = "UNKNOWN"
    uncertainty: str = "INSUFFICIENT"
    components: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class StrategicPitComparison(BaseModel):
    current_lap: int
    terminal_lap: int
    strategic_horizon_laps: int
    tactical_horizon_laps: Literal[5] = 5
    driver_id: str
    actions: list[StrategicActionValue] = Field(default_factory=list)
    best_strategic_action: str | None = None
    compound_priors: list[StintCompoundPrior] = Field(default_factory=list)
    common_terminal_point: bool = True
    owed_stop_accounting: bool = True
    public_recommendation_changed: bool = False
    components: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
