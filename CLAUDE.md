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

**Lint and type-check exist now** (this section used to say they did not):
`pyproject.toml` configures ruff (`E,F,I`, line length 100) and mypy (`python_version = "3.12"`,
`mypy_path = "scripts"`), and `.github/` has workflows. Run
`venv/bin/python -m ruff check app/ tests/` and `venv/bin/python -m mypy app/` before finishing.
Two **pre-existing** mypy errors in `app/catia/local_bridge.py` (lines 307/309, `Popen | None`
union-attr) are not yours — leave them or fix them deliberately, but do not be surprised by them.

**Setup and update are one script**, `scripts/setup.sh` (bash) and `scripts/setup.ps1`
(PowerShell — **the product ships on Windows**, so that one is the path that matters). Every
step is idempotent, so re-running *is* the update: the venv is reused, pip installs only what
changed, Alembic applies only new migrations, and the reference index rebuilds only when the
documents actually changed. There is deliberately no separate update script.

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
  retrieval/      the reference manuals the agent consults (see below)
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

## Reference manuals (`app/retrieval/`)

The agent can consult the CATIA and FEA documentation held on this machine instead of
answering about CATIA from memory, via one tool: `search_documentation`.

**It is lexical (BM25), not embeddings, and that was a decision rather than a shortcut.**
Three things about this deployment force it, all pointing the same way. The provider is
pluggable and defaults to local Ollama with no key — and **Anthropic publishes no embedding
model at all**, so a dense index would either drag a second vendor into a deployment that
deliberately chose one, or force an extra model pull onto an install whose point is that it
runs offline. The corpus is technical manuals, which is the regime where lexical retrieval is
strongest: what discriminates between passages here is exact terms (`M6`, `Ø12`, `tet4`,
`V5R21`, `Multi-sections Solid`) — precisely what embeddings blur. And the manuals are
bilingual, where accent folding does for free what no embedding quality gives you.
The honest caveat, worth keeping in mind before anyone "upgrades" this: on open-domain prose
hybrid lexical+dense beats either alone. `Corpus.search` sits behind a small surface so a
dense stage can be fused in later without the agent or tool layer knowing.

Layers, each testable alone: `analyze` (bilingual, jargon-preserving tokenizer) → `extract`
(PDF→pages, fallback chain poppler → pypdf → pdfminer) → `chunking` (pages→passages, headings
carried) → `bm25` (scorer over flat numpy arrays) → `corpus` (build/load/search) → `service`
(process-wide handle) → `language` (per-passage detection + preference).

Things that will bite you:

- **`KnowledgeService.search` cannot raise.** Missing index, corrupt index, wrong format
  version, disk gone — all return `[]`, logged once. Consulting the manuals improves an
  answer and must never be why there is not one. Keep it that way.
- **The tool and the prompt are gated on the index actually existing**, not on the setting.
  `_build_knowledge` withholds the tool and `system_prompt()` picks a variant without the
  documentation section. A prompt describing a tool the model was not given teaches it to
  hallucinate a call. There are **four** frozen system prompts for this reason (CATIA × docs).
- **Never name the mechanism in user-facing text.** The step label is "Checking the
  documentation"; the prompt tells the model to cite the document and page and *not* to
  narrate the lookup. `tests/test_retrieval.py` and the prompt-cleanliness check exist because
  "I searched my knowledge base" is a worse answer than the answer.
- **Heading detection is the most tuned code in `chunking.py`.** These manuals are almost
  entirely numbered procedures, so a bare `\d+\.` pattern labels thousands of instruction
  steps as section titles. Only keyword (`Chapter 4`) or multi-level (`3.2`) numbers count,
  and the terminal-punctuation test runs *before* the numbered test. Both orderings are
  load-bearing and both have already been got wrong once.
- **Language is a boost, never a filter** (`LANGUAGE_PREFERENCE_BOOST`). CATIA's menus are
  translated, so a French user needs the French page — but a workbench documented only in
  English must still answer them. A clearly better match in the other language still wins.
- **Every considered file is fingerprinted, including skipped ones.** Recording only successes
  makes `is_stale` permanently true on any corpus containing one unreadable PDF.
- Builds are atomic (staging directory, swapped in), so rebuilding under a live server is safe.

```
python -m app.retrieval.build            # build or rebuild
python -m app.retrieval.build --check    # exits non-zero when a rebuild is needed
python -m app.retrieval.build --query X  # see what the agent would find
```

## CATIA V5 reference (`app/catia_kb/`)

The other half of the CATIA answer, and it does a different job from the corpus above. The
corpus knows what page 147 of the Part Design manual *says*; this knows that Edge Fillet is
in Part Design's Dress-Up Features toolbar at `Insert > Dress-Up Features > Edge Fillet`,
that it needs P1, that the French interface calls it `Congé d'arête` and the German one
`Kantenverrundung`, that it fails when the radius exceeds the narrowest adjacent face *on the
propagated tangent chain* rather than the edge you clicked, and that Tritangent Fillet is the
alternative when a whole face should disappear. ~1,600 entries: workbenches, commands, dialog
fields, file formats, `Tools > Options` settings, error messages, aerospace vocabulary,
workflows, methodology, the automation object model, and the V5R19 product trigram table.

It ships **in the code, not in an index**, so unlike the manuals it is always present. That is
why there are still four frozen system prompts and not eight — the domain section is
unconditional.

Three consumers, in order of how much they earn:

1. **Query expansion** (`recognise.expand_query`, wired into `KnowledgeService.search`). Half
   the corpus is French; without this, half of it is unreachable from an English question. A
   query for "draft angle" gets `dépouille` added before it hits BM25.
2. **`explain_catia_term`** — the lookup tool. Returns *fields*, where `search_documentation`
   returns prose, so the model states a menu path it was handed rather than one it recalls.
3. **The per-turn brief** (`state.py`) — a few lines beside the user's message naming what
   their words refer to. This is what makes a small local model get the workbench right
   without having to decide to call a tool.

Things that will bite you:

- **Precision is the hard half, not recall.** This vocabulary contains `fit`, `add`, `part`,
  `box`, `web` and `pip`. Two disjoint tiers in `recognise.py` keep them from firing:
  `NEVER_BARE` never matches alone (ordinary English that collides with a CATIA name);
  `AMBIGUOUS_WORDS` needs corroboration from something unmistakable elsewhere in the message.
  Distinctive words — `pocket`, `fillet`, `joggle`, `sketch` — are in **neither**, because
  "how do I make a pocket" carries no other signal and must still work. `TestPrecision` is the
  set of sentences that must produce nothing; add to it before widening any alias.
- **Product codes need their capitals when they collide** (`PIP`/`pip`, `FIT`, `GAS`, `EST`,
  `CUT`). Codes with no collision (`GSD`, `ASL`, `CPD`) match either way.
- **Fuzzy matching only fires once something matched exactly.** Otherwise `document` scores
  0.94 against `Documents` and every English sentence with a long word produces a hit.
- **Expansion and the coverage floor are in direct conflict** — this shipped as a bug once.
  Expansion adds *synonyms*, and a passage matches the English name or the French one, never
  both, so a floor computed over the expanded query demands breadth no passage can have.
  `Corpus.search(..., coverage_query=)` measures the floor against the user's original query.
  Never remove that argument.
- **A missing translation is reported as missing.** `localised()` returns `None` and the tool
  payload says so in words. Never fall back to the English name presented as the localised
  one — an engineer can work with "I don't have the German name, it's here in the menu", and
  cannot recover from being sent to a menu item that does not exist. Same rule for the
  informal trigrams (`WSF`, `AMT`), which say they are informal.
- **The COM automation API is not localised** (`api.localisation`). `AddNewPad` is
  `AddNewPad` on every language install; only user-typed data (feature names, materials)
  translates. This is why the CATIA bridge works on any seat, and why a macro that looks up
  `"Pad.1"` by string is the one that breaks abroad.
- **`CatiaKnowledge` cannot raise**, same contract as `KnowledgeService`. Every method returns
  empty/unchanged on failure, logged once.
- **Ambiguity is named, not resolved.** SMD vs ASL, GSD vs WSF, GPS vs GAS, generative vs
  interactive Drafting, Geometrical Set vs Body — the `Disambiguation` table forks these and
  the brief prints the fork. Picking a side is how an airframe engineer loses a day.
- Duplicate entry keys raise at import; `missing_cross_references()` and `untranslated()` are
  asserted empty by the tests, which is what catches a rename orphaning a German name.

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
- **`data/` holds ~270 MB of Dassault Systèmes / CATIA training PDFs, and they are tracked
  again on purpose** (2026-09-01) so the corpus syncs to the Windows test workstation with a
  plain `git pull`. They are third-party copyrighted material in a repo carrying its own
  LICENSE, and a later `.gitignore` cannot undo it — removing them needs a history rewrite.
  Raise this before the repository is published or cloned widely. `data/bm25/index/` is *not*
  tracked: it is derived, rewritten whole on every build, and would conflict between machines.
- **Four of the 21 PDFs are scans with no text layer** (the large French `Formation-*` files,
  42–66 MB each) and cannot be indexed without OCR. The build reports them as
  `scanned, no text layer` and carries on; this is expected, not a regression.

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
