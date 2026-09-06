from datetime import date

import pytest

from f1_pitwall.core.exceptions import NotFound
from f1_pitwall.domain.models import Circuit, Driver, Event, Session
from f1_pitwall.domain.replay import (
    ControlSample,
    HistoricalRace,
    LapData,
    Participant,
    PitStop,
    Stint,
    TimingSample,
)
from f1_pitwall.services.race_state import RaceStateBuilder, cutoff_for


@pytest.fixture
def race():
    session = Session(type="Race", name="Race", date=date(2030, 1, 1), source="test")
    event = Event(
        year=2030,
        round=1,
        name="Test Grand Prix",
        race_date=session.date,
        circuit=Circuit(id="test", name="Test"),
        sessions=[session],
    )
    people = [
        Participant(driver=Driver(id=f"d{i}", first_name="Test", last_name=str(i)))
        for i in range(1, 5)
    ]
    laps, timing = [], []
    for p, delay in zip(people[:3], (0, 4, 180), strict=True):
        i = int(p.driver.id[1:])
        timing.append(
            TimingSample(
                driver_id=p.driver.id,
                at=0,
                grid_position=i,
                position=i,
                retired=False,
                in_pit=False,
                laps_completed=0,
            )
        )
        for n in range(1, 46):
            at = n * 100 + delay
            laps.append(
                LapData(
                    driver_id=p.driver.id,
                    number=n,
                    completed_at=at,
                    available_at=at,
                    lap_time_seconds=100,
                )
            )
            timing.append(TimingSample(driver_id=p.driver.id, at=at, laps_completed=n))
            timing.append(
                TimingSample(
                    driver_id=p.driver.id,
                    at=n * 100 - 1,
                    gap_to_leader=delay,
                    gap_to_ahead=delay,
                    laps_behind=1 if i == 3 else 0,
                )
            )
    timing.extend(
        [
            TimingSample(driver_id="d4", at=5, explicit_status="dns"),
            TimingSample(driver_id="d2", at=4005, retired=True),
        ]
    )
    return HistoricalRace(
        event=event,
        session=session,
        started_at=0,
        participants=people,
        laps=laps,
        timing=timing,
        pit_stops=[PitStop(driver_id="d2", entered_at=2550, exited_at=2560)],
        stints=[
            Stint(
                driver_id=p.driver.id,
                observed_at=10,
                number=1,
                compound="MEDIUM",
                tyre_age=3,
                age_observed_at=10,
            )
            for p in people
        ]
        + [
            Stint(
                driver_id="d2",
                observed_at=2561,
                number=2,
                compound="HARD",
                tyre_age=0,
                age_observed_at=2561,
            )
        ],
        control=[
            ControlSample(at=0, track_status="1", total_scheduled_laps=50),
            ControlSample(at=4200, track_status="4", total_scheduled_laps=45),
        ],
    )


def driver(state, identity):
    return next(d for d in state.drivers if d.driver.id == identity)


def truncate(race, cutoff):
    copy = race.model_copy(deep=True)
    copy.laps = [r for r in copy.laps if r.available_at <= cutoff]
    copy.timing = [r for r in copy.timing if r.at <= cutoff]
    copy.stints = [r for r in copy.stints if r.observed_at <= cutoff]
    copy.control = [r for r in copy.control if r.at <= cutoff]
    copy.pit_stops = [p for p in copy.pit_stops if p.entered_at <= cutoff]
    for pit in copy.pit_stops:
        if pit.exited_at is not None and pit.exited_at > cutoff:
            pit.exited_at = None
    return copy


def test_order_and_common_cutoff_with_lapped_driver(race):
    state = RaceStateBuilder().build(race, 20)
    assert [d.driver.id for d in state.drivers] == ["d1", "d2", "d3", "d4"]
    assert state.session_time_seconds == 2000
    assert [d.laps_completed for d in state.drivers] == [20, 19, 18, None]
    assert driver(state, "d2").gap_to_leader == 4
    assert driver(state, "d1").gap_to_behind == 4
    assert driver(state, "d3").lapped is True
    assert driver(state, "d3").laps_behind == 1
    assert driver(state, "d3").gap_to_leader is None
    assert driver(state, "d4").status == "dns"


def test_pits_tyres_and_stints_only_after_observation(race):
    builder = RaceStateBuilder()
    early, later = (driver(builder.build(race, n), "d2") for n in (25, 26))
    assert (early.compound, early.stint_number, early.pit_stops_completed) == ("MEDIUM", 1, 0)
    assert (later.compound, later.stint_number, later.pit_stops_completed) == ("HARD", 2, 1)
    assert early.tyre_age == 3 and later.tyre_age == 0


def test_recent_pace_excludes_pit_and_non_green_laps(race):
    race.control.extend(
        [ControlSample(at=2101, track_status="6"), ControlSample(at=2401, track_status="1")]
    )
    state = RaceStateBuilder().build(race, 27)
    d = driver(state, "d2")
    assert 22 not in d.pace_laps and 23 not in d.pace_laps and 24 not in d.pace_laps
    assert 26 not in d.pace_laps  # pit in/out fall within this lap
    assert d.recent_clean_pace == 100
    assert len(d.pace_laps) == 3


def test_no_future_leakage_remove_all_future_records(race):
    builder = RaceStateBuilder()
    baseline = builder.build(race, 20)
    assert builder.build(truncate(race, cutoff_for(race, 20)), 20) == baseline
    # Later corrections to an EARLIER lap must also remain invisible.
    race.laps.append(
        LapData(driver_id="d2", number=19, completed_at=1904, available_at=3000, lap_time_seconds=1)
    )
    assert builder.build(race, 20) == baseline


def test_no_future_leakage_mutate_laps_positions_tyres_stops_and_control(race):
    builder = RaceStateBuilder()
    baseline = builder.build(race, 20)
    cutoff = cutoff_for(race, 20)
    for row in race.laps:
        if row.available_at > cutoff:
            row.lap_time_seconds = 1
            row.completed_at += 12345
    for row in race.timing:
        if row.at > cutoff:
            row.position, row.retired = 1, True
    for row in race.stints:
        if row.observed_at > cutoff:
            row.compound, row.tyre_age, row.number = "WET", 999, 100
    race.pit_stops.append(PitStop(driver_id="d1", entered_at=3000, exited_at=3002))
    for row in race.control:
        if row.at > cutoff:
            row.track_status, row.total_scheduled_laps = "5", 1
    assert builder.build(race, 20) == baseline


def test_future_retirement_cannot_change_earlier_active_state(race):
    builder = RaceStateBuilder()
    assert driver(builder.build(race, 25), "d2").active is True
    assert driver(builder.build(race, 40), "d2").retired is False
    assert driver(builder.build(race, 41), "d2").status == "retired"
    race.timing = [t for t in race.timing if not t.retired]
    assert driver(builder.build(race, 25), "d2").active is True


def test_future_pit_exit_does_not_complete_open_stop(race):
    race.pit_stops = [PitStop(driver_id="d2", entered_at=1990, exited_at=2020)]
    builder = RaceStateBuilder()
    state = builder.build(race, 20)
    assert driver(state, "d2").pit_stops_completed == 0
    assert builder.build(truncate(race, 2000), 20) == state


def test_future_position_changes_do_not_change_earlier_order(race):
    builder = RaceStateBuilder()
    earlier = builder.build(race, 20)
    race.timing.extend(
        [
            TimingSample(driver_id="d2", at=2050, position=1),
            TimingSample(driver_id="d1", at=2050, position=2),
        ]
    )
    assert builder.build(race, 20) == earlier
    assert builder.build(race, 21).drivers[0].driver.id == "d2"


def test_missing_samples_and_late_retirement_not_inferred_from_last_lap(race):
    race.laps = [r for r in race.laps if r.driver_id != "d2" or r.number <= 10]
    race.timing = [s for s in race.timing if s.driver_id != "d2" or s.at <= 1004]
    d = driver(RaceStateBuilder().build(race, 25), "d2")
    assert d.retired is False  # No inference from end of the full historical lap table.
    assert d.gap_to_leader is None
    assert d.quality.confidence == "partial"


def test_unchanged_lap_deficit_persists_while_stale_seconds_gap_does_not(race):
    race.timing = [s for s in race.timing if s.driver_id != "d3" or s.at < 1900]
    d = driver(RaceStateBuilder().build(race, 25), "d3")
    assert d.lapped is True and d.laps_behind == 1
    assert d.gap_to_leader is None


def test_unknown_driver_data_and_invalid_lap(race):
    race.timing = [s for s in race.timing if s.driver_id != "d4"]
    race.stints = [s for s in race.stints if s.driver_id != "d4"]
    d = driver(RaceStateBuilder().build(race, 20), "d4")
    assert d.active is None and d.status == "unknown"
    assert d.compound is None and d.quality.fields
    with pytest.raises(NotFound):
        RaceStateBuilder().build(race, 999)


def test_delayed_old_stint_update_does_not_revert_current_tyre(race):
    race.stints.append(
        Stint(driver_id="d2", observed_at=2590, number=1, compound="MEDIUM", tyre_age=25)
    )
    assert driver(RaceStateBuilder().build(race, 26), "d2").compound == "HARD"


def test_explicit_disqualification_only_after_message(race):
    race.timing.append(TimingSample(driver_id="d2", at=2050, explicit_status="dsq"))
    assert driver(RaceStateBuilder().build(race, 20), "d2").active is True
    assert driver(RaceStateBuilder().build(race, 21), "d2").status == "dsq"


def test_prerace_pit_lane_presence_is_not_proof_of_starting(race):
    race.started_at = 1
    race.timing = [s for s in race.timing if s.driver_id != "d4"]
    race.timing.append(TimingSample(driver_id="d4", at=0, in_pit=True, retired=False))
    d = driver(RaceStateBuilder().build(race, 20), "d4")
    assert d.status == "unknown" and d.active is None
