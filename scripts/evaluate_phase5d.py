"""Phase 5D chronological residual-model and reliability-envelope evaluation."""

import argparse
import json
from collections import Counter
from pathlib import Path
from statistics import mean

import joblib
import numpy as np
from evaluate_counterfactuals import (
    attach_chronological_profiles,
    collect_factual_cases,
    grid_region,
    lap_cutoffs,
    load_races,
)
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from f1_pitwall.services.simulation import build_short_horizon_state, simulate_action

TRAIN_RACES = (
    (2021, 1),
    (2021, 4),
    (2021, 5),
    (2021, 14),
    (2022, 1),
    (2022, 6),
    (2022, 7),
    (2022, 16),
    (2022, 18),
    (2023, 1),
    (2023, 6),
    (2023, 7),
    (2023, 14),
)
DEVELOPMENT_RACES = ((2024, 1), (2024, 4), (2024, 8), (2024, 10), (2024, 16))
FINAL_RACES = ((2025, 1), (2025, 4), (2025, 8), (2025, 9), (2025, 16))
HORIZONS = (1, 3, 5)
ARTIFACT = Path(".cache/phase5d-frozen-models.joblib")
SELECTION_ROWS = Path(".cache/phase5d-selection-rows.joblib")
SELECTION_OUTPUT = Path("docs/phase5d-selection.json")
FINAL_OUTPUT = Path("docs/phase5d-final-holdout.json")

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


def numeric(value, fallback=np.nan):
    return float(value) if value is not None else fallback


def green_outcome_window(race, start, end):
    controls = sorted(
        (row for row in race.control if row.track_status is not None and row.at <= end),
        key=lambda row: row.at,
    )
    before = [row for row in controls if row.at <= start]
    relevant = before[-1:] + [row for row in controls if start < row.at <= end]
    return bool(relevant) and all(row.track_status == "1" for row in relevant)


def collect_rows(races, prior_races):
    cases = collect_factual_cases(races)
    attach_chronological_profiles(cases, prior_races)
    rows = []
    for case in cases["PIT_NOW"]:
        context = case["context"]
        _, cutoffs = lap_cutoffs(case["full_race"])
        state = build_short_horizon_state(context, case["driver_id"])
        result = simulate_action(context, case["driver_id"], case["action"])
        current = context.driver(case["driver_id"])
        for outcome in result.outcomes:
            actual_gap, actual_position = case["actual"][outcome.horizon_laps]
            actual_delta = (
                actual_gap - current.gap_to_leader
                if actual_gap is not None and current.gap_to_leader is not None
                else None
            )
            components = outcome.components
            rejoin = components.get("predicted_rejoin") or {}
            pit = components.get("pit_loss_components") or {}
            profile = components.get("circuit_profile") or {}
            nearby = components.get("nearby_car_projection") or []
            row = {
                "year": context.race.event.year,
                "race": case["race"],
                "circuit": context.race.event.circuit.id,
                "lap": case["lap"],
                "driver_id": case["driver_id"],
                "horizon": outcome.horizon_laps,
                "grid_region": grid_region(case["position"]),
                "traffic_level": rejoin.get("traffic") or state.traffic,
                "compound_transition": case.get("compound_transition"),
                "current_compound": state.compound,
                "phase5c_point": outcome.expected_delta_time_seconds,
                "actual_delta": actual_delta,
                "predicted_position": outcome.expected_position,
                "actual_position": actual_position,
                "structural_out_of_domain": state.gap_kind != "TIME",
                "green_outcome_window": green_outcome_window(
                    case["full_race"],
                    context.cutoff,
                    cutoffs.get(case["lap"] + outcome.horizon_laps, context.cutoff),
                ),
                "current_position": state.current_position,
                "race_progress": case["lap"]
                / max(context.state.total_scheduled_laps or case["lap"], 1),
                "relative_pace": state.relative_pace_seconds_per_lap,
                "pace_lap_count": len(state.relative_pace_laps),
                "tyre_age": state.tyre_age,
                "pit_stops_completed": state.pit_stops_completed,
                "pit_loss": pit.get("combined_observed_stop_lap_residual_seconds"),
                "pit_loss_mad": pit.get("residual_mad_seconds"),
                "pit_loss_samples": pit.get("sample_count"),
                "rejoin_position": rejoin.get("position"),
                "rejoin_width": rejoin.get("position_range_width"),
                "nearby_count": len(nearby),
                "unknown_cars": components.get("unknown_same_lap_cars"),
                "uncertain_crossings": sum(
                    bool(item.get("crossing_treated_as_uncertain")) for item in nearby
                ),
                "prior_races": profile.get("prior_races"),
                "prior_pit_samples": profile.get("pit_loss_samples"),
                "position_change_frequency": profile.get("position_change_frequency"),
                "fresh_delta": components.get("raw_fresh_delta_seconds_per_lap"),
                "fresh_samples": components.get("fresh_tyre_sample_count"),
                "fresh_mad": components.get("fresh_tyre_sample_mad_seconds"),
                "gap_kind": state.gap_kind,
            }
            row["traffic_code"] = TRAFFIC_CODES.get(row["traffic_level"], 4)
            row["compound_code"] = COMPOUND_CODES.get(state.compound, 5)
            row["gap_kind_code"] = GAP_CODES[state.gap_kind]
            rows.append(row)
    return rows


def usable(rows, horizon):
    return [
        row
        for row in rows
        if row["horizon"] == horizon
        and row["phase5c_point"] is not None
        and row["actual_delta"] is not None
    ]


def final_usable(rows, horizon):
    return [row for row in usable(rows, horizon) if row["green_outcome_window"]]


def matrix(rows):
    return np.asarray([[numeric(row[name]) for name in FEATURES] for row in rows])


def residuals(rows):
    return np.asarray([row["actual_delta"] - row["phase5c_point"] for row in rows])


def percentile(values, fraction):
    return (
        float(np.quantile(np.asarray(values), fraction, method="higher")) if len(values) else None
    )


def metrics(rows, predictions):
    errors = np.asarray(predictions) - np.asarray([row["actual_delta"] for row in rows])
    absolute = np.abs(errors)
    position_errors = [
        row["predicted_position"] - row["actual_position"]
        for row in rows
        if row["predicted_position"] is not None and row["actual_position"] is not None
    ]
    return {
        "samples": len(rows),
        "mae_seconds": round(float(absolute.mean()), 3) if len(rows) else None,
        "median_ae_seconds": round(float(np.median(absolute)), 3) if len(rows) else None,
        "bias_seconds": round(float(errors.mean()), 3) if len(rows) else None,
        "p90_ae_seconds": round(percentile(absolute, 0.9), 3) if len(rows) else None,
        "position_samples": len(position_errors),
        "position_mae": round(mean(abs(value) for value in position_errors), 3)
        if position_errors
        else None,
    }


def grouped(rows, predictions, field):
    output = {}
    for value in sorted({row[field] for row in rows}, key=str):
        indices = [index for index, row in enumerate(rows) if row[field] == value]
        if len(indices) < 3:
            continue
        output[str(value)] = metrics(
            [rows[index] for index in indices], [predictions[index] for index in indices]
        )
    return output


def ridge_model(alpha):
    return make_pipeline(SimpleImputer(), StandardScaler(), Ridge(alpha=alpha))


def boosted_model(max_leaf_nodes):
    return make_pipeline(
        SimpleImputer(),
        HistGradientBoostingRegressor(
            loss="absolute_error",
            learning_rate=0.05,
            max_iter=100,
            max_leaf_nodes=max_leaf_nodes,
            min_samples_leaf=10,
            l2_regularization=1.0,
            random_state=51,
        ),
    )


def fit_candidates(train, development):
    x_train, y_train = matrix(train), residuals(train)
    x_dev = matrix(development)
    candidates = {
        "A_phase5c_ridge": (None, np.zeros(len(development))),
    }
    for alpha in (1.0, 10.0, 100.0):
        model = ridge_model(alpha).fit(x_train, y_train)
        candidates[f"B_ridge_{alpha:g}"] = (model, model.predict(x_dev))
    for leaves in (7, 15):
        model = boosted_model(leaves).fit(x_train, y_train)
        candidates[f"C_hist_gradient_boosting_{leaves}"] = (model, model.predict(x_dev))
    evaluated = {}
    for name, (_, correction) in candidates.items():
        predictions = np.asarray([row["phase5c_point"] for row in development]) + correction
        evaluated[name] = {
            "overall": metrics(development, predictions),
            "by_circuit": grouped(development, predictions, "circuit"),
            "by_grid_region": grouped(development, predictions, "grid_region"),
            "by_traffic_level": grouped(development, predictions, "traffic_level"),
        }
    baseline = evaluated["A_phase5c_ridge"]["overall"]
    eligible = [
        name
        for name, result in evaluated.items()
        if result["overall"]["mae_seconds"] <= baseline["mae_seconds"] - 0.1
        and result["overall"]["p90_ae_seconds"] <= baseline["p90_ae_seconds"]
        and abs(result["overall"]["bias_seconds"]) <= abs(baseline["bias_seconds"]) + 0.25
    ]
    selected = min(
        eligible,
        key=lambda name: (
            evaluated[name]["overall"]["mae_seconds"]
            + 0.1 * evaluated[name]["overall"]["p90_ae_seconds"],
            0 if name.startswith("B_") else 1,
        ),
        default="A_phase5c_ridge",
    )
    return selected, candidates[selected][0], evaluated


def point_predictions(rows, model):
    correction = model.predict(matrix(rows)) if model is not None else np.zeros(len(rows))
    return np.asarray([row["phase5c_point"] for row in rows]) + correction


def risk_model(train, development, point_model):
    errors = np.abs(
        point_predictions(train, point_model) - np.asarray([r["actual_delta"] for r in train])
    )
    development_errors = np.abs(
        point_predictions(development, point_model)
        - np.asarray([r["actual_delta"] for r in development])
    )
    candidates = {
        "ridge_100": ridge_model(100.0),
        "hist_gradient_boosting_7": boosted_model(7),
        "hist_gradient_boosting_15": boosted_model(15),
    }
    comparison = {}
    for name, model in candidates.items():
        model.fit(matrix(train), np.log1p(errors))
        risks = predicted_risk(development, model)
        order = np.argsort(risks)
        count = max(1, int(len(order) * 0.6))
        accepted, rejected = order[:count], order[count:]
        comparison[name] = {
            "accepted_coverage": round(count / len(order), 3),
            "accepted_mae_seconds": round(float(development_errors[accepted].mean()), 3),
            "accepted_p90_seconds": round(percentile(development_errors[accepted], 0.9), 3),
            "rejected_mae_seconds": round(float(development_errors[rejected].mean()), 3)
            if len(rejected)
            else None,
        }
    selected = min(
        comparison,
        key=lambda name: (
            comparison[name]["accepted_mae_seconds"]
            + 0.1 * comparison[name]["accepted_p90_seconds"],
            0 if name.startswith("ridge") else 1,
        ),
    )
    return selected, candidates[selected], comparison


def predicted_risk(rows, model):
    return np.maximum(0.5, np.expm1(model.predict(matrix(rows))))


def traffic_flags(row):
    return {
        "pack_rejoin": row["nearby_count"] >= 4,
        "multiple_uncertain_crossings": row["uncertain_crossings"] >= 2,
        "unknown_same_lap_cars": (row["unknown_cars"] or 0) > 0,
        "wide_rejoin_range": (row["rejoin_width"] or 0) >= 4,
        "heavy_or_unknown_traffic": row["traffic_level"] in {"HEAVY_TRAFFIC", "UNKNOWN"},
    }


def select_traffic_gates(rows, errors):
    overall_p90 = percentile(np.abs(errors), 0.9)
    selected, evidence = [], {}
    for name in traffic_flags(rows[0]):
        indices = [index for index, row in enumerate(rows) if traffic_flags(row)[name]]
        values = [abs(errors[index]) for index in indices]
        evidence[name] = {
            "samples": len(values),
            "mae_seconds": round(mean(values), 3) if values else None,
            "p90_ae_seconds": round(percentile(values, 0.9), 3) if values else None,
        }
        if len(values) >= 5 and percentile(values, 0.9) > 1.25 * overall_p90:
            selected.append(name)
    return selected, evidence


def applicability(row, risk, thresholds, gates):
    if row["structural_out_of_domain"] or any(traffic_flags(row)[name] for name in gates):
        return "OUT_OF_DOMAIN"
    if risk <= thresholds[0]:
        return "RELIABLE"
    if risk <= thresholds[1]:
        return "USABLE"
    if risk <= thresholds[2]:
        return "WEAK"
    return "OUT_OF_DOMAIN"


def interval_report(rows, predictions, risks, frozen):
    labels = [
        applicability(row, risk, frozen["risk_thresholds"], frozen["traffic_gates"])
        for row, risk in zip(rows, risks, strict=True)
    ]
    errors = np.abs(predictions - np.asarray([row["actual_delta"] for row in rows]))
    widths80 = risks * frozen["normalized_q80"]
    widths90 = risks * frozen["normalized_q90"]
    output = {
        "coverage_80": round(float(np.mean(errors <= widths80)), 3),
        "coverage_90": round(float(np.mean(errors <= widths90)), 3),
        "mean_half_width_80": round(float(np.mean(widths80)), 3),
        "mean_half_width_90": round(float(np.mean(widths90)), 3),
        "by_applicability": {},
    }
    for label in ("RELIABLE", "USABLE", "WEAK", "OUT_OF_DOMAIN"):
        indices = [index for index, value in enumerate(labels) if value == label]
        subset = [rows[index] for index in indices]
        estimates = [predictions[index] for index in indices]
        result = metrics(subset, estimates) if subset else metrics([], [])
        result["coverage_fraction"] = round(len(indices) / len(rows), 3) if rows else 0
        result["interval_80_coverage"] = (
            round(mean(bool(errors[index] <= widths80[index]) for index in indices), 3)
            if indices
            else None
        )
        result["interval_90_coverage"] = (
            round(mean(bool(errors[index] <= widths90[index]) for index in indices), 3)
            if indices
            else None
        )
        output["by_applicability"][label] = result
    accepted = [index for index, label in enumerate(labels) if label in {"RELIABLE", "USABLE"}]
    output["reliable_plus_usable"] = {
        **metrics(
            [rows[index] for index in accepted],
            [predictions[index] for index in accepted],
        ),
        "coverage_fraction": round(len(accepted) / len(rows), 3) if rows else 0,
    }
    return output, labels, widths80, widths90


def dataset_counts(rows):
    transitions = {}
    for row in rows:
        transitions.setdefault((row["race"], row["lap"], row["driver_id"]), row)
    values = list(transitions.values())
    dimensions = {}
    for field in ("year", "circuit", "grid_region", "traffic_level", "compound_transition"):
        dimensions[field] = dict(Counter(str(row[field]) for row in values))
    return {
        "transitions": len(values),
        "timed_samples_by_horizon": {
            str(horizon): len(usable(rows, horizon)) for horizon in HORIZONS
        },
        "valid_green_timed_samples_by_horizon": {
            str(horizon): len(final_usable(rows, horizon)) for horizon in HORIZONS
        },
        **dimensions,
    }


def leave_circuit_out(rows, selected_name):
    result = {}
    for circuit in sorted({row["circuit"] for row in rows}):
        train = [row for row in rows if row["circuit"] != circuit]
        test = [row for row in rows if row["circuit"] == circuit]
        if len(train) < 20 or len(test) < 3:
            continue
        if selected_name == "A_phase5c_ridge":
            model = None
        elif selected_name.startswith("B_"):
            model = ridge_model(float(selected_name.rsplit("_", 1)[1])).fit(
                matrix(train), residuals(train)
            )
        else:
            model = boosted_model(int(selected_name.rsplit("_", 1)[1])).fit(
                matrix(train), residuals(train)
            )
        result[circuit] = metrics(test, point_predictions(test, model))
    return result


def grouped_weighted_mae(values):
    samples = sum(row["samples"] for row in values.values())
    return (
        sum(row["samples"] * row["mae_seconds"] for row in values.values()) / samples
        if samples
        else None
    )


def selection_run():
    if SELECTION_ROWS.exists():
        cached = joblib.load(SELECTION_ROWS)
        train_rows = cached["training"]
        development_rows = cached["development"]
    else:
        train_races = load_races(TRAIN_RACES)
        development_races = load_races(DEVELOPMENT_RACES)
        train_rows = collect_rows(train_races, train_races)
        development_rows = collect_rows(development_races, train_races + development_races)
        joblib.dump({"training": train_rows, "development": development_rows}, SELECTION_ROWS)
    frozen, report = (
        {},
        {
            "chronology": "2021-2023 train; 2024 selection and interval calibration; 2025 unopened",
            "features": list(FEATURES),
            "forbidden_features": [
                "future compound",
                "actual stop time",
                "actual rejoin",
                "future traffic",
                "future position",
                "future lap time",
            ],
            "dataset": {
                "training": dataset_counts(train_rows),
                "development": dataset_counts(development_rows),
            },
            "horizons": {},
        },
    )
    models = {}
    for horizon in HORIZONS:
        train = usable(train_rows, horizon)
        development = usable(development_rows, horizon)
        selected, model, candidates = fit_candidates(train, development)
        baseline_lco = leave_circuit_out(train, "A_phase5c_ridge")
        candidate_lco = leave_circuit_out(train, selected)
        circuit_gate = {
            "baseline_weighted_mae_seconds": grouped_weighted_mae(baseline_lco),
            "candidate_weighted_mae_seconds": grouped_weighted_mae(candidate_lco),
            "candidate_before_gate": selected,
        }
        if (
            selected != "A_phase5c_ridge"
            and circuit_gate["candidate_weighted_mae_seconds"]
            > circuit_gate["baseline_weighted_mae_seconds"]
        ):
            selected, model = "A_phase5c_ridge", None
            circuit_gate["decision"] = "reject_candidate_for_unseen_circuit_regression"
        else:
            circuit_gate["decision"] = "pass"
        predictions = point_predictions(development, model)
        risk_name, risk, risk_comparison = risk_model(train, development, model)
        risks = predicted_risk(development, risk)
        errors = predictions - np.asarray([row["actual_delta"] for row in development])
        gates, gate_evidence = select_traffic_gates(development, errors)
        thresholds = [float(np.quantile(risks, q)) for q in (0.25, 0.6, 0.85)]
        normalized = np.abs(errors) / risks
        horizon_frozen = {
            "selected_model": selected,
            "risk_thresholds": thresholds,
            "traffic_gates": gates,
            "normalized_q80": percentile(normalized, 0.8),
            "normalized_q90": percentile(normalized, 0.9),
        }
        intervals, _, _, _ = interval_report(development, predictions, risks, horizon_frozen)
        frozen[horizon] = horizon_frozen
        models[horizon] = {"point": model, "risk": risk}
        report["horizons"][str(horizon)] = {
            "frozen": horizon_frozen,
            "risk_model": risk_name,
            "risk_model_comparison": risk_comparison,
            "candidate_comparison": candidates,
            "circuit_generalization_gate": circuit_gate,
            "selected_development": metrics(development, predictions),
            "traffic_gate_evidence": gate_evidence,
            "interval_calibration": intervals,
            "leave_circuit_out_training": leave_circuit_out(train, selected),
        }
    ARTIFACT.parent.mkdir(exist_ok=True)
    joblib.dump({"frozen": frozen, "models": models}, ARTIFACT)
    SELECTION_OUTPUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(SELECTION_OUTPUT), "selected": frozen}, indent=2))


def final_run():
    artifact = joblib.load(ARTIFACT)
    final_races = load_races(FINAL_RACES)
    prior_races = load_races(TRAIN_RACES + DEVELOPMENT_RACES)
    rows = collect_rows(final_races, prior_races + final_races)
    report = {
        "chronology": "Frozen selection artifact loaded before 2025 labels; no refitting",
        "dataset": dataset_counts(rows),
        "horizons": {},
    }
    for horizon in HORIZONS:
        selected = final_usable(rows, horizon)
        models = artifact["models"][horizon]
        frozen = artifact["frozen"][horizon]
        predictions = point_predictions(selected, models["point"])
        risks = predicted_risk(selected, models["risk"])
        intervals, labels, widths80, widths90 = interval_report(
            selected, predictions, risks, frozen
        )
        report["horizons"][str(horizon)] = {
            "selected_model": frozen["selected_model"],
            "overall": metrics(selected, predictions),
            "intervals": intervals,
            "by_circuit": grouped(selected, predictions, "circuit"),
            "by_grid_region": grouped(selected, predictions, "grid_region"),
            "by_traffic_level": grouped(selected, predictions, "traffic_level"),
            "examples": [
                {
                    "race": row["race"],
                    "lap": row["lap"],
                    "driver_id": row["driver_id"],
                    "point": round(float(point), 3),
                    "actual": row["actual_delta"],
                    "interval_80": [round(float(point - w80), 3), round(float(point + w80), 3)],
                    "interval_90": [round(float(point - w90), 3), round(float(point + w90), 3)],
                    "applicability": label,
                }
                for row, point, w80, w90, label in list(
                    zip(selected, predictions, widths80, widths90, labels, strict=True)
                )[:20]
            ],
        }
    FINAL_OUTPUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(FINAL_OUTPUT), "horizons": report["horizons"]}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("selection", "final"))
    args = parser.parse_args()
    selection_run() if args.mode == "selection" else final_run()
