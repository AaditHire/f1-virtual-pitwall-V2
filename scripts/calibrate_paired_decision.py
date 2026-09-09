"""Freeze paired decision gates on 2024 development races before 2025 validation."""

import json
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from statistics import mean
from types import SimpleNamespace

from f1_pitwall.domain.replay import HistoricalRace
from f1_pitwall.services.analysis_context import AnalysisContext
from f1_pitwall.services.paired import (
    EQUIVALENCE_BANDS,
    POSITION_EQUIVALENCE_PLACES,
    evaluate_paired_candidates,
)
from f1_pitwall.services.pit_analysis import estimate_pit_loss
from f1_pitwall.services.strategy import recommend_driver_action

DEVELOPMENT_RACES = ((2024, 1), (2024, 4), (2024, 10), (2024, 16))
SEEDS = (6100, 16100, 26100)
CANDIDATES = ((0.55, 0.70), (0.60, 0.75), (0.67, 0.80), (0.70, 0.85), (0.75, 0.90))


def load_race(year, round_number):
    return HistoricalRace.model_validate_json(
        Path(f".cache/analysis-history-{year}-{round_number}.json").read_text(encoding="utf-8")
    )


def classify(comparison, open_threshold, strong_threshold):
    frequency = comparison.pit_better_frequency or 0
    extend_frequency = comparison.extend_better_frequency or 0
    time_delta = comparison.median_time_delta_seconds
    cycle_delta = comparison.pit_cycle_position_delta
    band = EQUIVALENCE_BANDS[5]
    position_open = cycle_delta is not None and cycle_delta <= -(POSITION_EQUIVALENCE_PLACES + 1)
    position_strong = bool(
        position_open
        and comparison.pit_net_position_range_80
        and comparison.extend_net_position_range_80
        and comparison.pit_net_position_range_80[1] <= comparison.extend_net_position_range_80[0]
    )
    if (
        time_delta is not None and time_delta <= -band and frequency >= strong_threshold
    ) or position_strong:
        return "PIT_WINDOW_STRONG"
    if frequency >= open_threshold or position_open:
        return "PIT_WINDOW_OPEN"
    if extend_frequency >= strong_threshold and (
        not position_open or (time_delta is not None and time_delta > 2 * band)
    ):
        return "PIT_WINDOW_CLOSED"
    return "PIT_WINDOW_UNCERTAIN"


def evaluate_race(selection):
    year, round_number = selection
    race = load_race(year, round_number)
    last_lap = max(row.number for row in race.laps) - 5
    records = []
    for lap in range(8, last_lap + 1, 4):
        context = AnalysisContext(race, lap)
        pit_loss = estimate_pit_loss(context)
        for driver in context.state.drivers:
            if driver.status != "active":
                continue
            policy = recommend_driver_action(context, driver.driver.id, pit_loss)
            comparisons = []
            for seed in SEEDS:
                paired = evaluate_paired_candidates(
                    context, driver.driver.id, policy.actions, 100, seed + lap
                )
                comparisons.append(paired.best_comparison)
            if all(comparisons):
                records.append(
                    {
                        "key": f"{year}/{round_number}/{lap}/{driver.driver.id}",
                        "comparisons": [row.model_dump(mode="json") for row in comparisons],
                    }
                )
    return records


def candidate_report(records, candidate):
    open_threshold, strong_threshold = candidate
    state_sets = []
    for record in records:
        states = [
            classify(SimpleNamespace(**comparison), open_threshold, strong_threshold)
            for comparison in record["comparisons"]
        ]
        state_sets.append(states)
    base = [states[0] for states in state_sets]
    unanimous = [len(set(states)) == 1 for states in state_sets]
    strong_instability = [
        "PIT_WINDOW_STRONG" in states and len(set(states)) > 1 for states in state_sets
    ]
    return {
        "open_threshold": open_threshold,
        "strong_threshold": strong_threshold,
        "states": dict(sorted(Counter(base).items())),
        "unanimous_seed_rate": mean(unanimous) if unanimous else 0,
        "strong_signal_instability_rate": mean(strong_instability) if strong_instability else 0,
    }


def run(output):
    with ProcessPoolExecutor(max_workers=len(DEVELOPMENT_RACES)) as executor:
        nested = list(executor.map(evaluate_race, DEVELOPMENT_RACES))
    records = [record for rows in nested for record in rows]
    candidates = [candidate_report(records, candidate) for candidate in CANDIDATES]
    best_unanimous = max(row["unanimous_seed_rate"] for row in candidates)
    eligible = [row for row in candidates if row["unanimous_seed_rate"] >= best_unanimous - 0.01]
    selected = min(
        eligible,
        key=lambda row: (
            row["strong_signal_instability_rate"],
            row["strong_threshold"],
            row["open_threshold"],
        ),
    )
    report = {
        "chronology": {
            "development": [f"{year}/{round_number}" for year, round_number in DEVELOPMENT_RACES],
            "validation": "2025 races only; not inspected during this calibration run",
        },
        "equivalence_band": {
            "seconds_by_horizon": EQUIVALENCE_BANDS,
            "position_places": POSITION_EQUIVALENCE_PLACES,
            "method": (
                "Chronological factual MAE in Phase 5F's 2024 evaluation after fitting "
                "the transition model on 2021-2023 races. The position band is the "
                "one-place median absolute position error at +3 and +5."
            ),
        },
        "sampled_driver_states": len(records),
        "trajectory_count": 100,
        "repeat_seeds": list(SEEDS),
        "candidate_thresholds": candidates,
        "selected": selected,
        "selection_rule": (
            "Choose the least restrictive candidate within one percentage point of the "
            "best repeated-seed unanimity, then minimize strong-signal instability. PIT "
            "frequency is not part of the objective."
        ),
    }
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    result = run(Path("docs/phase6b-paired-calibration.json"))
    print(json.dumps(result, indent=2))
