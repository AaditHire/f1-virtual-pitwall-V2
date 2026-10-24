"""Prebuilt replay archives, so serverless cold starts can skip FastF1.

A snapshot is a gzipped HistoricalRace produced by the same normalization as FastF1Provider.
scripts/build_replay_snapshots.py writes them and CI publishes them as GitHub release assets.
"""

import gzip
import logging
from pathlib import Path

import httpx

from f1_pitwall.core.config import Settings
from f1_pitwall.domain.models import Event
from f1_pitwall.domain.replay import HistoricalRace

log = logging.getLogger(__name__)

# Bump when normalization or HistoricalRace changes, so stale snapshots are ignored and rebuilt.
SNAPSHOT_VERSION = 1
MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024


def snapshot_name(year: int, round: int) -> str:
    return f"replay-v{SNAPSHOT_VERSION}-{year}-{round:02d}.json.gz"


def encode_snapshot(race: HistoricalRace) -> bytes:
    payload = race.model_dump_json(exclude_defaults=True).encode()
    return gzip.compress(payload, compresslevel=9, mtime=0)


def decode_snapshot(data: bytes) -> HistoricalRace:
    return HistoricalRace.model_validate_json(gzip.decompress(data))


class SnapshotStore:
    """Reads snapshots from an HTTP base URL or a local directory; misses fall back to FastF1."""

    def __init__(self, client: httpx.AsyncClient, settings: Settings):
        self.client, self.settings = client, settings
        self.base = settings.replay_snapshot_url

    async def fetch(self, event: Event) -> bytes | None:
        if not self.base:
            return None
        name = snapshot_name(event.year, event.round)
        if not self.base.startswith(("http://", "https://")):
            path = Path(self.base) / name
            return path.read_bytes() if path.is_file() else None
        url = f"{self.base.rstrip('/')}/{name}"
        try:
            response = await self.client.get(url, timeout=self.settings.timeout)
        except httpx.HTTPError as exc:
            log.warning("replay_snapshot_unreachable url=%s error=%s", url, type(exc).__name__)
            return None
        if response.status_code == 404:
            return None
        if response.status_code != 200 or len(response.content) > MAX_SNAPSHOT_BYTES:
            log.warning(
                "replay_snapshot_rejected url=%s status=%s bytes=%s",
                url,
                response.status_code,
                len(response.content),
            )
            return None
        return response.content

    def decode(self, event: Event, data: bytes) -> HistoricalRace | None:
        try:
            race = decode_snapshot(data)
        except (OSError, EOFError, ValueError) as exc:
            log.warning(
                "replay_snapshot_invalid year=%s round=%s error=%s", event.year, event.round, exc
            )
            return None
        if (race.event.year, race.event.round) != (event.year, event.round):
            log.warning("replay_snapshot_mismatch year=%s round=%s", event.year, event.round)
            return None
        return race
