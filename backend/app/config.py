"""Application settings loaded from the environment.

Only settings that the current phase actually uses are declared here. Database
and model provider settings are added by the phases that introduce them.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["local", "test", "ci", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]

# The .env file lives at the repository root, not next to this module. Anchoring
# the path here means the backend reads the same file whether it is started from
# the repository root, from the backend directory, or by an editor.
REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"


class Settings(BaseSettings):
    """Runtime settings for the backend service.

    Values come from environment variables prefixed with ``SW_`` and from the
    repository root ``.env`` file when it exists. Environment variables win.
    Invalid values fail at startup rather than at first use.
    """

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        env_prefix="SW_",
        extra="ignore",
    )

    app_env: AppEnv = "local"
    log_level: LogLevel = "INFO"
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    database_path: Path = Path("../data/generated/settlement.sqlite")
    """SQLite file the API reads and writes.

    Relative paths are resolved against the process working directory, which
    is the backend directory when the service is started by `make api`."""


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the settings for this process, resolving them only once."""
    return Settings()
