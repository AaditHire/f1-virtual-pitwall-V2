"""Small chronological Phase 6E hybrid strategic-model validation."""

from __future__ import annotations

import json
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from statistics import mean, median
from time import perf_counter

from audit_phase6c import destination_compound, green, grid_group, load_race, pit_lap_for

from f1_pitwall.services.analysis_context import AnalysisContext
from f1_pitwall.services.strategic_stint import compare_strategic_actions

VALIDATION_RACES = (
    (2024, 1, "Bahrain"),
    (2024, 10, "Barcelona"),
    (2024, 16, "Monza"),
    (2024, 18, "Singapore"),
)
MAX_STOPS_PER_RACE = 6
CHECKPOINT_LEADS = (5, 3, 1)
FORECAST_HORIZONS = (10, 15, 20)


def _driver(context, driver_id):
    return next((row for row in context.state.drivers if row.driver.id == driver_id), None)


def _valid(context, driver_id):
    driver = _driver(context, driver_id)
    return driver if green(context) and driver is not None and driver.status == "active" else None


def _select_stops(stops):
    grouped = defaultdict(list)
    for stop in stops:
        grouped[stop["grid_group"]].append(stop)
    selected = []
    groups = ("P1-P5", "P6-P10", "P11-P15", "P16+")
    while len(selected) < MAX_STOPS_PER_RACE:
        added = False
        for group in groups:
            if grouped[group] and len(selected) < MAX_STOPS_PER_RACE:
                selected.append(grouped[group].pop(0))
                added = True
        if not added:
            break
    return selected


def _actual_delta(contexts, driver_id, lap, horizon):
    current = _driver(contexts[lap], driver_id)
    future_context = contexts.get(lap + horizon)
    future = _driver(future_context, driver_id) if future_context else None
    if current is None or future is None:
        return {"timing_delta_seconds": None, "position_delta": None}
    timed = (
        not current.lapped
        and not future.lapped
        and current.gap_to_leader is not None
        and future.gap_to_leader is not None
    )
    return {
        "timing_delta_seconds": round(future.gap_to_leader - current.gap_to_leader, 3)
        if timed
        else None,
        "position_delta": future.position - current.position
        if current.position is not None and future.position is not None
        else None,
    }


def _actual_break_even(contexts, driver_id, checkpoint, terminal):
    current = _driver(contexts[checkpoint], driver_id)
    if current is None or current.lapped or current.gap_to_leader is None:
        return None
    for lap in range(checkpoint + 1, terminal + 1):
        future = _driver(contexts.get(lap), driver_id) if contexts.get(lap) else None
        if (
            future is not None
            and not future.lapped
            and future.gap_to_leader is not None
            and future.gap_to_leader <= current.gap_to_leader
        ):
            return lap
    return None


def _matching_action(comparison, compound, lead):
    target_delay = {1: 0, 3: 2, 5: 5}[lead]
    candidates = [action for action in comparison.actions if action.delay_laps == target_delay]
    exact = [action for action in candidates if action.compound == compound]
    return min(
        exact or candidates,
        key=lambda action: (
            action.terminal_expected_seconds
            if action.terminal_expected_seconds is not None
            else 1e9
        ),
        default=None,
    )


def _fixed_delay_action(comparison, compound):
    candidates = [action for action in comparison.actions if action.delay_laps == 5]
    exact = [action for action in candidates if action.compound == compound]
    return min(
        exact or candidates,
        key=lambda action: (
            action.terminal_expected_seconds
            if action.terminal_expected_seconds is not None
            else 1e9
        ),
        default=None,
    )


def _generic_forecast(comparison, lead, horizon):
    current = comparison.components.get("current_relative_pace_seconds_per_lap")
    pit_loss = comparison.components.get("pit_loss_seconds")
    if current is None or pit_loss is None:
        return None
    delay = {1: 0, 3: 2, 5: 5}[lead]
    return current * delay + pit_loss + (horizon - delay) * (current - 1.1)


def _circuit_median_forecast(comparison, action, horizon):
    if action is None:
        return None
    prior = next(
        (row for row in comparison.compound_priors if row.compound == action.compound), None
    )
    current = comparison.components.get("current_relative_pace_seconds_per_lap")
    pit_loss = comparison.components.get("pit_loss_seconds")
    historical = prior.components.get("historical_compound_median_relative_pace") if prior else None
    if current is None or pit_loss is None or historical is None:
        return None
    return current * action.delay_laps + pit_loss + (horizon - action.delay_laps) * historical


def _compact(comparison):
    payload = comparison.model_dump(mode="json")
    for action in payload["actions"]:
        action["components"].pop("all_horizon_values", None)
        action["components"].pop("all_horizon_ranges", None)
    return payload


def _signal(value):
    if value is None:
        return "UNKNOWN"
    if value < -2:
        return "EARLIER_STOP_ADVANTAGE"
    if value > 2:
        return "LATER_STOP_ADVANTAGE"
    return "EQUIVALENT"


def _split_cases(selected, evaluations, contexts, last_lap):
    by_stop = {row["stop_id"]: row for row in evaluations if row["lead_laps"] == 1}
    cases = []
    for left_index, left in enumerate(selected):
        for right in selected[left_index + 1 :]:
            earlier, later = sorted((left, right), key=lambda row: row["pit_lap"])
            difference = later["pit_lap"] - earlier["pit_lap"]
            if (
                not 2 <= difference <= 10
                or earlier["compound"] != later["compound"]
                or earlier["grid_group"] != later["grid_group"]
            ):
                continue
            evaluation = by_stop.get(
                next(
                    (key for key in by_stop if key.endswith(f"/{selected.index(earlier)}")),
                    "",
                )
            )
            if evaluation is None:
                continue
            actions = evaluation["comparison"]["actions"]
            pit_now = next(
                (
                    row
                    for row in actions
                    if row["delay_laps"] == 0 and row["compound"] == earlier["compound"]
                ),
                None,
            )
            delayed = min(
                (
                    row
                    for row in actions
                    if row["delay_laps"] > 0 and row["compound"] == earlier["compound"]
                ),
                key=lambda row: abs(row["delay_laps"] - difference),
                default=None,
            )
            if pit_now is None or delayed is None:
                continue
            initial_context = contexts[earlier["pit_lap"] - 1]
            future_lap = min(last_lap, later["pit_lap"] + 10)
            future_context = contexts.get(future_lap)
            initial_early = _driver(initial_context, earlier["driver_id"])
            initial_late = _driver(initial_context, later["driver_id"])
            future_early = _driver(future_context, earlier["driver_id"]) if future_context else None
            future_late = _driver(future_context, later["driver_id"]) if future_context else None
            drivers = (initial_early, initial_late, future_early, future_late)
            if any(
                row is None or row.lapped or row.gap_to_leader is None or row.status != "active"
                for row in drivers
            ):
                continue
            initial_gap = initial_early.gap_to_leader - initial_late.gap_to_leader
            future_gap = future_early.gap_to_leader - future_late.gap_to_leader
            predicted_delta = (
                pit_now["terminal_expected_seconds"] - delayed["terminal_expected_seconds"]
            )
            observed_delta = future_gap - initial_gap
            predicted = _signal(predicted_delta)
            observed = _signal(observed_delta)
            cases.append(
                {
                    "earlier_driver": earlier["driver_code"],
                    "later_driver": later["driver_code"],
                    "earlier_pit_lap": earlier["pit_lap"],
                    "later_pit_lap": later["pit_lap"],
                    "compound": earlier["compound"],
                    "grid_group": earlier["grid_group"],
                    "model_delayed_action": delayed["action"],
                    "predicted_terminal_delta_seconds": predicted_delta,
                    "observed_pairwise_gap_change_seconds": observed_delta,
                    "predicted_label": predicted,
                    "observed_label": observed,
                    "direction_agreement": predicted == observed,
                    "future_label_lap": future_lap,
                    "warning": (
                        "Pairwise gap evolution is a noisy label, not counterfactual ground truth."
                    ),
                }
            )
    return cases


def evaluate_race(spec):
    year, round_number, circuit = spec
    race = load_race(year, round_number)
    laps = sorted({row.number for row in race.laps})
    contexts = {lap: AnalysisContext(race, lap) for lap in range(6, max(laps) + 1)}
    stops = []
    for stop in sorted(race.pit_stops, key=lambda row: row.entered_at):
        pit_lap = pit_lap_for(race, stop.entered_at, laps)
        if pit_lap is None or not all(
            lap in contexts and _valid(contexts[lap], stop.driver_id)
            for lap in (pit_lap - 5, pit_lap - 3, pit_lap - 1)
        ):
            continue
        pre = _driver(contexts[pit_lap - 1], stop.driver_id)
        compound = destination_compound(race, stop, pre.stint_number)
        if compound not in {"SOFT", "MEDIUM", "HARD"}:
            continue
        stops.append(
            {
                "driver_id": stop.driver_id,
                "driver_code": pre.driver.code,
                "pit_lap": pit_lap,
                "compound": compound,
                "grid_group": grid_group(pre.position),
            }
        )
    selected = _select_stops(stops)
    evaluations = []
    started = perf_counter()
    for stop_index, stop in enumerate(selected):
        for lead in CHECKPOINT_LEADS:
            lap = stop["pit_lap"] - lead
            comparison = compare_strategic_actions(
                contexts[lap], stop["driver_id"], 100, 6100 + lap
            )
            matching = _matching_action(comparison, stop["compound"], lead)
            fixed = _fixed_delay_action(comparison, stop["compound"])
            labels = {
                horizon: _actual_delta(contexts, stop["driver_id"], lap, horizon)
                for horizon in FORECAST_HORIZONS
                if lap + horizon <= max(laps)
            }
            forecasts = {}
            for horizon, actual in labels.items():
                forecasts[horizon] = {
                    "actual": actual,
                    "generic_fresh_advantage": _generic_forecast(comparison, lead, horizon),
                    "fixed_delay_5": fixed.cumulative_value_seconds.get(horizon) if fixed else None,
                    "historical_circuit_median": _circuit_median_forecast(
                        comparison, matching, horizon
                    ),
                    "strategic_hybrid": matching.cumulative_value_seconds.get(horizon)
                    if matching
                    else None,
                }
            pit_now = [action for action in comparison.actions if action.delay_laps == 0]
            evaluations.append(
                {
                    "stop_id": f"{year}/{round_number}/{stop_index}",
                    **stop,
                    "checkpoint_lap": lap,
                    "lead_laps": lead,
                    "actual_break_even_lap": _actual_break_even(
                        contexts, stop["driver_id"], lap, comparison.terminal_lap
                    ),
                    "predicted_pit_now_break_even_laps": {
                        action.compound: action.break_even_lap for action in pit_now
                    },
                    "matching_action": matching.action if matching else None,
                    "forecasts": forecasts,
                    "comparison": _compact(comparison),
                }
            )
    split_cases = _split_cases(selected, evaluations, contexts, max(laps))
    return {
        "race": f"{year}/{round_number}",
        "event": race.event.name,
        "circuit": circuit,
        "selected_stops": len(selected),
        "evaluations": evaluations,
        "split_cases": split_cases,
        "runtime_seconds": perf_counter() - started,
    }


def _errors(rows, baseline, horizon):
    pairs = []
    for row in rows:
        forecast = row["forecasts"].get(str(horizon)) or row["forecasts"].get(horizon)
        if not forecast:
            continue
        predicted = forecast.get(baseline)
        actual = forecast["actual"]["timing_delta_seconds"]
        if predicted is not None and actual is not None:
            pairs.append(abs(predicted - actual))
    return {
        "count": len(pairs),
        "mae_seconds": mean(pairs) if pairs else None,
        "median_absolute_error_seconds": median(pairs) if pairs else None,
    }


def summarize(results):
    rows = [row for result in results for row in result["evaluations"]]
    baselines = (
        "generic_fresh_advantage",
        "fixed_delay_5",
        "historical_circuit_median",
        "strategic_hybrid",
    )
    best_delays = defaultdict(int)
    terminal_spreads = []
    automatic_disadvantages = []
    break_even_errors = []
    for row in rows:
        comparison = row["comparison"]
        best = next(
            (
                action
                for action in comparison["actions"]
                if action["action"] == comparison["best_strategic_action"]
            ),
            None,
        )
        if best:
            best_delays[best["delay_laps"]] += 1
        terminals = [
            action["terminal_expected_seconds"]
            for action in comparison["actions"]
            if action["terminal_expected_seconds"] is not None
        ]
        if terminals:
            terminal_spreads.append(max(terminals) - min(terminals))
        pit_now = [action for action in comparison["actions"] if action["delay_laps"] == 0]
        delayed = [action for action in comparison["actions"] if action["delay_laps"] > 0]
        if pit_now and delayed:
            automatic_disadvantages.append(
                min(action["terminal_expected_seconds"] for action in pit_now)
                - min(action["terminal_expected_seconds"] for action in delayed)
            )
        actual_break = row["actual_break_even_lap"]
        predicted = [
            value
            for value in row["predicted_pit_now_break_even_laps"].values()
            if value is not None
        ]
        if actual_break is not None and predicted:
            break_even_errors.append(abs(min(predicted) - actual_break))
    split_cases = [row for result in results for row in result["split_cases"]]
    return {
        "states": len(rows),
        "selected_stops": sum(result["selected_stops"] for result in results),
        "best_action_delay_distribution": dict(sorted(best_delays.items())),
        "terminal_action_separation_seconds": {
            "median": median(terminal_spreads) if terminal_spreads else None,
            "mean": mean(terminal_spreads) if terminal_spreads else None,
        },
        "pit_now_minus_best_delayed_terminal_seconds": {
            "median": median(automatic_disadvantages) if automatic_disadvantages else None,
            "mean": mean(automatic_disadvantages) if automatic_disadvantages else None,
            "pit_now_better_count": sum(value < 0 for value in automatic_disadvantages),
            "delayed_better_count": sum(value > 0 for value in automatic_disadvantages),
            "equivalent_within_2_seconds": sum(
                abs(value) <= 2 for value in automatic_disadvantages
            ),
        },
        "break_even_lap_error": {
            "count": len(break_even_errors),
            "median_absolute_laps": median(break_even_errors) if break_even_errors else None,
            "mean_absolute_laps": mean(break_even_errors) if break_even_errors else None,
        },
        "forecast_errors": {
            baseline: {horizon: _errors(rows, baseline, horizon) for horizon in FORECAST_HORIZONS}
            for baseline in baselines
        },
        "comparable_split_stops": {
            "cases": len(split_cases),
            "direction_agreement": sum(row["direction_agreement"] for row in split_cases),
            "earlier_advantage_predicted": sum(
                row["predicted_label"] == "EARLIER_STOP_ADVANTAGE" for row in split_cases
            ),
            "later_advantage_predicted": sum(
                row["predicted_label"] == "LATER_STOP_ADVANTAGE" for row in split_cases
            ),
            "equivalent_predicted": sum(
                row["predicted_label"] == "EQUIVALENT" for row in split_cases
            ),
        },
    }


def run(output):
    started = perf_counter()
    with ProcessPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(evaluate_race, VALIDATION_RACES))
    report = {
        "chronology": {
            "prior_training": "17 cached 2021-2023 races",
            "holdout_races": [
                f"{year}/{round_number}" for year, round_number, _ in VALIDATION_RACES
            ],
            "future_observations_used_only_as_labels": True,
            "retuned_on_holdout": False,
        },
        "sampling": {
            "maximum_factual_stops_per_race": MAX_STOPS_PER_RACE,
            "checkpoints": ["N-5", "N-3", "N-1"],
            "trajectory_count": 100,
        },
        "results": summarize(results),
        "race_results": results,
        "wall_clock_seconds": perf_counter() - started,
    }
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    report = run(Path("docs/phase6e-validation.json"))
    print(
        json.dumps(
            {"results": report["results"], "runtime": report["wall_clock_seconds"]}, indent=2
        )
    )
