"""Real FastF1 historical archives; no mocked provider traffic."""

import asyncio
import json

import fastf1
import httpx
import pytest
import pytest_asyncio
from fastf1 import _api

from f1_pitwall.core.config import Settings
from f1_pitwall.providers.fastf1 import normalize_archive, seconds
from f1_pitwall.services.hub import Hub
from f1_pitwall.services.race_state import RaceStateBuilder, cutoff_for

pytestmark = [pytest.mark.network, pytest.mark.asyncio(loop_scope="module")]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def replay_hub():
    async with httpx.AsyncClient(follow_redirects=True) as client:
        yield Hub(client, Settings.from_env())


def source_session(year, round):
    session = fastf1.get_session(year, round, "R")
    session.load(telemetry=False, weather=False, messages=False)
    return session


@pytest.mark.parametrize("selection", [(2021, 1), (2023, 1), (2024, 1), "recent"])
async def test_replay_gates_a_b_c_d_e(replay_hub, selection):
    hub = replay_hub
    if selection == "recent":
        year = (await hub.seasons.get_active_season()).year
        event = await hub.jolpica.latest_completed(year)
        selection = (year, event.round)
    race = await hub.replay.load_race(*selection)
    available = await hub.replay.get_available_laps(*selection)
    lap = available.laps[len(available.laps) // 2]
    state = await hub.replay.get_race_state(*selection, lap)
    source = await asyncio.to_thread(source_session, *selection)
    assert len(state.drivers) == len(race.participants) == len(source.drivers)
    assert {str(d.driver.number) for d in state.drivers} == set(source.drivers)
    assert len({d.driver.id for d in state.drivers}) == len(state.drivers)
    assert state.total_scheduled_laps is not None
    assert available.laps[0] == 1 and available.laps[-1] > lap
    positions = [d.position for d in state.drivers if d.position is not None]
    assert positions == sorted(positions)
    if selection == (2024, 1):
        assert any(d.lapped is True and d.laps_behind > 0 for d in state.drivers)
    verified = 0
    for driver in state.drivers:
        if driver.tyre_age_observed_at is None:
            continue
        # Independent FastF1 processed source comparison at the tyre observation's time,
        # not against a future lap or end-of-race stint summary.
        rows = source.laps[
            (source.laps.DriverNumber == str(driver.driver.number))
            & (source.laps.Time.dt.total_seconds() <= driver.tyre_age_observed_at)
        ]
        if rows.empty:
            continue
        row = rows.iloc[-1]
        if row.Compound == driver.compound and int(row.Stint) == driver.stint_number:
            # TimingAppData tyre-life updates can lag a lap crossing. Keep reported
            # age in the product and verify the independently processed value when aligned.
            if row.TyreLife == driver.tyre_age:
                verified += 1
    assert verified >= 3, f"Only {verified} aligned tyre/source observations in {selection}"
    print(
        json.dumps(
            {
                "year": selection[0],
                "round": selection[1],
                "event": race.event.name,
                "lap": lap,
                "participants": len(state.drivers),
                "source_tyres_verified": verified,
                "scheduled_laps": state.total_scheduled_laps,
            }
        )
    )


async def test_replay_gate_f_timestamped_real_retirement(replay_hub):
    race = await replay_hub.replay.load_race(2023, 1)
    retirement = next(s for s in race.timing if s.retired is True)
    builder = RaceStateBuilder()
    early = builder.build(race, 5)
    driver = next(d for d in early.drivers if d.driver.id == retirement.driver_id)
    assert driver.active is True and driver.retired is False
    anchors = sorted((cutoff_for(race, lap), lap) for lap in {r.number for r in race.laps})
    before = max(lap for at, lap in anchors if at < retirement.at)
    after = min(lap for at, lap in anchors if at >= retirement.at)
    before_driver = next(
        d for d in builder.build(race, before).drivers if d.driver.id == retirement.driver_id
    )
    after_driver = next(
        d for d in builder.build(race, after).drivers if d.driver.id == retirement.driver_id
    )
    assert before_driver.retired is False
    assert after_driver.retired is True and after_driver.active is False
    print(
        json.dumps(
            {
                "retirement_driver": driver.driver.full_name,
                "reported_at": retirement.at,
                "before_lap": before,
                "after_lap": after,
            }
        )
    )


async def test_replay_gate_g_raw_archive_prefix_independence(replay_hub):
    race = await replay_hub.replay.load_race(2023, 1)
    builder = RaceStateBuilder()
    baseline = builder.build(race, 20)
    session = fastf1.get_session(2023, 1, "R")
    streams = {}
    for topic in (
        "driver_list",
        "session_status",
        "timing_data",
        "timing_app_data",
        "track_status",
        "lap_count",
        "race_control_messages",
    ):
        raw = await asyncio.to_thread(_api.fetch_page, session.api_path, topic)
        streams[topic] = [row for row in raw if seconds(row[0]) <= baseline.session_time_seconds]
    prefix = normalize_archive(race.event, streams)
    assert builder.build(prefix, 20) == baseline


async def test_replay_gate_h_api_real_snapshot(replay_hub):
    from f1_pitwall.main import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        # Reuse the actual loaded provider session, never fabricated or mocked data.
        app.state.hub = replay_hub
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/v1/replay/2024/1/25")
            assert response.status_code == 200, response.text
            state = response.json()
            assert state["current_lap"] == 25 and len(state["drivers"]) > 0
            identity = state["drivers"][0]["driver"]["id"]
            individual = await client.get(f"/api/v1/replay/2024/1/25/drivers/{identity}")
            assert individual.json() == state["drivers"][0]
            assert (await client.get("/api/v1/replay/2024/1/laps")).json()["participants"] == len(
                state["drivers"]
            )
            assert (await client.get("/api/v1/replay/2024/1/999")).status_code == 404
            assert (await client.get("/api/v1/replay/2024/1/0")).status_code == 422
            assert (
                await client.get("/api/v1/replay/2024/1/25/drivers/not-real")
            ).status_code == 404
