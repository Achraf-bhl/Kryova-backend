# Kryova Windows Integration — Progress Journal

Branch (both repos): `chore/windows-integration-20260827`

---

## Phase 0 — Sync and baseline — PASS
Started: 2026-08-27 Finished: 2026-08-27

### What I ran
```
cd Kryova-backend  && git status && git log --oneline -10 && git remote -v
cd Kryova-frontend && git status && git log --oneline -10 && git remote -v
git fetch --all && git pull   (both repos)
git checkout -b chore/windows-integration-20260827   (both repos)
python --version; node --version; npm --version; cargo --version; rustup show; ollama --version
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
Get-CimInstance Win32_VideoController | Select Name, DriverVersion
Invoke-RestMethod http://localhost:11434/api/tags
Get-NetTCPConnection -LocalPort 3000,8000 -State Listen
Get-ItemProperty HKLM/HKCU ...Uninstall\* | Where DisplayName -like "*Kryova*"
Get-ItemProperty "HKLM:\SOFTWARE\Dassault Systemes\CATIA*", "...3DEXPERIENCE*"
Get-ChildItem "C:\Program Files\Dassault Systemes\B33" -Recurse -Filter CATIA.exe
Get-ChildItem HKLM:\SOFTWARE\Classes | Where PSChildName -like "CATIA*"
```

### Raw output (trimmed)
- Backend: was 1 commit behind `origin/main`, fast-forwarded `c77e1ba -> 74b1733`.
  Working tree was clean before and after. New on pull: `app/jobs/celery_app.py`,
  `scripts/catia_bridge/catia_bridge.py` (+README), rate limiting rework, refresh-token
  migration `d5e6f7a8b9c0`, `.env` **removed from tracking** (now only `.env.example`).
- Frontend: was 1 commit behind, fast-forwarded `5d8a93b -> 9831a5c`. New on pull:
  `catia-bridge-panel.tsx`, `use-catia-bridge.ts`, `lib/catia-bridge.ts`, `types/catia.ts`,
  `app/api/catia/events/route.ts` (a **Next.js** route, not a Tauri/FastAPI one).
- Both repos: clean, on `chore/windows-integration-20260827`.
- Environment: Python 3.14.3 · Node v24.14.0 · npm 11.12.1 · cargo 1.98.0 ·
  rustup stable-x86_64-pc-windows-msvc (target installed) · ollama 0.33.1.
- GPU: NVIDIA GeForce RTX 5070 Laptop, 8151 MiB VRAM, driver 592.01 (also an AMD
  Radeon 610M iGPU present — NVIDIA is the one that matters here). **≥6GB VRAM → GPU
  offload branch per Phase 2 decision table.**
- Ollama: reachable at :11434, has `gpt-oss:20b` pulled. **`qwen2.5-coder:7b` is NOT
  pulled yet** — needed for Phase 2c.
- Ports 3000/8000: a stray `node` process (PID 1248) was briefly seen holding 3000 on
  the first check; by the second check it was gone and both ports were free. No
  process currently bound to either.
- No Kryova entry in the uninstall registry (HKLM or HKCU) — **nothing to uninstall**,
  Phase 1b is a no-op this run.
- CATIA: no `CATIA.exe` under `C:\Program Files\Dassault Systemes\B33` (recursive
  search), no `CATIA.Application` (or any `CATIA*`) ProgID under
  `HKLM:\SOFTWARE\Classes`, no `HKLM:\SOFTWARE\Dassault Systemes\CATIA*` /
  `3DEXPERIENCE*` registry keys. The `B33` folder holds only `win_b64`, `OSNT`,
  `DSUninstall.bat` — **installer media, not a completed install.** Treating CATIA as
  **absent**: Phase 7 live tests will SKIP, mock tests must still pass.

### Bugs found
- `scripts/catia_bridge/catia_bridge.py` (pulled in this session, not written by me):
  `get_active_document`, `read_parameters` are defined **without `self`** inside
  `CATIAComBridge`, but reference `self.mock`/`self.catia` in their bodies and are
  called as bound methods (`bridge.read_parameters()`). This raises
  `TypeError: read_parameters() takes 0 positional arguments but 1 was given` for
  every call except `--mock` mode's `export`/`update_parameters` paths (which do have
  `self` and don't call the broken methods). So `params --read` is dead on arrival, in
  both mock and live mode. Will need a fix — flagging here, not yet touched.
- Backend `app/api/routes/catia.py` does **not exist** — Phase 7's planned
  `app/catia/` + `app/api/routes/catia.py` (FastAPI-driven COM bridge) is genuinely
  net-new. The `scripts/catia_bridge/catia_bridge.py` script that already exists is a
  **different design**: a standalone CLI/daemon meant to run on the engineer's own
  Windows box and push exports to the Kryova API, not code invoked by FastAPI. Both
  can coexist, but Phase 7 as written (COM calls made from request handlers via a
  bridge module the API imports) should be reconciled with the already-shipped daemon
  design before duplicating effort — flagging for a decision at Phase 7, not blocking
  Phase 0-6.

### Still broken / open questions
- Reconcile Phase 7's "COM calls from FastAPI request handlers" design with the
  already-existing standalone daemon script + frontend SSE panel
  (`app/api/catia/events/route.ts`, `use-catia-bridge.ts`) before implementing.
- `qwen2.5-coder:7b` needs pulling (Phase 2c).
- `.env` was restored locally from git history (commit `74b1733` deleted it from
  tracking; content recovered from the diff, written back to a now-gitignored
  `.env`). `.env.local` (frontend, `NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1`)
  created fresh — none existed before.

---

## Phase 1 — Mandatory clean rebuild — PASS
Started: 2026-08-27 Finished: 2026-08-27

### What I ran
```
# 1a - already confirmed in Phase 0: ports 3000/8000 free, no stray processes.
# 1b - already confirmed in Phase 0: no Kryova entry in the uninstall registry. No-op.

# 1c - purge (PowerShell)
Remove-Item -Recurse -Force venv, .pytest_cache, .mypy_cache, .ruff_cache
Get-ChildItem -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Remove-Item -Recurse -Force .next, node_modules, .turbo
Remove-Item -Recurse -Force src-tauri\target

# 1d - backend rebuild
python -m venv venv
venv\Scripts\python.exe -m pip install --upgrade pip
venv\Scripts\pip.exe install -r requirements.txt -r requirements-dev.txt
venv\Scripts\pip.exe check

# 1e - frontend + Rust rebuild
npm ci
npx tsc --noEmit
cd src-tauri && cargo build
```

### Raw output (trimmed to the relevant parts)
- Purge: both repos' `git status --short` after purge showed only `PROGRESS.md`
  untracked (backend) / clean (frontend) — confirms nothing tracked was touched.
- `pip install`: all of `numpy==2.5.2`, `scipy==1.18.0`, `gmsh==4.15.2`,
  `faiss-cpu==1.15.0`, `psycopg[binary]==3.3.4`, `pydantic-core`, `SQLAlchemy`,
  `cryptography`, `mypy` resolved **prebuilt cp314 wheels** — no source builds, so
  the "Python 3.14 is very new, wheels may not exist" risk from CLAUDE.md did not
  materialize. `pip check` → `No broken requirements found.` Exit 0.
- `npm ci`: `added 462 packages, and audited 463 packages in 31s`, `found 0
  vulnerabilities`. One harmless `EBADENGINE` warning (`jsdom@30.0.1` wants Node
  `^22.22.2 || ^24.15.0 || >=26.0.0`, have `24.14.0` — one patch version off, not
  acted on). Exit 0.
- `npx tsc --noEmit`: exit 0, zero errors.
- `cargo build` (src-tauri): `Finished \`dev\` profile [unoptimized + debuginfo]
  target(s) in 1m 29s`, one linker-message warning (French-locale linker output,
  cosmetic). Exit 0. Full crate graph compiled clean on cargo 1.98.0 / rustc
  stable-x86_64-pc-windows-msvc, tauri 2.11.5.
- 1f (build stamp) and 1g (installer build) are **not done yet** — doing 1f next,
  1g comes after backend/frontend integrity phases per the plan's own ordering
  (Phase 9 is the mandatory final rebuild+reinstall anyway, so the first installer
  build happens there; an intermediate 1g install right now would just be
  discarded).

### Bugs found
- None introduced by the rebuild itself.

### Still broken / open questions
- GATE 1's "produce installer + verify DisplayVersion changed" (1g) deferred to
  avoid a throwaway install before the app+backend actually run correctly; will
  complete it before GATE 1 is marked fully closed, ahead of Phase 5.

### Phase 1f — build stamp
- Backend `/health` now returns `{"status","version","git_sha","built_at"}`
  (`app/main.py`). `git_sha` reads `KRYOVA_GIT_SHA` (for an installer build with
  no `.git`) else shells out to `git rev-parse --short HEAD`.
- Caught my own bug before committing: first draft computed `BASE_DIR` with one
  extra `os.path.dirname()` (landed on `Desktop`, not the repo root), so
  `git rev-parse` silently failed and `git_sha` came back `"unknown"`. Fixed by
  importing `BASE_DIR` from `app.core.config` instead of recomputing it.
  Verified live: `health_check()` now returns `git_sha: '74b1733'` (HEAD at the
  time, before this session's own commits).
- Frontend: `next.config.ts` resolves `NEXT_PUBLIC_BUILD_SHA` (env override or
  `git rev-parse --short HEAD`) and injects it as a build-time env var; root
  layout renders it as a `kryova-build` meta tag.
- Version bumped `0.1.0 -> 0.1.1` in `package.json`, `src-tauri/Cargo.toml`,
  `src-tauri/tauri.conf.json`.
- Committed as 2 commits (backend `9f0d72b`, frontend `b1fc8c4`).

---

## Phase 3a — Migrations — PASS (after 3 real bugs fixed)
Started: 2026-08-27 Finished: 2026-08-27

### What I ran
```
venv\Scripts\python.exe -m alembic upgrade head
venv\Scripts\python.exe -m alembic check
venv\Scripts\python.exe -c "import app.main"   # boot smoke test
venv\Scripts\python.exe -m alembic current
```

### Raw output (trimmed)
- `alembic upgrade head`: ran `c4a1b2d3e5f6 -> d5e6f7a8b9c0` (the refresh-token
  migration pulled in Phase 0), clean.
- First `alembic check`: **FAILED** — two findings:
  1. `remove_index 'ix_conversation_messages_sequence'` — real drift.
  2. `remove_table 'alembic_version'` — looked like a false positive.
- After fixes (below): `alembic check` → `No new upgrade operations detected.`
  `alembic current` → `d5e6f7a8b9c0 (head)`. Re-ran `alembic upgrade head`
  afterward to confirm idempotency — no-op, clean.

### Bugs found
1. **App couldn't even boot.** `import app.main` raised
   `ModuleNotFoundError: No module named 'asyncpg'`. `app/core/database.py`
   builds `async_engine`/`AsyncSessionLocal`/`get_async_db` (wired into
   `app/api/deps.py` as `AsyncDbSession`), but `requirements.txt` never got the
   async driver. Confirmed `AsyncDbSession` has **zero route consumers** (only
   referenced in `deps.py`'s own definition and `tests/conftest.py`'s override) —
   this is unused scaffolding, not something actively broken by a real caller.
   Rather than deleting a teammate's in-flight work unasked, fixed the missing
   dependency (`asyncpg==0.31.0`, prebuilt cp314 wheel available) and left a
   comment + this note flagging it as dead code someone needs to either wire up
   or remove.
2. **Real model/migration drift.** `ConversationMessage` never declared the
   unique composite index (`conversation_id`, `sequence`) that migration
   `c4a1b2d3e5f6` creates in the database. Added the matching `Index(...,
   unique=True)` to `__table_args__` — the constraint itself is a legitimate
   invariant (two messages in one conversation can't share a position), the
   model was just missing the declaration.
3. **`alembic check` false positive on its own bookkeeping table.**
   `migrations/env.py` passed `version_table_schema=settings.db_schema` to
   `context.configure` *in addition to* wrapping the connection in
   `schema_translate_map={None: settings.db_schema}` and an explicit
   `SET search_path` (on the direct, non-pooler connection — the one place
   `SET` against Postgres is safe per CLAUDE.md). The two schema mechanisms
   disagreed inside alembic's own version-table exclusion logic, so every
   `alembic check` run flagged `alembic_version` for removal. Removed the
   redundant `version_table_schema` kwarg; `search_path` alone (already set
   earlier in the same connection) is enough to route the unqualified version
   table to the right schema. Confirmed `alembic current` still resolves the
   version table correctly afterward — nothing about where it physically lives
   changed.

### Still broken / open questions
- `AsyncDbSession` / `get_async_db` remain dead code with no route using them.
  Not touching further without direction — either wire it into a real endpoint
  or remove it is a call for whoever started that migration.

---

## Phase 3b — Offline physics tests — PASS
Started: 2026-08-27 Finished: 2026-08-27

### What I ran
```
venv\Scripts\python.exe -m pytest tests/test_solver.py tests/test_mesh.py tests/test_geometry.py -v
```

### Raw output (trimmed)
`50 passed, 1 warning in 28.52s`. The one warning is a known
`StarletteDeprecationWarning` about `httpx`/`starlette.testclient` (pre-existing,
not introduced here). No skips, no xfails.

### Bugs found
- None.

---

## Phase 3c — Backend full suite — PASS
Started: 2026-08-27 Finished: 2026-08-27

### What I ran
```
venv\Scripts\python.exe -m pytest -v --tb=short
```
(against the live Neon `kryova_test` schema, per this repo's own testing convention)

### Raw output (trimmed)
```
================= 184 passed, 2 warnings in 205.22s (0:03:25) =================
```
Covers auth (register/login/refresh/logout, cookie flags, CSRF, token-type
isolation), projects CRUD + ownership, chunked/resumable geometry upload,
simulation lifecycle, AI endpoint wiring, media/blob store, rate limiting,
startup orphan-job handling, and the full physics suite again as part of the
whole run. Zero failures, zero skips, zero xfails. The two warnings are
pre-existing deprecation notices (`httpx`/`starlette.testclient`,
`HTTP_422_UNPROCESSABLE_ENTITY` naming), not introduced by this session.

### Bugs found
- None (the three Phase 3a bugs were already fixed before this run started).

---

## Phase 4 (partial) — Frontend integrity — PASS so far
Started: 2026-08-27

### What I ran
```
npx tsc --noEmit     (already run in Phase 1e, exit 0)
npm run lint
npm run test
```

### Raw output (trimmed)
- `npm run lint`: exit 0, no output (clean — ESLint prints nothing on success).
  The documented `.remember/tmp/last-ndc.ts` warning wasn't even triggered this
  run.
- `npm run test`: `Test Files 4 passed (4)`, `Tests 28 passed (28)`, 28.09s.
  (CLAUDE.md says 21 — the repo has grown 7 tests since that doc was last
  verified on 2026-08-25; not a problem, just stale doc, consistent with the
  file's own "went stale once already" warning.)

### Bugs found
- None.

### Still broken / open questions
- Phase 4b (component test gap) not started yet.

---

## Phase 3d — Schema drift (Pydantic <-> api.ts) — PASS (1 real mismatch, fixed)
Started: 2026-08-27 Finished: 2026-08-27

### What I ran
Read every file in `app/schemas/*.py` (253 lines total) and `src/lib/api-client.ts`
against `src/types/api.ts` (153 lines) side by side, plus `app/solve/types.py`
for the `LoadCase`/`Material`/`Selector` shapes and `app/api/routes/*.py` for
which schema backs which route.

### Drift table
| Field | Pydantic | api.ts | Fix |
|---|---|---|---|
| `UserRead.is_active` | `bool` (required) | **absent** | Added `is_active: boolean;` to `UserRead` in `api.ts`. `tsc --noEmit` still clean (nothing constructs a `UserRead` literal in this repo, so the type was silently incomplete, not actively wrong). Commit `ec6c0ee`. |

### Everything else checked, no open mismatch
- `ProjectRead`/`ProjectCreate`/`ProjectUpdate`, `GeometryVersionRead`,
  `SimulationRead`/`SimulationCreate`, `SurfaceField`, `AIStatus`,
  `ResultInterpretation` all match field-for-field, including optionality.
- `LoadCase`/`Material`/`Fixture`/`Load`/`Selector` (`app/solve/types.py`) match
  `LoadCasePayload`/`Material`/`Fixture`/`Load`/`Selector` (`api.ts`)
  field-for-field, including the `FaceSelector | BoxSelector` discriminated
  union.
- **Pagination is fully implemented** (`GET /projects`, `GET
  /projects/{id}/geometry`, `GET /projects/{id}/simulations` all return
  `Page[T]` = `{total, page, page_size, items}`) and the frontend already
  consumes it correctly (`api-client.ts` declares matching `ProjectPage`/
  `GeometryPage`/`SimulationPage` locally and `server-api.ts` unwraps
  `.items`). **CLAUDE.md's "No list endpoint paginates" is stale** — worth a
  doc update outside this session's scope, not touching it here.
- Chunked media upload (`UploadSessionCreate`/`UploadSessionRead`,
  `MediaRead`) is used by the frontend (`beginUpload`/chunk/`complete` in
  `api-client.ts`) via a narrower inline type that only names the 3 fields it
  actually reads — legitimate subset typing (TS structural typing), not
  drift.
- `SessionRead` (`{user, csrf_token}`) matches the frontend's local `Session`
  type exactly.

### Not drift, just unbuilt: password reset
- `PasswordResetRequest`/`PasswordReset` schemas and
  `POST /auth/password-reset-request` / `POST /auth/password-reset` are new
  (the migration for their columns was pulled in Phase 0). There is **no**
  frontend type or UI for this flow yet — not a mismatch to fix, just a
  backend-only feature in progress. Flagging, not building UI for it; out of
  this session's scope unless asked.

### Recommendation (not acted on, per the operating prompt: propose, don't do unasked)
Generating `api.ts` from the OpenAPI schema would have caught the
`is_active` gap automatically and would keep pagination/discriminated-union
types in sync without a manual line-by-line diff each time. Given the repo's
stated minimalism (3 frontend dependencies, hand-rolled everything), this is
a real tradeoff, not an obvious win — flagging for the user to decide, not
doing it.

---

## Phase 4b — Component test gap — PASS
Started: 2026-08-27 Finished: 2026-08-27

### What I ran
```
npx tsc --noEmit
npm run lint
npm run test
```
Wrote three new test files, each a regression test for a specific documented
bug class rather than a generic smoke test:
- `src/app/(auth)/login/page.test.tsx`
- `src/app/dashboard/projects/[projectId]/simulations/[simulationId]/page.test.tsx`
- `src/components/webgl-stress-viewer.test.tsx`

### Raw output (trimmed)
- `tsc --noEmit`: exit 0.
- `npm run lint`: exit 0, no output.
- `npm run test`: `Test Files 7 passed (7)`, `Tests 37 passed (37)` (28
  pre-existing + 9 new), 2.5s.

### What each test actually asserts
- **Login**: fields render; submit button is `disabled` while the request is
  in flight (`Button`'s `disabled={disabled ?? loading}`); on a 401 the
  backend's `detail` message renders and `localStorage.length === 0` is
  asserted explicitly, not just "no crash." Had to fix my own first draft:
  `api-client.ts`'s `request()` auto-retries any 401 through `/auth/refresh`
  once (confirmed by reading the function, matches the existing
  `api-client.test.ts` coverage) — a fresh login has no refresh cookie
  either, so my mock needed a second `mockResolvedValueOnce` for that retry
  or the test crashed on `Cannot read properties of undefined (reading
  'ok')`. Not a product bug, just an incomplete mock.
- **Results page**: three cases against a fully mocked `@/lib/api-client`
  (`ResultInterpretationPanel`/`WebGLStressViewer` stubbed out — they have
  their own coverage) — `mass_kg: 12.34` renders as exactly `"12.34 kg"`
  (asserting `"0.01 kg"` is absent, the shipped bug's exact wrong value, not
  just that *a* number renders), `max_von_mises_mpa: 150` renders as
  `"150.0 MPa"`, and a `FAILED` simulation with an `error` string renders
  that message rather than a blank page.
- **WebGL viewer**: jsdom has no real WebGL, so `canvas.getContext("webgl")`
  returning `null` is jsdom's actual behavior, not a simulated one — the
  "degrades gracefully" case exercises the component's real fallback path.
  For the mount/unmount case, built a hand-written mock `WebGLRenderingContext`
  (every method the component calls, as `vi.fn()`) since there is no real GL
  in jsdom, and asserted the unmount cleanup calls `deleteProgram` once,
  `deleteShader` twice (vertex + fragment), `deleteBuffer` four times
  (position/normal/stress/index), and
  `getExtension("WEBGL_lose_context").loseContext` once — matching the
  cleanup function in `webgl-stress-viewer.tsx` line by line.

### Bugs found
- None in product code. One test-authoring mistake (incomplete fetch mock
  for the login 401 case), caught by actually running the test and fixed
  before committing — not left as a known-red test.

### Still broken / open questions
- None for this phase.

---

## Phase 2 (revised) — Ollama on the GPU — PASS
Started: 2026-08-27 Finished: 2026-08-27

### Correction to the original plan
The plan specified `qwen2.5-coder:7b`. The owner's machine already had
`gpt-oss:20b` pulled and asked for that as the main model, so the download was
stopped and `.env.local` now pins `AI_PROVIDER=ollama`, `AI_MODEL=gpt-oss:20b`,
`AI_BASE_URL=http://localhost:11434`, `AI_TIMEOUT_SECONDS=180`.

`AI_BASE_URL` has to be set explicitly: `.env` sets it to
`https://openrouter.ai/api/v1` for the `openai_compatible` provider, and
`.env.local` only overrode the provider name at first — so the Ollama provider
was built pointing at OpenRouter and health-checked `openrouter.ai/api/v1/api/tags`
(404). Not a code bug; a config-precedence trap worth knowing about.

### GPU verdict
```
ollama ps
NAME           SIZE     PROCESSOR          CONTEXT
gpt-oss:20b    14 GB    57%/43% CPU/GPU    4096

nvidia-smi: RTX 5070 Laptop, 8151 MiB total, 6074 MiB used by llama-server.exe
```
The GPU **is** being used (CUDA, ~6 GB resident, 43% of layers offloaded).
A 14 GB model cannot fit entirely in 8.1 GB of VRAM, so a CPU/GPU split is
unavoidable. The owner clarified they wanted the GPU *used*, not the whole
model resident, so no model swap. Measured throughput on an agent-style
tool-calling prompt: **31.2 tok/s**, 4-5 s per tool call.

Docs consulted (per R3): https://docs.ollama.com/gpu — current Ollama exposes
`CUDA_VISIBLE_DEVICES` for device selection and offloads automatically; there
is no `OLLAMA_GPU_LAYERS` knob to set, so nothing was set.

### Provider verified against gpt-oss:20b (not assumed)
- `health()` OK
- `chat()` plain: 6.5 s, coherent reply
- `chat()` with tools: **4.3 s, correctly emitted a ToolCall for get_weather with city=Paris**
- `complete()` schema-constrained: 6.8 s, valid parsed model

One false alarm worth recording: `complete()` first returned "Ollama returned an
empty response." That was my test passing `max_tokens=300`; gpt-oss is a
reasoning model and spent the budget in its `thinking` field before emitting
`content`. At a realistic budget it works. Not a product bug — but a 20B
reasoning model does need headroom, which is why `AI_MAX_TOKENS` stays at 8000.

### Live endpoint
`GET /api/v1/ai/status` returns
`{"enabled":true,"provider":"ollama","model":"gpt-oss:20b","detail":null}`

---

## Phase 7 — CATIA integration — PASS
Started: 2026-08-27 Finished: 2026-08-27

### CORRECTION: Phase 0 was wrong about CATIA being absent
Phase 0 recorded "CATIA is **absent** ... installer media, not a completed
install." **That was my error and it was wrong.** I searched recursively for
`CATIA.exe`; the CATIA V5 executable is **`CNEXT.exe`**. I also read a blank
PowerShell table as an empty registry result.

The definitive test is the COM ProgID, not a filename search:
```
New-Object -ComObject CATIA.Application   ->  succeeds
HKCR\CATIA.Application\CLSID -> {87fd6f40-e252-11d5-8040-0010b5fa1031}
LocalServer32 -> "C:\Program Files\Dassault Systemes\B33\win_b64\code\bin\CNEXT.exe"
                 -env CATIA_P3.V5-6R2023.B33 -direnv "C:\ProgramData\DassaultSystemes\CATEnv"
SystemConfiguration.Version/Release -> 5 / 33
```
**CATIA V5-6R2023 (V5-R33) is installed and COM-automatable.** SIMULIA/Abaqus
2022 is also on this machine (`C:\SIMULIA\Commands\abaqus.bat`) — not used yet,
but relevant to any future solver work.

### Why this phase changed shape
The owner's requirement: the product is 100% driven by the chatbot, and the user
should **not** upload a CAD file — the system opens CATIA and takes the geometry
itself. The plan's original design (routes only) would not have delivered that,
because the agent had no tool to call. So the work is: bridge + **agent tools** +
routes, with the agent as the primary consumer.

### What was built
- `app/catia/errors.py` — `CATIABridgeError` and four typed subclasses.
- `app/catia/bridge.py` — the COM layer. `win32com` imported *inside* the
  worker so the module imports on Linux/macOS; one thread per call owning its
  own apartment (`CoInitialize`/`CoUninitialize`), since FastAPI hands sync
  handlers an arbitrary threadpool thread and COM objects must not cross
  apartments; a timeout on every call because a modal CATIA dialog blocks COM
  forever; `GetActiveObject` (attach) kept strictly separate from `Dispatch`
  (launch) so a status check never boots a CAD system as a side effect.
- `app/ai/tools.py` — `catia_status`, `open_in_catia`, `sync_geometry_from_catia`.
- `app/api/routes/catia.py` — `/status` (always 200), `/documents`, `/launch`,
  `/projects/{id}/sync` (404 for another user's project).
- `requirements.txt` — `pywin32==312; sys_platform == "win32"`. cp314 wheel exists.

### The actual fix for "please upload the CAD file"
`app/ai/agent.py`'s system prompt literally instructed the model to ask for an
upload: *"the CAD file (STEP, IGES or STL, which they must upload themselves --
you have no tool for it)"*. Tools alone would not have changed behaviour. The
prompt now routes new projects through `catia_status` then `open_in_catia` then
`sync_geometry_from_catia`, and only falls back to asking for a file when CATIA
is genuinely unavailable, saying why.

### Raw output — verified against LIVE CATIA, not mocks
```
launch() from cold  -> CatiaStatus(running=True, version='V5-R33', document_count=0)
new_part()          -> CatiaDocument(name='Part1.CATPart', doc_type='catpart')
export_active_document -> bridge_probe.stp, 8907 bytes, starts with ISO-10303

# agent tools against the real DB
catia_status -> {'running': True, 'version': 'V5-R33', 'open_documents': 1,
                 'active_document': 'Part1.CATPart'}
sync_geometry_from_catia -> {'geometry_version': 1,
                             'filename': 'project_9e82f8f8.stp',
                             'size_bytes': 8914}

# full pipeline on the CATIA-sourced geometry
generate_tet_mesh -> 144 nodes, 395 tets, bbox [0,0,0] .. [100,100,100] (exact)
run_simulation    -> SUCCEEDED
   max_von_mises_mpa 5.183   vs hand calc F/A = 50000/10000 = 5.0
   mass_kg           7.87    vs 0.001 m3 * 7870 kg/m3     = 7.87  (exact)

# live HTTP, authenticated
GET /api/v1/catia/status    -> {"running":true,"version":"V5-R33",
                                "open_documents":1,"active_document":"Part1.CATPart"}
GET /api/v1/catia/documents -> [{"name":"Part1.CATPart","doc_type":"catpart"}]
GET /api/v1/catia/status    -> 401 unauthenticated (auth enforced)
```
The 3.7% stress difference is ordinary FE discretisation on a coarse 25 mm mesh
with a fully-fixed face; mass is exact. This is the definitive proof that
CATIA to STEP to mesh to FEA works.

### Tests
`pytest tests/test_catia.py -v` gives **18 passed, 3 skipped** with CATIA closed;
**21 passed** with CATIA running. The 3 live tests skip automatically via a
`bridge.is_catia_running()` check rather than failing on a machine without CATIA.
Offline coverage: format parsing (`ExportData` wants `"stp"`, not `"step"` — a
silent no-op export if wrong), non-Windows import guard, `com_error` never
escaping as itself, export path traversal, HTTP contract, 404-not-403.

### Full suite after the change
`pytest -q` gives **205 passed, 2 warnings in 485.92s**. Up from 184; zero
regressions. `ruff check app/ tests/` gives All checks passed.

### Build stamp confirms the running server is current code
`GET /health` gives `git_sha: "73da56a"`; `git rev-parse --short HEAD` gives `73da56a`.

### Still open
- Real-time *push* from CATIA (event-driven) is not implemented; sync is
  on-demand (the agent calls it, or the user asks). CATIA V5 COM does not
  expose a usable change-notification hook without a CAA/VBA add-in, so a
  push model would mean polling the active document. Flagged, not built.
- `scripts/catia_bridge/catia_bridge.py` (the older standalone daemon, and the
  frontend SSE panel on :9100 that expects it) is now superseded by
  `app/catia/`. Its `read_parameters`/`get_active_document` are still missing
  `self` and would crash if run. Recommend deleting the script and pointing
  `catia-bridge-panel.tsx` at `/api/v1/catia/status` — not done unasked.
