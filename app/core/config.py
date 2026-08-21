from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _as_psycopg_url(url: str) -> str:
    """Force SQLAlchemy onto the psycopg 3 driver.

    Neon hands out plain `postgresql://` URLs, which SQLAlchemy would route to
    psycopg2. Rewriting here means the connection string can be pasted from the
    Neon console unedited.
    """
    for prefix in ("postgresql+psycopg://", "postgresql+psycopg2://", "postgresql+asyncpg://"):
        if url.startswith(prefix):
            return url
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


class Settings(BaseSettings):
    """Application settings, loaded from the environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    project_name: str = "Kryova API"
    api_v1_prefix: str = "/api/v1"

    # Postgres only -- Neon in every environment, so what runs in dev is what
    # runs in production, down to the dialect.
    database_url: str
    db_pool_size: int = 5
    db_max_overflow: int = 5
    # Neon closes idle connections; recycle before it does rather than after.
    db_pool_recycle_seconds: int = 280

    # The schema the application's tables live in. Every statement is compiled
    # schema-qualified against this rather than relying on search_path -- see
    # app/core/database.py for why that distinction matters on a pooled endpoint.
    db_schema: str = "public"

    # Tests build their tables in a separate schema of the same database, so a
    # run can never touch application data.
    test_schema: str = "kryova_test"

    secret_key: str = "changeme"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # Local heavy-file store. CAD files, meshes, result fields and vector
    # indexes never leave this machine: only their small metadata rows go to
    # the cloud database.
    media_root: Path = BASE_DIR / "media_data"
    media_chunk_size: int = 8 * 1024 * 1024
    max_media_bytes: int = 2 * 1024 * 1024 * 1024
    # Abandoned chunked uploads are swept after this long.
    upload_session_ttl_hours: int = 24

    # Background jobs. Inline runs meshing/solving on the request thread, which
    # is what tests and `--reload` dev servers want.
    inline_jobs: bool = False
    job_workers: int = 2

    # Analysis limits, to keep one upload from consuming the whole machine.
    max_elements: int = 400_000

    # Uploads
    max_upload_bytes: int = 200 * 1024 * 1024
    allowed_geometry_formats: list[str] = Field(
        default_factory=lambda: ["step", "stp", "iges", "igs", "stl"]
    )

    @field_validator("database_url")
    @classmethod
    def _require_postgres(cls, value: str) -> str:
        normalised = _as_psycopg_url(value)
        if not normalised.startswith("postgresql+"):
            raise ValueError(
                "DATABASE_URL must be a PostgreSQL URL (Neon). "
                f"Got: {value.split('://', 1)[0] if '://' in value else value!r}"
            )
        return normalised

    @property
    def media_staging_dir(self) -> Path:
        """Where in-progress chunked uploads accumulate before assembly."""
        return self.media_root / "_staging"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
