"""OpenF1 race archive: free historical timing for races the F1 archive now locks.

Since October 2026 F1's live-timing archive needs an F1 TV login, so replays of 2023+ races come
from OpenF1's public historical endpoints. OpenF1 rows are not a packet stream, so each fact is
dated by when it happened on track, which is when the timing screens showed it: a lap at its
crossing, a tyre at the lap it started or finished, a pit stop at pit entry and exit.
"""

import logging
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from statistics import median

from f1_pitwall.core.exceptions import ProviderError
from f1_pitwall.domain.enums import SessionType
from f1_pitwall.domain.models import Constructor, Driver, Event, Session
from f1_pitwall.domain.replay import (
    ControlSample,
    HistoricalRace,
    LapData,
    Participant,
    PitStop,
    Stint,
    TimingSample,
)
from f1_pitwall.providers.fastf1 import gap, lap_validity, slug
from f1_pitwall.providers.openf1 import OpenF1, driver_reference

log = logging.getLogger(__name__)

FIRST_SEASON = 2023  # OpenF1's historical data starts with the 2023 season.
TOPICS = ("drivers", "laps", "position", "intervals", "stints", "pit", "race_control")
# A retired car is shown as out once it is clearly overdue on its lap, not when it last crossed.
OVERDUE_LAPS = 1.5


def timestamp(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else None


def car(row: dict) -> int | None:
    try:
        return int(row["driver_number"])
    except (KeyError, TypeError, ValueError):
        return None


def number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def openf1_participants(rows: list[dict]) -> tuple[list[Participant], dict[int, str]]:
    """Session roster keyed by car number, with the same driver ids as the F1 archive."""
    profiles = {car(row): row for row in rows if car(row) is not None}
    participants, ids = [], {}
    for driver_number, row in profiles.items():
        full = row.get("full_name") or row.get("broadcast_name") or str(driver_number)
        parts = full.strip().split(maxsplit=1)
        first = row.get("first_name") or parts[0]
        last = row.get("last_name") or (parts[1].title() if len(parts) > 1 else "")
        reference = driver_reference(row, first, last)
        identity = f"f1:{reference}" if reference else f"f1:{slug(f'{first} {last}')}"
        if not reference and not last:
            identity = f"f1:{driver_number}"
        if identity in ids.values():
            identity = f"{identity}_{driver_number}"
        ids[driver_number] = identity
        team = row.get("team_name")
        participants.append(
            Participant(
                driver=Driver(
                    id=identity,
                    number=driver_number,
                    code=row.get("name_acronym"),
                    first_name=first,
                    last_name=last,
                ),
                constructor=Constructor(id=f"f1:{slug(team)}", name=team) if team else None,
            )
        )
    return participants, ids


def track_controls(rows: list[dict], at) -> list[ControlSample]:
    """F1 track status codes rebuilt from race control: 1 green, 2 yellow, 4 SC, 5 red, 6/7 VSC."""
    controls, overall, yellows, shown = [], "1", set(), None
    for row in sorted(rows, key=lambda r: r.get("date") or ""):
        when = at(row.get("date"))
        message = str(row.get("message") or "").upper()
        flag, scope = str(row.get("flag") or "").upper(), row.get("scope")
        if when is None:
            continue
        if "VIRTUAL SAFETY CAR" in message or message.startswith("VSC "):
            if "ENDING" in message:
                overall = "7"
            elif "DEPLOYED" in message:
                overall = "6"
        elif row.get("category") == "SafetyCar" and "DEPLOYED" in message:
            overall = "4"
        elif flag == "RED":
            overall = "5"
        elif flag in ("GREEN", "CLEAR") and scope == "Track":
            overall = "1"
            yellows.clear()
        elif flag in ("YELLOW", "DOUBLE YELLOW"):
            yellows.add(row.get("sector") or "track")
        elif flag == "CLEAR":
            yellows.discard(row.get("sector") or "track")
        else:
            continue
        code = "2" if overall == "1" and yellows else overall
        if code != shown:
            controls.append(ControlSample(at=when, track_status=code))
            shown = code
    return controls


def normalize_openf1_race(
    event: Event, origin: datetime, rows: dict[str, list[dict]], results: list[dict]
) -> HistoricalRace:
    def at(value) -> float | None:
        moment = timestamp(value)
        return (moment - origin).total_seconds() if moment else None

    participants, ids = openf1_participants(rows["drivers"])
    if not participants:
        raise ValueError("race roster is unavailable")
    status = rows["race_control"]
    starts = [
        at(r.get("date"))
        for r in status
        if r.get("category") == "SessionStatus" and "STARTED" in str(r.get("message")).upper()
    ]
    first_laps = [at(r.get("date_start")) for r in rows["laps"] if r.get("lap_number") == 1]
    candidates = [t for t in starts if t is not None] or [t for t in first_laps if t is not None]
    if not candidates:
        raise ValueError("race start is unavailable")
    started = min(candidates)
    if not results and not any("CHEQUERED" in str(r.get("message")).upper() for r in status):
        raise ValueError("race archive is not completed")

    # A lap ends where the next one starts; the lap's own duration covers a car's final lap.
    lap_starts: dict[int, dict[int, tuple[float, float | None]]] = defaultdict(dict)
    pit_out_laps: dict[int, set[int]] = defaultdict(set)
    for row in rows["laps"]:
        driver_number, lap, start = car(row), row.get("lap_number"), at(row.get("date_start"))
        if lap == 1:
            # Every car starts lap 1 together; OpenF1 leaves some opening laps undated or
            # misdated (a lap-1 retiree at Melbourne 2023 is dated after the flag).
            start = started
        if driver_number not in ids or not isinstance(lap, int) or lap < 1:
            continue
        if lap > 1 and row.get("is_pit_out_lap") is True:
            pit_out_laps[driver_number].add(lap)
        if start is not None:
            lap_starts[driver_number][lap] = (start, number(row.get("lap_duration")))
    laps, timing = [], []
    crossings: dict[int, dict[int, float]] = defaultdict(dict)
    unfinished: dict[int, float] = {}
    for driver_number, by_lap in lap_starts.items():
        driver_id = ids[driver_number]
        for lap, (start, duration) in sorted(by_lap.items()):
            if lap + 1 in by_lap:
                end = by_lap[lap + 1][0]
            elif duration is not None:
                end = start + duration
            else:
                unfinished[driver_number] = start  # the car stopped on this lap
                continue
            crossings[driver_number][lap] = end
            laps.append(
                LapData(
                    driver_id=driver_id,
                    number=lap,
                    completed_at=end,
                    available_at=end,
                    lap_time_seconds=duration,
                )
            )
            timing.append(TimingSample(driver_id=driver_id, at=end, laps_completed=lap))
    if not laps:
        raise ValueError("no timed race laps available")
    finished = max(lap.completed_at for lap in laps)
    timed = [lap.lap_time_seconds for lap in laps if lap.lap_time_seconds]
    field_lap = median(timed) if timed else 120.0

    grid = {}
    for row in sorted(rows["position"], key=lambda r: r.get("date") or ""):
        when, driver_number = at(row.get("date")), car(row)
        if when is None or driver_number not in ids or not isinstance(row.get("position"), int):
            continue
        timing.append(TimingSample(driver_id=ids[driver_number], at=when, position=row["position"]))
        if when <= started:
            grid[driver_number] = (when, row["position"])
    timing.extend(
        TimingSample(driver_id=ids[n], at=when, grid_position=position)
        for n, (when, position) in grid.items()
    )
    for row in rows["intervals"]:
        when, driver_number = at(row.get("date")), car(row)
        if when is None or driver_number not in ids:
            continue
        leader = row.get("gap_to_leader")
        seconds, behind = (number(leader), 0) if number(leader) is not None else gap(leader)
        interval = row.get("interval")
        ahead = number(interval) if number(interval) is not None else gap(interval)[0]
        timing.append(
            TimingSample(
                driver_id=ids[driver_number],
                at=when,
                gap_to_leader=seconds,
                gap_to_ahead=ahead,
                laps_behind=behind,
            )
        )

    pits, pit_exits = [], {}
    for row in rows["pit"]:
        driver_number, exited = car(row), at(row.get("date"))
        lane = number(row.get("lane_duration")) or number(row.get("pit_duration"))
        if driver_number not in ids or exited is None or lane is None or exited < started:
            continue
        # OpenF1 dates a stop when the car leaves the pit lane.
        entered = exited - lane
        driver_id = ids[driver_number]
        pits.append(PitStop(driver_id=driver_id, entered_at=entered, exited_at=exited))
        timing.append(TimingSample(driver_id=driver_id, at=entered, in_pit=True))
        timing.append(TimingSample(driver_id=driver_id, at=exited, in_pit=False))
        if isinstance(row.get("lap_number"), int):
            pit_exits[(driver_number, row["lap_number"])] = exited
    if not pits:
        # A few early-2023 races have no pit rows; their out laps still show each stop, dated
        # at the line crossing the car made in the pit lane.
        for driver_number, out_laps in pit_out_laps.items():
            for lap in sorted(out_laps):
                crossed = crossings[driver_number].get(lap - 1)
                if crossed is not None and crossed >= started:
                    pits.append(
                        PitStop(driver_id=ids[driver_number], entered_at=crossed, exited_at=crossed)
                    )

    stints = []
    for row in rows["stints"]:
        driver_number, first = car(row), row.get("lap_start")
        compound, fitted_age = row.get("compound"), number(row.get("tyre_age_at_start"))
        if driver_number not in ids or not isinstance(first, int) or not compound:
            continue
        compound = None if str(compound).upper() == "UNKNOWN" else str(compound).upper()
        last = row["lap_end"] if isinstance(row.get("lap_end"), int) else first
        fitted = (
            started
            if first <= 1
            else pit_exits.get((driver_number, first - 1))
            or crossings[driver_number].get(first - 1)
        )
        samples = [(fitted, fitted_age)] if fitted is not None else []
        for lap in range(first, last + 1):
            if lap in crossings[driver_number]:
                age = None if fitted_age is None else fitted_age + lap - first + 1
                samples.append((crossings[driver_number][lap], age))
        stints.extend(
            Stint(
                driver_id=ids[driver_number],
                observed_at=when,
                number=int(row.get("stint_number") or 1),
                compound=compound,
                tyre_age=age,
                age_observed_at=when if age is not None else None,
            )
            for when, age in samples
        )

    total_laps = 0
    for row in results:
        driver_number = car(row)
        if driver_number not in ids:
            continue
        laps_run = row.get("number_of_laps")
        if isinstance(laps_run, int) and row.get("position") == 1:
            total_laps = laps_run
        if row.get("dns") is True:
            timing.append(
                TimingSample(driver_id=ids[driver_number], at=started, explicit_status="dns")
            )
        elif row.get("dnf") is True:
            # The classification only confirms the retirement; it is dated by the car going
            # overdue on its lap, which the timing screens showed at that moment.
            own = [
                lap.lap_time_seconds
                for lap in laps
                if lap.driver_id == ids[driver_number] and lap.lap_time_seconds
            ]
            typical = median(own) if own else field_lap
            last_seen = unfinished.get(driver_number)
            if last_seen is None:
                last_seen = max(crossings[driver_number].values(), default=started)
            # A car that stops on the last lap is still shown as out by the flag.
            overdue = max(last_seen, min(last_seen + OVERDUE_LAPS * typical, finished))
            timing.append(TimingSample(driver_id=ids[driver_number], at=overdue, retired=True))
    messages = []
    for row in status:
        when, message = at(row.get("date")), str(row.get("message") or "")
        if when is None:
            continue
        driver_number = car(row)
        found = re.search(r"\bCAR (\d+)\b", message.upper())
        driver_number = driver_number or (int(found[1]) if found else None)
        if driver_number in ids and "DISQUALIFIED" in message.upper():
            timing.append(
                TimingSample(driver_id=ids[driver_number], at=when, explicit_status="dsq")
            )
        if driver_number is not None:
            messages.append((when, {"Message": message, "RacingNumber": driver_number}))

    control = track_controls(status, at)
    if total_laps:
        # Scheduled distance; only a race stopped early runs fewer laps than this.
        control.append(ControlSample(at=started, total_scheduled_laps=total_laps))
    session = next(
        (s for s in event.sessions if s.type == SessionType.RACE),
        Session(type=SessionType.RACE, name="Race", date=event.race_date, source="openf1"),
    )
    return HistoricalRace(
        event=event,
        session=session.model_copy(update={"end": None}),
        started_at=started,
        participants=sorted(participants, key=lambda p: p.driver.id),
        laps=laps,
        timing=timing,
        stints=stints,
        pit_stops=pits,
        control=control,
        lap_validity=lap_validity(
            messages, {str(n): driver_id for n, driver_id in ids.items()}, laps
        ),
        source="openf1-historical",
    )


class OpenF1Archive:
    """Loads completed races from OpenF1, which needs no login for past sessions."""

    def __init__(self, openf1: OpenF1):
        self.openf1 = openf1

    @staticmethod
    def covers(event: Event) -> bool:
        return event.year >= FIRST_SEASON

    async def _race_session(self, event: Event) -> dict:
        for row in await self.openf1.rows("sessions", year=event.year, session_name="Race"):
            start = timestamp(row.get("date_start"))
            if start and abs(start.date() - event.race_date) <= timedelta(days=1):
                return row
        raise ValueError("OpenF1 has no race session on this date")

    async def load(self, event: Event) -> HistoricalRace:
        try:
            session = await self._race_session(event)
            key, origin = session["session_key"], timestamp(session["date_start"])
            if origin is None:
                raise ValueError("race session has no start time")
            rows = {topic: await self.openf1.archive_rows(topic, key) for topic in TOPICS}
            results = await self.openf1.archive_rows("session_result", key)
            race = normalize_openf1_race(event, origin, rows, results)
        except ProviderError as exc:
            raise ProviderError("openf1", f"Historical archive unavailable: {exc.message}") from exc
        except Exception as exc:
            log.exception("openf1_replay_failed year=%s round=%s", event.year, event.round)
            raise ProviderError("openf1", f"Historical archive unavailable: {exc}") from exc
        log.info(
            "replay_loaded source=openf1 year=%s round=%s participants=%s laps=%s",
            event.year,
            event.round,
            len(race.participants),
            len(race.laps),
        )
        return race
