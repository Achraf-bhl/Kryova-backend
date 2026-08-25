# Kryova Backend

FastAPI service for an AI-native CAD + FEA platform: upload geometry → mesh → linear-static
FEA → viewer-ready results. Frontend is a **separate repo** (`../Kryova-frontend`, own
CLAUDE.md). Product scope lives in [KRYOVA_PRD.md](KRYOVA_PRD.md); honest current state and
the gap to a shippable product live in [KRYOVA_STATE_OF_THE_PROJECT.md](KRYOVA_STATE_OF_THE_PROJECT.md).

## Stack

Python 3.14 · FastAPI 0.141 · SQLAlchemy 2.0 (sync) + psycopg 3 · Alembic · Pydantic v2 +
pydantic-settings · numpy/scipy (FEA) · gmsh 4.15 (meshing) · faiss-cpu (vector indexes,
storage only — no consumer yet) · bcrypt + python-jose (auth) · Neon Postgres

## Commands

```
Install:      python -m venv venv && source venv/bin/activate && pip install -r requirements-dev.txt
Migrate:      alembic upgrade head
Dev server:   uvicorn app.main:app --reload      # with --reload also set INLINE_JOBS=true
Offline test: pytest tests/test_solver.py tests/test_mesh.py tests/test_geometry.py   # ~0.6s, no DB
Full test:    pytest                              # needs a live Neon connection, ~4 min
Drift check:  alembic check                       # fails if models diverged from migrations
New revision: alembic revision --autogenerate -m "..."
```

**There is no venv checked out on this machine and the system Python has none of the
dependencies** — create one before claiming any backend test result. `numpy 1.26 / scipy 1.11
/ SQLAlchemy 1.4` in the system site-packages are the wrong versions and are not the project's.

**There is no linter, formatter, type checker, or CI.** No `pyproject.toml`, no ruff/black/mypy
config, no `.github/`. Do not tell the user "lint passes" — there is nothing to run. Adding
these is tracked as a priority in the state-of-the-project doc.

## Architecture

Layered and DI-driven: **route → dep (auth/ownership) → service/runner → module → DB**.
Everything is injected via `Depends`; nothing imports a session or a store directly.

```
app/
  main.py         app factory, CORS, lifespan (fails jobs orphaned by a restart), /health
  core/           config.py (pydantic-settings), database.py (engine/session), security.py (bcrypt + JWT)
  models/         SQLAlchemy ORM: User, Project, GeometryVersion, SimulationJob, Media
  schemas/        Pydantic request/response models
  api/            deps.py (auth + ownership guards), rate_limit.py, routes/<domain>.py, router.py
  media/          content-addressed local blob store, chunked/resumable uploads, FAISS indexes
  geometry/       format detection, dependency-free file inspection
  mesh/           gmsh tet meshing, quality metrics, exact primitives for tests
  solve/          Solver interface, linear-static tet4 FEA, materials, region selectors
  simulation/     runner.py — the geometry → mesh → solve job pipeline
  jobs/           JobQueue interface; ThreadPoolJobQueue today, Celery/RQ later
migrations/       Alembic (versions/ is the only migration path)
tests/            pytest, mirrors app/
```

### Three seams exist on purpose — respect them

- **`solve.Solver`** (ABC) — mesh in, load case in, fields out. A surrogate/neural solver must
  drop in without the API, job, or (future) AI layer knowing which ran.
- **`jobs.JobQueue`** (ABC) — one method, `submit`. Moving to Celery must not touch routes.
- **`media.LocalMediaStore`** — content addressing + chunked IO behind a small surface, so an
  S3 store is a swap, not a rewrite.

Never reach around a seam. If a route needs to know which solver ran, put it on the job row.

## Non-negotiable rules

These are facts that cannot be inferred by reading a single file.

**Units are mm-N-MPa everywhere, and nothing in the codebase converts.**
Length/displacement mm · force N · Young's modulus and stress MPa · density kg/m³ · **mass
output is already kilograms**. CAD files are read in their own coordinates and assumed
millimetres. Any new quantity must land in this system at the boundary, not deeper in.

**Never `SET` session state against the pooled Neon endpoint.** The `-pooler` host is PgBouncer
in transaction-pooling mode: a `SET search_path` (or timezone, or anything) survives on the
shared backend connection and is handed to the next client. This has already happened here —
a running server started resolving its tables into the test schema. Every table reference is
compiled schema-qualified via `execution_options={"schema_translate_map": {None: settings.db_schema}}`
(`app/core/database.py`), and the test fixtures do the same for `kryova_test`. Do not
"simplify" this to `search_path`.

**Cross-user access returns 404, not 403** (`get_owned_project`, `_get_job`) so ids cannot be
enumerated across accounts. Keep it that way on every new resource.

**Background jobs own their own session.** They outlive the request, whose session is closed
the moment the response is sent — use `SessionScopeDep`/`get_session_scope`, never the request
session. Commit the job row *before* `queue.submit`, because the worker looks it up by id in a
different session.

**Gmsh is a process-global, non-thread-safe singleton.** Meshing serialises on a module lock in
`app/mesh/gmsh_mesher.py`, and gmsh is initialised with `interruptible=False` — otherwise it
installs a SIGINT handler that raises off the main thread, and meshing never runs on the main
thread.

**Gmsh picks its reader from the file extension, and blobs are named by SHA-256 with no
extension**, so `gmsh_mesher.py` stages them (hard link where the bytes need no change). It
also rewrites the 80-byte STL comment header when needed: gmsh's STL sniffer skips lines
starting with NUL, so a valid binary STL with the conventional zeroed header and no `0x0A`
byte anywhere is rejected with a bare "Error loading". **The stored blob is never modified.**

**An under-constrained model is caught by the equilibrium residual, not by looking for NaNs.**
SuperLU returns a finite, meaningless vector for a singular system. Do not replace
`_residual_is_small` with a finiteness check.

**Loads are distributed by tributary area** (`solve/selection.distribute_force`), so refining
the mesh does not change the applied load. Region selection is by geometric selector
(`{"type":"face","axis":"z","side":"min"}` / `{"type":"box",...}`), never by face id — face ids
are meaningless across a re-export.

**Every heavy byte goes through `app/media/`.** Nothing loads a whole file into memory —
not writing, not hashing, not serving. Blobs are content-addressed (SHA-256, sharded
`blobs/ab/cd/…`), so two records can share one blob: deletion goes through `MediaService`,
which drops the file only once nothing references it. Small metadata rows go to Neon; a
400 MB STEP file never crosses the network.

**Migrations** live only in `migrations/versions/`. Run `alembic check` before finishing any
model change.

## Known landmines in the current code

Read these before touching the relevant file — they are live defects, not style opinions.

- **`solve/linear_static.py` tet10 support is dead and broken.** The module docstring claims
  tet10 elements "are selected automatically when the mesh provides midside nodes". They are
  not: `LinearStaticSolver.solve` only ever calls `assemble_stiffness` (tet4), and
  `_recover_stress` is tet4-only. `assemble_stiffness_tet10` has no callers and would raise on
  the leftover `# placeholder` einsum at line 172 (`"ni,nik->nki"` requires `n_elem == 10`).
  Either wire it up properly with tests, or delete it — do not leave the docstring claiming a
  capability the solver does not have.
- **`SECRET_KEY` defaults to `"changeme"`** (`core/config.py`) and nothing refuses to start on
  it. Any deployment that forgets the env var signs JWTs with a public constant.
- **The rate limiter trusts `X-Forwarded-For` unconditionally** (`api/routes/auth.py::_client_ip`).
  A client that sets a random XFF per request has no rate limit at all. It is also in-process,
  so it does nothing across workers.
- **No list endpoint paginates.** `/projects`, `/projects/{id}/geometry`,
  `/projects/{id}/simulations`, `/media` all return everything.
- **The README and `.env.example` claim SQLite is refused at startup.** It is not —
  `Settings._require_postgres` returns SQLite URLs unchanged and `database.py` has a full
  SQLite branch. Fix the docs or the code, but do not trust either in isolation.
- **`/health` returns `{"status":"ok"}` unconditionally** — it does not check the database, so
  it cannot be used as a readiness probe.
- **`data/` holds 266 MB of committed Dassault Systèmes / CATIA training PDFs.** They bloat
  every clone and are third-party copyrighted material in a repo carrying its own LICENSE.
  Do not add more; raise removal with the user before shipping publicly.

## Testing

- Physics tests (`test_solver.py`, `test_mesh.py`, `test_geometry.py`) never request a database
  fixture, so they open no connection and run offline in under a second. Keep it that way —
  that tight loop is the reason meshing/FEA work is bearable.
- Everything else runs against **the same Neon database in a `kryova_test` schema**, created
  and dropped per run. Testing on SQLite while shipping on Postgres is the drift that hides
  JSONB, enum and cascade bugs. The cost is real: ~250 ms per round trip, ~4 minutes for the
  DB suite. The fixtures minimise round trips — one connection per session, isolation by
  transaction rollback, no app lifespan per test. If it gets painful, the fix is a local
  Postgres, not a return to SQLite.
- The test schema is selected with `schema_translate_map`, never `SET search_path` (see above).
- `client` fixture overrides the job queue to `InlineJobQueue` so jobs run on the request
  thread inside the test's open transaction — a worker thread would use its own connection and
  see none of the uncommitted data.
- The solver is verified against **closed-form solutions**, not recorded output: a bar in pure
  tension reproduces σ = F/A and δ = FL/AE to 1e-6, including through a real gmsh mesh.
  Any new solver work must be verified the same way.

## Conventions

- Files `snake_case` · classes `PascalCase` · functions/vars `snake_case` · constants `UPPER_SNAKE`
- Routes: `api/routes/<domain>.py` exposing `router = APIRouter(prefix=..., tags=[...])`,
  wired in `api/router.py` — an unwired router is invisible everywhere, including `/docs`
- Schemas: `<Resource>Create` / `<Resource>Update` / `<Resource>Read`
- Errors: raise `HTTPException` with a **human-readable, actionable** `detail`. The existing
  messages tell the user what to do ("Increase element_size_mm to coarsen it", "Check that the
  fixtures remove all six rigid-body motions") — match that register, do not degrade to
  "Invalid input".
- Expected, explainable failures (`MeshError`, `SolverError`, `ValueError`) are recorded on the
  job row with their message; anything else is logged with a stack trace and recorded as
  "Unexpected solver failure".

## Do not

- Don't convert units anywhere — the whole codebase is mm-N-MPa
- Don't use `SET search_path`, or any session-level `SET`, against the pooled endpoint
- Don't borrow the request session in a background job
- Don't call gmsh off the module lock, or modify a stored blob in place
- Don't return unpaginated collections in new endpoints (the existing ones are a known debt,
  not a pattern to copy)
- Don't add migrations outside `migrations/versions/`
- Don't return 403 for another user's resource — 404
- Don't claim a capability in a docstring or README that the code does not have (this has
  already happened twice — tet10 and the SQLite refusal)
