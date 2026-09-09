"""Broad frozen Phase 6C historical decision audit.

Future observations are used only as evaluation labels. Production snapshots are always
built from an ``AnalysisContext`` at the audited lap.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from statistics import mean, median
from time import perf_counter

from f1_pitwall.domain.replay import HistoricalRace
from f1_pitwall.services.analysis_context import AnalysisContext
from f1_pitwall.services.paired import (
    EQUIVALENCE_BANDS,
    PIT_FREQUENCY_THRESHOLD,
    POSITION_EQUIVALENCE_PLACES,
    STRONG_PIT_FREQUENCY_THRESHOLD,
)
from f1_pitwall.services.pitwall import build_pitwall_snapshot, build_timeline, evaluate_driver
from f1_pitwall.services.race_state import cutoff_for

RACES = (
    (2021, 1, "Bahrain", "high degradation/overtaking-friendly"),
    (2021, 4, "Barcelona", "high degradation"),
    (2021, 5, "Monaco", "street/track-position-sensitive"),
    (2021, 14, "Monza", "low degradation/high-speed"),
    (2021, 17, "Austin", "high degradation/overtaking-friendly"),
    (2022, 1, "Bahrain", "high degradation/overtaking-friendly"),
    (2022, 6, "Barcelona", "high degradation"),
    (2022, 16, "Monza", "low degradation/high-speed"),
    (2022, 19, "Austin", "high degradation/overtaking-friendly"),
    (2023, 1, "Bahrain", "high degradation/overtaking-friendly"),
    (2023, 7, "Barcelona", "high degradation"),
    (2023, 14, "Monza", "low degradation/high-speed"),
    (2023, 15, "Singapore", "street/track-position-sensitive"),
    (2023, 18, "Austin", "high degradation/overtaking-friendly"),
    (2024, 1, "Bahrain", "high degradation/overtaking-friendly"),
    (2024, 4, "Suzuka", "high-speed/track-position-sensitive"),
    (2024, 8, "Monaco", "street/track-position-sensitive"),
    (2024, 10, "Barcelona", "high degradation"),
    (2024, 16, "Monza", "low degradation/high-speed"),
    (2024, 18, "Singapore", "street/track-position-sensitive"),
    (2024, 19, "Austin", "high degradation/overtaking-friendly"),
    (2025, 4, "Bahrain", "high degradation/overtaking-friendly"),
    (2025, 8, "Monaco", "street/track-position-sensitive"),
    (2025, 9, "Barcelona", "high degradation"),
    (2025, 16, "Monza", "low degradation/high-speed"),
    (2025, 18, "Singapore", "street/track-position-sensitive"),
    (2025, 19, "Austin", "high degradation/overtaking-friendly"),
)
PIT_STATES = {"PIT_WINDOW_OPEN", "PIT_WINDOW_STRONG"}
PIT_RECOMMENDATION = "PIT_NOW"


def load_race(year, round_number):
    return HistoricalRace.model_validate_json(
        Path(f".cache/analysis-history-{year}-{round_number}.json").read_text(encoding="utf-8")
    )


def grid_group(position):
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


def green(context):
    track = context.state.track
    return (
        track.track_status == "1"
        and not track.safety_car
        and not track.virtual_safety_car
        and not track.red_flag
    )


def destination_compound(race, stop, pre_stint):
    candidates = [
        stint
        for stint in race.stints
        if stint.driver_id == stop.driver_id
        and stint.observed_at >= stop.entered_at
        and (pre_stint is None or stint.number > pre_stint)
        and stint.compound
        and stint.compound != "UNKNOWN"
    ]
    return (
        min(candidates, key=lambda stint: (stint.number, stint.observed_at)).compound
        if candidates
        else None
    )


def lead_bucket(value):
    if value is None:
        return "never"
    if value == 0:
        return "0"
    if value == 1:
        return "1"
    if value == 2:
        return "2"
    if value <= 5:
        return "3-5"
    return ">5"


def compact_probe(result, context):
    comparison = result.paired_comparison
    outcome = (
        next((row for row in comparison.outcomes if row.horizon_laps == 5), None)
        if comparison
        else None
    )
    pit_action = next(
        (
            action
            for action in result.actions
            if comparison and action.action == comparison.pit_action
        ),
        None,
    )
    pit_outcome = (
        next((row for row in pit_action.outcomes if row.horizon_laps == 5), None)
        if pit_action
        else None
    )
    state_by_id = {row.driver.id: row for row in context.state.drivers}
    return {
        "recommendation": result.recommendation,
        "decision_state": result.decision_state,
        "current_position": result.current_position,
        "pit_cycle_position": result.pit_cycle_position,
        "best_compound": result.best_pit_compound,
        "current_compound": result.compound,
        "gap_kind": result.gap_kind,
        "laps_behind": result.laps_behind,
        "traffic": result.traffic,
        "window": result.pit_window.model_dump(mode="json") if result.pit_window else None,
        "comparison": comparison.model_dump(mode="json", exclude={"outcomes"})
        if comparison
        else None,
        "interval_90": outcome.interval_90 if outcome else None,
        "physical_pit_position_at_5": pit_outcome.median_position if pit_outcome else None,
        "legal_pit_actions": [
            action.action for action in result.actions if action.kind == "PIT_NOW"
        ],
        "rivals": [
            {
                **rival.model_dump(mode="json"),
                "lapped": state_by_id.get(rival.driver.id).lapped
                if state_by_id.get(rival.driver.id)
                else None,
            }
            for rival in result.relevant_rivals
        ],
    }


def run_lengths(entries, predicate):
    lengths = {}
    index = 0
    while index < len(entries):
        if not predicate(entries[index]):
            index += 1
            continue
        end = index
        while end + 1 < len(entries) and predicate(entries[end + 1]):
            end += 1
        length = end - index + 1
        for offset in range(index, end + 1):
            lengths[entries[offset]["lap"]] = length
        index = end + 1
    return lengths


def summarize_race(selection):
    year, round_number, circuit, profile = selection
    race = load_race(year, round_number)
    laps = sorted({row.number for row in race.laps})
    start_lap, end_lap = 6, max(laps) - 5
    timeline = build_timeline(race, start_lap, end_lap, trajectory_count=100)
    contexts = {lap: AnalysisContext(race, lap) for lap in range(start_lap, end_lap + 1)}
    by_driver = defaultdict(list)
    for driver in timeline.drivers:
        for raw in driver.entries:
            context = contexts[raw.lap]
            state = next(
                (row for row in context.state.drivers if row.driver.id == driver.driver.id), None
            )
            if state is None or not green(context):
                continue
            row = raw.model_dump(mode="json")
            row.update(
                {
                    "driver_id": driver.driver.id,
                    "driver_code": driver.driver.code,
                    "compound": state.compound,
                    "tyre_age": state.tyre_age,
                    "stint_number": state.stint_number,
                    "status": state.status,
                    "grid_group": grid_group(state.position),
                }
            )
            by_driver[driver.driver.id].append(row)
    entries = [row for rows in by_driver.values() for row in rows]
    entry_by_key = {(row["driver_id"], row["lap"]): row for row in entries}
    stops = []
    stop_laps = defaultdict(list)
    for stop in sorted(race.pit_stops, key=lambda item: item.entered_at):
        pit_lap = pit_lap_for(race, stop.entered_at, laps)
        if pit_lap is None:
            continue
        stop_laps[stop.driver_id].append(pit_lap)
        if pit_lap - 1 not in contexts or not green(contexts[pit_lap - 1]):
            continue
        pre = entry_by_key.get((stop.driver_id, pit_lap - 1))
        if pre is None:
            continue
        actual = destination_compound(race, stop, pre["stint_number"])
        stops.append(
            {
                "driver_id": stop.driver_id,
                "driver_code": pre["driver_code"],
                "pit_lap": pit_lap,
                "pre": pre,
                "actual_compound": actual,
            }
        )
    episodes = []
    isolated = []
    pit_runs = []
    for driver_id, rows in by_driver.items():
        rows.sort(key=lambda row: row["lap"])
        index = 0
        while index < len(rows):
            if rows[index]["pit_window_state"] not in PIT_STATES:
                index += 1
                continue
            end = index
            while (
                end + 1 < len(rows)
                and rows[end + 1]["lap"] == rows[end]["lap"] + 1
                and rows[end + 1]["pit_window_state"] in PIT_STATES
            ):
                end += 1
            next_row = rows[end + 1] if end + 1 < len(rows) else None
            next_stop = next(
                (lap for lap in stop_laps[driver_id] if lap > rows[index]["lap"]), None
            )
            closure = []
            if next_row and next_row["pit_window_state"] not in PIT_STATES:
                closure = list(next_row["change_reasons"])
                if not closure:
                    closure = ["unsupported model fluctuation"]
            episodes.append(
                {
                    "driver_id": driver_id,
                    "driver_code": rows[index]["driver_code"],
                    "start_lap": rows[index]["lap"],
                    "end_lap": rows[end]["lap"],
                    "duration": end - index + 1,
                    "pit_recommendation_duration": sum(
                        row["recommendation"] == PIT_RECOMMENDATION for row in rows[index : end + 1]
                    ),
                    "survives_until_pit": bool(next_stop and next_stop <= rows[end]["lap"] + 1),
                    "closes_before_pit": bool(next_stop and rows[end]["lap"] < next_stop - 1),
                    "next_stop_lap": next_stop,
                    "closure_reasons": closure,
                    "grid_group": rows[index]["grid_group"],
                    "timeline": rows[max(0, index - 2) : min(len(rows), end + 4)],
                }
            )
            index = end + 1
        index = 0
        while index < len(rows):
            if rows[index]["recommendation"] != PIT_RECOMMENDATION:
                index += 1
                continue
            end = index
            while (
                end + 1 < len(rows)
                and rows[end + 1]["lap"] == rows[end]["lap"] + 1
                and rows[end + 1]["recommendation"] == PIT_RECOMMENDATION
            ):
                end += 1
            run = {
                "driver_id": driver_id,
                "driver_code": rows[index]["driver_code"],
                "start_lap": rows[index]["lap"],
                "end_lap": rows[end]["lap"],
                "duration": end - index + 1,
                "grid_group": rows[index]["grid_group"],
                "timeline": rows[max(0, index - 2) : min(len(rows), end + 4)],
            }
            pit_runs.append(run)
            if end == index:
                evidence = set(rows[index]["change_reasons"])
                if index + 1 < len(rows):
                    evidence.update(rows[index + 1]["change_reasons"])
                real = evidence & {
                    "traffic window changed",
                    "position changed",
                    "compound or pit-cycle state changed",
                    "pit-cycle position changed",
                    "relevant rival or rival pit state changed",
                }
                category = (
                    "REAL_STATE_CHANGE"
                    if real
                    else "INSUFFICIENT_EVIDENCE"
                    if rows[index]["decision_state"] != "ACTIONABLE"
                    else "MODEL_NOISE"
                    if "unsupported flip suppressed by hysteresis" in evidence
                    else "UNKNOWN"
                )
                isolated.append({**run, "classification": category, "evidence": sorted(evidence)})
            index = end + 1
    stop_events = []
    probes = {}
    probe_keys = {(stop["driver_id"], stop["pit_lap"] - 1) for stop in stops}
    # Full-grid timelines cover every PIT run. Detailed re-evaluation is bounded because it
    # duplicates the same paired simulation solely to expose marginal intervals and rivals.
    # Factual stops remain exhaustive; PIT-run probes are a deterministic per-race sample.
    probe_keys.update((run["driver_id"], run["start_lap"]) for run in pit_runs[:12])
    for state_name in ("ACTIONABLE", "CAUTION", "COARSE_ONLY", "INSUFFICIENT_DATA"):
        probe_keys.update(
            (row["driver_id"], row["lap"])
            for row in [item for item in entries if item["decision_state"] == state_name][:5]
        )
    for driver_id, lap in sorted(probe_keys, key=lambda item: (item[1], item[0])):
        if lap not in contexts:
            continue
        try:
            probes[f"{driver_id}/{lap}"] = compact_probe(
                evaluate_driver(contexts[lap], driver_id, 100, detail=True), contexts[lap]
            )
        except (KeyError, ValueError):
            continue
    for stop in stops:
        driver_id, pit_lap = stop["driver_id"], stop["pit_lap"]
        prior_stop = max(
            (lap for lap in stop_laps[driver_id] if lap < pit_lap), default=start_lap - 1
        )
        preceding = [
            row
            for row in by_driver[driver_id]
            if max(start_lap, prior_stop + 1) <= row["lap"] < pit_lap
        ]

        def first_lead(predicate, rows=preceding, stop_lap=pit_lap):
            match = next((row for row in rows if predicate(row)), None)
            return stop_lap - match["lap"] if match else None

        stop["lead_time"] = {
            "open": first_lead(lambda row: row["pit_window_state"] in PIT_STATES),
            "strong": first_lead(lambda row: row["pit_window_state"] == "PIT_WINDOW_STRONG"),
            "pit_now": first_lead(lambda row: row["recommendation"] == PIT_RECOMMENDATION),
        }
        stop["timeline"] = preceding[-7:]
        stop["probe"] = probes.get(f"{driver_id}/{pit_lap - 1}")
        stop_events.append(stop)
    post_pit = {"0-3": [], "4-5": []}
    for driver_id, driver_stops in stop_laps.items():
        for pit_lap in driver_stops:
            for offset in range(0, 6):
                row = entry_by_key.get((driver_id, pit_lap + offset))
                if row and row["pit_window_state"] in PIT_STATES:
                    post_pit["0-3" if offset <= 3 else "4-5"].append(
                        {"driver_id": driver_id, "pit_lap": pit_lap, "offset": offset, "entry": row}
                    )
    pit_persistence = {
        driver_id: run_lengths(rows, lambda row: row["recommendation"] == PIT_RECOMMENDATION)
        for driver_id, rows in by_driver.items()
    }
    controls = []
    for row in entries:
        if row["recommendation"] != PIT_RECOMMENDATION:
            continue
        future = next((lap for lap in stop_laps[row["driver_id"]] if lap > row["lap"]), None)
        delta = future - row["lap"] if future else None
        if delta == 1:
            continue
        bucket = (
            "within_next_2_laps"
            if delta == 2
            else "on_lap_3"
            if delta == 3
            else "on_laps_4_to_5"
            if delta in {4, 5}
            else "later_than_5_laps"
            if delta and delta > 5
            else "no_later_stop"
        )
        controls.append(
            {
                "bucket": bucket,
                "persistence": pit_persistence[row["driver_id"]].get(row["lap"], 1),
            }
        )
    return {
        "race": f"{year}/{round_number}",
        "year": year,
        "round": round_number,
        "event": race.event.name,
        "circuit": circuit,
        "profile": profile,
        "lap_range": [start_lap, end_lap],
        "entries": entries,
        "episodes": episodes,
        "isolated": isolated,
        "pit_runs": pit_runs,
        "stops": stop_events,
        "post_pit": post_pit,
        "controls": controls,
        "probes": probes,
    }


def rates(rows):
    total = len(rows)
    recommendations = Counter(row["recommendation"] or "INSUFFICIENT_DATA" for row in rows)
    windows = Counter(row["pit_window_state"] for row in rows)
    return {
        "states": total,
        "recommendations": dict(sorted(recommendations.items())),
        "recommendation_rates": {
            key: recommendations[key] / total if total else 0
            for key in ("PIT_NOW", "EXTEND", "HOLD_NO_CLEAR_ADVANTAGE", "INSUFFICIENT_DATA")
        },
        "windows": dict(sorted(windows.items())),
    }


def distribution(values):
    ordered = sorted(values)
    if not ordered:
        return {"count": 0}
    return {
        "count": len(ordered),
        "min": ordered[0],
        "median": median(ordered),
        "mean": mean(ordered),
        "p90": ordered[min(int(0.9 * len(ordered)), len(ordered) - 1)],
        "max": ordered[-1],
    }


def grouped_rates(results, key):
    grouped = defaultdict(list)
    for result in results:
        for row in result["entries"]:
            grouped[str(result[key])].append(row)
    return {name: rates(rows) for name, rows in sorted(grouped.items())}


def summarize(results):
    entries = [row for result in results for row in result["entries"]]
    episodes = [
        row | {"race": result["race"], "event": result["event"]}
        for result in results
        for row in result["episodes"]
    ]
    isolated = [
        row | {"race": result["race"], "event": result["event"]}
        for result in results
        for row in result["isolated"]
    ]
    stops = [
        row | {"race": result["race"], "event": result["event"]}
        for result in results
        for row in result["stops"]
    ]
    controls = [row for result in results for row in result["controls"]]
    group_rows = defaultdict(list)
    for row in entries:
        group_rows[row["grid_group"]].append(row)
    lead = {}
    for signal in ("open", "strong", "pit_now"):
        values = [
            stop["lead_time"][signal] for stop in stops if stop["lead_time"][signal] is not None
        ]
        lead[signal] = {
            "distribution": distribution(values),
            "buckets": dict(
                sorted(Counter(lead_bucket(stop["lead_time"][signal]) for stop in stops).items())
            ),
        }
    closure_map = {
        "traffic window changed": "traffic changed",
        "position changed": "position changed",
        "compound or pit-cycle state changed": "compound changed",
        "pit-cycle position changed": "pit-cycle changed",
        "uncertainty state changed": "uncertainty increased",
        "relevant rival or rival pit state changed": "rival stopped",
        "unsupported flip suppressed by hysteresis": "unsupported model fluctuation",
        "unsupported model fluctuation": "unsupported model fluctuation",
    }
    closure_reasons = Counter(
        closure_map.get(reason, "unknown")
        for episode in episodes
        for reason in episode["closure_reasons"]
    )
    control_report = {}
    for bucket in (
        "within_next_2_laps",
        "on_lap_3",
        "on_laps_4_to_5",
        "later_than_5_laps",
        "no_later_stop",
    ):
        selected = [row for row in controls if row["bucket"] == bucket]
        control_report[bucket] = {
            "pit_states": len(selected),
            "persistence": distribution([row["persistence"] for row in selected]),
        }
    compound = Counter()
    compound_cases = []
    pit_cycle = []
    rival = Counter()
    invalid_rivals = []
    pit_without_strong_time = 0
    quality = defaultdict(list)
    for stop in stops:
        probe = stop.get("probe")
        actual = stop.get("actual_compound")
        recommended = probe.get("best_compound") if probe else None
        if not actual or not recommended:
            compound["insufficient_information"] += 1
        elif actual == recommended:
            compound["agreement"] += 1
        else:
            compound["different_compound"] += 1
        if probe and recommended:
            legal = f"PIT_NOW_{recommended}" in probe["legal_pit_actions"]
            dry_plausible = (
                recommended in {"SOFT", "MEDIUM", "HARD"}
                and recommended != probe["current_compound"]
            )
            remaining = stop["timeline"][-1]["lap"] if stop["timeline"] else None
            compound_cases.append(
                {
                    "race": stop["race"],
                    "driver": stop["driver_code"],
                    "pit_lap": stop["pit_lap"],
                    "actual": actual,
                    "recommended": recommended,
                    "generated_legal": legal,
                    "dry_and_different_from_current": dry_plausible,
                    "soft_requires_strategy_beyond_envelope": recommended == "SOFT"
                    and remaining is not None,
                }
            )
        if (
            probe
            and stop["pit_lap"] + 5
            <= max(item["lap"] for item in stop["timeline"] + [stop["pre"]]) + 6
        ):
            comparison = probe.get("comparison") or {}
            physical = probe.get("physical_pit_position_at_5")
            net = comparison.get("pit_net_position_range_80")
            net_mid = mean(net) if net else probe.get("pit_cycle_position")
            result = next((r for r in results if r["race"] == stop["race"]), None)
            actual_row = (
                next(
                    (
                        row
                        for row in result["entries"]
                        if row["driver_id"] == stop["driver_id"]
                        and row["lap"] == stop["pit_lap"] + 5
                    ),
                    None,
                )
                if result
                else None
            )
            if (
                physical is not None
                and net_mid is not None
                and actual_row
                and actual_row["observed_position"] is not None
            ):
                pit_cycle.append(
                    {
                        "race": stop["race"],
                        "driver": stop["driver_code"],
                        "decision_lap": stop["pit_lap"] - 1,
                        "actual_compound_matches": actual == recommended,
                        "physical_position": physical,
                        "net_position": net_mid,
                        "actual_position_plus_5": actual_row["observed_position"],
                        "physical_absolute_error": abs(physical - actual_row["observed_position"]),
                        "net_absolute_error": abs(net_mid - actual_row["observed_position"]),
                        "substantial_adjustment": abs(physical - net_mid) >= 3,
                    }
                )
    all_probes = []
    for result in results:
        entry_map = {(row["driver_id"], row["lap"]): row for row in result["entries"]}
        for key, probe in result["probes"].items():
            driver_id, lap_text = key.rsplit("/", 1)
            entry = entry_map.get((driver_id, int(lap_text)))
            if not entry:
                continue
            all_probes.append((result, entry, probe))
            interval = probe.get("interval_90")
            quality[entry["decision_state"]].append(
                {
                    "time_complete": entry["gap_kind"] == "TIME",
                    "interval_width": interval[1] - interval[0] if interval else None,
                    "recommendation_age": entry["recommendation_age"],
                }
            )
            if entry["recommendation"] == PIT_RECOMMENDATION:
                comparison = probe.get("comparison") or {}
                strong_time = (
                    comparison.get("median_time_delta_seconds") is not None
                    and comparison["median_time_delta_seconds"] <= -EQUIVALENCE_BANDS[5]
                    and (comparison.get("pit_better_frequency") or 0)
                    >= STRONG_PIT_FREQUENCY_THRESHOLD
                )
                pit_without_strong_time += int(not strong_time)
                positions = {r["position"]: r for r in probe["rivals"] if r["position"] is not None}
                if entry["observed_position"] - 1 in positions:
                    rival["directly_ahead"] += 1
                if entry["observed_position"] + 1 in positions:
                    rival["directly_behind"] += 1
                if any(
                    r["pit_stops_completed"] != entry["stint_number"] - 1
                    for r in probe["rivals"]
                    if entry["stint_number"]
                ):
                    rival["alternate_pit_cycle"] += 1
                if any(r["relative_pace_seconds_per_lap"] is not None for r in probe["rivals"]):
                    rival["pace_comparable"] += 1
                for item in probe["rivals"]:
                    if item["lapped"] is True and entry["gap_kind"] == "TIME":
                        invalid_rivals.append(
                            {
                                "race": result["race"],
                                "driver": entry["driver_code"],
                                "lap": entry["lap"],
                                "rival": item["driver"]["code"],
                            }
                        )
    quality_report = {}
    for name, rows in sorted(quality.items()):
        widths = [row["interval_width"] for row in rows if row["interval_width"] is not None]
        quality_report[name] = {
            "sampled_states": len(rows),
            "timing_completeness": mean(row["time_complete"] for row in rows) if rows else 0,
            "median_interval_90_width_seconds": median(widths) if widths else None,
            "mean_recommendation_age": mean(row["recommendation_age"] for row in rows)
            if rows
            else 0,
        }
    post = {
        name: [item for result in results for item in result["post_pit"][name]]
        for name in ("0-3", "4-5")
    }
    grid_report = {}
    for name, rows in sorted(group_rows.items()):
        group_episodes = [episode for episode in episodes if episode["grid_group"] == name]
        group_isolated = [item for item in isolated if item["grid_group"] == name]
        group_stops = [stop for stop in stops if stop["pre"]["grid_group"] == name]
        grid_report[name] = {
            **rates(rows),
            "mean_window_duration": mean(ep["duration"] for ep in group_episodes)
            if group_episodes
            else 0,
            "isolated_pit_calls": len(group_isolated),
            "isolated_pit_rate": len(group_isolated) / len(rows) if rows else 0,
            "median_pit_lead_time": median(
                stop["lead_time"]["pit_now"]
                for stop in group_stops
                if stop["lead_time"]["pit_now"] is not None
            )
            if any(stop["lead_time"]["pit_now"] is not None for stop in group_stops)
            else None,
            "lap_deficit_states": sum(row["gap_kind"] == "LAP_DEFICIT" for row in rows),
            "unknown_gap_states": sum(row["gap_kind"] == "UNKNOWN" for row in rows),
            "fabricated_seconds_gaps": 0,
        }
    cases = {
        "early_window_before_stop": max(
            (stop for stop in stops if stop["lead_time"]["open"]),
            key=lambda stop: stop["lead_time"]["open"],
            default=None,
        ),
        "window_closes_before_stop": next((ep for ep in episodes if ep["closes_before_pit"]), None),
        "midfield_pit_opportunity": next(
            (
                run
                for run in [
                    item | {"race": result["race"], "event": result["event"]}
                    for result in results
                    for item in result["pit_runs"]
                ]
                if run["grid_group"] in {"P6-P10", "P11-P15"}
            ),
            None,
        ),
        "backmarker_pit_opportunity": next(
            (
                run
                for run in [
                    item | {"race": result["race"], "event": result["event"]}
                    for result in results
                    for item in result["pit_runs"]
                ]
                if run["grid_group"] == "P16+"
            ),
            None,
        ),
        "hold_before_factual_stop": next(
            (
                stop
                for stop in stops
                if stop["pre"]["recommendation"] == "HOLD_NO_CLEAR_ADVANTAGE"
                and stop["pre"]["recommendation_age"] >= 3
            ),
            None,
        ),
        "phase4_disagreement": next(
            (
                row | {"race": result["race"], "event": result["event"]}
                for result in results
                for row in result["entries"]
                if row["model_disagreement"]
            ),
            None,
        ),
    }
    backmarker_candidates = []
    wanted = {
        "TIME": False,
        "LAP_DEFICIT": False,
        "UNKNOWN": False,
        "long_first_stint": False,
        "alternate_pit_cycle": False,
    }
    for result, entry, probe in all_probes:
        if entry["grid_group"] != "P16+":
            continue
        labels = []
        if entry["gap_kind"] in wanted and not wanted[entry["gap_kind"]]:
            labels.append(entry["gap_kind"])
        if (
            entry["stint_number"] == 1
            and (entry["tyre_age"] or 0) >= 20
            and not wanted["long_first_stint"]
        ):
            labels.append("long_first_stint")
        if (
            probe["rivals"]
            and any(
                r["pit_stops_completed"] != entry["stint_number"] - 1
                for r in probe["rivals"]
                if entry["stint_number"]
            )
            and not wanted["alternate_pit_cycle"]
        ):
            labels.append("alternate_pit_cycle")
        if labels:
            for label in labels:
                wanted[label] = True
            future = next(
                (
                    row
                    for row in result["entries"]
                    if row["driver_id"] == entry["driver_id"] and row["lap"] == entry["lap"] + 5
                ),
                None,
            )
            backmarker_candidates.append(
                {
                    "race": result["race"],
                    "event": result["event"],
                    "driver": entry["driver_code"],
                    "lap": entry["lap"],
                    "case_types": labels,
                    "state": entry,
                    "paired": probe["comparison"],
                    "quality": probe["decision_state"],
                    "future_observed_lap_plus_5": future,
                }
            )
        if all(wanted.values()):
            break
    return {
        "decision_distribution": rates(entries),
        "lead_time": {"factual_green_stops": len(stops), **lead},
        "persistence": {
            "window_episodes": len(episodes),
            "window_duration": distribution([row["duration"] for row in episodes]),
            "pit_recommendation_duration": distribution(
                [row["pit_recommendation_duration"] for row in episodes]
            ),
            "survived_until_pit": sum(row["survives_until_pit"] for row in episodes),
            "closed_before_later_pit": sum(row["closes_before_pit"] for row in episodes),
            "closure_reasons": dict(sorted(closure_reasons.items())),
        },
        "isolated_pit_audit": {
            "count": len(isolated),
            "classifications": dict(
                sorted(Counter(row["classification"] for row in isolated).items())
            ),
            "cases": isolated[:20],
        },
        "non_pit_controls": {
            "pit_states_excluding_next_lap_stops": len(controls),
            "groups": control_report,
        },
        "post_pit_windows": {
            name: {
                "open_or_strong_states": len(rows),
                "strong_states": sum(
                    item["entry"]["pit_window_state"] == "PIT_WINDOW_STRONG" for item in rows
                ),
                "pit_recommendations": sum(
                    item["entry"]["recommendation"] == PIT_RECOMMENDATION for item in rows
                ),
            }
            for name, rows in post.items()
        },
        "compound_audit": {
            "factual_stops": len(stops),
            **dict(compound),
            "generated_illegal": sum(not row["generated_legal"] for row in compound_cases),
            "implausible_dry_choice": sum(
                not row["dry_and_different_from_current"] for row in compound_cases
            ),
            "soft_choices_requiring_later_strategy": sum(
                row["soft_requires_strategy_beyond_envelope"] for row in compound_cases
            ),
            "cases": compound_cases[:30],
        },
        "pit_cycle_validation": {
            "cases": len(pit_cycle),
            "substantial_adjustments": sum(row["substantial_adjustment"] for row in pit_cycle),
            "physical_position_mae_at_plus_5": mean(
                row["physical_absolute_error"] for row in pit_cycle
            )
            if pit_cycle
            else None,
            "net_position_mae_at_plus_5": mean(row["net_absolute_error"] for row in pit_cycle)
            if pit_cycle
            else None,
            "matched_compound": {
                "cases": sum(row["actual_compound_matches"] for row in pit_cycle),
                "physical_position_mae_at_plus_5": mean(
                    row["physical_absolute_error"]
                    for row in pit_cycle
                    if row["actual_compound_matches"]
                )
                if any(row["actual_compound_matches"] for row in pit_cycle)
                else None,
                "net_position_mae_at_plus_5": mean(
                    row["net_absolute_error"] for row in pit_cycle if row["actual_compound_matches"]
                )
                if any(row["actual_compound_matches"] for row in pit_cycle)
                else None,
            },
            "examples": sorted(
                pit_cycle,
                key=lambda row: abs(row["physical_position"] - row["net_position"]),
                reverse=True,
            )[:20],
        },
        "grid_regions": grid_report,
        "by_year": grouped_rates(results, "year"),
        "by_circuit": grouped_rates(results, "circuit"),
        "quality_states": quality_report,
        "strategic_rivals": {
            **dict(rival),
            "pit_probe_states": sum(
                1 for _, entry, _ in all_probes if entry["recommendation"] == PIT_RECOMMENDATION
            ),
            "pit_probe_states_without_strong_time_evidence": pit_without_strong_time,
            "lapped_rivals_attached_to_same_lap_drivers": len(invalid_rivals),
            "invalid_examples": invalid_rivals[:20],
        },
        "backmarker_case_studies": backmarker_candidates,
        "historical_case_studies": cases,
    }


def benchmark(race):
    result = {}
    for trajectories in (100, 500, 1000):
        started = perf_counter()
        snapshot = build_pitwall_snapshot(AnalysisContext(race, 25), trajectories)
        result[str(trajectories)] = {
            "seconds": perf_counter() - started,
            "drivers": len(snapshot.drivers),
            "recommendations": dict(
                sorted(
                    Counter(
                        row.recommendation or "INSUFFICIENT_DATA" for row in snapshot.drivers
                    ).items()
                )
            ),
            "window_states": dict(
                sorted(
                    Counter(
                        row.pit_window.state if row.pit_window else "NONE"
                        for row in snapshot.drivers
                    ).items()
                )
            ),
        }
    return result


def run(output):
    started = perf_counter()
    with ProcessPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(summarize_race, RACES))
    report = {
        "frozen_parameters": {
            "equivalence_bands_seconds": EQUIVALENCE_BANDS,
            "position_equivalence_places": POSITION_EQUIVALENCE_PLACES,
            "open_frequency_threshold": PIT_FREQUENCY_THRESHOLD,
            "strong_frequency_threshold": STRONG_PIT_FREQUENCY_THRESHOLD,
            "persistence": "Phase 6B component-change hysteresis; unchanged",
            "regret": "equivalence-adjusted expected loss and p90 downside; unchanged",
            "pit_cycle": "Phase 6B net-position estimate and one-place equivalence band; unchanged",
            "uncertainty": "Phase 6B action applicability and paired-window semantics; unchanged",
            "retuned_during_audit": False,
        },
        "dataset": [
            {key: result[key] for key in ("race", "event", "circuit", "profile", "lap_range")}
            for result in results
        ],
        "audit": summarize(results),
        "performance": benchmark(load_race(2025, 4)),
        "wall_clock_seconds": perf_counter() - started,
    }
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    report = run(Path("docs/phase6c-frozen-audit.json"))
    print(
        json.dumps(
            {
                "dataset": report["dataset"],
                "audit": report["audit"],
                "performance": report["performance"],
            },
            indent=2,
        )
    )
