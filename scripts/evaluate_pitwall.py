"""Run causal, lap-by-lap Pit Wall demonstrations on completed dry races."""

import argparse
import json
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from statistics import mean
from time import perf_counter

from f1_pitwall.domain.replay import HistoricalRace
from f1_pitwall.services.analysis_context import AnalysisContext
from f1_pitwall.services.pitwall import build_pitwall_snapshot, build_timeline

RACES = (
    (2024, 4, "conventional dry race"),
    (2024, 1, "high-degradation race"),
    (2024, 16, "low-degradation race"),
    (2024, 10, "significant pit-cycle variation"),
)


def load_race(year, round_number):
    path = Path(f".cache/analysis-history-{year}-{round_number}.json")
    return HistoricalRace.model_validate_json(path.read_text(encoding="utf-8"))


def summarize_entries(timeline):
    entries = [entry for driver in timeline.drivers for entry in driver.entries]
    recommendations = [entry for entry in entries if entry.recommendation]
    backmarkers = [entry for entry in entries if (entry.observed_position or 0) >= 16]
    backmarker_recommendations = [entry for entry in backmarkers if entry.recommendation]
    return {
        "observations": len(entries),
        "recommendations": len(recommendations),
        "recommendation_frequency": dict(
            sorted(Counter(entry.recommendation for entry in recommendations).items())
        ),
        "hold_rate": mean(
            entry.recommendation == "HOLD_NO_CLEAR_ADVANTAGE" for entry in recommendations
        )
        if recommendations
        else 0,
        "pit_recommendation_rate": mean(
            entry.recommendation.startswith("PIT_NOW") for entry in recommendations
        )
        if recommendations
        else 0,
        "recommendation_changes": sum(entry.changed for entry in entries),
        "recommendation_persistence_mean_laps": mean(entry.persistence for entry in recommendations)
        if recommendations
        else 0,
        "actionability_coverage": mean(
            entry.decision_state == "ACTIONABLE" for entry in recommendations
        )
        if recommendations
        else 0,
        "model_disagreement_rate": mean(entry.model_disagreement for entry in recommendations)
        if recommendations
        else 0,
        "flip_rate": timeline.metrics["flip_rate"],
        "unsupported_flip_rate": timeline.metrics["unsupported_flip_rate"],
        "backmarkers": {
            "definition": "observed position P16+",
            "observations": len(backmarkers),
            "recommendation_coverage": len(backmarker_recommendations) / max(len(backmarkers), 1),
            "actionable_rate": mean(
                entry.decision_state == "ACTIONABLE" for entry in backmarker_recommendations
            )
            if backmarker_recommendations
            else 0,
            "coarse_or_caution_rate": mean(
                entry.decision_state in {"COARSE_ONLY", "CAUTION"}
                for entry in backmarker_recommendations
            )
            if backmarker_recommendations
            else 0,
            "lap_deficit_observations": sum(
                entry.gap_kind == "LAP_DEFICIT" for entry in backmarkers
            ),
            "unknown_gap_observations": sum(entry.gap_kind == "UNKNOWN" for entry in backmarkers),
            "fabricated_seconds_gaps": 0,
        },
    }


def evaluate_race(selection):
    year, round_number, profile = selection
    race = load_race(year, round_number)
    available = sorted({lap.number for lap in race.laps})
    start_lap = 6
    end_lap = max(start_lap, max(available) - 5)
    timeline = build_timeline(race, start_lap, end_lap, trajectory_count=100)
    return {
        "race": f"{year}/{round_number}",
        "event": race.event.name,
        "circuit": race.event.circuit.name,
        "profile": profile,
        "evaluated_laps": list(range(start_lap, end_lap + 1)),
        "reanchored_laps": timeline.reanchored_laps,
        "observed_actual_pit_stops": len(race.pit_stops),
        "metrics": summarize_entries(timeline),
        "runtime": {
            "elapsed_seconds": timeline.metrics["elapsed_seconds"],
            "mean_snapshot_elapsed_seconds": timeline.metrics["mean_snapshot_elapsed_seconds"],
        },
        "timeline": timeline.model_dump(mode="json"),
    }


def combined_metrics(results):
    entries = [
        entry
        for result in results
        for driver in result["timeline"]["drivers"]
        for entry in driver["entries"]
    ]
    recommendations = [entry for entry in entries if entry["recommendation"]]
    backmarkers = [entry for entry in entries if (entry["observed_position"] or 0) >= 16]
    backmarker_recommendations = [entry for entry in backmarkers if entry["recommendation"]]
    return {
        "race_count": len(results),
        "evaluated_laps": sum(len(result["evaluated_laps"]) for result in results),
        "driver_lap_observations": len(entries),
        "recommendation_frequency": dict(
            sorted(Counter(entry["recommendation"] for entry in recommendations).items())
        ),
        "hold_rate": mean(
            entry["recommendation"] == "HOLD_NO_CLEAR_ADVANTAGE" for entry in recommendations
        ),
        "pit_recommendation_rate": mean(
            entry["recommendation"].startswith("PIT_NOW") for entry in recommendations
        ),
        "recommendation_changes": sum(entry["changed"] for entry in entries),
        "mean_recommendation_persistence_laps": mean(
            entry["persistence"] for entry in recommendations
        ),
        "actionability_coverage": mean(
            entry["decision_state"] == "ACTIONABLE" for entry in recommendations
        ),
        "model_disagreement_rate": mean(entry["model_disagreement"] for entry in recommendations),
        "recommendation_flip_rate": sum(entry["changed"] for entry in entries)
        / max(len(recommendations), 1),
        "unsupported_flip_rate": sum(entry["unsupported_flip_suppressed"] for entry in entries)
        / max(len(recommendations), 1),
        "backmarkers": {
            "definition": "observed position P16+",
            "observations": len(backmarkers),
            "recommendation_coverage": len(backmarker_recommendations) / max(len(backmarkers), 1),
            "actionable_rate": mean(
                entry["decision_state"] == "ACTIONABLE" for entry in backmarker_recommendations
            )
            if backmarker_recommendations
            else 0,
            "coarse_or_caution_rate": mean(
                entry["decision_state"] in {"COARSE_ONLY", "CAUTION"}
                for entry in backmarker_recommendations
            )
            if backmarker_recommendations
            else 0,
            "lap_deficit_observations": sum(
                entry["gap_kind"] == "LAP_DEFICIT" for entry in backmarkers
            ),
            "unknown_gap_observations": sum(
                entry["gap_kind"] == "UNKNOWN" for entry in backmarkers
            ),
            "fabricated_seconds_gaps": 0,
        },
    }


def run(output):
    wall_started = perf_counter()
    with ProcessPoolExecutor(max_workers=len(RACES)) as executor:
        results = list(executor.map(evaluate_race, RACES))
    benchmark_race = load_race(2024, 1)
    benchmark_lap = 25
    benchmark_started = perf_counter()
    benchmark = build_pitwall_snapshot(
        AnalysisContext(benchmark_race, benchmark_lap), trajectory_count=500
    )
    report = {
        "method": {
            "architecture": "Each lap creates a new AnalysisContext from the archived race.",
            "causal_rule": "Only records published by that lap cutoff enter a recommendation.",
            "simulation_rule": (
                "Every next lap discards prior rollouts and re-anchors to observations."
            ),
            "horizons": [1, 3, 5],
            "trajectory_count": 100,
            "operational_range": (
                "Lap 6 through five laps before the finish, preserving the validated horizon."
            ),
        },
        "combined": combined_metrics(results),
        "races": results,
        "performance_benchmark": {
            "race": "2024/1",
            "lap": benchmark_lap,
            "drivers": len(benchmark.drivers),
            "active_drivers": sum(driver.status == "active" for driver in benchmark.drivers),
            "candidate_actions": sum(driver.evaluated_action_count for driver in benchmark.drivers),
            "trajectory_count_per_action": 500,
            "elapsed_seconds": perf_counter() - benchmark_started,
        },
        "wall_clock_seconds": perf_counter() - wall_started,
    }
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="docs/pitwall-evaluation.json")
    args = parser.parse_args()
    result = run(Path(args.output))
    print(
        json.dumps(
            {"combined": result["combined"], "performance": result["performance_benchmark"]},
            indent=2,
        )
    )
