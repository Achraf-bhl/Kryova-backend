# Windows verification — 2026-10-05 (the budgeted DeepSeek brief)

Brief: `docs/WINDOWS_VERIFICATION.md`, *START HERE — the 2026-10-05 brief*. Seat: the Legion
laptop (Windows 11, Python 3.14.3, CATIA V5-R33 French, local PostgreSQL). Model: `deepseek-flash`
(DeepSeek-V4.1-Flash), hosted, `AI_EFFORT_CHAT=low`, `AI_TOOL_LIMIT=0`.

**Rung reached: Ladder Level 2, passed.** Steps a–d of the brief passed (d after two fixes), and
step e ran Level 2. Levels 4–6 were not started, as the brief says.
**Money: USD 1.87 → 1.83 on DeepSeek's own balance endpoint — USD 0.04 spent.** The ledger,
priced at DeepSeek's *peak* rates, says USD 0.0748; the run was off-peak (half price).

## Stage 0 — the tree

* Both repos had **unpushed local commits** the brief did not expect: backend 17 (the 2026-09-24/25
  seat fixes), frontend 4. `git pull --ff-only` refused both; `git log --cherry-mark` showed none of
  them upstream. Kept, not discarded: backup branches `backup/windows-2026-09-25` at the old heads,
  then **rebased onto `origin/main`**. Conflicts resolved:
  * `requirements.txt` — upstream moved torch/laya to `requirements-laya.txt`; the torch 2.14.0 pin
    (2.5.1 has no CPython 3.14 wheel) now lives there.
  * `tests/conftest.py` — both sides added an autouse fixture; both kept.
  * `app/ai/tool_retrieval.py` — upstream's capped `_decider_request`, fed the de-duplicated context
    (both sides' tests kept).
  * `app/ai/agent.py` — upstream's `LLMBusy` handling, with the once-per-turn `state_block` passed in.
  * `app/catia/dispatch.py` — two imports, both kept. `.env.example` — upstream's block.
  * **Skipped `865d821`** (AI_THINK for Ollama): upstream deleted the Ollama provider (`0d4443d`).
* `docs/verification-2026-09-24/` (114 untracked files, 16.2 MB — the previous seat run's evidence)
  would have been deleted by `git clean -fd`; copied first to `Desktop\Kryova-backups\` (byte count
  verified).

## Stage 1 — free checks

* Backend `pip install -r requirements-dev.txt`: nothing to install. `alembic upgrade head` ran nine
  revisions to `0f0bec54f55e` (both new ones); `alembic check`: no drift.
* Frontend `npm ci`: 456 packages. **`npm audit`: 10 vulnerabilities (9 high, 1 critical)** — not
  triaged here.
* **`tests/test_process_queue.py` alone reproduced the Linux hang**: the crash test FAILED and the
  file never finished inside 300 s. A deadlock in our code against CPython 3.14's
  `ProcessPoolExecutor`: `terminate_broken` holds the non-reentrant `_shutdown_lock` while
  `future.set_exception()` runs done-callbacks on the management thread; ours rebuilt the pool and
  called `shutdown()` on it. Fixed (`aec6a56`); 16 passed in 3.6 s; the break brings the hang back.
* **Full run 1: 23 failed / 12,411 passed / 68 skipped / 1 xpassed, 15 min 22 s.** 11 were the stale
  V&V artefact (expected). The other twelve, each read before either side was touched
  (`a80f95c`, `b99d585`, `44a15aa`):

  | Test | What it was | Fix |
  |---|---|---|
  | test_turn_metrics (21 vs 41 tools offered) | this seat's `.env.local` `AI_TOOL_LIMIT=15` leaking into the suite | conftest resets per-workstation AI knobs |
  | test_mcp (a "mutating" tool ran without consent) | the test used `create_project`, which is deliberately un-gated | uses `set_design_parameter` |
  | test_project_management (`KeyError: 'id'`) | the sidebar row key is `conversation_id` | test |
  | test_study_concurrency (`KeyError: 'verdict'`) | the study lives in `mesh_stats.study` | test |
  | test_verify_convergence (level order) | `assess` orders fine-first by design | compares the whole concurrent study to the sequential one; `as_completed` break caught |
  | test_solver_plane (free mode "not detected") | pinned a limitation ROAD_TO_10 9.5 closed | asserts the refusal |
  | test_memory_governor[60] | the estimate rounds to whole MB (60.38 → 60) | `>=`, mirror of the sibling's `+1` |
  | test_config_and_jobs | three queues ship since 6.4 | test |
  | test_backups (WinError 193) | `subprocess.run` patched after `take_scheduled` bound it as a default | fake injected through the route's call |
  | test_desktop (`\home\a\…`) | native path vs a POSIX literal | test |
  | test_desktop_stage | **our own `8328ab1` bumped pypdf and left the installer lock at 6.4.0** | lock regenerated with the header's uv command; only pypdf changed |
  | test_repository_hygiene | three settings missing from `.env.example` | documented |

* `mypy app/`: three Windows-only errors (`os.sysconf`, `resource.setrlimit`/`RLIMIT_AS`), through
  `getattr` now (`21f4666`). `ruff check .`: ten unsorted migration imports, fixed. `scan_secrets`:
  no findings.
* Frontend (`3af153d`): 8 failed of 1,251 on its first Windows run — one product defect (`readTail`
  reported a **directory as an empty log**: Windows opens it, size 0) and two Windows-only test bugs
  (CRLF workflow file; backslash allow-list keys). 1,251/1,251 after; eslint and tsc clean.
* **The suite started a real CATIA** (DCOM, parent `svchost`, 83 s into run 1) that outlived it.
  Guarded (`3916853`): real `Dispatch`/`GetActiveObject` raise inside tests, and the suite names the
  tests that tried. It named `test_ai_context.py::TestCatiaErrorTranslation` (both tests call
  `Dispatch`). `TestLiveCatia` is now opt-in (`KRYOVA_LIVE_CATIA=1`) instead of switching itself on
  whenever CATIA happens to be open. Run 2 also found the brief's `AI_DAILY_COST_BUDGET_USD=1.50`
  leaking into five `test_ai_org_budget` tests; same fix as `AI_TOOL_LIMIT`.
* **Full run 2** (after the fixes above, before `3916853`): 17 failed / 12,424 passed — 11 stale V&V,
  the five budget leaks, the live-CATIA status test.
* **V&V re-recorded last**: 4/5 agree (FV52, LE1, LE3, LE10), LE11 unconverged — every verdict the
  same as the previous recording; `--check` current.
* **Full run 3** (final tree, V&V re-recorded): **12,441 passed / 68 skipped / 1 xpassed / 0 failed, 15 min 47 s.** The COM guard refused eight attempts (none reached CATIA).

## Stage 1b — G11 without a model

* **9.1 restore (G11.2–3), on a throwaway home** through `local_cluster.prepare` /
  `backups.take_scheduled` / `request_restore`: a real Windows `pg_dump --enable-row-security`
  (172 kB) and `pg_restore --clean --if-exists --single-transaction` over `public`; the marker row
  came back, a `kryova-before-restore-*` dump was taken first, the app role is still `NOSUPERUSER
  NOBYPASSRLS`, 45 tables, `restore-request.json` gone. A truncated dump: `restore-request.failed.json`
  with pg_restore's reason, normal launch, data untouched. **Not run through the installed app.**
* **9.4 (G11.7), two real processes on one Postgres**: B waited 3.0 s on the advisory lock while A
  held it, then was refused — "frame is held by 'ana' for another 595.979 s".
* **9.6 (G11.5)**: `GET /admin/observability` — spans, 7 priced turns, cache hit rate 94.7 %, queue
  depth, bridge operations.
* **9.7 (G11.6), raw JSON-RPC** (no desktop MCP client installed): MCP 2026-07-28 only;
  `server/discover` carries the instructions; a mutating call without consent is refused in the
  toolbox's words; `delete_simulation` is unknown. **`tools/list` gave 36 of 37**: `catia_status`
  disappeared whenever a bridge was connected — fixed (`cef0286`).

## Stage 2 — the key

`.env.local` (gitignored) got one final block: `AI_PROVIDER=deepseek`, `AI_MODEL=deepseek-flash`,
`AI_BASE_URL=https://api.deepseek.com` (the earlier Ollama block's base URL would otherwise have
won), the key, `AI_EFFORT_CHAT=low`, the two budgets, `AI_PRICES` at DeepSeek's **peak** rates read
off its pricing page that day (so the cost budget over-estimates), `AI_INTENT_ROUTER=none`,
`AI_TOOL_LIMIT=0`. Resolved settings were printed and matched. **Mid-test the token budget was
raised from 1.5M to 3M** (stated in the file): it counts cached tokens in full and would have stopped
step d after USD 0.01 of real spend; the priced USD 1.50 cost budget stayed as the money guard.

## Stage 3 — end to end, through the web GUI (production build, 127.0.0.1)

| Step | Prompt (verbatim) | Verdict | Rounds / tools | Prompt tok | Cached | Out | USD (peak est.) | Wall |
|---|---|---|---|---|---|---|---|---|
| a1 | Say hello in French. | PASS | 1 / 0 | 70,476 | 0 % | 186 | 0.0214 | 4.4 s |
| a2 | Say hello in French. (same conversation) | PASS — H7 | 1 / 0 | 70,674 | 99 % | 223 | 0.0009 | 3.9 s |
| b | List my projects. | PASS | 2 / 1 | 143,466 | 98 % | 594 | 0.0025 | 6.5 s |
| c | Make a 50 x 30 x 10 mm plate. | PASS (Level 1) | 11 / 11 | 791,570 | 100 % | 1,740 | 0.0079 | 29.0 s |
| d1 | Analyse this plate in steel: fix the end face at x minimum, put a 100 N load pointing down (-Z) on the end face at x maximum, and tell me the peak von Mises stress. No convergence study. | consent gate (correct) | 4 / 3 | 301,472 | 98 % | 888 | 0.0046 | 15.9 s |
| d2 | Go. | **FAIL — Kryova**, fixed | 3 / 2 | 232,511 | 72 % | 1,746 | 0.0224 | 14.6 s |
| e | Make an 80 x 50 mm steel base plate, 12 mm thick. Centred on its top face, add a round boss 30 mm in diameter whose height equals the plate thickness. Then drill one through hole, centred on the boss, with half the boss diameter, through both boss and plate. Tell me the finished part's volume. | PASS (Level 2) | 19 / 19 | 1,399,845 | 99 % | 2,612 | 0.0152 | 51.0 s |
| **Total** | | | | **3,010,014** | **94.7 %** | | **0.0748** | |

* **a** — French answer, 185 / 222 streamed token deltas, usage on the last chunk (H6). The cache
  (H7): 0 % then 99 %. **70,476 prompt tokens to say hello** — 243 tool schemas on every request (H9).
* **c** — measured 15,000 mm³ (50·30·10), 4,600 mm² (2·(1500+500+300)), 6 faces, centre of gravity
  (0, 0, 5): all match hand arithmetic. `solid_count` honestly UNMEASURED; the 0.015 kg default-density
  mass called meaningless. CATIA window and the product's own `catia_capture_view` agree.
* **d** — closed form for a cantilever: M = 100·50 = 5,000 N·mm, I = 30·10³/12 = 2,500 mm⁴,
  **σ = Mc/I = 10.0 MPa**; δ = FL³/3EI = 0.00813 mm at E = 205 GPa. The run: centroid 8.39, nodal
  surface **9.64 MPa (−3.6 %)**, δ 0.00820 mm (+0.9 %, shear on a stubby beam). Three defects:
  1. The agent answered **"peak 8.39 MPa"** — its tools never carried the governing peak (`fcf97c2`).
  2. The viewer's legend ran **0.4–7.6 MPa** under a **9.6 MPa** headline — the archived colour
     field was the centroid average (`6438075`). Re-run after the fix (no model, same case):
     legend 0.1–9.6 MPa, neutral-axis band visible (`d6-viewer-after-fix.png`).
  3. "**version not recorded**" on every run page (`ce6801f` + frontend `6188b53`).
  Node click (5.80 MPa) agrees with its colour; headline names its basis; dark mode legible.
* **e** — measured **52,241.15 mm³** against 48,000 + π·15²·12 − π·7.5²·24 = 52,241.2; mass 0.4106 kg;
  the boss diameter *derived* from measured face areas and cross-checked through the annular face.

## Token-hungry patterns (the brief asked for numbers)

1. **Every request carries all 243 tool schemas: ~70,000 prompt tokens, even for "say hello".**
   The cache makes it cheap in money (99 % hits after the first call) — but see 2.
2. **`AI_DAILY_TOKEN_BUDGET` counts cached tokens in full.** Step c cost USD 0.008 and used 53 % of a
   1.5M budget; a day is ~20 steps. The priced cost budget is the meaningful limit for a cached vendor.
3. **The cache breaks when the tool offer changes.** After a backend restart the offer went 243 →
   246 (the connected-bridge intersection) and that turn was 72 % cached and cost 0.0224 — the
   priciest turn of the day for 3 rounds.
4. `check_part` was called three times in a row on the plate (steps 8–10, the same part) and calls per
   round stayed ~1.0 (H12): one tool per model round, every round re-sending ~72k tokens.

## Smaller findings, not fixed

* Every new chat creates an empty "New project" (17 such placeholders on this account) — by design in
  `use-agent-chat.ts`; a chat that never builds leaves one behind.
* The "say hello" conversation was auto-titled "CATIA bridge initial project" (from the answer, not
  the question).
* The pad step label reads "Extrusion.1, 0.015 kg" — a default-density mass stated as fact beside an
  answer that calls it meaningless.
* The agent's step-d prose said a clamped plate has "an infinite bending moment" at the edge; the
  moment is F·L = 5,000 N·mm. The conclusion (treat the edge peak as indicative) was right.
* Level 2's automatic "nothing measured this" footer contradicted the answer's face-area measurement
  of the boss — the claim tracker does not count `catia_list_faces` as measuring.
* French (8.6): the greeting, suggestion cards, "Attach", "OLDER" and the whole run page are English.
* `npm audit`: 10 vulnerabilities.

## Screenshots

`00-after-login` · `a1-hello` · `a2-hello-again-cache` · `b-list-projects` · `c0-catia-before` ·
`c1-plate-app` · `c2-plate-catia-window` · `c3-plate-capture-view-in-chat` · `d1-analysis-app` ·
`d2-analysis-result-app` · `d3-viewer` · `d4-viewer-vonmises` · `d5-viewer-node-click` ·
`d6-viewer-after-fix` · `e1-level2-app` · `e2-level2-catia-window` ·
`e3-level2-capture-view-in-chat` · `g10-5-dark-viewer`

## Still open

* G10: offline banner (6), Phase 7 in the browser (7), the rest of French (5).
* G11: the restore through the installed app's routes and relaunch (2), the crash report (4), a real
  MCP client (6), the observability console panel (5).
* H1 at `AI_EFFORT_CHAT=high`, H2–H5, H8, H10, H11, H13 (a, c).
* `KRYOVA_MASTER_PLAN.md` status lines for E15/E7 (Phases 6–8) still say what Linux wrote; the
  tests behind them have now passed here, and the PARTIAL→DONE moves are left to a session that
  re-stamps `plan_progress`.
* User decisions: Phase 10, 9.3, chrono pricing (not attempted).
