"""Frontend-safe paired PIT-versus-EXTEND counterfactual models."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from f1_pitwall.domain.analysis import TrafficLevel
from f1_pitwall.domain.simulation import RolloutDistribution

PitWindowState = Literal[
    "PIT_WINDOW_CLOSED",
    "PIT_WINDOW_OPEN",
    "PIT_WINDOW_STRONG",
    "PIT_WINDOW_UNCERTAIN",
]
OpportunityStrength = Literal["NO_OPPORTUNITY", "MARGINAL", "STRONG"]
OpportunityCandidate = Literal["TIME_ONLY", "TIME_POSITION", "FULL_EVIDENCE"]


class PairedRegret(BaseModel):
    pit_seconds: float | None = None
    extend_seconds: float | None = None


class PairedActionValue(BaseModel):
    pit_time_advantage_seconds: float | None = None
    pit_track_position_advantage: float | None = None
    pit_cycle_position_advantage: float | None = None


class PairedHorizonDistribution(BaseModel):
    horizon_laps: Literal[1, 3, 5]
    trajectory_count: int
    median_time_delta_seconds: float | None = None
    mean_time_delta_seconds: float | None = None
    interval_50: tuple[float, float] | None = None
    interval_80: tuple[float, float] | None = None
    interval_90: tuple[float, float] | None = None
    median_track_position_delta: float | None = None
    median_pit_cycle_position_delta: float | None = None
    pit_net_position_range_80: tuple[int, int] | None = None
    extend_net_position_range_80: tuple[int, int] | None = None
    pit_better_frequency: float | None = None
    extend_better_frequency: float | None = None
    equivalence_frequency: float | None = None
    expected_regret: PairedRegret = Field(default_factory=PairedRegret)
    downside_tail_90: PairedRegret = Field(default_factory=PairedRegret)
    action_value: PairedActionValue = Field(default_factory=PairedActionValue)
    frequency_label: str = "matched-trajectory simulation frequency; not global calibration"


class PairedComparison(BaseModel):
    pit_action: str
    extend_action: str
    decision_horizon_laps: Literal[5] = 5
    median_time_delta_seconds: float | None = None
    position_delta: float | None = None
    pit_cycle_position_delta: float | None = None
    pit_net_position_range_80: tuple[int, int] | None = None
    extend_net_position_range_80: tuple[int, int] | None = None
    pit_better_frequency: float | None = None
    extend_better_frequency: float | None = None
    equivalence_frequency: float | None = None
    expected_regret: PairedRegret = Field(default_factory=PairedRegret)
    downside_tail: PairedRegret = Field(default_factory=PairedRegret)
    action_value: PairedActionValue = Field(default_factory=PairedActionValue)
    pit_window_state: PitWindowState = "PIT_WINDOW_UNCERTAIN"
    outcomes: list[PairedHorizonDistribution] = Field(default_factory=list)
    components: dict[str, Any] = Field(default_factory=dict)


class PitWindow(BaseModel):
    state: PitWindowState
    best_compound: str | None = None
    paired_advantage_seconds: float | None = None
    pit_cycle_position_advantage: float | None = None
    traffic: TrafficLevel = "UNKNOWN"
    rejoin: str | None = None
    uncertainty: str
    reason: str


class PairedCandidateSet(BaseModel):
    trajectory_count: int
    seed: int
    equivalence_band_seconds: float
    pit_frequency_threshold: float
    strong_pit_frequency_threshold: float
    marginal_outcomes: dict[str, list[RolloutDistribution]] = Field(default_factory=dict)
    comparisons: list[PairedComparison] = Field(default_factory=list)
    best_comparison: PairedComparison | None = None
    components: dict[str, Any] = Field(default_factory=dict)


class PitOpportunityValue(BaseModel):
    """Interpretable short-horizon PIT evidence; negative deltas favor PIT."""

    candidate: OpportunityCandidate
    signal: OpportunityStrength
    time_opportunity: OpportunityStrength
    position_opportunity: OpportunityStrength
    value: float | None = None
    components: dict[str, Any] = Field(default_factory=dict)
