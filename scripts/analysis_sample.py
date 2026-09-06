"""Print one compact historical engineering analysis, without future evaluation."""

import argparse
import asyncio

import httpx

from f1_pitwall.core.config import Settings
from f1_pitwall.services.analysis import analyze_driver
from f1_pitwall.services.analysis_context import AnalysisContext
from f1_pitwall.services.hub import Hub
from f1_pitwall.services.pair_analysis import calculate_overcut, calculate_undercut


async def main(year, round, lap, driver_code, target_code):
    async with httpx.AsyncClient(follow_redirects=True) as client:
        hub = Hub(client, Settings.from_env())
        race = await hub.replay.load_race(year, round)
        context = AnalysisContext(race, lap)
        driver = next(d.driver.id for d in context.state.drivers if d.driver.code == driver_code)
        target = next(d.driver.id for d in context.state.drivers if d.driver.code == target_code)
        result = analyze_driver(context, driver)
        tyre = result.tyres

        def number(value):
            return f"{value:.3f}" if value is not None else "unavailable"

        print(f"Driver: {driver_code} | {year} {race.event.name} | Lap {lap}")
        print(
            f"Tyre: {tyre.compound}, age {tyre.tyre_age}, selected relative forecast "
            f"{number(tyre.degradation_sec_per_lap)} s/lap ({tyre.confidence}), "
            f"{tyre.sample_count} clean laps"
        )
        print(f"Recent pace: {number(result.recent_pace.seconds)} s")
        print(
            f"Pit loss: {number(result.pit_loss.total_seconds)} s "
            f"({result.pit_loss.confidence}, {result.pit_loss.sample_count} stops)"
        )
        print(
            f"Rejoin: P{result.rejoin.projected_position} "
            f"(range {result.rejoin.position_range}), {result.rejoin.traffic}"
        )
        print(f"Current traffic: {result.traffic.status}")
        for function in (calculate_undercut, calculate_overcut):
            pair = function(context, driver, target, result.pit_loss)
            print(
                f"{pair.kind.title()} vs {target_code}: margin {number(pair.estimated_margin)} s, "
                f"{pair.opportunity} ({pair.confidence})"
            )
        print(
            "Conditional clean-lap margins; out-lap and unequal stops unmeasured. "
            "No recommendation."
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("year", type=int)
    parser.add_argument("round", type=int)
    parser.add_argument("lap", type=int)
    parser.add_argument("driver_code")
    parser.add_argument("target_code")
    args = parser.parse_args()
    asyncio.run(main(args.year, args.round, args.lap, args.driver_code, args.target_code))
