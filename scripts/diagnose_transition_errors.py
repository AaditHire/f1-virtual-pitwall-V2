"""Pre-change Phase 5C lap-by-lap PIT transition diagnosis."""

import json
from math import ceil
from pathlib import Path
from statistics import mean, median

from evaluate_counterfactuals import (
    DEVELOPMENT_RACES,
    PRIOR_RACES,
    VALIDATION_RACES,
    attach_chronological_profiles,
    collect_factual_cases,
    grid_region,
    load_races,
)

from f1_pitwall.services.predictive_pace import estimate_multi_lap_relative_pace
from f1_pitwall.services.simulation import simulate_action

CHECKPOINTS = (
    "rejoin",
    "post_stop_lap_1",
    "post_stop_lap_2",
    "post_stop_lap_3",
    "post_stop_lap_4",
    "post_stop_lap_5",
)


def _p90(values):
    values = sorted(values)
    return values[ceil(0.9 * len(values)) - 1] if values else None


def _raw_prediction(case):
    result = simulate_action(case["context"], case["driver_id"], case["action"])
    outcome = next(item for item in result.outcomes if item.horizon_laps == 5)
    trace = outcome.components.get("lap_by_lap_projection", [])
    if outcome.expected_delta_time_seconds is None or not trace:
        return [], trace, outcome, result
    initial = outcome.components["projected_gap_to_leader_seconds"]
    initial -= outcome.expected_delta_time_seconds
    deltas = [row["driver_gap"] - initial for row in trace]
    if deltas:
        deltas.append(deltas[-1] + (deltas[-1] - deltas[-2] if len(deltas) > 1 else 0.0))
    return deltas, trace, outcome, result


def collect_rows(cases):
    rows = []
    for case in cases["PIT_NOW"]:
        predicted, trace, outcome, result = _raw_prediction(case)
        improved_pace = estimate_multi_lap_relative_pace(
            case["context"], case["driver_id"]
        )
        baseline_pace = outcome.components.get("relative_pace_seconds_per_lap")
        current_gap = case["context"].driver(case["driver_id"]).gap_to_leader
        previous_predicted = previous_actual = 0.0
        positions = {
            "rejoin": result.outcomes[0].physical_track_position,
            "post_stop_lap_2": result.outcomes[1].physical_track_position,
            "post_stop_lap_4": result.outcomes[2].physical_track_position,
        }
        for index, checkpoint in enumerate(CHECKPOINTS):
            actual = case["actual_transition_path"].get(checkpoint, {})
            actual_gap = actual.get("gap_to_leader")
            actual_delta = (
                actual_gap - current_gap
                if actual.get("stable_gap_reference")
                and actual_gap is not None
                and current_gap is not None
                else None
            )
            predicted_delta = predicted[index] if index < len(predicted) else None
            row = {
                "race": case["race"],
                "circuit": case["context"].race.event.circuit.id,
                "lap": case["lap"],
                "driver_id": case["driver_id"],
                "starting_position": case["position"],
                "grid_region": grid_region(case["position"]),
                "compound_transition": case["compound_transition"],
                "traffic_level": outcome.components["predicted_rejoin"]["traffic"],
                "decision_to_entry_seconds": case["decision_to_pit_entry_seconds"],
                "checkpoint": checkpoint,
                "predicted_relative_delta_raw": predicted_delta,
                "actual_relative_delta": actual_delta,
                "raw_error": predicted_delta - actual_delta
                if predicted_delta is not None and actual_delta is not None
                else None,
                "predicted_increment": predicted_delta - previous_predicted
                if predicted_delta is not None
                else None,
                "actual_increment": actual_delta - previous_actual
                if actual_delta is not None
                else None,
                "incremental_error": (
                    predicted_delta - previous_predicted - (actual_delta - previous_actual)
                    if predicted_delta is not None and actual_delta is not None
                    else None
                ),
                "warm_up_gain": trace[index].get("fresh_gain_seconds")
                if index < len(trace)
                else trace[-1].get("fresh_gain_seconds")
                if trace
                else None,
                "traffic_loss": trace[index].get("traffic_loss_seconds")
                if index < len(trace)
                else trace[-1].get("traffic_loss_seconds")
                if trace
                else None,
                "predicted_pit_loss": outcome.components["pit_loss_components"][
                    "combined_observed_stop_lap_residual_seconds"
                ],
                "actual_pit_loss": case["actual_transition"]["pit_loss_seconds"],
                "baseline_relative_pace": baseline_pace,
                "improved_relative_pace": improved_pace[
                    "expected_relative_pace_seconds_per_lap"
                ],
                "improved_pace_uncertainty": improved_pace[
                    "uncertainty_seconds_per_lap"
                ],
                "improved_pace_evidence": improved_pace,
                "predicted_position": positions.get(checkpoint),
                "actual_position": actual.get("position"),
            }
            rows.append(row)
            if predicted_delta is not None:
                previous_predicted = predicted_delta
            if actual_delta is not None:
                previous_actual = actual_delta
    return rows


def calibration(rows):
    result = {}
    for checkpoint in CHECKPOINTS:
        errors = [
            row["raw_error"]
            for row in rows
            if row["checkpoint"] == checkpoint and row["raw_error"] is not None
        ]
        result[checkpoint] = -median(errors) if errors else 0.0
    return result


def apply_calibration(rows, values):
    result = []
    previous = {}
    for row in rows:
        item = dict(row)
        predicted = row["predicted_relative_delta_raw"]
        actual = row["actual_relative_delta"]
        adjusted = predicted + values[row["checkpoint"]] if predicted is not None else None
        item["predicted_relative_delta"] = adjusted
        item["error"] = adjusted - actual if adjusted is not None and actual is not None else None
        key = row["race"], row["lap"], row["driver_id"]
        prior = previous.get(key, (0.0, 0.0))
        item["calibrated_incremental_error"] = (
            adjusted - prior[0] - (actual - prior[1])
            if adjusted is not None and actual is not None
            else None
        )
        if adjusted is not None and actual is not None:
            previous[key] = adjusted, actual
        result.append(item)
    return result


def metrics(rows):
    result = {}
    for checkpoint in CHECKPOINTS:
        selected = [
            row for row in rows if row["checkpoint"] == checkpoint and row["error"] is not None
        ]
        errors = [row["error"] for row in selected]
        increments = [row["calibrated_incremental_error"] for row in selected]
        result[checkpoint] = {
            "samples": len(errors),
            "mae_seconds": mean(abs(value) for value in errors) if errors else None,
            "bias_seconds": mean(errors) if errors else None,
            "median_absolute_error_seconds": median(abs(value) for value in errors)
            if errors
            else None,
            "p90_absolute_error_seconds": _p90([abs(value) for value in errors]),
            "mean_incremental_error_seconds": mean(increments) if increments else None,
            "median_warm_up_gain_seconds": median(
                row["warm_up_gain"] for row in selected if row["warm_up_gain"] is not None
            )
            if any(row["warm_up_gain"] is not None for row in selected)
            else None,
            "mean_traffic_loss_seconds": mean(
                row["traffic_loss"] for row in selected if row["traffic_loss"] is not None
            )
            if any(row["traffic_loss"] is not None for row in selected)
            else None,
        }
    return result


def outliers(rows, limit=20):
    final = [row for row in rows if row["checkpoint"] == "post_stop_lap_4" and row["error"]]
    selected = sorted(final, key=lambda row: abs(row["error"]), reverse=True)[:limit]
    for row in selected:
        causes = []
        if row["decision_to_entry_seconds"] > 60:
            causes.append("decision_snapshot_too_early")
        if row["actual_pit_loss"] is None:
            causes.append("unqualified_actual_stop")
        elif abs(row["predicted_pit_loss"] - row["actual_pit_loss"]) > 4:
            causes.append("unusual_or_mispredicted_pit_stop")
        if row["traffic_level"] in {"MODERATE_TRAFFIC", "HEAVY_TRAFFIC", "UNKNOWN"}:
            causes.append("traffic_interaction")
        if row["grid_region"] == "P16+":
            causes.append("backmarker_or_lapped_interaction")
        if abs(row["incremental_error"]) > 2:
            causes.append("multi_lap_pace_failure")
        row["classified_causes"] = causes or ["unresolved"]
    return selected


def build_report():
    prior_races = load_races(PRIOR_RACES)
    development_races = load_races(DEVELOPMENT_RACES)
    validation_races = load_races(VALIDATION_RACES)
    development = collect_factual_cases(development_races)
    validation = collect_factual_cases(validation_races)
    attach_chronological_profiles(development, prior_races + development_races)
    attach_chronological_profiles(
        validation, prior_races + development_races + validation_races
    )
    development_rows = collect_rows(development)
    validation_rows = collect_rows(validation)
    fitted = calibration(development_rows)
    development_rows = apply_calibration(development_rows, fitted)
    validation_rows = apply_calibration(validation_rows, fitted)
    return {
        "model": "unaltered Phase 5B production model",
        "chronology": "calibration from 2023 development only; 2024 is untouched holdout",
        "checkpoint_calibration_seconds": fitted,
        "development": metrics(development_rows),
        "validation": metrics(validation_rows),
        "development_rows": development_rows,
        "validation_rows": validation_rows,
        "worst_20_post_stop_lap_4": outliers(validation_rows),
    }


if __name__ == "__main__":
    report = build_report()
    output = Path("docs/pit-transition-diagnosis-phase5c.json")
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "validation": report["validation"]}, indent=2))
