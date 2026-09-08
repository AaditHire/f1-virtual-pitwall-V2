"""Frozen Phase 5D policy separation on 2024 and independent 2025 EXTEND check."""

import json
from pathlib import Path

from evaluate_counterfactuals import (
    attach_chronological_profiles,
    collect_factual_cases,
    evaluate_factual_cases,
    factual_report,
    lap_cutoffs,
    load_races,
    policy_report,
    policy_rows,
    tyre_threshold,
)
from evaluate_phase5d import (
    DEVELOPMENT_RACES,
    FINAL_RACES,
    TRAIN_RACES,
    green_outcome_window,
)

OUTPUT = Path("docs/phase5d-policy-and-extend.json")


def separation(rows):
    dimensions = {"all": rows}
    for region in ("P1-P5", "P6-P10", "P11-P15", "P16+"):
        dimensions[region] = [row for row in rows if row["region"] == region]
    result = {}
    for name, selected in dimensions.items():
        eligible = [
            row for row in selected if row["pit_when_legal"]["action"].startswith("PIT_NOW_")
        ]
        separated = []
        for row in eligible:
            pit = row["pit_when_legal"]
            extend = row["always_extend_3"]
            margin = abs(pit["relative_time_3"] - extend["relative_time_3"])
            combined = pit["uncertainty_3"] + extend["uncertainty_3"]
            separated.append(margin > combined)
        result[name] = {
            "common_snapshots": len(selected),
            "pit_eligible_snapshots": len(eligible),
            "separated_snapshots": sum(separated),
            "separation_rate_all": sum(separated) / len(selected) if selected else None,
            "separation_rate_when_pit_eligible": sum(separated) / len(eligible)
            if eligible
            else None,
        }
    return result


def main():
    training = load_races(TRAIN_RACES)
    development = load_races(DEVELOPMENT_RACES)
    threshold, samples = tyre_threshold(training)
    rows, exclusions, backmarker_exclusions = policy_rows(
        development, None, threshold, training + development
    )

    final_races = load_races(FINAL_RACES)
    final_cases = collect_factual_cases(final_races)
    final_cases["PIT_NOW"] = []
    attach_chronological_profiles(final_cases, training + development + final_races)
    extend_rows = evaluate_factual_cases(final_cases, None)
    race_map = {f"{race.event.year}/{race.event.round}": race for race in final_races}
    cutoff_map = {key: lap_cutoffs(race)[1] for key, race in race_map.items()}
    valid_extend_rows = []
    excluded_extend = {str(horizon): 0 for horizon in (1, 3, 5)}
    for row in extend_rows:
        race = race_map[row["race"]]
        cutoffs = cutoff_map[row["race"]]
        valid = green_outcome_window(
            race,
            cutoffs[row["lap"]],
            cutoffs.get(row["lap"] + row["horizon"], cutoffs[row["lap"]]),
        )
        if valid:
            valid_extend_rows.append(row)
        else:
            excluded_extend[str(row["horizon"])] += 1
    report = {
        "chronology": (
            "Policy separation uses 2024 development. The 2025 EXTEND report is an "
            "independent unchanged-model check and does not fit or select parameters."
        ),
        "policy": policy_report(rows),
        "separation": separation(rows),
        "coverage": {
            "common_snapshots": len(rows),
            "exclusions": exclusions,
            "backmarker_exclusions": backmarker_exclusions,
            "age_threshold_laps": threshold,
            "age_threshold_samples": samples,
        },
        "extend_2025": factual_report(valid_extend_rows)["EXTEND"],
        "extend_non_green_window_exclusions": excluded_extend,
    }
    OUTPUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "separation": report["separation"]}, indent=2))


if __name__ == "__main__":
    main()
