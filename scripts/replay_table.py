"""Render a saved replay snapshot (or smoke report) as a full-grid Markdown table."""

import argparse
import json
from pathlib import Path


def lap_time(value):
    if value is None:
        return "—"
    return f"{int(value // 60)}:{value % 60:06.3f}"


def table(state):
    lines = [
        f"# {state['event']['year']} {state['event']['name']} — Lap {state['current_lap']}",
        "",
        "At the first reported leader completion; gaps and tyre ages are latest known samples.",
        "",
        "| POS | DRIVER | GAP | TYRE | AGE | STINT | PITS | LAST LAP | STATUS |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for driver in state["drivers"]:
        gap = driver["gap_to_leader"]
        gap = f"+{gap:.3f}s" if gap is not None else "—"
        if driver["lapped"]:
            gap = f"+{driver['laps_behind']} lap(s)"
        if driver["position"] == 1:
            gap = "Leader"
        values = [
            driver["position"],
            driver["driver"]["code"] or driver["driver"]["full_name"],
            gap,
            driver["compound"],
            driver["tyre_age"],
            driver["stint_number"],
            driver["pit_stops_completed"],
            lap_time(driver["last_lap_time"]),
            driver["status"],
        ]
        lines.append("| " + " | ".join(str(v) if v is not None else "—" for v in values) + " |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    data = json.loads(args.snapshot.read_text(encoding="utf-8-sig"))
    result = table(data.get("replay_snapshot", data))
    if args.output:
        args.output.write_text(result, encoding="utf-8")
    else:
        print(result)
