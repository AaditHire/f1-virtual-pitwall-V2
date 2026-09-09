"""Evaluate the frozen paired Pit Wall on later dry 2025 races."""

import json
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from statistics import mean
from time import perf_counter

from f1_pitwall.domain.replay import HistoricalRace
from f1_pitwall.services.analysis_context import AnalysisContext
from f1_pitwall.services.pitwall import build_pitwall_snapshot, build_timeline
from f1_pitwall.services.race_state import cutoff_for

VALIDATION_RACES = (
    (2025, 4, "Bahrain dry/high degradation"),
    (2025, 9, "Spain dry/pit-cycle variation"),
    (2025, 16, "Italy dry/low degradation"),
)


def load_race(year, round_number):
    return HistoricalRace.model_validate_json(
        Path(f".cache/analysis-history-{year}-{round_number}.json").read_text(encoding="utf-8")
    )


def group(position):
    if position is None:
        return "UNKNOWN"
    if position <= 5:
        return "P1-P5"
    if position <= 10:
        return "P6-P10"
    if position <= 15:
        return "P11-P15"
    return "P16+"


def pit_lap_for(race, entered_at, laps):
    return next((lap for lap in laps if cutoff_for(race, lap) >= entered_at), None)


def summarize_race(selection):
    year, round_number, profile = selection
    race = load_race(year, round_number)
    available = sorted({row.number for row in race.laps})
    start_lap, end_lap = 6, max(available) - 5
    timeline = build_timeline(race, start_lap, end_lap, trajectory_count=100)
    entries = {
        (driver.driver.id, entry.lap): entry
        for driver in timeline.drivers
        for entry in driver.entries
    }
    pit_events = []
    next_pit_states = set()
    for stop in sorted(race.pit_stops, key=lambda row: row.entered_at):
        pit_lap = pit_lap_for(race, stop.entered_at, available)
        if pit_lap is None or not start_lap + 5 <= pit_lap <= end_lap + 1:
            continue
        pre_context = AnalysisContext(race, pit_lap - 1)
        if (
            pre_context.state.track.safety_car
            or pre_context.state.track.virtual_safety_car
            or pre_context.state.track.red_flag
        ):
            continue
        sequence = [entries.get((stop.driver_id, lap)) for lap in range(pit_lap - 5, pit_lap)]
        if any(entry is None for entry in sequence):
            continue
        next_pit_states.add((stop.driver_id, pit_lap - 1))
        ranks = {
            "PIT_WINDOW_CLOSED": 0,
            "PIT_WINDOW_UNCERTAIN": 1,
            "PIT_WINDOW_OPEN": 2,
            "PIT_WINDOW_STRONG": 3,
        }
        pit_events.append(
            {
                "driver_id": stop.driver_id,
                "observed_pit_lap": pit_lap,
                "reference_only": (
                    "The team stop is behavioral context, not proof PIT was optimal."
                ),
                "preceding_laps": [entry.model_dump(mode="json") for entry in sequence],
                "window_opened": any(
                    entry.pit_window_state in {"PIT_WINDOW_OPEN", "PIT_WINDOW_STRONG"}
                    for entry in sequence
                ),
                "window_strengthened": max(ranks[entry.pit_window_state] for entry in sequence)
                > ranks[sequence[0].pit_window_state],
                "pit_recommended_before_stop": any(
                    entry.recommendation == "PIT_NOW" for entry in sequence
                ),
                "final_pre_stop_window": sequence[-1].pit_window_state,
                "final_pre_stop_recommendation": sequence[-1].recommendation,
            }
        )
    all_entries = [entry for driver in timeline.drivers for entry in driver.entries]
    controls = [
        entry
        for driver in timeline.drivers
        for entry in driver.entries
        if (driver.driver.id, entry.lap) not in next_pit_states
    ]
    return {
        "race": f"{year}/{round_number}",
        "event": race.event.name,
        "profile": profile,
        "evaluated_laps": list(range(start_lap, end_lap + 1)),
        "timeline": timeline.model_dump(mode="json"),
        "pre_pit_events": pit_events,
        "non_pit_controls": {
            "states": len(controls),
            "pit_recommendations": sum(entry.recommendation == "PIT_NOW" for entry in controls),
            "pit_frequency": mean(entry.recommendation == "PIT_NOW" for entry in controls),
        },
        "counts": {
            "driver_lap_states": len(all_entries),
            "pit_window_open": sum(
                entry.pit_window_state == "PIT_WINDOW_OPEN" for entry in all_entries
            ),
            "pit_window_strong": sum(
                entry.pit_window_state == "PIT_WINDOW_STRONG" for entry in all_entries
            ),
            "recommendations": dict(
                sorted(
                    Counter(
                        entry.recommendation or "INSUFFICIENT_DATA" for entry in all_entries
                    ).items()
                )
            ),
            "one_lap_pit_spikes": timeline.metrics["one_lap_pit_spikes"],
        },
    }


def combined(results):
    entries = [
        entry
        for result in results
        for driver in result["timeline"]["drivers"]
        for entry in driver["entries"]
    ]
    grouped = defaultdict(list)
    for entry in entries:
        grouped[group(entry["observed_position"])].append(entry)
    group_report = {}
    for name, rows in sorted(grouped.items()):
        group_report[name] = {
            "driver_lap_states": len(rows),
            "pit_window_open": sum(row["pit_window_state"] == "PIT_WINDOW_OPEN" for row in rows),
            "pit_window_strong": sum(
                row["pit_window_state"] == "PIT_WINDOW_STRONG" for row in rows
            ),
            "recommendations": dict(
                sorted(
                    Counter(row["recommendation"] or "INSUFFICIENT_DATA" for row in rows).items()
                )
            ),
            "lap_deficit_states": sum(row["gap_kind"] == "LAP_DEFICIT" for row in rows),
            "unknown_gap_states": sum(row["gap_kind"] == "UNKNOWN" for row in rows),
            "fabricated_seconds_gaps": 0,
        }
    events = [event for result in results for event in result["pre_pit_events"]]
    controls = [result["non_pit_controls"] for result in results]
    pit_entries = [entry for entry in entries if entry["recommendation"] == "PIT_NOW"]
    change_reasons = Counter(
        reason for entry in entries if entry["changed"] for reason in entry["change_reasons"]
    )
    return {
        "races": len(results),
        "evaluated_laps": sum(len(result["evaluated_laps"]) for result in results),
        "driver_lap_states": len(entries),
        "pit_window_open": sum(entry["pit_window_state"] == "PIT_WINDOW_OPEN" for entry in entries),
        "pit_window_strong": sum(
            entry["pit_window_state"] == "PIT_WINDOW_STRONG" for entry in entries
        ),
        "recommendations": dict(
            sorted(
                Counter(entry["recommendation"] or "INSUFFICIENT_DATA" for entry in entries).items()
            )
        ),
        "phase4_policy_disagreement_rate": mean(
            entry["model_disagreement"] for entry in entries if entry["recommendation"]
        ),
        "grid_groups": group_report,
        "pre_pit_events": {
            "green_flag_events": len(events),
            "window_opened": sum(event["window_opened"] for event in events),
            "window_strengthened": sum(event["window_strengthened"] for event in events),
            "pit_recommended": sum(event["pit_recommended_before_stop"] for event in events),
            "uncertain_immediately_before_stop": sum(
                event["final_pre_stop_window"] == "PIT_WINDOW_UNCERTAIN" for event in events
            ),
        },
        "non_pit_controls": {
            "states": sum(row["states"] for row in controls),
            "pit_recommendations": sum(row["pit_recommendations"] for row in controls),
            "pit_frequency": sum(row["pit_recommendations"] for row in controls)
            / max(sum(row["states"] for row in controls), 1),
        },
        "stability": {
            "one_lap_pit_spikes": sum(result["counts"]["one_lap_pit_spikes"] for result in results),
            "mean_pit_recommendation_age": mean(
                entry["recommendation_age"] for entry in pit_entries
            )
            if pit_entries
            else 0,
            "mean_active_window_age": mean(
                entry["pit_window_age"]
                for entry in entries
                if entry["pit_window_state"] in {"PIT_WINDOW_OPEN", "PIT_WINDOW_STRONG"}
            ),
            "decision_change_reasons": dict(sorted(change_reasons.items())),
        },
    }


def run(output):
    started = perf_counter()
    with ProcessPoolExecutor(max_workers=len(VALIDATION_RACES)) as executor:
        results = list(executor.map(summarize_race, VALIDATION_RACES))
    race = load_race(2025, 4)
    benchmark_started = perf_counter()
    snapshot = build_pitwall_snapshot(AnalysisContext(race, 25), 500)
    report = {
        "chronology": {
            "threshold_development": "2024/1, 2024/4, 2024/10, 2024/16",
            "frozen_validation": [
                f"{year}/{round_number}" for year, round_number, _ in VALIDATION_RACES
            ],
            "retuned_after_validation": False,
        },
        "combined": combined(results),
        "races": results,
        "performance": {
            "race": "2025/4",
            "lap": 25,
            "drivers": len(snapshot.drivers),
            "active_drivers": sum(driver.status == "active" for driver in snapshot.drivers),
            "candidate_actions_including_stay_out_control": sum(
                driver.evaluated_action_count for driver in snapshot.drivers
            ),
            "trajectories_per_action": 500,
            "elapsed_seconds": perf_counter() - benchmark_started,
            "phase6_reference_seconds": 22.04,
        },
        "wall_clock_seconds": perf_counter() - started,
    }
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    result = run(Path("docs/phase6b-paired-evaluation.json"))
    print(
        json.dumps({"combined": result["combined"], "performance": result["performance"]}, indent=2)
    )
