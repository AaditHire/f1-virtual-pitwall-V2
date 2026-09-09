"""Focused, pit-cycle-independent short-horizon PIT opportunity signals."""

from __future__ import annotations

from f1_pitwall.domain.paired import PairedComparison, PitOpportunityValue
from f1_pitwall.services.paired import EQUIVALENCE_BANDS, POSITION_EQUIVALENCE_PLACES

TIME_STRONG_FREQUENCY = 0.70
TIME_MARGINAL_FREQUENCY = 0.55
TRAFFIC_PENALTIES = {
    "CLEAR_AIR": 0.0,
    "LIGHT_TRAFFIC": 0.25,
    "MODERATE_TRAFFIC": 0.5,
    "HEAVY_TRAFFIC": 1.0,
    "UNKNOWN": 1.0,
}
WEAK_APPLICABILITY_PENALTY = 1.0


def _time_strength(comparison):
    delta = comparison.median_time_delta_seconds
    frequency = comparison.pit_better_frequency
    if delta is None or frequency is None:
        return "NO_OPPORTUNITY"
    if delta <= -EQUIVALENCE_BANDS[5] and frequency >= TIME_STRONG_FREQUENCY:
        return "STRONG"
    if delta < 0 and frequency >= TIME_MARGINAL_FREQUENCY:
        return "MARGINAL"
    return "NO_OPPORTUNITY"


def _position_strength(comparison):
    delta = comparison.position_delta
    if delta is None:
        return "NO_OPPORTUNITY"
    if delta <= -POSITION_EQUIVALENCE_PLACES:
        return "STRONG"
    if delta < 0:
        return "MARGINAL"
    return "NO_OPPORTUNITY"


def calculate_pit_opportunity(
    comparison: PairedComparison,
    candidate="FULL_EVIDENCE",
    rejoin_traffic="UNKNOWN",
    applicability="USABLE",
):
    """Calculate one of three frozen, fully exposed candidate formulations.

    Pit-cycle position is copied as a diagnostic component and is never used in the
    value or signal gates.
    """
    if candidate not in {"TIME_ONLY", "TIME_POSITION", "FULL_EVIDENCE"}:
        raise ValueError(f"unknown PIT opportunity candidate: {candidate}")
    band = EQUIVALENCE_BANDS[5]
    time_strength = _time_strength(comparison)
    position_strength = _position_strength(comparison)
    time_delta = comparison.median_time_delta_seconds
    position_delta = comparison.position_delta
    pit_frequency = comparison.pit_better_frequency
    downside = comparison.downside_tail.pit_seconds
    interval = next((row.interval_90 for row in comparison.outcomes if row.horizon_laps == 5), None)
    time_value = -time_delta / band if time_delta is not None else None
    position_value = (
        -position_delta / POSITION_EQUIVALENCE_PLACES if position_delta is not None else None
    )
    downside_penalty = max(downside or 0, 0) / band
    interval_penalty = (interval[1] - interval[0]) / (2 * band) if interval is not None else 1.0
    traffic_penalty = TRAFFIC_PENALTIES.get(rejoin_traffic, 1.0)
    applicability_penalty = (
        WEAK_APPLICABILITY_PENALTY if applicability in {"WEAK", "OUT_OF_DOMAIN"} else 0.0
    )

    if candidate == "TIME_ONLY":
        value = time_value
        signal = time_strength
    elif candidate == "TIME_POSITION":
        available = [value for value in (time_value, position_value) if value is not None]
        value = sum(available) if available else None
        if "STRONG" in {time_strength, position_strength}:
            signal = "STRONG"
        elif "MARGINAL" in {time_strength, position_strength}:
            signal = "MARGINAL"
        else:
            signal = "NO_OPPORTUNITY"
    else:
        available = [value for value in (time_value, position_value) if value is not None]
        value = (
            sum(available)
            - downside_penalty
            - interval_penalty
            - traffic_penalty
            - applicability_penalty
            if available
            else None
        )
        evidence = {time_strength, position_strength}
        if "STRONG" in evidence and value is not None and value >= 1:
            signal = "STRONG"
        elif evidence & {"STRONG", "MARGINAL"} and value is not None and value > 0:
            signal = "MARGINAL"
        else:
            signal = "NO_OPPORTUNITY"

    return PitOpportunityValue(
        candidate=candidate,
        signal=signal,
        time_opportunity=time_strength,
        position_opportunity=(position_strength if candidate != "TIME_ONLY" else "NO_OPPORTUNITY"),
        value=round(value, 3) if value is not None else None,
        components={
            "median_time_delta_seconds": time_delta,
            "time_equivalence_band_seconds": band,
            "pit_better_frequency": pit_frequency,
            "median_physical_position_delta": position_delta,
            "physical_position_equivalence_places": POSITION_EQUIVALENCE_PLACES,
            "pit_downside_tail_90_seconds": downside,
            "paired_interval_90": interval,
            "rejoin_traffic": rejoin_traffic,
            "applicability": applicability,
            "normalized_time_value": round(time_value, 3) if time_value is not None else None,
            "normalized_position_value": round(position_value, 3)
            if position_value is not None
            else None,
            "downside_penalty": round(downside_penalty, 3),
            "interval_width_penalty": round(interval_penalty, 3),
            "traffic_penalty": traffic_penalty,
            "applicability_penalty": applicability_penalty,
            "pit_cycle_position_delta_diagnostic": comparison.pit_cycle_position_delta,
            "pit_cycle_used_in_signal": False,
        },
    )
