"""Chronological, causal ridge benchmark for the Phase 5C transition ceiling."""

import json
from pathlib import Path

import numpy as np

SOURCE = Path("docs/pit-transition-diagnosis-phase5c.json")
OUTPUT = Path("docs/pit-transition-ridge-phase5c.json")
CHECKPOINTS = {"rejoin": 1, "post_stop_lap_2": 3, "post_stop_lap_4": 5}
ALPHAS = (0.1, 1.0, 10.0, 100.0)


def features(row):
    horizon = CHECKPOINTS[row["checkpoint"]]
    return [
        row["predicted_relative_delta_raw"],
        row["baseline_relative_pace"] * horizon,
        row["predicted_pit_loss"],
        row["warm_up_gain"],
        row["traffic_loss"],
        row["starting_position"] / 10,
    ]


def fit(rows, alpha):
    x = np.asarray([features(row) for row in rows], dtype=float)
    y = np.asarray([row["actual_relative_delta"] for row in rows], dtype=float)
    mean, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale == 0] = 1
    z = (x - mean) / scale
    design = np.column_stack((np.ones(len(z)), z))
    penalty = np.eye(design.shape[1]) * alpha
    penalty[0, 0] = 0
    weights = np.linalg.solve(design.T @ design + penalty, design.T @ y)
    return mean, scale, weights


def predict(model, rows):
    mean, scale, weights = model
    x = np.asarray([features(row) for row in rows], dtype=float)
    return np.column_stack((np.ones(len(x)), (x - mean) / scale)) @ weights


def metrics(rows, estimates):
    errors = np.asarray(estimates) - np.asarray(
        [row["actual_relative_delta"] for row in rows], dtype=float
    )
    absolute = np.abs(errors)
    return {
        "n": len(rows),
        "mae_seconds": round(float(absolute.mean()), 3),
        "median_ae_seconds": round(float(np.median(absolute)), 3),
        "bias_seconds": round(float(errors.mean()), 3),
        "p90_ae_seconds": round(float(np.percentile(absolute, 90)), 3),
    }


def grouped_metrics(rows, ridge_estimates, baseline_estimates, field):
    result = {}
    for value in sorted({row[field] for row in rows}):
        indices = [index for index, row in enumerate(rows) if row[field] == value]
        if len(indices) < 3:
            continue
        subset = [rows[index] for index in indices]
        result[str(value)] = {
            "phase5b": metrics(subset, [baseline_estimates[index] for index in indices]),
            "ridge": metrics(subset, [ridge_estimates[index] for index in indices]),
        }
    return result


def worst_cases(rows, estimates, limit=20):
    selected = []
    for row, estimate in zip(rows, estimates, strict=True):
        item = {
            key: row[key]
            for key in (
                "race",
                "circuit",
                "lap",
                "driver_id",
                "starting_position",
                "grid_region",
                "compound_transition",
                "traffic_level",
                "decision_to_entry_seconds",
                "predicted_pit_loss",
                "actual_pit_loss",
                "predicted_position",
                "actual_position",
            )
        }
        item["predicted_relative_delta"] = round(float(estimate), 3)
        item["actual_relative_delta"] = row["actual_relative_delta"]
        item["error_seconds"] = round(float(estimate - row["actual_relative_delta"]), 3)
        cause_flags = {
            "bad_source_timing": False,
            "red_flag_or_safety_car_contamination": False,
            "unusual_or_mispredicted_pit_stop": (
                row["actual_pit_loss"] is not None
                and abs(row["predicted_pit_loss"] - row["actual_pit_loss"]) > 4
            ),
            "traffic_interaction": row["traffic_level"]
            in {"MODERATE_TRAFFIC", "HEAVY_TRAFFIC", "UNKNOWN"},
            "backmarker_or_lapped_interaction": row["grid_region"] == "P16+",
            "wrong_compound_inference": row["compound_transition"] is None,
            "multi_lap_pace_failure": abs(item["error_seconds"]) > 5,
            "overtaking_failure": (
                row["predicted_position"] is not None
                and row["actual_position"] is not None
                and abs(row["predicted_position"] - row["actual_position"]) > 1
            ),
        }
        causes = [name for name, applies in cause_flags.items() if applies]
        if row["decision_to_entry_seconds"] > 60:
            causes.append("decision_snapshot_too_early")
        if row["actual_pit_loss"] is None:
            causes.append("unqualified_actual_stop")
        item["cause_flags"] = cause_flags
        item["classified_causes"] = causes or ["unresolved"]
        selected.append(item)
    return sorted(selected, key=lambda row: abs(row["error_seconds"]), reverse=True)[:limit]


def main():
    source = json.loads(SOURCE.read_text())
    result = {
        "model": "ridge regression benchmark; not a production dependency",
        "features_available_at_lap_n": [
            "phase5b raw delta",
            "recent relative pace times horizon",
            "causal pit-loss estimate",
            "causal warm-up gain",
            "causal traffic loss",
            "starting position",
        ],
        "selection": (
            "Train 2023 rounds 1/6, select alpha on rounds 7/15; refit all 2023; "
            "evaluate 2024 once."
        ),
        "checkpoints": {},
    }
    for checkpoint, horizon in CHECKPOINTS.items():
        development = [
            r
            for r in source["development_rows"]
            if r["checkpoint"] == checkpoint
            and r["actual_relative_delta"] is not None
            and r["predicted_relative_delta_raw"] is not None
        ]
        validation = [
            r
            for r in source["validation_rows"]
            if r["checkpoint"] == checkpoint
            and r["actual_relative_delta"] is not None
            and r["predicted_relative_delta_raw"] is not None
        ]
        train = [r for r in development if int(r["race"].split("/")[1]) in {1, 6}]
        tune = [r for r in development if int(r["race"].split("/")[1]) in {7, 15}]
        candidates = {}
        for alpha in ALPHAS:
            model = fit(train, alpha)
            candidates[str(alpha)] = metrics(tune, predict(model, tune))
        selected = min(ALPHAS, key=lambda alpha: candidates[str(alpha)]["mae_seconds"])
        model = fit(development, selected)
        ridge_estimates = predict(model, validation)
        baseline_estimates = [r["predicted_relative_delta"] for r in validation]
        ridge = metrics(validation, ridge_estimates)
        baseline = metrics(validation, baseline_estimates)
        mean, scale, weights = model
        result["checkpoints"][f"+{horizon}"] = {
            "selected_alpha": selected,
            "development_train_n": len(train),
            "development_tune_n": len(tune),
            "tuning": candidates,
            "phase5b": baseline,
            "ridge": ridge,
            "mae_change_seconds": round(ridge["mae_seconds"] - baseline["mae_seconds"], 3),
            "fitted_parameters": {
                "feature_order": [
                    "raw_delta",
                    "relative_pace_times_horizon",
                    "pit_loss",
                    "warm_up_gain",
                    "traffic_loss",
                    "starting_position_div_10",
                ],
                "mean": mean.tolist(),
                "scale": scale.tolist(),
                "weights_with_intercept_first": weights.tolist(),
            },
            "by_circuit": grouped_metrics(
                validation, ridge_estimates, baseline_estimates, "circuit"
            ),
            "by_grid_region": grouped_metrics(
                validation, ridge_estimates, baseline_estimates, "grid_region"
            ),
            "by_traffic_level": grouped_metrics(
                validation, ridge_estimates, baseline_estimates, "traffic_level"
            ),
            "worst_cases": worst_cases(validation, ridge_estimates)
            if horizon == 5
            else [],
        }
    result["decision"] = (
        "reject"
        if any(
            row["ridge"]["mae_seconds"] >= row["phase5b"]["mae_seconds"]
            for row in result["checkpoints"].values()
        )
        else "candidate"
    )
    OUTPUT.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
