"""Build chronological empirical stint-segment priors for Phase 6E."""

from __future__ import annotations

import json
from pathlib import Path
from statistics import median

from f1_pitwall.services.analysis_context import AnalysisContext
from f1_pitwall.services.pace import normalized_lap_times

TRAINING_RACES = (
    (2021, 1),
    (2021, 4),
    (2021, 5),
    (2021, 14),
    (2021, 17),
    (2022, 1),
    (2022, 6),
    (2022, 7),
    (2022, 16),
    (2022, 18),
    (2022, 19),
    (2023, 1),
    (2023, 6),
    (2023, 7),
    (2023, 14),
    (2023, 15),
    (2023, 18),
)
DRY_COMPOUNDS = {"SOFT", "MEDIUM", "HARD"}


def load_race(year, round_number):
    path = Path(f".cache/analysis-history-{year}-{round_number}.json")
    return path, json.loads(path.read_text(encoding="utf-8"))


def extract_segments(year, round_number):
    path = Path(f".cache/analysis-history-{year}-{round_number}.json")
    from f1_pitwall.domain.replay import HistoricalRace

    race = HistoricalRace.model_validate_json(path.read_text(encoding="utf-8"))
    final_lap = max(row.number for row in race.laps)
    context = AnalysisContext(race, final_lap)
    participant = {row.driver.id: row for row in race.participants}
    segments = []
    for driver_id in context.drivers:
        normalized = dict(normalized_lap_times(context, driver_id, current_stint=False))
        grouped = {}
        for lap in context.clean.get(driver_id, []):
            previous = context.rows[driver_id].get(lap.number - 1)
            if previous is None:
                continue
            stint = context.stint_at(driver_id, previous.completed_at)
            end_stint = context.stint_at(driver_id, lap.completed_at)
            if (
                stint is None
                or end_stint is None
                or stint.number != end_stint.number
                or stint.compound not in DRY_COMPOUNDS
                or lap.number not in normalized
            ):
                continue
            grouped.setdefault((stint.number, stint.compound), []).append(
                (lap.number, normalized[lap.number])
            )
        stint_records = [row for row in context.race.stints if row.driver_id == driver_id]
        max_stint = max((row.number for row in stint_records), default=0)
        for (number, compound), points in grouped.items():
            if len(points) < 3:
                continue
            pace = median(value for _, value in points)
            if abs(pace) > 5:
                continue
            ages = [
                row.tyre_age
                for row in stint_records
                if row.number == number and row.tyre_age is not None
            ]
            competitive_length = max(ages) + 1 if ages and number < max_stint else None
            constructor = (
                participant.get(driver_id).constructor if participant.get(driver_id) else None
            )
            segments.append(
                {
                    "race": f"{year}/{round_number}",
                    "race_date": race.event.race_date.isoformat(),
                    "circuit_id": race.event.circuit.id,
                    "driver_id": driver_id,
                    "constructor_id": constructor.id if constructor else None,
                    "compound": compound,
                    "stint_number": number,
                    "relative_pace_seconds_per_lap": round(pace, 4),
                    "pace_lap_count": len(points),
                    "competitive_stint_length_laps": round(competitive_length, 1)
                    if competitive_length is not None
                    else None,
                    "length_is_completed_stint": competitive_length is not None,
                }
            )
    return {
        "race": f"{year}/{round_number}",
        "event": race.event.name,
        "date": race.event.race_date.isoformat(),
        "circuit_id": race.event.circuit.id,
        "segments": segments,
    }


def run(output):
    races = [extract_segments(*race) for race in TRAINING_RACES]
    artifact = {
        "method": (
            "Median clean stint pace relative to the contemporaneous leave-one-driver-out "
            "field median; completed-stint length is an observed strategy prior."
        ),
        "training_cutoff": "2023-12-31",
        "future_races_used": False,
        "races": [
            {key: race[key] for key in ("race", "event", "date", "circuit_id")} for race in races
        ],
        "segments": [segment for race in races for segment in race["segments"]],
    }
    output.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    return artifact


if __name__ == "__main__":
    artifact = run(Path("src/f1_pitwall/models/strategic_stint_priors.json"))
    print(
        json.dumps(
            {
                "races": len(artifact["races"]),
                "segments": len(artifact["segments"]),
                "by_compound": {
                    compound: sum(row["compound"] == compound for row in artifact["segments"])
                    for compound in sorted(DRY_COMPOUNDS)
                },
            },
            indent=2,
        )
    )
