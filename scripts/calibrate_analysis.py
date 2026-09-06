"""Phase 3B chronological model comparison and diagnostics.

Development races define the candidate choice; later validation races report the result.
Future laps appear only in labels created by this script, never in production calculators.
"""

import argparse
import asyncio
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

import httpx

from f1_pitwall.core.config import Settings
from f1_pitwall.domain.replay import HistoricalRace
from f1_pitwall.services.analysis_context import AnalysisContext
from f1_pitwall.services.hub import Hub
from f1_pitwall.services.pace import robust_line
from f1_pitwall.services.pair_analysis import calculate_overcut, calculate_undercut
from f1_pitwall.services.pit_analysis import estimate_pit_loss, predict_pit_rejoin
from f1_pitwall.services.traffic import analyze_traffic

DEVELOPMENT_RACES = [(2023, 1), (2023, 6), (2023, 7), (2023, 14)]
VALIDATION_RACES = [(2024, 1), (2024, 4), (2024, 8), (2024, 10), (2024, 16)]
MODELS = ("zero", "raw_all", "normalized_all", "normalized_recent_5")


def references(context, excluded_driver=None):
    """Race-lap medians from at least five other clean drivers."""
    by_lap = defaultdict(list)
    for identity, rows in context.clean.items():
        if identity == excluded_driver:
            continue
        for row in rows:
            by_lap[row.number].append(row.lap_time_seconds)
    return {lap: median(values) for lap, values in by_lap.items() if len(values) >= 5}


def normalized_points(context, identity, skip_first=0):
    reference = references(context, identity)
    rows = context.stint_laps(identity)[skip_first:]
    return [
        (row.number, row.lap_time_seconds - reference[row.number])
        for row in rows
        if row.number in reference
    ]


def normalized_times(context, identity, lap_numbers):
    reference = references(context, identity)
    return [
        context.rows[identity][number].lap_time_seconds - reference[number]
        for number in lap_numbers
        if number in context.rows[identity] and number in reference
    ]


def fresh_sample(context, sample):
    identity = sample["driver_id"]
    old = context.stint_at(identity, sample["entered_at"] - 1)
    first_post = sample["post_laps"][0]
    new = context.stint_at(identity, context.rows[identity][first_post].completed_at)
    pre_raw = [context.rows[identity][n].lap_time_seconds for n in sample["pre_laps"]]
    pre_norm = normalized_times(context, identity, sample["pre_laps"])
    post_norm = normalized_times(context, identity, [first_post])
    if not old or not new or old.number == new.number or len(pre_norm) < 2 or not post_norm:
        return None
    return {
        "driver_id": identity,
        "old_compound": old.compound,
        "new_compound": new.compound,
        "old_age": old.tyre_age,
        "raw_gain": median(pre_raw) - context.rows[identity][first_post].lap_time_seconds,
        "normalized_gain": median(pre_norm) - post_norm[0],
        "first_post_lap": first_post,
        "evidence_available_at": sample["evidence_available_at"],
    }


def fresh_estimates(context, driver_id, old_compound, new_compound=None):
    loss = estimate_pit_loss(context)
    samples = [fresh_sample(context, sample) for sample in loss.components["samples"]]
    samples = [sample for sample in samples if sample and sample["old_compound"] == old_compound]
    exact = [sample for sample in samples if sample["new_compound"] == new_compound]
    selected = exact if new_compound and exact else samples
    return {
        "zero": 0.0,
        "raw": median(sample["raw_gain"] for sample in selected) if selected else None,
        "normalized": median(sample["normalized_gain"] for sample in selected)
        if selected
        else None,
        "sample_count": len(selected),
        "transition_exact": bool(new_compound and exact),
    }


def slope(points):
    if len(points) < 5 or points[-1][0] - points[0][0] < 4:
        return None
    return robust_line(points, x=lambda p: p[0], y=lambda p: p[1])[0]


def race_samples(race):
    cuts = {}
    for row in race.laps:
        cuts[row.number] = min(cuts.get(row.number, float("inf")), row.available_at)
    last_lap = max(cuts)
    final = AnalysisContext(race, last_lap)
    samples = []
    for lap in range(10, last_lap - 4, 5):
        if lap not in cuts:
            continue
        current = AnalysisContext(race, lap)
        for identity, driver in current.drivers.items():
            if driver.status != "active" or driver.stint_number is None:
                continue
            raw = current.stint_laps(identity)
            norm = normalized_points(current, identity)
            if len(raw) < 5 or len(norm) < 5:
                continue
            recent = norm[-3:]
            traffic = analyze_traffic(current, identity)
            final_reference = references(final, identity)
            for horizon in (3, 5):
                future_rows = [
                    row
                    for row in final.stint_laps(identity, driver.stint_number)
                    if row.number > (driver.laps_completed or 0)
                    and row.number <= (driver.laps_completed or 0) + horizon
                    and row.number in final_reference
                ]
                if len(future_rows) < 2:
                    continue
                future = [
                    (row.number, row.lap_time_seconds - final_reference[row.number])
                    for row in future_rows
                ]
                anchor_lap = median(point[0] for point in recent)
                target_lap = median(point[0] for point in future)
                anchor = median(point[1] for point in recent)
                actual = median(point[1] for point in future)
                distance = target_lap - anchor_lap
                raw_slope = slope([(row.number, row.lap_time_seconds) for row in raw])
                candidates = {
                    "zero": 0.0,
                    "raw_all": raw_slope,
                    "normalized_all": slope(norm),
                    "normalized_recent_5": slope(norm[-5:]),
                }
                if any(value is None for value in candidates.values()):
                    continue
                predictions = {
                    name: anchor + value * distance for name, value in candidates.items()
                }
                current_age = driver.tyre_age
                race_fraction = lap / (current.state.total_scheduled_laps or last_lap)
                samples.append(
                    {
                        "race": f"{race.event.year}/{race.event.round}",
                        "event": race.event.name,
                        "circuit": race.event.circuit.name,
                        "lap": lap,
                        "horizon": horizon,
                        "driver_id": identity,
                        "driver": driver.driver.code,
                        "team": driver.constructor.name if driver.constructor else None,
                        "compound": driver.compound,
                        "stint": driver.stint_number,
                        "tyre_age": current_age,
                        "stint_length": raw[-1].number - raw[0].number + 1,
                        "clean_laps": len(raw),
                        "reference_laps": len(norm),
                        "reference_coverage": len(norm) / len(raw),
                        "traffic": traffic.status,
                        "race_phase": "early"
                        if race_fraction < 1 / 3
                        else "middle"
                        if race_fraction < 2 / 3
                        else "late",
                        "anchor": anchor,
                        "actual": actual,
                        "distance": distance,
                        "slopes": candidates,
                        "predictions": predictions,
                        "errors": {name: predictions[name] - actual for name in MODELS},
                    }
                )
    return samples


def metrics(rows):
    result = {"n": len(rows)}
    for model in MODELS:
        errors = [row["errors"][model] for row in rows]
        absolute = [abs(value) for value in errors]
        result[model] = {
            "mae": mean(absolute) if absolute else None,
            "median_absolute_error": median(absolute) if absolute else None,
            "bias": mean(errors) if errors else None,
        }
    return result


def grouped(rows, key):
    groups = defaultdict(list)
    for row in rows:
        groups[str(row[key])].append(row)
    return {name: metrics(items) for name, items in sorted(groups.items())}


def warmup_comparison(races):
    output = {}
    contexts = {}
    finals = {}
    for race in races:
        cuts = sorted({r.number for r in race.laps})
        finals[id(race)] = AnalysisContext(race, max(cuts))
        contexts[id(race)] = {
            lap: AnalysisContext(race, lap) for lap in range(10, max(cuts) - 4, 5) if lap in cuts
        }
    for skip in (0, 1, 2):
        rows = []
        for race in races:
            cuts = sorted({r.number for r in race.laps})
            final = finals[id(race)]
            for context in contexts[id(race)].values():
                for identity, driver in context.drivers.items():
                    points = normalized_points(context, identity, skip)
                    fitted = slope(points)
                    if fitted is None or driver.stint_number is None:
                        continue
                    final_ref = references(final, identity)
                    future = [
                        (r.number, r.lap_time_seconds - final_ref[r.number])
                        for r in final.stint_laps(identity, driver.stint_number)
                        if r.number > (driver.laps_completed or 0)
                        and r.number <= (driver.laps_completed or 0) + 5
                        and r.number in final_ref
                    ]
                    if len(future) < 2:
                        continue
                    anchor = median(value for _, value in points[-3:])
                    distance = median(n for n, _ in future) - median(n for n, _ in points[-3:])
                    actual = median(value for _, value in future)
                    rows.append(anchor + fitted * distance - actual)
        output[f"skip_{skip}"] = {
            "n": len(rows),
            "mae": mean(abs(e) for e in rows) if rows else None,
            "bias": mean(rows) if rows else None,
        }
    return output


def stop_validation(race):
    cuts = {}
    for row in race.laps:
        cuts[row.number] = min(cuts.get(row.number, float("inf")), row.available_at)
    final = AnalysisContext(race, max(cuts))
    full_loss = estimate_pit_loss(final)
    rows = []
    contexts = {}
    for sample in full_loss.components["samples"]:
        before = [lap for lap, cutoff in cuts.items() if cutoff < sample["entered_at"]]
        if not before:
            continue
        lap = max(before)
        if lap not in contexts:
            contexts[lap] = AnalysisContext(race, lap)
        context = contexts[lap]
        actual = fresh_sample(final, sample)
        if not actual:
            continue
        estimates = fresh_estimates(context, sample["driver_id"], actual["old_compound"])
        if estimates["raw"] is None or estimates["normalized"] is None:
            continue
        rows.append(
            {
                "race": f"{race.event.year}/{race.event.round}",
                "event": race.event.name,
                "lap": lap,
                **actual,
                "estimates": estimates,
                "errors": {
                    "zero": -actual["normalized_gain"],
                    "raw": estimates["raw"] - actual["normalized_gain"],
                    "normalized": estimates["normalized"] - actual["normalized_gain"],
                },
            }
        )
    return rows


def fresh_metrics(rows):
    result = {"n": len(rows)}
    for name in ("zero", "raw", "normalized"):
        errors = [row["errors"][name] for row in rows]
        result[name] = {
            "mae": mean(abs(error) for error in errors) if errors else None,
            "median_absolute_error": median(abs(error) for error in errors) if errors else None,
            "bias": mean(errors) if errors else None,
        }
    result["median_sample_count"] = (
        median(row["estimates"]["sample_count"] for row in rows) if rows else None
    )
    return result


def fresh_grouped(rows, key):
    groups = defaultdict(list)
    for row in rows:
        if key == "transition":
            value = f"{row['old_compound']}->{row['new_compound']}"
        elif key == "old_age_bucket":
            age = row["old_age"]
            value = (
                "unknown" if age is None else "<10" if age < 10 else "10-19" if age < 20 else "20+"
            )
        else:
            value = row[key]
        groups[str(value)].append(row)
    return {name: fresh_metrics(items) for name, items in sorted(groups.items())}


def rejoin_validation(race):
    cuts = {}
    for row in race.laps:
        cuts[row.number] = min(cuts.get(row.number, float("inf")), row.available_at)
    results = []
    contexts = {}
    for pit in race.pit_stops:
        if pit.exited_at is None:
            continue
        before = [lap for lap, cutoff in cuts.items() if cutoff < pit.entered_at]
        after = [lap for lap, cutoff in cuts.items() if cutoff >= pit.exited_at]
        if not before or not after:
            continue
        lap, actual_lap = max(before), min(after)
        if lap not in contexts:
            contexts[lap] = AnalysisContext(race, lap)
        if actual_lap not in contexts:
            contexts[actual_lap] = AnalysisContext(race, actual_lap)
        current, actual = contexts[lap], contexts[actual_lap]
        loss = estimate_pit_loss(current)
        prediction = predict_pit_rejoin(pit.driver_id, current.state, loss)
        actual_position = actual.driver(pit.driver_id).position
        if not prediction.position_range or actual_position is None:
            continue
        lower, upper = prediction.position_range
        results.append(
            {
                "race": f"{race.event.year}/{race.event.round}",
                "lap": lap,
                "driver_id": pit.driver_id,
                "predicted": prediction.projected_position,
                "range": [lower, upper],
                "actual": actual_position,
                "absolute_error": abs(prediction.projected_position - actual_position)
                if prediction.projected_position is not None
                else None,
                "range_miss": max(lower - actual_position, actual_position - upper, 0),
            }
        )
    exact = [row for row in results if row["absolute_error"] is not None]
    return {
        "rows": results,
        "n": len(results),
        "exact_n": len(exact),
        "mae": mean(row["absolute_error"] for row in exact) if exact else None,
        "median_absolute_error": median(row["absolute_error"] for row in exact) if exact else None,
        "p90_absolute_error": sorted(row["absolute_error"] for row in exact)[
            min(len(exact) - 1, int(len(exact) * 0.9))
        ]
        if exact
        else None,
        "range_miss_mean": mean(row["range_miss"] for row in results) if results else None,
    }


def pair_validation(race):
    cuts = {}
    for row in race.laps:
        cuts[row.number] = min(cuts.get(row.number, float("inf")), row.available_at)
    contexts = {}

    def context(lap):
        if lap not in contexts:
            contexts[lap] = AnalysisContext(race, lap)
        return contexts[lap]

    rows = []
    for first_stop in race.pit_stops:
        if first_stop.exited_at is None:
            continue
        before = [lap for lap, cutoff in cuts.items() if cutoff < first_stop.entered_at]
        if not before:
            continue
        lap = max(before)
        current = context(lap)
        first_driver = current.driver(first_stop.driver_id)
        if first_driver.position is None:
            continue
        for kind in ("undercut", "overcut"):
            neighbour_position = (
                first_driver.position - 1 if kind == "undercut" else first_driver.position + 1
            )
            neighbours = [d for d in current.state.drivers if d.position == neighbour_position]
            if not neighbours:
                continue
            neighbour = neighbours[0]
            later_stops = [
                stop
                for stop in race.pit_stops
                if stop.driver_id == neighbour.driver.id
                and stop.exited_at is not None
                and first_stop.entered_at < stop.entered_at <= first_stop.entered_at + 150
            ]
            if not later_stops:
                continue
            second_stop = min(later_stops, key=lambda stop: stop.entered_at)
            second_before = max(
                (n for n, cutoff in cuts.items() if cutoff < second_stop.entered_at), default=0
            )
            if second_before - lap != 1:
                continue
            driver_id, target_id = (
                (first_stop.driver_id, neighbour.driver.id)
                if kind == "undercut"
                else (neighbour.driver.id, first_stop.driver_id)
            )
            function = calculate_undercut if kind == "undercut" else calculate_overcut
            result = function(current, driver_id, target_id, estimate_pit_loss(current))
            evaluation_lap = min(
                (n for n, cutoff in cuts.items() if cutoff >= second_stop.exited_at), default=0
            )
            if not evaluation_lap:
                continue
            after = context(evaluation_lap)
            driver, target = after.driver(driver_id), after.driver(target_id)
            if driver.position is None or target.position is None:
                continue
            actual_positive = driver.position < target.position
            actual_margin = (
                target.gap_to_leader - driver.gap_to_leader
                if target.gap_to_leader is not None and driver.gap_to_leader is not None
                else None
            )
            rows.append(
                {
                    "race": f"{race.event.year}/{race.event.round}",
                    "kind": kind,
                    "lap": lap,
                    "driver_id": driver_id,
                    "target_id": target_id,
                    "prediction": result.opportunity,
                    "predicted_margin": result.estimated_margin,
                    "actual_positive": actual_positive,
                    "actual_margin": actual_margin,
                    "margin_error": result.estimated_margin - actual_margin
                    if result.estimated_margin is not None and actual_margin is not None
                    else None,
                    "confidence": result.confidence,
                }
            )
    return rows


def pair_metrics(rows, kind):
    selected = [row for row in rows if row["kind"] == kind]
    decisive = [row for row in selected if row["prediction"] in {"YES", "NO"}]
    confusion = {name: 0 for name in ("tp", "fp", "tn", "fn")}
    for row in decisive:
        predicted = row["prediction"] == "YES"
        actual = row["actual_positive"]
        confusion[
            "tp" if predicted and actual else "fp" if predicted else "fn" if actual else "tn"
        ] += 1
    timed = [row for row in selected if row["margin_error"] is not None]
    return {
        "n": len(selected),
        "numeric_n": sum(row["predicted_margin"] is not None for row in selected),
        "decisive_n": len(decisive),
        "marginal_n": sum(row["prediction"] == "MARGINAL" for row in selected),
        "unknown_n": sum(row["prediction"] == "UNKNOWN" for row in selected),
        **confusion,
        "positive_prevalence": mean(row["actual_positive"] for row in selected)
        if selected
        else None,
        "margin_mae_seconds": mean(abs(row["margin_error"]) for row in timed) if timed else None,
        "margin_bias_seconds": mean(row["margin_error"] for row in timed) if timed else None,
    }


async def load_races(hub, selections, cached):
    races = []
    for year, round_number in selections:
        path = Path(f".cache/analysis-history-{year}-{round_number}.json")
        if cached and path.exists():
            race = HistoricalRace.model_validate_json(path.read_text(encoding="utf-8"))
        else:
            race = await hub.replay.load_race(year, round_number)
            path.parent.mkdir(exist_ok=True)
            path.write_text(race.model_dump_json(exclude_unset=True), encoding="utf-8")
        races.append(race)
        print(f"loaded {year}/{round_number}", flush=True)
    return races


async def main(output, cached):
    async with httpx.AsyncClient(follow_redirects=True) as client:
        hub = Hub(client, Settings.from_env())
        development = await load_races(hub, DEVELOPMENT_RACES, cached)
        validation = await load_races(hub, VALIDATION_RACES, cached)
    dev_rows, validation_rows = [], []
    for race in development:
        dev_rows.extend(race_samples(race))
    for race in validation:
        validation_rows.extend(race_samples(race))
    dev_stops = [row for race in development for row in stop_validation(race)]
    validation_stops = [row for race in validation for row in stop_validation(race)]
    rejoin = {
        f"{race.event.year}/{race.event.round}": rejoin_validation(race)
        for race in development + validation
    }
    dev_pairs = [row for race in development for row in pair_validation(race)]
    validation_pairs = [row for race in validation for row in pair_validation(race)]
    diagnostic_keys = (
        "circuit",
        "compound",
        "stint_length",
        "tyre_age",
        "driver",
        "team",
        "race_phase",
        "clean_laps",
        "traffic",
        "horizon",
    )
    report = {
        "development_races": DEVELOPMENT_RACES,
        "validation_races": VALIDATION_RACES,
        "target": "Future driver pace relative to leave-one-driver-out field median",
        "reference_minimum_other_drivers": 5,
        "development": {
            "overall": metrics(dev_rows),
            "groups": {key: grouped(dev_rows, key) for key in diagnostic_keys},
        },
        "validation": {
            "overall": metrics(validation_rows),
            "groups": {
                key: grouped(validation_rows, key) for key in ("circuit", "compound", "horizon")
            },
        },
        "warmup_development": warmup_comparison(development),
        "warmup_validation": warmup_comparison(validation),
        "fresh_tyre": {
            "development": {
                "overall": fresh_metrics(dev_stops),
                "groups": {
                    key: fresh_grouped(dev_stops, key)
                    for key in ("event", "transition", "old_age_bucket")
                },
            },
            "validation": {
                "overall": fresh_metrics(validation_stops),
                "groups": {
                    key: fresh_grouped(validation_stops, key)
                    for key in ("event", "transition", "old_age_bucket")
                },
            },
        },
        "rejoin": rejoin,
        "pairs": {
            "development": {
                kind: pair_metrics(dev_pairs, kind) for kind in ("undercut", "overcut")
            },
            "validation": {
                kind: pair_metrics(validation_pairs, kind) for kind in ("undercut", "overcut")
            },
        },
        "samples": {"development": dev_rows, "validation": validation_rows},
        "fresh_samples": {"development": dev_stops, "validation": validation_stops},
        "pair_samples": {"development": dev_pairs, "validation": validation_pairs},
    }
    Path(output).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "development": report["development"]["overall"],
                "validation": report["validation"]["overall"],
                "warmup_development": report["warmup_development"],
                "warmup_validation": report["warmup_validation"],
                "fresh_tyre": report["fresh_tyre"],
                "pairs": report["pairs"],
                "rejoin": {
                    key: {
                        name: value[name] for name in ("n", "exact_n", "mae", "p90_absolute_error")
                    }
                    for key, value in rejoin.items()
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=".cache/analysis-calibration.json")
    parser.add_argument("--cached", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.output, args.cached))
