"""Coarse event-level strategic stint value model with a five-lap tactical handoff."""

from __future__ import annotations

import json
from importlib.resources import files
from math import sqrt
from statistics import median

from f1_pitwall.domain.strategic import (
    StintCompoundPrior,
    StrategicActionValue,
    StrategicPitComparison,
)
from f1_pitwall.services.pace import normalized_lap_times
from f1_pitwall.services.pit_analysis import estimate_pit_loss, predict_pit_rejoin
from f1_pitwall.services.predictive_pace import estimate_field_relative_pace
from f1_pitwall.services.simulation import HISTORICAL_CIRCUIT_PROFILES
from f1_pitwall.services.strategy import generate_actions
from f1_pitwall.services.traffic import analyze_traffic
from f1_pitwall.services.transition_kernel import rollout_action

TACTICAL_HORIZON = 5
MAX_STRATEGIC_HORIZON = 25
MIN_STRATEGIC_HORIZON = 10
MIN_SAME_RACE_SAMPLES = 3
MIN_CIRCUIT_SAMPLES = 5
MIN_ERA_SAMPLES = 10
DRY_COMPOUNDS = {"SOFT", "MEDIUM", "HARD"}


def _quantile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return None
    index = (len(ordered) - 1) * fraction
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _prior_artifact():
    path = files("f1_pitwall").joinpath("models/strategic_stint_priors.json")
    return json.loads(path.read_text(encoding="utf-8"))


def _same_race_segments(context):
    segments = []
    participant = {row.driver.id: row for row in context.race.participants}
    for driver_id in context.drivers:
        normalized = dict(normalized_lap_times(context, driver_id, current_stint=False))
        grouped = {}
        for lap in context.clean.get(driver_id, []):
            previous = context.rows[driver_id].get(lap.number - 1)
            if previous is None:
                continue
            start = context.stint_at(driver_id, previous.completed_at)
            end = context.stint_at(driver_id, lap.completed_at)
            if (
                start is None
                or end is None
                or start.number != end.number
                or start.compound not in DRY_COMPOUNDS
                or lap.number not in normalized
            ):
                continue
            grouped.setdefault((start.number, start.compound), []).append(normalized[lap.number])
        records = [row for row in context.race.stints if row.driver_id == driver_id]
        maximum = max((row.number for row in records), default=0)
        for (number, compound), values in grouped.items():
            if len(values) < 3:
                continue
            pace = median(values)
            if abs(pace) > 5:
                continue
            ages = [
                row.tyre_age for row in records if row.number == number and row.tyre_age is not None
            ]
            constructor = (
                participant.get(driver_id).constructor if participant.get(driver_id) else None
            )
            segments.append(
                {
                    "race": f"{context.race.event.year}/current",
                    "race_date": context.race.event.race_date.isoformat(),
                    "circuit_id": context.race.event.circuit.id,
                    "driver_id": driver_id,
                    "constructor_id": constructor.id if constructor else None,
                    "compound": compound,
                    "relative_pace_seconds_per_lap": pace,
                    "competitive_stint_length_laps": max(ages) + 1
                    if ages and number < maximum
                    else None,
                }
            )
    return segments


def _eligible_historical_segments(context):
    event_date = context.race.event.race_date.isoformat()
    return [
        row
        for row in _prior_artifact()["segments"]
        if row["race_date"] < event_date and int(row["race_date"][:4]) >= 2022
    ]


def _source_pools(context):
    same_race = _same_race_segments(context)
    historical = _eligible_historical_segments(context)
    circuit = [row for row in historical if row["circuit_id"] == context.race.event.circuit.id]
    return (
        ("SAME_RACE_OBSERVED", same_race, MIN_SAME_RACE_SAMPLES),
        ("EARLIER_SAME_CIRCUIT", circuit, MIN_CIRCUIT_SAMPLES),
        ("REGULATION_ERA", historical, MIN_ERA_SAMPLES),
    )


def _select_evidence(context, compound, field, require_non_null=True):
    for source, pool, minimum in _source_pools(context):
        exact = [
            row
            for row in pool
            if row["compound"] == compound and (not require_non_null or row.get(field) is not None)
        ]
        if len(exact) >= minimum:
            return source, exact, pool
    return "INSUFFICIENT", [], []


def estimate_compound_prior(context, driver_id, compound):
    compound = compound.upper()
    current = estimate_field_relative_pace(context, driver_id)
    pace_source, pace_rows, scope = _select_evidence(
        context, compound, "relative_pace_seconds_per_lap"
    )
    length_source, length_rows, _ = _select_evidence(
        context, compound, "competitive_stint_length_laps"
    )
    paces = [row["relative_pace_seconds_per_lap"] for row in pace_rows]
    scope_paces = [
        row["relative_pace_seconds_per_lap"]
        for row in scope
        if row.get("relative_pace_seconds_per_lap") is not None
    ]
    lengths = [row["competitive_stint_length_laps"] for row in length_rows]
    compound_effect = median(paces) - median(scope_paces) if paces and scope_paces else None
    current_pace = current["expected_field_relative_pace_seconds"]
    expected = (
        current_pace + compound_effect
        if compound_effect is not None and current_pace is not None
        else median(paces)
        if paces
        else None
    )
    pace_iqr = (_quantile(paces, 0.25), _quantile(paces, 0.75)) if paces else None
    length_iqr = (_quantile(lengths, 0.25), _quantile(lengths, 0.75)) if lengths else None
    empirical_spread = max(0.3, (pace_iqr[1] - pace_iqr[0]) / 2) if pace_iqr else None
    uncertainty = (
        sqrt(empirical_spread**2 + current["uncertainty_seconds_per_lap"] ** 2)
        if empirical_spread is not None and current["uncertainty_seconds_per_lap"] is not None
        else empirical_spread
    )
    warnings = []
    if pace_source == "INSUFFICIENT":
        warnings.append("No chronological compound-pace prior met the minimum sample count.")
    if length_source == "INSUFFICIENT":
        warnings.append("No chronological completed-stint prior met the minimum sample count.")
    return StintCompoundPrior(
        compound=compound,
        expected_relative_pace_seconds_per_lap=round(expected, 3) if expected is not None else None,
        compound_effect_seconds_per_lap=round(compound_effect, 3)
        if compound_effect is not None
        else None,
        pace_iqr_seconds_per_lap=tuple(round(value, 3) for value in pace_iqr) if pace_iqr else None,
        median_competitive_stint_laps=round(median(lengths), 1) if lengths else None,
        competitive_stint_iqr_laps=tuple(round(value, 1) for value in length_iqr)
        if length_iqr
        else None,
        pace_sample_count=len(paces),
        length_sample_count=len(lengths),
        source=pace_source,
        uncertainty_seconds_per_lap=round(uncertainty, 3) if uncertainty is not None else None,
        components={
            "length_source": length_source,
            "current_driver_relative_pace_seconds_per_lap": current_pace,
            "current_driver_pace_source": current["source"],
            "historical_compound_median_relative_pace": median(paces) if paces else None,
            "historical_scope_median_relative_pace": median(scope_paces) if scope_paces else None,
            "fallback_hierarchy": [
                "same race observed evidence",
                "earlier same-circuit evidence",
                "broader regulation-era evidence",
                "insufficient",
            ],
            "historical_samples_strictly_before_event": True,
            "raw_degradation_slope_used": False,
        },
        warnings=warnings,
    )


def _tactical_summary(context, driver_id, action, trajectory_count, seed):
    rollout = rollout_action(context, driver_id, action, trajectory_count, seed)
    horizon = next(row for row in rollout.outcomes if row.horizon_laps == 5)
    return rollout, horizon


def _strategic_pit_loss(context):
    pit_loss = estimate_pit_loss(context)
    if pit_loss.total_seconds is not None:
        return pit_loss
    profile = HISTORICAL_CIRCUIT_PROFILES.get(context.race.event.circuit.id)
    if profile is None or profile["source_year"] >= context.race.event.year:
        return pit_loss
    fallback = pit_loss.model_copy(deep=True)
    fallback.total_seconds = profile["typical_pit_loss_seconds"]
    fallback.confidence = "LOW"
    fallback.method = "Chronological earlier-race circuit pit-loss median"
    fallback.components["residual_mad_seconds"] = profile["pit_loss_mad_seconds"]
    fallback.components["strategic_circuit_prior"] = profile
    fallback.warnings.append(
        "No same-race stop was observed by the cutoff; an earlier-circuit prior is used."
    )
    return fallback


def _cost_after_handoff(action, horizon, current_pace, pit_loss, pit_loss_mad, prior):
    tactical = action.tactical_5_lap_delta_seconds
    if tactical is None or prior.expected_relative_pace_seconds_per_lap is None:
        return None, None
    if horizon <= TACTICAL_HORIZON:
        return tactical, action.tactical_5_lap_interval_90
    delay = action.delay_laps
    remaining = horizon - TACTICAL_HORIZON
    current_laps = min(max(delay - TACTICAL_HORIZON, 0), remaining)
    stopped_after_handoff = delay >= TACTICAL_HORIZON and horizon > delay
    new_laps = (
        remaining
        if delay < TACTICAL_HORIZON
        else max(0, horizon - delay)
        if stopped_after_handoff
        else 0
    )
    expected = (
        tactical
        + current_laps * current_pace
        + new_laps * (prior.expected_relative_pace_seconds_per_lap or 0)
    )
    if stopped_after_handoff:
        expected += pit_loss
    competitive_length = prior.median_competitive_stint_laps
    new_stint_laps_total = max(0, horizon - delay - 1)
    additional_stops = (
        int((new_stint_laps_total - 1) // competitive_length)
        if competitive_length and new_stint_laps_total > competitive_length
        else 0
    )
    expected += additional_stops * pit_loss
    tactical_half_width = (
        (action.tactical_5_lap_interval_90[1] - action.tactical_5_lap_interval_90[0]) / 2
        if action.tactical_5_lap_interval_90
        else 0
    )
    pace_uncertainty = (prior.uncertainty_seconds_per_lap or 1.5) * new_laps
    current_uncertainty = action.components["current_pace_uncertainty"] * current_laps
    total_uncertainty = sqrt(
        tactical_half_width**2
        + pace_uncertainty**2
        + current_uncertainty**2
        + (pit_loss_mad * sqrt(additional_stops + int(stopped_after_handoff))) ** 2
    )
    return expected, (expected - total_uncertainty, expected + total_uncertainty)


def _crossing(pit_now, delayed, current_lap, optimistic=False, conservative=False):
    for offset in sorted(pit_now.components["all_horizon_values"]):
        left = pit_now.components["all_horizon_values"][offset]
        right = delayed.components["all_horizon_values"][offset]
        if left is None or right is None:
            continue
        if optimistic:
            left_range = pit_now.components["all_horizon_ranges"][offset]
            right_range = delayed.components["all_horizon_ranges"][offset]
            if left_range is None or right_range is None:
                continue
            left = left_range[0]
            right = right_range[1]
        elif conservative:
            left_range = pit_now.components["all_horizon_ranges"][offset]
            right_range = delayed.components["all_horizon_ranges"][offset]
            if left_range is None or right_range is None:
                continue
            left = left_range[1]
            right = right_range[0]
        if left <= right:
            return current_lap + offset
    return None


def compare_strategic_actions(context, driver_id, trajectory_count=100, seed=0):
    driver = context.driver(driver_id)
    total = context.state.total_scheduled_laps
    remaining = total - context.state.current_lap if total is not None else 0
    horizon = min(MAX_STRATEGIC_HORIZON, remaining)
    terminal_lap = context.state.current_lap + horizon
    result = StrategicPitComparison(
        current_lap=context.state.current_lap,
        terminal_lap=terminal_lap,
        strategic_horizon_laps=horizon,
        driver_id=driver_id,
        components={
            "terminal_definition": "one common observed leader-lap checkpoint",
            "tactical_handoff": "stochastic Phase 5E rollout through lap +5 only",
            "strategic_segment": "one coarse empirical stint segment after lap +5",
            "recursive_tactical_rollout_beyond_five_laps": False,
        },
    )
    if driver.status != "active" or horizon < MIN_STRATEGIC_HORIZON:
        result.warnings.append("At least ten remaining active-race laps are required.")
        return result
    compounds = sorted(
        {
            action.compound
            for action in generate_actions(context, driver_id)
            if action.kind == "PIT_NOW" and action.compound in DRY_COMPOUNDS
        }
    )
    if not compounds:
        result.warnings.append("No causal, legal dry compound candidate is available.")
        return result
    priors = [estimate_compound_prior(context, driver_id, compound) for compound in compounds]
    result.compound_priors = priors
    pit_loss = _strategic_pit_loss(context)
    if pit_loss.total_seconds is None:
        result.warnings.append("No causal pit-loss estimate is available.")
        return result
    current = estimate_field_relative_pace(context, driver_id)
    current_pace = current["expected_field_relative_pace_seconds"]
    current_uncertainty = current["uncertainty_seconds_per_lap"]
    current_prior = (
        estimate_compound_prior(context, driver_id, driver.compound)
        if driver.compound in DRY_COMPOUNDS
        else None
    )
    window_delay = (
        round((current_prior.median_competitive_stint_laps or 0) - (driver.tyre_age or 0))
        if current_prior
        else 0
    )
    delays = sorted(
        {
            delay
            for delay in (0, 2, 5, window_delay)
            if 0 <= delay <= horizon - 3 and (delay in {0, 2, 5} or delay >= 7)
        }
    )
    rejoin = predict_pit_rejoin(driver_id, context.state, pit_loss)
    traffic = analyze_traffic(context, driver_id, rejoin).status
    tactical_cache = {}
    actions = []
    for prior in priors:
        if prior.expected_relative_pace_seconds_per_lap is None:
            continue
        for delay in delays:
            tactical_action = (
                f"PIT_NOW_{prior.compound}"
                if delay == 0
                else f"EXTEND_{delay}"
                if delay < TACTICAL_HORIZON
                else "EXTEND_5"
            )
            if tactical_action not in tactical_cache:
                tactical_cache[tactical_action] = _tactical_summary(
                    context,
                    driver_id,
                    tactical_action,
                    trajectory_count,
                    seed + delay,
                )
            _, tactical = tactical_cache[tactical_action]
            action_name = (
                f"PIT_NOW_{prior.compound}"
                if delay == 0
                else f"EXTEND_TO_WINDOW_{delay}_THEN_PIT_{prior.compound}"
                if delay == window_delay and delay not in {2, 5}
                else f"EXTEND_{delay}_THEN_PIT_{prior.compound}"
            )
            action = StrategicActionValue(
                action=action_name,
                compound=prior.compound,
                delay_laps=delay,
                terminal_lap=terminal_lap,
                tactical_action=tactical_action,
                tactical_handoff_lap=context.state.current_lap + TACTICAL_HORIZON,
                tactical_5_lap_delta_seconds=tactical.median_relative_delta_seconds,
                tactical_5_lap_interval_90=tactical.interval_90,
                physical_position_range_at_handoff=tactical.position_range_90,
                future_stop_obligations=[
                    "Stop paid inside tactical segment"
                    if delay < TACTICAL_HORIZON
                    else f"Owed stop paid after {delay} extension laps"
                ],
                traffic_risk=traffic,
                uncertainty="MEDIUM"
                if prior.source in {"SAME_RACE_OBSERVED", "EARLIER_SAME_CIRCUIT"}
                else "LOW"
                if prior.source == "REGULATION_ERA"
                else "INSUFFICIENT",
                components={
                    "current_pace_seconds_per_lap": current_pace,
                    "current_pace_uncertainty": current_uncertainty,
                    "new_compound_pace_seconds_per_lap": (
                        prior.expected_relative_pace_seconds_per_lap
                    ),
                    "pit_loss_seconds": pit_loss.total_seconds,
                    "pit_loss_mad_seconds": pit_loss.components.get("residual_mad_seconds") or 0,
                    "compound_prior_source": prior.source,
                    "median_competitive_stint_laps": prior.median_competitive_stint_laps,
                    "same_terminal_horizon_laps": horizon,
                    "owed_stop_included_by_terminal": True,
                    "pit_cycle_position_used": False,
                },
                warnings=[
                    "Terminal physical/net position is unavailable beyond the validated "
                    "tactical handoff."
                ],
            )
            all_values = {}
            all_ranges = {}
            for offset in range(TACTICAL_HORIZON, horizon + 1):
                expected, interval = _cost_after_handoff(
                    action,
                    offset,
                    current_pace,
                    pit_loss.total_seconds,
                    pit_loss.components.get("residual_mad_seconds") or 0,
                    prior,
                )
                all_values[offset] = expected
                all_ranges[offset] = interval
            action.components["all_horizon_values"] = all_values
            action.components["all_horizon_ranges"] = all_ranges
            new_stint_laps_to_terminal = max(0, horizon - delay - 1)
            if (
                prior.median_competitive_stint_laps
                and new_stint_laps_to_terminal > prior.median_competitive_stint_laps
            ):
                additional = int(
                    (new_stint_laps_to_terminal - 1) // prior.median_competitive_stint_laps
                )
                action.future_stop_obligations.append(
                    f"{additional} additional stop(s) charged because the terminal exceeds "
                    "the median observed competitive stint length"
                )
                action.components["additional_stop_count"] = additional
            else:
                action.components["additional_stop_count"] = 0
            for checkpoint in (5, 10, 15, 20, horizon):
                if checkpoint <= horizon:
                    action.cumulative_value_seconds[checkpoint] = (
                        round(all_values[checkpoint], 3)
                        if all_values[checkpoint] is not None
                        else None
                    )
                    interval = all_ranges[checkpoint]
                    action.cumulative_ranges_90[checkpoint] = (
                        tuple(round(value, 3) for value in interval) if interval else None
                    )
            terminal = all_values[horizon]
            action.terminal_expected_seconds = round(terminal, 3) if terminal is not None else None
            actions.append(action)
    usable = [action for action in actions if action.terminal_expected_seconds is not None]
    if not usable:
        result.warnings.append("No action has enough tactical and stint-prior evidence.")
        result.actions = actions
        return result
    best = min(usable, key=lambda action: action.terminal_expected_seconds)
    for action in usable:
        action.time_loss_vs_best_seconds = round(
            action.terminal_expected_seconds - best.terminal_expected_seconds, 3
        )
        pit_now = next(
            (item for item in usable if item.compound == action.compound and item.delay_laps == 0),
            None,
        )
        delayed = (
            min(
                (
                    item
                    for item in usable
                    if item.compound == action.compound and item.delay_laps > 0
                ),
                key=lambda item: item.terminal_expected_seconds,
                default=None,
            )
            if action.delay_laps == 0
            else action
        )
        if pit_now and delayed:
            crossing = _crossing(pit_now, delayed, context.state.current_lap)
            optimistic = _crossing(pit_now, delayed, context.state.current_lap, optimistic=True)
            conservative = _crossing(pit_now, delayed, context.state.current_lap, conservative=True)
            action.break_even_lap = crossing
            action.break_even_lap_range = (
                (optimistic, conservative)
                if optimistic is not None and conservative is not None
                else None
            )
            action.break_even_time_seconds = (
                round(
                    pit_now.components["all_horizon_values"][crossing - context.state.current_lap]
                    - delayed.components["all_horizon_values"][
                        crossing - context.state.current_lap
                    ],
                    3,
                )
                if crossing is not None
                else None
            )
    result.actions = actions
    result.best_strategic_action = best.action
    result.components.update(
        {
            "pit_loss_seconds": pit_loss.total_seconds,
            "pit_loss_confidence": pit_loss.confidence,
            "current_relative_pace_seconds_per_lap": current_pace,
            "current_pace_source": current["source"],
            "candidate_delays": delays,
            "terminal_values_are_relative_time_costs": True,
        }
    )
    return result
