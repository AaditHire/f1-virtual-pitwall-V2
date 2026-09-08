"""Cache the frozen Phase 5D final-holdout races without inspecting their labels."""

import asyncio
from pathlib import Path

import httpx

from f1_pitwall.core.config import Settings
from f1_pitwall.services.hub import Hub

FINAL_HOLDOUT_RACES = ((2025, 1), (2025, 4), (2025, 8), (2025, 9), (2025, 16))


async def main():
    async with httpx.AsyncClient(follow_redirects=True) as client:
        hub = Hub(client, Settings.from_env())
        for year, round_number in FINAL_HOLDOUT_RACES:
            path = Path(f".cache/analysis-history-{year}-{round_number}.json")
            if not path.exists():
                race = await hub.replay.load_race(year, round_number)
                path.write_text(race.model_dump_json(exclude_unset=True), encoding="utf-8")
            print(f"cached {year}/{round_number}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
