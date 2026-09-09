"""Validate the narrow Phase 6C defect fix on untouched late-2025 races."""

import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from time import perf_counter

from audit_phase6c import summarize, summarize_race

VALIDATION_RACES = (
    (2025, 20, "Mexico City", "high altitude/overtaking-friendly"),
    (2025, 22, "Las Vegas", "street/low degradation"),
    (2025, 23, "Qatar", "high-speed/high tyre load"),
    (2025, 24, "Abu Dhabi", "traction/overtaking-friendly"),
)


def run(output):
    started = perf_counter()
    with ProcessPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(summarize_race, VALIDATION_RACES))
    report = {
        "chronology": {
            "defects_observed_in": "Phase 6C frozen 2021-2025 audit",
            "untouched_fix_validation": [
                f"{year}/{round_number}" for year, round_number, _, _ in VALIDATION_RACES
            ],
            "retuned_on_validation": False,
        },
        "fix": {
            "normal_stop_cooldown_laps": 3,
            "position_only_strong_window_disabled": True,
            "equivalence_bands_and_frequency_thresholds_changed": False,
        },
        "dataset": [
            {key: result[key] for key in ("race", "event", "circuit", "profile", "lap_range")}
            for result in results
        ],
        "audit": summarize(results),
        "wall_clock_seconds": perf_counter() - started,
    }
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    report = run(Path("docs/phase6c-fix-validation.json"))
    print(
        json.dumps(
            {
                "decision_distribution": report["audit"]["decision_distribution"],
                "post_pit_windows": report["audit"]["post_pit_windows"],
                "isolated_pit_audit": report["audit"]["isolated_pit_audit"],
                "pit_cycle_validation": report["audit"]["pit_cycle_validation"],
                "strategic_rivals": report["audit"]["strategic_rivals"],
            },
            indent=2,
        )
    )
