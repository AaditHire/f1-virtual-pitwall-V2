import os

from pydantic import BaseModel, Field, SecretStr


class Settings(BaseModel):
    jolpica_url: str = "https://api.jolpi.ca/ergast/f1"
    openf1_url: str = "https://api.openf1.org/v1"
    # OpenF1 serves real-time data only to sponsor accounts; without these, live mode is historical.
    openf1_token_url: str = "https://api.openf1.org/token"
    openf1_username: str | None = None
    openf1_password: SecretStr | None = None
    timeout: float = Field(default=15, gt=0)
    retries: int = Field(default=2, ge=0, le=5)
    long_ttl: float = Field(default=3600, ge=0)
    medium_ttl: float = Field(default=300, ge=0)
    short_ttl: float = Field(default=60, ge=0)
    cache_size: int = Field(default=256, gt=0)
    replay_cache_dir: str = ".cache/fastf1"
    replay_cache_size: int = Field(default=4, gt=0)
    # Prebuilt archives (HTTP base URL or local directory); unset or missing races use FastF1.
    replay_snapshot_url: str | None = (
        "https://github.com/AaditHire/f1-virtual-pitwall-V2/releases/download/replays"
    )
    news_feeds: dict[str, str] = {
        "BBC Sport F1": "https://feeds.bbci.co.uk/sport/formula1/rss.xml",
        "Autosport F1": "https://www.autosport.com/rss/f1/news/",
    }

    @classmethod
    def from_env(cls) -> "Settings":
        # Complex fields (e.g. feeds) use JSON; scalar fields are validated by Pydantic.
        import json

        values = {}
        for name in cls.model_fields:
            value = os.getenv(f"F1_{name.upper()}")
            if value is not None:
                values[name] = json.loads(value) if name == "news_feeds" else value
        return cls.model_validate(values)
