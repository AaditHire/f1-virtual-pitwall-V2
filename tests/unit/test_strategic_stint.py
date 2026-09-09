# ruff: noqa: F811

from test_analysis import history as analysis_history  # noqa: F401
from test_strategy import strategy_history

from f1_pitwall.domain.strategic import StintCompoundPrior, StrategicActionValue
from f1_pitwall.services.analysis_context import AnalysisContext
from f1_pitwall.services.strategic_stint import (
    _cost_after_handoff,
    compare_strategic_actions,
    estimate_compound_prior,
)


def _context(history, lap=22):
    return AnalysisContext(strategy_history(history), lap)


def test_all_actions_share_terminal_and_include_owed_stop(analysis_history):
    result = compare_strategic_actions(_context(analysis_history), "d0", 100, 11)
    assert result.actions
    assert {action.terminal_lap for action in result.actions} == {result.terminal_lap}
    assert result.common_terminal_point is True
    assert result.owed_stop_accounting is True
    assert all(action.components["owed_stop_included_by_terminal"] for action in result.actions)
    delayed = next(action for action in result.actions if action.delay_laps == 5)
    assert delayed.future_stop_obligations == ["Owed stop paid after 5 extension laps"]


def test_delayed_stop_uses_tactical_transition_then_strategic_segment(analysis_history):
    result = compare_strategic_actions(_context(analysis_history), "d0", 100, 12)
    by_delay = {action.delay_laps: action for action in result.actions}
    assert by_delay[0].tactical_action == "PIT_NOW_HARD"
    assert by_delay[2].tactical_action == "EXTEND_2"
    assert by_delay[5].tactical_action == "EXTEND_5"
    assert all(action.tactical_handoff_lap == result.current_lap + 5 for action in result.actions)
    assert result.components["recursive_tactical_rollout_beyond_five_laps"] is False


def test_owed_stop_is_charged_immediately_after_five_lap_handoff():
    action = StrategicActionValue(
        action="EXTEND_5_THEN_PIT_HARD",
        compound="HARD",
        delay_laps=5,
        terminal_lap=20,
        tactical_action="EXTEND_5",
        tactical_handoff_lap=10,
        tactical_5_lap_delta_seconds=2,
        tactical_5_lap_interval_90=(1, 3),
        components={"current_pace_uncertainty": 0.5},
    )
    prior = StintCompoundPrior(
        compound="HARD",
        expected_relative_pace_seconds_per_lap=-0.5,
        median_competitive_stint_laps=20,
        source="REGULATION_ERA",
        uncertainty_seconds_per_lap=0.5,
    )
    expected, _ = _cost_after_handoff(action, 6, 0.25, 20, 1, prior)
    assert expected == 21.5


def test_pit_now_handoff_does_not_pay_the_same_stop_twice():
    action = StrategicActionValue(
        action="PIT_NOW_HARD",
        compound="HARD",
        delay_laps=0,
        terminal_lap=20,
        tactical_action="PIT_NOW_HARD",
        tactical_handoff_lap=10,
        tactical_5_lap_delta_seconds=22,
        tactical_5_lap_interval_90=(20, 24),
        components={"current_pace_uncertainty": 0.5},
    )
    prior = StintCompoundPrior(
        compound="HARD",
        expected_relative_pace_seconds_per_lap=-1,
        median_competitive_stint_laps=20,
        source="REGULATION_ERA",
        uncertainty_seconds_per_lap=0.5,
    )
    expected, _ = _cost_after_handoff(action, 6, 0.25, 20, 1, prior)
    assert expected == 21


def test_break_even_is_reported_against_delayed_stop(analysis_history):
    result = compare_strategic_actions(_context(analysis_history), "d0", 100, 13)
    pit_now = next(action for action in result.actions if action.delay_laps == 0)
    assert pit_now.break_even_lap is not None
    assert result.current_lap + 5 <= pit_now.break_even_lap <= result.terminal_lap


def test_compound_prior_returns_counts_range_and_chronological_source(analysis_history):
    prior = estimate_compound_prior(_context(analysis_history), "d0", "HARD")
    assert prior.source == "REGULATION_ERA"
    assert prior.pace_sample_count >= 10
    assert prior.length_sample_count >= 10
    assert prior.pace_iqr_seconds_per_lap[0] <= prior.pace_iqr_seconds_per_lap[1]
    assert prior.competitive_stint_iqr_laps[0] <= prior.competitive_stint_iqr_laps[1]
    assert prior.components["historical_samples_strictly_before_event"] is True
    assert prior.components["raw_degradation_slope_used"] is False


def test_future_prior_samples_are_excluded(analysis_history, monkeypatch):
    context = _context(analysis_history)
    past = {
        "race": "2023/1",
        "race_date": "2023-01-01",
        "circuit_id": "other",
        "driver_id": "past",
        "constructor_id": None,
        "compound": "HARD",
        "relative_pace_seconds_per_lap": 1.0,
        "competitive_stint_length_laps": 20,
    }
    future = {**past, "race_date": "2025-01-01", "relative_pace_seconds_per_lap": -99}
    monkeypatch.setattr(
        "f1_pitwall.services.strategic_stint._prior_artifact",
        lambda: {"segments": [past] * 10 + [future] * 10},
    )
    monkeypatch.setattr("f1_pitwall.services.strategic_stint._same_race_segments", lambda _: [])
    prior = estimate_compound_prior(context, "d0", "HARD")
    assert prior.pace_iqr_seconds_per_lap == (1.0, 1.0)


def test_strategic_output_ignores_future_observation_mutations(analysis_history):
    race = strategy_history(analysis_history)
    context = AnalysisContext(race, 22)
    expected = compare_strategic_actions(context, "d0", 100, 14)
    changed = race.model_copy(deep=True)
    for lap in changed.laps:
        if lap.available_at > context.cutoff:
            lap.lap_time_seconds = 1
    for timing in changed.timing:
        if timing.at > context.cutoff:
            timing.position, timing.gap_to_leader = 1, 0
    actual = compare_strategic_actions(AnalysisContext(changed, 22), "d0", 100, 14)
    assert actual == expected


def test_missing_prior_evidence_returns_no_strategic_action(analysis_history, monkeypatch):
    monkeypatch.setattr(
        "f1_pitwall.services.strategic_stint._prior_artifact", lambda: {"segments": []}
    )
    monkeypatch.setattr("f1_pitwall.services.strategic_stint._same_race_segments", lambda _: [])
    result = compare_strategic_actions(_context(analysis_history), "d0", 100, 15)
    assert not result.actions
    assert result.best_strategic_action is None
    assert "No action has enough tactical and stint-prior evidence." in result.warnings
