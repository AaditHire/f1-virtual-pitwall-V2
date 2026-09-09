# ruff: noqa: F811

from test_analysis import history as analysis_history  # noqa: F401
from test_strategy import strategy_history

from f1_pitwall.domain.paired import (
    PairedComparison,
    PairedHorizonDistribution,
    PairedRegret,
)
from f1_pitwall.services.analysis_context import AnalysisContext
from f1_pitwall.services.pit_opportunity import calculate_pit_opportunity
from f1_pitwall.services.pitwall import evaluate_driver


def comparison(time_delta, position_delta, frequency, downside=0, cycle_delta=-10, interval=None):
    interval = interval or (time_delta - 0.5, time_delta + 0.5)
    horizon = PairedHorizonDistribution(
        horizon_laps=5,
        trajectory_count=100,
        median_time_delta_seconds=time_delta,
        median_track_position_delta=position_delta,
        median_pit_cycle_position_delta=cycle_delta,
        pit_better_frequency=frequency,
        interval_90=interval,
        downside_tail_90=PairedRegret(pit_seconds=downside),
    )
    return PairedComparison(
        pit_action="PIT_NOW_HARD",
        extend_action="EXTEND_5",
        median_time_delta_seconds=time_delta,
        position_delta=position_delta,
        pit_cycle_position_delta=cycle_delta,
        pit_better_frequency=frequency,
        downside_tail=PairedRegret(pit_seconds=downside),
        outcomes=[horizon],
    )


def test_signal_calculation_exposes_time_position_and_penalties():
    result = calculate_pit_opportunity(
        comparison(-4, -1, 0.8, downside=0.1, interval=(-5, -3)),
        "FULL_EVIDENCE",
        "CLEAR_AIR",
        "USABLE",
    )
    assert result.signal == "STRONG"
    assert result.time_opportunity == "STRONG"
    assert result.position_opportunity == "STRONG"
    assert result.value > 1
    assert result.components["pit_cycle_used_in_signal"] is False
    assert result.components["traffic_penalty"] == 0


def test_strong_marginal_and_no_opportunity_states():
    strong = calculate_pit_opportunity(comparison(-4, 0, 0.8), "TIME_ONLY")
    marginal = calculate_pit_opportunity(
        comparison(-1, 0, 0.6, interval=(-1.2, -0.8)),
        "FULL_EVIDENCE",
        "CLEAR_AIR",
    )
    none = calculate_pit_opportunity(comparison(5, 2, 0), "TIME_POSITION")
    assert strong.signal == "STRONG"
    assert marginal.signal == "MARGINAL"
    assert none.signal == "NO_OPPORTUNITY"


def test_pit_cycle_is_diagnostic_and_cannot_change_signal():
    favorable_cycle = calculate_pit_opportunity(
        comparison(5, 2, 0, cycle_delta=-12), "FULL_EVIDENCE"
    )
    adverse_cycle = calculate_pit_opportunity(comparison(5, 2, 0, cycle_delta=12), "FULL_EVIDENCE")
    assert favorable_cycle.signal == adverse_cycle.signal == "NO_OPPORTUNITY"
    assert favorable_cycle.value == adverse_cycle.value
    assert favorable_cycle.components["pit_cycle_position_delta_diagnostic"] == -12


def test_signal_is_reproducible_for_identical_evidence():
    evidence = comparison(-3, -1, 0.75, downside=0.2)
    first = calculate_pit_opportunity(evidence, "FULL_EVIDENCE", "LIGHT_TRAFFIC")
    second = calculate_pit_opportunity(evidence, "FULL_EVIDENCE", "LIGHT_TRAFFIC")
    assert first == second


def test_opportunity_signal_ignores_future_observation_mutations(analysis_history):
    race = strategy_history(analysis_history)
    context = AnalysisContext(race, 22)
    baseline = evaluate_driver(context, "d0", 100, detail=True)
    expected = calculate_pit_opportunity(baseline.paired_comparison)

    changed = race.model_copy(deep=True)
    for lap in changed.laps:
        if lap.available_at > context.cutoff:
            lap.lap_time_seconds = 1
    for timing in changed.timing:
        if timing.at > context.cutoff:
            timing.position, timing.gap_to_leader = 1, 0
    actual_driver = evaluate_driver(AnalysisContext(changed, 22), "d0", 100, detail=True)
    actual = calculate_pit_opportunity(actual_driver.paired_comparison)

    assert actual == expected
