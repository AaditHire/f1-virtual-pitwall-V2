"""Small real-archive coverage/latency audit for the Phase 12A radio source layer."""

import asyncio
import json
from collections import Counter
from time import perf_counter

import httpx

from f1_pitwall.core.config import Settings
from f1_pitwall.services.hub import Hub

EVENTS = ((2021, 1), (2024, 1), (2025, 24))


async def audit_event(hub: Hub, year: int, round_number: int) -> dict:
    started = perf_counter()
    race = await hub.replay.load_race(year, round_number)
    load_seconds = perf_counter() - started
    lap = max(row.number for row in race.laps)

    started = perf_counter()
    feed = await hub.replay.get_radio(year, round_number, lap, limit=100)
    first_feed_ms = (perf_counter() - started) * 1000
    started = perf_counter()
    await hub.replay.get_radio(year, round_number, lap, limit=100)
    cached_feed_ms = (perf_counter() - started) * 1000

    counts = Counter(record.driver_id for record in race.radio)
    return {
        "year": year,
        "round": round_number,
        "event": race.event.name,
        "leader_laps": lap,
        "participants": len(race.participants),
        "messages": len(race.radio),
        "drivers_with_radio": len(counts),
        "messages_by_driver": dict(sorted(counts.items())),
        "pre_leader_lap_messages": sum(record.leader_lap is None for record in race.radio),
        "lap_aligned_messages": sum(record.leader_lap is not None for record in race.radio),
        "provider_timestamps": sum(record.provider_timestamp is not None for record in race.radio),
        "official_audio_references": sum(
            record.audio_url.startswith("https://livetiming.formula1.com/static/")
            for record in race.radio
        ),
        "process_cold_disk_cache_load_seconds": round(load_seconds, 3),
        "first_feed_ms": round(first_feed_ms, 3),
        "cached_feed_ms": round(cached_feed_ms, 3),
        "returned_at_limit_100": len(feed.messages),
    }


async def main() -> None:
    settings = Settings.from_env()
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers={"User-Agent": "F1Pitwall/0.1 (Phase 12A audit)"},
    ) as client:
        hub = Hub(client, settings)
        results = [await audit_event(hub, year, round_number) for year, round_number in EVENTS]
    print(json.dumps({"events": results}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
