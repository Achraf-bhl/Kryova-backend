# Kryova — backend

FastAPI backend for Kryova, an AI-native CAD + FEA platform. See [KRYOVA_PRD.md](KRYOVA_PRD.md)
for product scope.

> Import geometry, describe your load case in plain language, get a real FEA result and an
> AI-proposed path to a better design — all in a browser.

## Status

**Working end to end.** Upload a STEP/IGES/STL file, queue a simulation, and get back stress,
displacement, factor of safety, and a viewer-ready result surface. On a 20×20×60 mm bar this
runs in well under a second. Auth, projects, immutable geometry versions, resumable chunked
uploads and content-addressed storage are all in place.

**Not built yet** — and these are the parts the product thesis actually rests on:

| | State |
|---|---|
| `ai/` — load-case inference, result summaries, geometry proposals | not started |
| `dfm/` — manufacturability checks | not started |
| PDF report export | not started |
| Analysis types beyond linear static (modal, thermal, nonlinear, contact) | not started |
| Tet10 elements | partially written, **not wired up and currently broken** — see [Accuracy](#accuracy) |
| CI, linting, type checking, container image, deploy | none |

A frontend exists in the sibling repo (`../Kryova-frontend`) covering sign-in, upload, load-case
setup and a WebGL stress viewer.

For a candid assessment of where this sits against SimScale, Onshape, Ansys Discovery and the
AI-native entrants — and an ordered plan to close the gap — see
[KRYOVA_STATE_OF_THE_PROJECT.md](KRYOVA_STATE_OF_THE_PROJECT.md).

## Where things live

Two stores, on purpose:

| | Where | What |
|---|---|---|
| **Cloud** | Neon Postgres | Users, projects, geometry versions, simulation jobs, media metadata — small rows only |
| **Local** | this machine's disk | Every heavy file: CAD uploads, volume meshes, result fields, FAISS indexes |

A 400 MB STEP file never crosses the network to Neon. The database holds a `media` row
naming its SHA-256; the bytes sit in `MEDIA_ROOT`. This keeps cloud storage costs and query
latency flat no matter how large the parts get.

## Setup

```bash
python -m venv venv
source venv/bin/activate         # Windows: venv\Scripts\activate
pip install -r requirements-dev.txt

cp .env.example .env             # then paste your Neon URL and set SECRET_KEY
alembic upgrade head
uvicorn app.main:app --reload    # with --reload, also set INLINE_JOBS=true
```

Interactive API docs: <http://127.0.0.1:8000/docs>

Paste the Neon connection string exactly as the console gives it — the app rewrites
`postgresql://` onto the psycopg 3 driver itself. Use the **pooled** (`-pooler`) endpoint.

`SECRET_KEY` **defaults to `changeme` and nothing stops the app booting on it.** Generate a
real one (`python -c "import secrets; print(secrets.token_urlsafe(32))"`) before the service is
reachable by anyone, or every JWT it issues is signed with a public constant.

A SQLite URL is accepted and takes a working code path (`app/core/database.py`), but it is a
local convenience only: the schema, the pooling behaviour and the JSONB columns you actually
ship on are Postgres. Do not treat a green SQLite run as evidence.

Python 3.14 is fine — numpy, scipy, gmsh, psycopg and faiss all ship working wheels for it.

## Testing

```bash
pytest tests/test_solver.py tests/test_mesh.py   # offline, ~0.6s
pytest                                           # everything, needs the network
```

The physics tests never open a database connection, so the tight loop while working on
meshing or FEA stays instant.

Everything else runs against **the same Neon database, in a `kryova_test` schema** that is
created and dropped per run. Testing on SQLite while shipping on Postgres is exactly the
drift that hides JSONB, enum and cascade bugs until production, so the suite pays the network
cost instead.

That cost is real and worth knowing: Neon in `eu-west-2` is **~250 ms per round trip** from
here, so the database-backed suite takes about four minutes. The fixtures are built to spend
as few round trips as possible — one connection for the whole session, isolation by
transaction rollback rather than rebuilding the schema, and no application lifespan per test.
If the loop gets painful, the fix is a local Postgres for development, not a return to
SQLite.

The test schema is selected with `schema_translate_map`, never `SET search_path` — see the
pooling note under [Operational notes](#operational-notes) for why that distinction is not
cosmetic.

```bash
alembic revision --autogenerate -m "..."   # after changing a model
alembic check                              # fails if models have drifted from migrations
```

## Layout

```
app/
  core/         config, SQLAlchemy engine/session, password hashing + JWT
  models/       ORM models (User, Project, GeometryVersion, SimulationJob, Media)
  schemas/      Pydantic request/response models
  api/          deps.py (auth + ownership guards), routes/
  media/        local blob store, chunked uploads, FAISS indexes
  geometry/     format detection and dependency-free file inspection
  mesh/         gmsh tet meshing, quality metrics, exact primitives for tests
  solve/        Solver interface, linear static tet4 FEA, material library
  simulation/   the geometry -> mesh -> solve job runner
  jobs/         JobQueue interface; thread pool today, Celery/RQ later
migrations/     Alembic
tests/
```

Three seams exist so the heavy parts can be replaced without a rewrite:

- **`solve.Solver`** — mesh in, load case in, fields out. A surrogate or neural solver drops
  in without the API, job, or AI layers knowing which one ran.
- **`jobs.JobQueue`** — one method. Moving to Celery or RQ does not touch the routes.
- **`media.LocalMediaStore`** — content addressing and chunked IO behind a small surface, so
  an S3-backed store is a swap rather than a rewrite.

## The media layer

Everything heavy goes through `app/media/`. Three properties matter:

**Content addressing.** A blob's name *is* its SHA-256, sharded two levels deep
(`blobs/ab/cd/abcd…`). Re-uploading the same part after a failed run costs no extra disk, and
every read is verifiable — `POST /media/{id}/verify` re-hashes the file and says whether it
still matches. Because two records can legitimately share one blob, deletion goes through the
service, which drops the file only once nothing references it.

**Chunked IO, everywhere.** Nothing loads a whole file into memory — not writing, not
hashing, not serving. Downloads stream.

**Resumable uploads.** Large CAD files do not survive being sent as one request. Open a
session, PUT chunks by index in any order, retry any that fail, then complete:

```
POST   /media/uploads                      -> {id, chunk_size, total_chunks}
PUT    /media/uploads/{id}/chunks/{index}  -> progress, incl. missing_chunks
POST   /media/uploads/{id}/complete        -> the assembled media record
POST   /projects/{id}/geometry/attach      -> register it as a geometry version
```

Supply `expected_sha256` at session start and a corrupted transfer is rejected at assembly
rather than discovered later in the mesher.

**Vector indexes** (`app/media/vectors.py`) are FAISS indexes persisted through the same
store, so they get the same addressing, dedup and integrity checking. `LocalVectorIndex`
covers build / add / remove / search / save / load, with cosine (via normalised inner
product) or L2, and caller-supplied int64 ids so vectors can be addressed by their own row
ids rather than insertion order. What gets embedded, and how, belongs to the AI layer when it
lands — this is the storage half only.

## Units

Everything uses the self-consistent **mm-N-MPa** system that mechanical engineers already
work in, so nothing in the codebase converts units:

| Quantity | Unit |
|---|---|
| Length, displacement | mm |
| Force | N |
| Young's modulus, stress | MPa |
| Density | kg/m³ (mass output is kg) |

CAD files are read in their own coordinates and assumed to be millimetres.

## The analysis pipeline

1. **Upload** — streamed to the local store under a size cap, checksummed, and inspected.
   Every upload is a new immutable version; nothing is overwritten.
2. **Mesh** — gmsh fills the solid with linear tetrahedra, reading the blob in place. STL
   triangle soup is classified into surfaces and closed into a volume first; a non-watertight
   file is rejected with a reason rather than silently meshed into nonsense.
3. **Solve** — small-strain isotropic linear static FEA. Loads are given as a total force in
   newtons and spread over the selected surface by tributary area, so refining the mesh does
   not change the applied load.
4. **Store** — the summary goes on the job row in Neon; the full displacement and stress
   fields go to the local store as a `.npz` media blob.

Regions are picked with selectors rather than face ids, which are meaningless across a
re-export:

```json
{"type": "face", "axis": "z", "side": "min", "tolerance": 0.01}
{"type": "box", "min": [0, 0, 0], "max": [10, 10, 5]}
```

A fixture restrains all three translations by default; `"dofs": ["z"]` makes it a roller or a
symmetry plane instead.

### Accuracy — and its limits

The solver is verified against **closed-form solutions** rather than recorded output: a bar in
pure tension reproduces σ = F/A and δ = FL/AE to 1e-6, including through a real gmsh mesh.
`tests/test_solver.py` also checks Poisson contraction, load linearity, modulus scaling, and
that ill-posed models are refused.

An under-constrained model is caught by the equilibrium residual, not by looking for NaNs —
SuperLU will happily return a finite, meaningless vector for a singular system.

Three limits are worth stating plainly, because a factor of safety is a number people make
decisions on:

- **Tet4 elements are constant-strain and stiff in bending.** A bending-dominated part is
  under-predicted for deflection unless the mesh is refined hard. Tet10 is the real fix.
  `assemble_stiffness_tet10` exists in `app/solve/linear_static.py` but **has no callers, is
  not selected automatically despite what its module docstring says, and contains a leftover
  placeholder expression that would raise if it were called.** Treat the solver as tet4-only.
- **No mesh-convergence check and no error estimate.** Nothing tells the user whether the
  answer moved between two mesh densities, so nothing distinguishes a converged result from a
  coarse one.
- **Factor of safety comes from the single peak element**, with no singularity handling. At a
  re-entrant corner, peak stress rises without bound as the mesh refines, so the reported FoS
  there is a mesh artefact rather than a property of the part.

## API

All routes are under `/api/v1` and require a bearer token except `register`, `login`,
`materials`, and `/health`.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/auth/register` | Create an account |
| `POST` | `/auth/login` | OAuth2 password form → JWT |
| `GET` | `/auth/me` | Current user |
| `GET` `POST` | `/projects` | List / create projects |
| `GET` `PATCH` `DELETE` | `/projects/{id}` | Read / update / delete a project |
| `POST` | `/projects/{id}/geometry` | Upload a CAD file as a new version |
| `POST` | `/projects/{id}/geometry/attach` | Register an already-uploaded blob as a version |
| `GET` | `/projects/{id}/geometry` | List versions, newest first |
| `GET` | `/projects/{id}/geometry/{n}` | Version metadata |
| `GET` | `/projects/{id}/geometry/{n}/download` | Original bytes, streamed |
| `POST` | `/projects/{id}/simulations` | Queue a run → `202` with a job to poll |
| `GET` | `/projects/{id}/simulations` | List runs, newest first |
| `GET` | `/projects/{id}/simulations/{sid}` | Job status and result summary |
| `GET` | `/projects/{id}/simulations/{sid}/surface` | Result surface for a 3D viewer |
| `DELETE` | `/projects/{id}/simulations/{sid}` | Delete a finished run |
| `POST` | `/media/uploads` | Open a resumable chunked upload |
| `PUT` | `/media/uploads/{id}/chunks/{n}` | Send one chunk |
| `GET` | `/media/uploads/{id}` | Progress, including missing chunks |
| `POST` | `/media/uploads/{id}/complete` | Assemble and register |
| `DELETE` | `/media/uploads/{id}` | Abort and discard staged chunks |
| `GET` | `/media` `/media/{id}` | List / read media metadata |
| `GET` | `/media/{id}/content` | Download the file, streamed |
| `POST` | `/media/{id}/verify` | Re-hash on disk and confirm integrity |
| `DELETE` | `/media/{id}` | Delete a record, and the blob if now unreferenced |
| `GET` | `/materials` `/materials/{name}` | Built-in material library |

Accepted geometry: `.step` `.stp` `.iges` `.igs` `.stl`. Malformed files are rejected at
upload with a 422 rather than failing later in the mesher.

Projects, simulations and media belonging to another user return **404, not 403**, so ids
cannot be enumerated across accounts.

The `/surface` payload carries only boundary nodes and triangles — the interior of a volume
mesh is never drawn, and shipping it would multiply the payload for nothing.

### Example

```jsonc
POST /api/v1/projects/{id}/simulations
{
  "element_size_mm": 4.0,
  "load_case": {
    "name": "8 kN axial pull",
    "material": {"name": "aluminium-6061-t6", "youngs_modulus_mpa": 68900,
                 "poissons_ratio": 0.33, "yield_strength_mpa": 276, "density_kg_m3": 2700},
    "fixtures": [{"where": {"type": "face", "axis": "z", "side": "min"}}],
    "loads": [{"where": {"type": "face", "axis": "z", "side": "max"},
               "force_n": [0, 0, 8000]}]
  }
}
```

## Operational notes

- **Jobs** run on an in-process thread pool. That does not survive a restart, so any job left
  `queued` or `running` is failed at startup with a message telling the user to re-run it,
  rather than being polled forever.
- **Gmsh is a process-global singleton** and is not thread-safe, so meshing serialises on a
  module lock. It is also initialised with `interruptible=False`: gmsh otherwise installs a
  SIGINT handler, which raises off the main thread — and meshing never runs on the main
  thread.
- **Gmsh is fussy about how a file is presented**, and blobs are named by their SHA-256 with
  no extension, so `app/mesh/gmsh_mesher.py` stages them. Two things it works around: gmsh
  picks its reader from the file extension, and its STL sniffer skips lines starting with a
  NUL — so a valid binary STL with the conventional zeroed 80-byte header and no `0x0A` byte
  anywhere is rejected with a bare "Error loading". That happens for real parts whose
  coordinates pack NUL-heavy (exact powers of two). Staging uses a hard link where the bytes
  need no change, and only rewrites the 80-byte comment header when it must. The stored blob
  is never modified.
- **Neon drops idle connections**, so the engine uses `pool_pre_ping` and recycles below the
  idle timeout. Note that pre-ping costs one extra round trip per checkout.
- **Never `SET` session state against the pooled endpoint.** Neon's `-pooler` host is
  PgBouncer in transaction-pooling mode, which does not reset session state between clients:
  a `SET search_path` (or `SET timezone`, or anything else) stays on the shared backend
  connection and is handed to whoever gets it next. This is not theoretical — it happened
  here, and a running server started resolving its tables into the test schema. The app
  therefore compiles every table reference schema-qualified via `schema_translate_map`
  (`DB_SCHEMA`, default `public`) and never relies on `search_path`; the test fixtures do the
  same for `kryova_test`. If you prefer to avoid the class of problem entirely, use Neon's
  direct (non-pooled) endpoint — SQLAlchemy already pools client-side.
- **`MAX_ELEMENTS`** caps mesh size so a single upload cannot consume the machine.
- **Media lives on one machine.** That is the point, but it means the disk under `MEDIA_ROOT`
  is not redundant — back it up, or move to the S3-backed store before this is more than a
  single node.

## Development tooling

There is deliberately little, and you should know what is missing before trusting a green run:

| | State |
|---|---|
| Tests | `pytest` — 9 files. Physics offline, everything else needs live Neon |
| Linter / formatter | **none** — no ruff, black, or `pyproject.toml` |
| Type checker | **none** — the code is annotated throughout but nothing checks it |
| CI | **none** — no `.github/`; nothing runs on push |
| Container / deploy | **none** — no Dockerfile, no deployment config |
| Observability | stdlib `logging` only — no structured logs, metrics, or error tracking |

`/health` returns `{"status":"ok"}` unconditionally and does not touch the database, so it is a
liveness probe, not a readiness one.

## Repository note

`data/` holds 266 MB of Dassault Systèmes and CATIA training PDFs, tracked in git. They are
third-party copyrighted material and they are in every clone of a repo that ships its own
LICENSE. They are reference reading, not a build input — resolve their status before this
repository is published.

## Roadmap

Ordered by what unblocks the most. The first three are not features; they are the difference
between a demo and something that can be deployed.

1. **Make it safe to run.** Refuse to boot on `SECRET_KEY=changeme`; stop trusting a
   client-supplied `X-Forwarded-For` for rate-limit identity; paginate every list endpoint.
2. **Make it enforceable.** ruff + mypy + a `pyproject.toml`, and a CI workflow running the
   offline physics tests on every push. The DB suite needs an ephemeral Postgres service
   container rather than shared Neon before it can run in CI.
3. **Make it deployable.** Dockerfile, a `/ready` probe that checks the database, structured
   logging with request ids.
4. **`dfm/`** — rule-based manufacturability checks (wall thickness, sharp internal corners,
   draft angle). Rule-based first: faster to ship and easier to trust than ML.
5. **`ai/`** — thin LLM orchestration with tool-calling into the modules above: infer a load
   case from a plain-language description, summarise results, propose geometry changes. Its
   document chunks and embeddings go through the existing vector index. It must not own
   geometry logic.
6. **PDF report export.**
7. **Tet10 elements** — finish or delete the half-written implementation, and add a mesh
   convergence indicator alongside it so a factor of safety comes with a confidence.
8. **Modal analysis**, then thermal. These are the two analysis types users ask for first once
   linear static works.
9. A garbage-collection sweep for unreferenced blobs and expired upload sessions
   (`MediaService.sweep_expired_uploads` exists; nothing schedules it yet).
