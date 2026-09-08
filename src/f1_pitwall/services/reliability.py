"""Frozen Phase 5D reliability envelope for PIT transition outcomes."""

from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np

FEATURES = (
    "phase5c_point",
    "current_position",
    "race_progress",
    "relative_pace",
    "pace_lap_count",
    "tyre_age",
    "pit_stops_completed",
    "pit_loss",
    "pit_loss_mad",
    "pit_loss_samples",
    "rejoin_position",
    "rejoin_width",
    "nearby_count",
    "unknown_cars",
    "uncertain_crossings",
    "prior_races",
    "prior_pit_samples",
    "position_change_frequency",
    "fresh_delta",
    "fresh_samples",
    "fresh_mad",
    "traffic_code",
    "compound_code",
    "gap_kind_code",
)
TRAFFIC_CODES = {
    "CLEAR_AIR": 0,
    "LIGHT_TRAFFIC": 1,
    "MODERATE_TRAFFIC": 2,
    "HEAVY_TRAFFIC": 3,
    "UNKNOWN": 4,
}
COMPOUND_CODES = {"HARD": 0, "MEDIUM": 1, "SOFT": 2, "INTERMEDIATE": 3, "WET": 4}
GAP_CODES = {"TIME": 0, "LAP_DEFICIT": 1, "UNKNOWN": 2}


@lru_cache(maxsize=1)
def _artifact():
    path = Path(__file__).parents[1] / "models" / "phase5d_reliability.joblib"
    return joblib.load(path)


def _traffic_flags(values):
    return {
        "pack_rejoin": values["nearby_count"] >= 4,
        "multiple_uncertain_crossings": values["uncertain_crossings"] >= 2,
        "unknown_same_lap_cars": values["unknown_cars"] > 0,
        "wide_rejoin_range": values["rejoin_width"] >= 4,
        "heavy_or_unknown_traffic": values["traffic_code"] >= 3,
    }


def _feature_values(context, state, outcome):
    components = outcome.components
    pit = components.get("pit_loss_components") or {}
    rejoin = components.get("predicted_rejoin") or {}
    profile = components.get("circuit_profile") or {}
    nearby = components.get("nearby_car_projection") or []
    return {
        "phase5c_point": outcome.expected_delta_time_seconds,
        "current_position": state.current_position,
        "race_progress": context.state.current_lap
        / max(context.state.total_scheduled_laps or context.state.current_lap, 1),
        "relative_pace": state.relative_pace_seconds_per_lap,
        "pace_lap_count": len(state.relative_pace_laps),
        "tyre_age": state.tyre_age,
        "pit_stops_completed": state.pit_stops_completed,
        "pit_loss": pit.get("combined_observed_stop_lap_residual_seconds"),
        "pit_loss_mad": pit.get("residual_mad_seconds"),
        "pit_loss_samples": pit.get("sample_count"),
        "rejoin_position": rejoin.get("position"),
        "rejoin_width": rejoin.get("position_range_width") or 0,
        "nearby_count": len(nearby),
        "unknown_cars": components.get("unknown_same_lap_cars") or 0,
        "uncertain_crossings": sum(
            bool(item.get("crossing_treated_as_uncertain")) for item in nearby
        ),
        "prior_races": profile.get("prior_races"),
        "prior_pit_samples": profile.get("pit_loss_samples"),
        "position_change_frequency": profile.get("position_change_frequency"),
        "fresh_delta": components.get("raw_fresh_delta_seconds_per_lap"),
        "fresh_samples": components.get("fresh_tyre_sample_count"),
        "fresh_mad": components.get("fresh_tyre_sample_mad_seconds"),
        "traffic_code": TRAFFIC_CODES.get(rejoin.get("traffic") or state.traffic, 4),
        "compound_code": COMPOUND_CODES.get(state.compound, 5),
        "gap_kind_code": GAP_CODES[state.gap_kind],
    }


def apply_pit_reliability(context, state, outcome):
    bundle = _artifact()
    horizon = outcome.horizon_laps
    model = bundle["models"][horizon]["risk"]
    frozen = bundle["frozen"][horizon]
    values = _feature_values(context, state, outcome)
    row = np.asarray(
        [[float(values[name]) if values[name] is not None else np.nan for name in FEATURES]]
    )
    risk = max(0.5, float(np.expm1(model.predict(row)[0])))
    gates = _traffic_flags(values)
    if state.gap_kind != "TIME" or any(gates[name] for name in frozen["traffic_gates"]):
        applicability = "OUT_OF_DOMAIN"
    elif risk <= frozen["risk_thresholds"][0]:
        applicability = "RELIABLE"
    elif risk <= frozen["risk_thresholds"][1]:
        applicability = "USABLE"
    elif risk <= frozen["risk_thresholds"][2]:
        applicability = "WEAK"
    else:
        applicability = "OUT_OF_DOMAIN"
    width80 = risk * frozen["normalized_q80"]
    width90 = risk * frozen["normalized_q90"]
    point = outcome.expected_delta_time_seconds
    outcome.prediction_interval_80 = (round(point - width80, 3), round(point + width80, 3))
    outcome.prediction_interval_90 = (round(point - width90, 3), round(point + width90, 3))
    outcome.uncertainty_seconds = round(width90, 3)
    outcome.applicability = applicability
    outcome.confidence = (
        "MEDIUM"
        if applicability == "RELIABLE"
        else "LOW"
        if applicability in {"USABLE", "WEAK"}
        else "INSUFFICIENT"
    )
    outcome.components["reliability_envelope"] = {
        "predicted_absolute_error_seconds": round(risk, 3),
        "calibration_split": "2024 development",
        "interval_method": "risk-scaled split conformal absolute residual",
        "active_traffic_gates": [name for name, active in gates.items() if active],
        "validated_traffic_gates": frozen["traffic_gates"],
    }
    if applicability == "OUT_OF_DOMAIN":
        outcome.warnings.append(
            "This PIT transition is outside the validated precision envelope; use the wide "
            "interval rather than the point estimate alone."
        )
    return outcome
