"""FastF1 archive adapter. Deliberately bypasses retrospective lap corrections.

FastF1's public Laps table repairs timing using later packets and can generate
retirement laps. We instead normalize its original cached timestamped streams.
Only this module knows the upstream packet schema or FastF1's private API.
"""

import logging
import math
import re
import time
from pathlib import Path
from threading import Lock

from f1_pitwall.core.config import Settings
from f1_pitwall.core.exceptions import ProviderError
from f1_pitwall.domain.enums import SessionType
from f1_pitwall.domain.models import Constructor, Driver, Event, Session
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

log = logging.getLogger(__name__)
_FASTF1_LOCK = Lock()  # FastF1's disk-cache configuration is process-global.


def fetch_stream(path: str, topic: str, settings: Settings) -> list:
    """FastF1's HTTP cache and parser with explicit network timeouts/retries."""
    import fastf1
    import requests
    from fastf1 import _api

    last_error = None
    for base in (_api.base_url, _api.base_url_mirror):
        for attempt in range(settings.retries + 1):
            try:
                response = fastf1.Cache.requests_get(
                    base + path + _api.pages[topic], headers=_api.headers, timeout=settings.timeout
                )
                response.raise_for_status()
                # Reject corrupt records rather than silently drop parts of race history.
                return [
                    [line[:12], _api.parse(line[12:])]
                    for line in response.content.decode("utf-8-sig").splitlines()
                    if line
                ]
            except requests.RequestException as exc:
                last_error = exc
                status = exc.response.status_code if exc.response is not None else None
                if status is not None and status not in {408, 429, 500, 502, 503, 504}:
                    break
                if attempt < settings.retries:
                    delay = 0.5 * 2**attempt
                    if exc.response is not None and "Retry-After" in exc.response.headers:
                        delay = numeric(exc.response.headers["Retry-After"])
                        if delay is None or delay > 10:
                            break
                    time.sleep(delay)
    raise ValueError(f"archive stream unavailable: {topic} ({last_error})")


def seconds(value: str) -> float:
    parts = value.split(":")
    result = sum(float(part) * 60**i for i, part in enumerate(reversed(parts)))
    if not math.isfinite(result) or result < 0:
        raise ValueError("invalid archive duration")
    return result


def lap_duration(value) -> float | None:
    try:
        result = seconds(value)
        return result if math.isfinite(result) and result > 0 else None
    except (ValueError, TypeError, AttributeError):
        return None


def integer(value) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (ValueError, TypeError):
        return None


def numeric(value) -> float | None:
    try:
        result = float(value)
        return result if result >= 0 and result < float("inf") else None
    except (ValueError, TypeError):
        return None


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def gap(value) -> tuple[float | None, int | None]:
    if not isinstance(value, str):
        return None, None
    # '+1 LAP' means lapped; 'LAP 25' is the leader's lap display, not a deficit.
    lapped = re.fullmatch(r"\+?(\d+)\s+(?:L|LAPS?)", value.upper())
    if lapped:
        return None, int(lapped[1])
    amount = numeric(value.lstrip("+"))
    return amount, 0 if amount is not None else None


def roster(records, started_at: float) -> tuple[list[Participant], dict[str, str]]:
    people, seen = {}, {}
    for stamp, update in sorted(records, key=lambda r: seconds(r[0])):
        at = seconds(stamp)
        if at > started_at:
            continue
        for number, row in update.items():
            if not isinstance(row, dict):
                continue
            people.setdefault(number, {}).update(row)
            seen.setdefault(number, at)
    participants, ids = [], {}
    for number, row in people.items():
        if not row.get("FirstName") or not row.get("LastName"):
            raise ValueError("pre-race DriverList lacks participant identity")
        identity = row.get("Reference")
        if not identity:
            identity = slug(f"{row['FirstName']} {row['LastName']}")
        driver = Driver(
            id=f"f1:{identity}",
            number=integer(row.get("RacingNumber", number)),
            first_name=row["FirstName"],
            last_name=row["LastName"],
            code=row.get("Tla"),
        )
        team = row.get("TeamName")
        participants.append(
            Participant(
                driver=driver,
                registered_at=seen[number],
                constructor=Constructor(id=f"f1:{slug(team)}", name=team) if team else None,
            )
        )
        ids[number] = driver.id
    if not participants or len(set(ids.values())) != len(ids):
        raise ValueError("empty or ambiguous session roster")
    return sorted(participants, key=lambda p: p.driver.id), ids


def normalize_timing(records, ids, started_at):
    timing, laps, pits = [], [], []
    counts, in_pit, open_pit = {}, {}, {}
    for stamp, update in sorted(records, key=lambda r: seconds(r[0])):
        at = seconds(stamp)
        for number, row in update.get("Lines", {}).items():
            if number not in ids:
                continue
            driver_id = ids[number]
            values = {}
            for source, target in (("Position", "position"), ("NumberOfLaps", "laps_completed")):
                if source in row:
                    values[target] = integer(row[source])
            for source, target in (
                ("Retired", "retired"),
                ("InPit", "in_pit"),
                ("Stopped", "stopped"),
            ):
                if source in row and isinstance(row[source], bool):
                    values[target] = row[source]
            if row.get("Disqualified") is True:
                values["explicit_status"] = "dsq"
            if row.get("DidNotStart") is True:
                values["explicit_status"] = "dns"
            if "GapToLeader" in row:
                values["gap_to_leader"], values["laps_behind"] = gap(row["GapToLeader"])
            if "IntervalToPositionAhead" in row:
                values["gap_to_ahead"] = gap(row["IntervalToPositionAhead"].get("Value"))[0]
            if values:
                timing.append(TimingSample(driver_id=driver_id, at=at, **values))
            if "InPit" in row:
                current = row["InPit"]
                if current is True and in_pit.get(number) is not True:
                    open_pit[number] = at
                elif current is False and number in open_pit:
                    entered = open_pit.pop(number)
                    if entered >= started_at:
                        pits.append(PitStop(driver_id=driver_id, entered_at=entered, exited_at=at))
                in_pit[number] = current
            count = integer(row.get("NumberOfLaps"))
            if count is not None and count > counts.get(number, 0) and at >= started_at:
                counts[number] = count
                lap_time = row.get("LastLapTime", {}).get("Value")
                duration = lap_duration(lap_time)
                laps.append(
                    LapData(
                        driver_id=driver_id,
                        number=count,
                        completed_at=at,
                        available_at=at,
                        lap_time_seconds=duration,
                    )
                )
            # Never attach an unnumbered late lap-time packet to an inferred lap.
    for number, entered in open_pit.items():
        if entered >= started_at:
            pits.append(PitStop(driver_id=ids[number], entered_at=entered))
    return timing, laps, pits


def normalize_tyres(records, ids):
    samples, grid, state = [], [], {}
    for stamp, update in sorted(records, key=lambda r: seconds(r[0])):
        at = seconds(stamp)
        for number, row in update.get("Lines", {}).items():
            if number not in ids:
                continue
            if "GridPos" in row:
                grid.append(
                    TimingSample(
                        driver_id=ids[number], at=at, grid_position=integer(row["GridPos"])
                    )
                )
            stints = row.get("Stints", {})
            items = enumerate(stints) if isinstance(stints, list) else stints.items()
            for index, patch in items:
                key = (number, int(index))
                data = state.setdefault(key, {})
                data.update(patch)
                if "TotalLaps" in patch:
                    data["age_observed_at"] = at
                if data.get("Compound"):
                    samples.append(
                        Stint(
                            driver_id=ids[number],
                            observed_at=at,
                            number=int(index) + 1,
                            compound=data["Compound"],
                            tyre_age=numeric(data.get("TotalLaps")),
                            age_observed_at=data.get("age_observed_at"),
                        )
                    )
    return samples, grid


def normalize_lap_revisions(records, ids, laps):
    """Accept late lap times only when the source explicitly identifies the lap."""
    crossings = {(r.driver_id, r.number): r for r in laps}
    revisions = []
    for stamp, update in sorted(records, key=lambda r: seconds(r[0])):
        at = seconds(stamp)
        for number, row in update.get("Lines", {}).items():
            if number not in ids:
                continue
            stints = row.get("Stints", {})
            for patch in stints.values() if isinstance(stints, dict) else stints:
                if not patch.get("LapTime") or "LapNumber" not in patch:
                    continue
                crossing = crossings.get((ids[number], integer(patch["LapNumber"])))
                if crossing and crossing.available_at <= at:
                    revisions.append(
                        crossing.model_copy(
                            update={
                                "available_at": at,
                                "lap_time_seconds": lap_duration(patch["LapTime"]),
                            }
                        )
                    )
    return revisions


def normalize_lap_validity(records, ids, laps):
    """Explicit car/lap deletions; message Lap is the race clock, not the offending lap."""
    validity = []
    for stamp, update in records:
        at = seconds(stamp)
        messages = update.get("Messages", {})
        for message in messages.values() if isinstance(messages, dict) else messages:
            text = message.get("Message", "").upper()
            car = re.search(r"\bCAR (\d+)\b", text)
            number = str(message.get("RacingNumber", car[1] if car else ""))
            if number not in ids or not any(word in text for word in ("DELETED", "REINSTATED")):
                continue
            lap = re.search(r"\bLAP (\d+)\b", text)
            if lap:
                lap_number = int(lap[1])
            else:
                # NEXT LAP messages often identify only a time. Require a unique observed match.
                time_match = re.search(r"\bTIME (\d+:\d+\.\d+)\b", text)
                duration = lap_duration(time_match[1]) if time_match else None
                matches = {
                    r.number
                    for r in laps
                    if r.driver_id == ids[number]
                    and r.available_at <= at
                    and r.completed_at <= at
                    and duration is not None
                    and r.lap_time_seconds is not None
                    and abs(r.lap_time_seconds - duration) < 0.001
                }
                if len(matches) != 1:
                    continue
                lap_number = matches.pop()
            if lap_number > 0:
                validity.append(
                    LapValidity(
                        driver_id=ids[number],
                        lap_number=lap_number,
                        at=at,
                        valid="REINSTATED" in text,
                    )
                )
    return validity


def normalize_archive(event: Event, streams: dict) -> HistoricalRace:
    starts = [seconds(t) for t, r in streams["session_status"] if r.get("Status") == "Started"]
    if not starts:
        raise ValueError("race start is unavailable")
    started = min(starts)
    participants, ids = roster(streams["driver_list"], started)
    timing, laps, pits = normalize_timing(streams["timing_data"], ids, started)
    stints, grid = normalize_tyres(streams.get("timing_app_data", []), ids)
    laps.extend(normalize_lap_revisions(streams.get("timing_app_data", []), ids, laps))
    control = [
        ControlSample(at=seconds(t), track_status=str(r["Status"]))
        for t, r in streams.get("track_status", [])
        if "Status" in r
    ]
    control.extend(
        ControlSample(at=seconds(t), total_scheduled_laps=integer(r["TotalLaps"]))
        for t, r in streams.get("lap_count", [])
        if "TotalLaps" in r
    )
    for t, update in streams.get("race_control_messages", []):
        messages = update.get("Messages", {})
        for message in messages.values() if isinstance(messages, dict) else messages:
            number = str(message.get("RacingNumber", ""))
            text = message.get("Message", "").upper()
            # Only explicit car-specific messages are evidence; never use final classifications.
            if number in ids:
                status = (
                    "dsq" if "DISQUALIFIED" in text else "dns" if "DID NOT START" in text else None
                )
                if status:
                    timing.append(
                        TimingSample(driver_id=ids[number], at=seconds(t), explicit_status=status)
                    )
    if not laps:
        raise ValueError("no timed race laps available")
    session = next(
        (s for s in event.sessions if s.type == SessionType.RACE),
        Session(type=SessionType.RACE, name="Race", date=event.race_date, source="fastf1"),
    )
    # No actual finish time or final lap count is introduced into replay metadata.
    session = session.model_copy(update={"end": None})
    return HistoricalRace(
        event=event,
        session=session,
        started_at=started,
        participants=participants,
        laps=laps,
        timing=timing + grid,
        stints=stints,
        pit_stops=pits,
        control=control,
        lap_validity=normalize_lap_validity(streams.get("race_control_messages", []), ids, laps),
    )


class FastF1Provider:
    def __init__(self, settings: Settings):
        self.settings = settings

    def load(self, event: Event) -> HistoricalRace:
        import fastf1

        try:
            with _FASTF1_LOCK:
                cache = Path(self.settings.replay_cache_dir).resolve()
                cache.mkdir(parents=True, exist_ok=True)
                fastf1.Cache.enable_cache(str(cache))
                session = fastf1.get_session(event.year, event.round, "R")
                if abs((session.date.date() - event.race_date).days) > 1:
                    raise ValueError("FastF1 and calendar disagree on race date")
                streams = {}
                for topic in (
                    "driver_list",
                    "session_status",
                    "timing_data",
                    "timing_app_data",
                    "track_status",
                    "lap_count",
                    "race_control_messages",
                ):
                    data = fetch_stream(session.api_path, topic, self.settings)
                    if data is None:
                        raise ValueError(f"archive stream unavailable: {topic}")
                    streams[topic] = data
                # Loading a historical archive requires completion, but completion data is
                # never passed to the builder or used to infer retirements/scheduled laps.
                if not any(
                    r.get("Status") in {"Finished", "Finalised", "Ends"}
                    for _, r in streams["session_status"]
                ):
                    raise ValueError("race archive is not completed")
                result = normalize_archive(event, streams)
                log.info(
                    "replay_loaded year=%s round=%s participants=%s laps=%s",
                    event.year,
                    event.round,
                    len(result.participants),
                    len(result.laps),
                )
                return result
        except Exception as exc:
            log.exception("replay_load_failed year=%s round=%s", event.year, event.round)
            raise ProviderError("fastf1", f"Historical archive unavailable: {exc}") from exc
