"""Chronological factual calibration and common-cohort counterfactual policy evaluation."""

import argparse
import json
from collections import Counter, defaultdict
from math import ceil
from pathlib import Path
from statistics import mean, median

from f1_pitwall.domain.replay import HistoricalRace
from f1_pitwall.services.analysis_context import AnalysisContext
from f1_pitwall.services.fresh_tyre import estimate_fresh_tyre_delta
from f1_pitwall.services.pace import get_recent_pace, normalized_lap_times
from f1_pitwall.services.pit_analysis import (
    estimate_pit_loss,
    observed_pit_samples,
    predict_pit_rejoin,
)
from f1_pitwall.services.simulation import (
    FRESH_RELIABILITY,
    REJOIN_MAE_POSITIONS,
    _traffic_penalty,
    build_short_horizon_state,
    legal_counterfactual_actions,
    simulate_action,
)
from f1_pitwall.services.strategy import analyze_strategy_all
from f1_pitwall.services.traffic import analyze_traffic

DEVELOPMENT_RACES = ((2023, 1), (2023, 6), (2023, 7), (2023, 14))
VALIDATION_RACES = ((2024, 1), (2024, 4), (2024, 8), (2024, 10), (2024, 16))
RAW_CALIBRATION = {
    kind: {horizon: {"bias_seconds": 0.0, "mae_seconds": 0.0} for horizon in (1, 3, 5)}
    for kind in ("EXTEND", "PIT_NOW")
}
PHASE5_CALIBRATION = {
    "EXTEND": {
        1: {"bias_seconds": 0.0, "mae_seconds": 1.25},
        3: {"bias_seconds": 0.0, "mae_seconds": 2.104},
        5: {"bias_seconds": 0.0, "mae_seconds": 2.945},
    },
    "PIT_NOW": {
        1: {"bias_seconds": -0.9, "mae_seconds": 3.0},
        3: {"bias_seconds": -1.812, "mae_seconds": 3.586},
        5: {"bias_seconds": -3.583, "mae_seconds": 4.888},
    },
}


def load_races(selections):
    return [
        HistoricalRace.model_validate_json(
            Path(f".cache/analysis-history-{year}-{round_number}.json").read_text(encoding="utf-8")
        )
        for year, round_number in selections
    ]


def circuit_profile(races, circuit_id, before_date):
    eligible = [
        race
        for race in races
        if race.event.circuit.id == circuit_id and race.event.race_date < before_date
    ]
    losses = []
    changes = opportunities = 0
    for race in eligible:
        laps = sorted({row.number for row in race.laps})
        if not laps:
            continue
        samples, _ = observed_pit_samples(AnalysisContext(race, max(laps)))
        losses.extend(sample["loss_seconds"] for sample in samples)
        histories = defaultdict(list)
        for sample in sorted(race.timing, key=lambda item: item.at):
            if sample.position is not None:
                histories[sample.driver_id].append(sample.position)
        changes += sum(
            left != right
            for values in histories.values()
            for left, right in zip(values, values[1:], strict=False)
        )
        opportunities += sum(
            max(
                (
                    sample.laps_completed or 0
                    for sample in race.timing
                    if sample.driver_id == driver_id
                ),
                default=0,
            )
            for driver_id in histories
        )
    loss = median(losses) if losses else None
    frequency = changes / opportunities if opportunities else None
    return {
        "circuit_id": circuit_id,
        "source": "chronological_prior_historical_races",
        "prior_races": len(eligible),
        "typical_pit_loss_seconds": loss,
        "pit_loss_mad_seconds": median(abs(value - loss) for value in losses) if losses else None,
        "pit_loss_samples": len(losses),
        "position_change_frequency": frequency,
        "overtake_threshold_seconds": (
            max(0.5, min(1.5, 1.5 - 4 * frequency)) if frequency is not None else None
        ),
    }


def attach_chronological_profiles(cases, prior_races):
    profiles = {}
    for selected in cases.values():
        for case in selected:
            event = case["full_race"].event if "full_race" in case else case["context"].race.event
            key = (event.circuit.id, event.race_date)
            if key not in profiles:
                profiles[key] = circuit_profile(prior_races, event.circuit.id, event.race_date)
            profile = profiles[key]
            case["context"]._circuit_profile = profile
            for attribute in (
                "_simulation_pit_loss",
                "_simulation_driver_cache",
                "_simulation_states",
            ):
                if hasattr(case["context"], attribute):
                    delattr(case["context"], attribute)
    return list(profiles.values())


def lap_cutoffs(race):
    laps = sorted({row.number for row in race.laps})
    return laps, {
        lap: min(row.available_at for row in race.laps if row.number == lap) for lap in laps
    }


def cached_context(cache, race, lap):
    cache.setdefault(lap, AnalysisContext(race, lap))
    return cache[lap]


def future_values(context, driver_id):
    driver = context.driver(driver_id)
    return driver.gap_to_leader, driver.position


def stable_gap_reference(race, context, cuts, lap, horizon, target_lap=None):
    leader = next((driver for driver in context.state.drivers if driver.position == 1), None)
    if leader is None:
        return False
    leader_stops = any(
        stop.driver_id == leader.driver.id
        and context.cutoff < stop.entered_at <= cuts[target_lap or lap + horizon]
        for stop in race.pit_stops
    )
    controls = [
        sample
        for sample in race.control
        if context.cutoff < sample.at <= cuts[target_lap or lap + horizon]
        and sample.track_status is not None
    ]
    return not leader_stops and all(sample.track_status == "1" for sample in controls)


def labelled_future(race, current, future, cuts, lap, horizon, driver_id, target_lap=None):
    gap, position = future_values(future, driver_id)
    return (
        gap if stable_gap_reference(race, current, cuts, lap, horizon, target_lap) else None,
        position,
    )


def actual_compound_after_stop(race, context, stop):
    current = context.driver(stop.driver_id)
    candidates = [
        stint
        for stint in race.stints
        if stint.driver_id == stop.driver_id
        and stint.observed_at > stop.entered_at
        and stint.number > (current.stint_number or 0)
        and stint.compound
    ]
    value = min(candidates, key=lambda stint: stint.observed_at).compound if candidates else None
    return value if value and value.upper() not in {"UNKNOWN", "TEST_UNKNOWN"} else None


def collect_factual_cases(races):
    stay_out, pit_now = [], []
    for race in races:
        laps, cuts = lap_cutoffs(race)
        contexts = {}
        decision_laps = [
            value for value in laps if value >= 10 and value % 10 == 0 and value + 5 in cuts
        ]
        for lap in decision_laps:
            current = cached_context(contexts, race, lap)
            future = {
                horizon: cached_context(contexts, race, lap + horizon) for horizon in (1, 3, 5)
            }
            for driver in current.state.drivers:
                if driver.status != "active":
                    continue
                stopped = any(
                    stop.driver_id == driver.driver.id
                    and current.cutoff < stop.entered_at <= cuts[lap + 5]
                    for stop in race.pit_stops
                )
                if stopped:
                    continue
                stay_out.append(
                    {
                        "race": f"{race.event.year}/{race.event.round}",
                        "lap": lap,
                        "driver_id": driver.driver.id,
                        "position": driver.position,
                        "context": current,
                        "action": "EXTEND_5",
                        "actual": {
                            horizon: labelled_future(
                                race,
                                current,
                                future[horizon],
                                cuts,
                                lap,
                                horizon,
                                driver.driver.id,
                            )
                            for horizon in (1, 3, 5)
                        },
                    }
                )
        full_context = cached_context(contexts, race, max(laps))
        full_stop_samples, _ = observed_pit_samples(full_context)
        for stop in race.pit_stops:
            prior = [lap for lap, cutoff in cuts.items() if cutoff < stop.entered_at]
            if not prior:
                continue
            lap = max(prior)
            if lap + 5 not in cuts:
                continue
            current = cached_context(contexts, race, lap)
            driver = current.driver(stop.driver_id)
            if driver.status != "active":
                continue
            compound = actual_compound_after_stop(race, current, stop)
            if not compound:
                continue
            rejoin_laps = [
                value
                for value, cutoff in cuts.items()
                if stop.exited_at is not None and cutoff >= stop.exited_at
            ]
            if not rejoin_laps:
                continue
            rejoin_lap = min(rejoin_laps)
            targets = {horizon: rejoin_lap + horizon - 1 for horizon in (1, 3, 5)}
            if any(target not in cuts for target in targets.values()):
                continue
            future = {
                horizon: cached_context(contexts, race, target)
                for horizon, target in targets.items()
            }
            observed_stop = next(
                (
                    sample
                    for sample in full_stop_samples
                    if sample["driver_id"] == stop.driver_id
                    and sample["entered_at"] == stop.entered_at
                ),
                None,
            )
            actual_rejoin = future[1].driver(stop.driver_id)
            first_post_gain = None
            first_post_lap = None
            if observed_stop:
                normalized = dict(
                    normalized_lap_times(
                        full_context,
                        stop.driver_id,
                        observed_stop["pre_laps"] + observed_stop["post_laps"],
                        current_stint=False,
                    )
                )
                pre_values = [
                    normalized[number]
                    for number in observed_stop["pre_laps"]
                    if number in normalized
                ]
                post_values = [
                    normalized[number]
                    for number in observed_stop["post_laps"]
                    if number in normalized
                ]
                if pre_values and post_values:
                    first_post_gain = median(pre_values) - post_values[0]
                    first_post_lap = observed_stop["post_laps"][0]
            pit_now.append(
                {
                    "race": f"{race.event.year}/{race.event.round}",
                    "lap": lap,
                    "driver_id": stop.driver_id,
                    "position": driver.position,
                    "context": current,
                    "action": f"PIT_NOW_{compound.upper()}",
                    "compound_transition": f"{driver.compound}->{compound.upper()}",
                    "decision_to_pit_entry_seconds": stop.entered_at - current.cutoff,
                    "stop": stop,
                    "full_race": race,
                    "actual_transition": {
                        "pit_loss_seconds": observed_stop["loss_seconds"]
                        if observed_stop
                        else None,
                        "rejoin_position": actual_rejoin.position,
                        "rejoin_gap_ahead": actual_rejoin.gap_to_ahead,
                        "rejoin_gap_behind": actual_rejoin.gap_to_behind,
                        "first_post_stop_clean_lap": first_post_lap,
                        "first_post_stop_gain_seconds": first_post_gain,
                    },
                    "actual": {
                        horizon: labelled_future(
                            race,
                            current,
                            future[horizon],
                            cuts,
                            lap,
                            horizon,
                            stop.driver_id,
                            targets[horizon],
                        )
                        for horizon in (1, 3, 5)
                    },
                }
            )
    return {"EXTEND": stay_out, "PIT_NOW": pit_now}


def phase5_legacy_prediction(context, driver_id, action, horizon):
    """Frozen-gap Phase 5 formula retained only for before/after diagnosis."""
    state = build_short_horizon_state(context, driver_id)
    if not hasattr(context, "_phase5_legacy_pit_loss"):
        context._phase5_legacy_pit_loss = estimate_pit_loss(context)
    pit_loss = context._phase5_legacy_pit_loss
    compound = action.removeprefix("PIT_NOW_") if action.startswith("PIT_NOW_") else None
    extension = 0 if compound else int(action.removeprefix("EXTEND_"))
    pit_occurred = extension < horizon
    pre_laps = min(extension, horizon)
    post_laps = horizon - pre_laps
    relative_pace = state.relative_pace_seconds_per_lap
    if relative_pace is None or state.gap_to_leader is None:
        return None
    if pit_occurred and pit_loss.total_seconds is None:
        return None
    cache = getattr(context, "_phase5_legacy_driver", {})
    key = (driver_id, compound)
    if key not in cache:
        rejoin = predict_pit_rejoin(driver_id, context.state, pit_loss)
        traffic = analyze_traffic(context, driver_id, rejoin)
        absolute_pace = get_recent_pace(context, driver_id).seconds
        fresh = estimate_fresh_tyre_delta(context, driver_id, pit_loss, compound)
        cache[key] = rejoin, traffic, absolute_pace, fresh
        context._phase5_legacy_driver = cache
    rejoin, traffic, absolute_pace, fresh = cache[key]
    fresh_gain = (fresh.fresh_tyre_delta or 0.0) * FRESH_RELIABILITY
    current_penalty = _traffic_penalty(context, driver_id, traffic, absolute_pace)
    fresh_pace = absolute_pace - fresh_gain if absolute_pace is not None else None
    rejoin_penalty = _traffic_penalty(
        context, driver_id, traffic, relative_pace, rejoin, fresh_pace
    )
    raw = (
        pre_laps * (relative_pace + (current_penalty or 0.0))
        + (pit_loss.total_seconds if pit_occurred else 0.0)
        + post_laps * (relative_pace - fresh_gain + (rejoin_penalty or 0.0))
    )
    predicted = (
        raw + PHASE5_CALIBRATION["PIT_NOW" if compound else "EXTEND"][horizon]["bias_seconds"]
    )
    gaps = [
        item.gap_to_leader
        for item in context.state.drivers
        if item.driver.id != driver_id
        and item.status == "active"
        and not item.lapped
        and item.gap_to_leader is not None
    ]
    projected_gap = state.gap_to_leader + predicted
    position = 1 + sum(gap < projected_gap for gap in gaps)
    widening = ceil(REJOIN_MAE_POSITIONS) if pit_occurred else 0
    return {
        "delta_time": predicted,
        "position": position,
        "position_range": (
            max(1, position - widening),
            min(len(context.state.drivers), position + widening),
        ),
        "pit_loss": pit_loss.total_seconds,
        "rejoin_position": rejoin.projected_position,
        "rejoin_gap_ahead": rejoin.gap_ahead,
        "rejoin_gap_behind": rejoin.gap_behind,
        "first_post_stop_gain": fresh.fresh_tyre_delta,
    }


def evaluate_phase5_baseline(cases):
    rows = []
    for kind, selected in cases.items():
        for case in selected:
            current = case["context"].driver(case["driver_id"])
            for horizon in (1, 3, 5):
                prediction = phase5_legacy_prediction(
                    case["context"], case["driver_id"], case["action"], horizon
                )
                actual_gap, actual_position = case["actual"][horizon]
                actual_delta = (
                    actual_gap - current.gap_to_leader
                    if actual_gap is not None and current.gap_to_leader is not None
                    else None
                )
                rows.append(
                    {
                        "kind": kind,
                        "race": case["race"],
                        "lap": case["lap"],
                        "driver_id": case["driver_id"],
                        "position": case["position"],
                        "horizon": horizon,
                        "predicted_delta_time": prediction["delta_time"] if prediction else None,
                        "actual_delta_time": actual_delta,
                        "time_error": prediction["delta_time"] - actual_delta
                        if prediction and actual_delta is not None
                        else None,
                        "predicted_position": prediction["position"] if prediction else None,
                        "actual_position": actual_position,
                        "position_error": prediction["position"] - actual_position
                        if prediction and actual_position is not None
                        else None,
                        "position_range_miss": None,
                        "position_range": prediction["position_range"] if prediction else None,
                        "confidence": "LOW" if prediction else "INSUFFICIENT",
                        "circuit": case["context"].race.event.circuit.id,
                        "grid_region": grid_region(case["position"]),
                        "compound_transition": case.get("compound_transition"),
                        "traffic_level": build_short_horizon_state(
                            case["context"], case["driver_id"]
                        ).traffic,
                        "race_state": (
                            "green"
                            if case["context"].state.track.track_status == "1"
                            else "abnormal_or_unknown"
                        ),
                        "pit_loss_seconds": prediction["pit_loss"] if prediction else None,
                        "predicted_rejoin": {
                            "position": prediction["rejoin_position"],
                            "gap_ahead": prediction["rejoin_gap_ahead"],
                            "gap_behind": prediction["rejoin_gap_behind"],
                        }
                        if prediction
                        else None,
                        "actual_transition": case.get("actual_transition"),
                        "decision_to_pit_entry_seconds": case.get("decision_to_pit_entry_seconds"),
                    }
                )
    return rows


def evaluate_factual_cases(cases, calibration):
    rows = []
    for kind, selected in cases.items():
        for case in selected:
            result = simulate_action(
                case["context"], case["driver_id"], case["action"], calibration
            )
            current = case["context"].driver(case["driver_id"])
            for outcome in result.outcomes:
                actual_gap, actual_position = case["actual"][outcome.horizon_laps]
                actual_delta = (
                    actual_gap - current.gap_to_leader
                    if actual_gap is not None and current.gap_to_leader is not None
                    else None
                )
                predicted_position = (
                    outcome.expected_position
                    if outcome.expected_position is not None
                    else sum(outcome.position_range) / 2
                    if outcome.position_range
                    else None
                )
                range_miss = (
                    max(
                        outcome.position_range[0] - actual_position,
                        actual_position - outcome.position_range[1],
                        0,
                    )
                    if outcome.position_range and actual_position is not None
                    else None
                )
                rows.append(
                    {
                        "kind": kind,
                        "race": case["race"],
                        "lap": case["lap"],
                        "driver_id": case["driver_id"],
                        "position": case["position"],
                        "horizon": outcome.horizon_laps,
                        "predicted_delta_time": outcome.expected_delta_time_seconds,
                        "actual_delta_time": actual_delta,
                        "time_error": outcome.expected_delta_time_seconds - actual_delta
                        if outcome.expected_delta_time_seconds is not None
                        and actual_delta is not None
                        else None,
                        "predicted_position": predicted_position,
                        "position_range": outcome.position_range,
                        "actual_position": actual_position,
                        "position_error": predicted_position - actual_position
                        if predicted_position is not None and actual_position is not None
                        else None,
                        "position_range_miss": range_miss,
                        "confidence": outcome.confidence,
                        "circuit": case["context"].race.event.circuit.id,
                        "grid_region": grid_region(case["position"]),
                        "compound_transition": case.get("compound_transition"),
                        "traffic_level": build_short_horizon_state(
                            case["context"], case["driver_id"]
                        ).traffic,
                        "race_state": (
                            "green"
                            if case["context"].state.track.track_status == "1"
                            else "abnormal_or_unknown"
                        ),
                        "pit_loss_seconds": outcome.components.get("pit_loss_components", {}).get(
                            "combined_observed_stop_lap_residual_seconds"
                        ),
                        "predicted_rejoin": outcome.components.get("predicted_rejoin"),
                        "warm_up_curve": outcome.components.get("warm_up_curve"),
                        "actual_transition": case.get("actual_transition"),
                        "decision_to_pit_entry_seconds": case.get("decision_to_pit_entry_seconds"),
                    }
                )
    return rows


def derive_calibration(raw_rows):
    result = {}
    for kind in ("EXTEND", "PIT_NOW"):
        result[kind] = {}
        for horizon in (1, 3, 5):
            selected = [
                row
                for row in raw_rows
                if row["kind"] == kind
                and row["horizon"] == horizon
                and row["time_error"] is not None
            ]
            bias = -median(row["time_error"] for row in selected) if selected else 0.0
            errors = [
                row["predicted_delta_time"] + bias - row["actual_delta_time"] for row in selected
            ]
            result[kind][horizon] = {
                "bias_seconds": round(bias, 3),
                "mae_seconds": round(mean(abs(value) for value in errors), 3) if errors else 99.0,
                "development_samples": len(errors),
            }
    return result


def factual_metrics(rows, kind, horizon):
    selected = [row for row in rows if row["kind"] == kind and row["horizon"] == horizon]
    timed = [row for row in selected if row["time_error"] is not None]
    positioned = [row for row in selected if row["position_error"] is not None]
    ranged = [row for row in selected if row["position_range_miss"] is not None]
    absolute_time = sorted(abs(row["time_error"]) for row in timed)
    absolute_position = sorted(abs(row["position_error"]) for row in positioned)

    def p90(values):
        return values[ceil(0.9 * len(values)) - 1] if values else None

    return {
        "cases": len(selected),
        "relative_time_samples": len(timed),
        "relative_time_mae_seconds": mean(abs(row["time_error"]) for row in timed)
        if timed
        else None,
        "relative_time_bias_seconds": mean(row["time_error"] for row in timed) if timed else None,
        "relative_time_median_absolute_error_seconds": median(absolute_time)
        if absolute_time
        else None,
        "relative_time_p90_absolute_error_seconds": p90(absolute_time),
        "position_samples": len(positioned),
        "position_mae": mean(abs(row["position_error"]) for row in positioned)
        if positioned
        else None,
        "position_bias": mean(row["position_error"] for row in positioned) if positioned else None,
        "position_median_absolute_error": median(absolute_position) if absolute_position else None,
        "position_p90_absolute_error": p90(absolute_position),
        "position_range_samples": len(ranged),
        "position_range_miss_mean": mean(row["position_range_miss"] for row in ranged)
        if ranged
        else None,
    }


def factual_report(rows):
    return {
        kind: {str(horizon): factual_metrics(rows, kind, horizon) for horizon in (1, 3, 5)}
        for kind in ("EXTEND", "PIT_NOW")
    }


def _bucket(value, cuts, labels):
    if value is None:
        return "UNKNOWN"
    for cut, label in zip(cuts, labels, strict=False):
        if value <= cut:
            return label
    return labels[-1]


def grouped_pit_error(rows):
    selected = [row for row in rows if row["kind"] == "PIT_NOW"]
    dimensions = {
        "circuit": lambda row: row["circuit"],
        "compound_transition": lambda row: row["compound_transition"] or "UNKNOWN",
        "traffic_level": lambda row: row["traffic_level"],
        "starting_position": lambda row: _bucket(
            row["position"], (5, 10, 15, 99), ("P1-P5", "P6-P10", "P11-P15", "P16+")
        ),
        "pit_lane_loss": lambda row: _bucket(
            row["pit_loss_seconds"], (20, 24, 28, 999), ("<=20", "20-24", "24-28", ">28")
        ),
        "race_lap": lambda row: _bucket(
            row["lap"], (15, 30, 45, 999), ("L1-L15", "L16-L30", "L31-L45", "L46+")
        ),
        "race_state": lambda row: row["race_state"],
        "grid_region": lambda row: row["grid_region"],
        "decision_to_pit_entry": lambda row: _bucket(
            row.get("decision_to_pit_entry_seconds"),
            (15, 30, 60, 9999),
            ("<=15s", "16-30s", "31-60s", ">60s"),
        ),
    }
    result = {}
    for name, key in dimensions.items():
        groups = defaultdict(list)
        for row in selected:
            groups[key(row)].append(row)
        result[name] = {
            group: {
                str(horizon): factual_metrics(values, "PIT_NOW", horizon) for horizon in (1, 3, 5)
            }
            for group, values in sorted(groups.items())
        }
    return result


def pit_case_records(rows):
    records = {}
    for row in rows:
        if row["kind"] != "PIT_NOW":
            continue
        key = (row["race"], row["lap"], row["driver_id"])
        record = records.setdefault(
            key,
            {
                "race": row["race"],
                "circuit": row["circuit"],
                "lap": row["lap"],
                "driver_id": row["driver_id"],
                "starting_position": row["position"],
                "grid_region": row["grid_region"],
                "compound_transition": row["compound_transition"],
                "traffic_level": row["traffic_level"],
                "race_state": row["race_state"],
                "decision_to_pit_entry_seconds": row.get("decision_to_pit_entry_seconds"),
                "predicted_pit_loss_seconds": row["pit_loss_seconds"],
                "actual_pit_loss_seconds": (row["actual_transition"] or {}).get("pit_loss_seconds"),
                "predicted_rejoin": row["predicted_rejoin"],
                "actual_rejoin": row["actual_transition"],
                "warm_up_curve": row.get("warm_up_curve"),
                "horizons": {},
            },
        )
        record["horizons"][str(row["horizon"])] = {
            "predicted_relative_time": row["predicted_delta_time"],
            "actual_relative_time": row["actual_delta_time"],
            "time_error": row["time_error"],
            "predicted_position": row["predicted_position"],
            "actual_position": row["actual_position"],
            "position_error": row["position_error"],
        }
    return list(records.values())


def tyre_threshold(races):
    ages = []
    for race in races:
        for stop in race.pit_stops:
            stints = [
                stint
                for stint in race.stints
                if stint.driver_id == stop.driver_id
                and stint.observed_at < stop.entered_at
                and stint.tyre_age is not None
            ]
            if stints:
                ages.append(max(stints, key=lambda stint: stint.observed_at).tyre_age)
    return int(round(median(ages))), len(ages)


def grid_region(position):
    if position is None:
        return "UNKNOWN"
    if position <= 5:
        return "P1-P5"
    if position <= 10:
        return "P6-P10"
    if position <= 15:
        return "P11-P15"
    return "P16+"


def preferred_pit(actions):
    pits = [action for action in actions if action.startswith("PIT_NOW_")]
    hardness = {"HARD": 3, "MEDIUM": 2, "SOFT": 1, "INTERMEDIATE": 1, "WET": 2}
    return (
        max(pits, key=lambda action: hardness.get(action.removeprefix("PIT_NOW_"), 0))
        if pits
        else None
    )


def operational_phase4_action(decision, legal):
    if decision.recommended_action == "PIT_NOW" and decision.recommended_compound:
        return f"PIT_NOW_{decision.recommended_compound}"
    if decision.recommended_action == "EXTEND" and decision.recommended_extension_laps:
        return f"EXTEND_{decision.recommended_extension_laps}"
    extensions = [action for action in legal if action.startswith("EXTEND_")]
    return (
        max(extensions, key=lambda action: int(action.removeprefix("EXTEND_")))
        if extensions
        else None
    )


def outcome_value(result, current_position, horizon=5):
    outcome = next(item for item in result.outcomes if item.horizon_laps == horizon)
    position_change = outcome.expected_position_change
    if position_change is None and outcome.position_range:
        position_change = current_position - sum(outcome.position_range) / 2
    return outcome, position_change


def policy_rows(races, calibration, threshold, prior_races=()):
    rows, exclusions, backmarker_exclusions = [], Counter(), Counter()
    profiles = {}
    for race in races:
        laps, cuts = lap_cutoffs(race)
        decision_laps = [
            value for value in laps if value >= 10 and value % 10 == 0 and value + 5 in cuts
        ]
        for lap in decision_laps:
            context = AnalysisContext(race, lap)
            profile_key = (race.event.circuit.id, race.event.race_date)
            if profile_key not in profiles:
                profiles[profile_key] = circuit_profile(
                    prior_races, race.event.circuit.id, race.event.race_date
                )
            context._circuit_profile = profiles[profile_key]
            grid = analyze_strategy_all(context)
            for decision in grid.decisions:
                driver_id = decision.driver.id
                is_backmarker = (
                    decision.current_position is not None and decision.current_position >= 16
                )
                if decision.current_position is None:
                    exclusions["missing current position"] += 1
                    continue
                legal = legal_counterfactual_actions(context, driver_id)
                extensions = [action for action in legal if action.startswith("EXTEND_")]
                if not extensions:
                    exclusions["no extension action"] += 1
                    if is_backmarker:
                        backmarker_exclusions["no extension action"] += 1
                    continue
                extend3 = max(
                    extensions,
                    key=lambda action: min(int(action.removeprefix("EXTEND_")), 3),
                )
                pit = preferred_pit(legal)
                phase4 = operational_phase4_action(decision, legal)
                policies = {
                    "phase4": phase4,
                    "always_extend_3": extend3,
                    "pit_when_legal": pit or extend3,
                    "age_threshold": pit
                    if pit and decision.tyre_age is not None and decision.tyre_age >= threshold
                    else extend3,
                }
                simulated = {
                    action: simulate_action(context, driver_id, action, calibration)
                    for action in set(policies.values())
                    if action
                }
                unusable = []
                for policy, action in policies.items():
                    if action is None:
                        unusable.append(f"{policy}: no action")
                        continue
                    for horizon in simulated[action].outcomes:
                        if (
                            horizon.expected_delta_time_seconds is None
                            or horizon.position_range is None
                        ):
                            missing = horizon.components.get("missing_inputs", [])
                            unusable.extend(f"{policy}: {item}" for item in missing)
                if unusable:
                    exclusions.update(set(unusable))
                    if is_backmarker:
                        backmarker_exclusions.update(set(unusable))
                    continue
                row = {
                    "race": f"{race.event.year}/{race.event.round}",
                    "lap": lap,
                    "driver_id": driver_id,
                    "position": decision.current_position,
                    "region": grid_region(decision.current_position),
                    "phase4_state": decision.recommended_action,
                }
                for policy, action in policies.items():
                    outcome3, change3 = outcome_value(
                        simulated[action], decision.current_position, 3
                    )
                    outcome5, change5 = outcome_value(
                        simulated[action], decision.current_position, 5
                    )
                    row[policy] = {
                        "action": action,
                        "position_change_3": change3,
                        "position_change_5": change5,
                        "relative_time_3": outcome3.expected_delta_time_seconds,
                        "relative_time_5": outcome5.expected_delta_time_seconds,
                        "uncertainty_3": outcome3.uncertainty_seconds,
                        "uncertainty_5": outcome5.uncertainty_seconds,
                    }
                rows.append(row)
    return rows, dict(exclusions), dict(backmarker_exclusions)


def policy_metrics(rows, policy):
    values = [row[policy] for row in rows]
    changes = [value["position_change_5"] for value in values]
    return {
        "common_snapshots": len(rows),
        "mean_expected_position_change_5": mean(changes) if changes else None,
        "median_expected_position_change_5": median(changes) if changes else None,
        "mean_expected_relative_time_3": mean(value["relative_time_3"] for value in values)
        if values
        else None,
        "mean_expected_relative_time_5": mean(value["relative_time_5"] for value in values)
        if values
        else None,
        "downside_rate": mean(change < 0 for change in changes) if changes else None,
        "pit_frequency": mean(value["action"].startswith("PIT_NOW") for value in values)
        if values
        else None,
        "extend_frequency": mean(value["action"].startswith("EXTEND") for value in values)
        if values
        else None,
        "actions": dict(Counter(value["action"] for value in values)),
        "hold_frequency": mean(row["phase4_state"] == "HOLD_NO_CLEAR_ADVANTAGE" for row in rows)
        if policy == "phase4" and rows
        else 0.0,
        "decision_coverage": mean(row["phase4_state"] in {"PIT_NOW", "EXTEND"} for row in rows)
        if policy == "phase4" and rows
        else 1.0,
    }


def policy_report(rows):
    policies = ("phase4", "always_extend_3", "pit_when_legal", "age_threshold")
    return {
        "overall": {policy: policy_metrics(rows, policy) for policy in policies},
        "by_grid_region": {
            region: {
                policy: policy_metrics([row for row in rows if row["region"] == region], policy)
                for policy in policies
            }
            for region in ("P1-P5", "P6-P10", "P11-P15", "P16+")
        },
        "by_circuit": {
            circuit: {
                policy: policy_metrics([row for row in rows if row["race"] == circuit], policy)
                for policy in policies
            }
            for circuit in sorted({row["race"] for row in rows})
        },
    }


def build_report():
    development_races = load_races(DEVELOPMENT_RACES)
    validation_races = load_races(VALIDATION_RACES)
    development_cases = collect_factual_cases(development_races)
    validation_cases = collect_factual_cases(validation_races)
    baseline_development = evaluate_phase5_baseline(development_cases)
    baseline_validation = evaluate_phase5_baseline(validation_cases)
    development_profiles = attach_chronological_profiles(development_cases, development_races)
    validation_profiles = attach_chronological_profiles(
        validation_cases, development_races + validation_races
    )
    raw_development = evaluate_factual_cases(development_cases, RAW_CALIBRATION)
    calibration = derive_calibration(raw_development)
    development_factual = evaluate_factual_cases(development_cases, calibration)
    validation_factual = evaluate_factual_cases(validation_cases, calibration)
    threshold, threshold_samples = tyre_threshold(development_races)
    (
        development_policy_rows,
        development_exclusions,
        development_backmarker_exclusions,
    ) = policy_rows(development_races, calibration, threshold, development_races)
    validation_policy_rows, validation_exclusions, validation_backmarker_exclusions = policy_rows(
        validation_races,
        calibration,
        threshold,
        development_races + validation_races,
    )
    backmarker_rows = [
        row
        for row in validation_policy_rows
        if row["position"] is not None and row["position"] >= 16
    ]
    return {
        "method": {
            "development_races": DEVELOPMENT_RACES,
            "validation_races": VALIDATION_RACES,
            "causal_boundary": "Only the AnalysisContext prefix is passed to simulation",
            "factual_future_use": "Future gaps/positions and actual compounds exist only here",
            "policy_cohort": "Every reported policy metric uses the same usable snapshots",
            "circularity_guard": (
                "Simulation never receives Phase 4 score, margin or recommendation; the "
                "recommendation is consulted only after outcomes exist to select an action."
            ),
        },
        "calibration": calibration,
        "circuit_profiles": {
            "development": development_profiles,
            "validation": validation_profiles,
        },
        "global_tyre_age_threshold": {
            "laps": threshold,
            "samples": threshold_samples,
            "method": "Rounded median pre-stop age across the complete development split",
        },
        "factual_validation": {
            "phase5_before": {
                "development": factual_report(baseline_development),
                "validation": factual_report(baseline_validation),
            },
            "phase5b_after": {
                "development": factual_report(development_factual),
                "validation": factual_report(validation_factual),
            },
        },
        "pit_error_diagnosis": {
            "phase5_before_grouped_validation": grouped_pit_error(baseline_validation),
            "phase5b_after_grouped_validation": grouped_pit_error(validation_factual),
            "phase5_factual_pit_cases": pit_case_records(baseline_validation),
            "phase5b_factual_pit_cases": pit_case_records(validation_factual),
            "interpretation": (
                "Compare stop-residual and first-post-lap errors with cumulative +3/+5 "
                "error; the legacy model applies the first post-stop effect on the stop lap "
                "and repeats it across the horizon while rivals remain frozen."
            ),
        },
        "policy_comparison": {
            "development": policy_report(development_policy_rows),
            "validation": policy_report(validation_policy_rows),
        },
        "coverage": {
            "development_common_snapshots": len(development_policy_rows),
            "validation_common_snapshots": len(validation_policy_rows),
            "development_exclusions": development_exclusions,
            "validation_exclusions": validation_exclusions,
            "development_backmarker_exclusions": development_backmarker_exclusions,
            "validation_backmarker_exclusions": validation_backmarker_exclusions,
        },
        "backmarkers": {
            "definition": "P16+ on the common held-out cohort",
            "common_snapshots": len(backmarker_rows),
            "policies": policy_report(backmarker_rows)["overall"],
            "examples": backmarker_rows[:12],
            "exclusion_causes": validation_backmarker_exclusions,
        },
        "phase4_recalibration": {
            "changed": False,
            "reason": (
                "Outcome-model factual errors must be judged before its counterfactual ranking "
                "can justify changing Phase 4. Validation races were not used to tune Phase 4."
            ),
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="docs/counterfactual-validation.json")
    arguments = parser.parse_args()
    report = build_report()
    Path(arguments.output).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
