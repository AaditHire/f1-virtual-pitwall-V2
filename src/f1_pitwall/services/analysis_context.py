"""The analysis boundary: materialize a publication-time prefix, then index it."""

from bisect import bisect_right
from functools import cached_property

from f1_pitwall.core.exceptions import NotFound
from f1_pitwall.domain.replay import HistoricalRace
from f1_pitwall.services.race_state import RaceStateBuilder, clean_lap, cutoff_for


class AnalysisContext:
    def __init__(self, history: HistoricalRace, lap: int):
        cutoff = cutoff_for(history, lap)
        self.race = history.model_copy(
            update={
                "laps": [
                    r
                    for r in history.laps
                    if r.available_at <= cutoff and r.completed_at <= cutoff and r.number <= lap
                ],
                "timing": [r for r in history.timing if r.at <= cutoff],
                "stints": [r for r in history.stints if r.observed_at <= cutoff],
                "control": [r for r in history.control if r.at <= cutoff],
                "lap_validity": [r for r in history.lap_validity if r.at <= cutoff],
                "participants": [p for p in history.participants if p.registered_at <= cutoff],
                "pit_stops": [
                    p.model_copy(
                        update={
                            "exited_at": p.exited_at
                            if p.exited_at is not None and p.exited_at <= cutoff
                            else None
                        }
                    )
                    for p in history.pit_stops
                    if p.entered_at <= cutoff
                ],
            },
            deep=True,
        )
        self.cutoff = cutoff
        self.state = RaceStateBuilder().build(self.race, lap)
        self.drivers = {d.driver.id: d for d in self.state.drivers}
        self.rows = {identity: {} for identity in self.drivers}
        for row in sorted(self.race.laps, key=lambda r: r.available_at):
            if row.driver_id in self.rows:
                self.rows[row.driver_id][row.number] = row
        self.validity = {}
        for record in sorted(self.race.lap_validity, key=lambda r: r.at):
            self.validity[record.driver_id, record.lap_number] = record.valid
        self.stint_index = {}
        for identity in self.drivers:
            records = sorted(
                (s for s in self.race.stints if s.driver_id == identity),
                key=lambda s: s.observed_at,
            )
            times, states = [], []
            for record in records:
                times.append(record.observed_at)
                states.append(max([record] + states[-1:], key=lambda s: (s.number, s.observed_at)))
            self.stint_index[identity] = times, states

    def driver(self, identity):
        if identity not in self.drivers:
            raise NotFound(f"Driver {identity} is not in this race session")
        return self.drivers[identity]

    def stint_at(self, identity, at):
        times, states = self.stint_index.get(identity, ([], []))
        index = bisect_right(times, at) - 1
        return states[index] if index >= 0 else None

    @cached_property
    def clean(self):
        result = {}
        for identity, rows in self.rows.items():
            result[identity] = [
                row
                for n, row in sorted(rows.items())
                if self.validity.get((identity, n)) is not False
                and clean_lap(self.race, row, rows.get(n - 1), self.cutoff)
                # A physically inconsistent short duration is also an obvious timing anomaly.
                and row.lap_time_seconds >= (row.completed_at - rows[n - 1].completed_at) * 0.5
            ]
        return result

    def stint_laps(self, identity, number=None):
        driver = self.driver(identity)
        number = driver.stint_number if number is None else number
        result = []
        for row in self.clean[identity]:
            previous = self.rows[identity][row.number - 1]
            stint = self.stint_at(identity, previous.completed_at)
            end_stint = self.stint_at(identity, row.completed_at)
            if stint and end_stint and stint.number == end_stint.number == number:
                result.append(row)
        return result
