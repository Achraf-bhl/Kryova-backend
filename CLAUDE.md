# Kryova Backend

## What this project is for — read this before proposing anything

**The goal is a system an engineer can talk to that designs, analyses, validates and documents a
complete working machine** — a stamping press, a gearbox, a conveyor, a robot arm, a motorcycle
chassis — to a standard a licensed engineer can review, sign, and have manufactured.

Not a chatbot that models a bracket. Not a gear generator. **A machine.**

FastAPI service for an AI-native CAD + FEA platform: upload geometry → mesh → FEA → viewer-ready
results, with an agent that authors geometry through CATIA or an open kernel. The frontend is a
**separate repo** (`../Kryova-frontend`, own CLAUDE.md).

## The three documents that run this project

Read them in this order when you start a session. They answer different questions and none of
them substitutes for another.

1. **[KRYOVA_MASTER_PLAN.md](KRYOVA_MASTER_PLAN.md)** — *where the project is going and where it
   currently stands.* Eight decisions, then 29 phases (`E1`–`E18` engineering, `P1`–`P10`
   product), each a numbered task list where **every task carries a status line**. This is the
   answer to "where were we?" and it is the file you keep current.
2. **[KRYOVA_BUILD_PLAN.md](KRYOVA_BUILD_PLAN.md)** — *the short-term working queue and the
   history.* One batch at a time, green before the next; its *Done* section is the running log.
   The master plan is current state, the build plan is what happened.
3. **[docs/GUI_PROMPT_LADDER.md](docs/GUI_PROMPT_LADDER.md)** — *how the product is actually
   tested.* 50 prompts across six levels, driven through the real web GUI. When you test the
   whole thing end to end, **you run prompts from this file** — do not invent your own and do not
   test with a tool call.

Supporting: [KRYOVA_CAPABILITY_ROADMAP.md](KRYOVA_CAPABILITY_ROADMAP.md) (the audit the plan grew
out of), [KRYOVA_PRD.md](KRYOVA_PRD.md), [KRYOVA_STATE_OF_THE_PROJECT.md](KRYOVA_STATE_OF_THE_PROJECT.md).

### Keeping the master plan current — this is not optional bookkeeping

**A session that finishes work and leaves the plan unchanged has thrown away the only thing that
lets the next session start.** Rules:

1. **Update the task's status line in the same commit as the work.** Never as a follow-up.
2. Status vocabulary, exactly five: `NOT STARTED` · `IN PROGRESS (date)` ·
   `PARTIAL (date) — what shipped` · `DONE (date) — what shipped` · `BLOCKED — what blocks it`.
   `PARTIAL` and `DONE` end with `Tested by: <files>`.
3. **`DONE` requires a test that proves it.** Name the file. A claim with nothing to open is an
   intention, not a status.
4. When every task in a phase is done, add `> ✅ PHASE COMPLETE (date) — all tasks done and
   tested.` under the phase heading. One open task means no marker, however much has shipped.
5. **Never delete a status; supersede it.** Then append one line to the build plan's *Done*.
6. **You may change the plan itself.** Add a task, split a phase, add a whole phase, or rewrite
   one that turned out to be wrong — when the work teaches you something the plan did not know.
   That is how the four defects recorded in its header got found. Say in the commit message what
   changed and why. What you must not do is quietly leave a task describing something nobody is
   going to build, or mark something done that is not.
7. **Keep this CLAUDE.md current too.** When you learn something that would have saved you an
   hour — a trap, a command, a rule that is not inferable from one file — add it here, and delete
   anything you find that is no longer true. Two claims in this file were false for weeks (a venv
   that did exist, a SQLite refusal that was never implemented), and each one cost a session.

## Where the work runs — Linux writes it, Windows proves it

**This matters more than any other process rule here, and getting it wrong wastes a whole
session.**

**On Linux (this machine — writing code):**

1. Write the tests with the work and commit them together. **Do not skip a test because it will
   be run elsewhere** — the seat runs what exists, so a phase with no tests written is a phase
   that never gets verified.
2. Run `pytest` — the suite is local and fast now (see *Database*). Run `ruff` and `mypy`.
3. Verify every new guard **by breaking the thing it guards** and watching a named test fail.
   Where a guard cannot be shown to fail, label it unpinned rather than shipping it as verified.
4. **There is no CATIA and no GUI run here.** Do not claim an end-to-end result from Linux.
   An integration claim between gates is unproven and is written as unproven.

**On Windows (the machine with CATIA and the bridge — proving the product):**

1. This is where the **whole application** is exercised: the real `/api/v1/ai/chat` endpoint
   through the web GUI, the CATIA seat through the bridge, the desktop shell.
2. **Test with the prompt ladder** (`docs/GUI_PROMPT_LADDER.md`), not with improvised prompts.
   Levels 1–4 measure the product; 5 and 6 measure the distance to it.
3. Two pictures per prompt where CATIA is involved: `catia_capture_view` (the part as CATIA draws
   it, through the product's own tool) **and** a screenshot of the application window (the spec
   tree, any dialog, any greyed command). A run with no picture has not been verified, it has
   been believed.
4. Record the **rung reached**, not just pass/fail, in the ladder's run log, and write a dated
   report in `docs/verification-<date>/`.
5. **An end-to-end test goes through the chatbot, never through the dispatcher.** Calling
   `dispatch` directly tests the tools; it does not test the product, and every defect that has
   mattered here lived between the model and the tools.

### Driving the GUI on that machine — a session has hands on the real product

1. **Account.** `admin@admin.com` / `admin`, created by **`scripts/create_admin.py`**. There is
   deliberately no API that mints staff, so a script through `SessionLocal` is the sanctioned
   route for a `platform_admin` `StaffGrant`. It also *has* to create the account: `UserCreate`
   requires eight characters and `admin` is five, while the login route takes an
   `OAuth2PasswordRequestForm` and imposes no length — so this password can sign in but cannot be
   registered. The script is idempotent (re-running resets a forgotten password) and refuses a
   non-local `DATABASE_URL` without `--i-know`.
2. **Browser.** Edge is launched **once**, by hand, with `--remote-debugging-port=9222` and its
   own persistent `--user-data-dir`; the driver attaches with `playwright-core`'s
   `chromium.connectOverCDP` and runs one batch of actions per invocation. The persistent profile
   is what keeps the login cookie and the open conversation alive between tool calls.
   **Never call `browser.close()` on a CDP-attached browser** — it shuts Edge down and signs the
   session out; exiting the process is the whole of the detach.
3. **Scope every DOM read to `main`.** A document-wide text or element scan returns Next.js's RSC
   `self.__next_f.push` payload and floods the context with megabytes of build output.
4. **The backend spawns its own CATIA bridge.** `app/catia/local_bridge.py` auto-pairs a device
   row named "This workstation" and runs `catia_bridge run --wait-for-catia` with the token
   passed by environment. A hand-paired second daemon cannot start beside it — `bridge.lock` is
   held, one daemon per machine — so pairing by hand is not just unnecessary, it fails.
5. **The model is `qwen3.5:9b`**, configured in `.env.local`. Chosen over `qwen3-coder:30b` on the
   grounds that matter here: the 30b is 22 GB on an 8 GB card, runs 72% on the CPU, and took two
   and a half minutes to answer with a single word — a CATIA build is tens of turns of that. The
   9b returns a correct structured `tool_call` in 8–15 s with the full tool payload, and sits 81%
   on the GPU at `num_ctx=32768` with `OLLAMA_FLASH_ATTENTION=1` and `OLLAMA_KV_CACHE_TYPE=q8_0`.
   Dropping the window to 16k only reaches 86% — the weights are the bulk, not the KV cache — so
   the full window is kept. A truncated prompt is refused loudly by `app/ai/providers/ollama.py`
   and would end a long run.
6. **Confirm Ollama is actually on the GPU before a gate**: `ollama ps` for the CPU/GPU split,
   `nvidia-smi` for resident bytes. A gate run on the CPU measures patience, not the product.

**Why it is batched.** Driving a real conversation through the local model against a real seat is
the only test that has ever found the defects that matter — every one of the seven found on
2026-09-05 was invisible to the offline suite and left the geometry looking plausible. It is also
four to seven minutes per prompt on this hardware. So work runs in **stretches** (pytest only,
Ollama stopped so the card is free) separated by **gates** (the whole product, once, properly).
The gates are listed in the master plan's Part 2.

## Commands

```
Install       python -m venv venv && source venv/bin/activate && pip install -r requirements-dev.txt
Migrate       venv/bin/python -m alembic upgrade head
Drift check   venv/bin/python -m alembic check          # fails if models diverged from migrations
New revision  venv/bin/python -m alembic revision --autogenerate -m "..."
Dev server    venv/bin/uvicorn app.main:app --reload    # with --reload also set INLINE_JOBS=true
Lint          venv/bin/python -m ruff check app/ tests/
Types         venv/bin/python -m mypy app/
Test          venv/bin/python -m pytest                 # whole suite, ~9 min against local Postgres
Offline only  venv/bin/python -m pytest tests/test_solver.py tests/test_mesh.py \
                  tests/test_geometry.py tests/test_kernel.py tests/test_interrogation.py \
                  tests/test_design_*.py tests/test_render.py tests/test_vision.py
```

**Always `venv/bin/python`, never the system one**, whose numpy/scipy/SQLAlchemy are the wrong
versions and are not the project's. **The venv exists** — a session that believed otherwise once
rebuilt the environment for nothing.

**Both ruff and mypy are clean, and there is no list of errors to expect.** A tolerated error is
one nobody reads, so the next real one hides behind it — if either prints anything, it is yours.
`mypy app/` says *Success: no issues found in 343 source files*, with no exceptions, as of
2026-09-08. **Clear `.mypy_cache/` before believing a stale answer**: adding an override to
`pyproject.toml` did not take effect until the cache was removed, which looked exactly like the
override not working.

**Setup and update are one script**, `scripts/setup.sh` (bash) and `scripts/setup.ps1`
(PowerShell — **the product ships on Windows**, so that one is the path that matters). Every step
is idempotent, so re-running *is* the update. There is deliberately no separate update script.

## Repository layout

### Backend (this repo)

Layered and DI-driven: **route → dep (auth/ownership) → service/runner → module → DB**. Everything
is injected via `Depends`; nothing imports a session or a store directly.

```
app/
  main.py         app factory, CORS, lifespan (fails jobs orphaned by a restart), /health
  core/           config (pydantic-settings), database (engine/session/tenant_scope), security,
                  sessions, audit, metering — the cross-cutting layer everything may import
  models/         SQLAlchemy ORM. One module per domain; a new model must be registered here
  schemas/        Pydantic request/response models, <Resource>Create/Update/Read
  api/            deps.py (auth + ownership guards), rate_limit.py, routes/<domain>.py, router.py
  media/          content-addressed blob store, chunked/resumable uploads, FAISS indexes
  documents/      attachment parsing and the untrusted-text boundary (Decision 8)
  geometry/       format detection, dependency-free file inspection
  mesh/           gmsh tet meshing, quality metrics, exact primitives for tests
  solve/          Solver + ModalSolver ABCs, in-house FEA, CalculiX federation, materials, loads
  simulation/     runner.py — the geometry → mesh → solve job pipeline
  jobs/           JobQueue ABC; ThreadPoolJobQueue today, Celery/RQ later
  design/         a part as a compilable specification, not a tree to edit
  kernel/         the open geometry kernel — OCCT, headless, free, in CI
  catia/          the CATIA backend: 201-operation registry, dispatch, the bridge protocol
  catia_kb/       ~1,600 curated CATIA entries — shipped in code, not in an index
  retrieval/      BM25 over the reference manuals the agent consults
  render/         deterministic hidden-line rendering, section cuts, render diffing
  ai/             agent, tools, prompts, state, resume, tool retrieval, providers, vision
  assembly/       product structure, interface contracts, clash, mass roll-up
  dynamics/       multibody kinematics and reactions
  fatigue/        rainflow and damage, federated to pyLife
  optimise/       optimisation drivers, gradients, honesty rules
  requirements/   the requirements model and coverage
  rules/          design rules, DFM, GD&T
  sheetmetal/     bend allowance, unfold, K-factor
  manufacture/    drawings, dimensions, sheet layout, DXF/STEP export
  verify/         convergence, the validation register, commitments, the accuracy changelog
  observe/        spans and the metering listener
  parts/          bought-in standard parts
migrations/       Alembic — versions/ is the only migration path
scripts/          setup, admin provisioning, the CATIA bridge daemon
docs/             the bridge protocol, the prompt ladder, verification reports, runbooks
tests/            pytest, mirrors app/
data/bm25/        the reference manuals and the built index
```

**The folder pattern is one directory per bounded capability**, flat inside unless the capability
is genuinely large. A subfolder appears only when a capability has internal parts worth naming
separately — `kernel/occt/` (the backend) and `kernel/occt/interrogate/` (the scans) exist
because the alternative is a forty-file directory. `solve/calculix/` is a subfolder because it is
a federated engine behind a seam. **Do not create a subfolder for two files**, and do not
introduce a parallel structure alongside an existing one: find the domain slice that owns your
work and mirror it.

### Frontend (`../Kryova-frontend`, own CLAUDE.md — read it before touching that repo)

Next.js 16 App Router + React 19 + Tailwind v4, wrapped in **Tauri 2** for the desktop. One
frontend for web and desktop, one API, one auth (Decision 6). **Three runtime dependencies by
doctrine** (`next`, `react`, `react-dom`) — a new one is a named decision in the master plan, not
an import, and CI fails if the count changes.

```
src/
  proxy.ts        Next middleware — cookie route gate, the auth boundary
  app/            App Router routes: (auth)/, dashboard/, setup/, layout.tsx, error boundaries
  components/     feature components flat at the top; subfolders per surface —
                  ui/ (the primitives: button, input, select, pill, icons, page-shell),
                  chat/, catia/, simulate/
  hooks/          use-<thing>.ts — data and UI hooks
  lib/            api-client, server-api, chunked-upload, conversation-{resume,events,transcript},
                  agent-stream, load-case, surface-field, poll-schedule, markdown, format
  types/          api.ts, catia.ts, conversation.ts — shared contracts
src-tauri/        the desktop shell
scripts/          setup.mjs / setup.sh / setup.ps1, desktop-build.mjs
```

**Tests sit beside the code they test** (`foo.ts` and `foo.test.ts`), which is the opposite of the
backend's mirrored `tests/` tree. Do not move either to match the other.

Commands: `npm run dev` · `build` · `test` (vitest) · `lint` · `type-check` · `desktop:dev` ·
`desktop:build` · `desktop:msi`.

### Naming

Backend: files `snake_case`, classes `PascalCase`, functions and variables `snake_case`,
constants `UPPER_SNAKE`. Routes live in `api/routes/<domain>.py` exposing
`router = APIRouter(prefix=..., tags=[...])` and are wired in `api/router.py` — **an unwired
router is invisible everywhere, including `/docs`**. Schemas are `<Resource>Create` /
`<Resource>Update` / `<Resource>Read`. Tests mirror the module: `app/solve/modal.py` →
`tests/test_solver.py` or `tests/test_modal.py`, one file per capability, never one per class.

Frontend: components and files `kebab-case.tsx` (`agent-step-list.tsx`), hooks
`use-<thing>.ts`, types `PascalCase` inside `kebab-case.ts` files, tests `<name>.test.ts(x)`
beside the source.

**Test names are sentences.** `test_a_pocket_that_cuts_nothing_is_refused`, not `test_pocket_2`.
Class names group them into a claim: `TestTheRenderIsTheRightWayUp`. This is the convention the
whole suite uses and it is why a failure line is readable without opening the file.

## The eight decisions that constrain how code here is written

Contradicting one is a design change, not a detail. Full text in the master plan, Part 0.

1. **OCCT is the internal engine; CATIA is the delivery target.** The agent designs and iterates
   in OCCT because a design loop needs tens of rebuilds a minute and a seat gives one every few
   seconds; the result **lands** in CATIA, where the customer works. Every customer holds a CATIA
   licence — there is no CATIA-free product story. But geometry must be buildable headless, free
   and in CI, or there is no geometry test without a seat and no sensitivity or optimisation at
   all. **No customer-facing OCCT surface**, and **an operation is added to the OCCT backend only
   when a test, a sweep or an optimisation needs one**, never for coverage. Never write anything
   that assumes CATIA is the only way to make geometry.
2. **Physics is federated, never re-implemented.** Keep `solve/loads.py`, `solve/selection.py` and
   `solve/materials.py` — the load-case and geometric-selector vocabulary is the real asset. Swap
   the kernel underneath. Do not hand-write another solver.
3. **Verification is the product.** An unmeasured claim is never a pass; an unconverged number is
   worse than no number; every result is bound to the geometry, mesh, material, load case and
   solver version that produced it. The honesty conventions elsewhere in this file are this rule
   applied locally — extend them, never erode them.
4. **Free and open, with the licence consequences taken seriously.** GPL solvers (CalculiX,
   code_aster, OpenFOAM, gmsh) are invoked **as separate processes across a file/CLI boundary**.
   Never link one in-process, however convenient.
5. **Honest scope.** Kryova does the structural, kinematic and packaging content and integrates
   bought-in components. Unattended sign-off on a safety-critical machine is not the goal and
   never becomes it. Do not write copy, docstrings or model prompts implying otherwise.
6. **One platform.** Web and desktop share one frontend, one API, one auth. No second admin web
   app, no separate viewer product.
7. **Security and tenancy are architecture.** Refresh tokens rotate per use in per-device families
   with reuse detection. Orgs own projects; Postgres RLS is the safety net under application
   scoping, fed **only** by `SET LOCAL` inside an explicit transaction — the one sanctioned
   exception to the "never `SET` on a pooled endpoint" rule, safe because it dies at COMMIT.
   Cross-tenant is 404, never 403. Admin impersonation carries both identities, defaults
   read-only, and always lands in the append-only audit log.
8. **Attachments are data, never instructions.** User files are parsed locally into
   provenance-tagged content. Extracted text is quoted material — it never enters system prompts,
   and no tool action may be justified solely by attachment text without the user seeing that
   justification. Document-borne prompt injection is a tested-against attack class here.

## Non-negotiable rules

Facts that cannot be inferred by reading a single file.

**Units are mm-N-MPa everywhere, and nothing in the codebase converts.** Length and displacement
mm · force N · Young's modulus and stress MPa · density kg/m³ · **mass output is already
kilograms**. CAD files are read in their own coordinates and assumed millimetres. Any new quantity
lands in this system at the boundary, not deeper in.

**Never `SET` session state against a pooled endpoint.** A pooled host is PgBouncer in
transaction-pooling mode: a `SET search_path` survives on the shared backend connection and is
handed to the next client. This has already happened here — a running server started resolving its
tables into the test schema. Every table reference is compiled schema-qualified via
`execution_options={"schema_translate_map": {None: settings.db_schema}}` (`app/core/database.py`),
and the fixtures do the same for the test schema. Do not "simplify" this to `search_path`.
`SET LOCAL` inside an explicit transaction is the one exception (Decision 7).

**Cross-user and cross-tenant access returns 404, not 403** (`get_owned_project`, `_get_job`), so
ids cannot be enumerated across accounts. Keep it that way on every new resource.

**Background jobs own their own session.** They outlive the request, whose session is closed the
moment the response is sent — use `SessionScopeDep`/`get_session_scope`, never the request
session. Commit the job row *before* `queue.submit`, because the worker looks it up by id in a
different session.

**Gmsh is a process-global, non-thread-safe singleton.** Meshing serialises on a module lock in
`app/mesh/gmsh_mesher.py`, and gmsh is initialised with `interruptible=False` — otherwise it
installs a SIGINT handler that raises off the main thread, and meshing never runs on the main
thread.

**Gmsh picks its reader from the file extension, and blobs are named by SHA-256 with no
extension**, so `gmsh_mesher.py` stages them (hard link where the bytes need no change). It also
rewrites the 80-byte STL comment header when needed: gmsh's STL sniffer skips lines starting with
NUL, so a valid binary STL with the conventional zeroed header and no `0x0A` byte anywhere is
rejected with a bare "Error loading". **The stored blob is never modified.**

**An under-constrained model is caught by the equilibrium residual, not by looking for NaNs.**
SuperLU returns a finite, meaningless vector for a singular system. Do not replace
`_residual_is_small` with a finiteness check.

**Loads are distributed by tributary area** (`solve/selection.distribute_force`), so refining the
mesh does not change the applied load. Region selection is by geometric selector
(`{"type":"face","axis":"z","side":"min"}`), never by face id — face ids are meaningless across a
re-export.

**Every heavy byte goes through `app/media/`.** Nothing loads a whole file into memory — not
writing, not hashing, not serving. Blobs are content-addressed and shared, so deletion goes
through `MediaService`, which drops the file only once nothing references it.

**Migrations live only in `migrations/versions/`.** Run `alembic check` before finishing any model
change. **Migrations are serialised, always** — Alembic's `down_revision` chain is linear, so two
revisions generated in parallel produce branching heads somebody has to merge by hand.

**A Python-side column default does not exist until the flush.** `UUIDPrimaryKey` gives `id` a
`default=new_uuid`, and SQLAlchemy applies it *during* the flush — so anything reading `obj.id`
before then gets `None`. This bit three separate times on 2026-09-06: `personal_slug` built an
organisation slug from a user id in a `before_flush` hook, so every user created inside one flush
raised `AttributeError` (and only `test_startup.py` caught it, because the tenancy tests flush
first and act second); and `AuditService.record` hashed the id before the flush and wrote a
different one after, so **every audit entry failed its own verification on read-back**. The fix is
to *materialise* the id before you read it.

**Two concurrent full-suite runs drop each other's tables.** The `kryova_test` schema is created
and dropped per run. Run the suite once at a time.

**A conversation acts on the document it owns, not on CATIA's `ActiveDocument`.** Every
document-scoped call frame carries `document: {doc_name, remote_path}` from the conversation's
`CatiaDocument` row, and `backend.ensure_document` activates it — reopening it from disk if CATIA
was restarted — before the operation runs. Without it, an engineer clicking another part between
two messages silently redirected the work; nothing errored, the wrong file just grew features. The
unscoped tools are enumerated with their reasons in `dispatch._UNSCOPED_TOOLS`. A backend that
holds documents must override `ensure_document`; `tests/test_document_binding.py` fails if one
inherits the no-op.

**The transcript is not the record of what was done — `CatiaOperation` is.** The window trims the
oldest messages and the summary is an LLM paraphrase of what it trimmed. `app/ai/resume.py` reads
the log instead. Loose ends are keyed on the tool alone and cleared by any later success of that
tool — keying on arguments would leave every superseded retry in the list forever. It carries no
`catia_` prefix on purpose: that prefix means "goes to the workstation", and this answers with
CATIA closed (`tests/test_tool_registry.py` enforces it).

**Errors are human-readable and actionable.** "Increase element_size_mm to coarsen it", "Check
that the fixtures remove all six rigid-body motions" — match that register, never degrade to
"Invalid input". Expected, explainable failures (`MeshError`, `SolverError`, `ValueError`) are
recorded on the job row with their message; anything else is logged with a stack trace and
recorded as "Unexpected solver failure".

## Database

**Local PostgreSQL, not Neon** — switched on Windows 2026-09-07 and on Linux 2026-09-08. Neon's
URL stays in `.env` as the fallback; the local server is an override in `.env.local`, which is
gitignored and read *after* `.env` so every line in it wins. Full recipe for both platforms,
including why the role must not be a superuser, is in **[docs/LOCAL_POSTGRES.md](docs/LOCAL_POSTGRES.md)**.

1. **Two databases**: `kryova` for the application, `kryova_test` for the suite. `conftest.py`
   **refuses** a `TEST_DATABASE_URL` that resolves to the same host and database as
   `DATABASE_URL`, because the fixtures create and drop tables.
2. **Leaving `TEST_DATABASE_URL` unset falls back to in-memory SQLite**, and the RLS, JSONB and
   cascade tests then skip themselves. That is how a green tick once stood for a Postgres suite
   that had never touched Postgres, so `conftest.py` also reads the variable out of
   `.env`/`.env.local` into the environment — setting it there used to look like configuration
   and do nothing.
3. **The application role must be `NOBYPASSRLS` or the RLS policies are inert.** A superuser, and
   equally Neon's `neondb_owner`, outranks both `ENABLE` and `FORCE ROW LEVEL SECURITY`. On the
   local Linux server the role is not a superuser, so the policies genuinely enforce and
   `test_the_application_role_must_not_bypass_row_level_security` XPASSes. **CI enforces too as of
   2026-09-08**: the `postgres:17` container bootstraps as `postgres` and `ci.yml` creates the
   application role `NOBYPASSRLS`, with a step that fails the job if the role it connected as can
   bypass. Measured both ways against a real container — as `POSTGRES_USER: kryova` the role reads
   `rolsuper/rolbypassrls true true` and the test xfails; with the bootstrap it reads `false false`
   and XPASSes. **Neon is the one place left inert**, because `neondb_owner` holds `BYPASSRLS` and
   cannot drop it, so the `xfail(strict=False)` marker stays and must not be deleted to make a
   local or CI run look tidy.
4. **`sslmode=disable` in the local URL is required, not a shortcut.** A stock local Postgres has
   `ssl = off` and refuses the handshake. `app/core/database.py::sslmode_for` reads the mode out
   of the URL rather than hardcoding it.
5. `expire_on_commit=False` is load-bearing; endpoints read ORM attributes after `commit()`.
6. **`~250 ms` per round trip on Neon, `~0.14 ms` locally.** The DB suite was four minutes of
   almost pure latency and is now well under one.

## Testing

1. **One capability, one test file, mirroring `app/`.** The suite is ~6,400 tests.
2. **Physics and design tests never request a database fixture**, so they open no connection and
   run offline in under a second. Keep it that way — that tight loop is why meshing, FEA and
   design work is bearable. `app/design/`'s 338 tests run offline because `execute` takes its
   runner as an injected callable; do not import `app.kernel` into that package.
3. **The solver is verified against closed-form solutions, not recorded output.** A bar in pure
   tension reproduces σ = F/A and δ = FL/AE to 1e-6, including through a real gmsh mesh. Any new
   solver work is verified the same way. **Eigenvalue tolerances must be relative** — eigenvalues
   are ω², order 1e10 for steel and 1e4 for rubber, so any absolute threshold is simultaneously
   too tight for one and too loose for the other.
4. **Verify a new guard by breaking the thing it guards** and watching a named test fail. Say so
   in the commit. A guard nobody has seen fail is a guard nobody knows works.
5. `client` overrides the job queue to `InlineJobQueue` so jobs run on the request thread inside
   the test's open transaction — a worker thread would use its own connection and see none of the
   uncommitted data.
6. **Retrieval is verified twice.** `test_retrieval.py` proves the machinery on synthetic
   fixtures. `test_retrieval_corpus.py` measures the *real* index — 38 questions scored
   precision@1/@3 and MRR, plus corpus and citation health. Every test in the first passed while
   the shipped index was labelling 23% of its passages with a procedure step, which is the whole
   argument for having the second. It **skips** when no index is built, so a fresh clone stays
   green.
7. **A one-off API-surface check is not a test** — does this OCCT symbol exist, does this method
   take these arguments — and is worth doing, because shipping code that calls a name that is not
   there wastes a seat session on an `AttributeError`.

## Build with parallel agents — the default, not an optimisation

A phase here is usually five or six pieces touching disjoint files — a module, its tests, a wiring
change, a document check, an install — and running them one after another spends the night on the
ordering rather than on the code. Six agents on one phase is normal; ten is not too many when the
lanes are genuinely separate. What makes it work rather than a merge conflict:

1. **Assign by FILE, not by topic.** Every brief names exactly what that agent owns and what it
   must not touch, **by path**. "You own `app/verify/**` and `tests/test_verify*.py`" is a lane;
   "you handle verification" is a collision. Two agents that both decide they need `deck.py` will
   silently overwrite each other, and the loser's work looks like it was never done.
2. **Say who else is running.** The brief names the other lanes, so an agent that needs something
   from a locked file **reports the seam it needs** instead of reaching in. A clean unsatisfied
   interface is worth far more than a duplicated writer — integration is cheap, and un-picking two
   divergent copies of one function is not.
3. **Nobody commits but the parent.** Agents do not `git add` and do not `git commit`. Six agents
   committing into one branch interleaves half-finished work with no way to tell which change
   belongs to which piece. The parent reads the reports, integrates, runs the checks, and commits
   each piece with its own message.
4. **Migrations are serialised** and **nobody runs the full suite** — see the rules above for
   both; concurrent runs drop each other's `kryova_test` schema. Each agent runs *its own* test
   files, plus `ruff` and `mypy`.
5. **Give them the authority to decide** within their lane. An agent that must ask before every
   choice is a slower version of doing it yourself.
6. **Say which of the two a claim rests on** — the offline suite or a seat run. They prove
   different things and only one of them proves the path.

## Tools

**Use `rg` (ripgrep), not `grep`**, for code search — it respects `.gitignore`, so it will not
drown you in `venv/`, `node_modules/` or `data/bm25/`. `rg -n "pattern" app/` for a search,
`rg --files -g "*.py"` to list. It is verified working correctly in this repo (2026-09-08).
`fd` and `ast-grep` are not installed; use `find` or `rg --files`. `gh` for GitHub, `jq`/`yq` for
JSON/YAML.

**The Bash tool's working directory persists between calls.** The frontend is a sibling checkout,
so a `cd ../Kryova-frontend` in one call leaves the next one there — `pwd` before anything that
depends on which repo you are in. This has already produced a build of the wrong tree under the
right tag.

**Read only the files you need.** `rg` to locate first; avoid loading large files wholesale.

## The design IR (`app/design/`)

A part described as a **specification that is compiled**, rather than a feature tree that is
edited. It exists because editing a tree conversationally breaks on the topological naming
problem: insert a fillet upstream and every downstream reference shatters. Regenerating from a
spec has no downstream edit to break.

Read `spec.py` first (what a design *is*), then `compile.py`. Then `execute` runs a plan,
`assertions` says whether the result is acceptable, `diff` says what an edit reached, `correct`
closes the loop. `names` and `params` are usable alone.

1. **Only `execute` touches anything outside the package, and through an injected callable.** No
   session, no socket, no `dispatch` import. That is why 338 tests run offline in under a second.
2. **`Plan.digest()` does not answer "does this build the same part?"** despite reading like it. A
   plan carries each call's `note` — rationale travels with the design on purpose — so rewriting a
   comment moves the digest and builds identical geometry. Every geometry question goes through
   `diff.builds_the_same` (tools and resolved arguments only).
3. **Impact analysis compares compiled plans, never spec text.** By compile time every parameter
   is a literal in the argument list of the calls using it, so a feature moved iff its resolved
   calls differ — exact, with no parameter-usage graph to fall out of step.
4. **A reference is not always a read.** `catia_sketch_rectangle(sketch=@profile)` draws *into*
   the profile; `catia_pad(sketch=@profile)` extrudes what it finds. So a feature's geometry can
   change while its call is byte-identical. The two are told apart by `Plan.unaddressable`.
5. **A plan carries exactly one late-bound value**, `Created(feature)`, because a fresh pad is
   called whatever CATIA invented. `bind()` resolves it from what the creating call reported.
   Predicting `Pad.1` is the positional fragility this package exists to remove.
6. **An assertion that could not be measured is `UNMEASURED`, never a pass.**
7. **The correction loop's stopping rules are exact, not heuristic.** A repair compiling to the
   same buildable plan cannot change the outcome, so it ends the loop and is not counted as an
   attempt; a plan already tried is a cycle. A repair that *does not compile* is a normal attempt
   on purpose — the compiler's error names the feature and says what to do.

## The geometry kernel (`app/kernel/`)

Decision 1 made real. OCCT via `cadquery-ocp` (OCP) — `pythonocc-core` is not on PyPI and would
have forced conda into the deployment.

Reading order: `occt/binding.py` (the one place OCP is imported), then `occt/document.py`, then
`occt/naming.py` (the three rules that make names survive regeneration — break one and `Solve()`
returns success while resolving to nothing).

Four backend-neutral modules sit above the backends, and the split is load-bearing:
`measurement.py` (what a part reports, with `Detail` levels for latency), `interrogation.py` (what
a part can be *made into* — has a premise, can be inapplicable, is frequently sampled, and never
runs speculatively), `contract.py` (the documented vocabulary an assertion may read;
`undocumented_paths()` is asserted empty), `provenance.py` (measured / approximated /
unavailable-with-a-reason, as a sidecar so paths still resolve).

1. **`topology.explore` de-duplicates and `explore_oriented` does not**, deliberately.
   `TopExp_Explorer` visits a sub-shape once *per owning parent*, so a box explores as 24 edges.
2. **A face's outward normal is not its surface normal.** OCCT stores orientation separately, so a
   REVERSED face's normal points into the material. `face_normal_at` is the one place that
   correction lives.
3. **A UV grid is not a set of points on a face.** A trimmed face reports its whole surface's
   parameter range, so most of a naive grid lands in the hole. Everything goes through
   `interrogate/sampling.py`.
4. **Sampled answers must never be reported as measured.** Thickness and undercut are upper bounds
   from a finite ray set, and they say so.
5. **`str.capitalize()` is banned in error text** — it lowercases everything after the first
   character, turning `BRepFeat_MakePrism` into `brepfeat_makeprism`. Use `errors._as_sentence`.
6. **OCP passes `Handle(Geom_…)&` by value**, so any OCCT function that works by reassigning a
   handle is *inert* here — it builds the answer and drops it, with no exception and no return
   value. `GeomLib::ExtendCurveToPoint` and `ExtendSurfByLength` are both like this.
7. **`explore` yields base `TopoDS_Shape`.** `BRepAdaptor_Curve`, `BRepTools_WireExplorer` and
   `MakeWire.Add` are overloaded on the concrete type and refuse it — cast through
   `symbol("TopoDS").Edge_s` / `Face_s` / `Wire_s`. This has cost time four times now.
8. **Only what `occt/binding.py` registers is reachable through `symbol()`.** Go through
   `classify.edge_curve_type` and `BRepAdaptor_Surface(...).Plane()`, or add the symbol to the
   registry deliberately rather than importing OCP at the call site.

## Analyses available

All verified against closed-form solutions, not recorded output.

1. **Linear static** — `solve/linear_static.py`, `LoadCase`, checked against σ = F/A and δ = FL/AE.
2. **Modal** — `solve/modal.py`, `ModalCase`, checked against the bar `f=(2n−1)/4L·√(E/ρ)`,
   cantilever Euler-Bernoulli modes 1–3, six rigid-body modes free-free.
3. **Buckling** — `solve/buckling.py`, `BucklingCase`, checked against Euler `P=π²EI/(KL)²`.
4. **Thermal stress** — `solve/thermal.py` via `LoadCase.delta_t_k`, checked against the
   restrained bar `σ = −EαΔT`.

Three things that are easy to get wrong and are pinned by tests:

1. **Thermal strain must be subtracted during stress recovery**, not only added as a load. Leaving
   it out reports the stress of a freely-expanding part — wrong sign and wrong size.
2. **Buckling is posed as `−Kg φ = μ K φ`**, not the natural way round: `Kg` is indefinite and the
   generalised symmetric eigensolver needs the positive-definite matrix on the right. `λ = 1/μ`.
3. **The modal mass matrix is integrated analytically** in barycentric coordinates, not with the
   stiffness assembly's four-point Gauss rule — that rule is exact only to degree 2 and tet10's
   `N^T N` is quartic, so reusing it would be wrong by a few percent: plausible-looking, and wrong.

### CalculiX: there is no `ccx` on this Linux machine, and it shapes what may be claimed

`app/solve/calculix/` is written entirely from the CalculiX manual. Every module in it says so
about itself (`steps.py`, `frd.py`, `elements.py`) with a `[M]` citation per keyword and a
`[S]` where the manual stops and ccx's own source answers. **A deck written here is documented,
not verified**, and the difference is a status line: the first Windows run is the measurement.
Keep facts as *tables of constants* rather than inline format strings, so that run corrects one
value instead of a scatter of them.

Three things about shells and beams that produce a plausible wrong number rather than an error
(master plan 6.3, all pinned by `tests/test_solver_calculix_elements.py`):

1. **ccx has no shell or beam formulation — it expands them into solids** (S4/B31 → C3D8I,
   S8R/B32 → C3D20R) and ties the expansion back with MPCs. One element through the thickness,
   whatever the surface mesh density, so a through-thickness stress *distribution* is not
   something these elements contain.
2. **The results file is written for the expanded model unless `OUTPUT=2D` is asked for.** The
   expanded model has more nodes than the mesh submitted and every number in the `.frd` is a
   real displacement of a real node — just not the node `frd.displacements(frd,
   mesh.node_count)` believes. Nothing errors; there is not even a length mismatch.
3. **A shell or beam node has six degrees of freedom, a solid node three.** A `clamp` written as
   DOFs 1-3 is a **pin**, and a cantilever on a pin is a different structure. `constraints.
   local_dofs` reads the fixture's *word* for this; a `custom` fixture naming `["x","y","z"]`
   stays a pin on purpose. `check_restraints` needs `dofs_per_node=STRUCTURAL_DOFS` for these —
   the three-DOF form refuses a beam clamped at one node *and* refuses a straight beam as a
   degenerate mesh, two refusals of correct models.

`write_frame_deck` takes a **nodal force vector, not a `LoadCase`**, and that is deliberate:
tributary-area distribution is defined over a solid's boundary triangles, and loads on 1-D and
2-D regions have no rule yet. An equal split would run, look right, and be mesh-dependent in
exactly the way the load vocabulary exists to prevent.
A bar in tension still returns a finite positive buckling factor (~68,000×), because a 3D bar has
small compressive pockets at the load introduction. That is correct; the meaningful statement is
the ratio to the compressive case.

## Seams — respect them

1. **`solve.Solver`** (ABC) — mesh in, load case in, fields out. A surrogate or neural solver must
   drop in without the API, job or AI layer knowing which ran.
2. **`solve.ModalSolver`** (ABC) — a sibling, not a method on `Solver`: natural frequencies come
   from a different input and return a different output, so folding them in would make every
   caller branch on what it got back.
3. **`jobs.JobQueue`** (ABC) — one method, `submit`. Moving to Celery must not touch routes.
4. **`media.LocalMediaStore`** — content addressing and chunked IO behind a small surface, so an
   S3 store is a swap, not a rewrite.

**Never reach around a seam.** If a route needs to know which solver ran, put it on the job row.

## Reference manuals (`app/retrieval/`) and the CATIA KB (`app/catia_kb/`)

The agent consults the CATIA and FEA documentation held on this machine instead of answering from
memory, via `search_documentation`. **It is lexical (BM25), not embeddings, and that was a decision
rather than a shortcut**: the discriminating terms are exact (`M6`, `Ø12`, `tet4`, `V5R21`,
`Multi-sections Solid`) — precisely what embeddings blur — the corpus is bilingual, where accent
folding does for free what no embedding quality gives, and no embedding model ships with a
deployment whose point is that it runs offline. `Corpus.search` sits behind a small surface so a
dense stage can be fused in later.

`app/catia_kb/` does a different job: ~1,600 entries that know Edge Fillet is in Part Design's
Dress-Up Features toolbar, that the French interface calls it `Congé d'arête`, and when it fails.
It ships **in the code, not in an index**, so unlike the manuals it is always present.

1. **`KnowledgeService.search` and `CatiaKnowledge` cannot raise.** Missing index, corrupt index,
   wrong format version, disk gone — all return empty, logged once. Consulting the manuals
   improves an answer and must never be why there is not one.
2. **The tool and the prompt are gated on the index actually existing**, not on the setting. A
   prompt describing a tool the model was not given teaches it to hallucinate a call. There are
   **four** frozen system prompts for this reason (CATIA × docs).
3. **Never name the mechanism in user-facing text.** The step label is "Checking the
   documentation"; "I searched my knowledge base" is a worse answer than the answer.
4. **Heading detection is the most tuned code in `chunking.py`.** These manuals are almost entirely
   numbered procedures, so a bare `\d+\.` pattern labels thousands of instruction steps as section
   titles. **Refusing to *accept* a line is not the same as *rejecting* it** — declining a bare
   `6.` only passed the line to the title-case fallback, which took it, labelling 1,143 of 5,003
   real passages. `_ENUMERATOR_RE`, `_PATH_FRAGMENT_RE` and the minor-word-ending test are the
   explicit rejections; all three run before the title-case fallback.
5. **Language is a boost, never a filter** (1.35, measured — re-sweep with
   `tests/test_retrieval_corpus.py` before changing it). CATIA's menus are translated, so a French
   user needs the French page, but a workbench documented only in English must still answer them.
6. **Precision is the hard half of the KB, not recall.** This vocabulary contains `fit`, `add`,
   `part`, `box`, `web` and `pip`. `NEVER_BARE` never matches alone; `AMBIGUOUS_WORDS` needs
   corroboration. Distinctive words are in neither, because "how do I make a pocket" carries no
   other signal and must still work. `TestPrecision` is the set of sentences that must produce
   nothing — add to it before widening any alias. Two supporting rules: **product codes need their
   capitals when they collide** (`PIP`/`pip`, `FIT`, `GAS`, `EST`, `CUT`; codes with no collision
   like `GSD` match either way), and **fuzzy matching only fires once something matched exactly**,
   or `document` scores 0.94 against `Documents` and every English sentence with a long word
   produces a hit.
7. **Expansion and the coverage floor are in direct conflict** — this shipped as a bug once.
   Expansion adds synonyms, and a passage matches the English name or the French one, never both.
   `Corpus.search(..., coverage_query=)` measures the floor against the user's original query.
   Never remove that argument.
8. **A missing translation is reported as missing.** Never fall back to the English name presented
   as the localised one — an engineer can work with "I don't have the German name, it's here in
   the menu", and cannot recover from being sent to a menu item that does not exist.
9. **The COM automation API is not localised.** `AddNewPad` is `AddNewPad` on every language
   install; only user-typed data translates. This is why the bridge works on any seat, and why a
   macro that looks up `"Pad.1"` by string is the one that breaks abroad.
10. **Ambiguity is named, not resolved.** SMD vs ASL, GSD vs WSF, Geometrical Set vs Body — the
    `Disambiguation` table forks these and the brief prints the fork.

```
venv/bin/python -m app.retrieval.build            # build or rebuild
venv/bin/python -m app.retrieval.build --check    # exits non-zero when a rebuild is needed
venv/bin/python -m app.retrieval.build --query X  # see what the agent would find
```

## Rendering and the visual check (`app/render/`, `app/ai/vision.py`)

Eight canonical views rendered byte-identically run to run, section cuts, a before/after diff, and
a vision model asked whether the part matches the request.

1. **Hidden-line removal, not OpenGL.** Two renders of the same geometry must be *byte-identical*
   so a render hash can join mass and plan-digest as a third identity check, and a GL image is a
   function of the driver, sampling and display server on a project that develops on Linux and
   ships on Windows. HLR is arithmetic; the raster under it is integer.
2. **OCCT's `gp_Ax2` Y axis is `direction × X`** — the opposite of the up vector `views.py`
   declares — so `project._flatten` negates y. Leaving it out renders every part upside down and
   **nothing can see it**: a consistently mirrored image is still byte-identical to itself, and a
   wireframe looks plausible either way up. It shipped inverted for one day.
3. Determinism is defended at each cheap place to lose it: no anti-aliasing, `floor(v+0.5)` rather
   than banker's rounding, dash phase per polyline not per segment, curve deflection relative to
   model size, and a hand-written PNG encoder (an outside one can add a timestamp chunk).
4. **A section's normal points at the material that is removed** — `catia_split`'s own convention.
   Two conventions for one question is how a part ends up mirrored with every test green. Hatching
   fills by **even-odd across every wire at once**, so a bore falls out of the parity with nothing
   having to identify it as a hole.
5. **The visual check is a filter, never a sign-off**, so `VisualReview` deliberately has no
   `approved`/`passed` property — only `objected`. Every way it can fail to run is `unchecked`,
   which is never a pass. Nothing in it raises.
6. **Ollama does not refuse an image handed to a text-only model** — it drops it and answers
   anyway, so the check would manufacture agreement, which is worse than no check. `_sees()` gates
   on `/api/show` `capabilities` or a `projector_info` block (structural signals, no model-name
   list to rot); `AI_VISION_MODEL` names the model that looks. `num_ctx` must be sized for the
   images too — Ollama truncates a prompt from the front in silence.

## Driving CATIA's interface

Eight tools reach every command on the seat rather than the thirty Kryova implements directly.
Server specs in `app/catia/tool_specs.py`, resolution in `app/catia_kb/ui.py`, daemon in
`scripts/catia_bridge/`. Full contract in `docs/CATIA_BRIDGE_PROTOCOL.md`.

1. **It is Win32, never COM.** `GetMenu`, `EnumChildWindows`, `SendMessageTimeoutW`. Two
   consequences you must not undo: it reads the seat's *actual* labels so it works in a language
   nobody wrote a table for, and it keeps working while a modal dialog has COM blocked — which is
   when it matters most.
2. **`OUT_OF_BAND_TOOLS` skip the COM liveness probe**, and the same tools are in
   `dispatch._NO_AUTO_CHECKPOINT`. A checkpoint is a COM save, and a failed checkpoint refuses the
   call — gate these on COM and the tools that dismiss a stuck dialog can only run when no dialog
   is stuck. `catia_run_command` is the deliberate exception.
3. **`StartCommand` fails silently.** Hand it a name CATIA does not know and it does nothing,
   raises nothing, returns nothing. So the daemon tries the **live menu first** and falls back to
   `StartCommand` with `verified: false`. Never report an unverified `StartCommand` as success.
4. **Command labels are localised; internal command ids are not, and are undocumented.**
   `COMMAND_IDS` holds only ids with a published source. Do not add one from memory.
5. **Buttons are pressed by role, never by label.** `ButtonRole` + `BUTTON_LABELS` resolve
   OK/Cancel/Apply per language; `STANDARD_CONTROL_IDS` (IDOK=1, IDCANCEL=2) is the language-proof
   fallback. A Spanish seat's accept button reads `Aceptar`.
6. **Refusals are exact-label or leading-phrase, never substring.** A leading-word rule refused
   `Exit Sketcher Workbench`; a substring rule refuses `Copy Options`. **An over-refusal is not
   safe** — the agent's recovery from a refusal is to try something else, so it becomes a wrongly
   built part.
7. **Mock mode simulates the interface** (`mock_ui.py`) and runs in a language:
   `--mock-language de`. Pressing OK on the mock Pad dialog builds a real mock Pad, so tests assert
   the outcome. Every interactive test runs against `en` and `de`.
8. **What Linux cannot verify** is listed in the protocol doc: whether CATIA's dialogs answer
   `WM_GETTEXT`, whether `EN_CHANGE` is needed, what its window classes are. `describe_dialog`
   reports unrecognised controls with their class name so the first Windows session produces the
   answer instead of a shrug.

### Two seat behaviours that cost a restart each — measured, not theorised

1. **`catia_sketch_dimension` fails on this seat far more often than it works** (measured
   2026-09-08, three consecutive PRO1 runs on French V5-R33). `AddDimensionConstraint` returns a
   constraint and *reading* `.Dimension` off it raises `E_INVALIDARG` —
   `(0, 'CATIAConstraint', 'La méthode Dimension a échoué', ...)`. It is refused in words now and
   the half-made constraint is discarded, so a failed dimension no longer poisons the sketch it
   was added to — but **the underlying call still fails**. The working route to a dimensioned
   profile is to *draw it at the size you want*: the coordinates passed to
   `catia_sketch_polyline` and `catia_sketch_rectangle` are millimetres and **are** the dimension.
   Do not build a feature on the assumption that a constraint will take.
2. **A CATPart that a CATProduct has open does not close.** `Document.Close()` returns cleanly and
   the document stays in `Documents`, so `while Count > 0: close Item(1)` spins forever reporting
   success every time round — it wedged the seat on 2026-09-08 and cost a restart. Close products
   first, and test whether the **count fell**, not whether the call succeeded. Same shape as the
   read loop `MAX_READS_WITHOUT_PROGRESS` exists for.
   `ABQMaterialPropertiesCatalog.CATfct` never closes at all; it is CATIA's own material
   catalogue, not a leftover.

## Known landmines

Live defects, not style opinions. Read before touching the file.

**This section was audited on 2026-09-08 and five of its nine entries were false** — fixed weeks
earlier and never removed from here. That is worse than an empty section: a stale landmine sends
you to re-fix something that works, and it teaches you to skim the ones that are real. **Verify an
entry before acting on it, and delete it the moment it stops being true.**

1. **`data/bm25/` holds ~450 MB of tracked Dassault Systèmes PDFs, on purpose** (2026-09-01) so the
   corpus syncs to the Windows workstation with a plain `git pull`. They are third-party
   copyrighted material in a repo carrying its own LICENSE, and a later `.gitignore` cannot undo
   it — removing them needs a history rewrite. Raise this before the repository is published or
   cloned widely. `data/bm25/index/` is *not* tracked: it is derived and would conflict between
   machines.
2. **Four of the 25 PDFs are scans with no text layer** and cannot be indexed without OCR. The
   build reports them as `scanned, no text layer` and carries on; expected, not a regression.
3. **RLS is inert on Neon** — see *Database* item 3. The policies are deployed and correct and
   `neondb_owner` outranks them. **CI was the other half of this entry until 2026-09-08** and is
   now fixed; a claim that CI cannot enforce RLS is out of date, and the assertions in
   `TestContinuousIntegration` are what keep it that way.
4. **`pip install pychrono` installs an unrelated package and succeeds.** The engine probe checks
   the module really is Chrono. There is no dynamics engine and the docstrings say so.
5. **The in-memory rate-limiter backend is per-process.** `RedisBackend` exists in
   `api/rate_limit.py`; with `InMemoryBackend` selected, multiple workers each enforce their own
   budget. Check which backend is configured before reasoning about a limit.
6. **`ezdxf` is imported by `manufacture/dxf.py` and `documents/readers.py` and is declared in no
   requirements file** (found 2026-09-08). Both guard the import and degrade, so **DXF export and
   DXF attachment reading can never run** — that half is still true and is a product decision
   nobody has taken. The mypy half is fixed: the three `import-not-found` errors were **failing
   the mypy gate in CI on every run**, and `pyproject.toml` now declares the module optional and
   untyped, so `mypy app/` reports *Success: no issues found* and the "if mypy prints anything, it
   is yours" rule is literally true again. Declaring it says the import is optional; it does not
   decide whether DXF should be a supported feature.
7. ~~**The suite has pre-existing failures on `main` as of 2026-09-08**~~ — **fixed 2026-09-08.**
   All twelve are green and the suite is 6,451 passing / 0 failing. Kept as a record of what they
   turned out to be, because the split is the useful part: **one was a real defect** (the
   verification nudge discarded the "nothing has actually been done" message that the exhausted
   correction loop had just written, so a model that ran nothing closed the turn with "Done." and
   a footnote — the exact silent failure `test_written_tool_calls.py` exists to prevent, arriving
   from the other side); **one was a tripwire working correctly** (`KryovaFaceMap` was added to
   the frozen VBA library and the test fails on purpose until a human reviews it — reviewed and
   accepted); **one was a stale artefact** (the BM25 index predated the chunker changes; rebuilt,
   P@1 back to 94.7%); and **the other nine were tests that had rotted**, each in a different way:
   a fixture whose user message accidentally carried a measurable requirement, a stub one call
   short of the code it fakes, a volume oracle coupled to how many times the code reads a volume,
   a negative probe naming a tool the system prompt has since started teaching, and a `caplog`
   assertion over every logger in the process rather than the one under test. **None of the nine
   was wrong about what it claimed** — every one was wrong about how it checked it, which is why
   they all failed on a change to something else.

**Removed on 2026-09-08 because they were no longer true** — recorded so nobody reinstates them
from memory: `SECRET_KEY="changeme"` boots (it is refused at startup, `config.py:450`); the rate
limiter trusts `X-Forwarded-For` unconditionally (it honours `trust_proxy_headers` and counts from
the right, `auth.py::_client_ip`); no list endpoint paginates (all four do, `page_size` capped at
100); SQLite is not actually refused (`_require_postgres` raises for any non-PostgreSQL URL);
`/health` is not a readiness probe (it probes the database and the media store and returns 503).

## Do not

1. Don't convert units anywhere — the whole codebase is mm-N-MPa.
2. Don't use `SET search_path`, or any session-level `SET`, against a pooled endpoint.
3. Don't borrow the request session in a background job.
4. Don't call gmsh off the module lock, or modify a stored blob in place.
5. Don't return unpaginated collections — every list endpoint paginates, `page_size` capped at 100.
6. Don't add migrations outside `migrations/versions/`.
7. Don't return 403 for another user's or tenant's resource — 404.
8. Don't claim a capability in a docstring, README or status line that the code does not have.
   This has already happened twice, and both times it cost a session to discover.
9. Don't mark a master-plan task `DONE` without a test that proves it, and don't leave a finished
   task unmarked.
10. Don't claim an end-to-end result from Linux. No CATIA here.
