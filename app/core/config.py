from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_SECRET_KEY = "changeme"
MIN_SECRET_KEY_LENGTH = 32

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Hard ceiling on a client-chosen upload chunk. A chunk is held in memory or on
# disk in one piece while it is verified, so an unbounded value lets one request
# decide how much of the machine it gets. Lives here rather than in the schema
# because both the schema's `le=` and the media service check it.
MAX_UPLOAD_CHUNK_BYTES = 64 * 1024 * 1024

JOB_QUEUE_BACKENDS = ("threadpool", "process", "inline")

# Every value below is an allow-list because each field is read straight into a
# security decision: the signing algorithm, a Set-Cookie attribute, and the
# switch that turns the production guards on. A free-text field there means a
# typo fails *open*, which is exactly what happened before these existed.
#: Payment providers this build knows. Declared here so the validator can read
#: it without importing `app/core/payments.py`, which imports these settings.
PAYMENT_PROVIDERS = frozenset({"none", "stripe", "fake"})

JWT_ALGORITHMS = frozenset({"HS256", "HS384", "HS512"})
COOKIE_SAMESITE_VALUES = frozenset({"lax", "strict", "none"})
ENVIRONMENTS = frozenset({"development", "test", "staging", "production"})

#: Every limit a plan or a tenant may change (ROAD_TO_10 3.5). Each is also a global setting
#: of the same name, which is what answers when neither a tenant override nor a plan does --
#: the order `core/limits.py` resolves them in. Listed here, not in `metering`, because
#: `plan_limits` below is validated against it and config imports nothing from the app.
LIMIT_FIELDS: tuple[str, ...] = (
    "max_concurrent_simulations_per_user",
    "max_waiting_simulations_per_user",
    "chat_requests_per_minute",
    "simulation_requests_per_minute",
    "mcp_requests_per_minute",
    "catia_ops_per_minute",
)

#: The one limit for which 0 is a real answer: no queue at all, so a run past the concurrency
#: limit is refused exactly as it was before queueing existed. For a per-minute rate, 0 would
#: mean "refuse every request", which is a suspended account and not a limit.
LIMITS_THAT_MAY_BE_ZERO: frozenset[str] = frozenset({"max_waiting_simulations_per_user"})

#: The plan names `plan_limits` may use. Must equal `app.models.billing.Plan`'s values
#: (`tests/test_plan_limits.py` compares them), because this module cannot import models.
PLAN_NAMES: frozenset[str] = frozenset({"free", "team", "enterprise"})

# Declared here rather than in `app/mail/transport.py` so the validator below can
# read it without importing that module -- which imports these settings, and the
# cycle would only show up as an ImportError at startup.
MAIL_TRANSPORTS = frozenset({"smtp", "console", "memory"})

# The transports that can reach somebody who is not us. `console` and `memory`
# cannot, which is the whole of why production refuses them.
DELIVERING_MAIL_TRANSPORTS = frozenset({"smtp"})


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


class ModelPrice(BaseModel):
    """What a model costs, in US dollars per **million** tokens.

    Lives in configuration and nowhere in code: a vendor changes its prices and
    a deployment moves between vendors by editing `.env`, never by shipping a
    release (the user's rule, 2026-10-04). Nothing here is a Kryova claim about
    what any vendor charges -- every figure is the operator's, copied from the
    vendor's own price page on the day, and an absent model has *no price*, which
    the ledger records as unknown rather than as free.

    `cached_input` is what a prompt-cache read costs. Left out it defaults to the
    full `input` price, so a vendor whose discount the operator has not entered
    is over-estimated rather than under-estimated.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    input: Decimal = Field(ge=0, description="USD per million fresh input tokens")
    output: Decimal = Field(ge=0, description="USD per million output tokens")
    cached_input: Decimal | None = Field(
        default=None, ge=0, description="USD per million input tokens served from the cache"
    )


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
    # How long one connection attempt may take before it is abandoned. Not a
    # tuning knob: without it psycopg on Windows never notices a refused port,
    # so a server started while Postgres was down sat at "Waiting for
    # application startup" forever instead of failing (measured 2026-09-14).
    db_connect_timeout_seconds: int = 10

    # A Postgres run from its zip archive -- no installer, no Windows service --
    # does not survive a reboot. Name its binaries and data directory and the
    # server starts it at startup when DATABASE_URL points at this machine and
    # nothing is answering. Unset, nothing is ever started. See
    # app/core/local_postgres.py.
    local_postgres_bin_dir: str | None = None
    local_postgres_data_dir: str | None = None

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

    # Both spellings of the loopback origin, because a browser treats them as different origins
    # and the desktop shell loads the app from the numeric one (CLAUDE.md, *Driving the GUI*
    # 2a: Chromium resolves `localhost` to ::1 and the stack is IPv4). With only the first, the
    # window opened and every API call failed CORS, which reads as "Failed to fetch".
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"]
    )
    frontend_url: str = "http://localhost:3000"
    redis_url: str | None = None
    # How many server processes run. `uvicorn` and `gunicorn` both read this same
    # `WEB_CONCURRENCY` variable to choose their `--workers`, so one setting is the fact
    # and the process manager acts on it. The application cannot count its own siblings,
    # which is why it has to be told: with more than one, an in-process rate limiter is N
    # separate limiters and the real limit is N times the configured one, so production
    # refuses that combination (`_harden_production`, ROAD_TO_10 3.1). The desktop app is
    # one process and leaves it at 1.
    web_concurrency: int = Field(default=1, ge=1)

    # Rate limiting keys off the client address. `X-Forwarded-For` is a header
    # any client can write, so it is only believed when a reverse proxy is known
    # to be in front and to append to it: turn this on ONLY when every request
    # reaches the app through that proxy. `trusted_proxy_count` is how many
    # proxies append, counted from the right-hand (nearest) end of the header --
    # with one nginx in front the client address is the last-but-one entry, and
    # everything to its left was supplied by the caller and is worthless.
    trust_proxy_headers: bool = False
    trusted_proxy_count: int = 1

    # Mail (P1.5). `console` writes messages to the log so a developer can click
    # a verification link without running a mail server; production refuses it
    # in `_harden_production`, because a deployment left on it still returns 204
    # from a password reset and still tells the user to check an inbox nothing
    # was sent to. `memory` is the test transport.
    mail_transport: str = "console"
    mail_from: str = "Kryova <no-reply@kryova.local>"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True

    # How long a verification link stays usable, and how often one may be asked
    # for. The resend window is per account rather than per IP: an attacker who
    # wants to use us to post mail at somebody picks the address, not the route
    # they ask from, so an IP budget is the wrong denominator for this one.
    email_verification_ttl_hours: int = 24
    email_verification_resend_seconds: int = 60

    # Verification gates project creation, not sign-in (P1.5): friction where it
    # protects and not where it annoys. Turning it off is for a deployment with
    # no mail transport that still wants the product to work end to end.
    require_verified_email_for_projects: bool = True

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
    # How many jobs run at once, and how many threads each solve may use (ROAD_TO_10 6.2).
    # **Unset means derived from this machine** (`app/core/compute_plan.py`): the cores that are
    # actually there, less what CATIA and the system keep. A value set here is used as given,
    # even where it oversubscribes -- the operator may know something the derivation does not --
    # and the startup log and `/admin/health` say which one is in force. Read the figure through
    # `compute_plan.current()`, never from these two fields.
    job_workers: int | None = Field(default=None, ge=1)
    solver_threads: int | None = Field(default=None, ge=1)
    # Import the OCCT kernel on a background thread at startup so the first geometry operation
    # does not pay its 2.2 s (measured 2026-10-05). Off in the test suite.
    warm_geometry_kernel: bool = True
    job_queue_backend: str = "threadpool"  # "threadpool", "process" or "inline"
    # With `job_queue_backend=process`: a hard ceiling on each worker process's *address space*
    # (POSIX RLIMIT_AS; ignored on Windows). Unset by default because address space is larger
    # than resident memory and a low figure can stop a solve that would have fitted in RAM.
    job_memory_limit_mb: int | None = Field(default=None, ge=256)
    # The autoscale recommendation (`app/jobs/autoscale.py`, GET /admin/compute/scaling).
    # The application computes a worker count; an orchestrator acts on it. `job_workers`
    # above is the jobs-per-worker figure the recommendation divides by.
    autoscale_min_workers: int = 1
    autoscale_max_workers: int = 8
    autoscale_target_wait_s: float = 120.0

    # Analysis limits, to keep one upload from consuming the whole machine.
    # **A value set here is used as given; unset, the limit for a structural run is derived from
    # this machine's memory** and never exceeds 400,000 (`app/simulation/memory.py::element_limit`).
    # Read it through that function. Conduction, flow and plane runs have far less to hold per
    # element and keep 400,000 when this is unset (`memory.default_element_limit`).
    max_elements: int | None = Field(default=None, ge=1)
    # Memory a solve admits itself against (ROAD_TO_10 6.3). `memory_reserve_mb` is what is always
    # left for everything else on the workstation (unset: a tenth of the machine's, at least
    # 1 GB); a solve that does not fit what is free waits up to `memory_wait_s`, and one that
    # can never fit is refused at once.
    # Grids of one convergence study solved at once (ROAD_TO_10 6.5). 1 = one after another,
    # exactly as before. Above 1 the grids still mesh one at a time (gmsh's lock) and each solve
    # is admitted against memory separately, so a study that does not fit waits rather than
    # fails. Default 1 because the win is unmeasured: see docs/MAKING_IT_FASTER.md.
    study_concurrency: int = Field(default=1, ge=1, le=5)
    memory_reserve_mb: int | None = Field(default=None, ge=0)
    memory_wait_s: float = Field(default=120.0, ge=0)
    # Element-size floor, as a divisor of the geometry's bounding-box diagonal.
    # A size below diagonal/this is refused before meshing starts: it is never a
    # deliberate request, and gmsh would spend minutes building a mesh that the
    # `max_elements` check then throws away.
    max_elements_along_diagonal: int = 2_000
    # Queued or running simulations one user may hold at once. Meshing and
    # solving are the most expensive thing this service does, so the quota is
    # what stops a single account from occupying every worker.
    max_concurrent_simulations_per_user: int = 3
    # How many more runs one user may have *waiting* behind that limit (ROAD_TO_10 3.4). A
    # run past the concurrency limit is queued and started when a slot frees, up to this many;
    # past it, the request is refused with the position it would have taken. 0 turns queueing
    # off and restores the old refusal.
    max_waiting_simulations_per_user: int = 10

    # Per-principal request budgets (P1.6). These are *rate* limits, distinct
    # from the concurrency cap above and from P8's quotas: this is "how often
    # may one identity ask", not "how much may they consume". A turn is minutes
    # of GPU and a simulation is minutes of CPU, so the numbers are generous
    # per minute and exist to stop a loop, not to ration ordinary work.
    chat_requests_per_minute: int = 20
    simulation_requests_per_minute: int = 10
    # MCP calls (E23.3). One `tools/call` is one request, and a client agent makes many per
    # task, so this is wider than the chat budget, which is one per turn.
    mcp_requests_per_minute: int = 120
    # Limits by plan (ROAD_TO_10 3.5), as JSON: `{"free": {"chat_requests_per_minute": 10},
    # "team": {"chat_requests_per_minute": 40}}`. Any name in `LIMIT_FIELDS`, any plan in
    # `PLAN_NAMES`. **Empty by default and that is the honest state**: a price list is the
    # operator's decision, and a plan that says nothing falls through to the global setting of
    # the same name, so a fresh deployment behaves exactly as it did before plans carried limits.
    # A per-tenant override on the billing account outranks a plan.
    plan_limits: dict[str, dict[str, int]] = Field(default_factory=dict)
    # Login, the second factor and the silent token refresh, counted *per address*
    # (ROAD_TO_10 3.3). Wider than the ten a minute the other pre-sign-in routes keep,
    # because one office behind one NAT is one address and a hundred engineers all signing
    # in on a Monday morning. It is safe to be wider only because those same routes are
    # also counted per *account* at ten a minute (`rate_limit.account_limiter`), so online
    # guessing at one account is capped whatever the address budget does. Lower it to
    # tighten credential stuffing across many accounts from one address; the cost of
    # lowering it is the NAT lockout this exists to remove.
    auth_ip_requests_per_minute: int = 30

    # --- Billing (P8.2) ------------------------------------------------------
    # `none` is the default and the only one a self-hosted install needs: plans
    # are an operator action and no money moves. `stripe` needs the optional
    # `stripe` package and a key; `fake` is for tests.
    payment_provider: str = "none"
    stripe_api_key: str = ""

    # **Plan allowances are the operator's, not a Kryova price list.** Decision
    # 4 says this product is free and open, so a self-hosted deployment sets
    # what its own users may do and a hosted one sets what it sells. `0` means
    # "no allowance set", which `check_quota` reads as unlimited and *says so*
    # rather than letting an unset limit read as a policy. Nothing here is a
    # number Kryova is claiming; see `core/metering.PROVISIONAL_PLANS`.
    free_plan_solver_seconds: int = 0
    free_plan_ai_tokens: int = 0
    free_plan_storage_bytes: int = 0
    team_plan_solver_seconds: int = 0
    team_plan_ai_tokens: int = 0
    team_plan_storage_bytes: int = 0

    # --- Approval gates (P5.5) ----------------------------------------------
    # Whether the person who raised a gate may approve it themselves. **False by
    # default, so a team gets four-eyes without configuring anything.** It is a
    # setting rather than a hard rule because most self-hosted installs have
    # exactly one engineer, and a review process that cannot be completed is one
    # people route around entirely -- which loses the record as well as the
    # second opinion. Turning it on is a decision somebody makes knowingly, and
    # the gate still records who approved and what they saw.
    allow_self_approval: bool = False

    # AI. Which model serves the AI features -- see app/ai/providers/.
    # Hosted only (decided 2026-10-04): no local model is run, for development,
    # tests or production. That also means every deployment posts geometry
    # summaries and load cases to the model vendor, a data-flow fact that
    # belongs in the customer contract, not buried here. A misconfigured
    # provider makes the AI endpoints report themselves unavailable; it never
    # stops the app booting.
    #
    # Moving vendor is these four variables and nothing in the code: no model
    # name, URL or key is written anywhere else.
    #   deepseek          -> DeepSeek (api.deepseek.com), needs AI_API_KEY
    #   anthropic         -> hosted Claude, needs AI_API_KEY
    #   nvidia            -> NVIDIA NIM (build.nvidia.com), needs AI_API_KEY
    #   openai_compatible -> OpenAI (https://api.openai.com/v1) / vLLM / Groq /
    #                        OpenRouter, needs AI_BASE_URL
    ai_provider: str = "deepseek"
    ai_model: str = "deepseek-flash"
    ai_base_url: str | None = None
    ai_api_key: str | None = None
    # Interpreting a result is a judgement task and gets more headroom than
    # parsing a sentence into a load case, which is near-mechanical. "Effort" is
    # a hint each provider translates into its vendor's own terms; for a
    # reasoning model "low" and below means thinking off.
    ai_effort_interpret: str = "high"
    ai_effort_parse: str = "low"
    # The effort an agent step that may call tools runs at -- the one judgement
    # that matters (which of 201 CATIA operations, with which numbers). "low" is
    # *thinking off*: the fast, cheap, non-reasoning mode, which is the default
    # because a turn is ~20 of these steps and each reasoning step adds thousands
    # of billed output tokens and tens of seconds. Raise it ("high", "max") to
    # buy judgement; measure the difference on the prompt ladder before relying
    # on it, because nothing here has run against a live model.
    ai_effort_chat: str = "low"
    # Whether a reasoning model reasons at all. False switches it off on every
    # call (DeepSeek, NVIDIA), which is faster and cheaper and measurably worse
    # at picking the right CATIA operation. The providers that take no such
    # switch ignore it.
    ai_thinking: bool = True
    # Tokens a call that reasons may spend beyond its answer's own cap
    # (`ai_max_tokens`). A reasoning model will spend thousands deliberating over
    # a trivial question if nothing stops it.
    ai_reasoning_budget: int = 8_192
    # Which model looks at a render (Phase 4.2). Separate from `ai_model` for a
    # deployment whose main model cannot see. Unset means "use ai_model", which
    # is right for a vendor whose every model reads images; a model that cannot
    # answers the request with an error, and the visual check then reports
    # itself unchecked rather than agreeing.
    ai_vision_model: str | None = None
    #: How many tool schemas to put in front of the model in one turn — master
    #: plan 16.1. 0 (the default) offers the whole registry, which is what every
    #: deployment did before retrieval existed: it changes what the model sees,
    #: so it is switched on deliberately and measured, never inherited.
    #:
    #: It narrows the *offer* only. `ToolBox.call` still accepts every tool, so
    #: no setting of this can make a capability unreachable.
    ai_tool_limit: int = 0
    #: Who names the tool family a request needs before the turn starts, on
    #: top of the lexical rules `tool_retrieval.select` always runs -- see
    #: `app/ai/laya_decide.py`'s module docstring for the measurement this is
    #: based on. `"none"` (default): lexical only, byte-identical to every
    #: deployment before this existed. `"laya"`: a 421M local decision model
    #: answers in ~140 ms and adds no load on the conversational provider.
    #: Local, so off unless the user agrees, and installed only from
    #: `requirements-laya.txt`. `"llm"`: the agent's own hosted provider
    #: answers, a billed request per decision (more when the answer needs a
    #: repair), counted in the turn's usage. Only matters when `ai_tool_limit` is narrowing the offer at all.
    ai_intent_router: str = "none"
    #: Where Laya runs. `"auto"` (default) takes CUDA when torch can see it, else
    #: the CPU. `"cpu"` keeps a 421M model off the GPU entirely -- the right choice
    #: when the conversational model already needs the whole card (a 27B on a
    #: 16 GB GPU spills to system RAM as it is), and it needs no CUDA build of
    #: torch, which matters on a GPU newer than the pinned wheel supports.
    ai_intent_router_device: str = "auto"

    #: Tokens one user may spend per UTC day. 0 means unlimited.
    #:
    #: This field has to exist for the environment variable to do anything.
    #: `usage.daily_token_budget()` reads it with `getattr(..., DEFAULT)`, and
    #: `Settings` is configured `extra="ignore"` — so while the field was absent,
    #: `AI_DAILY_TOKEN_BUDGET` was accepted, silently dropped, and the documented
    #: knob did nothing. Found on 2026-09-05 by hitting the limit during an
    #: overnight test run and failing to raise it. `max_steps()` carries a comment
    #: about exactly this trap and works around it by reading `os.environ` too;
    #: the budget had the same hole and no workaround.
    ai_daily_token_budget: int = 2_000_000

    #: What each model costs, as JSON: `{"deepseek-flash": {"input": 0.14,
    #: "cached_input": 0.014, "output": 0.28}}` -- US dollars per million tokens,
    #: keyed on the exact model name the provider is configured with. **Empty by
    #: default, on purpose**: a default would be a price list Kryova is claiming
    #: for a vendor it does not speak for. A call to a model with no entry is
    #: recorded with an unknown cost, never a zero one, and no cost budget can be
    #: enforced against it (`app/ai/pricing.py`).
    ai_prices: dict[str, ModelPrice] = Field(default_factory=dict)

    #: Dollars one user may spend per UTC day, priced from `ai_prices`. 0 means
    #: unlimited. Enforced beside `ai_daily_token_budget`, not instead of it: a
    #: call to an unpriced model still counts against the token budget.
    ai_daily_cost_budget_usd: Decimal = Decimal(0)

    #: Dollars one organisation may spend per UTC day / per UTC calendar month,
    #: across every member (ROAD_TO_10 1.3). 0 means unlimited. A billing
    #: account's own override outranks these (`BillingAccount.ai_org_*`).
    ai_org_daily_cost_budget_usd: Decimal = Decimal(0)
    ai_org_monthly_cost_budget_usd: Decimal = Decimal(0)

    #: Verbosity of the application's own logs, as a level name.
    #:
    #: Declared as a real field for the reason the budget above documents:
    #: `Settings` is `extra="ignore"`, so a `LOG_LEVEL` variable with no field
    #: behind it is accepted and silently dropped. INFO rather than WARNING
    #: because the interesting lines in this codebase -- which tool the agent
    #: reached for, which document the bridge activated, why a corpus file was
    #: skipped -- are all logged at INFO, and a server that prints none of them
    #: is one you debug by adding print statements.
    log_level: str = "INFO"

    ai_max_tokens: int = 8_000
    # A reasoning model thinking at high effort can take most of a minute before
    # it answers; the default is generous on purpose.
    ai_timeout_seconds: float = 120.0
    # How many past turns of a conversation are replayed to the model. Beyond
    # this the oldest turns are dropped, so a long session cannot grow the
    # prompt (and its cost) without limit.
    ai_max_context_messages: int = 40
    # Once a conversation passes this many messages the older ones are folded
    # into a running summary. Deliberately below `ai_max_context_messages`, so
    # summarisation happens before anything would be dropped outright.
    ai_summarise_after_messages: int = 30
    # The same two limits in *estimated tokens*, because a message count cannot see
    # that one tool result is 6,000 characters and another is 20 (`app/ai/context.py`,
    # `app/ai/tokens.py`). Whichever limit is reached first wins, for the window and
    # for the fold. `ai_context_token_budget` is what the replayed history may hold;
    # sized so history + the tool registry (~66k) + system prompt + state block + a
    # reply fit a 128k-token window with headroom. The fold must fire first, so
    # `ai_summarise_after_tokens` has to be the smaller (refused at startup otherwise:
    # a window that drops material before it is folded forgets it for good).
    # 0 for the budget switches both off and leaves the message counts alone; 0 for the
    # fold alone leaves only the window budget.
    ai_context_token_budget: int = 30_000
    ai_summarise_after_tokens: int = 20_000
    # Old tool results are replayed as a one-line digest once more than this many
    # are in the window (`app/ai/digest.py`); the newest this-many stay verbatim.
    # 0 turns digests off. **The saving is measured, the accuracy is not** (THE QUEUE
    # H10): the cost side is `tests/test_ai_replay_digest.py`, the question of whether a
    # model reasons as well from a digest is a ladder run's to answer.
    ai_replay_keep_verbatim: int = 8
    # The digest boundary moves in steps of this many results, never one at a time, and
    # the size of the step is a cost decision with a measured shape. Every move changes
    # the prompt from the first newly digested result onward, so everything after it is
    # re-billed at the full price instead of the cache price; what a digest saves is only
    # the cache price of the bytes it removes, on each later step. At a 90 % cache
    # discount a move therefore has to be paid back over many steps, and the old default
    # (keep 12, block 6) moved so often it cost up to 37 % *more* than not shortening
    # anything on a 30-step turn. A block of 1 -- a boundary that slides -- costs 2-3x.
    # Eight kept and a block of 24 was never worse than not digesting in a sweep of 30
    # settings (turns of 20-60 steps, results of 1-6 KB) and saved 7.5 % on average at
    # a 90 % discount; the settings that saved more kept fewer results verbatim, which
    # is the accuracy risk this file cannot measure, so they were not chosen.
    ai_replay_digest_block: int = 24

    # Reference material the assistant can consult -- CATIA and FEA manuals,
    # indexed on this machine. Off removes the lookup tool from the agent's
    # vocabulary entirely, rather than leaving it to call something that will
    # always come back empty.
    knowledge_enabled: bool = True
    knowledge_root: Path = BASE_DIR / "data" / "bm25"
    # Passages returned per lookup. More crowds the transcript out of the
    # context window; fewer and a broad question misses the paragraph it needed.
    knowledge_max_passages: int = 5

    # The structured CATIA V5 reference (`app.catia_kb`): workbenches, commands,
    # dialog fields, error messages, aerospace vocabulary, and the localised
    # command names for every interface language the manuals cover. It ships in
    # the code rather than in an index, so unlike the corpus above it is always
    # present -- this switch exists for measuring its effect, not for deployments
    # that lack the data.
    catia_knowledge_enabled: bool = True
    # Whether a turn carries a few lines naming the CATIA terms found in the
    # user's message. Costs tokens on the turns it fires and is what lets a
    # small local model get the workbench and menu path right without having to
    # decide to call a tool first.
    catia_knowledge_brief_enabled: bool = True
    # Whether a documentation search is widened with the same term in the other
    # languages the manuals are written in. Half this corpus is French; without
    # it, half of it is unreachable from an English question.
    catia_knowledge_expand_queries: bool = True

    # Which kernel executes a geometry tool call.
    #   catia -> a real seat over the desktop bridge (needs Windows + a licence)
    #   occt  -> the open kernel, in this process; no licence, no seat, no network
    # Default `catia` so an existing deployment is unchanged. `occt` is what makes
    # Decision 1 true of the product rather than only of the libraries: the agent
    # can build geometry on any machine. Never chosen automatically -- a
    # deployment that silently fell back would hand the user a part built by a
    # different kernel without saying so.
    geometry_backend: str = "catia"

    # Which solver runs a structural job. `internal` is the in-house
    # linear-static solver; `calculix` federates it across a subprocess boundary
    # (Decision 4). Default `internal` so an existing deployment is unchanged,
    # and -- exactly as with `geometry_backend` above -- **never chosen
    # automatically**: a deployment that silently fell back would hand the user a
    # result computed by something they did not select, and Decision 3 binds a
    # result to what produced it. A named solver that cannot run is an error, not
    # a substitution.
    solver_backend: str = "internal"
    # Which solver runs a *conduction* job, and it is deliberately its own
    # setting. `SOLVER_BACKEND` names a structural solver: a deployment that had
    # set it to `calculix` and then asked for a temperature field would be asking
    # a name chosen for a different analysis to answer this one, and that is
    # refused by name rather than quietly handed the in-house solver. Since
    # 2026-09-17 ccx's `*HEAT TRANSFER, STEADY STATE` step is federated behind
    # the seam, so `calculix` is a valid value here -- selected separately,
    # because the two choices are independent.
    conduction_backend: str = "internal"
    # Which solver steps a *transient* conduction job forward in time. Its own
    # setting for the reason `conduction_backend` is: a steady solver and a
    # time-stepping one are chosen independently, and a deployment that pointed
    # one of them at a federated engine must not thereby move the other.
    transient_conduction_backend: str = "internal"
    # The most temperature values a transient run may hold: time samples (steps
    # + 1) times nodes. The whole history is kept -- in memory while stepping and
    # in the stored fields afterwards -- because a history thinned to fit would be
    # sampling where the provenance says solved. So a run that would exceed it is
    # refused before the solve, naming the time step and the mesh size that would
    # bring it under. 50 million doubles is 400 MB.
    max_transient_values: int = 50_000_000
    # Where `ccx` is, when it is not on PATH. Empty means "look on PATH" --
    # `app/solve/calculix/run.py` refuses to fall back to PATH when this names a
    # path that does not exist, so a wrong setting is reported rather than
    # silently working with a solver the operator did not choose.
    calculix_path: str = ""
    # How a `flow-laminar` job reaches OpenFOAM (E10.2). OpenFOAM is GPL and runs
    # only as a separate process: `docker` runs the pinned image below, `local`
    # runs a machine's own install sourced onto PATH before the server started.
    # There is no flow backend setting beside these, for the reason
    # `registry.build_conduction_solver` gives: one engine needs no knob to choose it.
    openfoam_launcher: Literal["docker", "local"] = "docker"
    # The image is the version pin. It is never pulled during a run -- a run that
    # downloads a solver has a version nobody chose -- so a missing image is
    # refused by name with the `docker pull` that fixes it.
    openfoam_image: str = "opencfd/openfoam-default:2412"
    # A run that has not finished by then is stopped and refused, naming the cell
    # size as the lever, so a runaway mesh cannot hold a worker for ever.
    openfoam_timeout_s: float = 3600.0

    # How a multibody run reaches Project Chrono (E9.1). Chrono is BSD and could be
    # linked in-process, unlike OpenFOAM -- the process boundary here is not a licence
    # boundary but a packaging one: PyChrono ships through conda only, and Decision 1
    # keeps conda out of this deployment. So it runs in a container that has its own
    # conda, and this venv never grows one. `pychrono` must never enter any
    # requirements file: the PyPI name belongs to an unrelated timing utility and
    # installing it would make the availability probe report an engine that is not there.
    chrono_launcher: Literal["docker", "local"] = "docker"
    # The image is the version pin, and it is built rather than pulled: no published
    # image ships PyChrono, so `scripts/chrono_image.sh` makes one from micromamba.
    # Never built during a run, for `openfoam_image`'s reason.
    chrono_image: str = "kryova-chrono:9.0.1"
    # A multibody run that has not finished by then is stopped and refused, naming the
    # step size and the duration as the levers.
    chrono_timeout_s: float = 1800.0

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

    @field_validator("payment_provider")
    @classmethod
    def _known_payment_provider(cls, value: str) -> str:
        candidate = value.strip().lower()
        if candidate not in PAYMENT_PROVIDERS:
            raise ValueError(
                f"PAYMENT_PROVIDER must be one of {', '.join(sorted(PAYMENT_PROVIDERS))}; "
                f"got {value!r}."
            )
        return candidate

    @field_validator("mail_transport")
    @classmethod
    def _known_mail_transport(cls, value: str) -> str:
        candidate = value.strip().lower()
        if candidate not in MAIL_TRANSPORTS:
            raise ValueError(
                f"MAIL_TRANSPORT must be one of {', '.join(sorted(MAIL_TRANSPORTS))}; "
                f"got {value!r}."
            )
        return candidate

    @field_validator("plan_limits")
    @classmethod
    def _plan_limits_name_real_things(
        cls, value: dict[str, dict[str, int]]
    ) -> dict[str, dict[str, int]]:
        """A typo in `PLAN_LIMITS` must be a startup error, not a limit that silently never applies.

        An unknown plan or limit name would be read as "this plan says nothing" and fall
        through to the global setting -- configured, parsed, and doing nothing, which is the
        failure class this file refuses everywhere else.
        """
        for plan, limits in value.items():
            if plan not in PLAN_NAMES:
                raise ValueError(
                    f"PLAN_LIMITS names a plan {plan!r}; the plans are {', '.join(sorted(PLAN_NAMES))}."
                )
            for name, number in limits.items():
                if name not in LIMIT_FIELDS:
                    raise ValueError(
                        f"PLAN_LIMITS[{plan!r}] names a limit {name!r}; the limits are "
                        f"{', '.join(LIMIT_FIELDS)}."
                    )
                minimum = 0 if name in LIMITS_THAT_MAY_BE_ZERO else 1
                if number < minimum:
                    raise ValueError(
                        f"PLAN_LIMITS[{plan!r}][{name!r}] is {number}; it must be at least {minimum}."
                    )
        return value

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() == "production"

    @property
    def mail_reaches_real_mailboxes(self) -> bool:
        """Whether this deployment can actually send a person an email.

        Read by `/setup` and the operations dashboard rather than inferred from
        "is SMTP configured", because the answer has to include *console*, which
        is configured, works, and reaches nobody.
        """
        return self.mail_transport in DELIVERING_MAIL_TRANSPORTS

    @model_validator(mode="after")
    def _fold_before_the_window_drops(self) -> "Settings":
        """The summary has to be written before the window can lose what it covers."""
        if (
            self.ai_context_token_budget > 0
            and self.ai_summarise_after_tokens >= self.ai_context_token_budget
        ):
            raise ValueError(
                f"AI_SUMMARISE_AFTER_TOKENS ({self.ai_summarise_after_tokens}) must be below "
                f"AI_CONTEXT_TOKEN_BUDGET ({self.ai_context_token_budget}): the window would "
                "drop messages before they were ever folded into the summary. Lower the "
                "first, raise the second, or set AI_CONTEXT_TOKEN_BUDGET=0 to count messages only."
            )
        return self

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
        if not [origin for origin in self.cors_origins if origin.strip()]:
            # An empty list is not "no cross-origin access": it is a deployment
            # whose own frontend cannot reach it, which presents as every request
            # failing in the browser with nothing in the server log. Refusing at
            # startup turns a confusing outage into a sentence.
            problems.append(
                "CORS_ORIGINS is empty, so no browser origin can call this API — "
                "list the frontend's exact origin"
            )
        if any(origin.startswith("http://") for origin in self.cors_origins):
            problems.append(
                f"CORS_ORIGINS contains a plaintext http:// origin: {self.cors_origins}"
            )
        if self.web_concurrency > 1 and not (self.redis_url or "").strip():
            # The same failure class as the mail transport below: every component reports
            # success. Each worker has its own in-process limiter, so a limit of ten a
            # minute is ten a minute *per worker* -- N times the configured number, with no
            # error and nothing in the log but one warning at first use. The desktop app is
            # one process and is not affected: this only bites a deployment that asked for
            # more than one.
            problems.append(
                f"WEB_CONCURRENCY is {self.web_concurrency} but REDIS_URL is not set, so each "
                "worker would count its own rate-limit budget and every limit would really be "
                f"{self.web_concurrency} times what is configured. Set REDIS_URL, or run one worker"
            )
        if self.mail_transport not in DELIVERING_MAIL_TRANSPORTS:
            # The failure this refuses is quiet and total: on the console
            # transport `/auth/password-reset-request` still returns 204, the
            # frontend still says "check your email", and the token goes to a
            # log file. Every component reports success and the user is locked
            # out with nothing to retry. Same class as SECRET_KEY=changeme, so
            # it gets the same answer -- the process does not start.
            problems.append(
                f"MAIL_TRANSPORT is {self.mail_transport!r}, which delivers to nobody — "
                "password resets, verification links and invitations would be written to "
                "the log. Set MAIL_TRANSPORT=smtp and SMTP_HOST"
            )
        elif not self.smtp_host.strip():
            problems.append("SMTP_HOST is empty, so the smtp transport has nowhere to connect")
        unpriced = self.unpriced_cost_budget()
        if unpriced:
            # A cost budget with no price for the model that spends it is a budget
            # that never trips: the ledger would record an unknown cost for every
            # call and the cap would read 0 forever. Same class as the in-memory
            # rate limiter -- configured, working, and doing nothing.
            problems.append(unpriced)
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

    def unpriced_cost_budget(self) -> str | None:
        """A sentence when a cost budget is set that no call can ever count against.

        Returned rather than raised so one rule serves both audiences: production
        refuses to boot on it (`_harden_production`) and a development machine is
        told once at startup (`insecure_defaults`). Matched case-insensitively,
        the way `app.ai.pricing.price_for` matches, so the two cannot disagree.
        """
        budgets = (
            self.ai_daily_cost_budget_usd,
            self.ai_org_daily_cost_budget_usd,
            self.ai_org_monthly_cost_budget_usd,
        )
        if not any(budget > 0 for budget in budgets):
            return None
        if self.ai_model.lower() in {name.lower() for name in self.ai_prices}:
            return None
        return (
            f"a cost budget is set but AI_PRICES has no entry for AI_MODEL {self.ai_model!r}, "
            "so no call can ever count against it and it will never trip -- add "
            '{"<model>": {"input": ..., "output": ...}} in US dollars per million tokens'
        )

    def insecure_defaults(self) -> list[str]:
        """Development-grade settings that would be refused in production.

        Returned rather than raised, because a developer machine must still
        start — but returned rather than ignored, because the reason
        `SECRET_KEY` sat at "changeme" long enough to become a documented
        landmine is that nothing ever said so out loud. `main.py` logs these at
        startup; production refuses them outright in `_harden_production`.
        """
        found = []
        if self.secret_key == INSECURE_SECRET_KEY:
            found.append(
                "SECRET_KEY is the public default 'changeme' — every token this "
                "process signs can be forged by anyone with a copy of the source. "
                'Set one: python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        elif len(self.secret_key) < MIN_SECRET_KEY_LENGTH:
            found.append(f"SECRET_KEY is shorter than {MIN_SECRET_KEY_LENGTH} characters")
        unpriced = self.unpriced_cost_budget()
        if unpriced:
            found.append(unpriced)
        return found

    @property
    def media_staging_dir(self) -> Path:
        """Where in-progress chunked uploads accumulate before assembly."""
        return self.media_root / "_staging"

    @property
    def knowledge_index_dir(self) -> Path:
        """Where the built reference index lives. Rebuildable, never edited."""
        return self.knowledge_root / "index"

    @property
    def knowledge_source_dirs(self) -> list[Path]:
        """Directories scanned for reference documents, in priority order.

        Both roots are walked recursively (`discover_sources` uses `rglob`), and
        between them they cover every layout this corpus has had. The manuals
        currently sit directly in `data/bm25/`, which is reached through the
        second root; `data/bm25/sources/` is the tidier home for anything added
        later, and organising it by workbench or language is fine because it is
        walked too. Either location works and nothing has to be moved.

        Scanning the parent means a stray `.md` or `.txt` dropped anywhere under
        `data/` is reference material as far as the index is concerned. That is
        deliberate -- the corpus is meant to be extended by copying a file in --
        but it is why `index/` and the corpus README are excluded explicitly:
        the first is the build's own output and the second is a note about the
        manuals rather than one of them.

        Duplicates are impossible: `discover_sources` de-duplicates on the
        resolved path, so a file under both roots is indexed once.
        """
        return [self.knowledge_root / "sources", self.knowledge_root.parent]

    @property
    def knowledge_exclude(self) -> list[Path]:
        """Paths under a scanned root that are not reference material.

        `data/verify/` holds verification artefacts — recorded benchmark runs,
        solver-corpus baselines, and a conda lock that happens to be a `.txt`.
        That lock is what made this necessary (2026-09-14): scanning `data/`
        picked it up as a 26th manual, so the agent would have been offered 74
        package URLs as CAD documentation, and the index read as stale until it
        was rebuilt around them.
        """
        return [self.knowledge_root.parent / "verify"]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
