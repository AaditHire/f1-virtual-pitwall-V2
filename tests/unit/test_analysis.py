from datetime import date
from unittest.mock import AsyncMock

import httpx
import pytest

from f1_pitwall.domain.models import Circuit, Driver, Event, Session
from f1_pitwall.domain.replay import (
    ControlSample,
    HistoricalRace,
    LapData,
    LapValidity,
    Participant,
    PitStop,
    Stint,
    TimingSample,
)
from f1_pitwall.main import create_app
from f1_pitwall.providers.fastf1 import normalize_lap_validity
from f1_pitwall.services.analysis import analyze_driver
from f1_pitwall.services.analysis_context import AnalysisContext
from f1_pitwall.services.fresh_tyre import estimate_fresh_tyre_delta
from f1_pitwall.services.pace import analyze_tyres, get_clean_laps, get_recent_pace, get_stint_pace
from f1_pitwall.services.pair_analysis import calculate_overcut, calculate_undercut
from f1_pitwall.services.pit_analysis import estimate_pit_loss, find_pit_window, predict_pit_rejoin
from f1_pitwall.services.traffic import analyze_traffic, blockage_penalty


@pytest.fixture
def history():
    session = Session(type="Race", name="Race", date=date(2024, 1, 1), source="test")
    event = Event(
        year=2024,
        round=1,
        name="Synthetic",
        race_date=session.date,
        circuit=Circuit(id="synthetic", name="Synthetic"),
    )
    people, laps, timing, stints, pits = [], [], [], [], []
    for i in range(6):
        identity = f"d{i}"
        people.append(Participant(driver=Driver(id=identity, first_name="Test", last_name=str(i))))
        timing.append(
            TimingSample(
                driver_id=identity,
                at=0,
                position=i + 1,
                in_pit=False,
                laps_behind=0,
                laps_completed=0,
            )
        )
        stints.append(
            Stint(
                driver_id=identity,
                number=1,
                observed_at=0,
                compound="MEDIUM",
                tyre_age=0,
                age_observed_at=0,
            )
        )
        elapsed = i * 5
        for n in range(1, 36):
            duration = 100 + (n if n < 10 else n - 10) * 0.2 + i * 0.4
            if n == 10:
                pits.append(
                    PitStop(driver_id=identity, entered_at=elapsed + 70, exited_at=elapsed + 98)
                )
                stints.append(
                    Stint(
                        driver_id=identity,
                        number=2,
                        observed_at=elapsed + 95,
                        compound="MEDIUM",
                        tyre_age=0,
                        age_observed_at=elapsed + 95,
                    )
                )
                duration += 22
            elapsed += duration
            laps.append(
                LapData(
                    driver_id=identity,
                    number=n,
                    completed_at=elapsed,
                    available_at=elapsed,
                    lap_time_seconds=duration,
                )
            )
            timing.append(TimingSample(driver_id=identity, at=elapsed, laps_completed=n))
            stints.append(
                Stint(
                    driver_id=identity,
                    number=1 if n < 10 else 2,
                    observed_at=elapsed,
                    compound="MEDIUM",
                    tyre_age=n if n < 10 else n - 10,
                    age_observed_at=elapsed,
                )
            )
    # Current-gap observations are independent delta packets at a common clock.
    for lap in [r for r in laps if r.driver_id == "d0"]:
        for i, gap in enumerate((0, 2, 10, 40, 60, 80)):
            timing.append(
                TimingSample(
                    driver_id=f"d{i}",
                    at=lap.completed_at - 0.01,
                    gap_to_leader=gap,
                    gap_to_ahead=(0, 2, 8, 30, 20, 20)[i],
                )
            )
    return HistoricalRace(
        event=event,
        session=session,
        started_at=0,
        participants=people,
        laps=laps,
        timing=timing,
        stints=stints,
        pit_stops=pits,
        control=[ControlSample(at=0, track_status="1", total_scheduled_laps=35)],
    )


def test_pace_medians_and_robust_stint_trend(history):
    context = AnalysisContext(history, 22)
    tyre = analyze_tyres(context, "d0")
    assert tyre.degradation_sec_per_lap == 0
    assert tyre.expected_3_lap_pace_loss == 0
    assert tyre.expected_5_lap_pace_loss == 0
    assert tyre.components["candidate_raw_slope_sec_per_lap"] == pytest.approx(0.2)
    assert tyre.recent_trend_sec_per_lap == pytest.approx(0)
    assert tyre.confidence == "LOW"
    assert tyre.stint_number == 2 and tyre.tyre_age == 12
    assert get_recent_pace(context, "d0").seconds == pytest.approx(102.2)
    assert get_stint_pace(context, "d0").sample_count == 12
    # An isolated slow but physically consistent lap is retained; the slope resists it.
    row = next(r for r in history.laps if r.driver_id == "d0" and r.number == 16)
    row.lap_time_seconds += 1.5
    changed = AnalysisContext(history, 22)
    assert 16 in [r.number for r in get_clean_laps(changed, "d0")]
    changed_tyre = analyze_tyres(changed, "d0")
    assert changed_tyre.degradation_sec_per_lap == 0
    assert changed_tyre.components["candidate_raw_slope_sec_per_lap"] == pytest.approx(0.2)


@pytest.mark.parametrize("lap", [1, 3, 11, 13])
def test_young_stint_is_insufficient(history, lap):
    result = analyze_tyres(AnalysisContext(history, lap), "d0")
    assert result.confidence == "INSUFFICIENT"
    assert result.degradation_sec_per_lap is None
    assert result.estimated_competitive_life_laps is None


def test_normalized_forecast_requires_five_other_clean_drivers(history):
    history.participants = history.participants[:5]
    result = analyze_tyres(AnalysisContext(history, 22), "d0")
    assert result.confidence == "INSUFFICIENT"
    assert result.degradation_sec_per_lap is None
    assert result.reference_coverage == 0


def test_clean_filter_pits_flags_invalid_and_extreme_anomaly(history):
    row = next(r for r in history.laps if r.driver_id == "d0" and r.number == 17)
    row.lap_time_seconds = 1
    history.lap_validity.append(LapValidity(driver_id="d0", lap_number=18, at=1900, valid=False))
    history.control.extend(
        [ControlSample(at=1400, track_status="6"), ControlSample(at=1600, track_status="1")]
    )
    clean = {r.number for r in get_clean_laps(AnalysisContext(history, 22), "d0")}
    assert not clean.intersection({1, 10, 14, 15, 16, 17, 18})
    assert {11, 12, 13, 19, 20, 21, 22} <= clean


def test_pit_loss_completed_evidence_and_unknown_components(history):
    early = estimate_pit_loss(AnalysisContext(history, 11))
    assert early.total_seconds is None and early.sample_count == 0
    later = estimate_pit_loss(AnalysisContext(history, 22))
    assert later.sample_count == 6
    # Independently: pit lap=122; mean(pre median101.6, post median100.4)=101.
    assert later.total_seconds == pytest.approx(21)
    assert later.transit_seconds is None and later.stationary_seconds is None
    assert later.components["pit_lane_elapsed_median_seconds"] == 28
    assert later.total_seconds != 28


def test_non_green_pit_samples_are_not_normal_stop_evidence(history):
    history.control.extend(
        [ControlSample(at=970, track_status="4"), ControlSample(at=1200, track_status="1")]
    )
    assert estimate_pit_loss(AnalysisContext(history, 22)).total_seconds is None


def test_rejoin_geometry_and_incomplete_lapped_field(history):
    context = AnalysisContext(history, 22)
    loss = estimate_pit_loss(context)
    result = predict_pit_rejoin("d0", context.state, loss)
    assert result.projected_position == 3
    assert result.gap_ahead == pytest.approx(11)
    assert result.gap_behind == pytest.approx(19)
    assert result.traffic == "CLEAR_AIR"
    context.driver("d3").gap_to_leader = None
    context.driver("d3").lapped = True
    uncertain = predict_pit_rejoin("d0", context.state, loss)
    assert uncertain.projected_position is None and uncertain.position_range == (3, 4)
    assert uncertain.traffic == "UNKNOWN"
    assert predict_pit_rejoin("d3", context.state, loss).confidence == "INSUFFICIENT"


@pytest.mark.parametrize(
    "gap,level",
    [(0.7, "HEAVY_TRAFFIC"), (1.5, "MODERATE_TRAFFIC"), (4, "LIGHT_TRAFFIC"), (8, "CLEAR_AIR")],
)
def test_rejoin_traffic_thresholds(history, gap, level):
    context = AnalysisContext(history, 22)
    loss = estimate_pit_loss(context)
    context.driver("d1").gap_to_leader = loss.total_seconds - gap
    context.driver("d2").gap_to_leader = 60
    context.driver("d3").gap_to_leader = 80
    assert predict_pit_rejoin("d0", context.state, loss).traffic == level
    window = find_pit_window("d0", context.state, loss)
    assert window.clear_air_opportunity == (level == "CLEAR_AIR")
    assert window.components["projection_laps"] == 0


def test_traffic_relative_pace_and_blockage(history):
    context = AnalysisContext(history, 22)
    traffic = analyze_traffic(context, "d1")
    assert traffic.gap_ahead == 2
    assert traffic.relative_pace_ahead < 0  # Faster leader, not a slower blocker.
    assert traffic.slower_car_blockage is False
    assert blockage_penalty(context, "d3", 1, 100, True) > 0
    assert blockage_penalty(context, "d3", 1, 100, False) is None


def test_pair_formulas_include_cost_cancellation_and_traffic(history):
    context = AnalysisContext(history, 22)
    loss = estimate_pit_loss(context)
    under = calculate_undercut(context, "d1", "d0", loss)
    over = calculate_overcut(context, "d1", "d0", loss)
    for result in (under, over):
        assert result.confidence == "LOW"
        assert result.required_gain == 2
        assert result.components["pit_loss_difference_seconds"] == 0
        assert result.components["fresh_tyre_evidence"]["warm_up_delta_seconds"] == 0
        assert result.fresh_tyre.sample_count == 6
        assert result.estimated_margin is not None and result.conditions_required
    assert under.estimated_margin == pytest.approx(
        under.components["target_recent_pace"]
        - under.components["estimated_fresh_clean_lap_seconds"]
        - under.traffic_penalty
        - 2
    )
    assert over.estimated_margin == pytest.approx(
        over.components["estimated_fresh_clean_lap_seconds"]
        - over.components["driver_recent_pace"]
        + over.traffic_penalty
        - over.components["driver_current_traffic_penalty"]
        - 2
    )


def test_fresh_tyre_delta_is_normalized_empirical_evidence(history):
    context = AnalysisContext(history, 22)
    result = estimate_fresh_tyre_delta(context, "d1", estimate_pit_loss(context), "MEDIUM")
    assert result.sample_count == 6
    assert result.components["transition_exact"] is True
    assert result.fresh_tyre_delta == pytest.approx(0)
    assert result.warm_up_delta_seconds == pytest.approx(0)
    assert result.confidence == "LOW"  # Held-out error does not justify stronger confidence.
    assert all(
        sample["evidence_available_at"] <= context.cutoff for sample in result.components["samples"]
    )


def test_pair_missing_inputs_are_not_zero_or_opportunity(history):
    context = AnalysisContext(history, 5)
    loss = estimate_pit_loss(context)
    for function in (calculate_undercut, calculate_overcut):
        result = function(context, "d1", "d0", loss)
        assert result.estimated_margin is None and result.opportunity == "UNKNOWN"
        assert result.confidence == "INSUFFICIENT"
        assert function(context, "d0", "d0", loss).estimated_margin is None


def all_outputs(history, lap):
    context = AnalysisContext(history, lap)
    loss = estimate_pit_loss(context)
    return [
        *[analyze_driver(context, d).model_dump() for d in context.drivers],
        calculate_undercut(context, "d1", "d0", loss).model_dump(),
        calculate_overcut(context, "d1", "d0", loss).model_dump(),
    ]


@pytest.mark.parametrize("mode", ["remove", "mutate"])
def test_all_outputs_independent_of_future(history, mode):
    lap = 22
    context = AnalysisContext(history, lap)
    baseline = all_outputs(history, lap)
    cutoff = context.cutoff
    if mode == "remove":
        changed = context.race
    else:
        changed = history.model_copy(deep=True)
        for row in changed.laps:
            if row.available_at > cutoff:
                row.lap_time_seconds, row.completed_at = 1, 999999
        for sample in changed.timing:
            if sample.at > cutoff:
                sample.position, sample.retired = 1, True
        for stint in changed.stints:
            if stint.observed_at > cutoff:
                stint.compound, stint.number, stint.tyre_age = "WET", 100, 900
        changed.lap_validity.append(
            LapValidity(driver_id="d0", lap_number=16, at=cutoff + 1, valid=False)
        )
        changed.control.append(ControlSample(at=cutoff + 1, track_status="5"))
        changed.pit_stops.append(
            PitStop(driver_id="d0", entered_at=cutoff + 1, exited_at=cutoff + 30)
        )
        changed.laps.append(
            LapData(
                driver_id="d0",
                number=15,
                completed_at=1500,
                available_at=cutoff + 1,
                lap_time_seconds=10,
            )
        )
    assert all_outputs(changed, lap) == baseline


def test_future_exit_and_late_deletion_do_not_leak(history):
    cutoff = AnalysisContext(history, 22).cutoff
    history.pit_stops.append(PitStop(driver_id="d0", entered_at=cutoff - 1, exited_at=cutoff + 20))
    baseline = all_outputs(history, 22)
    history.pit_stops[-1].exited_at = cutoff + 200
    assert all_outputs(history, 22) == baseline
    history.lap_validity.append(
        LapValidity(driver_id="d0", lap_number=20, at=cutoff + 1, valid=False)
    )
    assert all_outputs(history, 22) == baseline
    assert 20 not in analyze_tyres(AnalysisContext(history, 23), "d0").components["lap_numbers"]


def test_explicit_deletion_parser_uses_offending_lap_not_race_clock(history):
    row = history.laps[3]
    rows = [
        (
            "00:20:00.000",
            {
                "Messages": {
                    "1": {"Lap": 12, "Message": "CAR 4 (NOR) TIME 1:40.800 DELETED - LAP 4"},
                    "2": {"Lap": 12, "Message": "CAR 4 (NOR) TIME 1:40.800 REINSTATED - LAP 4"},
                }
            },
        )
    ]
    result = normalize_lap_validity(rows, {"4": "d0"}, [row])
    assert [(r.lap_number, r.valid, r.at) for r in result] == [(4, False, 1200), (4, True, 1200)]


async def test_analysis_api_all_routes_errors_and_shared_replay(history):
    app = create_app()
    async with app.router.lifespan_context(app):
        loader = AsyncMock(return_value=history)
        app.state.hub.replay.load_race = loader
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            prefix = "/api/v1/analysis/2024/1/22"
            aggregate = (await client.get(f"{prefix}/drivers/d1")).json()
            for part in ("tyres", "traffic"):
                response = await client.get(f"{prefix}/drivers/d1/{part}")
                assert response.status_code == 200 and response.json() == aggregate[part]
            for kind, query in (("undercut", "attacker=d1"), ("overcut", "driver=d1")):
                response = await client.get(f"{prefix}/{kind}?{query}&target=d0")
                assert response.status_code == 200 and response.json()["kind"] == kind
                response = await client.get(
                    f"{prefix}/{kind}?{query}&target=d0&new_compound=medium"
                )
                assert response.status_code == 200
                assert response.json()["fresh_tyre"]["new_compound"] == "MEDIUM"
            assert (await client.get(f"{prefix}/drivers/missing")).status_code == 404
            assert (await client.get("/api/v1/analysis/2024/1/999/drivers/d1")).status_code == 404
            assert (await client.get("/api/v1/analysis/2024/1/0/drivers/d1")).status_code == 422
            assert (await client.get(f"{prefix}/undercut?attacker=d1")).status_code == 422
        assert loader.await_count == 9  # Exactly one replay lookup per valid/path-resolved request.
