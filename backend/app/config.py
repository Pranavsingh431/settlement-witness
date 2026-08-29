"""Application settings loaded from the environment.

Only settings that the current phase actually uses are declared here. Database
and model provider settings are added by the phases that introduce them.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, model_validator
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
        env_ignore_empty=True,
        env_prefix="SW_",
        extra="ignore",
        populate_by_name=True,
    )

    app_env: AppEnv = "local"
    log_level: LogLevel = "INFO"
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    database_path: Path = Path("../data/generated/settlement.sqlite")
    """SQLite file the API reads and writes.

    Relative paths are resolved against the process working directory, which
    is the backend directory when the service is started by `make api`."""

    database_url: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("SW_DATABASE_URL", "SW_DATABASE_DATABASE_URL"),
    )
    """Optional SQLAlchemy database URL for a managed database.

    The URL is a secret because it normally contains a database password. A
    configured URL takes precedence over ``database_path``. ``SW_DATABASE_URL``
    is the canonical operator-facing name; Vercel's Neon integration currently
    provisions ``SW_DATABASE_DATABASE_URL``, which is accepted as an equivalent
    source. Vercel functions must use this setting: their filesystem is not
    durable enough to hold the append-only audit trail.
    """

    max_upload_bytes: int = Field(default=8 * 1024 * 1024, ge=1)
    """Largest CSV document the import endpoint will accept, in bytes.

    Two limits derive from this one. The request body that carries the document
    is bounded at this value plus a small allowance for the multipart envelope,
    counted before anything parses it, so an oversized request cannot be spooled
    whatever its `Content-Length` claims. The document inside is then checked
    against this value exactly. Either refusal is a 413, and neither reaches the
    import service or leaves a receipt.

    The default holds a document of roughly forty thousand settlement lines,
    which is far more than the demonstration corpus and small enough that one
    request cannot exhaust a laptop."""

    @model_validator(mode="after")
    def _production_requires_a_managed_database(self) -> "Settings":
        """Refuse a production process that would fall back to a local SQLite file."""
        if self.app_env == "production" and self.database_url is None:
            message = (
                "SW_DATABASE_URL or SW_DATABASE_DATABASE_URL must be set when SW_APP_ENV=production"
            )
            raise ValueError(message)
        return self

    @property
    def resolved_database_url(self) -> str:
        """Return the configured managed URL or the local SQLite URL.

        This is intentionally the sole selection point. A deployment cannot
        accidentally create one database from ``database_path`` while a CLI or
        another function uses ``database_url``.
        """
        if self.database_url is not None:
            supplied = self.database_url.get_secret_value()
            if supplied.startswith("postgres://"):
                return "postgresql+psycopg://" + supplied.removeprefix("postgres://")
            if supplied.startswith("postgresql://"):
                return "postgresql+psycopg://" + supplied.removeprefix("postgresql://")
            return supplied
        return f"sqlite+pysqlite:///{self.database_path}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the settings for this process, resolving them only once."""
    return Settings()
