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
out of), [KRYOVA_PRD.md](KRYOVA_PRD.md), [KRYOVA_STATE_OF_THE_PROJECT.md](KRYOVA_STATE_OF_THE_PROJECT.md),
and **[docs/MAKING_IT_FASTER.md](docs/MAKING_IT_FASTER.md) — read it before optimising
anything**, because most of the obvious moves here are already made, structurally impossible,
or actively harmful, and the file says which is which.

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
5. **Never delete a status; supersede it.** Then append one line to the build plan's *Done*, and
   re-stamp the plan's progress block (`python -m scripts.plan_progress --write`) — it is
   generated from those status lines, and it is the one number in the plan nobody may type.
6. **You may change the plan itself.** Add a task, split a phase, add a whole phase, or rewrite
   one that turned out to be wrong — when the work teaches you something the plan did not know.
   That is how the four defects recorded in its header got found. Say in the commit message what
   changed and why. What you must not do is quietly leave a task describing something nobody is
   going to build, or mark something done that is not.
7. **Keep this CLAUDE.md current too.** When you learn something that would have saved you an
   hour — a trap, a command, a rule that is not inferable from one file — add it here, and delete
   anything you find that is no longer true. Two claims in this file were false for weeks (a venv
   that did exist, a SQLite refusal that was never implemented), and each one cost a session.

## Decide, then say what you decided

**Do not come back with a question when you could come back with a result.** Within a task,
every choice is yours: which design, which trade-off, which of two defensible readings of an
ambiguous instruction. Pick the one you would defend to a reviewer, do the work, and state the
decision and its reason in the summary and in the plan. A session that stops to ask has spent
the user's turn and delivered nothing.

Three things this does **not** license, because they are the reasons the rule has limits:

1. **Never invent a fact to avoid asking.** A number, a citation, a capability, a test result —
   if you cannot establish it, the honest encoding is the one this codebase already has for it
   (`UNKNOWN` with a reason, `unmeasured`, `blocked` with the missing capability named). Deciding
   is not the same as guessing, and the whole of `app/verify/` exists to keep them apart.
2. **Finish the phase if it can be finished, and say so plainly if it cannot.** Work stopped by
   hardware goes in THE QUEUE (see below) with the claim it would settle. Work stopped by a
   missing capability goes in the plan as a task, in the phase that owns the capability — not as
   a silent gap and not as a task nobody will build.
3. **A decision that changes an interface is still a decision, and it goes in the commit
   message and the plan.** Widening `SolveOutput`, adding a `Selector`, changing a response
   shape: take it, do not stall on it, and write down what it cost.

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
   **Break one thing at a time and prove the restore, not just the failure.** A script that
   mutates a source file, runs pytest and copies a backup back can poison its own backup —
   re-run it, or start it on a tree a previous run left dirty, and every later "the guard caught
   it" is measuring the *earlier* break. That happened on 2026-09-08 and it invalidated three
   results in one pass: LE10's solid was left one-piece, so its three tests failed under every
   subsequent break for a reason none of those breaks had caused. What caught it was
   `code_fingerprint()` — the recorded artefact's hash of every file that decides an answer — so
   after a break run, **check the fingerprint back to its recorded value**
   (`data/verify/validation-outcomes.json`) rather than trusting `diff` against a backup that may
   itself be the broken copy. If it does not match, the tree is dirty and nothing measured on it
   counts.
   **Key the backups on the full path, never `basename`.** This tree has
   `app/models/simulation.py` *and* `app/schemas/simulation.py`; a script saving to
   `/tmp/$(basename $f)` gives them one file, so the second `cp` overwrites the first and the
   restore writes the model over the schema. It happened on 2026-09-09, `diff` against the
   backup said IDENTICAL for both, and the symptom was an unrelated
   `InvalidRequestError: Table 'simulation_jobs' is already defined`. Recovery is
   `git checkout --` the clobbered file and re-apply, which only works because the edit was
   small enough to remember — so make the backup path collision-proof instead.
4. **There is no CATIA and no GUI run here.** Do not claim an end-to-end result from Linux.
   An integration claim between gates is unproven and is written as unproven.

**On Windows (the machine with CATIA and the bridge — proving the product):**

> **The Linux stretch stopped on 2026-09-09 at phase E7**, with ten of twenty-nine phases
> complete, the suite green, and everything closed that could be closed without hardware. If
> you are reading this on the Windows machine, **you are the next session, and your brief is
> the top of [docs/WINDOWS_VERIFICATION.md](docs/WINDOWS_VERIFICATION.md)** — read it before
> anything else. Three jobs, in order: verify what Linux wrote, **finish THE QUEUE** (sections
> A–D are measurements, **section E is code you have to write on that machine**), and drive
> the product through the GUI with `docs/GUI_PROMPT_LADDER.md`.

0. **Start from THE QUEUE at the top of [docs/WINDOWS_VERIFICATION.md](docs/WINDOWS_VERIFICATION.md)** —
   a checkbox list of every item a Linux session was stopped on by missing hardware, grouped by
   what it needs (`ccx`, a CATIA seat, a document, a vision model, **a seat to write against**)
   and ordered by what a run settles per minute. **A Linux session that hits a hardware wall
   adds its row there in the same commit as the work**, the way a finished task updates the
   master plan. An unrecorded blocker gets rediscovered from scratch, which has already cost
   two sessions. **Section E is not measurement — it is unwritten code**: the CATIA side of
   sheet metal (E1), the four stop gates (E2), and the conduction oracle against ccx (E3).
1. This is where the **whole application** is exercised: the real `/api/v1/ai/chat` endpoint
   through the web GUI on `localhost`, the CATIA seat through the bridge, the desktop shell.
2. **Test with the prompt ladder** (`docs/GUI_PROMPT_LADDER.md`), which as of 2026-09-09 is a
   **method rather than a script**: six levels, each saying what it must test and what it must
   not re-test, and **you write one prompt per level on the day** against what the plan says
   has just been built. Three rules from that file are absolute and are repeated here because
   they are the ones a hurried session drops: **one prompt per level**, **a screenshot every
   time a prompt finishes**, and **you do not move to the next level until the current one
   passes properly** — a Level 4 pass on top of a shaky Level 2 measures nothing. Prompts are
   driven in the browser, never through `dispatch` and never from `pytest`.
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
Re-record V&V venv/bin/python -m app.verify.recorded     # after any solver/mesher/verify change
V&V is stale? venv/bin/python -m app.verify.recorded --check   # instant; the CI form
Plan progress  venv/bin/python -m scripts.plan_progress          # per-phase table, from the plan
Re-stamp it    venv/bin/python -m scripts.plan_progress --write  # after moving any status line
Offline only  venv/bin/python -m pytest tests/test_solver.py tests/test_mesh.py \
                  tests/test_geometry.py tests/test_kernel.py tests/test_interrogation.py \
                  tests/test_design_*.py tests/test_render.py tests/test_vision.py
```

**Always `venv/bin/python`, never the system one**, whose numpy/scipy/SQLAlchemy are the wrong
versions and are not the project's. **The venv exists** — a session that believed otherwise once
rebuilt the environment for nothing.

**Both ruff and mypy are clean, and there is no list of errors to expect.** A tolerated error is
one nobody reads, so the next real one hides behind it — if either prints anything, it is yours.
`mypy app/` says *Success: no issues found*, with no exceptions — last checked 2026-09-09. The
file count it prints is whatever `app/` holds that day (355 on 2026-09-09) and is not worth
pinning here; *Success* is the claim. **Clear `.mypy_cache/` before believing a stale answer**: adding an override to
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
  mesh/           gmsh tet and tri meshing, quality metrics, exact primitives for tests
  solve/          Solver + ModalSolver + PlanarSolver ABCs, in-house FEA (solid and plane),
                  CalculiX federation, materials, loads
  simulation/     runner.py — the geometry → mesh → solve job pipeline
  jobs/           JobQueue ABC; ThreadPoolJobQueue today, Celery/RQ later
  design/         a part as a compilable specification, not a tree to edit
  kernel/         the open geometry kernel — OCCT, headless, free, in CI
  catia/          the CATIA backend: 201-operation registry, dispatch, the bridge protocol
  catia_kb/       ~1,600 curated CATIA entries — shipped in code, not in an index
  retrieval/      BM25 over the reference manuals the agent consults
  render/         deterministic hidden-line rendering, section cuts, render diffing
  ai/             agent, tools, prompts, state, resume, tool retrieval, providers, vision
  assembly/       product structure, interface contracts, clash, mass roll-up, locking
  dynamics/       multibody kinematics and reactions
  fatigue/        rainflow and damage, federated to pyLife
  optimise/       optimisation drivers, gradients, honesty rules
  requirements/   the requirements model and coverage
  rules/          design rules, DFM, GD&T
  sheetmetal/     bend allowance, unfold, K-factor, the folded layout
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

1. **One capability, one test file, mirroring `app/`.** 214 files, 7141 tests on 2026-09-09 —
   a figure that rots on every phase, so get today's with
   `venv/bin/python -m pytest --collect-only -q | tail -1` (2 s, opens no database) rather than
   trusting this line. It is here for order of magnitude, not for arithmetic.
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
7. **A new module usually owes something to a registry, and only the full suite knows which.**
   Every lane in E7.5 was green on its own files, `ruff` and `mypy` were clean, and the whole
   suite still came back with one failure: `app/solve/plane.py` opened an `observe.span` named
   `solve.plane` that `app/observe/catalogue.py` did not declare. Nothing in the lane could have
   seen it. Two tests caught it in the right order — one that every span in `app/` is declared,
   and a snapshot of the wired set that makes a human acknowledge the new one rather than letting
   a hook appear unannounced. So: **run the whole suite after integrating parallel lanes, not
   just the touched files**, and when adding a module ask what registry it owes an entry to —
   `observe/catalogue.py`, `solve/registry.py`, `db/models.py`, `api/router.py` are all
   "unregistered means invisible" in their own way.
8. **Give each lane the contract it codes against, not just its file list.** E7.5 ran four lanes
   over one new mesh type; the two that never saw each other's code compiled against a `TriMesh`
   spelled out in their briefs — field names, shapes, units, which properties exist — and
   integrated with no edits. What a brief cannot supply is the seams a lane will *discover*, so
   ask for those back explicitly: three of the four lanes reported a needed change in a file they
   did not own instead of reaching for it, which is how `app/verify/provenance.py` got fixed once,
   by the parent, rather than twice in two incompatible ways.
9. **Expect the tree to be red between lanes landing, and know which red is expected.** Every
   fingerprinted file is shared state: each lane that touches `app/solve/**`, `app/mesh/**` or a
   fingerprinted `app/verify/*` orphans the recorded validation artefact, so the register and
   trust tests fail on staleness until **the last lane in re-records**. That is the guard working,
   not a regression — but it looks identical to one, so say so in the plan rather than
   rediscovering it, and never re-record in the middle expecting it to hold.
10. **`while pgrep -f "python -m pytest"; do sleep …; done` never exits** — the waiting shell's own
   command line contains the pattern, so it matches itself. Two lanes and the parent all lost time
   to it. Match on something the waiter does not contain, or just run the tests: the physics and
   verification files request no database fixture, and `conftest.py`'s schema fixture is requested
   rather than autouse, so those runs do not collide. Only DB-touching runs must be serialised.

## Performance — read the file before you touch anything

**[docs/MAKING_IT_FASTER.md](docs/MAKING_IT_FASTER.md)** is the standing answer to "can we make
this faster". Four things from it that are asked most often and answered wrongly most often:

1. **Measure first, with the spans that already exist.** `app/observe/catalogue.py` declares
   every timed site, and each records fields beside the duration — `mesh.gmsh.wait` versus
   `mesh.gmsh.session` alone answers *is meshing slow or is meshing queued*, and those need
   opposite fixes. A change with no before-and-after from those spans is a rewrite with a
   hopeful commit message.
2. **The model is the cost, and it is not close.** A turn is 4–7 minutes on the workstation;
   every other subsystem here is seconds. Model choice, GPU residency and turns-per-task are
   the only levers that move what a user feels.
3. **"More threads" is not one of the levers.** Requests are sync `def` (so FastAPI already
   threadpools them), jobs already run in a pool, BLAS is already threaded inside `spsolve` —
   and what is left is serialised by the gmsh lock, CATIA's one-in-flight COM surface, or the
   GPU. Adding threads queues the same work behind the same lock, and
   `job_workers × BLAS threads` oversubscribes the cores. Process-level parallelism behind the
   `JobQueue` seam is the designed path.
4. **Three speedups are forbidden outright** because they trade the product for seconds:
   coarsening a mesh silently, skipping a convergence study, and sampling where the provenance
   says measured. Each buys real time and each converts an honest slow answer into a fast
   dishonest one.

## Tools

**Use `rg` (ripgrep), not `grep`**, for code search — it respects `.gitignore`, so it will not
drown you in `venv/`, `node_modules/` or `data/bm25/`. `rg -n "pattern" app/` for a search,
`rg --files -g "*.py"` to list. It is verified working correctly in this repo — re-checked
2026-09-09 by running `rg` and `grep` over the same file and diffing, which agree exactly.
**But a subagent reported it corrupting matched text on the same day** (`catalogue` printed as
`n`), which is the failure mode the root `CLAUDE.md` warns about for a wrapped `rg`, so it is
environment-dependent rather than settled. If a search returns text that does not look like the
file, do not debug the file: re-run the search with `grep -rn` and believe that instead.
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
9. **`gp_Ax2(P, main, X)` has Y = `main × X`.** So the box that fills `u×[0,L], v×[0,W], n×[0,t]`
   from a frame `(u, v, n)` is `BRepPrimAPI_MakeBox(gp_Ax2(P, n, u), L, W, t)` — pass `(P, u, n)`
   and the part is silently mirrored. Same asymmetry `render/project.py` documents from the other
   side; `occt/sheetmetal.py` is the second place it has bitten.
10. **`GC_MakeArcOfCircle(gp_Circ, p1, p2, sense)` returns the *major* arc for both senses** on
   this OCP build, measured twice (`verify/le11_geometry.py`, `occt/sheetmetal.py`). Always use
   the three-point form through an explicit midpoint.

## Sheet metal reaches geometry (`app/sheetmetal/fold.py`, `app/kernel/occt/sheetmetal.py`)

A `SheetMetalPart` builds as a real solid, and the blank and the solid are **one calculation**.
`fold.py` places the part — a frame per flange, a cylindrical sector per bend, holes on their
faces, plus the closed-form volume — importing no kernel, so it stays as cheap to test as the
flat pattern; `occt/sheetmetal.py` builds and fuses it, then cuts the holes.

1. **Both layouts consume one `unfold.tangent_extents` walk.** A leg cannot be 47.4 mm flat and
   48.0 mm folded, which is what makes `missions.py`'s `flat.volume_mismatch_mm3` evidence rather
   than noise. The folded volume differs from `blank area × t` by exactly `Σ θ·t²·w·(0.5 − K)` —
   **zero at K = 0.5** — so the reconciliation is an identity, not a tolerance.
2. **The bend centre is `t + r` beyond the frame plane bending up and `r` below it bending down.**
   Swap them and the part builds, weighs exactly the right amount, and every outside dimension is
   wrong by 2t.
3. **A sector sweeps along the bend (`width_dir`), never along its rotation axis** — for an up
   bend those are opposite. Sweeping the axis mirrors the part about its own root at identical
   volume, face count, length and height.
4. **A hole is cut and a hole in a bend zone is refused**, by the flat pattern's own message from
   the shared `unfold.hole_on_face`. But a part whose *blank* would overlap itself is **not**
   refused: it folds perfectly well, and over-refusal is the failure mode `app/catia/` warns about.
5. **No sheet-metal operation exists in the CATIA registry, deliberately.** The registry is what
   the seat can be told to do, and the COM half is unwritten and unverifiable here — it is `E1` in
   THE QUEUE, not a declaration made on Linux.

## Several authors on one product (`app/assembly/locking.py`)

Master plan 14.5. `ProductStructure` is frozen and safe; the *product* was not, because two
callers could each read the head, each rebuild, and the second erase the first with nothing
raising. `ProductRepository` is now the only way in.

1. **Optimistic first, leases second, and they compose.** Every commit names the revision it was
   written against; a stale one is refused with what moved and who moved it. A lease blocks
   another author's commit that touches a claimed component. **Holding a lease does not excuse a
   stale base** — pinned by a test, because that is the tempting simplification.
2. **There is no clock in the module.** Every call that cares takes `now`. A lease whose expiry
   comes from the machine's clock cannot be tested without sleeping and cannot be reasoned about
   across two processes.
3. **Leases are per component, never per occurrence** — a bolt used forty times is one component,
   and locking `frame/leg.2` would let two authors edit one design down two paths.
4. **A merge refuses by name where both sides changed one component**, and does *not* conflict
   where both made the same change (components compare by value, so rebuild-from-spec stays
   mergeable). A commit that changed nothing is refused.
5. **It is not a distributed lock.** One in-process object: two API workers would each be
   internally consistent and collectively wrong. Persisting it is E15's storage question.

## Requirements, and what a verdict may rest on (`app/requirements/`)

Master plan E11, closed 2026-09-09. A requirement is an assertion with a source, a rationale and
a place in a decomposition graph; `app.design.assertions.Outcome` is reused, never re-declared.

1. **Validation flows *up*.** A requirement with nothing to measure and children in the set takes
   its verdict from them — all met is `PASSED`, any violated is `FAILED`, anything else is
   `UNMEASURED` naming what is open. Without it the one requirement the customer signed is the
   one the report is silent about while everything below it passes.
2. **A derived verdict is not a measurement.** Its evidence basis is `by decomposition`, it has
   its own coverage column, it never counts as `by_measurement`, and the caveat — sound only as
   far as the decomposition is complete, which nothing checks — is printed beside the verdict.
3. **"A requirement nothing checks is a wish" is refused by the *set*, not by the requirement**,
   because the decomposition links point upward and only the assembled set can see them. No
   measure, no `needs`, and nothing decomposed from it is still refused.
4. **`POST /kernel/conversations/{id}/requirements`** is how the product is handed a
   specification: it parses `.kreq`, measures the conversation's part *itself* (a requirement
   verified against numbers the client supplied is a claim about the client), and returns the
   report with coverage, evidence and `scans_needed`.
5. **Every measurement carries its own provenance** (`occt/metrology.measure`): `measured` for
   integrations and traversals, `approximated` for the oriented bounding box — the box is exact
   for a given orientation and the *orientation* is a search — and `unavailable` with a reason
   for a mass with no density. Until 2026-09-09 the base payload attached none, so every
   requirement met by an integrated volume reported its evidence as `unrecorded`.
6. **A requirement's `gap` is what aims a repair** (`sensitivity.aim`), and that is the whole of
   E5.2. Neither package imports the other; they meet at a number. Two traps: `most_influential`
   is a genuine tie on a plate (mass is equally elastic in all three dimensions), so which
   parameter to move is an argument, not a discovery; and a first-order step aimed at a hard
   bound lands *on* it, so a requirement with zero tolerance refuses its own repair by one ulp.

## Analyses available

All verified against closed-form solutions, not recorded output.

1. **Linear static** — `solve/linear_static.py`, `LoadCase`, checked against σ = F/A and δ = FL/AE.
2. **Modal** — `solve/modal.py`, `ModalCase`, checked against the bar `f=(2n−1)/4L·√(E/ρ)`,
   cantilever Euler-Bernoulli modes 1–3, six rigid-body modes free-free.
3. **Buckling** — `solve/buckling.py`, `BucklingCase`, checked against Euler `P=π²EI/(KL)²`.
4. **Thermal stress** — `solve/thermal.py`, checked against the restrained bar `σ = −EαΔT`.
   Takes a uniform `LoadCase.delta_t_k` **or a temperature that varies with position**: every
   function there accepts one number or one value per element, and `LinearStaticSolver.solve`
   takes an optional `temperatures=` array. A *prescribed* field (a formula of position, as
   NAFEMS LE11 gives) and a *solved* one (from conduction) are different sources for the same
   thing — the field is a solver argument and deliberately not on `LoadCase`, which is JSONB on
   the job row and would then hold data the size of the mesh.
5. **Steady conduction** — asked for by `analysis: "thermal-conduction"` with a `thermal_case`
   (migration `b941a651831a`, which also makes `load_case` nullable — a conduction run has no
   fixture, no force and no modulus, and an empty `LoadCase` on that row would put a material
   nobody chose into the provenance of a temperature field). **`CONDUCTION_BACKEND` is its own
   setting**: `SOLVER_BACKEND` names a structural solver, and a deployment that set it to
   `calculix` must not thereby change what answers a temperature field. A `grids > 1` study is
   refused by name (the study assesses peak von Mises stress, which a temperature field has
   none of), the stored field carries `temperatures_k` and never zeroed displacements, and the
   AI result interpreter refuses such a run rather than writing prose about a load case that
   does not exist. `solve/conduction.py`, `ThermalCase`, checked against the linear bar
   profile, the logarithmic tube wall, and the convecting bar's Biot tip temperature
   `T_tip=(T_b+Bi·T_inf)/(1+Bi)`. Dirichlet, convection (Robin) and heat-flux boundaries over the
   existing `Selector` vocabulary. **Conductivity is on the case, not on `Material`** — so
   nothing inherits an unchecked default — and **watts enter the unit system here**, converting
   exactly once, the way density does in the CalculiX deck writer. Steady state only: there is no
   time integration, so "how long until" is not a question it answers.
6. **Plane stress and plane strain** — `solve/plane.py`, `PlaneCase` on a `mesh/planar.TriMesh`
   (tri3/tri6), checked against σ = F/A, δ = FL/AE, `G = E/(2(1+ν))` from pure shear, and Lame's
   thick-walled cylinder. `PlanarSolver` is a **sibling** of `Solver`, for `ModalSolver`'s
   reason — different mesh, different case — but returns the same `SolveOutput`, so the
   verification, provenance and viewer paths need no fork. Three things to know before using it:
   a plane mesh carries **(n, 3) nodes at z = 0** so every existing region selector works
   unchanged, and drifting off that plane is refused rather than projected; `nodal_stress[:, 2]`
   is **0 for plane stress and ν(σxx+σyy) for plane strain**, which is the only difference
   between the two idealisations and is the easy thing to get plausibly wrong; and a load on a
   quadratic **edge** splits L/6 – 2L/3 – L/6, which is *not* the quadratic *face* rule in
   `selection.distribute_force` where the corner functions integrate to zero.
   **Asked for by `analysis: "plane-stress" | "plane-strain"` on a simulation, with a
   `thickness_mm`** (migration `490d3f517ca6`). The job needs a *planar face in z = 0*, not a
   thin solid — `generate_tri_mesh` refuses a solid by name, because meshing its boundary
   succeeds and hands back a closed shell. `thickness_mm` is refused rather than defaulted on a
   plane run and refused rather than ignored on a solid: every plane stress scales with it, and
   silently dropping one leaves the engineer believing it was used.

**A run states its own mesh dependence, and can be asked to measure it.** Every `StaticResult`
carries `mesh_convergence`, always present and defaulting to `single-grid` — `converged` is never
true on one grid however fine it is, because a single solve holds no evidence about its own
discretisation error. Pass `grids: 3` (or more, capped at 5) on a simulation and the runner
solves the same case on successively finer meshes and assesses the peak stress with a Grid
Convergence Index. **The size you give is the coarsest grid**, so a study costs more time and
never more memory; the finest grid's result is what is stored, with the verdict beside it; two
grids are refused because they cannot form a study. Spacing is 1.4 — 1.2 is inside gmsh's
remeshing noise, measured.

Five things that are easy to get wrong and are pinned by tests:

1. **Thermal strain must be subtracted during stress recovery**, not only added as a load. Leaving
   it out reports the stress of a freely-expanding part — wrong sign and wrong size.
2. **Buckling is posed as `−Kg φ = μ K φ`**, not the natural way round: `Kg` is indefinite and the
   generalised symmetric eigensolver needs the positive-definite matrix on the right. `λ = 1/μ`.
3. **The modal mass matrix is integrated analytically** in barycentric coordinates, not with the
   stiffness assembly's four-point Gauss rule — that rule is exact only to degree 2 and tet10's
   `N^T N` is quartic, so reusing it would be wrong by a few percent: plausible-looking, and wrong.
4. **The centroid and the nodes answer different questions, and averaging the first is not the
   second.** `SolveOutput.nodal_stress` is the full tensor in Voigt order **SXX SYY SZZ SXY SYZ
   SZX** (CalculiX's own order, so the adapter never permutes), and it is recovered by evaluating
   tet10's gradient at **each node's own natural coordinate** — `_TET10_NATURAL_NODES`, derived
   from `TET10_EDGES` rather than typed out. The element centroid stays the superconvergent point
   and still feeds the headline peak and the factor of safety. Averaging centroid values onto
   nodes was the previous behaviour and it **under-reads a plate surface in bending by ~25%**,
   which would be survivable except that the error *shrinks with refinement* — so a convergence
   study reads it as a converging answer rather than as an offset, and the GCI looks healthy while
   the number is wrong. Read `nodal_stress` for a stress at a point; it is `None` on a solver that
   does not produce one, and `quantities.stress_component_at` refuses **by name** rather than
   returning a substitute.
5. **A coarse mesh of a curved solid is a smaller part, not a coarser mesh.** gmsh chords a
   curved face, so the meshed volume falls as the element size grows — 7% short on NAFEMS LE10 at
   h = 300 mm. That is a different *problem*, not a coarser discretisation of the same one, and
   feeding it to Richardson extrapolation pollutes the observed order with a geometry trend.
   Compute the exact volume where the shape is analytic and refuse a level that misses it (LE10
   uses 0.5%); `run_study` records the refusal as a level failure, which is the honest outcome.
   Relatedly, **gmsh remeshes rather than refines**, so consecutive levels are not nested and the
   quantity wanders between them — space the levels wide enough (≈1.4× in representative size)
   that the discretisation trend dominates the remeshing noise, and choose the spacing on a rule
   stated *before* the sweep rather than by picking the best-looking triple afterwards.
   And a support that lands on an interior plane needs an **edge** for gmsh to put a node ring
   on: LE10's plate is built as two half-thickness solids fused for exactly this reason, because
   the one-piece extrusion put 2, 38 and 5 nodes under a symmetry support on three meshes and the
   answer scattered by a factor of two while looking like solver noise.

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
3. **`solve.PlanarSolver`** (ABC) — the third sibling, for plane stress and plane strain. Takes a
   `TriMesh` and a `PlaneCase` and returns the *same* `SolveOutput`: the input differs, so it
   cannot be a method on `Solver`; the output does not, so forking `SolveOutput` would fork the
   verification, provenance and viewer paths for nothing.
4. **`jobs.JobQueue`** (ABC) — one method, `submit`. Moving to Celery must not touch routes.
5. **`media.LocalMediaStore`** — content addressing and chunked IO behind a small surface, so an
   S3 store is a swap, not a rewrite.

**Never reach around a seam.** If a route needs to know which solver ran, put it on the job row.

## Validation (`app/verify/`) — the one place a number may not come from you

Decision 3 says verification is the product. `app/verify/` is where that is enforced:
`convergence.py` refuses to state an unconverged number, `provenance.py` binds every result to
the mesh, material, case and solver that produced it, `benchmarks.py` defines what a benchmark
may claim, `nafems.py` is the catalogue of cases, and `register.py` publishes the roll-up on an
unauthenticated trust page.

1. **A benchmark target must be read off a document, never recalled.** `Target` refuses a
   `PUBLISHED` basis with no `source`, and `register.py` republishes that string to readers
   outside this repository — so a remembered figure carrying a plausible citation is
   indistinguishable, on the published page, from one somebody checked. Every citation lives in
   `nafems.SOURCES` with the manual, the section, the NAFEMS publication it credits, the URL and
   the date it was read; a test refuses one typed at the call site. This is not theoretical: a
   figure recalled for FV52 was 4% off the reference row.
2. **`TargetBasis.UNKNOWN` is the honest shape for a case you cannot source** — fully encoded,
   run where possible, `MEASURED`, and never a pass. It *forbids* carrying a value.
3. **A case that cannot run names its blocker in a countable vocabulary** (`nafems.Blocker`), not
   in free text, because "two cases wait on one thing" is the report the catalogue exists to
   produce. A blocker no case backs fails a test, and so does a case with no blocker.
   **And every blocker needs an owner somewhere outside the catalogue** — a master-plan task, or
   a row in THE QUEUE if it needs hardware. Four blockers sat here for a day naming capabilities
   that appeared in no task anywhere, which reads as diligence and is a silent gap: the catalogue
   was describing work nobody was going to do. Before recording a blocker, ask *whose* it is.
   Also check whether the manual that reproduces the target also carries the **geometry** before
   declaring a case blocked on a document nobody has — LE10 was recorded that way and the Abaqus
   entry states all four semi-axes.
4. **The published register never runs anything.** `assert_publishable` refuses a runnable case
   at import, and `PUBLISHED_SUITE` *filters* on `runnable` rather than trusting anyone to
   remember — otherwise the day a blocked case becomes executable is the day the application
   stops booting. A runnable benchmark reaches the register only as a recorded outcome passed to
   `Register.build`.
5. **The page's prose is published beside its counts, and only a test keeps them agreeing.**
   `PUBLISHED_NOTES` is static text and `Summary` is computed, so the day a third case validated
   a note still read "Nothing in this register is validated today" next to a summary saying two
   analyses were. On a trust page that is worse than either statement alone: a reader cannot tell
   which half is stale, so neither is usable, and not having to decide that is the whole value of
   the page. `TestTheNotesDoNotContradictTheNumbers` reads both. Any count written into a test as
   a literal has the same problem from the other side — the blocked-case count was hand-written
   as 3 and made unblocking LE1 look like a regression twice; derive it from `CASES`.
6. **A published result comes from a recorded run, and a recording expires.** The register never
   executes a benchmark (a public unauthenticated route that solves is a denial-of-service tool
   with a nice name), so a case that runs reaches it through
   `data/verify/validation-outcomes.json`. That file carries a fingerprint of every source file
   that decides an answer — `app/solve/`, `app/mesh/`, and `benchmarks/convergence/nafems/
   provenance/quantities` — and a mismatch publishes **nothing**, with the reason, rather than a
   result nobody re-checked. `register.py` and `recorded.py` are outside the fingerprint on
   purpose: an artefact that expires when somebody rewords a note is one people regenerate
   without reading. Re-record with `venv/bin/python -m app.verify.recorded` after touching a
   solver, a mesher or a verification rule; `--check` is the instant CI form and the same
   comparison is a test, so forgetting fails the suite.
7. **The path scrubber replaces the whole string, so it must not fire on a URL.** `_PATH_RE`'s
   drive-letter branch matched the `s:/` inside `https://` until 2026-09-08, which would have
   published every citation as "[withheld: looked like a filesystem path]" — the guard destroying
   what it protects. A drive letter is one character and the lookbehind says so. The same pattern
   is duplicated in `tests/test_trust.py`; change both or neither.

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
7. **The suite is green on `main`** (7,141 passing / 0 failing, 2026-09-09). Twelve failures that
   stood here on 2026-09-08 were fixed that day, and **the breakdown of what they turned out to
   be is worth reading before you assume a red test means a broken feature** — one real defect,
   one tripwire working as designed, one stale artefact, and nine tests that were right about
   what they claimed and wrong about how they checked it. Written up in `KRYOVA_BUILD_PLAN.md`'s
   *Done*, 2026-09-08, where the history belongs.

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
