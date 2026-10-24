import gzip
from datetime import date
from unittest.mock import AsyncMock, Mock

import httpx
from test_replay import race as fixture

from f1_pitwall.core.config import Settings
from f1_pitwall.providers.replay_snapshots import (
    SnapshotStore,
    decode_snapshot,
    encode_snapshot,
    snapshot_name,
)
from f1_pitwall.services.replay import ReplayService
from scripts import build_replay_snapshots


def replay(store):
    race = fixture.__wrapped__()
    seasons, jolpica, provider = AsyncMock(), AsyncMock(), Mock()
    jolpica.calendar.return_value = [race.event]
    provider.load.return_value = race
    return race, provider, ReplayService(seasons, jolpica, provider, snapshots=store)


def http_store(handler, base="https://snapshots.test/replays"):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client, SnapshotStore(client, Settings(replay_snapshot_url=base))


def test_snapshot_round_trip_is_lossless_and_reproducible():
    race = fixture.__wrapped__()
    data = encode_snapshot(race)
    assert decode_snapshot(data) == race
    assert encode_snapshot(race) == data
    assert snapshot_name(2030, 1) == "replay-v1-2030-01.json.gz"


async def test_published_snapshot_is_used_instead_of_fastf1():
    race = fixture.__wrapped__()
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, content=encode_snapshot(race))

    client, store = http_store(handler)
    async with client:
        _, provider, service = replay(store)
        assert await service.load_race(race.event.year, race.event.round) == race
    provider.load.assert_not_called()
    name = snapshot_name(race.event.year, race.event.round)
    assert seen == [f"https://snapshots.test/replays/{name}"]


async def test_missing_corrupt_or_mismatched_snapshots_fall_back_to_fastf1():
    race = fixture.__wrapped__()
    wrong = race.model_copy(update={"event": race.event.model_copy(update={"round": 99})})
    responses = [
        httpx.Response(404),
        httpx.Response(500),
        httpx.Response(200, content=b"not gzip"),
        httpx.Response(200, content=gzip.compress(b"{}")),
        httpx.Response(200, content=encode_snapshot(wrong)),
    ]
    for response in responses:
        client, store = http_store(lambda request, response=response: response)
        async with client:
            _, provider, service = replay(store)
            assert await service.load_race(race.event.year, race.event.round) == race
        provider.load.assert_called_once()

    def unreachable(request):
        raise httpx.ConnectError("down")

    client, store = http_store(unreachable)
    async with client:
        _, provider, service = replay(store)
        assert await service.load_race(race.event.year, race.event.round) == race
    provider.load.assert_called_once()


async def test_snapshots_can_be_read_from_a_local_directory(tmp_path):
    race = fixture.__wrapped__()
    (tmp_path / snapshot_name(race.event.year, race.event.round)).write_bytes(encode_snapshot(race))
    async with httpx.AsyncClient() as client:
        store = SnapshotStore(client, Settings(replay_snapshot_url=str(tmp_path)))
        _, provider, service = replay(store)
        assert await service.load_race(race.event.year, race.event.round) == race
    provider.load.assert_not_called()


async def test_build_script_skips_published_and_unfinished_races(tmp_path, monkeypatch):
    race = fixture.__wrapped__()
    event = race.event
    published = event.model_copy(update={"round": 2})
    upcoming = event.model_copy(update={"round": 3, "race_date": date(2030, 12, 1)})
    broken = event.model_copy(update={"round": 4})
    hub = Mock()
    hub.jolpica.calendar = AsyncMock(return_value=[event, published, upcoming, broken])
    hub.replay.load_race = AsyncMock(
        side_effect=lambda year, round: race if round == event.round else 1 / 0
    )
    settings = []
    monkeypatch.setattr(build_replay_snapshots, "Hub", lambda client, s: settings.append(s) or hub)
    failures = await build_replay_snapshots.build(
        [event.year], tmp_path, {snapshot_name(event.year, 2)}, date(2030, 11, 1)
    )
    assert settings[0].replay_snapshot_url is None
    assert [call.args[1] for call in hub.replay.load_race.await_args_list] == [event.round, 4]
    assert failures == [snapshot_name(event.year, 4)]
    assert decode_snapshot((tmp_path / snapshot_name(event.year, event.round)).read_bytes()) == race
    assert build_replay_snapshots.years("2021-2023") == [2021, 2022, 2023]
