"""Measure historical clean-lap traffic loss against driver-specific clear-air pace."""

import json
from collections import defaultdict
from pathlib import Path
from statistics import median

from evaluate_counterfactuals import PRIOR_RACES, load_races, timing_values_at

from f1_pitwall.services.analysis_context import AnalysisContext
from f1_pitwall.services.pace import normalized_lap_times


def gap_band(gap):
    if gap is None:
        return "UNKNOWN"
    if gap <= 1:
        return "<=1s"
    if gap <= 2:
        return "1-2s"
    if gap <= 5:
        return "2-5s"
    return ">5s_clear_reference"


def summarize(values):
    if not values:
        return {"samples": 0, "median_loss_seconds": None, "mad_seconds": None}
    centre = median(values)
    return {
        "samples": len(values),
        "median_loss_seconds": centre,
        "mad_seconds": median(abs(value - centre) for value in values),
    }


def analyze(races):
    losses = defaultdict(list)
    exclusions = defaultdict(int)
    for race in races:
        laps = sorted({row.number for row in race.laps})
        context = AnalysisContext(race, max(laps))
        timing = {
            participant.driver.id: sorted(
                (
                    sample
                    for sample in race.timing
                    if sample.driver_id == participant.driver.id
                ),
                key=lambda sample: sample.at,
            )
            for participant in race.participants
        }
        for participant in race.participants:
            driver_id = participant.driver.id
            points = normalized_lap_times(context, driver_id, current_stint=False)
            labelled = []
            for number, value in points:
                row = context.rows[driver_id][number]
                state = timing_values_at(timing[driver_id], row.completed_at)
                labelled.append((gap_band(state["gap_to_ahead"]), value))
            clear = [value for band, value in labelled if band == ">5s_clear_reference"]
            if len(clear) < 3:
                exclusions["driver_without_three_clear_laps"] += 1
                continue
            reference = median(clear)
            for band, value in labelled:
                if band == "UNKNOWN":
                    exclusions["stale_or_missing_interval"] += 1
                    continue
                losses["all", band].append(value - reference)
                losses[race.event.circuit.id, band].append(value - reference)
    circuits = sorted({race.event.circuit.id for race in races})
    bands = ("<=1s", "1-2s", "2-5s", ">5s_clear_reference")
    return {
        "method": (
            "Green clean-lap normalized pace minus the same driver's median clear-air pace; "
            "pit laps and non-green laps are excluded by AnalysisContext."
        ),
        "races": [(race.event.year, race.event.round) for race in races],
        "overall": {band: summarize(losses["all", band]) for band in bands},
        "by_circuit": {
            circuit: {band: summarize(losses[circuit, band]) for band in bands}
            for circuit in circuits
        },
        "exclusions": dict(exclusions),
    }


if __name__ == "__main__":
    report = analyze(load_races(PRIOR_RACES))
    output = Path("docs/traffic-effects-phase5c.json")
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
