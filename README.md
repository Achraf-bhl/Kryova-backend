# Kryova — backend

FastAPI backend for Kryova, an AI-native CAD + FEA platform. See [KRYOVA_PRD.md](KRYOVA_PRD.md)
for product scope.

> Import geometry, describe your load case in plain language, get a real FEA result and an
> AI-proposed path to a better design — all in a browser.

## Status

The physics pipeline works end to end: upload a STEP/IGES/STL file, queue a simulation, and
get back stress, displacement, factor of safety, and a viewer-ready result surface. On a
20×20×60 mm bar this runs in well under a second.

Not yet built: DFM checks, the AI layer, PDF reports, and the frontend. See
[Roadmap](#roadmap).

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
venv\Scripts\activate            # macOS/Linux: source venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env             # then paste your Neon URL and set SECRET_KEY
alembic upgrade head
uvicorn app.main:app --reload    # with --reload, also set INLINE_JOBS=true
```

Interactive API docs: <http://127.0.0.1:8000/docs>

Paste the Neon connection string exactly as the console gives it — the app rewrites
`postgresql://` onto the psycopg 3 driver itself. Use the **pooled** (`-pooler`) endpoint.
SQLite is refused at startup rather than silently accepted.

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

### Accuracy

Tet4 elements are constant-strain and therefore stiff in bending — accuracy on
bending-dominated parts comes from refining the mesh, and tet10 elements are the real fix.
The solver is verified against closed-form solutions rather than recorded output: a bar in
pure tension reproduces σ = F/A and δ = FL/AE to 1e-6, including through a real gmsh mesh.
`tests/test_solver.py` also checks Poisson contraction, load linearity, modulus scaling, and
that ill-posed models are refused.

An under-constrained model is caught by the equilibrium residual, not by looking for NaNs —
SuperLU will happily return a finite, meaningless vector for a singular system.

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

## Roadmap

Next, in order:

1. `dfm/` — rule-based manufacturability checks (wall thickness, sharp internal corners,
   draft angle). Rule-based first: faster to ship and easier to trust than ML.
2. `ai/` — thin LLM orchestration with tool-calling into the modules above: infer a load case
   from a plain-language description, summarise results, propose geometry changes. Its
   document chunks and embeddings go through the existing vector index. It must not own
   geometry logic.
3. PDF report export.
4. Frontend: three.js viewer over `/surface`, load-case editor, chat panel.
5. tet10 elements, so bending-dominated parts are accurate without brute-force refinement.
6. A garbage-collection sweep for unreferenced blobs and expired upload sessions
   (`MediaService.sweep_expired_uploads` exists; nothing schedules it yet).
