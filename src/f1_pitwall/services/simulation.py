"""Independent deterministic 3/5-lap transition model for candidate actions."""

import asyncio
from collections import defaultdict
from math import ceil, sqrt
from statistics import median

from f1_pitwall.core.exceptions import NotFound
from f1_pitwall.domain.simulation import (
    CounterfactualActionOutcome,
    CounterfactualComparison,
    HorizonOutcome,
    ShortHorizonState,
)
from f1_pitwall.services.analysis_context import AnalysisContext
from f1_pitwall.services.fresh_tyre import estimate_fresh_tyre_delta
from f1_pitwall.services.pace import get_recent_pace, get_stint_pace
from f1_pitwall.services.pit_analysis import estimate_pit_loss, predict_pit_rejoin
from f1_pitwall.services.reliability import apply_pit_reliability
from f1_pitwall.services.strategy import generate_actions
from f1_pitwall.services.traffic import analyze_traffic, blockage_penalty

HORIZONS = (1, 3, 5)
PACE_MAE_SECONDS = 0.506
FRESH_MAE_SECONDS = 0.911
FRESH_ZERO_MAE_SECONDS = 1.399
FRESH_RELIABILITY = 1 - FRESH_MAE_SECONDS / FRESH_ZERO_MAE_SECONDS
REJOIN_MAE_POSITIONS = 1.013

# Derived only from the four-race 2023 Phase 5 development split and frozen before evaluating
# the five-race 2024 holdout.
DEFAULT_CALIBRATION = {
    "EXTEND": {
        1: {"bias_seconds": -0.147, "mae_seconds": 0.791},
        3: {"bias_seconds": -0.048, "mae_seconds": 2.405},
        5: {"bias_seconds": 0.0, "mae_seconds": 3.29},
    },
    "PIT_NOW": {
        1: {"bias_seconds": -1.365, "mae_seconds": 2.851},
        3: {"bias_seconds": -2.415, "mae_seconds": 3.81},
        5: {"bias_seconds": -4.467, "mae_seconds": 4.977},
    },
}

# Generated from thirteen cached 2021-2023 development histories. These are measured
# normalized profiles, not manually scored circuit labels. They are eligible only after the
# recorded source year and are replaced by an explicitly supplied chronological profile in
# evaluation.
HISTORICAL_CIRCUIT_PROFILES = {
    "bahrain": {
        "source_year": 2023,
        "prior_races": 3,
        "typical_pit_loss_seconds": 24.522,
        "pit_loss_mad_seconds": 0.879,
        "pit_loss_samples": 59,
        "position_change_frequency": 0.705368,
        "overtake_threshold_seconds": 0.5,
    },
    "monaco": {
        "source_year": 2023,
        "prior_races": 3,
        "typical_pit_loss_seconds": 22.429,
        "pit_loss_mad_seconds": 2.27,
        "pit_loss_samples": 17,
        "position_change_frequency": 0.154371,
        "overtake_threshold_seconds": 0.883,
    },
    "catalunya": {
        "source_year": 2023,
        "prior_races": 3,
        "typical_pit_loss_seconds": 23.283,
        "pit_loss_mad_seconds": 0.935,
        "pit_loss_samples": 92,
        "position_change_frequency": 0.449578,
        "overtake_threshold_seconds": 0.5,
    },
    "monza": {
        "source_year": 2023,
        "prior_races": 3,
        "typical_pit_loss_seconds": 25.072,
        "pit_loss_mad_seconds": 0.701,
        "pit_loss_samples": 41,
        "position_change_frequency": 0.441166,
        "overtake_threshold_seconds": 0.5,
    },
    "suzuka": {
        "source_year": 2022,
        "prior_races": 1,
        "typical_pit_loss_seconds": 23.317,
        "pit_loss_mad_seconds": 0.658,
        "pit_loss_samples": 8,
        "position_change_frequency": 0.740079,
        "overtake_threshold_seconds": 0.5,
    },
}

# Fixed ridge parameters fit on 2023 development cases after chronological alpha selection.
# Feature order: raw delta, relative pace * horizon, pit loss, final-lap warm-up gain,
# final-lap traffic loss, and current position / 10. The 2024 holdout never fits these values.
PIT_TRANSITION_RIDGE = {
    1: {
        "mean": (25.9055507246, 1.4942173913, 24.4112898551, 0.0, 0.0, 0.8405797101),
        "scale": (1.8127654791, 0.8982258983, 1.2715504128, 1.0, 1.0, 0.4515091732),
        "weights": (
            23.4864347826,
            0.2784745027,
            0.3740970527,
            0.1327099708,
            0.0,
            0.0,
            0.4858682604,
        ),
    },
    3: {
        "mean": (27.9631964286, 4.351125, 24.2881160714, 0.3863928571, 0.0791071429, 0.8857142857),
        "scale": (
            3.2907334637,
            2.3566343894,
            1.356572845,
            0.3449533964,
            0.2706053847,
            0.4319509609,
        ),
        "weights": (
            23.4694464286,
            0.4485590486,
            0.634573638,
            -0.018134022,
            0.1846924258,
            -0.228193685,
            0.7747917288,
        ),
    },
    5: {
        "mean": (
            29.8924693878,
            6.9957142857,
            24.1655918367,
            0.353122449,
            0.0805714286,
            0.8693877551,
        ),
        "scale": (
            4.8489820396,
            3.8285511727,
            1.3849013251,
            0.2940813876,
            0.3188437657,
            0.4243622257,
        ),
        "weights": (
            23.2291836735,
            0.4190127621,
            0.5887229488,
            -0.0747141935,
            0.016171725,
            0.2411281467,
            1.0131021937,
        ),
    },
}


def _round(value):
    return round(value, 3) if value is not None else None


def _pace(context, driver_id):
    cache = getattr(context, "_short_horizon_pace_cache", {})
    if driver_id in cache:
        return cache[driver_id]
    recent = get_recent_pace(context, driver_id)
    if recent.seconds is not None:
        result = recent.seconds, recent.lap_numbers, "recent_clean_pace"
    else:
        stint = get_stint_pace(context, driver_id)
        if stint.seconds is not None:
            result = stint.seconds, stint.lap_numbers, "current_stint_clean_pace"
        else:
            clean = context.clean.get(driver_id, [])[-3:]
            result = (
                (
                    median(row.lap_time_seconds for row in clean),
                    [row.number for row in clean],
                    "recent_relative_field_pace",
                )
                if clean
                else (None, [], "insufficient")
            )
    cache[driver_id] = result
    context._short_horizon_pace_cache = cache
    return result


def _relative_pace(context, driver_id):
    cache = getattr(context, "_short_horizon_relative_pace_cache", {})
    if driver_id in cache:
        return cache[driver_id]
    driver = context.driver(driver_id)
    leader = next((item for item in context.state.drivers if item.position == 1), None)
    if leader is None:
        return None, [], "insufficient"
    own, own_laps, own_source = _pace(context, driver_id)
    reference, reference_laps, reference_source = _pace(context, leader.driver.id)
    laps = sorted(set(own_laps + reference_laps))
    if own is None or reference is None:
        result = None, laps, "insufficient"
        cache[driver_id] = result
        context._short_horizon_relative_pace_cache = cache
        return result
    if driver.position == 1:
        result = 0.0, laps, own_source
    else:
        result = own - reference, laps, f"{own_source}_vs_{reference_source}"
    cache[driver_id] = result
    context._short_horizon_relative_pace_cache = cache
    return result


def _usable_gap(context, driver):
    cache = getattr(context, "_short_horizon_gap_cache", {})
    if driver.driver.id in cache:
        return cache[driver.driver.id]
    if driver.gap_to_leader is not None:
        result = driver.gap_to_leader, "published_leader_gap"
        cache[driver.driver.id] = result
        context._short_horizon_gap_cache = cache
        return result
    if driver.lapped or driver.position is None or driver.position <= 1:
        return None, "insufficient"
    ordered = {
        item.position: item
        for item in context.state.drivers
        if item.position is not None and item.status == "active" and not item.lapped
    }
    chain = [ordered.get(position) for position in range(2, driver.position + 1)]
    if chain and all(item is not None and item.gap_to_ahead is not None for item in chain):
        result = sum(item.gap_to_ahead for item in chain), "reconstructed_interval_chain"
        cache[driver.driver.id] = result
        context._short_horizon_gap_cache = cache
        return result
    return None, "insufficient"


def build_short_horizon_state(context, driver_id):
    driver = context.driver(driver_id)
    traffic = analyze_traffic(context, driver_id)
    relative_pace, pace_laps, pace_source = _relative_pace(context, driver_id)
    usable_gap, gap_source = _usable_gap(context, driver)
    pit_state = (
        "ON_TRACK"
        if driver.status == "active"
        else "IN_PIT"
        if driver.status == "in_pit"
        else "TERMINAL"
        if driver.status in {"retired", "stopped", "dns", "dsq"}
        else "UNKNOWN"
    )
    missing = [
        name
        for name, value in (
            ("position", driver.position),
            ("gap_to_leader", usable_gap),
            ("relative_pace", relative_pace),
            ("compound", driver.compound),
        )
        if value is None
    ]
    return ShortHorizonState(
        driver=driver.driver,
        current_position=driver.position,
        field_size=len(context.state.drivers),
        gap_to_leader=usable_gap,
        gap_kind=(
            "LAP_DEFICIT" if driver.lapped else "TIME" if usable_gap is not None else "UNKNOWN"
        ),
        laps_behind=driver.laps_behind,
        compound=driver.compound,
        tyre_age=driver.tyre_age,
        relative_pace_seconds_per_lap=_round(relative_pace),
        relative_pace_laps=pace_laps,
        pit_state=pit_state,
        traffic=traffic.status,
        laps_completed=driver.laps_completed,
        race_progress_fraction=_round(
            driver.laps_completed / context.state.total_scheduled_laps
            if driver.laps_completed is not None and context.state.total_scheduled_laps
            else None
        ),
        race_elapsed_seconds=_round(context.state.elapsed_race_seconds),
        pit_stops_completed=driver.pit_stops_completed,
        active=driver.status == "active",
        data_quality={
            "missing_inputs": missing,
            "driver": driver.quality.model_dump(),
            "traffic_confidence": traffic.confidence,
            "pace_reference": pace_source,
            "gap_source": gap_source,
            "fallback_hierarchy": [
                "recent clean pace",
                "current-stint clean pace",
                "recent relative field pace",
                "insufficient",
            ],
        },
    )


def _action_spec(action):
    if action.startswith("PIT_NOW_"):
        return "PIT_NOW", action.removeprefix("PIT_NOW_"), 0
    if action.startswith("EXTEND_"):
        try:
            laps = int(action.removeprefix("EXTEND_"))
        except ValueError as exc:
            raise NotFound(f"Unknown short-horizon action {action}") from exc
        if not 1 <= laps <= 5:
            raise NotFound(f"Unknown short-horizon action {action}")
        return "EXTEND", None, laps
    raise NotFound(f"Unknown short-horizon action {action}")


def _circuit_profile(context):
    supplied = getattr(context, "_circuit_profile", None)
    if supplied:
        return supplied
    historical = HISTORICAL_CIRCUIT_PROFILES.get(context.race.event.circuit.id)
    if historical and historical["source_year"] < context.race.event.year:
        return {
            "circuit_id": context.race.event.circuit.id,
            "source": "frozen_chronological_development_history",
            **historical,
        }
    histories = defaultdict(list)
    for sample in sorted(context.race.timing, key=lambda item: item.at):
        if sample.at <= context.cutoff and sample.position is not None:
            histories[sample.driver_id].append(sample.position)
    changes = sum(
        left != right
        for values in histories.values()
        for left, right in zip(values, values[1:], strict=False)
    )
    opportunities = sum(
        max(
            (
                sample.laps_completed or 0
                for sample in context.race.timing
                if sample.driver_id == driver_id and sample.at <= context.cutoff
            ),
            default=0,
        )
        for driver_id in histories
    )
    frequency = changes / opportunities if opportunities else None
    return {
        "circuit_id": context.race.event.circuit.id,
        "source": "current_session_prefix",
        "prior_races": 0,
        "position_change_frequency": frequency,
        "overtake_threshold_seconds": (
            max(0.5, min(1.5, 1.5 - 4 * frequency)) if frequency is not None else None
        ),
    }


def _project_field(context, driver_id, driver_gap, horizon):
    driver = context.driver(driver_id)
    projected, unknown, dynamic = [], 0, []
    target_cycle = max(item.pit_stops_completed for item in context.state.drivers)
    for item in context.state.drivers:
        if item.driver.id == driver_id or item.status != "active" or item.lapped:
            continue
        gap, source = _usable_gap(context, item)
        if gap is None:
            unknown += 1
            continue
        close = (
            abs((item.position or 99) - (driver.position or 99)) <= 5 or abs(gap - driver_gap) <= 15
        )
        pace, _, pace_source = _relative_pace(context, item.driver.id)
        moved = gap + horizon * pace if close and pace is not None else gap
        projected.append((item, moved))
        if close:
            traffic = analyze_traffic(context, item.driver.id)
            dynamic.append(
                {
                    "driver_id": item.driver.id,
                    "initial_gap": _round(gap),
                    "projected_gap": _round(moved),
                    "relative_pace": _round(pace),
                    "pace_uncertainty_seconds_per_lap": PACE_MAE_SECONDS,
                    "gap_source": source,
                    "pace_source": pace_source,
                    "compound": item.compound,
                    "tyre_age": item.tyre_age,
                    "traffic_state": traffic.status,
                    "pit_stops_completed": item.pit_stops_completed,
                    "expected_remaining_stop_obligation": max(
                        0, target_cycle - item.pit_stops_completed
                    ),
                    "laps_behind": item.laps_behind,
                    "gap_kind": "TIME",
                }
            )
    return projected, unknown, dynamic


def _position_projection(
    context, driver_id, initial_gap, projected_gap, uncertainty, pit_occurred, horizon, pit_loss
):
    driver = context.driver(driver_id)
    if driver.lapped or initial_gap is None or projected_gap is None:
        return None, None, None, len(context.state.drivers), None, None, []
    projected, unknown, dynamic = _project_field(context, driver_id, initial_gap, horizon)
    known = [gap for _, gap in projected]
    profile = _circuit_profile(context)
    threshold = profile.get("overtake_threshold_seconds")
    uncertain_crossings = []
    central = 1
    for item, gap in projected:
        initial_other_gap, _ = _usable_gap(context, item)
        initial_ahead = initial_other_gap < initial_gap
        final_ahead = gap < projected_gap
        separation = abs(gap - projected_gap)
        if initial_ahead != final_ahead and (threshold is None or separation < threshold):
            final_ahead = initial_ahead
            uncertain_crossings.append(item.driver.id)
        central += int(final_ahead)
    lower_gap, upper_gap = projected_gap - uncertainty, projected_gap + uncertainty
    best = 1 + sum(gap < lower_gap for gap in known)
    worst = 1 + sum(gap < upper_gap for gap in known) + unknown
    if pit_occurred:
        widening = ceil(REJOIN_MAE_POSITIONS)
        best = max(1, best - widening)
        worst = min(len(context.state.drivers), worst + widening)
    expected = central if not unknown else None
    change = driver.position - central if driver.position is not None and not unknown else None
    driver_stops = driver.pit_stops_completed + int(pit_occurred)
    target_cycle = max([driver_stops] + [item.pit_stops_completed for item, _ in projected])
    cycle_loss = pit_loss.total_seconds or 0.0
    driver_net_gap = projected_gap + max(0, target_cycle - driver_stops) * cycle_loss
    net_gaps = [
        gap + max(0, target_cycle - item.pit_stops_completed) * cycle_loss
        for item, gap in projected
    ]
    net_position = 1 + sum(gap < driver_net_gap for gap in net_gaps)
    net_range = (
        max(1, net_position - ceil(REJOIN_MAE_POSITIONS)),
        min(len(context.state.drivers), net_position + unknown + ceil(REJOIN_MAE_POSITIONS)),
    )
    for row in dynamic:
        row["crossing_treated_as_uncertain"] = row["driver_id"] in uncertain_crossings
        row["pit_cycle_target_stops"] = target_cycle
    return expected, (best, worst), change, unknown, net_position, net_range, dynamic


def _traffic_penalty(context, driver_id, traffic, pace, rejoin=None, fresh_pace=None):
    if rejoin is None:
        return blockage_penalty(
            context,
            traffic.ahead_id,
            traffic.gap_ahead,
            pace,
            traffic.status != "UNKNOWN",
        )
    return blockage_penalty(
        context,
        rejoin.ahead_id,
        rejoin.gap_ahead,
        fresh_pace,
        rejoin.projected_position is not None,
    )


def _calibration(calibration, kind, horizon):
    value = calibration.get(kind, {}).get(horizon)
    if value is not None:
        return value
    # Older callers may supply the Phase 5 two-horizon calibration.
    return calibration[kind][3]


def _warm_up_gain(fresh, post_index):
    curve = fresh.components.get("warm_up_curve_gain_seconds", {})
    key = (
        "post_stop_lap_1"
        if post_index == 1
        else "post_stop_lap_2"
        if post_index == 2
        else "post_stop_lap_3_plus"
    )
    value = curve.get(key)
    return (value if value is not None else 0.0) * FRESH_RELIABILITY


def _ridge_pit_transition_delta(raw_delta, relative_pace, pit_loss, trace, position, horizon):
    parameters = PIT_TRANSITION_RIDGE[horizon]
    final_lap = trace[-1]
    values = (
        raw_delta,
        relative_pace * horizon,
        pit_loss,
        final_lap["fresh_gain_seconds"] or 0.0,
        final_lap["traffic_loss_seconds"] or 0.0,
        position / 10,
    )
    standardized = [
        (value - mean) / scale
        for value, mean, scale in zip(values, parameters["mean"], parameters["scale"], strict=True)
    ]
    return parameters["weights"][0] + sum(
        weight * value
        for weight, value in zip(parameters["weights"][1:], standardized, strict=True)
    )


def _dynamic_path(
    context,
    driver_id,
    initial_gap,
    relative_pace,
    absolute_pace,
    horizon,
    extension,
    pit_loss,
    fresh,
    current_penalty,
):
    driver = context.driver(driver_id)
    cars = []
    unknown = 0
    target_cycle = max(item.pit_stops_completed for item in context.state.drivers)
    for item in context.state.drivers:
        if item.driver.id == driver_id or item.status != "active" or item.lapped:
            continue
        gap, gap_source = _usable_gap(context, item)
        if gap is None:
            unknown += 1
            continue
        local = (
            abs((item.position or 99) - (driver.position or 99)) <= 5
            or abs(gap - initial_gap) <= 15
        )
        pace, _, pace_source = _relative_pace(context, item.driver.id)
        traffic = analyze_traffic(context, item.driver.id) if local else None
        cars.append(
            {
                "state": item,
                "gap": gap,
                "initial_gap": gap,
                "pace": pace if local else 0.0,
                "local": local,
                "gap_source": gap_source,
                "pace_source": pace_source,
                "pace_uncertainty_seconds_per_lap": PACE_MAE_SECONDS if local else None,
                "compound": item.compound,
                "tyre_age": item.tyre_age,
                "traffic_state": traffic.status if traffic else "OUTSIDE_LOCAL_MODEL",
                "pit_stops_completed": item.pit_stops_completed,
                "expected_remaining_stop_obligation": max(
                    0, target_cycle - item.pit_stops_completed
                ),
            }
        )
    delta = 0.0
    trace = []
    post_index = 0
    for lap_index in range(1, horizon + 1):
        for car in cars:
            car["gap"] += car["pace"]
        pit_this_lap = extension + 1 == lap_index
        after_pit = extension < lap_index
        if pit_this_lap:
            lap_delta = relative_pace + pit_loss.total_seconds
            traffic_cost = 0.0
            gain = 0.0
        else:
            if after_pit:
                post_index += 1
                gain = _warm_up_gain(fresh, post_index)
            else:
                gain = 0.0
            tentative_gap = initial_gap + delta + relative_pace - gain
            ahead = [car for car in cars if car["gap"] <= tentative_gap]
            nearest = max(ahead, key=lambda car: car["gap"]) if ahead else None
            gap_ahead = tentative_gap - nearest["gap"] if nearest else None
            own_pace = absolute_pace - gain if absolute_pace is not None else None
            dynamic_penalty = blockage_penalty(
                context,
                nearest["state"].driver.id if nearest else None,
                gap_ahead,
                own_pace,
                unknown == 0,
            )
            traffic_cost = (
                dynamic_penalty
                if dynamic_penalty is not None
                else (current_penalty or 0.0)
                if not after_pit
                else 0.0
            )
            lap_delta = relative_pace - gain + traffic_cost
        delta += lap_delta
        trace.append(
            {
                "lap": lap_index,
                "pit_transition": pit_this_lap,
                "fresh_gain_seconds": _round(gain),
                "traffic_loss_seconds": _round(traffic_cost),
                "driver_gap": _round(initial_gap + delta),
                "nearby_gaps": {
                    car["state"].driver.id: _round(car["gap"]) for car in cars if car["local"]
                },
                "nearby_states": {
                    car["state"].driver.id: {
                        "gap_seconds": _round(car["gap"]),
                        "relative_pace_seconds_per_lap": _round(car["pace"]),
                        "pace_uncertainty_seconds_per_lap": car["pace_uncertainty_seconds_per_lap"],
                        "compound": car["compound"],
                        "tyre_age": car["tyre_age"],
                        "traffic_state": car["traffic_state"],
                        "pit_stops_completed": car["pit_stops_completed"],
                        "expected_remaining_stop_obligation": car[
                            "expected_remaining_stop_obligation"
                        ],
                    }
                    for car in cars
                    if car["local"]
                },
            }
        )
    return delta, trace, unknown


def simulate_action(context, driver_id, action, calibration=None):
    using_default_calibration = calibration is None
    calibration = calibration or DEFAULT_CALIBRATION
    states = getattr(context, "_simulation_states", {})
    if driver_id not in states:
        states[driver_id] = build_short_horizon_state(context, driver_id)
        context._simulation_states = states
    state = states[driver_id]
    kind, compound, extension = _action_spec(action)
    driver = context.driver(driver_id)
    if not hasattr(context, "_simulation_pit_loss"):
        context._simulation_pit_loss = estimate_pit_loss(context)
    pit_loss = context._simulation_pit_loss
    circuit_profile = _circuit_profile(context)
    if pit_loss.total_seconds is None and circuit_profile.get("typical_pit_loss_seconds"):
        pit_loss = pit_loss.model_copy(deep=True)
        pit_loss.total_seconds = circuit_profile["typical_pit_loss_seconds"]
        pit_loss.confidence = "LOW"
        pit_loss.method = "Chronological prior-race circuit pit-loss median"
        pit_loss.components["circuit_prior"] = circuit_profile
        pit_loss.components["residual_mad_seconds"] = circuit_profile.get("pit_loss_mad_seconds")
        pit_loss.warnings.append(
            "No current-session stop evidence; a lower-confidence prior circuit estimate is used."
        )
    cache = getattr(context, "_simulation_driver_cache", {})
    if driver_id not in cache:
        rejoin = predict_pit_rejoin(driver_id, context.state, pit_loss)
        traffic = analyze_traffic(context, driver_id, rejoin)
        relative_pace = state.relative_pace_seconds_per_lap
        absolute_pace = _pace(context, driver_id)[0]
        cache[driver_id] = {
            "rejoin": rejoin,
            "traffic": traffic,
            "relative_pace": relative_pace,
            "absolute_pace": absolute_pace,
            "current_penalty": _traffic_penalty(context, driver_id, traffic, absolute_pace),
            "fresh": {},
        }
        context._simulation_driver_cache = cache
    evidence = cache[driver_id]
    rejoin = evidence["rejoin"]
    traffic = evidence["traffic"]
    relative_pace = evidence["relative_pace"]
    absolute_pace = evidence["absolute_pace"]
    if compound not in evidence["fresh"]:
        evidence["fresh"][compound] = estimate_fresh_tyre_delta(
            context, driver_id, pit_loss, compound
        )
    fresh = evidence["fresh"][compound]
    raw_fresh = fresh.fresh_tyre_delta
    shrunk_fresh = raw_fresh * FRESH_RELIABILITY if raw_fresh is not None else 0.0
    current_penalty = evidence["current_penalty"]
    fresh_pace = absolute_pace - shrunk_fresh if absolute_pace is not None else None
    rejoin_penalty = _traffic_penalty(
        context, driver_id, traffic, relative_pace, rejoin, fresh_pace
    )
    outcomes = []
    for horizon in HORIZONS:
        pit_occurred = extension < horizon
        pre_laps = min(extension, horizon)
        post_laps = max(0, horizon - pre_laps - int(pit_occurred))
        missing = []
        if not state.active:
            missing.append("active on-track state")
        if relative_pace is None:
            missing.append("recent relative pace")
        if state.gap_to_leader is None or driver.lapped:
            missing.append("same-lap gap to leader")
        if pit_occurred and pit_loss.total_seconds is None:
            missing.append("causal pit-loss estimate")
        outcome = HorizonOutcome(horizon_laps=horizon)
        outcome.components = {
            "model": "independent short-horizon transition",
            "phase4_score_used": False,
            "phase4_recommendation_used": False,
            "relative_pace_seconds_per_lap": relative_pace,
            "zero_slope_tyre_assumption": True,
            "pre_pit_laps": pre_laps,
            "post_pit_laps": post_laps,
            "pit_occurs_within_horizon": pit_occurred,
            "pit_loss_components": {
                "sample_count": pit_loss.sample_count,
                "confidence": pit_loss.confidence,
                "entry_loss_seconds": pit_loss.entry_seconds,
                "pit_lane_transit_seconds": pit_loss.transit_seconds,
                "stationary_seconds": pit_loss.stationary_seconds,
                "exit_warm_up_seconds": pit_loss.exit_warm_up_seconds,
                "combined_observed_stop_lap_residual_seconds": (
                    pit_loss.total_seconds if pit_occurred else 0.0
                ),
                "pit_lane_elapsed_seconds": pit_loss.components.get(
                    "pit_lane_elapsed_median_seconds"
                ),
                "component_availability": pit_loss.components.get("component_availability"),
                "residual_mad_seconds": pit_loss.components.get("residual_mad_seconds"),
            },
            "current_blockage_seconds_per_lap": current_penalty,
            "rejoin_blockage_seconds_per_lap": rejoin_penalty,
            "raw_fresh_delta_seconds_per_lap": raw_fresh,
            "fresh_tyre_sample_count": fresh.sample_count,
            "fresh_tyre_sample_mad_seconds": fresh.components.get("sample_mad_seconds"),
            "fresh_reliability_shrinkage": FRESH_RELIABILITY,
            "shrunk_fresh_delta_seconds_per_lap": _round(shrunk_fresh),
            "warm_up_curve": fresh.components.get("warm_up_curve_gain_seconds"),
            "calibration": _calibration(calibration, kind, horizon),
            "circuit_profile": _circuit_profile(context),
            "predicted_rejoin": {
                "position": rejoin.projected_position,
                "position_range": rejoin.position_range,
                "position_range_width": (
                    rejoin.position_range[1] - rejoin.position_range[0]
                    if rejoin.position_range
                    else None
                ),
                "gap_ahead": rejoin.gap_ahead,
                "gap_behind": rejoin.gap_behind,
                "traffic": rejoin.traffic,
            }
            if pit_occurred
            else None,
            "missing_inputs": missing,
        }
        if missing:
            if (
                state.gap_kind == "LAP_DEFICIT"
                and state.current_position is not None
                and set(missing) <= {"same-lap gap to leader"}
            ):
                outcome.position_range = (
                    max(1, state.current_position - ceil(REJOIN_MAE_POSITIONS)),
                    state.field_size,
                )
                outcome.net_race_position_range = outcome.position_range
                outcome.applicability = "OUT_OF_DOMAIN"
                outcome.components["lap_deficit_projection"] = {
                    "laps_behind": state.laps_behind,
                    "laps_completed": state.laps_completed,
                    "race_progress_fraction": state.race_progress_fraction,
                    "race_elapsed_seconds": state.race_elapsed_seconds,
                    "track_order_position": state.current_position,
                    "seconds_gap_fabricated": False,
                }
                outcome.warnings.append(
                    "Only a broad track-order range is available for this lapped driver; "
                    "no seconds gap or point position is inferred."
                )
            outcome.warnings.append("Cannot simulate: missing " + ", ".join(missing) + ".")
            outcomes.append(outcome)
            continue
        raw_delta, trace, dynamic_unknown = _dynamic_path(
            context,
            driver_id,
            state.gap_to_leader,
            relative_pace,
            absolute_pace,
            horizon,
            extension,
            pit_loss,
            fresh,
            current_penalty,
        )
        if not pit_occurred:
            # Preserve the validated Phase 5 stay-out timing model. Dynamic motion changes
            # traffic and position geometry, but does not rewrite its factual time baseline.
            raw_delta = horizon * (relative_pace + (current_penalty or 0.0))
        calibration_row = _calibration(calibration, kind, horizon)
        bias = calibration_row["bias_seconds"]
        expected_delta = raw_delta + bias
        ridge_applied = (
            using_default_calibration and kind == "PIT_NOW" and driver.position is not None
        )
        if ridge_applied:
            expected_delta = _ridge_pit_transition_delta(
                raw_delta,
                relative_pace,
                pit_loss.total_seconds,
                trace,
                driver.position,
                horizon,
            )
        component_uncertainty = sqrt(
            (PACE_MAE_SECONDS * sqrt(pre_laps)) ** 2
            + (FRESH_MAE_SECONDS * sqrt(post_laps)) ** 2
            + (pit_loss.components.get("residual_mad_seconds") or 0.0) ** 2 * int(pit_occurred)
            + (PACE_MAE_SECONDS * sum(x is None for x in (current_penalty, rejoin_penalty))) ** 2
        )
        uncertainty = max(calibration_row["mae_seconds"], component_uncertainty)
        projected_gap = state.gap_to_leader + expected_delta
        (
            expected_position,
            position_range,
            position_change,
            unknown,
            net_position,
            net_range,
            dynamic,
        ) = _position_projection(
            context,
            driver_id,
            state.gap_to_leader,
            projected_gap,
            uncertainty,
            pit_occurred,
            horizon,
            pit_loss,
        )
        outcome.expected_delta_time_seconds = _round(expected_delta)
        outcome.uncertainty_seconds = _round(uncertainty)
        outcome.expected_position = expected_position
        outcome.position_range = position_range
        outcome.expected_position_change = _round(position_change)
        outcome.physical_track_position = expected_position
        outcome.net_race_position_estimate = net_position
        outcome.net_race_position_range = net_range
        outcome.confidence = (
            "LOW"
            if pit_occurred or unknown or current_penalty is None or rejoin_penalty is None
            else "MEDIUM"
        )
        outcome.components.update(
            {
                "uncalibrated_delta_time_seconds": _round(raw_delta),
                "pit_transition_ridge_applied": ridge_applied,
                "pit_transition_ridge_training": "2023 chronological development only",
                "projected_gap_to_leader_seconds": _round(projected_gap),
                "component_uncertainty_seconds": _round(component_uncertainty),
                "unknown_same_lap_cars": unknown,
                "dynamic_unknown_cars": dynamic_unknown,
                "nearby_car_projection": dynamic,
                "lap_by_lap_projection": trace,
                "rejoin_position_range_at_stop": rejoin.position_range if pit_occurred else None,
            }
        )
        outcome.warnings.extend(
            [
                "Tyre pace is zero-slope; rejected diagnostic degradation slopes are unused.",
                "Nearby cars move each lap from causal recent pace; distant cars remain "
                "outside the local interaction model.",
            ]
        )
        if pit_occurred:
            outcome.warnings.append(
                "Entry, lane transit and stationary race-time loss remain inseparable in "
                "the source archive and are carried in transition uncertainty."
            )
        if using_default_calibration and kind == "PIT_NOW":
            apply_pit_reliability(context, state, outcome)
        outcomes.append(outcome)
    return CounterfactualActionOutcome(
        action=action,
        kind=kind,
        compound=compound,
        extension_laps=extension if kind == "EXTEND" else None,
        outcomes=outcomes,
        confidence=(
            "INSUFFICIENT"
            if all(item.confidence == "INSUFFICIENT" for item in outcomes)
            else "LOW"
            if kind == "PIT_NOW" or any(item.confidence == "LOW" for item in outcomes)
            else "MEDIUM"
        ),
        warnings=[
            "Outcome estimates are independent of Phase 4 scores and recommendations.",
            "No probabilities, weather, safety car or whole-race path is simulated.",
        ],
    )


def legal_counterfactual_actions(context, driver_id):
    return [action.id for action in generate_actions(context, driver_id)]


def compare_counterfactual_actions(context, driver_id, calibration=None):
    state = build_short_horizon_state(context, driver_id)
    actions = [
        simulate_action(context, driver_id, action, calibration)
        for action in legal_counterfactual_actions(context, driver_id)
    ]
    usable = []
    for action in actions:
        horizon = next(item for item in action.outcomes if item.horizon_laps == 3)
        if horizon.expected_delta_time_seconds is not None:
            usable.append((action, horizon))
    pit = [item for item in usable if item[0].kind == "PIT_NOW"]
    extend = [item for item in usable if item[0].kind == "EXTEND"]
    decision = "INSUFFICIENT_DATA"
    preferred, margin, overlap = None, None, None
    if pit and extend:
        best_pit = min(pit, key=lambda item: item[1].expected_delta_time_seconds)
        best_extend = min(extend, key=lambda item: item[1].expected_delta_time_seconds)
        best, other = sorted(
            (best_pit, best_extend), key=lambda item: item[1].expected_delta_time_seconds
        )
        margin = other[1].expected_delta_time_seconds - best[1].expected_delta_time_seconds
        overlap = margin <= best[1].uncertainty_seconds + other[1].uncertainty_seconds
        if overlap:
            decision = "HOLD_NO_CLEAR_ADVANTAGE"
        else:
            preferred = best[0].action
            decision = best[0].kind
    return CounterfactualComparison(
        year=context.state.event.year,
        round=context.state.event.round,
        lap=context.state.current_lap,
        cutoff_seconds=context.cutoff,
        state=state,
        actions=actions,
        preferred_action=preferred,
        decision=decision,
        outcome_margin_seconds=_round(margin),
        uncertainty_overlap=overlap,
        confidence="LOW" if usable else "INSUFFICIENT",
        warnings=[
            "Substantial uncertainty overlap returns HOLD rather than forcing an action.",
            "This comparison does not modify the Phase 4 recommendation.",
        ],
    )


class SimulationService:
    def __init__(self, replay):
        self.replay = replay

    async def _run(self, year, round, lap, operation):
        race = await self.replay.load_race(year, round)
        return await asyncio.to_thread(lambda: operation(AnalysisContext(race, lap)))

    async def simulate(self, year, round, lap, driver_id, action):
        def operation(context):
            if action not in legal_counterfactual_actions(context, driver_id):
                raise NotFound(f"Action {action} is not legal at this snapshot")
            return simulate_action(context, driver_id, action)

        return await self._run(year, round, lap, operation)

    async def compare(self, year, round, lap, driver_id):
        return await self._run(
            year,
            round,
            lap,
            lambda context: compare_counterfactual_actions(context, driver_id),
        )
