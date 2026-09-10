import json
import logging
import re
import unicodedata
from datetime import datetime, timedelta

from pydantic import BaseModel

from f1_pitwall.domain.enums import SessionType
from f1_pitwall.domain.live import LiveSession
from f1_pitwall.domain.models import Driver, Event, GridEntry, Session
from f1_pitwall.providers.http import ProviderHTTP, normalized

log = logging.getLogger(__name__)


def name_key(name: str) -> str:
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", name.lower())


class Weekend(BaseModel):
    """Normalized provider weekend used solely to enrich our event schedule."""

    id: int
    circuit_name: str
    locality: str
    sessions: list[Session]


class OpenF1:
    def __init__(self, http: ProviderHTTP):
        self.http = http
        self.base = http.settings.openf1_url.rstrip("/")
        self.name = "openf1"

    async def rows(self, path: str, **params) -> list[dict]:
        data = json.loads(
            await self.http.get(
                f"{self.base}/{path}",
                self.http.settings.short_ttl,
                empty_on_no_results=True,
                **params,
            )
        )
        if not isinstance(data, list):
            raise ValueError("expected a list")
        return data

    @normalized
    async def _live_rows(self, path: str, session_key: int) -> list[dict]:
        return await self.rows(path, session_key=session_key)

    @normalized
    async def get_current_session(self) -> LiveSession | None:
        rows = await self.rows("sessions", session_key="latest")
        if not rows:
            return None
        row = max(rows, key=lambda item: item["date_start"])
        timestamps = [row.get("date_end"), row.get("date_start")]
        return LiveSession(
            session_key=row["session_key"],
            meeting_key=row.get("meeting_key"),
            name=row["session_name"],
            type=row.get("session_type", row["session_name"]),
            start=row["date_start"],
            end=row.get("date_end"),
            year=row.get("year", datetime.fromisoformat(row["date_start"]).year),
            circuit_name=row.get("circuit_short_name"),
            location=row.get("location"),
            country=row.get("country_name"),
            provider_timestamp=max(t for t in timestamps if t),
        )

    async def get_drivers(self, session_key: int):
        return await self._live_rows("drivers", session_key)

    async def get_positions(self, session_key: int):
        return await self._live_rows("position", session_key)

    async def get_intervals(self, session_key: int):
        return await self._live_rows("intervals", session_key)

    async def get_laps(self, session_key: int):
        return await self._live_rows("laps", session_key)

    async def get_stints(self, session_key: int):
        return await self._live_rows("stints", session_key)

    async def get_pit_stops(self, session_key: int):
        return await self._live_rows("pit", session_key)

    async def get_track_status(self, session_key: int):
        return await self._live_rows("track_status", session_key)

    async def get_weather(self, session_key: int):
        return await self._live_rows("weather", session_key)

    async def get_race_control(self, session_key: int):
        return await self._live_rows("race_control", session_key)

    @normalized
    async def get_session_results(
        self, session_key: int | None = None, meeting_key: int | None = None
    ):
        params = {}
        if session_key is not None:
            params["session_key"] = session_key
        if meeting_key is not None:
            params["meeting_key"] = meeting_key
        return await self.rows("session_result", **params)

    @normalized
    async def weekends(self, year: int) -> list[Weekend]:
        groups: dict[int, Weekend] = {}
        for row in await self.rows("sessions", year=year):
            name = row["session_name"]
            canonical = "Sprint Qualifying" if name == "Sprint Shootout" else name
            try:
                kind = SessionType(canonical)
            except ValueError:
                kind = SessionType.OTHER
            session = Session(
                name=name,
                type=kind,
                date=row["date_start"][:10],
                start=row["date_start"],
                end=row.get("date_end"),
                provider_id=row["session_key"],
                source="openf1",
                cancelled=row.get("is_cancelled", False),
            )
            key = row["meeting_key"]
            if key not in groups:
                groups[key] = Weekend(
                    id=key,
                    circuit_name=row["circuit_short_name"],
                    locality=row["location"],
                    sessions=[],
                )
            groups[key].sessions.append(session)
        return list(groups.values())

    def enrich(self, event: Event, weekends: list[Weekend]) -> Event:
        # Join on a race within one day AND normalized circuit/locality identity.
        # Dates alone can join the wrong event after calendar changes.
        candidates = []
        date_matches = []
        for weekend in weekends:
            races = [s for s in weekend.sessions if s.type == SessionType.RACE]
            if not any(abs(s.date - event.race_date) <= timedelta(days=1) for s in races):
                continue
            date_matches.append(weekend)
            left = {
                name_key(event.circuit.name),
                name_key(event.circuit.locality or ""),
                name_key(event.circuit.id),
            } - {""}
            right = {name_key(weekend.circuit_name), name_key(weekend.locality)} - {""}
            if any(a in b or b in a for a in left for b in right):
                candidates.append(weekend)
        result = event.model_copy(deep=True)
        if len(candidates) != 1:
            if date_matches:
                warning = "OpenF1 event identity differs or is ambiguous; retained Jolpica schedule"
                result.warnings.append(warning)
                log.warning("year=%s round=%s %s", event.year, event.round, warning)
            return result
        merged = {s.type: s for s in event.sessions}
        for session in candidates[0].sessions:
            previous = merged.get(session.type)
            if previous and previous.start and session.start != previous.start:
                warning = f"{session.name} start differs; OpenF1 session time takes priority"
                result.warnings.append(warning)
                log.warning("year=%s round=%s %s", event.year, event.round, warning)
            if session.type != SessionType.OTHER:
                merged[session.type] = session
        result.sessions = sorted(merged.values(), key=lambda s: (s.date, str(s.start or "~")))
        return result

    @normalized
    async def grid(self, session: Session, drivers: list[Driver]) -> list[GridEntry]:
        rows = await self.rows("starting_grid", session_key=session.provider_id)
        if not rows:
            return []
        profiles = {
            r["driver_number"]: r
            for r in await self.rows("drivers", session_key=session.provider_id)
        }
        entries = []
        for row in rows:
            profile = profiles[row["driver_number"]]
            matches = [
                d
                for d in drivers
                if (
                    name_key(d.full_name) == name_key(profile.get("full_name", ""))
                    or (d.code and d.code == profile.get("name_acronym"))
                )
            ]
            if len(matches) != 1:
                raise ValueError("cannot unambiguously link OpenF1 driver to canonical identity")
            position = row.get("position")
            entries.append(
                GridEntry(
                    driver=matches[0],
                    position=position,
                    source="openf1",
                    pit_lane=None if position in (None, 0) else False,
                )
            )
        return sorted(entries, key=lambda g: g.position if g.position else float("inf"))
