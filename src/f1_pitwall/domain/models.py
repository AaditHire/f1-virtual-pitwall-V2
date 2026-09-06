from datetime import UTC, date
from typing import Annotated, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel, Field, computed_field

from f1_pitwall.domain.enums import SessionType

UTCTime = Annotated[AwareDatetime, AfterValidator(lambda dt: dt.astimezone(UTC))]


class Season(BaseModel):
    year: int
    source: str = "jolpica"


class Constructor(BaseModel):
    id: str
    name: str
    nationality: str | None = None


class Driver(BaseModel):
    id: str
    number: int | None = None
    code: str | None = None
    first_name: str
    last_name: str
    nationality: str | None = None

    @computed_field
    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


class Circuit(BaseModel):
    id: str
    name: str
    locality: str | None = None
    country: str | None = None


class Session(BaseModel):
    type: SessionType
    name: str
    date: date
    start: UTCTime | None = None
    end: UTCTime | None = None
    source: str
    provider_id: int | None = None
    cancelled: bool = False


class Event(BaseModel):
    year: int
    round: int
    name: str
    circuit: Circuit
    race_date: date
    sessions: list[Session] = Field(default_factory=list)
    source: str = "jolpica"
    warnings: list[str] = Field(default_factory=list)


class QualifyingResult(BaseModel):
    position: int | None = None
    driver: Driver
    constructor: Constructor
    q1: str | None = None
    q2: str | None = None
    q3: str | None = None
    source: str = "jolpica"


class GridEntry(BaseModel):
    position: int | None = None
    driver: Driver
    constructor: Constructor | None = None
    pit_lane: bool | None = None
    source: str


class RaceResult(BaseModel):
    position: int | None = None
    position_text: str | None = None
    driver: Driver
    constructor: Constructor
    grid: int | None = None
    points: float | None = None
    laps: int | None = None
    status: str | None = None
    time: str | None = None
    source: str = "jolpica"


class DriverStanding(BaseModel):
    position: int | None = None
    points: float
    wins: int | None = None
    driver: Driver
    constructors: list[Constructor]


class ConstructorStanding(BaseModel):
    position: int | None = None
    points: float
    wins: int | None = None
    constructor: Constructor


class NewsArticle(BaseModel):
    headline: str
    source: str
    url: str
    published_at: UTCTime | None = None
    summary: str | None = None
    image_url: str | None = None
    tags: list[str] = Field(default_factory=list)


class DataSourceStatus(BaseModel):
    provider: str
    status: Literal["not_checked", "ok", "degraded"] = "not_checked"
    checked_at: UTCTime | None = None
    last_success_at: UTCTime | None = None
    errors: dict[str, str] = Field(default_factory=dict)
    unavailable_resources: dict[str, str] = Field(default_factory=dict)


class NewsFeed(BaseModel):
    articles: list[NewsArticle]
    provider_status: list[DataSourceStatus]


class NextSession(BaseModel):
    event: Event
    session: Session
    seconds_until_start: float


class Home(BaseModel):
    active_season: Season | None = None
    current_or_next_event: Event | None = None
    weekend_status: str = "unavailable"
    next_session: NextSession | None = None
    weekend_schedule: list[Session] = Field(default_factory=list)
    grid_event: Event | None = None
    recent_or_available_grid: list[GridEntry] = Field(default_factory=list)
    driver_standings_top: list[DriverStanding] = Field(default_factory=list)
    constructor_standings_top: list[ConstructorStanding] = Field(default_factory=list)
    latest_news: list[NewsArticle] = Field(default_factory=list)
    provider_status: list[DataSourceStatus] = Field(default_factory=list)
    errors: dict[str, str] = Field(default_factory=dict)
