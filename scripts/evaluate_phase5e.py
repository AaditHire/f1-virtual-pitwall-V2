"""Chronological evaluation of the Phase 5E one-lap probabilistic kernel."""

import argparse
import json
import zlib
from collections import Counter, defaultdict
from math import ceil
from pathlib import Path
from statistics import mean

import joblib
from evaluate_counterfactuals import (
    attach_chronological_profiles,
    collect_factual_cases,
    grid_region,
    lap_cutoffs,
    load_races,
)
from evaluate_phase5d import DEVELOPMENT_RACES, FINAL_RACES, TRAIN_RACES, green_outcome_window

from f1_pitwall.services.simulation import simulate_action
from f1_pitwall.services.transition_kernel import (
    _artifact,
    _deterministic_curve,
    _phase_for_lap,
    rollout_action,
)

HORIZONS = (1, 3, 5)
ARTIFACT = Path("src/f1_pitwall/models/phase5e_transition_kernel.joblib")
SELECTION_OUTPUT = Path("docs/phase5e-selection.json")
FINAL_OUTPUT = Path("docs/phase5e-final-holdout.json")
REPORT_OUTPUT = Path("docs/phase5e-transition-kernel.md")


def quantile(values, fraction):
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def interval_contains(interval, actual):
    return interval is not None and interval[0] <= actual <= interval[1]


def upper_quantile(values, fraction):
    ordered = sorted(values)
    return ordered[ceil(fraction * len(ordered)) - 1]


def collect_records(races, prior_races):
    cases = collect_factual_cases(races)
    attach_chronological_profiles(cases, prior_races)
    records = []
    for kind, selected in cases.items():
        for case in selected:
            context = case["context"]
            direct = simulate_action(context, case["driver_id"], case["action"])
            current = context.driver(case["driver_id"])
            deterministic = _deterministic_curve(direct, current.gap_to_leader)
            _, cutoffs = lap_cutoffs(case.get("full_race", context.race))
            outcomes = {}
            for predicted in direct.outcomes:
                actual_gap, actual_position = case["actual"][predicted.horizon_laps]
                actual_delta = (
                    actual_gap - current.gap_to_leader
                    if actual_gap is not None and current.gap_to_leader is not None
                    else None
                )
                end = cutoffs.get(case["lap"] + predicted.horizon_laps, context.cutoff)
                outcomes[predicted.horizon_laps] = {
                    "actual_delta": actual_delta,
                    "actual_position": actual_position,
                    "direct_delta": predicted.expected_delta_time_seconds,
                    "deterministic_delta": deterministic[predicted.horizon_laps - 1]
                    if deterministic
                    else None,
                    "direct_position": predicted.expected_position,
                    "direct_interval_50": None,
                    "direct_interval_80": predicted.prediction_interval_80,
                    "direct_interval_90": predicted.prediction_interval_90,
                    "applicability": predicted.applicability or "USABLE",
                    "green": green_outcome_window(context.race, context.cutoff, end),
                }
            records.append(
                {
                    "race": case["race"],
                    "year": context.race.event.year,
                    "circuit": context.race.event.circuit.id,
                    "lap": case["lap"],
                    "driver_id": case["driver_id"],
                    "kind": kind,
                    "action": case["action"],
                    "grid_region": grid_region(case["position"]),
                    "gap_kind": "LAP_DEFICIT" if current.lapped else (
                        "TIME" if current.gap_to_leader is not None else "UNKNOWN"
                    ),
                    "context": context,
                    "outcomes": outcomes,
                }
            )
    return records


def residual_curve(record):
    residuals = {}
    for horizon in HORIZONS:
        row = record["outcomes"][horizon]
        if (
            row["actual_delta"] is None
            or row["deterministic_delta"] is None
            or not row["green"]
        ):
            return None
        residuals[horizon] = row["actual_delta"] - row["deterministic_delta"]
    cumulative = [
        residuals[1],
        residuals[1] + (residuals[3] - residuals[1]) / 2,
        residuals[3],
        residuals[3] + (residuals[5] - residuals[3]) / 2,
        residuals[5],
    ]
    return [cumulative[0]] + [cumulative[index] - cumulative[index - 1] for index in range(1, 5)]


def fit_artifact(records):
    pools = {kind: defaultdict(list) for kind in ("PIT_NOW", "EXTEND")}
    latent = {kind: [] for kind in ("PIT_NOW", "EXTEND")}
    sample_counts = Counter()
    for record in records:
        curve = residual_curve(record)
        if curve is None:
            continue
        kind = record["kind"]
        latent[kind].append(mean(curve))
        extension = int(record["action"].removeprefix("EXTEND_")) if kind == "EXTEND" else 0
        for lap_index, value in enumerate(curve, 1):
            phase = _phase_for_lap(kind, extension, lap_index)
            applicability = record["outcomes"][1 if lap_index == 1 else 3 if lap_index <= 3 else 5][
                "applicability"
            ]
            pools[kind][phase].append(value)
            pools[kind][f"{phase}:{applicability}"].append(value)
            pools[kind]["DEFAULT"].append(value)
            sample_counts[f"{kind}:{phase}"] += 1
    return {
        "selected_error_model": "PERSISTENT",
        "persistent_weight": 0.65,
        "residual_pools": {
            kind: {name: values for name, values in values_by_phase.items()}
            for kind, values_by_phase in pools.items()
        },
        "latent_pools": latent,
        "sample_counts": dict(sample_counts),
        "source": "2021-2023 chronological factual one-lap residuals",
        "method": (
            "Empirical action/phase pools with reliability shrinkage; groups below 12 "
            "observations fall back to their action-phase pool."
        ),
    }


def score_records(records, error_model, trajectories=100):
    rows = []
    for record in records:
        seed = zlib.crc32(
            f"{record['race']}:{record['lap']}:{record['driver_id']}:{record['action']}".encode()
        )
        rollout = rollout_action(
            record["context"],
            record["driver_id"],
            record["action"],
            trajectories,
            seed,
            error_model,
        )
        for outcome in rollout.outcomes:
            factual = record["outcomes"][outcome.horizon_laps]
            if not factual["green"]:
                continue
            rows.append(
                {
                    "race": record["race"],
                    "circuit": record["circuit"],
                    "driver_id": record["driver_id"],
                    "kind": record["kind"],
                    "grid_region": record["grid_region"],
                    "gap_kind": record["gap_kind"],
                    "horizon": outcome.horizon_laps,
                    "actual": factual["actual_delta"],
                    "actual_position": factual["actual_position"],
                    "direct": factual["direct_delta"],
                    "deterministic": factual["deterministic_delta"],
                    "direct_position": factual["direct_position"],
                    "direct_interval_80": factual["direct_interval_80"],
                    "direct_interval_90": factual["direct_interval_90"],
                    "rollout": outcome.median_relative_delta_seconds,
                    "interval_50": outcome.interval_50,
                    "interval_80": outcome.interval_80,
                    "interval_90": outcome.interval_90,
                    "position": outcome.median_position,
                    "position_range_50": outcome.position_range_50,
                    "position_range_80": outcome.position_range_80,
                    "position_range_90": outcome.position_range_90,
                }
            )
    return rows


def metrics(rows, prediction="rollout"):
    timed = [row for row in rows if row["actual"] is not None and row[prediction] is not None]
    errors = [row[prediction] - row["actual"] for row in timed]
    positioned = [
        row
        for row in rows
        if row["actual_position"] is not None and row.get("position") is not None
    ]
    result = {
        "samples": len(rows),
        "timed_samples": len(timed),
        "mae_seconds": mean(abs(value) for value in errors) if errors else None,
        "bias_seconds": mean(errors) if errors else None,
        "p90_absolute_error_seconds": upper_quantile(
            [abs(value) for value in errors], 0.9
        )
        if errors
        else None,
        "position_samples": len(positioned),
        "position_mae": mean(
            abs(row["position"] - row["actual_position"]) for row in positioned
        )
        if positioned
        else None,
    }
    if prediction == "rollout":
        for level in (50, 80, 90):
            key = f"interval_{level}"
            eligible = [row for row in timed if row[key] is not None]
            result[f"coverage_{level}"] = mean(
                interval_contains(row[key], row["actual"]) for row in eligible
            ) if eligible else None
            result[f"mean_width_{level}_seconds"] = mean(
                row[key][1] - row[key][0] for row in eligible
            ) if eligible else None
            pkey = f"position_range_{level}"
            pos_eligible = [
                row for row in positioned if row[pkey] is not None
            ]
            result[f"position_coverage_{level}"] = mean(
                interval_contains(row[pkey], row["actual_position"]) for row in pos_eligible
            ) if pos_eligible else None
    return result


def report_rows(rows):
    report = {"overall": {}, "by_action": {}, "by_grid_region": {}, "by_circuit": {}}
    for horizon in HORIZONS:
        selected = [row for row in rows if row["horizon"] == horizon]
        report["overall"][str(horizon)] = {
            "direct_phase5d": metrics(selected, "direct"),
            "deterministic_rollout": metrics(selected, "deterministic"),
            "probabilistic_rollout": metrics(selected),
        }
    for dimension, target in (
        ("kind", "by_action"), ("grid_region", "by_grid_region"), ("circuit", "by_circuit")
    ):
        for value in sorted({row[dimension] for row in rows}):
            report[target][value] = {
                str(horizon): {
                    "direct_phase5d": metrics(selected, "direct"),
                    "deterministic_rollout": metrics(selected, "deterministic"),
                    "probabilistic_rollout": metrics(selected),
                }
                for horizon in HORIZONS
                for selected in (
                    [
                        row
                        for row in rows
                        if row[dimension] == value and row["horizon"] == horizon
                    ],
                )
            }
    return report


def model_score(report):
    values = []
    for horizon in HORIZONS:
        row = report["overall"][str(horizon)]["probabilistic_rollout"]
        if row["timed_samples"]:
            calibration_error = sum(
                abs(row[f"coverage_{level}"] - level / 100) for level in (50, 80, 90)
            )
            values.append(calibration_error + row["mae_seconds"] / 20)
    return mean(values)


def selection_run():
    training_races = load_races(TRAIN_RACES)
    development_races = load_races(DEVELOPMENT_RACES)
    training = collect_records(training_races, training_races)
    artifact = fit_artifact(training)
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, ARTIFACT)
    _artifact.cache_clear()
    development = collect_records(development_races, training_races + development_races)
    comparisons = {}
    for model in ("INDEPENDENT", "PERSISTENT"):
        rows = score_records(development, model)
        comparisons[model] = report_rows(rows)
    scores = {name: model_score(value) for name, value in comparisons.items()}
    artifact["selected_error_model"] = min(scores, key=scores.get)
    joblib.dump(artifact, ARTIFACT)
    _artifact.cache_clear()
    result = {
        "chronology": "2021-2023 residual fitting; 2024 error-correlation selection",
        "training_records": len(training),
        "development_records": len(development),
        "artifact_sample_counts": artifact["sample_counts"],
        "model_scores": scores,
        "selected_error_model": artifact["selected_error_model"],
        "comparisons": comparisons,
    }
    SELECTION_OUTPUT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(SELECTION_OUTPUT), "scores": scores}, indent=2))


def final_run():
    if not ARTIFACT.exists():
        raise RuntimeError("Run selection before final holdout")
    artifact = joblib.load(ARTIFACT)
    _artifact.cache_clear()
    prior = load_races(TRAIN_RACES + DEVELOPMENT_RACES)
    final_races = load_races(FINAL_RACES)
    records = collect_records(final_races, prior + final_races)
    rows = score_records(records, artifact["selected_error_model"])
    report = {
        "chronology": "Frozen Phase 5E artifact loaded before 2025 outcomes; no refitting",
        "selected_error_model": artifact["selected_error_model"],
        "records": len(records),
        **report_rows(rows),
    }
    FINAL_OUTPUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    REPORT_OUTPUT.write_text(
        "# Phase 5E one-lap transition kernel\n\n"
        "The production kernel combines the frozen Phase 5C deterministic transition with "
        "empirical one-lap residual sampling. Model selection uses 2021–2023 for fitting and "
        "2024 for choosing independent or persistent errors. The 2025 report is a frozen final "
        "holdout.\n\n"
        f"Selected error model: **{artifact['selected_error_model']}**.\n\n"
        "Machine-readable results: `phase5e-selection.json` and "
        "`phase5e-final-holdout.json`.\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(FINAL_OUTPUT), "overall": report["overall"]}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("selection", "final"))
    args = parser.parse_args()
    selection_run() if args.mode == "selection" else final_run()
