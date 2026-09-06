"""Pure replay builder. No I/O, DataFrames, final results or future inference."""

from math import isfinite
from statistics import median

from f1_pitwall.core.exceptions import NotFound
from f1_pitwall.domain.replay import (
    DataQuality,
    DriverRaceState,
    HistoricalRace,
    LapData,
    RaceControlState,
    RaceState,
)


def cutoff_for(race: HistoricalRace, lap: int) -> float:
    times = [r.available_at for r in race.laps if r.number == lap]
    if not times:
        raise NotFound(f"No reported completion for lap {lap}")
    return min(times)


def clean_lap(race: HistoricalRace, row: LapData, previous: LapData | None, cutoff: float) -> bool:
    if (
        row.lap_time_seconds is None
        or not isfinite(row.lap_time_seconds)
        or row.lap_time_seconds <= 0
        or previous is None
    ):
        return False
    if previous.number != row.number - 1:
        return False
    start, end = previous.completed_at, row.completed_at
    # Invalid durations cannot fit between the two observed crossings (allow broadcast jitter).
    if row.lap_time_seconds > end - start + 2:
        return False
    for pit in race.pit_stops:
        if pit.driver_id != row.driver_id:
            continue
        known_exit = (
            pit.exited_at if pit.exited_at is not None and pit.exited_at <= cutoff else None
        )
        if pit.entered_at <= end and (known_exit is None or known_exit >= start):
            return False
    statuses = sorted(
        (c for c in race.control if c.at <= end and c.at <= cutoff and c.track_status is not None),
        key=lambda c: c.at,
    )
    before = [c for c in statuses if c.at <= start]
    during = [c for c in statuses if c.at > start]
    # Unknown track conditions are not labelled clean. Yellow laps are excluded conservatively.
    return bool(before) and all(c.track_status == "1" for c in before[-1:] + during)


class RaceStateBuilder:
    def build(self, race: HistoricalRace, lap: int) -> RaceState:
        cutoff = cutoff_for(race, lap)
        rows: dict[str, dict[int, LapData]] = {}
        for row in sorted(race.laps, key=lambda r: r.available_at):
            if row.available_at <= cutoff and row.completed_at <= cutoff and row.number <= lap:
                rows.setdefault(row.driver_id, {})[row.number] = row
        values, observed = {}, {}
        for sample in sorted(race.timing, key=lambda s: s.at):
            if sample.at > cutoff:
                continue
            state = values.setdefault(sample.driver_id, {})
            times = observed.setdefault(sample.driver_id, {})
            for field in sample.model_fields_set - {"driver_id", "at"}:
                state[field] = getattr(sample, field)
                times[field] = sample.at
        drivers = [
            self._driver(
                race,
                p,
                rows.get(p.driver.id, {}),
                values.get(p.driver.id, {}),
                observed.get(p.driver.id, {}),
                cutoff,
            )
            for p in race.participants
            if p.registered_at <= cutoff
        ]
        drivers.sort(
            key=lambda d: (d.position if d.position is not None else float("inf"), d.driver.id)
        )
        positions = [d.position for d in drivers if d.position is not None]
        for index, driver in enumerate(drivers):
            if driver.position is not None and positions.count(driver.position) > 1:
                driver.quality.fields["position"] = "Conflicting asynchronous position samples"
            if index + 1 < len(drivers):
                behind = drivers[index + 1]
                if driver.position and behind.position == driver.position + 1:
                    driver.gap_to_behind = behind.gap_to_ahead
            if driver.quality.fields:
                driver.quality.confidence = "partial"
        control = {}
        for sample in sorted(race.control, key=lambda c: c.at):
            if sample.at <= cutoff:
                for field in sample.model_fields_set - {"at"}:
                    control[field] = getattr(sample, field)
        track = control.get("track_status")
        known = track in {"1", "2", "4", "5", "6", "7"}
        return RaceState(
            event=race.event,
            session=race.session,
            current_lap=lap,
            total_scheduled_laps=control.get("total_scheduled_laps"),
            session_time_seconds=cutoff,
            elapsed_race_seconds=cutoff - race.started_at,
            drivers=drivers,
            source=race.source,
            track=RaceControlState(
                track_status=track,
                safety_car=track == "4" if known else None,
                virtual_safety_car=track in {"6", "7"} if known else None,
                red_flag=track == "5" if known else None,
            ),
            quality=DataQuality(
                confidence="partial",
                warnings=[
                    "Snapshot uses only timestamped facts published by the leader-lap cutoff.",
                    "Positions, gaps and tyre life are reported samples; no interpolation.",
                    "UTC timestamp unavailable without a trustworthy archive clock alignment.",
                ],
            ),
        )

    def _driver(self, race, participant, laps, values, observed, cutoff):
        driver_id = participant.driver.id
        latest = laps[max(laps)] if laps else None
        state = DriverRaceState(
            driver=participant.driver,
            constructor=participant.constructor,
            position=values.get("position"),
            grid_position=values.get("grid_position"),
            laps_completed=values.get("laps_completed"),
            last_lap_number=latest.number if latest else None,
            last_lap_time=latest.lap_time_seconds if latest else None,
            retired=values.get("retired"),
        )
        status = values.get("explicit_status")
        if status in ("dns", "dsq"):
            state.status, state.active = status, False
        elif state.retired is True:
            state.status, state.active = "retired", False
        elif values.get("stopped"):
            state.status = "stopped"
        elif values.get("in_pit") and (
            (state.laps_completed or 0) > 0 or observed.get("in_pit", -1) >= race.started_at
        ):
            state.status, state.active = "in_pit", True
        elif (state.laps_completed or 0) > 0 or (
            values.get("in_pit") is False and observed.get("in_pit", -1) >= race.started_at
        ):
            state.status, state.active = "active", True
        else:
            state.quality.fields["status"] = "No timestamped evidence of starting or retirement"
        state.pit_stops_completed = sum(
            p.driver_id == driver_id
            and p.exited_at is not None
            and race.started_at <= p.entered_at <= p.exited_at <= cutoff
            for p in race.pit_stops
        )
        known_stints = [
            s for s in race.stints if s.driver_id == driver_id and s.observed_at <= cutoff
        ]
        if known_stints:
            stint = max(known_stints, key=lambda s: (s.number, s.observed_at))
            state.compound, state.stint_number = stint.compound, stint.number
            state.tyre_age, state.tyre_age_observed_at = stint.tyre_age, stint.age_observed_at
        for field in (
            "position",
            "grid_position",
            "laps_completed",
            "compound",
            "tyre_age",
            "last_lap_time",
        ):
            if getattr(state, field) is None:
                state.quality.fields[field] = "No usable observation at or before cutoff"
        clean = [
            r
            for n, r in sorted(laps.items())
            if clean_lap(
                race,
                r,
                laps.get(n - 1),
                cutoff,
            )
        ]
        selected = clean[-3:]
        state.pace_laps = [r.number for r in selected]
        state.recent_clean_pace = median(r.lap_time_seconds for r in selected) if selected else None
        if not selected:
            state.quality.fields["recent_clean_pace"] = (
                "No clean completed laps with known track status"
            )
        self._gaps(state, values, observed, cutoff)
        return state

    @staticmethod
    def _gaps(state, values, observed, cutoff):
        # Published timing gaps, never final classification or subtraction of unequal lap clocks.
        last = observed.get("gap_to_leader")
        interval = observed.get("gap_to_ahead")
        state.gap_observed_at, state.interval_observed_at = last, interval
        terminal = state.status in {"retired", "dns", "dsq", "stopped"}
        if not terminal:
            # The archive is a delta stream: '+1 LAP' is not resent while unchanged.
            # Preserve that observed deficit; never derive it from crossing-count differences.
            state.laps_behind = values.get("laps_behind")
        if not terminal and last is not None and cutoff - last <= 30:
            state.gap_to_leader = values.get("gap_to_leader")
        if not terminal and interval is not None and cutoff - interval <= 30:
            # After an order change an interval to the previous car is not trustworthy.
            if interval >= observed.get("position", 0):
                state.gap_to_ahead = values.get("gap_to_ahead")
        if state.position == 1 and not terminal:
            state.gap_to_leader, state.laps_behind = 0, 0
        if state.laps_behind is not None:
            state.lapped = state.laps_behind > 0
        if state.lapped:
            state.gap_to_leader = None
        if state.gap_to_leader is None:
            state.quality.fields["gap_to_leader"] = (
                "Missing, stale, terminal or lap-valued timing gap"
            )
        if state.gap_to_ahead is None:
            state.quality.fields["gap_to_ahead"] = "Missing/stale interval or changed race order"
