import json
from datetime import date, datetime

from f1_pitwall.domain.enums import SessionType
from f1_pitwall.domain.models import (
    Circuit,
    Constructor,
    ConstructorStanding,
    Driver,
    DriverStanding,
    Event,
    QualifyingResult,
    RaceResult,
    Season,
    Session,
)
from f1_pitwall.providers.http import ProviderHTTP, normalized


def optional_int(value) -> int | None:
    return int(value) if value not in (None, "", "\\N") else None


def driver(row: dict) -> Driver:
    return Driver(
        id=row["driverId"],
        number=optional_int(row.get("permanentNumber")),
        code=row.get("code"),
        first_name=row["givenName"],
        last_name=row["familyName"],
        nationality=row.get("nationality"),
    )


def constructor(row: dict) -> Constructor:
    return Constructor(
        id=row["constructorId"], name=row["name"], nationality=row.get("nationality")
    )


SESSION_KEYS = {
    "FirstPractice": SessionType.PRACTICE_1,
    "SecondPractice": SessionType.PRACTICE_2,
    "ThirdPractice": SessionType.PRACTICE_3,
    "SprintQualifying": SessionType.SPRINT_QUALIFYING,
    "SprintShootout": SessionType.SPRINT_QUALIFYING,
    "Sprint": SessionType.SPRINT,
    "Qualifying": SessionType.QUALIFYING,
}


def event(row: dict) -> Event:
    circuit = row["Circuit"]
    location = circuit.get("Location", {})
    sessions = []
    for name, kind, data in [
        (kind.value, kind, row[key]) for key, kind in SESSION_KEYS.items() if row.get(key)
    ] + [("Race", SessionType.RACE, row)]:
        # Date-only schedules stay date-only. Midnight is not an invented start time.
        start = (
            datetime.fromisoformat(f"{data['date']}T{data['time']}") if data.get("time") else None
        )
        sessions.append(
            Session(type=kind, name=name, date=data["date"], start=start, source="jolpica")
        )
    sessions.sort(key=lambda s: (s.date, s.start.isoformat() if s.start else "~"))
    return Event(
        year=row["season"],
        round=row["round"],
        name=row["raceName"],
        race_date=date.fromisoformat(row["date"]),
        sessions=sessions,
        circuit=Circuit(
            id=circuit["circuitId"],
            name=circuit["circuitName"],
            locality=location.get("locality"),
            country=location.get("country"),
        ),
    )


class Jolpica:
    def __init__(self, http: ProviderHTTP):
        self.http = http
        self.base = http.settings.jolpica_url.rstrip("/")

    async def rows(
        self, path: str, table: str, collection: str, ttl: float, nested: str | None = None
    ) -> list[dict]:
        result, offset = [], 0
        while True:
            data = json.loads(
                await self.http.get(f"{self.base}/{path}.json", ttl, limit=100, offset=offset)
            )["MRData"]
            rows = data[table][collection]
            if nested:
                rows = [item for group in rows for item in group[nested]]
            result.extend(rows)
            # Ergast total/offset count nested result/standing records, not races.
            total = int(data["total"])
            if len(result) >= total:
                break
            if not rows:
                raise ValueError("pagination ended before provider total")
            offset += len(rows)
        return result

    @normalized
    async def seasons(self) -> list[Season]:
        rows = await self.rows("seasons", "SeasonTable", "Seasons", self.http.settings.long_ttl)
        return sorted(
            (Season(year=r["season"]) for r in rows if int(r["season"]) >= 2021),
            key=lambda s: s.year,
        )

    @normalized
    async def active_season(self) -> Season:
        data = json.loads(
            await self.http.get(f"{self.base}/current.json", self.http.settings.short_ttl, limit=1)
        )["MRData"]["RaceTable"]
        return Season(year=data["season"])

    @normalized
    async def drivers(self, year: int) -> list[Driver]:
        return [
            driver(r)
            for r in await self.rows(
                f"{year}/drivers", "DriverTable", "Drivers", self.http.settings.long_ttl
            )
        ]

    @normalized
    async def constructors(self, year: int) -> list[Constructor]:
        return [
            constructor(r)
            for r in await self.rows(
                f"{year}/constructors",
                "ConstructorTable",
                "Constructors",
                self.http.settings.long_ttl,
            )
        ]

    @normalized
    async def calendar(self, year: int) -> list[Event]:
        return [
            event(r)
            for r in await self.rows(str(year), "RaceTable", "Races", self.http.settings.long_ttl)
        ]

    @normalized
    async def qualifying(self, year: int, round: int) -> list[QualifyingResult]:
        return [
            QualifyingResult(
                position=optional_int(r.get("position")),
                driver=driver(r["Driver"]),
                constructor=constructor(r["Constructor"]),
                q1=r.get("Q1"),
                q2=r.get("Q2"),
                q3=r.get("Q3"),
            )
            for r in await self.rows(
                f"{year}/{round}/qualifying",
                "RaceTable",
                "Races",
                self.http.settings.medium_ttl,
                "QualifyingResults",
            )
        ]

    @normalized
    async def results(self, year: int, round: int | str) -> list[RaceResult]:
        return [
            RaceResult(
                position=optional_int(r.get("position")),
                position_text=r.get("positionText"),
                driver=driver(r["Driver"]),
                constructor=constructor(r["Constructor"]),
                grid=optional_int(r.get("grid")),
                points=r.get("points"),
                laps=optional_int(r.get("laps")),
                status=r.get("status"),
                time=(r.get("Time") or {}).get("time"),
            )
            for r in await self.rows(
                f"{year}/{round}/results",
                "RaceTable",
                "Races",
                self.http.settings.medium_ttl,
                "Results",
            )
        ]

    @normalized
    async def latest_completed(self, year: int) -> Event | None:
        data = json.loads(
            await self.http.get(
                f"{self.base}/{year}/last/results.json", self.http.settings.medium_ttl, limit=1
            )
        )["MRData"]
        rows = data["RaceTable"]["Races"]
        return event(rows[0]) if rows else None

    @normalized
    async def driver_standings(self, year: int) -> list[DriverStanding]:
        return [
            DriverStanding(
                position=optional_int(r.get("position")),
                points=r["points"],
                wins=optional_int(r.get("wins")),
                driver=driver(r["Driver"]),
                constructors=[constructor(c) for c in r["Constructors"]],
            )
            for r in await self.rows(
                f"{year}/driverStandings",
                "StandingsTable",
                "StandingsLists",
                self.http.settings.medium_ttl,
                "DriverStandings",
            )
        ]

    @normalized
    async def constructor_standings(self, year: int) -> list[ConstructorStanding]:
        return [
            ConstructorStanding(
                position=optional_int(r.get("position")),
                points=r["points"],
                wins=optional_int(r.get("wins")),
                constructor=constructor(r["Constructor"]),
            )
            for r in await self.rows(
                f"{year}/constructorStandings",
                "StandingsTable",
                "StandingsLists",
                self.http.settings.medium_ttl,
                "ConstructorStandings",
            )
        ]
