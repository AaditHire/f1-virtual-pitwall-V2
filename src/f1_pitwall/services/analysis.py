"""Orchestration over the existing replay cache; CPU work stays off the event loop."""

import asyncio

from f1_pitwall.domain.analysis import DriverAnalysis
from f1_pitwall.services.analysis_context import AnalysisContext
from f1_pitwall.services.pace import analyze_tyres, get_recent_pace
from f1_pitwall.services.pair_analysis import calculate_overcut, calculate_undercut
from f1_pitwall.services.pit_analysis import estimate_pit_loss, find_pit_window
from f1_pitwall.services.traffic import analyze_traffic


def analyze_driver(context, driver_id, pit_loss=None):
    driver = context.driver(driver_id)
    loss = pit_loss or estimate_pit_loss(context)
    window = find_pit_window(driver_id, context.state, loss)
    return DriverAnalysis(
        year=context.state.event.year,
        round=context.state.event.round,
        lap=context.state.current_lap,
        cutoff_seconds=context.cutoff,
        driver=driver,
        recent_pace=get_recent_pace(context, driver_id),
        tyres=analyze_tyres(context, driver_id),
        pit_loss=loss,
        rejoin=window.rejoin,
        traffic=analyze_traffic(context, driver_id, window.rejoin),
        pit_window=window,
    )


class AnalysisService:
    def __init__(self, replay):
        self.replay = replay

    async def _run(self, year, round, lap, operation):
        race = await self.replay.load_race(year, round)
        return await asyncio.to_thread(lambda: operation(AnalysisContext(race, lap)))

    async def analyze_driver(self, year, round, lap, driver_id):
        return await self._run(year, round, lap, lambda c: analyze_driver(c, driver_id))

    async def analyze_tyres(self, year, round, lap, driver_id):
        return await self._run(year, round, lap, lambda c: analyze_tyres(c, driver_id))

    async def analyze_traffic(self, year, round, lap, driver_id):
        return (await self.analyze_driver(year, round, lap, driver_id)).traffic

    async def analyze_undercut(self, year, round, lap, attacker, target, new_compound=None):
        return await self._run(
            year,
            round,
            lap,
            lambda c: calculate_undercut(c, attacker, target, estimate_pit_loss(c), new_compound),
        )

    async def analyze_overcut(self, year, round, lap, driver, target, new_compound=None):
        return await self._run(
            year,
            round,
            lap,
            lambda c: calculate_overcut(c, driver, target, estimate_pit_loss(c), new_compound),
        )
