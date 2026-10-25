"""Runtime configuration, read from environment variables and an optional local `.env` file."""

from pydantic import HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict

# Official root discovery file, listed in the Montréal open-data catalogue (docs/sources.yml).
OFFICIAL_GBFS_DISCOVERY_URL = "https://gbfs.velobixi.com/gbfs/gbfs.json"


class Settings(BaseSettings):
    """Each field is read from the environment variable of the same name in upper case.

    Empty values count as unset, so a copied `.env.example` keeps the defaults.
    """

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_ignore_empty=True, extra="ignore"
    )

    gbfs_discovery_url: HttpUrl = HttpUrl(OFFICIAL_GBFS_DISCOVERY_URL)
    database_url: str | None = None  # unused until the database exists (issue #5)
