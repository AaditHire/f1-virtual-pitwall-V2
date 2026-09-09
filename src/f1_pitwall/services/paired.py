"""Matched-trajectory PIT-versus-EXTEND evaluation."""

from __future__ import annotations

import random
from statistics import mean, median

from f1_pitwall.domain.paired import (
    PairedActionValue,
    PairedCandidateSet,
    PairedComparison,
    PairedHorizonDistribution,
    PairedRegret,
)
from f1_pitwall.domain.simulation import RolloutDistribution
from f1_pitwall.domain.strategy import StrategyAction
from f1_pitwall.services.simulation import build_short_horizon_state, simulate_action
from f1_pitwall.services.transition_kernel import (
    HORIZONS,
    _anchor_outcome,
    _artifact,
    _calibrated_position_interval,
    _calibrated_time_interval,
    _deterministic_curve,
    _interval,
    _phase_for_lap,
    _pool,
    _quantile,
)

# Frozen before Phase 6B validation. These are the chronological 2024 factual MAEs from
# the Phase 5F 2021-2023-fit evaluation, not values selected to force a PIT frequency.
EQUIVALENCE_BANDS = {1: 0.948, 3: 1.791, 5: 2.482}
POSITION_EQUIVALENCE_PLACES = 1
PIT_FREQUENCY_THRESHOLD = 0.55
STRONG_PIT_FREQUENCY_THRESHOLD = 0.70


def _quantile_draw(ordered, quantile):
    return ordered[min(int(quantile * len(ordered)), len(ordered) - 1)]


def _position_sample(anchor, seconds_per_place, cumulative_residual, field_size, quantile):
    if anchor.expected_position is None:
        if anchor.position_range is None:
            return None
        low, high = anchor.position_range
        return min(high, low + int(quantile * (high - low + 1)))
    movement = round(cumulative_residual / seconds_per_place)
    return min(field_size, max(1, anchor.expected_position + movement))


def _action_paths(context, driver_id, actions, trajectory_count, seed):
    artifact = _artifact()
    state = build_short_horizon_state(context, driver_id)
    direct = {action.id: simulate_action(context, driver_id, action.id) for action in actions}
    curves = {
        action.id: _deterministic_curve(direct[action.id], state.gap_to_leader)
        for action in actions
    }
    applicability = {
        action.id: {
            horizon: (_anchor_outcome(direct[action.id], horizon).applicability or "USABLE")
            for horizon in HORIZONS
        }
        for action in actions
    }
    position_parameters = {}
    for action in actions:
        position_parameters[action.id] = {}
        for horizon in HORIZONS:
            anchor = _anchor_outcome(direct[action.id], horizon)
            nearby = anchor.components.get("nearby_car_projection") or []
            gaps = sorted(
                abs(
                    float(row["projected_gap"])
                    - float(anchor.components["projected_gap_to_leader_seconds"])
                )
                for row in nearby
                if row.get("projected_gap") is not None
            )
            position_parameters[action.id][horizon] = (
                anchor,
                max(0.6, median(gaps[:4]) if gaps else 1.5),
            )
    latent_values = {
        kind: sorted(float(value) for value in artifact["latent_pools"][kind])
        for kind in {action.kind for action in actions}
    }
    noise_values = {
        action.id: {
            lap_index: sorted(
                _pool(
                    artifact,
                    action.kind,
                    _phase_for_lap(action.kind, direct[action.id].extension_laps or 0, lap_index),
                    applicability[action.id][1 if lap_index == 1 else 3 if lap_index <= 3 else 5],
                )
            )
            for lap_index in range(1, 6)
        }
        for action in actions
    }
    shared_rng = random.Random(seed)
    pit_rng = random.Random(seed ^ 0x5A17)
    extend_rng = random.Random(seed ^ 0xE771)
    shared = [
        {
            "latent": shared_rng.random(),
            "laps": [shared_rng.random() for _ in range(5)],
            "positions": [shared_rng.random() for _ in range(5)],
        }
        for _ in range(trajectory_count)
    ]
    pit_specific = [[pit_rng.random() for _ in range(5)] for _ in range(trajectory_count)]
    extend_specific = [[extend_rng.random() for _ in range(5)] for _ in range(trajectory_count)]
    paths = {}
    persistent_weight = float(artifact.get("persistent_weight", 0.65))
    for action in actions:
        kind = action.kind
        extension = direct[action.id].extension_laps or 0
        curve = curves[action.id]
        cumulative = [[None] * 5 for _ in range(trajectory_count)]
        positions = [[None] * 5 for _ in range(trajectory_count)]
        for trajectory in range(trajectory_count):
            total = 0.0
            latent = _quantile_draw(latent_values[kind], shared[trajectory]["latent"])
            for lap_index in range(1, 6):
                horizon = 1 if lap_index == 1 else 3 if lap_index <= 3 else 5
                phase = _phase_for_lap(kind, extension, lap_index)
                if phase == "NORMAL_RUNNING":
                    draw = shared[trajectory]["laps"][lap_index - 1]
                elif kind == "PIT_NOW":
                    draw = pit_specific[trajectory][lap_index - 1]
                else:
                    draw = extend_specific[trajectory][lap_index - 1]
                noise = _quantile_draw(noise_values[action.id][lap_index], draw)
                residual = persistent_weight * latent + (1 - persistent_weight) * noise
                if curve is not None:
                    deterministic = curve[lap_index - 1] - (
                        curve[lap_index - 2] if lap_index > 1 else 0
                    )
                    total += deterministic + residual
                    cumulative[trajectory][lap_index - 1] = total
                    anchor, seconds_per_place = position_parameters[action.id][horizon]
                    positions[trajectory][lap_index - 1] = _position_sample(
                        anchor,
                        seconds_per_place,
                        total - curve[lap_index - 1],
                        state.field_size,
                        shared[trajectory]["positions"][lap_index - 1],
                    )
                else:
                    anchor, seconds_per_place = position_parameters[action.id][horizon]
                    positions[trajectory][lap_index - 1] = _position_sample(
                        anchor,
                        seconds_per_place,
                        0,
                        state.field_size,
                        shared[trajectory]["positions"][lap_index - 1],
                    )
        paths[action.id] = {
            "action": action,
            "direct": direct[action.id],
            "curve": curve,
            "applicability": applicability[action.id],
            "cumulative": cumulative,
            "positions": positions,
        }
    return state, artifact, paths


def _marginal(path, artifact, trajectory_count, field_size):
    result = []
    kind = path["action"].kind
    for horizon in HORIZONS:
        values = [row[horizon - 1] for row in path["cumulative"] if row[horizon - 1] is not None]
        positions = [row[horizon - 1] for row in path["positions"] if row[horizon - 1] is not None]
        anchor = _anchor_outcome(path["direct"], horizon)
        app = path["applicability"][horizon]
        result.append(
            RolloutDistribution(
                horizon_laps=horizon,
                trajectory_count=trajectory_count,
                median_relative_delta_seconds=round(median(values), 3) if values else None,
                mean_relative_delta_seconds=round(mean(values), 3) if values else None,
                interval_50=_calibrated_time_interval(artifact, kind, horizon, app, 50, values)
                if values
                else None,
                interval_80=_calibrated_time_interval(artifact, kind, horizon, app, 80, values)
                if values
                else None,
                interval_90=_calibrated_time_interval(artifact, kind, horizon, app, 90, values)
                if values
                else None,
                median_position=round(median(positions)) if positions else None,
                position_range_50=_calibrated_position_interval(
                    artifact, kind, horizon, app, 50, positions, field_size
                )
                if positions
                else anchor.position_range,
                position_range_80=_calibrated_position_interval(
                    artifact, kind, horizon, app, 80, positions, field_size
                )
                if positions
                else anchor.position_range,
                position_range_90=_calibrated_position_interval(
                    artifact, kind, horizon, app, 90, positions, field_size
                )
                if positions
                else anchor.position_range,
                median_net_pit_cycle_position=anchor.net_race_position_estimate,
                net_pit_cycle_position_range_80=anchor.net_race_position_range,
                applicability=app,
            )
        )
    return result


def _regret(values, band):
    pit = [max(value - band, 0) for value in values]
    extend = [max(-value - band, 0) for value in values]
    return (
        PairedRegret(pit_seconds=round(mean(pit), 3), extend_seconds=round(mean(extend), 3)),
        PairedRegret(
            pit_seconds=round(_quantile(pit, 0.9), 3),
            extend_seconds=round(_quantile(extend, 0.9), 3),
        ),
    )


def _paired_horizon(pit, extend, horizon, trajectory_count):
    pit_values = [row[horizon - 1] for row in pit["cumulative"]]
    extend_values = [row[horizon - 1] for row in extend["cumulative"]]
    timed = [
        left - right
        for left, right in zip(pit_values, extend_values, strict=True)
        if left is not None and right is not None
    ]
    pit_positions = [row[horizon - 1] for row in pit["positions"]]
    extend_positions = [row[horizon - 1] for row in extend["positions"]]
    position_deltas = [
        left - right
        for left, right in zip(pit_positions, extend_positions, strict=True)
        if left is not None and right is not None
    ]
    pit_anchor = _anchor_outcome(pit["direct"], horizon)
    extend_anchor = _anchor_outcome(extend["direct"], horizon)
    net_delta = (
        pit_anchor.net_race_position_estimate - extend_anchor.net_race_position_estimate
        if pit_anchor.net_race_position_estimate is not None
        and extend_anchor.net_race_position_estimate is not None
        else None
    )
    band = EQUIVALENCE_BANDS[horizon]
    evidence = timed or position_deltas
    threshold = band if timed else 0
    pit_frequency = mean(value < -threshold for value in evidence) if evidence else None
    extend_frequency = mean(value > threshold for value in evidence) if evidence else None
    equivalence_frequency = (
        mean(abs(value) <= threshold for value in evidence) if evidence else None
    )
    expected, tail = _regret(timed, band) if timed else (PairedRegret(), PairedRegret())
    median_delta = median(timed) if timed else None
    track_delta = median(position_deltas) if position_deltas else None
    value = PairedActionValue(
        pit_time_advantage_seconds=round(-median_delta, 3) if median_delta is not None else None,
        pit_track_position_advantage=round(-track_delta, 3) if track_delta is not None else None,
        pit_cycle_position_advantage=-net_delta if net_delta is not None else None,
    )
    return PairedHorizonDistribution(
        horizon_laps=horizon,
        trajectory_count=trajectory_count,
        median_time_delta_seconds=round(median_delta, 3) if median_delta is not None else None,
        mean_time_delta_seconds=round(mean(timed), 3) if timed else None,
        interval_50=_interval(timed, 0.5) if timed else None,
        interval_80=_interval(timed, 0.8) if timed else None,
        interval_90=_interval(timed, 0.9) if timed else None,
        median_track_position_delta=round(track_delta, 3) if track_delta is not None else None,
        median_pit_cycle_position_delta=net_delta,
        pit_net_position_range_80=pit_anchor.net_race_position_range,
        extend_net_position_range_80=extend_anchor.net_race_position_range,
        pit_better_frequency=round(pit_frequency, 3) if pit_frequency is not None else None,
        extend_better_frequency=round(extend_frequency, 3)
        if extend_frequency is not None
        else None,
        equivalence_frequency=round(equivalence_frequency, 3)
        if equivalence_frequency is not None
        else None,
        expected_regret=expected,
        downside_tail_90=tail,
        action_value=value,
    )


def _window_state(outcome):
    pit_frequency = outcome.pit_better_frequency or 0
    extend_frequency = outcome.extend_better_frequency or 0
    time_delta = outcome.median_time_delta_seconds
    cycle_delta = outcome.median_pit_cycle_position_delta
    band = EQUIVALENCE_BANDS[5]
    position_open = cycle_delta is not None and cycle_delta <= -(POSITION_EQUIVALENCE_PLACES + 1)
    strong_time = (
        time_delta is not None
        and time_delta <= -band
        and pit_frequency >= STRONG_PIT_FREQUENCY_THRESHOLD
    )
    # Phase 6C's frozen audit found that the pit-cycle estimate did not converge as well
    # as physical position at +5. It may open a window, but cannot independently promote
    # PIT to STRONG until that estimate is improved and revalidated.
    if strong_time:
        return "PIT_WINDOW_STRONG"
    if pit_frequency >= PIT_FREQUENCY_THRESHOLD or position_open:
        return "PIT_WINDOW_OPEN"
    if extend_frequency >= STRONG_PIT_FREQUENCY_THRESHOLD and (
        not position_open or (time_delta is not None and time_delta > 2 * band)
    ):
        return "PIT_WINDOW_CLOSED"
    return "PIT_WINDOW_UNCERTAIN"


def _comparison(pit, extend, trajectory_count):
    outcomes = [_paired_horizon(pit, extend, horizon, trajectory_count) for horizon in HORIZONS]
    decision = next(row for row in outcomes if row.horizon_laps == 5)
    state = _window_state(decision)
    return PairedComparison(
        pit_action=pit["action"].id,
        extend_action=extend["action"].id,
        median_time_delta_seconds=decision.median_time_delta_seconds,
        position_delta=decision.median_track_position_delta,
        pit_cycle_position_delta=decision.median_pit_cycle_position_delta,
        pit_net_position_range_80=decision.pit_net_position_range_80,
        extend_net_position_range_80=decision.extend_net_position_range_80,
        pit_better_frequency=decision.pit_better_frequency,
        extend_better_frequency=decision.extend_better_frequency,
        equivalence_frequency=decision.equivalence_frequency,
        expected_regret=decision.expected_regret,
        downside_tail=decision.downside_tail_90,
        action_value=decision.action_value,
        pit_window_state=state,
        outcomes=outcomes,
        components={
            "equivalence_band_seconds": EQUIVALENCE_BANDS[5],
            "time_delta_sign": "PIT minus EXTEND; negative favors PIT",
            "position_delta_sign": "PIT minus EXTEND; negative favors PIT",
            "track_position_is_distinct_from_pit_cycle_position": True,
        },
    )


def _comparison_rank(comparison):
    state_rank = {
        "PIT_WINDOW_STRONG": 0,
        "PIT_WINDOW_OPEN": 1,
        "PIT_WINDOW_UNCERTAIN": 2,
        "PIT_WINDOW_CLOSED": 3,
    }
    return (
        state_rank[comparison.pit_window_state],
        comparison.expected_regret.pit_seconds
        if comparison.expected_regret.pit_seconds is not None
        else 1e9,
        comparison.pit_cycle_position_delta
        if comparison.pit_cycle_position_delta is not None
        else 99,
        comparison.median_time_delta_seconds
        if comparison.median_time_delta_seconds is not None
        else 1e9,
    )


def evaluate_paired_candidates(context, driver_id, actions, trajectory_count=100, seed=0):
    """Evaluate all legal actions with shared common-random-number trajectories."""
    if not actions:
        return PairedCandidateSet(
            trajectory_count=trajectory_count,
            seed=seed,
            equivalence_band_seconds=EQUIVALENCE_BANDS[5],
            pit_frequency_threshold=PIT_FREQUENCY_THRESHOLD,
            strong_pit_frequency_threshold=STRONG_PIT_FREQUENCY_THRESHOLD,
        )
    actions = list(actions)
    if any(action.kind == "EXTEND" for action in actions) and not any(
        action.id == "EXTEND_5" for action in actions
    ):
        actions.append(
            StrategyAction(
                id="EXTEND_5",
                kind="EXTEND",
                extension_laps=5,
                warnings=[
                    "Paired control: remain on the current tyre for the complete five-lap "
                    "operating envelope; no pit loss is applied."
                ],
            )
        )
    state, artifact, paths = _action_paths(context, driver_id, actions, trajectory_count, seed)
    marginal = {
        action.id: _marginal(paths[action.id], artifact, trajectory_count, state.field_size)
        for action in actions
    }
    pits = [paths[action.id] for action in actions if action.kind == "PIT_NOW"]
    best_extend = paths.get("EXTEND_5")
    comparisons = [_comparison(pit, best_extend, trajectory_count) for pit in pits if best_extend]
    best = min(comparisons, key=_comparison_rank) if comparisons else None
    return PairedCandidateSet(
        trajectory_count=trajectory_count,
        seed=seed,
        equivalence_band_seconds=EQUIVALENCE_BANDS[5],
        pit_frequency_threshold=PIT_FREQUENCY_THRESHOLD,
        strong_pit_frequency_threshold=STRONG_PIT_FREQUENCY_THRESHOLD,
        marginal_outcomes=marginal,
        comparisons=comparisons,
        best_comparison=best,
        components={
            "shared": [
                "driver/team latent pace quantile",
                "normal-running lap variation quantile",
                "field/position variation quantile",
            ],
            "action_specific": [
                "pit loss and transit",
                "rejoin and post-stop traffic",
                "warm-up and fresh-tyre residual",
                "delayed-stop transition",
            ],
            "marginal_calibration_preserved": True,
            "extend_selection": (
                "All PIT compounds are paired against EXTEND_5, which stays on the current "
                "tyre and pays no pit loss inside the five-lap envelope."
            ),
            "seconds_gap_fabricated": False,
            "threshold_provenance": (
                "2024 chronological factual MAE after fitting on 2021-2023; frozen before "
                "2025 Phase 6B validation"
            ),
        },
    )
