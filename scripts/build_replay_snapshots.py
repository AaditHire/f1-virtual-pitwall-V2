"""Build replay snapshots for completed races, skipping ones already published.

    python scripts/build_replay_snapshots.py --years 2021-2026 --out snapshots --existing names.txt

Snapshots always come from FastF1, never from previously published snapshots.
"""

import argparse
import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx

from f1_pitwall.core.config import Settings
from f1_pitwall.providers.replay_snapshots import encode_snapshot, snapshot_name
from f1_pitwall.services.hub import Hub

# Archives usually complete within hours of the flag; a race this recent may not be ready yet.
SETTLE_DAYS = 3


def years(value: str) -> list[int]:
    first, _, last = value.partition("-")
    return list(range(int(first), int(last or first) + 1))


async def build(selected: list[int], out: Path, existing: set[str], today: date) -> list[str]:
    out.mkdir(parents=True, exist_ok=True)
    settings = Settings.from_env().model_copy(update={"replay_snapshot_url": None})
    failures = []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        hub = Hub(client, settings)
        for year in selected:
            for event in await hub.jolpica.calendar(year):
                name = snapshot_name(year, event.round)
                if event.race_date >= today or name in existing or (out / name).exists():
                    continue
                try:
                    race = await hub.replay.load_race(year, event.round)
                except Exception as exc:
                    settled = event.race_date <= today - timedelta(days=SETTLE_DAYS)
                    print(f"{'FAILED' if settled else 'not ready'} {name}: {exc}", flush=True)
                    if settled:
                        failures.append(name)
                    continue
                data = encode_snapshot(race)
                (out / name).write_bytes(data)
                print(f"built {name} ({len(data) // 1024} KB)", flush=True)
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--years", type=years, default=[date.today().year])
    parser.add_argument("--out", type=Path, default=Path("snapshots"))
    parser.add_argument("--existing", type=Path, help="file listing already-published names")
    args = parser.parse_args()
    existing = set(args.existing.read_text().split()) if args.existing else set()
    failures = asyncio.run(build(args.years, args.out, existing, date.today()))
    if failures:
        print(f"{len(failures)} completed races could not be built: {', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
