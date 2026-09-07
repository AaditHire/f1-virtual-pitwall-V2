"""Chronological Phase 4 evaluation; future records are labels only."""

import argparse
import json
from collections import Counter
from pathlib import Path
from statistics import mean, median

from f1_pitwall.domain.replay import HistoricalRace
from f1_pitwall.services.analysis_context import AnalysisContext
from f1_pitwall.services.pit_analysis import estimate_pit_loss, predict_pit_rejoin
from f1_pitwall.services.strategy import analyze_strategy_all

DEVELOPMENT_RACES = ((2023, 1), (2023, 6), (2023, 7), (2023, 14))
VALIDATION_RACES = ((2024, 1), (2024, 4), (2024, 8), (2024, 10), (2024, 16))


def load_races(selections):
    races = []
    for year, round_number in selections:
        path = Path(f".cache/analysis-history-{year}-{round_number}.json")
        races.append(HistoricalRace.model_validate_json(path.read_text(encoding="utf-8")))
    return races


def available_laps(race):
    return sorted({row.number for row in race.laps})


def cutoff_by_lap(race):
    return {
        lap: min(row.available_at for row in race.laps if row.number == lap)
        for lap in available_laps(race)
    }


def global_tyre_threshold(races):
    ages = []
    for race in races:
        for stop in race.pit_stops:
            stints = [
                stint
                for stint in race.stints
                if stint.driver_id == stop.driver_id
                and stint.observed_at < stop.entered_at
                and stint.tyre_age is not None
            ]
            if stints:
                ages.append(max(stints, key=lambda stint: stint.observed_at).tyre_age)
    return int(round(median(ages))), len(ages)


def region(position):
    if position is None:
        return "unknown"
    if position <= 5:
        return "P1-P5"
    if position <= 10:
        return "P6-P10"
    if position <= 15:
        return "P11-P15"
    return "P16+"


def pit_after_laps(race, driver_id, now, cuts, lap):
    stops = sorted(
        stop.entered_at
        for stop in race.pit_stops
        if stop.driver_id == driver_id and now < stop.entered_at <= cuts[lap + 3]
    )
    if not stops:
        return None
    entered_at = stops[0]
    return next(offset for offset in range(1, 4) if entered_at <= cuts[lap + offset])


def future_outcome(current, future, driver_id):
    before, after = current.driver(driver_id), future.driver(driver_id)
    position_change = (
        before.position - after.position
        if before.position is not None and after.position is not None
        else None
    )
    relative_time_gain = (
        before.gap_to_leader - after.gap_to_leader
        if before.gap_to_leader is not None and after.gap_to_leader is not None
        else None
    )
    return position_change, relative_time_gain


def evaluation_rows(races, threshold):
    rows = []
    for race in races:
        laps = available_laps(race)
        cuts = cutoff_by_lap(race)
        decision_laps = [lap for lap in laps if lap >= 10 and lap % 10 == 0 and lap + 5 in cuts]
        for lap in decision_laps:
            current = AnalysisContext(race, lap)
            future3, future5 = AnalysisContext(race, lap + 3), AnalysisContext(race, lap + 5)
            grid = analyze_strategy_all(current)
            for decision in grid.decisions:
                driver_id = decision.driver.id
                pit_offset = pit_after_laps(race, driver_id, current.cutoff, cuts, lap)
                action = "PIT_NOW" if pit_offset == 1 else "EXTEND"
                pos3, time3 = future_outcome(current, future3, driver_id)
                pos5, time5 = future_outcome(current, future5, driver_id)
                pit_possible = any(item.kind == "PIT_NOW" for item in decision.actions)
                engine = (
                    decision.recommended_action
                    if decision.recommended_action in {"PIT_NOW", "EXTEND"}
                    else decision.recommended_action
                )
                threshold_action = (
                    "PIT_NOW"
                    if pit_possible
                    and decision.tyre_age is not None
                    and decision.tyre_age >= threshold
                    else "EXTEND"
                )
                rows.append(
                    {
                        "race": f"{race.event.year}/{race.event.round}",
                        "lap": lap,
                        "driver_id": driver_id,
                        "position": decision.current_position,
                        "region": region(decision.current_position),
                        "tyre_age": decision.tyre_age,
                        "engine": engine,
                        "engine_action_id": next(
                            (
                                item.id
                                for item in decision.actions
                                if item.action_score == decision.decision_score
                            ),
                            None,
                        ),
                        "actual_action": action,
                        "pit_after_laps": pit_offset,
                        "always_extend_3": "EXTEND_3",
                        "pit_when_legal": "PIT_NOW" if pit_possible else "EXTEND",
                        "global_tyre_threshold": threshold_action,
                        "position_change_3": pos3,
                        "position_change_5": pos5,
                        "relative_time_gain_3": time3,
                        "relative_time_gain_5": time5,
                        "current_traffic": decision.traffic_status,
                        "rejoin_traffic": decision.expected_rejoin.traffic
                        if decision.expected_rejoin
                        else "UNKNOWN",
                        "rejoin_range": list(decision.expected_rejoin.position_range)
                        if decision.expected_rejoin and decision.expected_rejoin.position_range
                        else None,
                        "decision_margin": decision.decision_margin,
                        "confidence": decision.confidence,
                    }
                )
    return rows


def average(rows, key):
    values = [row[key] for row in rows if row[key] is not None]
    return mean(values) if values else None


def policy_metrics(rows, key):
    decisive = [
        row
        for row in rows
        if row[key] == "PIT_NOW" or str(row[key]).startswith("EXTEND")
    ]
    def aligned_with_observed(row):
        recommendation = row[key]
        if recommendation == "PIT_NOW":
            return row["pit_after_laps"] == 1
        extension = (
            int(row["engine_action_id"].rsplit("_", 1)[1])
            if key == "engine"
            and row["engine_action_id"]
            and row["engine_action_id"].startswith("EXTEND_")
            else 3
            if recommendation == "EXTEND_3"
            else 1
        )
        return row["pit_after_laps"] is None or row["pit_after_laps"] > extension

    aligned = [row for row in decisive if aligned_with_observed(row)]
    adverse_pit = [
        row
        for row in aligned
        if row[key] == "PIT_NOW"
        and row["position_change_5"] is not None
        and row["position_change_5"] < 0
    ]
    adverse_extend = [
        row
        for row in aligned
        if str(row[key]).startswith("EXTEND")
        and row["position_change_5"] is not None
        and row["position_change_5"] < 0
    ]
    return {
        "samples": len(rows),
        "decisive": len(decisive),
        "decision_coverage": len(decisive) / len(rows) if rows else None,
        "hold_frequency": sum(row[key] == "HOLD_NO_CLEAR_ADVANTAGE" for row in rows)
        / len(rows)
        if rows
        else None,
        "insufficient_frequency": sum(row[key] == "INSUFFICIENT_DATA" for row in rows)
        / len(rows)
        if rows
        else None,
        "recommendations": dict(Counter(row[key] for row in rows)),
        "observed_action_agreement": len(aligned) / len(decisive) if decisive else None,
        "aligned_samples": len(aligned),
        "aligned_mean_position_change_3": average(aligned, "position_change_3"),
        "aligned_mean_position_change_5": average(aligned, "position_change_5"),
        "aligned_mean_relative_time_gain_3": average(aligned, "relative_time_gain_3"),
        "aligned_mean_relative_time_gain_5": average(aligned, "relative_time_gain_5"),
        "incorrect_pit_proxy": len(adverse_pit),
        "incorrect_extend_proxy": len(adverse_extend),
        "proxy_definition": (
            "A decisive recommendation aligned through its prescribed action horizon and "
            "the driver then lost position by +5. This is an adverse-outcome proxy, not "
            "a counterfactual correctness label."
        ),
    }


def metrics(rows):
    policies = {
        key: policy_metrics(rows, key)
        for key in (
            "engine",
            "always_extend_3",
            "pit_when_legal",
            "global_tyre_threshold",
        )
    }
    return {
        "policies": policies,
        "engine_by_grid_region": {
            name: policy_metrics([row for row in rows if row["region"] == name], "engine")
            for name in ("P1-P5", "P6-P10", "P11-P15", "P16+")
        },
    }


def cached_context(cache, race, lap):
    cache.setdefault(lap, AnalysisContext(race, lap))
    return cache[lap]


def pit_rejoin_rows(races):
    rows = []
    for race in races:
        cuts = cutoff_by_lap(race)
        contexts = {}

        for stop in race.pit_stops:
            prior = [lap for lap, cutoff in cuts.items() if cutoff < stop.entered_at]
            after = [
                lap
                for lap, cutoff in cuts.items()
                if stop.exited_at and cutoff >= stop.exited_at
            ]
            if not prior or not after:
                continue
            lap, outcome_lap = max(prior), min(after)
            current = cached_context(contexts, race, lap)
            predicted = predict_pit_rejoin(
                stop.driver_id, current.state, estimate_pit_loss(current)
            )
            actual_position = cached_context(contexts, race, outcome_lap).driver(
                stop.driver_id
            ).position
            if not predicted.position_range or actual_position is None:
                continue
            lower, upper = predicted.position_range
            rows.append(
                {
                    "race": f"{race.event.year}/{race.event.round}",
                    "lap": lap,
                    "driver_id": stop.driver_id,
                    "predicted_position": predicted.projected_position,
                    "predicted_range": [lower, upper],
                    "actual_position": actual_position,
                    "absolute_error": abs(predicted.projected_position - actual_position)
                    if predicted.projected_position is not None
                    else None,
                    "range_miss": max(lower - actual_position, actual_position - upper, 0),
                }
            )
    exact = [row for row in rows if row["absolute_error"] is not None]
    return {
        "samples": len(rows),
        "exact_samples": len(exact),
        "mean_absolute_error_positions": average(exact, "absolute_error"),
        "mean_range_miss_positions": average(rows, "range_miss"),
        "rows": rows,
    }


def build_report():
    development = load_races(DEVELOPMENT_RACES)
    validation = load_races(VALIDATION_RACES)
    threshold, threshold_samples = global_tyre_threshold(development)
    dev_rows = evaluation_rows(development, threshold)
    validation_rows = evaluation_rows(validation, threshold)
    backmarkers = [
        row
        for row in validation_rows
        if row["position"] is not None
        and row["position"] >= 15
        and row["engine"] in {"PIT_NOW", "EXTEND"}
    ]
    return {
        "method": {
            "decision_laps": "Every tenth leader lap from lap 10 with +5 outcome available",
            "causal_boundary": "Strategy sees only records published by the Lap N cutoff",
            "future_use": "Only +3/+5 outcomes and observed actions use post-cutoff records",
            "actual_action": "Pit entry before the next leader-lap cutoff, otherwise EXTEND",
            "policy_alignment": (
                "PIT_NOW requires a stop by the next cutoff; EXTEND_N requires no stop for N "
                "leader laps. One-step baseline extensions require no stop by the next cutoff."
            ),
            "comparison_limit": (
                "Historical data reveal one action outcome. Policy outcome means use only "
                "rows where the policy agrees with the observed action and are selection-biased."
            ),
        },
        "development_races": DEVELOPMENT_RACES,
        "validation_races": VALIDATION_RACES,
        "global_tyre_age_threshold": {
            "laps": threshold,
            "derivation": "Rounded median pre-stop tyre age across all development races",
            "samples": threshold_samples,
        },
        "development": {**metrics(dev_rows), "rows": dev_rows},
        "validation": {**metrics(validation_rows), "rows": validation_rows},
        "pit_rejoin_validation": {
            "development": pit_rejoin_rows(development),
            "validation": pit_rejoin_rows(validation),
        },
        "backmarker_validation": {
            "definition": "Current position P15 or lower",
            "decisive_samples": len(backmarkers),
            "metrics": policy_metrics(backmarkers, "engine"),
            "examples": backmarkers[:12],
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="docs/strategy-validation.json")
    arguments = parser.parse_args()
    report = build_report()
    Path(arguments.output).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "global_tyre_age_threshold": report["global_tyre_age_threshold"],
                "development": report["development"]["policies"],
                "validation": report["validation"]["policies"],
                "validation_by_grid_region": report["validation"]["engine_by_grid_region"],
                "pit_rejoin_validation": {
                    split: {
                        key: value
                        for key, value in result.items()
                        if key != "rows"
                    }
                    for split, result in report["pit_rejoin_validation"].items()
                },
                "backmarkers": report["backmarker_validation"],
            },
            indent=2,
        )
    )
