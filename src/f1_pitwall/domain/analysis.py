"""Public, JSON-safe engineering results. Null means unavailable, never zero."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from f1_pitwall.domain.replay import DriverRaceState

Confidence = Literal["HIGH", "MEDIUM", "LOW", "INSUFFICIENT"]
TrafficLevel = Literal["CLEAR_AIR", "LIGHT_TRAFFIC", "MODERATE_TRAFFIC", "HEAVY_TRAFFIC", "UNKNOWN"]


class Evidence(BaseModel):
    confidence: Confidence = "INSUFFICIENT"
    method: str
    components: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class PaceAnalysis(Evidence):
    seconds: float | None = None
    lap_numbers: list[int] = Field(default_factory=list)
    sample_count: int = 0


class TyreAnalysis(Evidence):
    driver_id: str
    compound: str | None = None
    stint_number: int | None = None
    tyre_age: float | None = None
    degradation_sec_per_lap: float | None = None
    recent_trend_sec_per_lap: float | None = None
    recent_pace_delta: float | None = None
    estimated_competitive_life_laps: float | None = None
    expected_3_lap_pace_loss: float | None = None
    expected_5_lap_pace_loss: float | None = None
    reference_coverage: float | None = None
    sample_count: int = 0


class FreshTyreAnalysis(Evidence):
    driver_id: str
    old_compound: str | None = None
    new_compound: str | None = None
    fresh_tyre_delta: float | None = None
    warm_up_delta_seconds: float | None = None
    sample_count: int = 0


class PitLossAnalysis(Evidence):
    total_seconds: float | None = None
    entry_seconds: float | None = None
    transit_seconds: float | None = None
    stationary_seconds: float | None = None
    exit_warm_up_seconds: float | None = None
    sample_count: int = 0


class RejoinAnalysis(Evidence):
    driver_id: str
    projected_position: int | None = None
    position_range: tuple[int, int] | None = None
    gap_ahead: float | None = None
    gap_behind: float | None = None
    ahead_id: str | None = None
    behind_id: str | None = None
    nearby_drivers: list[str] = Field(default_factory=list)
    traffic: TrafficLevel = "UNKNOWN"


class TrafficAnalysis(Evidence):
    driver_id: str
    status: TrafficLevel = "UNKNOWN"
    ahead_id: str | None = None
    behind_id: str | None = None
    gap_ahead: float | None = None
    gap_behind: float | None = None
    relative_pace_ahead: float | None = None
    relative_pace_behind: float | None = None
    clear_air: bool | None = None
    slower_car_blockage: bool | None = None
    rejoin_density: int | None = None


class PitWindowAnalysis(Evidence):
    current_lap: int
    rejoin: RejoinAnalysis
    clear_air_opportunity: bool | None = None
    traffic_risk: TrafficLevel = "UNKNOWN"


class PairAnalysis(Evidence):
    kind: Literal["undercut", "overcut"]
    driver_id: str
    target_id: str
    current_gap: float | None = None
    required_gain: float | None = None
    estimated_fresh_tyre_gain: float | None = None
    traffic_penalty: float | None = None
    estimated_margin: float | None = None
    opportunity: Literal["YES", "MARGINAL", "NO", "UNKNOWN"] = "UNKNOWN"
    conditions_required: list[str] = Field(default_factory=list)
    fresh_tyre: FreshTyreAnalysis | None = None


class DriverAnalysis(BaseModel):
    year: int
    round: int
    lap: int
    cutoff_seconds: float
    driver: DriverRaceState
    recent_pace: PaceAnalysis
    tyres: TyreAnalysis
    pit_loss: PitLossAnalysis
    rejoin: RejoinAnalysis
    traffic: TrafficAnalysis
    pit_window: PitWindowAnalysis
