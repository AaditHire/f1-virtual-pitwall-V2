# ruff: noqa: F811

from unittest.mock import AsyncMock

import httpx
from test_analysis import history as analysis_history  # noqa: F401
from test_strategy import strategy_history

from f1_pitwall.domain.replay import PitStop
from f1_pitwall.main import create_app
from f1_pitwall.services.analysis_context import AnalysisContext
from f1_pitwall.services.pitwall import build_pitwall_snapshot, build_timeline, evaluate_driver


def test_full_grid_snapshot_and_driver_detail(analysis_history):
    race = strategy_history(analysis_history)
    context = AnalysisContext(race, 22)
    snapshot = build_pitwall_snapshot(context, 100)
    assert snapshot.reanchored_to_observed_lap is True
    assert snapshot.operating_envelope == "SHORT_HORIZON_ONLY"
    assert snapshot.horizons_laps == [1, 3, 5]
    assert snapshot.lap == 22
    assert len(snapshot.drivers) == len(context.state.drivers)
    active = [row for row in snapshot.drivers if row.status == "active"]
    assert active
    assert all(row.decision_state in {"ACTIONABLE", "CAUTION", "COARSE_ONLY"} for row in active)
    assert all(not row.actions for row in snapshot.drivers)
    assert all(alert.driver_id for alert in snapshot.alerts)

    detail = build_pitwall_snapshot(AnalysisContext(race, 22), 100, "d0")
    driver = next(row for row in detail.drivers if row.driver.id == "d0")
    assert driver.actions and driver.engineering_analysis is not None
    assert all(
        {outcome.horizon_laps for outcome in action.outcomes} == {1, 3, 5}
        for action in driver.actions
    )
    assert driver.data_quality["maximum_horizon_laps"] == 5

    inactive_context = AnalysisContext(race, 22)
    inactive_context.driver("d0").status = "retired"
    inactive = evaluate_driver(inactive_context, "d0", 100)
    assert inactive.status == "retired"
    assert inactive.recommendation is None
    assert inactive.evaluated_action_count == 0


def test_lapped_driver_never_gets_fabricated_seconds_gap(analysis_history):
    race = strategy_history(analysis_history)
    context = AnalysisContext(race, 22)
    driver = context.driver("d0")
    driver.lapped = True
    driver.laps_behind = 1
    result = evaluate_driver(context, "d0", 100)
    assert result.gap_kind == "LAP_DEFICIT"
    assert result.laps_behind == 1
    assert result.gap_to_leader_seconds is None


def test_timeline_reanchors_and_tracks_changes(analysis_history):
    race = strategy_history(analysis_history)
    timeline = build_timeline(race, 20, 22, "d0", 100)
    assert timeline.reanchored_laps == [20, 21, 22]
    assert len(timeline.drivers) == 1
    assert [row.lap for row in timeline.drivers[0].entries] == [20, 21, 22]
    assert 0 <= timeline.metrics["flip_rate"] <= 1
    assert 0 <= timeline.metrics["unsupported_flip_rate"] <= 1


def pitwall_output(race):
    return build_pitwall_snapshot(AnalysisContext(race, 22), 100).model_dump(
        exclude={"elapsed_seconds"}
    )


def test_pitwall_ignores_future_mutation(analysis_history):
    race = strategy_history(analysis_history)
    baseline = pitwall_output(race)
    context = AnalysisContext(race, 22)
    changed = race.model_copy(deep=True)
    for row in changed.laps:
        if row.available_at > context.cutoff:
            row.lap_time_seconds = 1
    for row in changed.timing:
        if row.at > context.cutoff:
            row.position, row.gap_to_leader = 1, 0
    changed.pit_stops.append(
        PitStop(driver_id="d0", entered_at=context.cutoff + 1, exited_at=context.cutoff + 20)
    )
    assert pitwall_output(changed) == baseline


async def test_pitwall_api_snapshot_detail_and_timeline(analysis_history):
    race = strategy_history(analysis_history)
    app = create_app()
    async with app.router.lifespan_context(app):
        app.state.hub.replay.load_race = AsyncMock(return_value=race)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            snapshot = await client.get("/api/v1/pitwall/2024/1/22?trajectory_count=100")
            detail = await client.get("/api/v1/pitwall/2024/1/22/drivers/d0?trajectory_count=100")
            timeline = await client.get(
                "/api/v1/pitwall/2024/1/timeline",
                params={"start_lap": 21, "end_lap": 22, "driver": "d0"},
            )
            invalid_count = await client.get(
                "/api/v1/pitwall/2024/1/22", params={"trajectory_count": 250}
            )
            invalid_range = await client.get(
                "/api/v1/pitwall/2024/1/timeline",
                params={"start_lap": 22, "end_lap": 21},
            )
        assert snapshot.status_code == detail.status_code == timeline.status_code == 200
        assert invalid_count.status_code == invalid_range.status_code == 422
        assert snapshot.json()["drivers"]
        assert detail.json()["actions"]
        assert timeline.json()["reanchored_laps"] == [21, 22]
