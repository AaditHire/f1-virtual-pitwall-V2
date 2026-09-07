# ruff: noqa: F811

from unittest.mock import AsyncMock

import httpx
from test_analysis import history as analysis_history  # noqa: F401
from test_strategy import strategy_history

from f1_pitwall.domain.replay import LapValidity, PitStop
from f1_pitwall.main import create_app
from f1_pitwall.services.analysis_context import AnalysisContext
from f1_pitwall.services.simulation import (
    build_short_horizon_state,
    compare_counterfactual_actions,
    simulate_action,
)


def test_minimal_state_and_independent_one_three_five_lap_outcomes(analysis_history):
    context = AnalysisContext(strategy_history(analysis_history), 22)
    state = build_short_horizon_state(context, "d0")
    assert state.active and state.current_position == 1
    assert state.relative_pace_laps
    assert not hasattr(state, "pit_loss")

    pit = simulate_action(context, "d0", "PIT_NOW_HARD")
    extend = simulate_action(context, "d0", "EXTEND_3")
    assert [row.horizon_laps for row in pit.outcomes] == [1, 3, 5]
    assert [row.horizon_laps for row in extend.outcomes] == [1, 3, 5]
    for result in (pit, extend):
        for outcome in result.outcomes:
            assert outcome.components["phase4_score_used"] is False
            assert outcome.components["phase4_recommendation_used"] is False
            assert outcome.components["zero_slope_tyre_assumption"] is True
            assert "candidate_raw_slope" not in str(outcome.components)
            assert outcome.position_range is not None
            assert outcome.components["lap_by_lap_projection"]
            assert outcome.physical_track_position == outcome.expected_position
            assert outcome.net_race_position_range is not None
    assert extend.outcomes[1].components["pit_occurs_within_horizon"] is False
    assert extend.outcomes[2].components["pit_occurs_within_horizon"] is True
    pit_components = pit.outcomes[0].components["pit_loss_components"]
    assert pit_components["combined_observed_stop_lap_residual_seconds"] is not None
    assert pit_components["stationary_seconds"] is None
    assert pit.outcomes[-1].components["nearby_car_projection"]
    assert any(
        row["initial_gap"] != row["projected_gap"]
        for row in pit.outcomes[-1].components["nearby_car_projection"]
        if row["relative_pace"] is not None and row["relative_pace"] != 0
    )


def test_common_snapshot_comparison_holds_when_uncertainty_overlaps(analysis_history):
    context = AnalysisContext(strategy_history(analysis_history), 22)
    overlap_calibration = {
        kind: {horizon: {"bias_seconds": 0.0, "mae_seconds": 100.0} for horizon in (3, 5)}
        for kind in ("PIT_NOW", "EXTEND")
    }
    comparison = compare_counterfactual_actions(context, "d0", overlap_calibration)
    assert {action.action for action in comparison.actions} == {
        "PIT_NOW_HARD",
        "EXTEND_1",
        "EXTEND_2",
        "EXTEND_3",
    }
    assert comparison.decision == "HOLD_NO_CLEAR_ADVANTAGE"
    assert comparison.uncertainty_overlap is True
    assert comparison.preferred_action is None


def test_comparison_selects_action_only_outside_error_bands(analysis_history):
    context = AnalysisContext(strategy_history(analysis_history), 22)
    separated = {
        kind: {
            horizon: {
                "bias_seconds": -100.0 if kind == "PIT_NOW" else 0.0,
                "mae_seconds": 0.1,
            }
            for horizon in (1, 3, 5)
        }
        for kind in ("PIT_NOW", "EXTEND")
    }
    comparison = compare_counterfactual_actions(context, "d0", separated)
    assert comparison.decision == "PIT_NOW"
    assert comparison.uncertainty_overlap is False
    assert comparison.preferred_action == "PIT_NOW_HARD"


def simulation_output(source):
    return compare_counterfactual_actions(AnalysisContext(source, 22), "d0").model_dump()


def test_simulation_is_independent_of_future_mutation_and_removal(analysis_history):
    race = strategy_history(analysis_history)
    context = AnalysisContext(race, 22)
    baseline = simulation_output(race)
    assert simulation_output(context.race) == baseline

    changed = race.model_copy(deep=True)
    for row in changed.laps:
        if row.available_at > context.cutoff:
            row.lap_time_seconds, row.completed_at = 1, 999999
    for sample in changed.timing:
        if sample.at > context.cutoff:
            sample.position, sample.gap_to_leader = 1, 0
    for stint in changed.stints:
        if stint.observed_at > context.cutoff:
            stint.compound, stint.tyre_age = "WET", 999
    changed.pit_stops.append(
        PitStop(driver_id="d0", entered_at=context.cutoff + 1, exited_at=context.cutoff + 30)
    )
    changed.lap_validity.append(
        LapValidity(driver_id="d0", lap_number=30, at=context.cutoff + 1, valid=False)
    )
    assert simulation_output(changed) == baseline


async def test_simulation_api_get_post_and_errors(analysis_history):
    race = strategy_history(analysis_history)
    app = create_app()
    async with app.router.lifespan_context(app):
        loader = AsyncMock(return_value=race)
        app.state.hub.replay.load_race = loader
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            prefix = "/api/v1/strategy/2024/1/22/drivers/d0/counterfactuals"
            comparison = await client.get(prefix)
            outcome = await client.post(
                "/api/v1/simulation/short-horizon",
                json={
                    "year": 2024,
                    "round": 1,
                    "lap": 22,
                    "driver_id": "d0",
                    "action": "PIT_NOW_HARD",
                },
            )
            assert comparison.status_code == outcome.status_code == 200
            assert outcome.json() == comparison.json()["actions"][0]
            bad = await client.post(
                "/api/v1/simulation/short-horizon",
                json={
                    "year": 2024,
                    "round": 1,
                    "lap": 22,
                    "driver_id": "d0",
                    "action": "WIN_RACE",
                },
            )
            assert bad.status_code == 404
        assert loader.await_count == 3
