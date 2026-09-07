# ruff: noqa: F811

from unittest.mock import AsyncMock

import httpx
from test_analysis import history as analysis_history  # noqa: F401

from f1_pitwall.domain.replay import LapValidity, PitStop, Stint
from f1_pitwall.main import create_app
from f1_pitwall.services.analysis_context import AnalysisContext
from f1_pitwall.services.strategy import (
    PAIR_SIGNAL_CAP_SECONDS,
    analyze_strategy_all,
    compare_actions,
    generate_actions,
    recommend_driver_action,
)


def strategy_history(source):
    changed = source.model_copy(deep=True)
    # Make HARD an observed race compound without altering any driver's current stint.
    changed.stints.append(
        Stint(
            driver_id="d5",
            number=1,
            observed_at=0.1,
            compound="HARD",
            tyre_age=0,
            age_observed_at=0.1,
        )
    )
    return changed


def test_full_grid_covers_active_and_excludes_retired(analysis_history):
    race = strategy_history(analysis_history)
    cutoff = AnalysisContext(race, 22).cutoff
    retired = next(sample for sample in race.timing if sample.driver_id == "d5")
    retired.at, retired.retired = cutoff - 0.1, True
    result = analyze_strategy_all(AnalysisContext(race, 22))
    assert result.active_count == 5
    assert len(result.decisions) == 5
    assert {row.driver.id for row in result.decisions} == {f"d{i}" for i in range(5)}
    assert [(row.driver.id, row.status) for row in result.excluded] == [("d5", "retired")]
    assert all(row.actions for row in result.decisions)


def test_actions_are_short_legal_and_ignore_diagnostic_slopes(analysis_history):
    context = AnalysisContext(strategy_history(analysis_history), 22)
    generated = generate_actions(context, "d0")
    assert [row.id for row in generated] == [
        "PIT_NOW_HARD",
        "EXTEND_1",
        "EXTEND_2",
        "EXTEND_3",
    ]
    decision = recommend_driver_action(context, "d0")
    assert decision.recommended_action in {
        "PIT_NOW",
        "EXTEND",
        "HOLD_NO_CLEAR_ADVANTAGE",
        "INSUFFICIENT_DATA",
    }
    for action in decision.actions:
        assert action.horizon_laps == 3
        assert action.score_components["tyre_model"] == "zero_slope"
        assert action.score_components["diagnostic_slopes_used"] is False
        assert abs(action.score_components["bounded_fresh_tyre_delta_seconds_per_lap"]) <= 0.911
        signal = action.score_components.get("pair_signal")
        if signal:
            assert abs(signal["capped_contribution_seconds"]) <= PAIR_SIGNAL_CAP_SECONDS


def test_close_scores_hold_and_action_comparison_is_ranked(analysis_history):
    result = recommend_driver_action(
        AnalysisContext(strategy_history(analysis_history), 22), "d1"
    )
    assert result.decision_margin < 0.75
    assert result.recommended_action == "HOLD_NO_CLEAR_ADVANTAGE"
    comparison = compare_actions(
        AnalysisContext(strategy_history(analysis_history), 22), "d0"
    )
    scores = [row.action_score for row in comparison.actions if row.action_score is not None]
    assert scores == sorted(scores, reverse=True)


def test_grid_objective_varies_smoothly_and_weak_signals_cannot_trigger_pit(
    analysis_history,
):
    grid = analyze_strategy_all(AnalysisContext(strategy_history(analysis_history), 22))
    front = grid.decisions[0].actions[0].score_components["objective"]["risk_weight"]
    back = grid.decisions[-1].actions[0].score_components["objective"]["risk_weight"]
    assert front > back
    for decision in grid.decisions:
        if decision.recommended_action != "PIT_NOW":
            continue
        chosen = next(
            action for action in decision.actions if action.action_score == decision.decision_score
        )
        assert chosen.traffic_status != "UNKNOWN"
        assert chosen.score_components["traffic_opportunity_seconds"] >= 0.75


def strategy_output(source):
    return analyze_strategy_all(AnalysisContext(source, 22)).model_dump()


def test_strategy_output_is_independent_of_future_mutation_and_removal(analysis_history):
    race = strategy_history(analysis_history)
    context = AnalysisContext(race, 22)
    baseline = strategy_output(race)
    removed = context.race
    assert strategy_output(removed) == baseline

    changed = race.model_copy(deep=True)
    for row in changed.laps:
        if row.available_at > context.cutoff:
            row.lap_time_seconds, row.completed_at = 1, 999999
    for sample in changed.timing:
        if sample.at > context.cutoff:
            sample.position, sample.retired = 1, True
    for stint in changed.stints:
        if stint.observed_at > context.cutoff:
            stint.compound, stint.tyre_age = "WET", 999
    changed.pit_stops.append(
        PitStop(driver_id="d0", entered_at=context.cutoff + 1, exited_at=context.cutoff + 30)
    )
    changed.lap_validity.append(
        LapValidity(driver_id="d0", lap_number=30, at=context.cutoff + 1, valid=False)
    )
    assert strategy_output(changed) == baseline


async def test_strategy_api_routes_share_one_replay_per_request(analysis_history):
    race = strategy_history(analysis_history)
    app = create_app()
    async with app.router.lifespan_context(app):
        loader = AsyncMock(return_value=race)
        app.state.hub.replay.load_race = loader
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            prefix = "/api/v1/strategy/2024/1/22"
            decision = await client.get(f"{prefix}/drivers/d0")
            grid = await client.get(f"{prefix}/all")
            actions = await client.get(f"{prefix}/drivers/d0/actions")
            assert decision.status_code == grid.status_code == actions.status_code == 200
            assert len(grid.json()["decisions"]) == grid.json()["active_count"] == 6
            assert actions.json()["driver"]["id"] == "d0"
            assert (await client.get(f"{prefix}/drivers/missing")).status_code == 404
        assert loader.await_count == 4
