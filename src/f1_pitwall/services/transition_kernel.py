"""Calibrated one-lap stochastic transitions for fixed short-horizon actions."""

from __future__ import annotations

import random
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from statistics import mean, median

import joblib

from f1_pitwall.domain.simulation import (
    OneLapTransition,
    ProbabilisticRollout,
    RolloutDistribution,
)
from f1_pitwall.services.simulation import build_short_horizon_state, simulate_action

HORIZONS = (1, 3, 5)

# Used only in an unpackaged development checkout. The generated artifact replaces these
# conservative pools in installed builds.
FALLBACK_ARTIFACT = {
    "selected_error_model": "PERSISTENT",
    "persistent_weight": 0.65,
    "residual_pools": {
        "PIT_NOW": {
            "DEFAULT": [-4.51, -2.02, -1.0, -0.25, 0.0, 0.48, 1.49, 4.51],
            "PIT_TRANSIT": [-4.51, -2.02, -1.0, -0.25, 0.0, 0.48, 1.49, 4.51],
            "POST_STOP_LAP_1": [-2.8, -1.4, -0.5, 0.0, 0.4, 1.2, 2.7],
            "NORMAL_RUNNING": [-2.2, -1.0, -0.3, 0.0, 0.3, 1.0, 2.2],
        },
        "EXTEND": {
            "DEFAULT": [-1.8, -0.8, -0.3, 0.0, 0.25, 0.75, 1.8],
            "NORMAL_RUNNING": [-1.8, -0.8, -0.3, 0.0, 0.25, 0.75, 1.8],
            "PIT_TRANSIT": [-4.51, -2.02, -1.0, -0.25, 0.0, 0.48, 1.49, 4.51],
            "POST_STOP_LAP_1": [-2.8, -1.4, -0.5, 0.0, 0.4, 1.2, 2.7],
        },
    },
    "latent_pools": {
        "PIT_NOW": [-2.0, -0.8, -0.2, 0.0, 0.3, 0.9, 2.2],
        "EXTEND": [-1.0, -0.4, -0.1, 0.0, 0.15, 0.45, 1.1],
    },
    "source": "conservative development fallback",
}


@dataclass
class MutableDriverState:
    driver_id: str
    position: int | None
    relative_time: float | None
    lap_deficit: int | None
    compound: str | None
    tyre_age: float | None
    stint: int | None
    recent_pace: float | None
    traffic: str
    pit_count: int
    laps_since_pit: int | None
    active: bool
    uncertainty: float
    phase: str


@lru_cache(maxsize=1)
def _artifact():
    path = files("f1_pitwall").joinpath("models/phase5e_transition_kernel.joblib")
    try:
        return joblib.load(path)
    except FileNotFoundError:
        return FALLBACK_ARTIFACT


def _quantile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _interval(values, coverage, digits=3):
    tail = (1 - coverage) / 2
    return round(_quantile(values, tail), digits), round(_quantile(values, 1 - tail), digits)


def _position_interval(values, coverage):
    interval = _interval(values, coverage, 0)
    return int(interval[0]), int(interval[1])


def _deterministic_curve(direct, initial_gap):
    first = _anchor_outcome(direct, 1).expected_delta_time_seconds
    trace = _anchor_outcome(direct, 5).components.get("lap_by_lap_projection") or []
    if first is None or initial_gap is None or len(trace) < 5:
        return None
    raw = [row.get("driver_gap") for row in trace]
    if any(value is None for value in raw):
        return None
    raw_cumulative = [value - initial_gap for value in raw]
    increments = [first] + [
        raw_cumulative[index] - raw_cumulative[index - 1] for index in range(1, 5)
    ]
    cumulative = []
    for value in increments:
        cumulative.append(value + (cumulative[-1] if cumulative else 0.0))
    return cumulative


def _phase_for_lap(kind, extension, lap_index):
    pit_lap = 1 if kind == "PIT_NOW" else extension + 1
    if lap_index == pit_lap:
        return "PIT_TRANSIT"
    if lap_index == pit_lap + 1:
        return "POST_STOP_LAP_1"
    return "NORMAL_RUNNING"


def _phase_after(kind, extension, lap_index):
    pit_lap = 1 if kind == "PIT_NOW" else extension + 1
    if lap_index == pit_lap:
        return "PIT_EXIT"
    if lap_index == pit_lap + 1:
        return "POST_STOP_LAP_1"
    return "NORMAL_RUNNING"


def _transition_phases(phase):
    if phase == "PIT_TRANSIT":
        return ["PIT_ENTRY", "PIT_TRANSIT", "PIT_EXIT"]
    return [phase]


def _pool(artifact, kind, phase, applicability):
    pools = artifact["residual_pools"][kind]
    grouped = pools.get(f"{phase}:{applicability}")
    values = grouped if grouped and len(grouped) >= 12 else pools.get(phase, pools["DEFAULT"])
    scale = (
        1.0
        if applicability in {"RELIABLE", "USABLE"}
        else 1.5
        if applicability == "WEAK"
        else 2.0
    )
    return [float(value) * scale for value in values]


def _sample_residual(rng, artifact, kind, phase, applicability, error_model, latent):
    noise = rng.choice(_pool(artifact, kind, phase, applicability))
    if error_model == "INDEPENDENT":
        return noise
    weight = float(artifact.get("persistent_weight", 0.65))
    return weight * latent + (1 - weight) * noise


def _anchor_outcome(direct, horizon):
    return next(row for row in direct.outcomes if row.horizon_laps == horizon)


def _position_sample(rng, direct, horizon, cumulative_residual, field_size):
    anchor = _anchor_outcome(direct, horizon)
    if anchor.expected_position is None:
        if anchor.position_range is None:
            return None
        return rng.randint(anchor.position_range[0], anchor.position_range[1])
    nearby = anchor.components.get("nearby_car_projection") or []
    gaps = sorted(
        abs(
            float(row["projected_gap"])
            - float(anchor.components["projected_gap_to_leader_seconds"])
        )
        for row in nearby
        if row.get("projected_gap") is not None
    )
    seconds_per_place = max(0.6, median(gaps[:4]) if gaps else 1.5)
    movement = round(cumulative_residual / seconds_per_place)
    return min(field_size, max(1, anchor.expected_position + movement))


def rollout_action(context, driver_id, action, trajectory_count=100, seed=0, error_model=None):
    """Recursively apply a frozen one-lap residual kernel without replanning."""
    direct = simulate_action(context, driver_id, action)
    state = build_short_horizon_state(context, driver_id)
    kind = direct.kind
    extension = direct.extension_laps or 0
    artifact = _artifact()
    selected = artifact.get("selected_error_model", "PERSISTENT")
    error_model = selected if error_model in {None, "FROZEN_SELECTED"} else error_model
    curve = _deterministic_curve(direct, state.gap_to_leader)
    applicability = {
        horizon: (_anchor_outcome(direct, horizon).applicability or "USABLE")
        for horizon in HORIZONS
    }
    rng = random.Random(seed)
    cumulative = [[0.0] * 5 for _ in range(trajectory_count)]
    positions = [[None] * 5 for _ in range(trajectory_count)]
    increments = [[None] * 5 for _ in range(trajectory_count)]
    if curve is not None:
        deterministic_increments = [curve[0]] + [curve[i] - curve[i - 1] for i in range(1, 5)]
        for trajectory in range(trajectory_count):
            latent = rng.choice(artifact["latent_pools"][kind])
            total = 0.0
            mutable = MutableDriverState(
                driver_id=driver_id,
                position=state.current_position,
                relative_time=state.gap_to_leader,
                lap_deficit=state.laps_behind,
                compound=state.compound,
                tyre_age=state.tyre_age,
                stint=context.driver(driver_id).stint_number,
                recent_pace=state.relative_pace_seconds_per_lap,
                traffic=state.traffic,
                pit_count=state.pit_stops_completed,
                laps_since_pit=None,
                active=state.active,
                uncertainty=0.0,
                phase="PIT_ENTRY" if kind == "PIT_NOW" else "NORMAL_RUNNING",
            )
            for lap_index in range(1, 6):
                phase = _phase_for_lap(kind, extension, lap_index)
                app = applicability[1 if lap_index == 1 else 3 if lap_index <= 3 else 5]
                residual = _sample_residual(
                    rng, artifact, kind, phase, app, error_model, latent
                )
                increment = deterministic_increments[lap_index - 1] + residual
                total += increment
                increments[trajectory][lap_index - 1] = increment
                cumulative[trajectory][lap_index - 1] = total
                mutable.relative_time = (
                    mutable.relative_time + increment if mutable.relative_time is not None else None
                )
                mutable.tyre_age = 0 if phase == "PIT_TRANSIT" else (mutable.tyre_age or 0) + 1
                if phase == "PIT_TRANSIT":
                    mutable.pit_count += 1
                    mutable.laps_since_pit = 0
                    mutable.compound = direct.compound or mutable.compound
                    mutable.stint = (mutable.stint or 0) + 1
                elif mutable.laps_since_pit is not None:
                    mutable.laps_since_pit += 1
                mutable.phase = _phase_after(kind, extension, lap_index)
                anchor_horizon = 1 if lap_index == 1 else 3 if lap_index <= 3 else 5
                positions[trajectory][lap_index - 1] = _position_sample(
                    rng,
                    direct,
                    anchor_horizon,
                    total - curve[lap_index - 1],
                    state.field_size,
                )
    else:
        # Lapped/unknown timing stays in track-order space; seconds are never fabricated.
        for trajectory in range(trajectory_count):
            for lap_index in range(1, 6):
                anchor = _anchor_outcome(
                    direct, 1 if lap_index == 1 else 3 if lap_index <= 3 else 5
                )
                if anchor.position_range:
                    positions[trajectory][lap_index - 1] = rng.randint(*anchor.position_range)

    transitions = []
    for lap_index in range(1, 6):
        phase = _phase_for_lap(kind, extension, lap_index)
        values = [row[lap_index - 1] for row in increments if row[lap_index - 1] is not None]
        pos = [row[lap_index - 1] for row in positions if row[lap_index - 1] is not None]
        transitions.append(
            OneLapTransition(
                lap_index=lap_index,
                from_phase="PIT_ENTRY" if lap_index == 1 and kind == "PIT_NOW" else (
                    _phase_after(kind, extension, lap_index - 1)
                    if lap_index > 1
                    else "NORMAL_RUNNING"
                ),
                to_phase=_phase_after(kind, extension, lap_index),
                transition_phases=_transition_phases(phase),
                median_relative_delta_seconds=round(median(values), 3) if values else None,
                interval_50=_interval(values, 0.5) if values else None,
                interval_80=_interval(values, 0.8) if values else None,
                interval_90=_interval(values, 0.9) if values else None,
                median_position=round(median(pos)) if pos else None,
                position_range_80=_position_interval(pos, 0.8) if pos else None,
            )
        )

    outcomes = []
    for horizon in HORIZONS:
        values = [row[horizon - 1] for row in cumulative] if curve is not None else []
        pos = [row[horizon - 1] for row in positions if row[horizon - 1] is not None]
        anchor = _anchor_outcome(direct, horizon)
        outcomes.append(
            RolloutDistribution(
                horizon_laps=horizon,
                trajectory_count=trajectory_count,
                median_relative_delta_seconds=round(median(values), 3) if values else None,
                mean_relative_delta_seconds=round(mean(values), 3) if values else None,
                interval_50=_interval(values, 0.5) if values else None,
                interval_80=_interval(values, 0.8) if values else None,
                interval_90=_interval(values, 0.9) if values else None,
                median_position=round(median(pos)) if pos else None,
                position_range_50=_position_interval(pos, 0.5) if pos else anchor.position_range,
                position_range_80=_position_interval(pos, 0.8) if pos else anchor.position_range,
                position_range_90=_position_interval(pos, 0.9) if pos else anchor.position_range,
                median_net_pit_cycle_position=anchor.net_race_position_estimate,
                net_pit_cycle_position_range_80=anchor.net_race_position_range,
                applicability=applicability[horizon],
            )
        )
    warnings = [
        "The action remains fixed throughout the rollout; no strategic replanning occurs.",
        "Safety cars, rain, failures and whole-race evolution are outside this kernel.",
    ]
    if curve is None:
        warnings.append(
            "No seconds distribution is produced for a lap-deficit or unknown-gap state."
        )
    return ProbabilisticRollout(
        action=action,
        kind=kind,
        seed=seed,
        trajectory_count=trajectory_count,
        error_model=error_model,
        state=state,
        transitions=transitions,
        outcomes=outcomes,
        components={
            "architecture": "deterministic Phase 5C transition plus empirical residual kernel",
            "artifact_source": artifact.get("source"),
            "persistent_weight": artifact.get("persistent_weight")
            if error_model == "PERSISTENT"
            else 0.0,
            "local_field_only": True,
            "seconds_gap_fabricated": False,
            "direct_baseline": direct.model_dump(),
            "deterministic_one_lap_curve": curve,
        },
        warnings=warnings,
    )
