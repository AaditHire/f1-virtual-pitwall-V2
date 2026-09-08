"""Calibrate and stress-test the frozen Phase 5E transition kernel."""

import json
import random
import zlib
from collections import Counter, defaultdict
from copy import deepcopy
from math import ceil
from pathlib import Path
from statistics import mean, median

import joblib
from evaluate_counterfactuals import timing_values_at
from evaluate_phase5d import DEVELOPMENT_RACES, FINAL_RACES, TRAIN_RACES, green_outcome_window
from evaluate_phase5e import (
    ARTIFACT,
    collect_records,
    fit_artifact,
    interval_contains,
    load_races,
    quantile,
    score_records,
)

from f1_pitwall.services.analysis_context import AnalysisContext
from f1_pitwall.services.pace import field_reference
from f1_pitwall.services.transition_kernel import (
    _artifact,
    _deterministic_curve,
    _sample_residual,
)

OUTPUT = Path("docs/phase5f-calibration-and-stress.json")
REPORT = Path("docs/phase5f-calibration-and-stress.md")
LEVELS = (50, 80, 90)
HORIZONS = (1, 3, 5)
MIN_GROUP = 20


def cached_records(name, races, prior_races):
    path = Path(f".cache/phase5f-v2-{name}-records.joblib")
    if path.exists():
        return joblib.load(path)
    legacy = Path(f".cache/phase5f-{name}-records.joblib")
    if legacy.exists():
        records = joblib.load(legacy)
        race_map = {f"{race.event.year}/{race.event.round}": race for race in races}
        for record in records:
            record["full_race"] = race_map[record["race"]]
    else:
        records = collect_records(races, prior_races)
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(records, path)
    return records


def group_key(row, candidate, level):
    base = [str(row["horizon"])]
    if candidate in {"action", "action_reliability", "action_phase", "hierarchical"}:
        base.append(row["kind"])
    if candidate in {"action_reliability", "hierarchical"}:
        base.append(row["applicability"])
    if candidate == "action_phase":
        base.append(row["phase"])
    return ":".join(base + [str(level)])


def calibration_values(rows, candidate, position=False):
    grouped = defaultdict(list)
    for row in rows:
        actual = row["actual_position"] if position else row["actual"]
        if actual is None:
            continue
        for level in LEVELS:
            interval = row[f"position_range_{level}" if position else f"interval_{level}"]
            if interval is None:
                continue
            if position:
                miss = max(interval[0] - actual, actual - interval[1], 0)
                value = miss
            else:
                half_width = max(row["rollout"] - interval[0], interval[1] - row["rollout"], 0.05)
                value = abs(row["actual"] - row["rollout"]) / half_width
            grouped[group_key(row, candidate, level)].append(value)
    return grouped


def fit_calibration(rows, candidate):
    time_values = calibration_values(rows, candidate)
    position_values = calibration_values(rows, candidate, position=True)
    time_factors, position_padding, counts = {}, {}, {}
    for key, values in time_values.items():
        level = int(key.rsplit(":", 1)[1])
        time_factors[key] = quantile(values, level / 100)
        counts[key] = len(values)
    for key, values in position_values.items():
        level = int(key.rsplit(":", 1)[1])
        position_padding[key] = ceil(quantile(values, level / 100))
    return time_factors, position_padding, counts


def lookup(mapping, row, candidate, level, fallback_candidate="action"):
    key = group_key(row, candidate, level)
    if key in mapping:
        return mapping[key]
    fallback = group_key(row, fallback_candidate, level)
    return mapping.get(fallback, 1 if "padding" not in fallback_candidate else 0)


def apply_candidate(rows, candidate, factors, padding, counts):
    output = []
    for source in rows:
        row = deepcopy(source)
        for level in LEVELS:
            key = group_key(row, candidate, level)
            chosen = candidate
            if candidate == "hierarchical" and counts.get(key, 0) < MIN_GROUP:
                chosen = "action"
            factor = lookup(factors, row, chosen, level)
            interval = row[f"interval_{level}"]
            if interval is not None:
                center = row["rollout"]
                row[f"interval_{level}"] = (
                    center + (interval[0] - center) * factor,
                    center + (interval[1] - center) * factor,
                )
            pad = int(lookup(padding, row, chosen, level))
            pkey = f"position_range_{level}"
            if row[pkey] is not None:
                row[pkey] = (max(1, row[pkey][0] - pad), row[pkey][1] + pad)
        output.append(row)
    return output


def calibration_metrics(rows):
    result = {}
    for horizon in HORIZONS:
        selected = [row for row in rows if row["horizon"] == horizon and row["actual"] is not None]
        metrics = {"samples": len(selected)}
        for level in LEVELS:
            intervals = [row[f"interval_{level}"] for row in selected]
            widths = [value[1] - value[0] for value in intervals]
            metrics[f"coverage_{level}"] = (
                mean(
                    interval_contains(value, row["actual"])
                    for value, row in zip(intervals, selected, strict=True)
                )
                if selected
                else None
            )
            metrics[f"mean_width_{level}"] = mean(widths) if widths else None
            metrics[f"median_width_{level}"] = median(widths) if widths else None
        result[str(horizon)] = metrics
    return result


def calibration_score(report):
    terms = []
    for row in report.values():
        if row["samples"]:
            error = sum(abs(row[f"coverage_{level}"] - level / 100) for level in LEVELS)
            terms.append(error + row["mean_width_80"] / 100)
    return mean(terms)


def performance(rows):
    output = calibration_metrics(rows)
    for horizon in HORIZONS:
        selected = [
            row
            for row in rows
            if row["horizon"] == horizon and row["actual"] is not None
        ]
        errors = [row["rollout"] - row["actual"] for row in selected]
        output[str(horizon)].update(
            {
                "mae": mean(abs(value) for value in errors) if errors else None,
                "bias": mean(errors) if errors else None,
                "p90": quantile([abs(value) for value in errors], 0.9)
                if errors
                else None,
            }
        )
    return output


def position_report(rows):
    output = {}
    for horizon in HORIZONS:
        selected = [
            row
            for row in rows
            if row["horizon"] == horizon
            and row["actual_position"] is not None
            and row["position"] is not None
        ]
        errors = [abs(row["position"] - row["actual_position"]) for row in selected]
        result = {
            "samples": len(selected),
            "mean_absolute_error": mean(errors) if errors else None,
            "median_absolute_error": median(errors) if errors else None,
        }
        for level in (80, 90):
            intervals = [row[f"position_range_{level}"] for row in selected]
            result[f"coverage_{level}"] = mean(
                interval_contains(interval, row["actual_position"])
                for interval, row in zip(intervals, selected, strict=True)
            ) if selected else None
            result[f"mean_width_{level}"] = mean(
                interval[1] - interval[0] for interval in intervals
            ) if intervals else None
        output[str(horizon)] = result
    return output


def artifact_calibration(rows, candidate):
    all_factors, all_padding, all_counts = {}, {}, {}
    # Always retain action fallback keys, then overlay eligible conditional groups.
    for name in ("action", candidate):
        factors, padding, counts = fit_calibration(rows, name)
        all_factors.update(factors)
        all_padding.update(padding)
        all_counts.update(counts)
    time_output, position_output = {}, {}
    for row in rows:
        for level in LEVELS:
            chosen = candidate
            key = group_key(row, chosen, level)
            if candidate == "hierarchical" and all_counts.get(key, 0) < MIN_GROUP:
                chosen = "action"
            source_key = group_key(row, chosen, level)
            target = f"{row['kind']}:{row['horizon']}:{row['applicability']}:{level}"
            time_output[target] = all_factors[source_key]
            position_output[target] = all_padding.get(source_key, 0)
            fallback = f"{row['kind']}:{row['horizon']}:{level}"
            time_output[fallback] = all_factors[group_key(row, "action", level)]
            position_output[fallback] = all_padding.get(group_key(row, "action", level), 0)
    return time_output, position_output, all_counts


def selective(rows):
    accepted = {
        "RELIABLE": {"RELIABLE"},
        "RELIABLE_USABLE": {"RELIABLE", "USABLE"},
        "ALL_NON_OOD": {"RELIABLE", "USABLE", "WEAK"},
        "ALL": {"RELIABLE", "USABLE", "WEAK", "OUT_OF_DOMAIN"},
    }
    output = {}
    for name, labels in accepted.items():
        output[name] = {}
        for horizon in HORIZONS:
            picked = [
                row
                for row in rows
                if row["horizon"] == horizon
                and row["applicability"] in labels
                and row["actual"] is not None
            ]
            errors = [row["rollout"] - row["actual"] for row in picked]
            output[name][str(horizon)] = {
                "samples": len(picked),
                "acceptance_rate": len(picked)
                / max(
                    1, len([r for r in rows if r["horizon"] == horizon and r["actual"] is not None])
                ),
                "mae": mean(abs(value) for value in errors) if errors else None,
                "p90": quantile([abs(value) for value in errors], 0.9) if errors else None,
                **(calibration_metrics(picked)[str(horizon)] if picked else {}),
            }
    return output


def reliability_report(rows):
    output = {}
    for label in ("RELIABLE", "USABLE", "WEAK", "OUT_OF_DOMAIN"):
        output[label] = {}
        for horizon in HORIZONS:
            selected = [
                row
                for row in rows
                if row["horizon"] == horizon
                and row["applicability"] == label
                and row["actual"] is not None
            ]
            errors = [row["rollout"] - row["actual"] for row in selected]
            output[label][str(horizon)] = {
                "samples": len(selected),
                "mae": mean(abs(value) for value in errors) if errors else None,
                "p90": quantile([abs(value) for value in errors], 0.9) if errors else None,
                **(calibration_metrics(selected)[str(horizon)] if selected else {}),
            }
    return output


def failure_modes(rows):
    selected = sorted(
        (
            row
            for row in rows
            if row["horizon"] == 5 and row["actual"] is not None
        ),
        key=lambda row: abs(row["rollout"] - row["actual"]),
        reverse=True,
    )[:20]
    counts = Counter()
    examples = []
    for row in selected:
        labels = []
        if row["gap_kind"] != "TIME":
            labels.append("lapped traffic")
        if (row.get("rejoin_width") or 0) >= 4:
            labels.append("rejoin issue")
        if row.get("traffic_level") in {"HEAVY_TRAFFIC", "UNKNOWN"}:
            labels.append("traffic interaction")
        if (row.get("uncertain_crossings") or 0) > 0:
            labels.append("overtaking")
        if (row.get("pit_loss_mad") or 0) >= 2:
            labels.append("unusual stop")
        if not labels:
            labels.append("unexplained pace shock")
        counts.update(labels)
        examples.append(
            {
                "race": row["race"],
                "driver_id": row["driver_id"],
                "kind": row["kind"],
                "absolute_error": abs(row["rollout"] - row["actual"]),
                "labels": labels,
            }
        )
    return {"worst_case_count": len(selected), "counts": dict(counts), "examples": examples}


def circuit_holdout(training, development, selected_candidate):
    output, unseen_pit_rows = {}, []
    for circuit in sorted({row["circuit"] for row in development}):
        fit = [row for row in training if row["circuit"] != circuit and row["year"] <= 2022]
        calibrate_records = [
            row for row in training if row["circuit"] != circuit and row["year"] == 2023
        ]
        test = [row for row in development if row["circuit"] == circuit]
        if not fit or not calibrate_records or not test:
            continue
        artifact = fit_artifact(fit)
        raw_calibration = score_records(
            calibrate_records, artifact["selected_error_model"], artifact_override=artifact
        )
        if selected_candidate == "current":
            factors, padding = {}, {}
        else:
            factors, padding, _ = artifact_calibration(
                raw_calibration, selected_candidate
            )
        artifact["time_calibration_factors"] = factors
        artifact["position_calibration_padding"] = padding
        scored = score_records(test, artifact["selected_error_model"], artifact_override=artifact)
        output[circuit] = {
            kind: performance([row for row in scored if row["kind"] == kind])
            for kind in ("PIT_NOW", "EXTEND")
        }
        unseen_pit_rows.extend(row for row in scored if row["kind"] == "PIT_NOW")
    return output, unseen_pit_rows


def long_horizon_rows(records, artifact, trajectories=100):
    rows = []
    for record in records:
        context = record["context"]
        current = context.driver(record["driver_id"])
        direct = context and __import__(
            "f1_pitwall.services.simulation", fromlist=["simulate_action"]
        ).simulate_action(context, record["driver_id"], record["action"])
        curve = _deterministic_curve(direct, current.gap_to_leader)
        if curve is None:
            continue
        race = record["full_race"]
        laps = sorted({row.number for row in race.laps})
        from evaluate_counterfactuals import lap_cutoffs

        _, cuts = lap_cutoffs(race)
        pit_lap = None
        if record["kind"] == "PIT_NOW":
            stops = sorted(
                (
                    s
                    for s in race.pit_stops
                    if s.driver_id == record["driver_id"] and s.entered_at > context.cutoff
                ),
                key=lambda s: s.entered_at,
            )
            if not stops or stops[0].exited_at is None:
                continue
            pit_lap = min(
                (lap for lap in laps if cuts.get(lap, 0) >= stops[0].exited_at), default=None
            )
        timing = sorted(
            (row for row in race.timing if row.driver_id == record["driver_id"]),
            key=lambda row: row.at,
        )
        full_context = AnalysisContext(race, max(laps))
        reference = field_reference(full_context, record["driver_id"])
        base_increment = curve[4] - curve[3]
        rng = random.Random(
            zlib.crc32(f"long:{record['race']}:{record['lap']}:{record['driver_id']}".encode())
        )
        paths = []
        for _ in range(trajectories):
            total = curve[4]
            values = {5: total}
            latent = rng.choice(artifact["latent_pools"][record["kind"]])
            for index in range(6, 16):
                total += base_increment + _sample_residual(
                    rng,
                    artifact,
                    record["kind"],
                    "NORMAL_RUNNING",
                    "WEAK",
                    artifact["selected_error_model"],
                    latent,
                )
                if index in {8, 10, 15}:
                    values[index] = total
            paths.append(values)
        for horizon in (8, 10, 15):
            target_lap = (pit_lap + horizon - 1) if pit_lap else record["lap"] + horizon
            if target_lap not in cuts:
                continue
            target_cutoff = cuts[target_lap]
            if any(
                stop.driver_id == record["driver_id"]
                and context.cutoff < stop.entered_at <= target_cutoff
                for stop in race.pit_stops
                if record["kind"] == "EXTEND"
            ):
                continue
            if not green_outcome_window(race, context.cutoff, target_cutoff):
                continue
            actual = timing_values_at(timing, target_cutoff)
            factual_laps = [
                lap
                for lap in race.laps
                if lap.driver_id == record["driver_id"]
                and record["lap"] < lap.number <= target_lap
                and lap.number in reference
                and lap.lap_time_seconds is not None
            ]
            if len(factual_laps) < horizon:
                continue
            actual_delta = sum(
                lap.lap_time_seconds - reference[lap.number] for lap in factual_laps
            )
            samples = [path[horizon] for path in paths]
            midpoint = median(samples)
            interval80 = (quantile(samples, 0.1), quantile(samples, 0.9))
            rows.append(
                {
                    "kind": record["kind"],
                    "horizon": horizon,
                    "prediction": midpoint,
                    "actual": actual_delta,
                    "interval80": interval80,
                    "position": direct.outcomes[-1].expected_position,
                    "actual_position": actual["position"],
                }
            )
    output = {}
    for horizon in (8, 10, 15):
        selected = [row for row in rows if row["horizon"] == horizon]
        errors = [row["prediction"] - row["actual"] for row in selected]
        positioned = [row for row in selected if row["position"] and row["actual_position"]]
        output[str(horizon)] = {
            "samples": len(selected),
            "mae": mean(abs(value) for value in errors) if errors else None,
            "bias": mean(errors) if errors else None,
            "p90": quantile([abs(value) for value in errors], 0.9) if errors else None,
            "coverage_80": mean(
                interval_contains(row["interval80"], row["actual"]) for row in selected
            )
            if selected
            else None,
            "mean_width_80": mean(row["interval80"][1] - row["interval80"][0] for row in selected)
            if selected
            else None,
            "position_mae": mean(
                abs(row["position"] - row["actual_position"]) for row in positioned
            )
            if positioned
            else None,
        }
    return output


def stability(records, artifact):
    representatives = []
    for kind in ("PIT_NOW", "EXTEND"):
        record = next(
            row
            for row in records
            if row["kind"] == kind
            and row["outcomes"][5]["actual_delta"] is not None
            and row["outcomes"][5]["green"]
        )
        representatives.append(record)
    output = {}
    for record in representatives:
        runs = {}
        for count in (100, 500, 1000):
            values = []
            for seed in range(10):
                result = score_records(
                    [record], artifact["selected_error_model"], count, artifact, seed
                )
                row = next(item for item in result if item["horizon"] == 5)
                values.append(row)
            runs[str(count)] = {
                "median_of_medians": median(row["rollout"] for row in values),
                "median_span": max(row["rollout"] for row in values)
                - min(row["rollout"] for row in values),
                "interval80_lower_span": max(row["interval_80"][0] for row in values)
                - min(row["interval_80"][0] for row in values),
                "interval80_upper_span": max(row["interval_80"][1] for row in values)
                - min(row["interval_80"][1] for row in values),
            }
        output[record["kind"]] = runs
    return output


def season_generalization(training, raw_dev, final_rows):
    fit = [row for row in training if row["year"] <= 2022]
    test_2023 = [row for row in training if row["year"] == 2023]
    artifact = fit_artifact(fit)
    rows_2023 = score_records(
        test_2023, artifact["selected_error_model"], artifact_override=artifact
    )
    return {
        "2023_fit_2021_2022": performance(rows_2023),
        "2024_fit_2021_2023": performance(raw_dev),
        "2025_fit_2021_2024": performance(final_rows),
    }


def main():
    training_races = load_races(TRAIN_RACES)
    development_races = load_races(DEVELOPMENT_RACES)
    final_races = load_races(FINAL_RACES)
    training = cached_records("training", training_races, training_races)
    development = cached_records(
        "development", development_races, training_races + development_races
    )
    artifact = joblib.load(ARTIFACT)
    artifact.pop("time_calibration_factors", None)
    artifact.pop("position_calibration_padding", None)
    raw_dev = score_records(
        development, artifact["selected_error_model"], artifact_override=artifact
    )
    calibration_races = {"2024/1", "2024/4", "2024/8"}
    calibrate = [row for row in raw_dev if row["race"] in calibration_races]
    select = [row for row in raw_dev if row["race"] not in calibration_races]
    candidates = {}
    fitted = {}
    for candidate in (
        "current",
        "pooled",
        "action",
        "action_reliability",
        "action_phase",
        "hierarchical",
    ):
        if candidate == "current":
            factors, padding, counts = {}, {}, {}
            calibrated = select
        else:
            factors, padding, counts = fit_calibration(calibrate, candidate)
            calibrated = apply_candidate(select, candidate, factors, padding, counts)
        report = calibration_metrics(calibrated)
        pit_report = calibration_metrics(
            [row for row in calibrated if row["kind"] == "PIT_NOW"]
        )
        extend_report = calibration_metrics(
            [row for row in calibrated if row["kind"] == "EXTEND"]
        )
        score = mean(
            (
                calibration_score(report),
                calibration_score(pit_report),
                calibration_score(extend_report),
            )
        )
        candidates[candidate] = {
            "score": score,
            "metrics": report,
            "pit_metrics": pit_report,
            "extend_metrics": extend_report,
        }
        fitted[candidate] = (factors, padding, counts)
    selected_candidate = min(candidates, key=lambda name: candidates[name]["score"])
    if selected_candidate == "current":
        time_factors, position_padding, counts = {}, {}, {}
    else:
        time_factors, position_padding, counts = artifact_calibration(
            raw_dev, selected_candidate
        )
    artifact["calibration_method"] = selected_candidate
    artifact["time_calibration_factors"] = time_factors
    artifact["position_calibration_padding"] = position_padding
    artifact["calibration_counts"] = counts
    artifact["safety_policy"] = {
        "RELIABLE": "normal rollout",
        "USABLE": "rollout with calibrated wider uncertainty",
        "WEAK": "coarse evolution; restrict strategic conclusions",
        "OUT_OF_DOMAIN": "stop precise time projection; retain coarse track-order state",
    }
    joblib.dump(artifact, ARTIFACT)
    _artifact.cache_clear()
    final = cached_records(
        "final", final_races, training_races + development_races + final_races
    )
    final_rows = score_records(final, artifact["selected_error_model"], artifact_override=artifact)
    circuits, unseen_pit = circuit_holdout(training, development, selected_candidate)
    result = {
        "chronology": (
            "2021-2023 fit; early 2024 calibration; late 2024 candidate selection; "
            "all 2024 refit; frozen 2025 evaluation"
        ),
        "candidate_comparison": candidates,
        "selected_calibration": selected_candidate,
        "final_calibration": performance(final_rows),
        "pit_calibration": performance(
            [row for row in final_rows if row["kind"] == "PIT_NOW"]
        ),
        "extend_calibration": performance(
            [row for row in final_rows if row["kind"] == "EXTEND"]
        ),
        "position_calibration": position_report(final_rows),
        "reliability": reliability_report(final_rows),
        "selective": selective(final_rows),
        "circuit_holdout": circuits,
        "unseen_circuit_pit": performance(unseen_pit),
        "season_generalization": season_generalization(training, raw_dev, final_rows),
        "long_horizon": long_horizon_rows(final, artifact),
        "stability": stability(final, artifact),
        "failure_modes": failure_modes(final_rows),
    }
    OUTPUT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(OUTPUT),
                "selected": selected_candidate,
                "pit": result["pit_calibration"],
                "long": result["long_horizon"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
