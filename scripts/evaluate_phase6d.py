"""Focused Phase 6D PIT-opportunity development and held-out evaluation."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from statistics import mean, median
from time import perf_counter

from audit_phase6c import green, grid_group, load_race, pit_lap_for

from f1_pitwall.services.analysis_context import AnalysisContext
from f1_pitwall.services.paired import (
    EQUIVALENCE_BANDS,
    POSITION_EQUIVALENCE_PLACES,
    evaluate_paired_candidates,
)
from f1_pitwall.services.pit_analysis import estimate_pit_loss
from f1_pitwall.services.pit_opportunity import (
    TIME_MARGINAL_FREQUENCY,
    TIME_STRONG_FREQUENCY,
    TRAFFIC_PENALTIES,
    WEAK_APPLICABILITY_PENALTY,
    calculate_pit_opportunity,
)
from f1_pitwall.services.strategy import recommend_driver_action

DEVELOPMENT_RACES = (
    (2023, 1, "Bahrain"),
    (2023, 7, "Barcelona"),
    (2023, 14, "Monza"),
    (2023, 15, "Singapore"),
    (2024, 1, "Bahrain"),
    (2024, 10, "Barcelona"),
)
VALIDATION_RACES = (
    (2024, 16, "Monza"),
    (2024, 18, "Singapore"),
    (2025, 4, "Bahrain"),
    (2025, 9, "Barcelona"),
    (2025, 16, "Monza"),
    (2025, 18, "Singapore"),
)
CANDIDATES = ("TIME_ONLY", "TIME_POSITION", "FULL_EVIDENCE")
MAX_STOPS_PER_RACE = 8
FROZEN_PARAMETERS = {
    "time_equivalence_band_seconds": EQUIVALENCE_BANDS[5],
    "physical_position_equivalence_places": POSITION_EQUIVALENCE_PLACES,
    "time_marginal_frequency": TIME_MARGINAL_FREQUENCY,
    "time_strong_frequency": TIME_STRONG_FREQUENCY,
    "traffic_penalties": TRAFFIC_PENALTIES,
    "weak_applicability_penalty": WEAK_APPLICABILITY_PENALTY,
}


def _driver(context, driver_id):
    return next((row for row in context.state.drivers if row.driver.id == driver_id), None)


def _valid_state(context, driver_id):
    driver = _driver(context, driver_id)
    return driver if green(context) and driver is not None and driver.status == "active" else None


def _round_robin_stops(stops):
    by_group = defaultdict(list)
    for stop in stops:
        by_group[stop["grid_group"]].append(stop)
    chosen = []
    groups = ("P1-P5", "P6-P10", "P11-P15", "P16+")
    while len(chosen) < MAX_STOPS_PER_RACE:
        added = False
        for group in groups:
            if by_group[group] and len(chosen) < MAX_STOPS_PER_RACE:
                chosen.append(by_group[group].pop(0))
                added = True
        if not added:
            break
    return chosen


def _observed_outcome(contexts, driver_id, lap, horizon):
    current = _driver(contexts[lap], driver_id)
    future_context = contexts.get(lap + horizon)
    future = _driver(future_context, driver_id) if future_context else None
    if current is None or future is None:
        return {"position_delta": None, "timing_delta_seconds": None}
    position_delta = (
        future.position - current.position
        if future.position is not None and current.position is not None
        else None
    )
    timed = (
        not current.lapped
        and not future.lapped
        and current.gap_to_leader is not None
        and future.gap_to_leader is not None
    )
    return {
        "position_delta": position_delta,
        "timing_delta_seconds": round(future.gap_to_leader - current.gap_to_leader, 3)
        if timed
        else None,
    }


def _evaluate_state(context, row, trajectory_count, contexts):
    started = perf_counter()
    pit_loss = estimate_pit_loss(context)
    policy = recommend_driver_action(context, row["driver_id"], pit_loss)
    paired = evaluate_paired_candidates(
        context,
        row["driver_id"],
        policy.actions,
        trajectory_count,
        6000 + row["lap"],
    )
    best = paired.best_comparison
    pit_policy = next(
        (action for action in policy.actions if best and action.id == best.pit_action), None
    )
    applicability = "UNAVAILABLE"
    if best:
        marginal = paired.marginal_outcomes.get(best.pit_action, [])
        applicability = next(
            (item.applicability for item in marginal if item.horizon_laps == 5),
            "UNAVAILABLE",
        )
    opportunities = {
        candidate: calculate_pit_opportunity(
            best,
            candidate,
            pit_policy.traffic_status if pit_policy else "UNKNOWN",
            applicability,
        ).model_dump(mode="json")
        for candidate in CANDIDATES
        if best
    }
    return {
        **row,
        "paired_available": best is not None,
        "paired": best.model_dump(mode="json") if best else None,
        "rejoin_traffic": pit_policy.traffic_status if pit_policy else "UNKNOWN",
        "applicability": applicability,
        "opportunities": opportunities,
        "observed_plus_3": _observed_outcome(contexts, row["driver_id"], row["lap"], 3),
        "observed_plus_5": _observed_outcome(contexts, row["driver_id"], row["lap"], 5),
        "runtime_seconds": perf_counter() - started,
    }


def evaluate_race(spec_count):
    (year, round_number, circuit), trajectory_count = spec_count
    race = load_race(year, round_number)
    laps = sorted({row.number for row in race.laps})
    start, end = 6, max(laps) - 5
    contexts = {lap: AnalysisContext(race, lap) for lap in range(start, max(laps) + 1)}
    stop_laps = defaultdict(list)
    stops = []
    for stop in sorted(race.pit_stops, key=lambda item: item.entered_at):
        pit_lap = pit_lap_for(race, stop.entered_at, laps)
        if pit_lap is None:
            continue
        stop_laps[stop.driver_id].append(pit_lap)
        if not all(
            lap in contexts and _valid_state(contexts[lap], stop.driver_id)
            for lap in (pit_lap - 3, pit_lap - 2, pit_lap - 1)
        ):
            continue
        before = _driver(contexts[pit_lap - 1], stop.driver_id)
        stops.append(
            {
                "driver_id": stop.driver_id,
                "driver_code": before.driver.code,
                "pit_lap": pit_lap,
                "grid_group": grid_group(before.position),
            }
        )
    selected_stops = _round_robin_stops(stops)
    positives = []
    for index, stop in enumerate(selected_stops):
        for lead in (3, 2, 1):
            driver = _driver(contexts[stop["pit_lap"] - lead], stop["driver_id"])
            positives.append(
                {
                    "sample": "NEAR_PIT",
                    "stop_id": f"{year}/{round_number}/{index}",
                    "driver_id": stop["driver_id"],
                    "driver_code": stop["driver_code"],
                    "lap": stop["pit_lap"] - lead,
                    "lead_laps": lead,
                    "grid_group": grid_group(driver.position),
                    "tyre_age": driver.tyre_age,
                }
            )
    positive_keys = {(row["driver_id"], row["lap"]) for row in positives}
    controls = []
    used = set()
    for positive in positives:
        candidates = []
        for lap in range(start, end + 1):
            key = (positive["driver_id"], lap)
            if key in positive_keys or key in used:
                continue
            driver = _valid_state(contexts[lap], positive["driver_id"])
            if driver is None or grid_group(driver.position) != positive["grid_group"]:
                continue
            stops_for_driver = stop_laps[positive["driver_id"]]
            if any(
                0 <= pit_lap - lap <= 3 or 0 <= lap - pit_lap <= 3 for pit_lap in stops_for_driver
            ):
                continue
            age_delta = (
                abs((driver.tyre_age or 0) - (positive["tyre_age"] or 0))
                if driver.tyre_age is not None and positive["tyre_age"] is not None
                else 99
            )
            candidates.append((age_delta, abs(lap - positive["lap"]), lap, driver))
        if not candidates:
            continue
        _, _, lap, driver = min(candidates, key=lambda item: item[:3])
        used.add((positive["driver_id"], lap))
        controls.append(
            {
                "sample": "CONTROL",
                "stop_id": None,
                "driver_id": positive["driver_id"],
                "driver_code": positive["driver_code"],
                "lap": lap,
                "lead_laps": None,
                "grid_group": grid_group(driver.position),
                "tyre_age": driver.tyre_age,
                "matched_to_lead": positive["lead_laps"],
            }
        )
    rows = positives + controls
    evaluated = [
        _evaluate_state(contexts[row["lap"]], row, trajectory_count, contexts) for row in rows
    ]
    return {
        "race": f"{year}/{round_number}",
        "event": race.event.name,
        "circuit": circuit,
        "trajectory_count": trajectory_count,
        "selected_stops": len(selected_stops),
        "near_pit_states": len(positives),
        "control_states": len(controls),
        "states": evaluated,
    }


def _distribution(rows, candidate, sample):
    selected = [row for row in rows if row["sample"] == sample]
    counts = Counter(
        row["opportunities"].get(candidate, {}).get("signal", "UNAVAILABLE") for row in selected
    )
    return {
        "states": len(selected),
        "counts": dict(counts),
        "rates": {key: value / len(selected) for key, value in counts.items()} if selected else {},
    }


def _numeric_summary(values):
    values = [value for value in values if value is not None]
    return {
        "count": len(values),
        "median": median(values) if values else None,
        "mean": mean(values) if values else None,
    }


def summarize(results, candidates=CANDIDATES):
    rows = [row for result in results for row in result["states"]]
    report = {"states": len(rows), "candidates": {}}
    for candidate in candidates:
        strong = [
            row for row in rows if row["opportunities"].get(candidate, {}).get("signal") == "STRONG"
        ]
        near_strong = [row for row in strong if row["sample"] == "NEAR_PIT"]
        leads = [row["lead_laps"] for row in near_strong]
        durations = []
        by_stop = defaultdict(set)
        for row in near_strong:
            by_stop[row["stop_id"]].add(row["lead_laps"])
        for stop_leads in by_stop.values():
            duration = 0
            for lead in (1, 2, 3):
                if lead in stop_leads:
                    duration += 1
                else:
                    break
            durations.append(duration)
        by_grid = {}
        for group in ("P1-P5", "P6-P10", "P11-P15", "P16+"):
            grouped = [row for row in rows if row["grid_group"] == group]
            by_grid[group] = {
                "near_pit": _distribution(grouped, candidate, "NEAR_PIT"),
                "control": _distribution(grouped, candidate, "CONTROL"),
            }
        report["candidates"][candidate] = {
            "near_pit": _distribution(rows, candidate, "NEAR_PIT"),
            "control": _distribution(rows, candidate, "CONTROL"),
            "strong_median_lead_laps": median(leads) if leads else None,
            "strong_persistence_before_stop": _numeric_summary(durations),
            "strong_observed_outcomes": {
                horizon: {
                    "physical_position_delta": _numeric_summary(
                        row[f"observed_plus_{horizon}"]["position_delta"] for row in strong
                    ),
                    "timing_delta_seconds": _numeric_summary(
                        row[f"observed_plus_{horizon}"]["timing_delta_seconds"] for row in strong
                    ),
                }
                for horizon in (3, 5)
            },
            "false_looking_isolated_control_signals": sum(
                row["sample"] == "CONTROL" for row in strong
            ),
            "by_grid": by_grid,
        }
    report["mean_state_runtime_seconds"] = mean(row["runtime_seconds"] for row in rows)
    report["paired_unavailable"] = sum(not row["paired_available"] for row in rows)
    return report


def select_candidate(development):
    ranked = []
    for candidate in CANDIDATES:
        result = development["candidates"][candidate]
        near = result["near_pit"]["rates"].get("STRONG", 0)
        control = result["control"]["rates"].get("STRONG", 0)
        if result["near_pit"]["counts"].get("STRONG", 0) and near > control:
            ranked.append((near - control, -control, candidate))
    return max(ranked)[2] if ranked else None


def run(mode, output, trajectory_count):
    races = DEVELOPMENT_RACES if mode == "development" else VALIDATION_RACES
    started = perf_counter()
    with ProcessPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(evaluate_race, [(race, trajectory_count) for race in races]))
    summary = summarize(results)
    report = {
        "mode": mode,
        "chronology": {
            "races": [f"{year}/{round_number}" for year, round_number, _ in races],
            "future_observations_used_only_as_labels": True,
            "pit_cycle_used_in_signal": False,
        },
        "sampling": {
            "max_stops_per_race": MAX_STOPS_PER_RACE,
            "near_pit_laps": ["N-3", "N-2", "N-1"],
            "controls": "same driver/grid region, nearest tyre age, no pit within three laps",
        },
        "trajectory_count": trajectory_count,
        "candidate_definitions_frozen": True,
        "frozen_parameters": FROZEN_PARAMETERS,
        "results": summary,
        "race_results": results,
        "wall_clock_seconds": perf_counter() - started,
    }
    if mode == "development":
        report["selected_candidate"] = select_candidate(summary)
        report["held_out_status"] = {
            "opened": False,
            "planned_races": [
                f"{year}/{round_number}" for year, round_number, _ in VALIDATION_RACES
            ],
            "reason": "No development candidate separated near-pit states from controls.",
        }
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("development", "validation"))
    parser.add_argument("--trajectories", type=int, default=100)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.mode, args.output, args.trajectories)
    print(
        json.dumps(
            {
                "mode": report["mode"],
                "selected_candidate": report.get("selected_candidate"),
                "results": report["results"],
                "wall_clock_seconds": report["wall_clock_seconds"],
            },
            indent=2,
        )
    )
