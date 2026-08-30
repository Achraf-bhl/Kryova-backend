from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_SECRET_KEY = "changeme"
MIN_SECRET_KEY_LENGTH = 32

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Hard ceiling on a client-chosen upload chunk. A chunk is held in memory or on
# disk in one piece while it is verified, so an unbounded value lets one request
# decide how much of the machine it gets. Lives here rather than in the schema
# because both the schema's `le=` and the media service check it.
MAX_UPLOAD_CHUNK_BYTES = 64 * 1024 * 1024

JOB_QUEUE_BACKENDS = ("threadpool", "inline")

# Every value below is an allow-list because each field is read straight into a
# security decision: the signing algorithm, a Set-Cookie attribute, and the
# switch that turns the production guards on. A free-text field there means a
# typo fails *open*, which is exactly what happened before these existed.
JWT_ALGORITHMS = frozenset({"HS256", "HS384", "HS512"})
COOKIE_SAMESITE_VALUES = frozenset({"lax", "strict", "none"})
ENVIRONMENTS = frozenset({"development", "test", "staging", "production"})


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

    # `.env` is gitignored: it holds real credentials and must never be
    # committed. `.env.example` documents every setting with placeholders and is
    # the file that is tracked. `.env.local` is read second, so it wins for any
    # key it sets -- a convenient place for per-machine provider API keys.
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
    redis_url: str | None = None

    # Rate limiting keys off the client address. `X-Forwarded-For` is a header
    # any client can write, so it is only believed when a reverse proxy is known
    # to be in front and to append to it: turn this on ONLY when every request
    # reaches the app through that proxy. `trusted_proxy_count` is how many
    # proxies append, counted from the right-hand (nearest) end of the header --
    # with one nginx in front the client address is the last-but-one entry, and
    # everything to its left was supplied by the caller and is worthless.
    trust_proxy_headers: bool = False
    trusted_proxy_count: int = 1

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
    job_queue_backend: str = "threadpool"  # "threadpool" or "inline"

    # Analysis limits, to keep one upload from consuming the whole machine.
    max_elements: int = 400_000
    # Element-size floor, as a divisor of the geometry's bounding-box diagonal.
    # A size below diagonal/this is refused before meshing starts: it is never a
    # deliberate request, and gmsh would spend minutes building a mesh that the
    # `max_elements` check then throws away.
    max_elements_along_diagonal: int = 2_000
    # Queued or running simulations one user may hold at once. Meshing and
    # solving are the most expensive thing this service does, so the quota is
    # what stops a single account from occupying every worker.
    max_concurrent_simulations_per_user: int = 3

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
    # How many past turns of a conversation are replayed to the model. Beyond
    # this the oldest turns are dropped, so a long session cannot grow the
    # prompt (and its cost) without limit.
    ai_max_context_messages: int = 40
    # Once a conversation passes this many messages the older ones are folded
    # into a running summary. Deliberately below `ai_max_context_messages`, so
    # summarisation happens before anything would be dropped outright.
    ai_summarise_after_messages: int = 30

    # CATIA desktop bridge. The daemon dials out to this service over a
    # WebSocket; see docs/CATIA_BRIDGE_PROTOCOL.md for the wire format.
    # Off switches the tools out of the agent's vocabulary entirely rather than
    # letting it call something that will always fail.
    catia_enabled: bool = True
    # One in-flight call per device (CATIA's automation surface is single
    # threaded), so a wedged call blocks that device's queue until it times out.
    catia_call_timeout_s: float = 30.0
    # A STEP export re-tessellates the whole part and legitimately takes minutes
    # on a large assembly, so it gets its own, much longer budget.
    catia_export_timeout_s: float = 180.0
    # Device tokens are long-lived by design: an engineer pairs the workstation
    # once. Only the SHA-256 of the token is stored server-side.
    catia_device_token_ttl_days: int = 365
    # Pairing codes are single-use and short-lived -- they are read aloud or
    # typed from a screen, so the window is the security boundary.
    catia_pairing_code_ttl_minutes: int = 10
    # Per-device op ceiling, mirroring the daemon's own limit.
    catia_ops_per_minute: int = 60
    # Single-machine install: start and pair the bridge daemon here rather than
    # making the engineer read a pairing code off their own screen and type it
    # into a terminal on the same machine. Off for a hosted deployment, where
    # the server is not the user's workstation and has no business spawning
    # processes for an account. See app/catia/local_bridge.py.
    catia_local_bridge: bool = True
    # Where that locally started daemon dials back to. It is this server, so the
    # only reason to change it is a non-default bind port.
    catia_local_bridge_server: str = "http://127.0.0.1:8000"

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

    @field_validator("job_queue_backend")
    @classmethod
    def _known_job_backend(cls, value: str) -> str:
        """Refuse an unknown backend rather than silently falling back.

        `celery` used to be accepted here and was never implemented: the queue
        it selected ran meshing and solving inline on the request thread. A
        deployment that still sets it has to hear about it at startup, not
        discover it from a request that takes four minutes.
        """
        if value not in JOB_QUEUE_BACKENDS:
            raise ValueError(
                f"JOB_QUEUE_BACKEND must be one of {', '.join(JOB_QUEUE_BACKENDS)}; got {value!r}. "
                "Distributed queues are not implemented -- run more processes behind the "
                "threadpool backend instead."
            )
        return value

    @field_validator("jwt_algorithm")
    @classmethod
    def _known_jwt_algorithm(cls, value: str) -> str:
        """Refuse an algorithm the token layer cannot safely verify.

        `"none"` is the important one: an unsigned JWT is a forged JWT, and
        `jwt.decode(..., algorithms=["none"])` accepts one. This used to be a
        free-text field, so a typo in the environment silently downgraded every
        token in the system.
        """
        candidate = value.strip().upper()
        if candidate not in JWT_ALGORITHMS:
            raise ValueError(
                f"JWT_ALGORITHM must be one of {', '.join(sorted(JWT_ALGORITHMS))}; got {value!r}."
            )
        return candidate

    @field_validator("cookie_samesite")
    @classmethod
    def _known_samesite(cls, value: str) -> str:
        """`SameSite` is emitted verbatim into a Set-Cookie header.

        An unrecognised value makes browsers drop the attribute entirely, which
        silently removes the cross-site protection the setting exists to give.
        """
        candidate = value.strip().lower()
        if candidate not in COOKIE_SAMESITE_VALUES:
            raise ValueError(
                f"COOKIE_SAMESITE must be one of {', '.join(sorted(COOKIE_SAMESITE_VALUES))}; "
                f"got {value!r}."
            )
        return candidate

    @field_validator("environment")
    @classmethod
    def _known_environment(cls, value: str) -> str:
        """Reject an environment name nothing recognises.

        `is_production` used to be an exact match on "production", so
        `ENVIRONMENT=prod` -- a plausible typo -- evaluated false and turned off
        the docs gate, the cookie-secure requirement, HSTS and the secret-key
        check all at once, with no warning. Naming the allowed values means the
        typo is a startup crash instead of a silent downgrade.
        """
        candidate = value.strip().lower()
        if candidate not in ENVIRONMENTS:
            raise ValueError(
                f"ENVIRONMENT must be one of {', '.join(sorted(ENVIRONMENTS))}; got {value!r}. "
                "An unrecognised value used to disable every production guard silently."
            )
        return candidate

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
                '(generate one with `python -c "import secrets; '
                'print(secrets.token_urlsafe(48))"`)'
            )
        if not self.cookie_secure:
            problems.append("COOKIE_SECURE must be true so session cookies are HTTPS-only")
        if any(origin.startswith("http://") for origin in self.cors_origins):
            problems.append(
                f"CORS_ORIGINS contains a plaintext http:// origin: {self.cors_origins}"
            )
        if any(origin.strip() == "*" for origin in self.cors_origins):
            # Starlette pairs `allow_origins=["*"]` with `allow_credentials=True`
            # by echoing whichever Origin asked, which is credentialed
            # any-origin access -- every authenticated endpoint readable by any
            # site the user visits. The http:// check above deliberately does
            # not catch this, because "*" has no scheme.
            problems.append(
                'CORS_ORIGINS contains "*", which with allow_credentials=True lets any '
                "site read authenticated responses; list the exact origins instead"
            )

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
