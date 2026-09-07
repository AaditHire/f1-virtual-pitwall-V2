"""Chronological Phase 5C component ablation over the PIT transition diagnosis."""

import json
from collections import Counter, defaultdict
from math import ceil
from pathlib import Path
from statistics import mean, median

from f1_pitwall.services.simulation import FRESH_RELIABILITY

CHECKPOINTS = (
    "rejoin",
    "post_stop_lap_1",
    "post_stop_lap_2",
    "post_stop_lap_3",
    "post_stop_lap_4",
    "post_stop_lap_5",
)


def _candidate_rows(rows):
    cumulative_warm_up = defaultdict(float)
    result = []
    for source in rows:
        row = dict(source)
        key = source["race"], source["lap"], source["driver_id"]
        checkpoint_index = CHECKPOINTS.index(source["checkpoint"])
        if checkpoint_index and source["warm_up_gain"] is not None:
            cumulative_warm_up[key] += source["warm_up_gain"] * (1 / FRESH_RELIABILITY - 1)
        base = source["predicted_relative_delta_raw"]
        baseline_pace = source.get("baseline_relative_pace")
        improved_pace = source.get("improved_relative_pace")
        row["A_phase5b_raw"] = base
        row["B_improved_pace_raw"] = (
            base
            + (checkpoint_index + 1) * (improved_pace - baseline_pace)
            - cumulative_warm_up[key]
            if base is not None and baseline_pace is not None and improved_pace is not None
            else None
        )
        result.append(row)
    return result


def _fit_global_calibration(rows, field):
    result = {}
    for checkpoint in CHECKPOINTS:
        errors = [
            row[field] - row["actual_relative_delta"]
            for row in rows
            if row["checkpoint"] == checkpoint
            and row[field] is not None
            and row["actual_relative_delta"] is not None
        ]
        result[checkpoint] = -median(errors) if errors else 0.0
    return result


def _fit_traffic_bins(rows, pace_calibration):
    result = {}
    for checkpoint in CHECKPOINTS:
        all_errors = [
            row["B_improved_pace_raw"] + pace_calibration[checkpoint] - row["actual_relative_delta"]
            for row in rows
            if row["checkpoint"] == checkpoint
            and row["B_improved_pace_raw"] is not None
            and row["actual_relative_delta"] is not None
        ]
        centre = median(all_errors) if all_errors else 0.0
        for traffic in (
            "CLEAR_AIR",
            "LIGHT_TRAFFIC",
            "MODERATE_TRAFFIC",
            "HEAVY_TRAFFIC",
            "UNKNOWN",
        ):
            errors = [
                row["B_improved_pace_raw"]
                + pace_calibration[checkpoint]
                - row["actual_relative_delta"]
                for row in rows
                if row["checkpoint"] == checkpoint
                and row["traffic_level"] == traffic
                and row["B_improved_pace_raw"] is not None
                and row["actual_relative_delta"] is not None
            ]
            result[checkpoint, traffic] = {
                "adjustment_seconds": centre - median(errors) if len(errors) >= 8 else 0.0,
                "samples": len(errors),
                "supported": len(errors) >= 8,
            }
    return result


def fit(development):
    calibrations = {
        "A_phase5b": _fit_global_calibration(development, "A_phase5b_raw"),
        "B_improved_pace": _fit_global_calibration(development, "B_improved_pace_raw"),
    }
    traffic = _fit_traffic_bins(development, calibrations["B_improved_pace"])
    return calibrations, traffic


def apply(rows, calibrations, traffic):
    output = []
    for source in rows:
        row = dict(source)
        checkpoint = row["checkpoint"]
        a = row["A_phase5b_raw"]
        b = row["B_improved_pace_raw"]
        row["A_phase5b"] = a + calibrations["A_phase5b"][checkpoint] if a is not None else None
        row["B_improved_pace"] = (
            b + calibrations["B_improved_pace"][checkpoint] if b is not None else None
        )
        traffic_bin = traffic[checkpoint, row["traffic_level"]]
        row["C_pace_traffic"] = (
            row["B_improved_pace"] + traffic_bin["adjustment_seconds"]
            if row["B_improved_pace"] is not None
            else None
        )
        # The overtake classifier is evaluated separately. It did not alter time prediction.
        row["D_pace_traffic_overtaking"] = row["C_pace_traffic"]
        output.append(row)
    return output


def metric(rows, field, checkpoint):
    selected = [
        row
        for row in rows
        if row["checkpoint"] == checkpoint
        and row[field] is not None
        and row["actual_relative_delta"] is not None
    ]
    errors = [row[field] - row["actual_relative_delta"] for row in selected]
    absolute = sorted(abs(value) for value in errors)
    return {
        "samples": len(errors),
        "mae_seconds": mean(absolute) if absolute else None,
        "bias_seconds": mean(errors) if errors else None,
        "median_absolute_error_seconds": median(absolute) if absolute else None,
        "p90_absolute_error_seconds": absolute[ceil(0.9 * len(absolute)) - 1] if absolute else None,
    }


def report_metrics(rows):
    fields = (
        "A_phase5b",
        "B_improved_pace",
        "C_pace_traffic",
        "D_pace_traffic_overtaking",
    )
    return {
        field: {checkpoint: metric(rows, field, checkpoint) for checkpoint in CHECKPOINTS}
        for field in fields
    }


def overtake_examples(rows):
    selected = [
        row
        for row in rows
        if row["checkpoint"] in {"rejoin", "post_stop_lap_2", "post_stop_lap_4"}
        and row["predicted_position"] is not None
        and row["actual_position"] is not None
    ]
    grouped = defaultdict(list)
    for row in selected:
        grouped[row["race"], row["lap"], row["driver_id"]].append(row)
    examples = []
    for values in grouped.values():
        values.sort(key=lambda row: CHECKPOINTS.index(row["checkpoint"]))
        if len(values) < 2:
            continue
        first, last = values[0], values[-1]
        predicted = last["predicted_position"] < first["predicted_position"]
        actual = last["actual_position"] < first["actual_position"]
        examples.append(
            {
                "circuit": first["circuit"],
                "grid_region": first["grid_region"],
                "traffic_level": first["traffic_level"],
                "predicted_crossing": predicted,
                "actual_overtake": actual,
            }
        )
    return examples


def classification_metrics(predictions, labels):
    counts = Counter(zip(predictions, labels, strict=True))
    true_positive = counts[True, True]
    predicted_positive = sum(value for (predicted, _), value in counts.items() if predicted)
    actual_positive = sum(value for (_, actual), value in counts.items() if actual)
    true_negative = counts[False, False]
    actual_negative = sum(value for (_, actual), value in counts.items() if not actual)
    recall = true_positive / actual_positive if actual_positive else None
    specificity = true_negative / actual_negative if actual_negative else None
    return {
        "samples": len(labels),
        "confusion": {str(key): value for key, value in counts.items()},
        "accuracy": mean(
            predicted == actual for predicted, actual in zip(predictions, labels, strict=True)
        )
        if labels
        else None,
        "precision": true_positive / predicted_positive if predicted_positive else None,
        "recall": recall,
        "specificity": specificity,
        "balanced_accuracy": (recall + specificity) / 2
        if recall is not None and specificity is not None
        else None,
        "brier_score": mean(
            (float(predicted) - actual) ** 2
            for predicted, actual in zip(predictions, labels, strict=True)
        )
        if labels
        else None,
    }


def overtake_metrics(development_rows, validation_rows):
    development = overtake_examples(development_rows)
    validation = overtake_examples(validation_rows)
    global_rate = mean(row["actual_overtake"] for row in development)
    levels = (
        ("circuit", "grid_region", "traffic_level", "predicted_crossing"),
        ("circuit", "predicted_crossing"),
        ("predicted_crossing",),
    )
    bins = {}
    for fields in levels:
        grouped = defaultdict(list)
        for row in development:
            grouped[tuple(row[field] for field in fields)].append(row["actual_overtake"])
        bins[fields] = {
            key: {
                "samples": len(values),
                "probability": (sum(values) + 2 * global_rate) / (len(values) + 2),
            }
            for key, values in grouped.items()
        }
    probabilities = []
    sources = Counter()
    for row in validation:
        probability, source = global_rate, "global"
        for fields in levels:
            key = tuple(row[field] for field in fields)
            candidate = bins[fields].get(key)
            if candidate and candidate["samples"] >= 5:
                probability = candidate["probability"]
                source = "+".join(fields)
                break
        probabilities.append(probability)
        sources[source] += 1
    labels = [row["actual_overtake"] for row in validation]
    empirical_predictions = [value >= 0.5 for value in probabilities]
    empirical = classification_metrics(empirical_predictions, labels)
    empirical["brier_score"] = mean(
        (probability - actual) ** 2
        for probability, actual in zip(probabilities, labels, strict=True)
    )
    baseline = classification_metrics([row["predicted_crossing"] for row in validation], labels)
    return {
        "model": "smoothed chronological empirical bins",
        "features": ["circuit", "grid_region", "traffic_level", "predicted_crossing"],
        "development_samples": len(development),
        "global_development_overtake_rate": global_rate,
        "fallback_sources": dict(sources),
        "existing_crossing_rule": baseline,
        "empirical_bins": empirical,
        "decision": (
            "candidate"
            if empirical["balanced_accuracy"] > baseline["balanced_accuracy"]
            and empirical["brier_score"] < baseline["brier_score"]
            else "reject_new_overtaking_complexity"
        ),
    }


def main():
    diagnosis = json.loads(
        Path("docs/pit-transition-diagnosis-phase5c.json").read_text(encoding="utf-8")
    )
    development = _candidate_rows(diagnosis["development_rows"])
    validation = _candidate_rows(diagnosis["validation_rows"])
    calibrations, traffic = fit(development)
    development = apply(development, calibrations, traffic)
    validation = apply(validation, calibrations, traffic)
    development_metrics = report_metrics(development)
    validation_metrics = report_metrics(validation)
    ridge = json.loads(Path("docs/pit-transition-ridge-phase5c.json").read_text(encoding="utf-8"))
    ridge_checkpoint = {"rejoin": "+1", "post_stop_lap_2": "+3", "post_stop_lap_4": "+5"}
    validation_metrics["E_selected_ridge"] = {
        checkpoint: {
            "samples": ridge["checkpoints"][label]["ridge"]["n"],
            "mae_seconds": ridge["checkpoints"][label]["ridge"]["mae_seconds"],
            "bias_seconds": ridge["checkpoints"][label]["ridge"]["bias_seconds"],
            "median_absolute_error_seconds": ridge["checkpoints"][label]["ridge"][
                "median_ae_seconds"
            ],
            "p90_absolute_error_seconds": ridge["checkpoints"][label]["ridge"]["p90_ae_seconds"],
        }
        for checkpoint, label in ridge_checkpoint.items()
    }
    selected = min(
        development_metrics,
        key=lambda field: development_metrics[field]["post_stop_lap_4"]["mae_seconds"],
    )
    report = {
        "chronology": "all calibration and component selection use 2023 only",
        "calibration": calibrations,
        "traffic_bins": {
            f"{checkpoint}:{traffic_level}": value
            for (checkpoint, traffic_level), value in traffic.items()
        },
        "development": development_metrics,
        "validation": validation_metrics,
        "selected_from_development": selected,
        "final_selected_model": "E_selected_ridge",
        "ridge_selection": ridge["selection"],
        "overtaking_validation": overtake_metrics(development, validation),
    }
    output = Path("docs/pit-transition-ablation-phase5c.json")
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
