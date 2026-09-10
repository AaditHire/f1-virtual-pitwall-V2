"""Current timing collection, freshness classification and causal normalization."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from f1_pitwall.core.exceptions import ProviderError
from f1_pitwall.domain.enums import SessionType
from f1_pitwall.domain.live import (
    CurrentPitWallResponse,
    Freshness,
    LiveRaceResponse,
    LiveSession,
    LiveStatus,
    LiveTimingBundle,
    RaceControlFeed,
    RaceControlMessage,
    SessionClassificationEntry,
    WeatherState,
)
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
from f1_pitwall.providers.live import LiveTimingProvider
from f1_pitwall.services.analysis_context import AnalysisContext


def _dt(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except (ValueError, TypeError):
        return None


def freshness(source: str, retrieved_at: datetime, provider_timestamp: datetime | None):
    age = None
    state = "UNKNOWN"
    if provider_timestamp:
        age = max(0.0, (retrieved_at - provider_timestamp).total_seconds())
        state = "FRESH" if age <= 30 else "DELAYED" if age <= 120 else "STALE"
    return Freshness(
        source=source,
        retrieved_at=retrieved_at,
        provider_timestamp=provider_timestamp,
        data_age_seconds=round(age, 3) if age is not None else None,
        state=state,
    )


def _latest_timestamp(bundle: LiveTimingBundle) -> datetime | None:
    found = []
    for rows in (
        bundle.positions,
        bundle.intervals,
        bundle.laps,
        bundle.pit_stops,
        bundle.track_status,
        bundle.weather,
        bundle.race_control,
    ):
        for row in rows:
            for key in ("date", "date_start", "timestamp"):
                value = _dt(row.get(key))
                if value:
                    if key == "date_start" and row.get("lap_duration"):
                        value += timedelta(seconds=float(row["lap_duration"]))
                    found.append(value)
    return max(found) if found else None


def _session_type(value: str) -> SessionType:
    aliases = {"Sprint Shootout": "Sprint Qualifying"}
    try:
        return SessionType(aliases.get(value, value))
    except ValueError:
        return SessionType.OTHER


TRACK_CODES = {
    "allclear": "1",
    "green": "1",
    "yellow": "2",
    "doubleyellow": "2",
    "safetycar": "4",
    "red": "5",
    "virtualsafetycar": "6",
    "vsc": "6",
}
PUBLIC_TRACK = {
    "1": "GREEN",
    "2": "YELLOW",
    "4": "SC",
    "5": "RED",
    "6": "VSC",
    "7": "VSC",
}


def _track_code(row: dict) -> str | None:
    value = row.get("status") or row.get("flag") or row.get("message")
    if value is None:
        return None
    compact = str(value).replace(" ", "").replace("_", "").casefold()
    if str(value) in {"1", "2", "4", "5", "6", "7"}:
        return str(value)
    return TRACK_CODES.get(compact)


def _number(row: dict) -> int | None:
    try:
        return int(row.get("driver_number"))
    except (TypeError, ValueError):
        return None


def _seconds(value: datetime | None, origin: datetime) -> float | None:
    return (value - origin).total_seconds() if value else None


def _gap(value):
    if value is None or value == "":
        return None, None
    if isinstance(value, (int, float)):
        return float(value), None
    text = str(value).upper()
    if "LAP" in text:
        try:
            return None, int("".join(c for c in text if c.isdigit()) or "1")
        except ValueError:
            return None, None
    try:
        return float(text.lstrip("+")), None
    except ValueError:
        return None, None


class LiveService:
    def __init__(self, provider: LiveTimingProvider, calendar, results, news, clock=None):
        self.provider = provider
        self.calendar, self.results, self.news = calendar, results, news
        self.clock = clock or (lambda: datetime.now(UTC))

    async def bundle(self) -> LiveTimingBundle | None:
        session = await self.provider.get_current_session()
        if session is None:
            return None
        names = {
            "drivers": self.provider.get_drivers,
            "positions": self.provider.get_positions,
            "intervals": self.provider.get_intervals,
            "laps": self.provider.get_laps,
            "stints": self.provider.get_stints,
            "pit_stops": self.provider.get_pit_stops,
            "track_status": self.provider.get_track_status,
            "weather": self.provider.get_weather,
            "race_control": self.provider.get_race_control,
        }
        values = await asyncio.gather(
            *(call(session.session_key) for call in names.values()), return_exceptions=True
        )
        data, errors = {}, {}
        for name, value in zip(names, values, strict=True):
            if isinstance(value, Exception):
                errors[name] = str(value)
                data[name] = []
            else:
                data[name] = value
        return LiveTimingBundle(session=session, retrieved_at=self.clock(), errors=errors, **data)

    async def status_bundle(self) -> LiveTimingBundle | None:
        """Fetch only evidence needed to establish provider-backed session state."""
        session = await self.provider.get_current_session()
        if session is None:
            return None
        values = await asyncio.gather(
            self.provider.get_laps(session.session_key),
            self.provider.get_race_control(session.session_key),
            return_exceptions=True,
        )
        data, errors = {}, {}
        for name, value in zip(("laps", "race_control"), values, strict=True):
            if isinstance(value, Exception):
                errors[name] = str(value)
                data[name] = []
            else:
                data[name] = value
        return LiveTimingBundle(session=session, retrieved_at=self.clock(), errors=errors, **data)

    async def weather_bundle(self) -> LiveTimingBundle | None:
        session = await self.provider.get_current_session()
        if session is None:
            return None
        try:
            rows = await self.provider.get_weather(session.session_key)
            return LiveTimingBundle(session=session, retrieved_at=self.clock(), weather=rows)
        except Exception as exc:
            return LiveTimingBundle(
                session=session,
                retrieved_at=self.clock(),
                errors={"weather": getattr(exc, "message", str(exc))},
            )

    async def control_bundle(self) -> LiveTimingBundle | None:
        session = await self.provider.get_current_session()
        if session is None:
            return None
        values = await asyncio.gather(
            self.provider.get_track_status(session.session_key),
            self.provider.get_race_control(session.session_key),
            return_exceptions=True,
        )
        data, errors = {}, {}
        for name, value in zip(("track_status", "race_control"), values, strict=True):
            if isinstance(value, Exception):
                errors[name] = str(value)
                data[name] = []
            else:
                data[name] = value
        return LiveTimingBundle(session=session, retrieved_at=self.clock(), errors=errors, **data)

    async def _event(self, session: LiveSession) -> Event | None:
        try:
            events = await self.calendar.get_calendar(session.year)
        except (ProviderError, Exception):
            return None
        matches = [
            event
            for event in events
            if any(item.provider_id == session.session_key for item in event.sessions)
        ]
        if not matches:
            matches = [
                event
                for event in events
                if abs((event.race_date - session.start.date()).days) <= 3
                and (
                    not session.location
                    or session.location.casefold() in (event.circuit.locality or "").casefold()
                    or (event.circuit.locality or "").casefold() in session.location.casefold()
                )
            ]
        return matches[0] if len(matches) == 1 else None

    def metadata(self, bundle: LiveTimingBundle):
        return freshness(self.provider.name, bundle.retrieved_at, _latest_timestamp(bundle))

    @staticmethod
    def _classifications(rows):
        result = []
        for row in rows:
            number = _number(row)
            if number is None:
                continue
            result.append(
                SessionClassificationEntry(
                    driver_number=number,
                    position=row.get("position"),
                    laps=row.get("number_of_laps"),
                    duration_seconds=row.get("duration"),
                    gap_to_leader=row.get("gap_to_leader"),
                    dns=row.get("dns"),
                    dnf=row.get("dnf"),
                    dsq=row.get("dsq"),
                )
            )
        return sorted(result, key=lambda item: item.position or 999)

    async def weekend_session_results(self, sessions):
        current = await self.provider.get_current_session()
        if current is None or current.meeting_key is None:
            return {}
        rows = await self.provider.get_session_results(meeting_key=current.meeting_key)
        by_key = {}
        for row in rows:
            by_key.setdefault(row.get("session_key"), []).append(row)
        return {
            session.name: self._classifications(by_key.get(session.provider_id, []))
            for session in sessions
            if session.provider_id
        }

    async def weather_available(self):
        bundle = await self.weather_bundle()
        return bool(bundle and bundle.weather)

    def session_status(self, bundle: LiveTimingBundle):
        now, session, meta = self.clock(), bundle.session, self.metadata(bundle)
        if now < session.start:
            return "UPCOMING"
        in_fresh_window = session.end is None or now <= session.end + timedelta(minutes=5)
        if meta.state == "FRESH" and in_fresh_window:
            return "LIVE"
        in_delayed_window = session.end is None or now <= session.end + timedelta(minutes=15)
        if meta.state == "DELAYED" and in_delayed_window:
            return "DELAYED"
        if session.end and now > session.end:
            return "COMPLETED"
        return "UNKNOWN"

    async def status(self, next_session=None) -> LiveStatus:
        retrieved = self.clock()
        try:
            bundle = await self.status_bundle()
        except ProviderError as exc:
            return LiveStatus(
                availability="UNAVAILABLE",
                provider=self.provider.name,
                freshness=freshness(self.provider.name, retrieved, None),
                next_session=next_session,
                reason=exc.message,
            )
        if bundle is None:
            return LiveStatus(
                availability="UNAVAILABLE",
                provider=self.provider.name,
                freshness=freshness(self.provider.name, retrieved, None),
                next_session=next_session,
                reason="No provider session is available",
            )
        state, meta = self.session_status(bundle), self.metadata(bundle)
        event = await self._event(bundle.session)
        availability = (
            "LIVE_AVAILABLE"
            if state == "LIVE"
            else "DELAYED_AVAILABLE"
            if state == "DELAYED"
            else "HISTORICAL_ONLY"
            if state == "COMPLETED"
            else "UNAVAILABLE"
        )
        return LiveStatus(
            live=state == "LIVE",
            availability=availability,
            session_status=state,
            event=event,
            session=self.canonical_session(bundle.session, event),
            next_session=next_session,
            provider=self.provider.name,
            freshness=meta,
            reason="; ".join(f"{k}: {v}" for k, v in bundle.errors.items()) or None,
        )

    @staticmethod
    def canonical_session(session: LiveSession, event: Event | None) -> Session:
        if event:
            found = next((s for s in event.sessions if s.provider_id == session.session_key), None)
            if found:
                return found
        return Session(
            type=_session_type(session.name),
            name=session.name,
            date=session.start.date(),
            start=session.start,
            end=session.end,
            source="openf1",
            provider_id=session.session_key,
        )

    async def history(self, bundle: LiveTimingBundle) -> HistoricalRace | None:
        event = await self._event(bundle.session)
        if event is None:
            return None
        origin = bundle.session.start
        profiles = {_number(row): row for row in bundle.drivers if _number(row) is not None}
        participants, ids = [], {}
        for number, row in profiles.items():
            full = row.get("full_name") or row.get("broadcast_name") or str(number)
            parts = full.strip().split(maxsplit=1)
            identity = (row.get("name_acronym") or str(number)).casefold()
            ids[number] = identity
            participants.append(
                Participant(
                    driver=Driver(
                        id=identity,
                        number=number,
                        code=row.get("name_acronym"),
                        first_name=parts[0],
                        last_name=parts[1] if len(parts) > 1 else "",
                    ),
                    constructor=Constructor(
                        id=(row.get("team_name") or "unknown").casefold().replace(" ", "_"),
                        name=row.get("team_name") or "Unknown",
                    )
                    if row.get("team_name")
                    else None,
                )
            )
        laps = []
        timing = []
        latest_lap = {}
        for row in bundle.laps:
            number, lap_no = _number(row), row.get("lap_number")
            start, duration = _dt(row.get("date_start")), row.get("lap_duration")
            if number not in ids or not lap_no or not start or not duration:
                continue
            completed = _seconds(start, origin) + float(duration)
            if completed < 0:
                continue
            latest_lap[number] = max(int(lap_no), latest_lap.get(number, 0))
            laps.append(
                LapData(
                    driver_id=ids[number],
                    number=int(lap_no),
                    completed_at=completed,
                    available_at=completed,
                    lap_time_seconds=float(duration),
                )
            )
            timing.append(
                TimingSample(driver_id=ids[number], at=completed, laps_completed=int(lap_no))
            )
        for row in bundle.positions:
            number, at = _number(row), _seconds(_dt(row.get("date")), origin)
            if number in ids and at is not None and at >= 0:
                timing.append(
                    TimingSample(driver_id=ids[number], at=at, position=row.get("position"))
                )
        for row in bundle.intervals:
            number, at = _number(row), _seconds(_dt(row.get("date")), origin)
            if number not in ids or at is None or at < 0:
                continue
            gap, behind = _gap(row.get("gap_to_leader"))
            interval, _ = _gap(row.get("interval"))
            timing.append(
                TimingSample(
                    driver_id=ids[number],
                    at=at,
                    gap_to_leader=gap,
                    gap_to_ahead=interval,
                    laps_behind=behind,
                )
            )
        leader_lap = max((row.number for row in laps), default=0)
        cutoff = min((row.available_at for row in laps if row.number == leader_lap), default=0)
        stints = []
        for row in bundle.stints:
            number = _number(row)
            if number not in ids:
                continue
            lap_start = row.get("lap_start") or 1
            age_start = row.get("tyre_age_at_start")
            age = None
            if age_start is not None and number in latest_lap:
                age = float(age_start) + max(0, latest_lap[number] - int(lap_start) + 1)
            stints.append(
                Stint(
                    driver_id=ids[number],
                    observed_at=cutoff,
                    number=int(row.get("stint_number") or 1),
                    compound=row.get("compound"),
                    tyre_age=age,
                    age_observed_at=cutoff if age is not None else None,
                )
            )
        pits = []
        for row in bundle.pit_stops:
            number, entered = _number(row), _seconds(_dt(row.get("date")), origin)
            if number not in ids or entered is None or entered < 0:
                continue
            duration = row.get("pit_duration") or row.get("duration")
            pits.append(
                PitStop(
                    driver_id=ids[number],
                    entered_at=entered,
                    exited_at=entered + float(duration) if duration else None,
                )
            )
        controls = []
        for row in bundle.track_status + bundle.race_control:
            at, code = _seconds(_dt(row.get("date")), origin), _track_code(row)
            if at is not None and at >= 0 and code:
                controls.append(ControlSample(at=at, track_status=code))
        return HistoricalRace(
            event=event,
            session=self.canonical_session(bundle.session, event),
            started_at=0,
            participants=participants,
            laps=laps,
            timing=timing,
            stints=stints,
            pit_stops=pits,
            control=controls,
            source=self.provider.name,
        )

    def weather(self, bundle: LiveTimingBundle) -> WeatherState | None:
        if not bundle.weather:
            return None
        row = max(bundle.weather, key=lambda item: item.get("date", ""))
        return WeatherState(
            air_temperature_c=row.get("air_temperature"),
            track_temperature_c=row.get("track_temperature"),
            humidity_percent=row.get("humidity"),
            rainfall=row.get("rainfall"),
            wind_speed=row.get("wind_speed"),
            wind_direction_degrees=row.get("wind_direction"),
            observed_at=_dt(row.get("date")),
        )

    def control(self, bundle: LiveTimingBundle) -> RaceControlFeed:
        recognized = [row for row in bundle.track_status + bundle.race_control if _track_code(row)]
        latest = max(recognized, key=lambda row: row.get("date", ""), default={})
        code = _track_code(latest)
        messages = [
            RaceControlMessage(
                timestamp=_dt(row.get("date")),
                category=row.get("category"),
                flag=row.get("flag"),
                scope=row.get("scope"),
                message=row.get("message"),
            )
            for row in bundle.race_control[-50:]
        ]
        return RaceControlFeed(
            track_status=PUBLIC_TRACK.get(code, "UNKNOWN"),
            messages=messages,
            freshness=self.metadata(bundle),
        )

    async def race(self, next_session=None, latest_results=None, news=None):
        try:
            bundle = await self.bundle()
        except ProviderError as exc:
            meta = freshness(self.provider.name, self.clock(), None)
            return LiveRaceResponse(
                live=False,
                availability="UNAVAILABLE",
                provider=self.provider.name,
                freshness=meta,
                next_session=next_session,
                next_event=next_session.event if next_session else None,
                current_weekend_status="UPCOMING" if next_session else "UNKNOWN",
                latest_results=latest_results or [],
                news=news or [],
                missing_requirements=[exc.message],
            )
        if bundle is None:
            return LiveRaceResponse(
                live=False,
                availability="UNAVAILABLE",
                provider=self.provider.name,
                freshness=freshness(self.provider.name, self.clock(), None),
                next_session=next_session,
                next_event=next_session.event if next_session else None,
                current_weekend_status="UPCOMING" if next_session else "UNKNOWN",
                latest_results=latest_results or [],
                news=news or [],
                missing_requirements=["current provider session"],
            )
        status, meta = self.session_status(bundle), self.metadata(bundle)
        history = await self.history(bundle)
        missing = list(bundle.errors)
        if not history:
            missing.append("canonical event match")
        if not bundle.drivers:
            missing.append("drivers")
        if not bundle.positions:
            missing.append("positions")
        if not bundle.laps:
            missing.append("laps")
        state = None
        if history and history.laps:
            leader_lap = max(row.number for row in history.laps)
            try:
                state = AnalysisContext(history, leader_lap).state
                state.timestamp = meta.provider_timestamp
            except Exception as exc:
                missing.append(f"race state: {type(exc).__name__}")
        availability = (
            "LIVE_AVAILABLE"
            if status == "LIVE"
            else "DELAYED_AVAILABLE"
            if status == "DELAYED"
            else "HISTORICAL_ONLY"
            if status == "COMPLETED"
            else "UNAVAILABLE"
        )
        event = history.event if history else await self._event(bundle.session)
        return LiveRaceResponse(
            live=status == "LIVE",
            availability=availability,
            event=event,
            session=self.canonical_session(bundle.session, event),
            lap=state.current_lap if state else None,
            status=status,
            track_status=self.control(bundle).track_status,
            weather=self.weather(bundle),
            drivers=state.drivers if state else [],
            race_state=state,
            provider=self.provider.name,
            freshness=meta,
            next_session=next_session,
            next_event=next_session.event if next_session else None,
            current_weekend_status=(
                status if status in {"LIVE", "DELAYED"} else "UPCOMING" if next_session else status
            ),
            latest_results=latest_results or [],
            news=news or [],
            missing_requirements=sorted(set(missing)),
        )

    async def current_pitwall(self, pitwall, driver_id=None, trajectory_count=100):
        try:
            bundle = await self.bundle()
        except ProviderError as exc:
            meta = freshness(self.provider.name, self.clock(), None)
            return CurrentPitWallResponse(
                analysis_status="UNAVAILABLE",
                provider=self.provider.name,
                data_freshness=meta,
                missing_requirements=[exc.message],
            )
        if bundle is None:
            meta = freshness(self.provider.name, self.clock(), None)
            return CurrentPitWallResponse(
                analysis_status="UNAVAILABLE",
                provider=self.provider.name,
                data_freshness=meta,
                missing_requirements=["current provider session"],
            )
        meta, status = self.metadata(bundle), self.session_status(bundle)
        missing = list(bundle.errors)
        if status not in {"LIVE", "DELAYED"}:
            missing.append("live or delayed active session")
        if _session_type(bundle.session.name) != SessionType.RACE:
            missing.append("active race session")
        for name in ("drivers", "positions", "laps", "stints"):
            if not getattr(bundle, name):
                missing.append(name)
        if not bundle.track_status and not any(_track_code(row) for row in bundle.race_control):
            missing.append("track status")
        history = await self.history(bundle)
        leader_lap = max((row.number for row in history.laps), default=0) if history else 0
        if leader_lap < 3:
            missing.append("at least three observed race laps")
        state = None
        if history and leader_lap:
            try:
                state = AnalysisContext(history, leader_lap).state
            except Exception as exc:
                missing.append(f"normalized race state: {type(exc).__name__}")
        if missing:
            driver_state = None
            if state and driver_id:
                found = next((d for d in state.drivers if d.driver.id == driver_id), None)
                driver_state = found.model_dump() if found else None
                if found is None:
                    missing.append(f"driver {driver_id}")
            return CurrentPitWallResponse(
                analysis_status="PARTIAL" if state else "UNAVAILABLE",
                provider=self.provider.name,
                last_update=meta.provider_timestamp,
                data_freshness=meta,
                current_state=driver_state,
                missing_requirements=sorted(set(missing)),
            )
        if driver_id:
            detail = await pitwall.driver_history(history, leader_lap, driver_id, trajectory_count)
            current = next(d for d in state.drivers if d.driver.id == driver_id)
            return CurrentPitWallResponse(
                analysis_status="AVAILABLE",
                provider=self.provider.name,
                last_update=meta.provider_timestamp,
                data_freshness=meta,
                driver=detail,
                current_state=current.model_dump(),
            )
        snapshot = await pitwall.snapshot_history(history, leader_lap, trajectory_count)
        return CurrentPitWallResponse(
            analysis_status="AVAILABLE",
            provider=self.provider.name,
            last_update=meta.provider_timestamp,
            data_freshness=meta,
            snapshot=snapshot,
        )
