"""Original Phase 3 historical holdouts; retained for baseline traceability.

Run: python scripts/evaluate_analysis.py --output docs/analysis-validation.json
No thresholds are fitted here. Local normalized archives can be reused with --cached.
Use calibrate_analysis.py for the current Phase 3B model comparison.
"""

import argparse
import asyncio
import json
from functools import lru_cache
from pathlib import Path
from statistics import mean, median

import httpx

from f1_pitwall.core.config import Settings
from f1_pitwall.domain.replay import HistoricalRace
from f1_pitwall.services.analysis import analyze_driver
from f1_pitwall.services.analysis_context import AnalysisContext
from f1_pitwall.services.hub import Hub
from f1_pitwall.services.pace import analyze_tyres, get_recent_pace
from f1_pitwall.services.pair_analysis import calculate_overcut, calculate_undercut
from f1_pitwall.services.pit_analysis import estimate_pit_loss, predict_pit_rejoin

RACES = [(2023, 1), (2024, 1), (2023, 14)]


def evaluate(race):
    year, round = race.event.year, race.event.round
    cuts = {}
    for row in race.laps:
        cuts[row.number] = min(cuts.get(row.number, float("inf")), row.available_at)

    @lru_cache(maxsize=4)
    def context(lap):
        return AnalysisContext(race, lap)

    report = {
        "year": year,
        "round": round,
        "event": race.event.name,
        "tyres": [],
        "rejoin": [],
        "pairs": [],
        "traffic_examples": {},
        "samples": [],
    }
    final = context(max(cuts))  # Evaluation labels only, never sent to a pre-stop calculation.
    final_loss = estimate_pit_loss(final)
    full_samples = final_loss.components["samples"]
    report["pit_loss"] = {
        "full_session_median_seconds": final_loss.total_seconds,
        "sample_count": final_loss.sample_count,
        "holdout": [],
    }
    for lap in (10, 20, 30, 40):
        if lap not in cuts:
            continue
        current = context(lap)
        loss = estimate_pit_loss(current)
        for identity, driver in current.drivers.items():
            if driver.status != "active":
                continue
            tyre = analyze_tyres(current, identity)
            recent = get_recent_pace(current, identity)
            rejoin = predict_pit_rejoin(identity, current.state, loss)
            if rejoin.traffic != "UNKNOWN":
                report["traffic_examples"].setdefault(
                    rejoin.traffic,
                    {"lap": lap, "driver_id": identity, "rejoin": rejoin.model_dump()},
                )
            if len(report["samples"]) < 4 and tyre.degradation_sec_per_lap is not None:
                report["samples"].append(analyze_driver(current, identity).model_dump(mode="json"))
            if tyre.degradation_sec_per_lap is None or len(recent.lap_numbers) < 3:
                continue
            # Future labels must be same stint and within five completed laps of the cutoff.
            future = [
                r
                for r in final.stint_laps(identity, driver.stint_number)
                if r.completed_at > current.cutoff and r.number <= (driver.laps_completed or 0) + 5
            ][:3]
            if len(future) != 3:
                continue
            horizon = median(r.number for r in future) - median(recent.lap_numbers)
            actual = median(r.lap_time_seconds for r in future)
            predicted = recent.seconds + tyre.degradation_sec_per_lap * horizon
            report["tyres"].append(
                {
                    "lap": lap,
                    "driver_id": identity,
                    "stint": driver.stint_number,
                    "slope": tyre.degradation_sec_per_lap,
                    "confidence": tyre.confidence,
                    "future_laps": [r.number for r in future],
                    "actual_seconds": actual,
                    "predicted_seconds": predicted,
                    "zero_slope_seconds": recent.seconds,
                    "absolute_error": abs(actual - predicted),
                    "baseline_absolute_error": abs(actual - recent.seconds),
                }
            )
    for pit in race.pit_stops:
        if pit.exited_at is None:
            continue
        before = [n for n, at in cuts.items() if at < pit.entered_at]
        after = [n for n, at in cuts.items() if at >= pit.exited_at]
        if not before or not after:
            continue
        lap, after_lap = max(before), min(after)
        current, actual_context = context(lap), context(after_lap)
        driver, actual_driver = current.driver(pit.driver_id), actual_context.driver(pit.driver_id)
        if driver.status != "active" or actual_driver.status != "active":
            continue
        loss = estimate_pit_loss(current)
        rejoin = predict_pit_rejoin(pit.driver_id, current.state, loss)
        if rejoin.position_range and actual_driver.position is not None:
            lower, upper = rejoin.position_range
            fixed = min(
                sum(d.active is not False for d in current.drivers.values()), driver.position + 5
            )
            report["rejoin"].append(
                {
                    "lap": lap,
                    "driver_id": pit.driver_id,
                    "actual_lap": after_lap,
                    "seconds_before_entry": pit.entered_at - current.cutoff,
                    "seconds_after_exit": actual_context.cutoff - pit.exited_at,
                    "current_position": driver.position,
                    "predicted_position": rejoin.projected_position,
                    "position_range": [lower, upper],
                    "actual_position": actual_driver.position,
                    "range_miss": max(
                        lower - actual_driver.position, actual_driver.position - upper, 0
                    ),
                    "absolute_error": abs(rejoin.projected_position - actual_driver.position)
                    if rejoin.projected_position is not None
                    else None,
                    "baseline_position": fixed,
                    "baseline_absolute_error": abs(fixed - actual_driver.position),
                    "confidence": rejoin.confidence,
                    "traffic": rejoin.traffic,
                }
            )
        observed = next(
            (
                s
                for s in full_samples
                if s["driver_id"] == pit.driver_id and s["entered_at"] == pit.entered_at
            ),
            None,
        )
        if observed and loss.total_seconds is not None:
            report["pit_loss"]["holdout"].append(
                {
                    "lap": lap,
                    "driver_id": pit.driver_id,
                    "predicted": loss.total_seconds,
                    "actual_residual": observed["loss_seconds"],
                    "absolute_error": abs(loss.total_seconds - observed["loss_seconds"]),
                    "full_session_baseline_error": abs(
                        final_loss.total_seconds - observed["loss_seconds"]
                    ),
                }
            )
        # Evaluate one-lap offset pairs only; the target/attacker must also actually stop.
        for kind in ("undercut", "overcut"):
            others = [
                d
                for d in current.state.drivers
                if d.position == driver.position + (1 if kind == "overcut" else -1)
            ]
            if not others:
                continue
            other = others[0]
            upcoming = [
                p
                for p in race.pit_stops
                if p.driver_id == other.driver.id
                and p.exited_at is not None
                and pit.entered_at < p.entered_at <= pit.entered_at + 150
            ]
            if not upcoming:
                continue
            next_pit = min(upcoming, key=lambda p: p.entered_at)
            target_pre = max((n for n, at in cuts.items() if at < next_pit.entered_at), default=0)
            if target_pre - lap != 1:
                continue
            identity, target = (
                (pit.driver_id, other.driver.id)
                if kind == "undercut"
                else (other.driver.id, pit.driver_id)
            )
            fn = calculate_undercut if kind == "undercut" else calculate_overcut
            result = fn(current, identity, target, loss)
            end_lap = min((n for n, at in cuts.items() if at >= next_pit.exited_at), default=0)
            if not end_lap:
                continue
            end = context(end_lap)
            a, b = end.driver(identity), end.driver(target)
            if a.position is None or b.position is None:
                continue
            actual_margin = (
                b.gap_to_leader - a.gap_to_leader
                if a.gap_to_leader is not None and b.gap_to_leader is not None
                else None
            )
            report["pairs"].append(
                {
                    "lap": lap,
                    "evaluation_lap": end_lap,
                    "kind": kind,
                    "driver_id": identity,
                    "target_id": target,
                    "estimated_margin": result.estimated_margin,
                    "opportunity": result.opportunity,
                    "confidence": result.confidence,
                    "actual_ahead": a.position < b.position,
                    "actual_margin": actual_margin,
                    "absolute_error": abs(result.estimated_margin - actual_margin)
                    if result.estimated_margin is not None and actual_margin is not None
                    else None,
                    "no_gain_baseline_margin": -result.current_gap
                    if result.current_gap is not None
                    else None,
                    "analysis": result.model_dump(mode="json"),
                }
            )
    tyres = report["tyres"]
    exact = [r for r in report["rejoin"] if r["absolute_error"] is not None]
    pit_rows = report["pit_loss"]["holdout"]
    pairs = [r for r in report["pairs"] if r["absolute_error"] is not None]

    def avg(rows, key):
        return mean(r[key] for r in rows) if rows else None

    report["summary"] = {
        "tyre_n": len(tyres),
        "tyre_mae_seconds": avg(tyres, "absolute_error"),
        "zero_slope_mae_seconds": avg(tyres, "baseline_absolute_error"),
        "rejoin_exact_n": len(exact),
        "rejoin_mae_positions": avg(exact, "absolute_error"),
        "fixed_five_positions_mae": avg(exact, "baseline_absolute_error"),
        "rejoin_range_n": len(report["rejoin"]),
        "rejoin_range_miss_mean": avg(report["rejoin"], "range_miss"),
        "pit_loss_n": len(pit_rows),
        "pit_loss_mae_seconds": avg(pit_rows, "absolute_error"),
        "full_session_median_mae_seconds": avg(pit_rows, "full_session_baseline_error"),
        "pair_n": len(report["pairs"]),
        "pair_numeric_n": sum(r["estimated_margin"] is not None for r in report["pairs"]),
        "pair_timed_n": len(pairs),
        "pair_mae_seconds": avg(pairs, "absolute_error"),
        "pair_no_gain_baseline_mae_seconds": mean(
            abs(r["actual_margin"] - r["no_gain_baseline_margin"]) for r in pairs
        )
        if pairs
        else None,
    }
    return report


async def main(output, cached=False):
    reports = []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        hub = Hub(client, Settings.from_env())
        for year, round in RACES:
            path = Path(f".cache/analysis-history-{year}-{round}.json")
            if cached and path.exists():
                race = HistoricalRace.model_validate_json(path.read_text())
                # Timing delta fields must survive JSON serialization without default nulls.
            else:
                race = await hub.replay.load_race(year, round)
                path.parent.mkdir(exist_ok=True)
                path.write_text(race.model_dump_json(exclude_unset=True), encoding="utf-8")
            report = await asyncio.to_thread(evaluate, race)
            reports.append(report)
            print(json.dumps({"race": [year, round], **report["summary"]}), flush=True)
    Path(output).write_text(
        json.dumps(
            {
                "method": "Chronological holdouts; fixed rules, no fitting. "
                "Future data used only for labels.",
                "baseline_note": "Full-session pit median is a hindsight reference, "
                "unavailable to runtime.",
                "races": reports,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="docs/analysis-validation.json")
    parser.add_argument("--cached", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.output, args.cached))
