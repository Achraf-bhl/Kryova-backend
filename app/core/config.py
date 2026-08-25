from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_SECRET_KEY = "changeme"
MIN_SECRET_KEY_LENGTH = 32

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

    # `.env` is tracked in this repo on purpose, so it is the wrong place for a
    # real credential. `.env.local` is gitignored and read second, which means
    # it wins for any key it sets -- put provider API keys there.
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"), env_file_encoding="utf-8", extra="ignore"
    )

    project_name: str = "Kryova API"
    api_v1_prefix: str = "/api/v1"

    # Neon Postgres is required in every environment.
    database_url: str = "postgresql://localhost/kryova"
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

    # Deployment posture. "production" turns on the guards below; anything else
    # is treated as a developer machine.
    environment: str = "development"

    secret_key: str = "changeme"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30
    cookie_secure: bool = False
    cookie_samesite: str = "lax"

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    frontend_url: str = "http://localhost:3000"

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

    # AI. Which model serves the AI features is the user's choice -- see
    # app/ai/providers/. The default is local Ollama: CAD geometry and load
    # cases are proprietary engineering IP, so nothing is posted to a third
    # party unless the user opts in. A misconfigured provider makes the AI
    # endpoints report themselves unavailable; it never stops the app booting.
    #   ollama            -> local, no key, offline (default)
    #   anthropic         -> hosted Claude, needs AI_API_KEY
    #   openai_compatible -> OpenAI / LM Studio / vLLM / llama.cpp / Groq /
    #                        OpenRouter, needs AI_BASE_URL
    ai_provider: str = "ollama"
    ai_model: str = "qwen2.5-coder:7b"
    ai_base_url: str | None = None
    ai_api_key: str | None = None
    # Interpreting a result is a judgement task and gets more headroom than
    # parsing a sentence into a load case, which is near-mechanical.
    ai_effort_interpret: str = "high"
    ai_effort_parse: str = "low"
    ai_max_tokens: int = 8_000
    # A 7B model on CPU can take a minute; the default is generous on purpose.
    ai_timeout_seconds: float = 120.0

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
    def is_production(self) -> bool:
        return self.environment.strip().lower() == "production"

    @model_validator(mode="after")
    def _harden_production(self) -> "Settings":
        """Refuse to boot a production process with development-grade secrets.

        `secret_key` signs every access and refresh token. Left at its default,
        anyone holding a copy of this source can mint a valid token for any
        user, so a missing environment variable has to be a startup crash rather
        than a silently insecure deployment.
        """
        if not self.is_production:
            return self

        problems = []
        if self.secret_key == INSECURE_SECRET_KEY:
            problems.append("SECRET_KEY is still the default 'changeme'")
        elif len(self.secret_key) < MIN_SECRET_KEY_LENGTH:
            problems.append(
                f"SECRET_KEY is shorter than {MIN_SECRET_KEY_LENGTH} characters "
                "(generate one with `python -c \"import secrets; "
                'print(secrets.token_urlsafe(48))"`)'
            )
        if not self.cookie_secure:
            problems.append("COOKIE_SECURE must be true so session cookies are HTTPS-only")
        if any(origin.startswith("http://") for origin in self.cors_origins):
            problems.append(f"CORS_ORIGINS contains a plaintext http:// origin: {self.cors_origins}")

        if problems:
            raise ValueError(
                "Refusing to start with ENVIRONMENT=production:\n  - " + "\n  - ".join(problems)
            )
        return self

    @property
    def media_staging_dir(self) -> Path:
        """Where in-progress chunked uploads accumulate before assembly."""
        return self.media_root / "_staging"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
