# Kryova — the plan to build machines from a conversation

**Version 3 (restructured 2026-09-08). Content is version 2 (written 2026-09-05); the structure
is new: no tables, no status board, one flat numbered task list per phase, and a status comment
on every task.** This is the controlling document for what Kryova is trying to become.

The goal, unchanged, and the point of everything below:

> **A system an engineer can talk to that designs, analyses, validates and documents a complete
> working machine — a stamping press, a gearbox, a conveyor, a robot arm, a motorcycle chassis —
> to a standard a licensed engineer can review, sign, and have manufactured.**

Not a chatbot that models a bracket. Not a gear generator. A machine — with the product around it
that real users, real teams and real money can actually use.

Companion documents:

1. [KRYOVA_BUILD_PLAN.md](KRYOVA_BUILD_PLAN.md) — the working queue. One batch at a time, green
   before the next. Short-term truth; this file is where it is going.
2. [KRYOVA_CAPABILITY_ROADMAP.md](KRYOVA_CAPABILITY_ROADMAP.md) — the 2026-09-03 capability audit
   this plan grew out of.
3. [KRYOVA_STATE_OF_THE_PROJECT.md](KRYOVA_STATE_OF_THE_PROJECT.md) — honest current state.

---

## How to read and maintain this file

1. **Every phase is a numbered task list.** `##### Phase E1 #####` … `##### Phase P10 #####`.
   Engineering Track phases are `E1`–`E23` (plus `E17.3`); Product Track phases are `P1`–`P10`.
2. **Every task carries a status line beginning `>`.** Exactly five forms, and nothing else:
   - `> NOT STARTED`
   - `> IN PROGRESS (since YYYY-MM-DD)`
   - `> PARTIAL (YYYY-MM-DD) — <what shipped>. Tested by: <files>.`
   - `> DONE (YYYY-MM-DD) — <what shipped>. Tested by: <files>.`
   - `> BLOCKED — <what blocks it>.`
3. **A task is `DONE` only when a test proves it.** The status line names the test file or the CI
   job. A claim with nothing to open is not a status, it is an intention.
4. **When a task finishes, edit its status line in the same commit as the work** — never as a
   follow-up that can be forgotten.
5. **When every task in a phase is `DONE`, add the phase-complete line** immediately under the
   phase heading: `> ✅ PHASE COMPLETE (YYYY-MM-DD) — all tasks done and tested.` A phase with one
   task still open does not get it, however much has shipped — **unless the marker names that task
   and says why it stays open**, which is the only honest way to close a phase whose last residual
   is a scope decision (E1 tasks 3 and 4 were, until 2026-09-14 and 2026-09-15) or a wait on
   hardware (E3 task 5). "Complete" and
   `PARTIAL` ten lines apart, with nothing connecting them, leaves a reader unable to tell which
   of the two is stale — and that is worse than either statement alone. Enforced by
   `TestThePlanKnowsItsOwnProgress` in `tests/test_repository_hygiene.py`.
6. **Never delete a status; supersede it.** This file is current state. The build plan's *Done*
   section is the history — one line lands there for every status change here that isn't
   `NOT STARTED`.
7. **A task whose proof names the product waits for its gate** (see *Stop gates*). Inside a
   stretch the offline suite is the whole of the evidence, and the status says so:
   `pytest green, end to end pending G<n>` is honest; a bare `DONE` on unit tests is not.
8. **A session starting Kryova work reads this file first.** It is the answer to "where were we?".
9. **The progress block below is generated from those status lines, and is the only number in this
   file nobody may type.** `venv/bin/python -m scripts.plan_progress --write` regenerates it;
   `--check` says whether it has gone stale. A percentage written by hand is wrong from the next
   status change onward and says nothing when it is — the same defect as the validation register's
   hand-written blocked-case count, which made unblocking a case look like a regression twice.
   Regenerate it in the commit that moves a status, the way the status itself moves with the work.

---

## Progress — counted from the status lines, never typed

<!-- progress:begin -->
**Measured 2026-09-16** by `venv/bin/python -m scripts.plan_progress`, which reads the status
line under every task in this file and the engineer-month figures in Part 4. Do not edit the
block by hand — regenerate it with `--write`, and `--check` says whether it has gone stale.

| Track | Phases complete | Tasks | Effort |
|---|---|---|---|
| Engineering — E1–E23 | 15/24 | 119/133 = 89% | 136/151 eng-months = 90% |
| Product — P1–P10 | 6/10 | 52/62 = 84% | 31/38 eng-months = 82% |
| **Programme** | 21/34 | 171/195 = 88% | 167/189 eng-months = 88% |

Weighting: `DONE` 1, `PARTIAL` ½, `IN PROGRESS` ¼, `BLOCKED` and `NOT STARTED` 0. The half is
a convention rather than a measurement, so read the per-phase rows, not the headline.

| | Phases |
|---|---|
| ✅ complete | E1, E2, E3, E4, E5, E6, E7, E10, E11, E12, E14, E16, E17.3, E19, E20, P1, P2, P3, P5, P8, P10 |
| in flight | E8 92%, E9 75%, E13 88%, E15 80%, E17 83%, E18 50%, E21 58%, E22 62%, E23 75%, P4 86%, P9 57% |
| nothing finished yet | P6, P7 |

**What this is not.** It is progress against the plan, not against a shipped product. Almost
every `DONE` above is proven by the offline suite on Linux; the stop gates in Part 2 are what
turn that into an end-to-end claim, and none of them has run yet. Two figures inside the plan
are also deliberately not progress: E1.3's operation count is scaffolding depth (it says so),
and roughly 36 of the remaining engineer-months are the work Part 4 marks as needing a real
mechanical engineer, which does not compress.
<!-- progress:end -->

---

## Part 0 — The decisions that shape everything below

Most of this plan is consequence. These decisions are the plan. The first five are the
engineering spine; the last three are the product spine.

### Decision 1 — OCCT is the internal engine; CATIA is the delivery target

**The most important decision in the document; it reverses the project's centre of gravity.**

Amended 2026-09-05. It used to read "CATIA is one backend among several", which invited a reading
it was never meant to carry: that Kryova might one day be sold without CATIA. It will not be.
**Every customer holds a CATIA licence — that is the market.** OCCT is the engine the agent
*designs and iterates in*, because a design loop needs tens of rebuilds a minute and a seat gives
one every few seconds; CATIA is where the result **lands**, because that is where the customer
works.

Three consequences, all binding:

1. **No customer-facing OCCT surface.** No "choose your kernel" in the UI, no CATIA-free product
   story, no marketing that implies one.
2. **Operations are added to the OCCT backend only when a test, a sensitivity sweep or an
   optimisation needs one** — never for coverage's sake. The 108/201 figure is scaffolding depth,
   not product progress, and reading it as progress is how it got to 108.
3. **The deployment is hybrid**: part of the backend runs on Kryova's server, part on the
   engineer's machine alongside CATIA. OCCT installs **silently, as an ordinary dependency**
   (`cadquery-ocp`, a Python wheel with no executable, no service and no shortcut — it cannot
   appear as a separate application, and that is structural rather than arranged). The honest
   cost is **~805 MB**: 166 MB of OCP plus 639 MB of VTK, a *hard* requirement — the compiled
   extension links VTK directly and will not load without it.

What has **not** changed is why the engine exists at all. Without it there is no CI for geometry,
no test that runs without a licensed seat, and no sensitivity or optimisation, because those need
many rebuilds and a seat cannot give them.

What CATIA-only does to every ambition:

1. 10⁵–10⁶ operations for a machine — at ~1 s per COM round trip, **28+ hours of pure latency**.
2. Test the geometry in CI — impossible; CI has no CATIA and never will.
3. Deterministic, reproducible builds — depend on a seat's version, language, install and options.
4. Many users, many designs at once — one bridge = one user; licences are the ceiling.
5. Optimisation, DOE, parameter sweeps — each of 500 candidates costs a workstation-hour.
6. An agent that iterates freely — every experiment is billed against a human's machine.

`app/design/` already made the necessary move without naming it: a design is a **specification
compiled to a call plan**, and `compile.py` is a compiler with a pluggable target. Nothing about a
`Plan` is CATIA-specific except the operation vocabulary it happens to emit. So: **add a second
compilation target — [Open CASCADE Technology](https://dev.opencascade.org/) (OCCT), the
open-source B-rep kernel behind FreeCAD — and make it the primary one.**

What that buys, none of it obtainable otherwise:

1. **Geometry in CI.** Every design in the repository rebuilds and re-asserts on every commit,
   headless, free, in seconds. No CAD company ships this because their kernel is their product;
   ours is a dependency.
2. **Throughput.** In-process kernel calls instead of COM round trips — 500 candidates as a coffee
   break rather than a fortnight.
3. **Determinism.** One pinned kernel version, one locale, one tessellation tolerance, in a
   container we control.
4. **No licence ceiling.** Optimisation, self-correction loops and the mission ladder become
   affordable, because the marginal geometry operation costs CPU rather than a seat-hour.
5. **A real answer to topological naming.** OCCT ships `TNaming_Selector` and, since OCCT 8.0
   (May 2026), `BRepGraph` — a graph representation of B-rep topology with bidirectional traversal
   and history tracking.

CATIA does **not** go away. It becomes what it should always have been: **the delivery and
interop backend**. The same `Plan` targets both. A design is authored, iterated, simulated and
verified against OCCT thousands of times, and materialised into a real CATPart once, at the end,
when a human wants one. This also makes the old `A1` blocker tractable: validating 201 operations
against a seat stops being a blocker on everything and becomes a **conformance suite** — the same
plan built on both kernels, geometry compared, unattended.

**Cost:** OCCT is a large, old, idiosyncratic C++ library; the surface is enormous and parts of it
are thinly documented. Budget real time for kernel-facing work and expect the first six weeks to
feel slow. Still cheaper than any alternative.

### Decision 2 — Physics is federated, never re-implemented

`app/solve/` is ~1,300 lines of hand-written FEA — correct, verified against closed-form
solutions, and a **component solver**: no contact, no plasticity, no large deformation, no shells
or beams as authored elements, no dynamics, and a direct solve that will not survive an assembly.
Writing the missing 90% is a decade of specialist work that has already been done, validated and
given away:

1. **[CalculiX](http://www.calculix.de/)** (GPL) — the workhorse. Abaqus-compatible `.inp` decks,
   25 years of validation, nonlinear, contact, plasticity, modal, buckling, thermal, dynamics.
2. **[code_aster](https://code-aster.org/)** (GPL, EDF) — fracture, cyclic plasticity, the things
   a nuclear utility needs and validates.
3. **[Elmer](https://www.csc.fi/web/elmer)** (LGPL) — multiphysics, FSI.
4. **[OpenFOAM](https://openfoam.org/)** (GPL) — CFD, late and only when genuinely needed.

**Kept, emphatically:** `solve/loads.py`, `solve/selection.py`, `solve/materials.py`. The
load-case vocabulary and the *geometric selector* abstraction (a face named by geometry, never by
id) are the real intellectual property — they are what the agent drives and what makes a load case
survive a re-mesh. The hand-written solver stays as **fast path and oracle**: any linear static
case must agree between it and CalculiX, and a disagreement is a bug in the integration.

### Decision 3 — Verification is the product, not a feature of it

An unvalidated simulation is a hypothesis with a colour map. Kryova's distinguishing claim is not
"it designed a part" — it is **"here is the part, here is what was claimed about it, here is the
evidence for each claim, and here is what remains unverified."** Assertions, provenance,
convergence reporting, the mission ladder, and the refusal to state an unsupported number are not
quality-of-life work; they are the reason a licensed engineer would put their name on the output.
The codebase already practises this locally (a mock mass says it is a mock; an unmeasured
assertion is never a pass); the plan extends it, never erodes it.

### Decision 4 — Free and open, with the licence consequences taken seriously

Every external dependency in this plan is free. It keeps the system deployable offline, keeps the
marginal cost of an experiment at zero, and avoids a vendor deciding our roadmap. The obligation
it carries:

1. **GPL solvers (CalculiX, code_aster, OpenFOAM, gmsh) are invoked as separate processes across a
   file/CLI boundary** — write an input deck, run the binary, read the results. Settled practice,
   and the right architecture anyway (solvers crash; a crash should kill a subprocess, not the
   API).
2. **LGPL libraries (OCCT, PlaneGCS, OpenCAMLib) stay dynamically linked and replaceable.**
3. Neither rule may be "optimised" away.
4. `data/bm25/` holds ~450 MB of tracked Dassault Systèmes PDFs — a separate, unresolved copyright
   hazard. Resolve before the repository is published or widely cloned (history rewrite).

### Decision 5 — Honest scope: which machines, and what "designs it" means

Nobody designs a motorcycle from zero. Small manufacturers buy the engine, the suspension, the
brakes — and design the **chassis, packaging, ergonomics, bodywork and integration**. That is what
the industry actually does, and it is a very large business. So the target, precisely:

> **Kryova designs, analyses and documents the structural, kinematic and packaging content of a
> machine, integrating bought-in functional components, to a standard a licensed engineer can
> review and sign.**

The machine classes, in tractability order — the **mission ladder**, each rung a permanent
regression test:

1. **M1 — machined bracket.** Nothing hard. The "hello world". Possible from Era I.
2. **M2 — welded frame / bench.** Weld sizing, fatigue at joints. Era III.
3. **M3 — sheet-metal enclosure.** Unfolding, bend allowance, DFM. Era IV.
4. **M4 — gearbox.** Gear geometry, bearings, tolerance stacks, lubrication. Era IV.
5. **M5 — sheet-metal stamping press.** Force path, frame stiffness, die set, drive, guarding.
   Era V.
6. **M6 — belt conveyor system.** Long assemblies, standard parts, modularity, layout. Era V.
7. **M7 — 6-axis robot arm.** Kinematics, dynamic loads, stiffness under motion. Era VI.
8. **M8 — motorcycle chassis + swingarm.** Fatigue under real duty cycles, MBD loads,
   homologation. Era VI.
9. **M9 — full vehicle chassis programme.** 10³–10⁴ parts, teams, change propagation. Era VII.

**Never in scope:** unattended sign-off on a safety-critical machine. The honest product is *"does
80% of the engineering in a tenth of the time; a licensed engineer signs."*

### Decision 6 — One platform: web and desktop share one frontend, one API, one auth

The frontend already exists and already made the right structural choices: Next.js 16 App Router +
React 19 + Tailwind v4, **wrapped in Tauri 2 for the desktop**, with a deliberate
three-dependency runtime (`next`, `react`, `react-dom`), a hand-written WebGL 1 stress viewer, and
237 passing tests. The desktop app is not a second product: it is the same frontend with two extra
powers — the CATIA workstation bridge runs beside it, and signed auto-update ships it. Every
capability in this plan surfaces through this one frontend; nothing gets a separate admin web app
or a separate viewer product. Any new frontend dependency is a named decision in this plan, not a
convenience import.

### Decision 7 — Security and tenancy are architecture, not a hardening pass

1. **Sessions**: short-lived access tokens; refresh tokens **rotated on every use**, grouped into
   **families per device**, with **reuse detection** that kills the whole family — a stolen refresh
   token becomes a detectable event instead of a 30-day capability. Absolute session expiry.
   Per-device session list with individual and global revocation.
2. **Tenancy**: organisations own projects; users belong to organisations with roles. Application
   code scopes every query, and **PostgreSQL Row-Level Security is the safety net underneath**.
   Tenant context reaches RLS via **`SET LOCAL` inside an explicit transaction only** —
   transaction-scoped, discarded at COMMIT/ROLLBACK, and therefore safe under transaction-pooling
   PgBouncer. This is the *one* disciplined exception to this repo's hard "never `SET` against the
   pooled endpoint" rule, and the rule exists precisely because a *session-level* `SET` once leaked
   between clients here. `SET LOCAL` outside a transaction, or any statement-mode pooling,
   re-opens that hole — both forbidden and tested against.
3. **Cross-tenant access returns 404, never 403** — already the codebase rule; RLS makes it
   enforceable rather than conventional.
4. **Admin power is bounded and recorded**: impersonation carries *both* identities in the token,
   is read-only by default, and every admin action lands in an append-only audit log.

### Decision 8 — Everything a user attaches is data to understand and never instructions to obey

Users will attach PDFs, spreadsheets, photos, drawings, STEP files, supplier datasheets. Two
commitments:

1. **Kryova reads them properly.** A local, free document-understanding pipeline (Docling /
   MarkItDown class, plus `ezdxf` for DXF and the geometry pipeline for CAD formats) turns
   attachments into structured, provenance-tagged content the agent can actually use — tables stay
   tables, dimensions stay numbers with units.
2. **Kryova never obeys them.** Document-borne prompt injection is a documented attack class
   ("ignore your instructions" hidden in white text on page 12 of a datasheet). Extracted content
   enters the model as quoted, provenance-tagged *data*, is never concatenated into the system
   prompt, and no tool call may be justified solely by text found inside an attachment without the
   user seeing that justification.

### The four defects v2 recorded, and where they are owned

1. **Auth was single-device by design accident.** `users.refresh_token_hash` was one column on the
   user row — a second login invalidated the first device's session silently. Owned by P1, fixed.
2. **CI was dishonest about the database.** The frontend *does* have CI (added 2026-08-29). The
   real gap was here: the **backend** workflow ran the whole suite with `TEST_DATABASE_URL` unset,
   which `tests/conftest.py` silently answers with in-memory SQLite — so one green tick stood for a
   Postgres suite that had never touched Postgres. Owned by P9, fixed.
3. **`SECRET_KEY` defaults to `"changeme"` and the server starts anyway.** Owned by P1 with a
   startup refusal.
4. **`pythonocc-core`'s coverage of OCCT's OCAF/TNaming layers was not confirmed by its docs.**
   v1's topological-naming plan leaned on it. Phase E1 opened with a spike to verify — which found
   the binding is not on PyPI at all and switched the project to `cadquery-ocp`.

---

## Part 1 — Where the code actually is (measured 2026-09-05, both repos)

### Backend (`Kryova-backend`)

1. `app/catia/ops/` — 201 operations, 11 domains, one declarative registry.
2. `app/design/` — spec · params · names · compile · execute · diff · assertions · correct
   (338 tests, all offline, <1 s).
3. `app/solve/` — linear static tet4/tet10 · modal · buckling · thermal stress · loads · selection.
4. `app/mesh/` — gmsh 4.15.2, industrial grade, keep.
5. `app/retrieval/` — BM25 over 21 indexed CATIA/FEA manuals (~4,900 passages).
6. `app/catia_kb/` — ~1,600 curated CATIA entries; query expansion, term lookup, per-turn brief.
7. `app/ai/` — agent · tools · prompts · state · resume · providers (pluggable, Ollama default).
8. `app/media/` — content-addressed blob store, chunked IO — the provenance substrate.
9. `app/api/routes/` — auth · projects · geometry · simulations · media · materials · ai · catia.
10. `app/models/` — User · Project · GeometryVersion · SimulationJob · Media · Conversation · catia.

Full suite: **2,849 passed, 4 skipped, ~223 s.** ruff clean. mypy clean.

Auth as it stands: JWT HS256 access + refresh with a **type claim that is checked** (a refresh
token cannot pass as access — good), bcrypt with prehash, cookie sessions, password reset with
hashed one-time tokens, `is_active`.

### Frontend (`../Kryova-frontend`)

1. `src/proxy.ts` — Next 16 middleware, cookie route gate.
2. `src/app/(auth)/` — login, register.
3. `src/app/setup/` — health-check + onboarding wizard.
4. `src/app/dashboard/` — the product surface.
5. `src/components/` — agent-chat · agent-step-list · webgl-stress-viewer (hand-written WebGL 1) ·
   geometry-preview · catia-bridge-panel · result-interpretation · markdown-message ·
   error-boundary · skeleton · mesh-orb.
6. `src/lib/` — api-client · server-api · chunked-upload · conversation-{resume,events,transcript} ·
   load-case · surface-field · poll-schedule · markdown · format.
7. `src-tauri/` — Tauri 2 desktop shell.

**237 tests, ~7 s, clean; lint and `tsc` clean.** Three runtime dependencies by explicit doctrine.
The WebGL viewer, chunked upload and conversation-resume are hand-rolled and tested — genuine
assets to extend, not to replace.

### What is genuinely strong, both sides

The operation registry (one declaration, everything generated); the design IR; offline test
discipline; the content-addressed store; the honesty conventions; a frontend that is small, fast,
typed and tested; a working desktop shell.

---

## Part 2 — Two tracks, one ladder

1. **Engineering Track — phases E1–E23 in eight eras.** The machine-building capability, and
   (Era VIII) what has to be true outside this repository for the machine to be worth building.
2. **Product Track — phases P1–P10.** The platform around it: identity, tenancy, admin, files,
   frontend experience, viewer scale, desktop, billing, delivery, trust.

They run **in parallel** and gate each other only where stated. The mission ladder gates both: a
mission is not "done" when the geometry is right — it is done when a signed-in user of the right
organisation can run it end to end in the product, see every number's provenance, and export the
package.

### Stop gates — where coding stops and the product is tested for real

**The expensive verification is batched, and the batches have names.** Driving a real conversation
through the local model against a real CATIA seat is the only test that has ever found the defects
that matter here — every one of the seven found on 2026-09-05 was invisible to the offline suite
and left the geometry looking plausible. It is also four to seven minutes per prompt on this
machine: a 20.6 GB model on an 8 GB card runs 70% on the CPU, and while it runs the GPU is
occupied. Doing that after every commit spends the night on the model rather than on the product.

So the work runs in **stretches** separated by **gates**:

1. **Inside a stretch — `pytest` only.** Write the tests with the work and commit them together,
   run `pytest`, `ruff check app/ tests/` and `mypy app/`, and verify each new guard by breaking
   the thing it guards. **Ollama is stopped** for the whole stretch so the card is free. No chatbot
   run, no CATIA seat, no screenshots.
2. **At a gate — the whole product, once, properly.** Ollama back up and **confirmed on the GPU**
   (`ollama ps`, `nvidia-smi`); the prompt driven through the real `/api/v1/ai/chat` endpoint,
   never the dispatcher; the CATIA seat exercised through the bridge; `catia_capture_view` *and* a
   window screenshot; a dated report in `docs/verification-<date>/` naming both. The rung reached
   is recorded, not just pass/fail.

**A gate is placed after a phase that changes what the product can do — never at a round number.**
The test of a gate is whether "what can Kryova do now that it could not do before this gate?" has
an answer an engineer would care about.

The gates:

1. **G1** — opens after **E6** (solver federation). What becomes true: the system can say a part
   *carries its load*, not only what shape it is. Driven at it: rung 3 (measure-and-correct to a
   mass target), then a load-bearing prompt — build it, load it, tell me if it holds; oracle check
   `ccx` vs `linear_static` on the same case.
   > RUN 2026-09-06, DID NOT PASS — rung 3 failed. See `docs/verification-2026-09-06/`. Rung 3 is
   > carried forward and G1 runs again.
   > **DUE — E6 closed 2026-09-08 and the gate has grown two items, because the phase's last two
   > tasks landed on a machine with no `ccx` on it.** (a) **Run the oracle on a thermal case**,
   > not only an isothermal one: `delta_t_k` reached CalculiX nowhere at all until 6.4, so a
   > restrained-bar case at `delta_t_k=80` is the first comparison that could ever have caught
   > it — expect `sigma = -E alpha dT` from both. (b) **Submit one shell deck and one beam deck
   > and read the `.frd` back**, which settles the three keyword claims in
   > `app/solve/calculix/elements.py` that no Linux run can: that `OUTPUT=2D` is accepted on
   > `*NODE FILE` *and* `*EL FILE`, that the results then land at the submitted node numbers, and
   > that the `*BEAM SECTION` data-line order (dimensions, then direction cosines) is the one ccx
   > reads. A wrong answer to any of the three is a deck ccx accepts.
   > **Neither (a) nor (b) is settled by the re-run below**, which ran the oracle on an
   > isothermal case only and submitted no shell or beam deck. They are `A2` and `A3` in THE
   > QUEUE (`docs/WINDOWS_VERIFICATION.md`) and the gate still carries them.
   > **RE-RUN 2026-09-08, STILL DID NOT PASS** — see `docs/verification-2026-09-08-G1/`.
   > Rung 3 is **discharged**: `catia_set_parameter` and the correction loop are verified end to
   > end (39.05 → 45.1 mm, 3.20153 kg against a 3.2 kg target, `Pad.1` still `Pad.1` after
   > replay), and E4.4's part panel and both status-chip tooltips are verified on the real
   > machine. The **oracle check passes** — but only after fixing a blocker it found: CalculiX
   > truncates numeric fields at 20 characters and `deck._number` emitted `repr`, which is 22–23
   > for a full-precision double, so `ccx` refused every deck built from real imported geometry
   > and the oracle had never once run on a real part. Fixed and pinned this session.
   > **What G1 now waits on is the load-bearing prompt.** `draft_load_case` chose faces from six
   > absolute direction words on a beam whose long axis is Z, clamping and loading two faces
   > 20 mm apart on a 200 mm part and reporting a factor of safety of 1303 with no complaint.
   > Three open items in the report: a part-relative face vocabulary, a sense check on a drafted
   > load case, and a convergence check (E7 task 1) — every stress in this run came from one
   > coarse tet4 mesh. Note also that G1 has now twice been run against an **unfinished E6**
   > (task 3 NOT STARTED, task 4 PARTIAL).
   > **Two of that report's three open items are closed on Linux, 2026-09-09.** (1) The face
   > vocabulary gained `far end` and `near end`, resolved against the part's own bounding box, so
   > the end of a beam can be named whatever direction it lies in — the model no longer has to
   > turn a box into an axis, which is the reasoning step it got wrong. (2) A drafted case is now
   > checked against the geometry it was drafted for, and the gate's own answer comes back
   > carrying *"the left face is held and the right face is loaded, and they are only 20 mm apart
   > across the part's x direction — the part is 200 mm long in z"*. Both are warnings in
   > `unresolved`, not refusals: a short span is a legitimate load case, and this codebase's rule
   > about over-refusal is that the agent's recovery from one is to try something else. **What
   > was missing was not permission to run; it was anybody saying the number looked wrong.**
   > (3) The convergence check is still open and is E7's — the machinery now exists and is
   > validated, but nothing in the request path runs a study, so every stress this product
   > reports still comes from one mesh.
2. **G2** — opens after **E11 + E12**. The input stops being a shape description and becomes a
   written requirement, with real materials and bought-in parts. Driven: rung 4 — two parts and a
   constraint, specified as a requirement rather than as dimensions.
   > NOT RUN
3. **G3** — opens after **E14 + E9**. Assemblies with interface contracts, and motion — a thing
   with a range rather than a pose. Driven: rung 5 — a mechanism whose clearance must hold through
   its travel.
   > NOT RUN
4. **G4** — opens after **E17 (with E17.3)**. A package leaves the system that a manufacturer can
   act on: drawings, STEP, BOM. Driven: the package is produced and *read* — a drawing looked at,
   not a file counted.
   > NOT RUN
5. **G5** — opens after **E18 M2 upward**. A machine, against a written requirement, off the
   mission ladder. Driven: rung 6.
   > NOT RUN
6. **GP1** — opens after **P1 + P2**. More than one person can use it safely — rotation, families,
   orgs, RLS. Driven: two accounts, two orgs, cross-tenant reads that must 404.
   > NOT RUN

Four rules that keep this from becoming a way of testing less:

1. **The bar does not move.** A task is still `DONE` only when its proof runs green, and a proof
   that names the product still waits for its gate. Batching changes *when* the expensive check
   runs, never *whether*.
2. **A defect found at a gate re-opens the stretch**, and the gate runs again afterwards. A gate
   that half-passed is a gate that did not pass; record the rung reached and carry it forward.
3. **The prompt gets harder at every gate.** A gate that re-runs the previous gate's prompt has
   measured nothing.
4. **Between gates, an integration claim is unproven and is written as unproven.** The offline
   suite proves the units; it has never once proved the path.

### The documentation-first rule (standing, all phases)

Every phase opens by reading the primary documentation of what it builds on — the OCCT reference
for a kernel phase, the CalculiX manual (Dhondt) for a solver phase, the Tauri v2 updater docs for
P7, the OWASP cheat-sheets for P1 — and recording the load-bearing facts in the phase's design
note *with citations*. Two rules make this stick:

1. **No dependency is adopted on the strength of a blog post** — primary docs or source, always.
2. **Any fact the plan leans on that the docs do not confirm becomes a spike, not an assumption**
   — exactly how v1's pythonocc/OCAF assumption got caught.

Where licences allow, dependency docs are ingested into the existing BM25 index (`app/retrieval/`)
so the agent can consult them the same way it consults the CATIA manuals.
---

# ENGINEERING TRACK

## ERA I — THE GEOMETRY ENGINE BECOMES OURS

##### Phase E1 — The open kernel: OCCT as the primary compilation target #####

> ✅ PHASE COMPLETE (2026-09-05, and **task 8 added and closed 2026-09-10**) — the phase's
> question is answered and every task is tested, with three residuals named rather than hidden.
> **Task 8 is here because the phase was complete on every task it had and still did not deliver
> its own promise**: the kernel built parts that could not reach the solver, because the route
> from geometry to a `GeometryVersion` was a seam no task owned. Found by driving the product,
> not by reading the plan — which is the argument for the ladder. **task 7** needs hardware this
> machine does not have. **Task 3 closed on 2026-09-14** (corrected that day): this marker said it
> stayed `PARTIAL` *by design, permanently*, because it read the task as 201 implementations,
> which Decision 1 forbids. The task also allows a reasoned refusal, and every unimplemented
> operation now has one, so task 3 is `DONE` without the operation count being a goal. **Task 4
> closed on 2026-09-15**, also as a scope decision: the sketch layer the product uses builds, and
> the one constraint operation, which nothing calls and whose solver has no wheel for the seat's
> Python, is refused with that reason rather than owed. No task in the phase is open.

**~8 engineer-months. The keystone. Nothing downstream is affordable until it lands.**

**The question it answers:** can Kryova build geometry without a licensed workstation,
deterministically, in CI, at machine scale?

1. **The binding spike.** Establish whether OCAF/TNaming is reachable from Python well enough to
   build persistent naming on, and name the fallback if it is not.
   > DONE (2026-09-05) — three findings, each of which changed the phase.
   > (a) **`pythonocc-core` is not on PyPI at all** — conda-only. Adopting it would have forced
   > conda into every deployment and into CI, against the whole point of Decision 1. The binding
   > is therefore [`cadquery-ocp`](https://pypi.org/project/cadquery-ocp) (OCP, pybind11,
   > OCCT 7.9.3): pip-installable, exposing all 320 OCCT modules including the full OCAF stack
   > (`TNaming`, `TDF`, `TDocStd`, `TFunction`, `TDataStd`). **The C++-service fallback is not
   > needed**, and neither is pyOCCT.
   > (b) **Persistent naming survives a real parametric rebuild.** The spike named a fillet face,
   > regenerated the part at different dimensions, and recovered the *new* corresponding face —
   > verified by area against the closed-form quarter-cylinder (691.150 mm² at r=8, h=55), not
   > against a recorded number.
   > (c) **Three non-obvious rules govern it**, two of which fail by making
   > `TNaming_Selector.Solve()` return **success while resolving to nothing**: one OCAF label
   > records one evolution kind; a regeneration must rewrite the *same* labels; edges must be
   > walked as well as faces, because a fillet's new face is `Generated` by the edge, not by any
   > face. All three documented in `app/kernel/occt/naming.py` with the failure each produces.
   > **Cost finding handed to P9.2:** OCP is ~166 MB and pulls ~640 MB of VTK as a hard dependency
   > this codebase never uses. Stripping it is a container-layer job, not a `--no-deps` install
   > that would silently break. Tested by: `tests/test_kernel.py`.

2. **The kernel service.** A process wrapping the kernel behind the same `CallRunner` interface
   `app/design/execute.py` already defines. In-process where safe, subprocess-isolated where the
   kernel can abort (OCCT does abort on degenerate booleans, and a crash must kill a worker, not
   the API).
   > DONE (2026-09-05). Tested by: `tests/test_kernel.py`. Code: `app/kernel/` (24 modules).

3. **The operation mapping.** Each of the 201 registry operations gets an OCCT implementation or
   an explicit, reasoned refusal. Not mechanical: `catia_pad` is `BRepPrimAPI_MakePrism` plus
   sketch resolution plus support resolution plus naming bookkeeping. Sequence by mission — M1's
   vocabulary first, then M2's weldment needs. Per Decision 1 an operation is added only when a
   test, a sweep or an optimisation needs it, never for coverage.
   > DONE (2026-09-14) — **every one of the 205 declared operations is now in exactly one of
   > three places, and a test fails the day one is in none**: 119 implemented in `HANDLERS`, 1
   > served by the dispatcher (`catia_export_step`, `backends.LOCALLY_SERVED`), and 85 refused
   > with a reason in `app/kernel/occt/refusals.py`. The task always said "an implementation *or*
   > an explicit, reasoned refusal". The PARTIAL below read it as 201 implementations, which
   > Decision 1 forbids, so it could never close. The second half had simply never been written:
   > all 85 raised `OperationNotSupported` with no reason, and the dispatcher told the agent
   > "not implemented in the open kernel yet" for a CATIA dialog tool and a missing loft alike.
   > **Three kinds of reason, because they send the agent different ways**: it drives CATIA's own
   > interface, so it will never exist here (dialogs, menus, selection, files); another part of
   > the product does the job (drawings from `app/manufacture`, relations from the design IR,
   > taking a feature back from `catia_delete_feature`); or no test, sweep or optimisation has
   > needed it yet, naming a served tool that gets the same part where one exists. **A reason may
   > only name a tool this backend serves**, checked by reading every `catia_` name out of every
   > reason. The dispatcher now carries the reason beside the coverage count.
   > **Three operations were implemented, each for a measured gap, not coverage:**
   > `catia_delete_feature` for the 2026-09-11 conversation below (the agent padded 200 mm, knew
   > it, and could not take it back). A delete is a rebuild from the build log without the calls
   > that made the feature, into a fresh document swapped in only on success. Dependents are
   > found by name (`sketch="outline"`, `"Pad.1#top"`) and refused unless `with_children`, with
   > every name that would go listed. Dependence on material a later cut does not name is caught
   > by the rebuild, which refuses and changes nothing. Every feature keeps its number, and a
   > deleted number is not handed out again. A body cannot be deleted, because the build log
   > does not record which body a feature went into (refused by name).
   > `catia_feature_parents`, because the delete's own summary sends the agent there first.
   > `catia_shell_faces`, because **the dispatcher's schema for `catia_shell` has nothing to
   > open**, so an agent on the open kernel could hollow a part only sealed. It takes a wall
   > thickness per face (`BRepOffset_MakeOffset.SetOffsetOnFace`).
   > **Two defects found on the way, both fixed.** (1) OCCT reports `IsDone()` for a shell whose
   > walls meet: on a 40x30x20 box open at the top, 15 mm walls came back as an invalid 20,888.9
   > mm3 shape, and 16 mm walls came back as the original box, 24,000 mm3 and six faces, reported
   > as a finished shell. It affected `catia_shell` with faces as well; both now refuse. (2) The
   > face-picking selector knew `top` and `bottom` only, while every face reference in the
   > registry documents six words and `elements` resolved all six as planes. So "thicken the
   > right wall" was refused. `front`, `back`, `left` and `right` now pick faces too, held to
   > the plane table by a test.
   > **Interface changes:** `catia_delete_feature`, `catia_feature_parents` and
   > `catia_shell_faces` are offered on `GEOMETRY_BACKEND=occt` (`local_tool_names`). The open
   > kernel's refusal text changes from "is not implemented in the open kernel yet" to "is not
   > available on the open kernel … <reason>". No schema, migration or response-shape change.
   > Every volume is checked against a hand calculation, and the outward shell against the
   > closed form of a box grown by a ball. 30 guards broken one at a time: 28 caught by a named
   > test, one removed as redundant (a rename having no names of its own), and one labelled
   > unpinned: `Thickening=False`, which measured identical to `True` on a solid. Restores
   > sha256-checked. **Not claimed:** anything on a seat (the CATIA side of these three
   > operations was already in the registry and is untouched), or deleting a feature inside a
   > multi-body part by its body.
   > Tested by: `tests/test_kernel_history.py`, `tests/test_kernel_refusals.py`,
   > `tests/test_kernel_shell_faces.py`, `tests/test_kernel_face_words.py`,
   > `tests/test_geometry_backends.py::TestAWrongFeatureCanBeTakenBack` (through `call_catia`),
   > `tests/test_agent.py::TestTheSecondPartRefusalIsBackendAccurate`.

   <!-- superseded 2026-09-14 -->
   > PARTIAL — 22/201 at the close of E1, **108/201 after E2**, 117/205 as of 2026-09-11. This
   > figure is scaffolding depth, not product progress, and reading it as progress is how it got
   > to 108. Tested by: `tests/test_kernel.py`.
   > **One operation now has the measured need this task's rule asks for: `catia_delete_feature`.**
   > Driving the GUI on 2026-09-11, the agent padded 200 mm instead of 10, **diagnosed its own
   > error correctly** ("the current part has wrong thickness") — and then could not act on it.
   > There is no way to remove a feature on the open kernel, so a botched part is unrecoverable
   > inside a conversation: the agent must keep building on wrong geometry or the user must
   > abandon the conversation. It spent three calls on `catia_new_part` hunting for a way back
   > and the turn ended on an E16.4 escalation.
   > The refusal message has been widened in the meantime to name the one escape that does exist
   > (`catia_assembly_component` closes the open part, then `catia_new_part` starts a fresh one),
   > because it was described only as the route to a *second part for an assembly* and no model
   > reading it would recognise it as "start over". **That is a mitigation, not the fix**: it
   > discards the whole part, so a single wrong pad costs every correct feature built after it.
   > This is exactly the trigger Decision 1 names — not coverage, a real conversation that could
   > not finish — so `catia_delete_feature` is the next operation to add, and the first one whose
   > justification came from the ladder rather than from a sweep.

4. **The sketch layer.**
   > DONE (2026-09-15) — **closed as a scope decision rather than by building a solver, and the
   > plan changed to say so.** The sketch layer the product uses is complete. Every profile tool
   > that feeds a pad, pocket, shaft, groove or rib (rectangle, circle, polygon, slot, point, line,
   > polyline, arc, three-point arc, ellipse, spline, axis) is fully determined by its arguments
   > and builds. The PARTIAL below still owed PlaneGCS to one operation,
   > `catia_sketch_constrain`. **Nothing in the product calls it.** On 2026-09-15 no design,
   > mission, template or test named it outside its own refusal, and Decision 1 adds an operation
   > only when a test, a sweep or an optimisation needs one. The seat takes the same route to a
   > sized profile: `catia_sketch_dimension` fails on V5-R33 more often than it works (CLAUDE.md,
   > *Two seat behaviours*), so the agent draws at the size it wants. **PlaneGCS also could not
   > ship today.** `planegcs` 0.8.0 on PyPI has wheels for CPython 3.12 and 3.13 only, and the
   > Windows seat runs 3.14, so installing it would mean compiling FreeCAD's solver from the
   > sdist on the delivery machine. So the task stops owing PlaneGCS instead of carrying a debt
   > nobody is going to pay.
   > **One defect fixed with it.** `catia_sketch_constrain` sat in `HANDLERS` with a body that
   > did nothing but raise, so `local_tool_names()` offered the agent a tool that could never
   > work. That is the class CLAUDE.md testing item 8 describes, the other way round. It is now a
   > reason in `refusals.py` beside the other 85, giving 118 implemented + 1 served + 86 refused =
   > 205. A new test fails if any handler's whole body is a raise.
   > **It reopens as a new task** the day a test, a sweep or an optimisation needs a sketch
   > constraint solved. The solver choice (PlaneGCS over SolveSpace, reasoned below) and the
   > wheel gap are both recorded here for that day.
   > **Interface change:** `catia_sketch_constrain` is no longer offered on
   > `GEOMETRY_BACKEND=occt`. The refusal still names PlaneGCS, and its CATIA side is untouched.
   > Tested by: `tests/test_kernel.py::TestSketchesAndSolidFeatures`,
   > `tests/test_kernel_refusals.py`.

   <!-- superseded 2026-09-15 -->
   > PARTIAL (2026-09-05) — parametric half done, solver deferred with cause. The finding that
   > changed this task: **the registry's sketch vocabulary is dimension-driven, not
   > constraint-driven.** `catia_sketch_rectangle` takes a width and a height,
   > `catia_sketch_circle` a diameter, `catia_sketch_polygon` a side count and a diameter — each
   > *fully determined by its arguments*, with nothing for a solver to solve. So every profile
   > that actually feeds a pad, pocket, shaft or groove is buildable without PlaneGCS, and those
   > profiles build now (`app/kernel/occt/sketching.py`). PlaneGCS is still owed for exactly one
   > operation, **`catia_sketch_constrain`**, which applies arbitrary constraints to free
   > geometry; it refuses by name with that reason rather than pretending. When it lands the
   > choice stands: **PlaneGCS** (FreeCAD's, LGPL — DogLeg / Levenberg-Marquardt / BFGS / SQP),
   > full constraint vocabulary, proven detachable (a WASM port exists). Rejected: SolveSpace's
   > solver — faster, narrower; a sketcher that cannot express tangency is not a sketcher.
   > This is a narrowing of scope, not a claim to have finished it.

5. **Persistent topological naming.** *The hard part.* Every created face gets a stable identity
   that survives regeneration, keyed by the design IR's semantic names
   (`swingarm.pivot_bore.inner_face`). Mechanism: `TNaming` plus, where available, OCCT 8.0's
   `BRepGraph` history. The published insight that bounds the work: **only faces need naming** —
   `TNaming_Selector` recovers edges and vertices from adjacent faces.
   > DONE (2026-09-05). Tested by: `tests/test_kernel.py`. Code: `app/kernel/occt/naming.py`.

6. **The conformance harness.** One `Plan`, two backends, geometry compared: volume, mass, centre
   of gravity, inertia tensor, bounding box, surface area, face/edge counts, within declared
   tolerance. Divergence is a finding about one backend, and the harness says which.
   > DONE (2026-09-05) — `compare_backends` is written and exercised against two runners.
   > Tested by: `tests/test_kernel.py`.

7. **Determinism.** Pinned OCCT version, pinned tessellation tolerance, fixed locale,
   containerised. Same spec + same version ⇒ same geometry, byte for byte, asserted in CI.
   > DONE (2026-09-05) — the same spec built twice produces the same geometry digest; a changed
   > dimension changes it. Tested by: `tests/test_kernel.py`.
   > **RESIDUAL CLOSED 2026-09-12 on the seat (THE QUEUE B1/B2).** The same plan built on a
   > real V5-R33 and compared: a 30x20x5 plate agrees **exactly** on volume (3000.0) and
   > surface area (1700.0), and a bored 60x40x10 agrees to **0.000%** (22869.026644707676
   > against CATIA's 22869.0266, and 6950.79644737231 against 6950.7964 — CATIA prints four
   > decimal places). Both plans built on both backends. **Decision 1's central claim is now
   > measured rather than assumed.**
   > Mass differs by **0.127%** and that is deliberate: CATIA applies its catalogue *Acier* at
   > 7860 kg/m3 where the kernel holds 7870 for `steel-1018`, and the bridge prefers CATIA's
   > density once a material is attached so the mass Kryova quotes is the mass the CATPart
   > reports (`scripts/catia_bridge/catia_com.py`).
   > **What running it for the first time found is that the harness could never have passed**,
   > for three reasons that were all invisible while both sides were `OcctRunner`: the kernel
   > says `centre_of_mass_mm` and the bridge says `center_of_gravity_mm`, so the centre of
   > mass was silently never compared; `CONFORMANCE_TOLERANCE_MM3` (1e-6) is finer than CATIA
   > *prints*; and one tolerance covers mm3, mm2 and kg, which is harmless at 1e-6 and would
   > have hidden the 0.127% at 1e-3. Fixed by `measurement.centre_of_mass`,
   > `SEAT_TOLERANCE_MM3`, and a harness that reports **deltas** rather than a verdict.
   > **One gap remains and it is E3's:** the bridge's `catia_measure` reports no
   > `face_count`/`edge_count`/`solid_count`, so topology cannot be compared — the only
   > remaining divergences, and "absent on one side" rather than "different".
   > Tested by: `tests/test_seat_conformance.py` (15, offline), verified by breaking the
   > alias. Code: `app/catia/runner.py`, `scripts/catia_conformance.py`,
   > `app/kernel/measurement.py`. Evidence: `docs/verification-2026-09-11/B1-conformance.json`.

8. **The open kernel's part reaches the solver.** *Added 2026-09-10, after the phase was already
   marked complete.* A part built on `occt` must become a `GeometryVersion` the mesher can read,
   the way `catia_export_step` does on a seat. Not a kernel capability — the shape is already in
   the process and `manufacture.export.write_step` already writes it — but a route, and until it
   existed the open kernel was a modelling toy rather than the product.
   > DONE (2026-09-10) — **the defect the 2026-09-10 ladder run found, and the reason Level 3 had
   > to be driven on the seat.** On `GEOMETRY_BACKEND=occt` the agent built the bracket correctly
   > and could then do nothing whatever to it: `catia_export_step` and `sync_geometry_from_catia`
   > were the only two geometry→solver routes in its entire vocabulary and **both were
   > CATIA-only**, so `run_simulation` had nothing to mesh. That blocked ladder Levels 3, 4 and 5
   > on the open kernel and with them every analysis feature the run was told to test — plane
   > analyses, conduction, convergence studies.
   >
   > **Why no test could have caught it.** Every tool worked. `write_step` worked. The kernel
   > document held the shape. The gap sat one layer above `dispatch`, in what the agent was
   > *offered* rather than in what any tool did — the same shape as the `catia_new_part` binding
   > defect measured on the seat on 2026-09-05, and the second time this exact class has been
   > invisible to a green suite. The new tests go through `call_catia` rather than a runner for
   > that reason.
   >
   > **The design decision, because it is the part a later reader will want to undo.**
   > `catia_export_step` is served on the open kernel from the dispatcher, via
   > `backends.LOCALLY_SERVED`, and is deliberately **not** in `HANDLERS`. That table declares
   > what OCCT implements of the *geometry vocabulary*, and both `local_coverage()` and the
   > cross-backend conformance harness read it as exactly that — an export added there would
   > inflate the coverage number with something the kernel does not implement and would make
   > `compare_backends` try to build a part with it. So `local_tool_names()` answers *what is
   > offered* and `local_coverage()` answers *what the kernel implements*, and they are now
   > different questions with a test pinning the difference.
   >
   > Two smaller things fixed alongside: `import_step_export` had `"catia_bridge"` hardcoded as
   > the blob's source and said "check the part in CATIA" in its refusals, which sends somebody
   > running the open kernel to an application they do not have; and a multi-body document
   > exports its **active body**, with the bodies left behind named in the result rather than
   > silently dropped.
   > Tested by: `tests/test_geometry_backends.py::TestThePartCanReachTheSolver` (6 tests, driven
   > through `call_catia`) plus the coverage-separation test — three guards verified by breaking
   > them (the offering, the empty-shape refusal, the coverage separation), 10 failures observed,
   > all files restored byte-for-byte. Code: `app/catia/dispatch.py::_export_locally`,
   > `app/geometry/backends.py::LOCALLY_SERVED`, `app/catia/geometry_import.py`.

9. **An option the schema advertises works on *both* backends.** *Added 2026-09-11, after the
   phase was already marked complete.* Tasks 6 and 8 are about geometry agreeing across the two
   backends; this is about the *vocabulary* agreeing. An argument declared in `app/catia/ops/`
   is a promise the agent reads and acts on, and honouring it on one backend and ignoring it on
   the other is a product that behaves differently depending on a setting nobody in the
   conversation can see.
   > DONE (2026-09-11) — **the defect ladder L2 found, and it had already been fixed once, on the
   > other side.** `catia_list_features` declares `body`, `kind` and `include_sketches`, and its
   > own summary calls it *"the first call to make on any document you did not just build
   > yourself"* and promises "the features, **sketches** and bodies". The gap was found on the
   > **CATIA** side on 2026-09-06 after ladder prompt H2 and closed there
   > (`tests/test_catia_list_features_options.py`). **The open kernel was never given the same
   > treatment**: its handler read none of the three and returned solid features only.
   >
   > Measured on 2026-09-11, `qwen3.6:27b`, `GEOMETRY_BACKEND=occt`. A rectangle had been drawn
   > on a sketch (`profiles: 1`, `ok`), a pad on it had just been turned back by the E16.4 guard
   > above, and the model called this tool to find out what was really in the document. It
   > answered `{"features": [], "detail": []}` — true of solid features, and read, correctly, as
   > *"the part is empty - the sketch and rectangle weren't saved"*. The model threw the sketch
   > away and rebuilt from scratch. **An empty answer to a question about a document that is not
   > empty is worse than a refusal, because it is believed.**
   >
   > The two defects compound and that is the finding worth carrying: E16.4's guard induced a
   > false belief and this listing corroborated it. Either alone the model recovers — it visibly
   > does so elsewhere in the same run. Together they were conclusive.
   >
   > All three options now work on the open kernel, in the shape the CATIA mock already uses so
   > the two agree: sketches in the listing with `type: "Sketch"`, an `elements` count, and
   > `profiles` / `can_be_built_from` — the field that answers *"why will this not pad?"* before
   > the pad is refused a second time. `kind` filters case-insensitively (the model types what
   > the user said, not what CATIA capitalises) and names what types *are* present when it
   > matches nothing, because an empty list is a second round trip. An unknown `body` is refused
   > naming the bodies that exist rather than silently answering about a different one.
   > Tested by: `tests/test_kernel_list_features_options.py` (12, offline, deliberately mirroring
   > the CATIA-side file so both backends are held to one contract) — verified by breaking it,
   > three failures observed, file restored and confirmed by `diff`.
   > Code: `app/kernel/occt/operations/document_ops.py::list_features`.
   >
   > **The residual is the general case, and it is a real one.** This closed `catia_list_features`
   > because that is where it was measured. Nothing yet asserts that *every* declared parameter is
   > read by *every* backend that claims the operation — the conformance harness (task 6) compares
   > geometry, not vocabulary. Until something does, this class can recur on any of the other
   > ~117 operations the open kernel implements.
   >
   > <!-- residual closed by task 10, same day -->

10. **No advertised argument is silently ignored.** *Added 2026-09-11, closing task 9's residual
    the same day it was written.* Task 9 fixed one operation because that is where the ladder
    happened to land. This asks the question of all of them: for every argument the registry
    declares on an operation the open kernel implements, is it **honoured, or refused** — never
    dropped?
    > DONE (2026-09-11) — **and the answer was eighteen.**
    >
    > **Found by measurement, not by reading code.** A static scan for the argument name in the
    > handler's module was written first and was wrong in both directions: it reported 74 holes,
    > of which `name` (×24) is read by `context.feature_name` in another module, and
    > `at_radius_mm`/`at_angle_deg` are read by `app.catia.ops.placement.resolve_polar` through a
    > **function-local import** that no import-graph closure can see. The trustworthy question is
    > the differential one — build the part without the argument, build it again with a
    > meaningfully different value, compare the geometry — and that is what the harness now asks.
    > Eighteen survived it.
    >
    > **Four produced a confidently wrong part rather than a missing one**, each returning `ok`
    > with a feature name and a full set of `measured` provenance:
    > * `catia_translate(direction=[1,0,0], distance_mm=50)` moved the part **1 mm**. The raw
    >   direction vector was being used as the whole displacement and `distance_mm` — a
    >   **required** argument — was never read. Bounding box [-50, 50] → [-49, 51].
    > * `catia_pad(thin=True, thickness_mm=3)` on a 100×60 profile returned a **solid** pad of
    >   120,000 mm³ where a 3 mm wall is about 18,480 — 6.5× the material.
    > * `catia_hole_at(thread='M6x1')` drilled a plain clearance hole. A tapped hole and a
    >   clearance hole are different parts to make, and nothing recorded the difference.
    > * `catia_pad(second_length_mm=30)` extruded one side: 120,000 mm³ where two-sided is 300,000.
    >
    > **Four were implemented or handled rather than tabled**, because their contracts were small
    > and their absence produced wrong geometry rather than a missing capability: `distance_mm` on
    > `catia_translate` (the direction is now normalised, so its magnitude carries no meaning and a
    > negative distance reverses it); `plane` on `catia_sketch_rectangle` / `_circle` / `_polygon`
    > (its documented meaning — *"Support to sketch on when no sketch is open"* — so it now opens
    > one, and a `plane` that contradicts a named sketch is refused rather than resolved, since
    > picking either is a profile on a plane nobody asked for); and `target_body` on
    > `catia_boolean`, which always combined into the *active* body and accepted a body name that
    > did not exist in silence.
    >
    > **`target_body` is checked in its own handler, and that is where this approach's boundary
    > is.** The value that is harmless for it is not a constant — it is whatever body happens to be
    > active — and only the document knows that, so a static table of arguments cannot express it.
    > **It also caught an error of method**: it was first recorded as ignored on a differential
    > against `"PartBody"`, which was already the active body, so the measurement was of the
    > harmless value and proved nothing. The full suite found it, through a pre-existing test that
    > legitimately passes `target_body="lower"`. *A differential is only as good as the difference.*
    >
    > **The remaining fourteen are refused by name**, in `app/kernel/occt/unsupported.py`, each with
    > what the argument would have done and what to do instead. `app/catia/` warns at length that
    > over-refusal is its own failure mode — but that argument does not apply to an argument
    > already being ignored: the part is wrong either way and the only question is whether anybody
    > is told. The refusal names the *argument* as its subject, not the operation, because
    > `catia_pad` works perfectly well and "catia_pad is not supported yet" would send the agent
    > looking for another way to extrude. A `harmless` value per entry keeps `thin: false` and
    > `recursive: true` building — a model that spells out a default has asked for nothing it will
    > not get, and `recursive` defaults to *true*, so a table assuming every flag defaults to false
    > would have had that one backwards.
    >
    > The guard sits in `OcctRunner.__call__`, in front of all 116 operations, and fires **before**
    > the handler: a half-applied operation would be worse than the silence it replaces.
    > Tested by: `tests/test_kernel_unsupported_arguments.py` (36, offline) — the table is checked
    > against the registry so it cannot drift, every entry's refusal is asserted legible, and the
    > count is pinned at **14 and may only shrink**, the same shape as `tests/test_delivery.py`'s
    > grandfathered names: a list of known gaps is only honest while it is closing. Verified by
    > breaking all three guards separately — 1, 5 and 5 named failures — with each file restored
    > and confirmed by `diff`.
    >
    > **A correction to task 9 landed with it.** That task had put sketches into
    > `catia_list_features`' `features` list to match the CATIA mock, and the full suite refused
    > it: on this backend `features` is `document.feature_names()`, which is also what every
    > *mutating* operation returns, so reshaping it left the listing and the build results
    > disagreeing about what the part contains — the failure
    > `tests/test_catia_list_features_options.py` guards against from the other side. The two
    > backends' `features` already differ in shape (dicts there, names here), so matching the mock
    > was never the consistency it looked like. Sketches now arrive under their own `sketches`
    > key, which fixes the measured defect without moving a contract.
    > Code: `app/kernel/occt/unsupported.py`, `app/kernel/occt/runner.py`,
    > `app/kernel/occt/operations/{transforms,sketcher,booleans,document_ops}.py`.

11. **One vocabulary, every operation that takes a plane.** *Added 2026-09-11.* Task 10 asked
    whether every declared *argument* is honoured. This is its sibling for *values*: a word the
    product accepts in one operation must mean the same thing in all of them.
    > DONE (2026-09-11) — **the last private accept-list, found by the agent losing seven steps to
    > it at ladder Level 4.** `elements.plane_frame`'s own docstring calls it *"the one resolver
    > every operation that takes 'which plane' goes through … Before it, each had its own
    > accept-list."* The bare face words — `top`, `bottom`, `front`, `back`, `left`, `right` — were
    > added to **`sketcher.resolve_support`** on 2026-09-09, after ladder L2 measured that
    > `support="top"` is *the first thing the model reaches for*. They were never added to
    > `plane_frame`. So for two days `catia_sketch_create(support="top")` worked and
    > `catia_hole_at(face="top")` answered *"There is nothing called 'top' in this part"*.
    >
    > Measured 2026-09-11: asked for a bolt hole on the fixed end of a bracket, the agent tried
    > `face="left"`, was refused, called `catia_list_faces`, tried `face="normal [-1, 0, 0]"` from
    > what that returned, was refused again, abandoned `hole_at` for a sketch-and-pocket that also
    > failed, and finally got there with `catia_hole`. Every refusal was correct about its own
    > accept-list and wrong about the product.
    >
    > The table now lives in `elements` and `sketcher` reads it from there, so there is one of it.
    > Tested by: `tests/test_kernel_face_words.py` (16, offline) — including that `top`/`bottom`
    > and `left`/`right` resolve to *opposite ends* of their axis, because a word resolving to the
    > wrong end builds a part inside out with every count and area still correct. Verified by
    > breaking it: **11 failures**, across every operation sharing the resolver, which is the scope
    > of the defect. Code: `app/kernel/occt/elements.py`,
    > `app/kernel/occt/operations/sketcher.py`.

12. **A feature that changes no material is refused, in every family that makes one.** *Added
    2026-09-11.*
    > DONE (2026-09-11) — `features.py` has refused a pad that adds nothing and a pocket that
    > removes nothing since gate G1 on 2026-09-06, on the stated grounds that *"there is no reading
    > under which a caller meant it"*. **Holes never called that guard**, and holes are the family
    > most likely to miss, because they are *positioned* rather than sketched: `BRepAlgoAPI_Cut`
    > succeeds when tool and target do not overlap, returns the target unchanged, and `IsDone()` is
    > true.
    >
    > Measured on a 180 × 90 × 10 plate: the same hole drilled twice gave `HoleAt.2`, and a hole at
    > `[500, 500]` — entirely beside the part — gave `HoleAt.3`. Both `ok`, both **0 mm³ removed**,
    > both with a feature name and full `measured` provenance. Found at ladder Level 4 the same
    > day, where the agent put its second bolt hole on top of its first and had to notice from the
    > volume that nothing had been cut.
    >
    > The guard moved to `operations/context.py` — every operation module imports `context` and
    > none imports a sibling — and `_drill` calls it, covering both `catia_hole` and
    > `catia_hole_at`. **The remedy half of the message is now per-operation**: the pocket's advice
    > names a sketch and `reversed: true`, and a hole has neither, so one shared message would send
    > a caller looking for an argument that does not exist — worse than no advice.
    > Tested by: `tests/test_kernel_hole_removes_material.py` (7, offline), verified by breaking it
    > — 4 failures. Code: `app/kernel/occt/operations/{context,holes,features}.py`.

**Phase proof:** M1 — a machined bracket — compiles, builds on OCCT in CI, builds on CATIA on a
real seat, and the two agree on every interrogated quantity to declared tolerance. Ten times,
identically, from a cold container.
> MET ON THE OCCT HALF (2026-09-05). The bracket (sketch → rectangle → pad → circle →
> through-pocket → corner fillets, 12 calls) compiles from a `DesignSpec` and builds on OCCT,
> with volumes matching closed-form values exactly and assertions checked against them.

**Risks:** OCCT API vastness and thin documentation; the binding lagging upstream; boolean
robustness on degenerate input (a weakness of every kernel). Mitigation: per-operation backend
capability already exists in the registry — the fallback for a failing operation is "CATIA backend
for that op", not "no geometry".

##### Phase E2 — Selection, reference geometry, and the authoring vocabulary real parts need #####

> ✅ PHASE COMPLETE (2026-09-05) — all six tasks done, and the phase Proof is written and green.

**~6 engineer-months.**

**The question it answers:** can the agent *point at* the thing it means, without a face id and
without a guess?

1. **Predicate selection.** Replace named-enum picking with geometric predicates: *all edges
   longer than 10 mm*, *all faces whose normal is within 5° of +Z*, *all cylindrical faces of
   Ø6 H7*, *the tangent-continuous edge chain from this one*. Only decidable against geometry that
   exists — which is why it was blocked before E1 and is nearly free after it.
   > DONE (2026-09-05) — every vocabulary word decidable; `parallel_to`/`perpendicular_to` make
   > "the vertical walls" one selection. Tested by: `tests/test_kernel.py`. Code:
   > `app/kernel/selection.py`, `occt/{resolve,classify,selectors}.py`.

2. **`feature#selector` resolves.** `SemanticName` already reserved the spelling and refused it
   with a message pointing here.
   > DONE (2026-09-05) — `slab#top` returns the annulus under a boss, a face the plain word `top`
   > can never return. Tested by: `tests/test_kernel.py`.

3. **Per-entity parameters.** Per-edge fillet radius, not one radius per group — the single most
   limiting schema constraint in the old vocabulary.
   > DONE (2026-09-05). Tested by: `tests/test_kernel.py`.

4. **Reference geometry completed.** Offset planes, plane-on-face, plane-through-3-points,
   plane-normal-to-curve, user axis systems, datum points/lines — the OCCT half.
   > DONE (2026-09-05) — complete for everything not needing a named face. Tested by:
   > `tests/test_reference_geometry.py` (36 tests).

5. **Part Design completion.** Multi-body, booleans between bodies, geometrical sets,
   rib/slot/stiffener, draft with parting line, variable/tritangent fillets, thread, user
   patterns, shell with face selection, thickness.
   > DONE (2026-09-05). Every geometry operation now records its own faces — pad/pocket/shaft/
   > groove, primitives, transforms, boolean, shell, fillet, chamfer and draft — and a test fails
   > if one stops. Drawn segments chain into contours, so four `catia_sketch_line` calls make a
   > padable square. Ribs and slots verified against Pappus's theorem; a thread is an annotation
   > that provably does not change the mass. Closes with the two features whose extent is not
   > stated by their own arguments: a **stiffener** runs until it meets material (built by
   > subtraction, exact against ½·b·h·t; a sweep that never meets material is refused by name
   > rather than left hanging in the air), and a **draft with a `parting` element** tapers *both*
   > sides away from the plane so a two-part mould releases — checked against the frustum closed
   > form on each side. Tested by: `tests/test_kernel.py`.

6. **Surfaces / GSD.** Multi-section with guides, adaptive sweep, blend with continuity, fill,
   join/heal with tolerance, trim/split, law-driven surfaces, curvature analysis.
   > DONE (2026-09-05). Opens with the half that earns the rest: **a surface is skin, not
   > material** — it lives in the document's construction store and the part's mass does not move
   > when one is built — and `catia_close_surface` / `catia_thick_surface` are the two named
   > crossings back. Extrude, revolve, offset, fill, loft, join, extract, boundary all land.
   > Traps pinned by tests that fail when the fix is removed:
   > (a) **a thickened surface comes back inside-out**, which `BRepCheck_Analyzer` calls valid and
   > which makes a later fuse silently swallow the thing being fused;
   > (b) **`MakeFilling` approximates even a flat boundary**, so a patched circular hole measured
   > 314.1595 mm² against πr²;
   > (c) **cells are not connected components** — a split shell's halves share the cut edge, so
   > `domains` correctly says one while the caller plainly wants two;
   > (d) **`untrim` on a plane is not refused by OCCT** — `MakeFace` reports success and returns a
   > face of area 8 × 10¹⁰⁰, which then flows into a mass and a bounding box looking like a
   > measurement;
   > (e) **`Handle(Geom_…)&` is passed by value by OCP**, so `GeomLib::ExtendCurveToPoint` and
   > `ExtendSurfByLength` build the answer and drop it — no exception, no return value. That is
   > why `catia_extrapolate` widens a parameter range instead, and it is measured by a test rather
   > than remembered;
   > (f) **handing `MakeFilling` a boundary edge carrying no parameter curve segfaults** — `Add`
   > accepts it quietly and the process dies inside `Build()`, so each boundary edge is matched to
   > the support's own edge by geometry first;
   > (g) **OCCT reports a tangency it did not deliver** — filling a cylinder's rim tangentially
   > returns success and a flat disc 82.5° out, so the fill measures `tangent_error_deg` and
   > refuses a patch that missed by more than a degree;
   > (h) **`Geom2d_Line` normalises the direction it is given**, so sweeping 0 → 2πn builds a
   > helix of the right shape and a 16% wrong length;
   > (i) **a face is a trimmed piece of an unbounded surface** — `GeomAPI_ProjectPointOnSurf`
   > answers for the whole surface, so a point beside a cylinder projected onto the *infinite*
   > plane of its top disc, 20 mm outside the rim; fixed in `closest_on_surface`;
   > (j) **an offset has a side and OCCT does not take it from the caller's argument** — it reads
   > the wire's own winding, so the side is stated here, measured on the result and mirrored when
   > it went the other way.
   > Which side of a cut survives is *stated* — cells ordered by the signed distance of their
   > centre from the cutting plane — and a cutter with no plane is refused rather than resolved by
   > whichever piece OCCT listed first. `catia_surface_analysis(kind='connect')` reports the
   > smallest tolerance that would join the pieces, which is the argument `catia_healing` takes,
   > so the analysis hands the repair its own parameter. Wireframe curves land too (helix, 3D
   > circle and arc, polyline, interpolating spline, section, intersection, extremum), verified
   > against `n·√(pitch² + (2πr)²)`. Derived anchors land (`catia_point_{on_curve,on_surface,
   > centre}`, `catia_line_{between,direction,normal,tangent}`), which is what makes a point
   > *associative* rather than a coordinate that goes stale. Associative curves and planes close
   > the gap (`catia_curve_{project,parallel,offset_3d,combine}`, `catia_plane_{normal_to_curve,
   > tangent_to_surface,mean}`, `catia_planes_between`). `catia_curve_connect` measures the
   > continuity it achieved and reports tangent error in degrees and curvature step per mm,
   > because a G2 claim that is asserted rather than measured is the kind of number this codebase
   > refuses to print. `catia_curve_spiral` reports its own worst radial error measured *between*
   > the interpolation knots — at the knots it reads 1e-14 against the 9.4e-5 mm it is really out
   > by, a factor of 10⁹. `catia_curve_reflect_line` keeps the **hidden** part of the line and
   > keeps only what lies on a **curved** face, because a polyhedron does its turning at edges
   > that already exist. Tested by: `tests/test_kernel.py` (21 named test classes),
   > `tests/test_reference_geometry.py`. Code: `occt/operations/` (15 modules),
   > `app/kernel/threads.py`.

**Phase proof:** a part authored with every fillet radius different, each chosen by predicate; the
design regenerates correctly after an upstream feature insertion changes every face id in the
model.
> GREEN (2026-09-05) — `TestTheProofOfPhaseTwo`. A 60×40×20 plate whose four vertical corners
> carry 2, 3, 4 and 5 mm — one call, edges chosen by predicate and radii matched to the selection
> order — compiled from a `DesignSpec` and run through the real `OcctRunner`; then the *same spec*
> with a through-notch inserted ahead of the fillets, recompiled and rebuilt from nothing. Volume
> exact against `blank − Σh·r²(1−π/4) − notch` both times. The insertion is not assumed to
> renumber nothing — it is measured: the plate's vertical edges move from positions 0, 1, 4, 7 to
> 5, 7, 19, 23, and the design still finds them because it refers to nothing positional.
>
> **Running the proof found four places where the layers were each right and disagreed, none of
> which any existing test could see:**
> (a) `catia_fillet`'s `radius_mm` was declared a number, so the per-edge list the kernel has
> taken since task 3 was unreachable from a spec;
> (b) `catia_fillet`'s `feature` argument was **declared and silently dropped** — the design
> suite's own bracket fixture asks for it, so the canonical example of the vocabulary was rounding
> every vertical edge on the part and reporting success;
> (c) a compiled design renames every feature to its own name and `Document.feature` looked up
> only the build name, which made `feature#selector` — the whole of task 2 — invisible to an
> authored design while working perfectly under a direct call;
> (d) `topology.shape_list` refused any OCCT list longer than two on the belief that OCP exposed
> no iterator; it does, and a pocket cutting a slot *through* a part turns one face into five, so
> an ordinary notch was unbuildable.
>
> **A defect that had been in `Sketch.face` since the file was written, found 2026-09-06 by
> driving rung 3** (`71ac1b2`): an inner profile padded as a **boss, not a bore**. The docstring
> said the right thing — *"Later profiles become holes in the first"* — and the code handed each
> one to `BRepBuilderAPI_MakeFace.Add`, which requires an inner wire to carry the *opposite*
> orientation; an unreversed wire is accepted without complaint and integrates as material. A
> 100×100 sketch with a 40 mm circle padded 10 mm measured 112,566 mm³ against 87,434, and
> `IsDone()` was true. Containment is decided by boolean algebra now, in **both** directions —
> draw order is not containment order — with a partial overlap refused rather than read as one
> thing or the other.
>
> **Known and deliberately not changed:** the bare word `vertical` matches a vertical bore's seam,
> which is a parameterisation artefact rather than an edge of the part — but `boss#vertical`
> naming a cylinder's seam is how `catia_measure_item` reports a boss height today, so narrowing
> it is a vocabulary decision rather than a bug fix.
> **Still open:** `catia_draft` in reflect-line mode, blocked not on the silhouette (that exists
> now) but on OCCT taking a neutral *plane* where the mode wants a curve on the face — a ruled
> surface has to be built and the face replaced, which is surfacing work rather than a missing
> argument.

## ERA II — PERCEPTION: THE SYSTEM KNOWS WHAT IT BUILT

##### Phase E3 — Geometric interrogation and the measurement layer #####

> ✅ PHASE COMPLETE (2026-09-08) — every task below is done and tested, with one residual named
> in the phase proof that needs hardware this machine does not have, the same shape as E1 task 7's.
> It is carried by **task 5**, which stays `PARTIAL`: the OCCT half is green and reachable, and
> the cross-backend agreement half is claimed at no gate until a seat has run it.

**~3 engineer-months.**

1. **Bulk measures.** Mass, volume, centre of mass, full inertia tensor, bounding boxes (AABB and
   oriented), surface area, per-face and per-edge measures — native OCCT, in-process, free.
   > DONE (2026-09-05). Tested by: `tests/test_interrogation.py` (39 tests, offline, all against
   > closed-form answers). Code: `app/kernel/interrogation.py`,
   > `occt/metrology.oriented_bounding_box`.

2. **Manufacturability scans.** Wall-thickness scan, draft analysis, curvature/continuity checks,
   undercut detection.
   > DONE (2026-09-05) — these have a premise ("pulled along +Z"), can be inapplicable, and are
   > frequently **sampled**; nothing here runs speculatively and `measure()` never calls it.
   > Tested by: `tests/test_interrogation.py`. Code: `app/kernel/occt/interrogate/` (8 modules).

3. **Clearance, interference and minimum-distance queries between bodies.**
   > DONE (2026-09-08). Tested by: `tests/test_measurement_elements.py` (44 tests, offline).
   > Code: `app/kernel/occt/operations/inspection.py`, `occt/elements.py`,
   > `occt/interrogate/proximity.py`.
   > **This status has now been wrong twice in opposite directions, which is worth recording
   > because both errors were made by reasoning about the code instead of opening the tests.**
   > It first read "not yet wired; that needs E2 task 2" long after E2 task 2 had shipped the
   > element references. The correction on 2026-09-08 fixed that and introduced a second false
   > claim — that `catia_measure_between` was "covered nowhere" — when `tests/test_measurement_elements.py`
   > had carried a whole `TestMeasureBetween` class since 2026-09-05 (commit `eb4d89a`), including
   > the contact, overlap, plane-pair and angle cases. **A status line asserting the absence of a
   > test must be checked by looking for the test.**
   > What was genuinely unpinned was narrower and is now closed: eleven guards over the tool's
   > *interface* rather than the geometry under it — that `kind` picks the headline and does not
   > gate the computation (`closest_points` returned the distance only by accident of ordering),
   > that every word the registry advertises is one the backend takes, that the kind word survives
   > the case and spacing a model gives it, that the payload echoes what each reference resolved
   > to, that swapping the operands swaps the closest pair (the distance is symmetric and the pair
   > is not), that a plane refuses an overlap volume from *either* side of the pair rather than
   > only the first, that a refusal names the tool that refused, and that all four numbers report
   > `MEASURED` with the method named — clearance sounds like something that would be sampled and
   > here it is not, so the claim is load-bearing. Each one was verified by breaking the thing it
   > guards and watching the named test fail; all eleven did.
   > **It also found a false comment in the same module.** `_SUPPORTED_KINDS` said of itself that
   > it was "checked against the registry's own enum by the tests so a kind added to the vocabulary
   > cannot be silently left unimplemented here". No such test existed, so an analysis kind added
   > to `catia_analysis_part`'s enum would have been refused at runtime as "not an analysis this
   > backend runs" — a refusal for a documented option, which an agent reads as a broken part
   > rather than a broken vocabulary. The check now exists and the comment names it.

4. **The measurement payload contract.** A stable, versioned vocabulary of numbers that
   `assertions.py` reads by path (`mass_kg`, `bounding_box_mm.size[2]`, …), backend-neutral,
   documented.
   > DONE (2026-09-05) — `undocumented_paths()` is asserted empty, which is what makes it a
   > contract rather than a list. Tested by: `tests/test_interrogation.py`. Code:
   > `app/kernel/contract.py`.

5. **Honest provenance on every number** — measured vs approximated vs unavailable-with-a-reason.
   > DONE (2026-09-05) — carried as a *sidecar* so `bounding_box_mm.size[2]` still resolves, and
   > read per path by `assertions.py`, so an exact mass is not tainted by a ray-cast thickness
   > beside it. Tested by: `tests/test_interrogation.py`. Code: `app/kernel/provenance.py`.

**Phase proof:** every assertion in the ladder through M4 is measurable, and each measurement
agrees between OCCT and CATIA to declared tolerance.
> PARTIAL (2026-09-15) — **both halves have evidence, and the seat half is short of M4.** OCCT:
> green and reachable via `catia_analysis_part`, `catia_measure`, `catia_measure_item` and
> `catia_measure_between`. Seat: THE QUEUE B2 measured a plate and a bored plate on CATIA
> V5-R33 (2026-09-11/12). Volume and area agree to 0.000%, faces agree, and the one-edge seam
> difference per closed cylinder is understood. Mass differs by 0.127% by design (CATIA's
> *Acier* density). **What is left is the "through M4" half.** `scripts/catia_conformance.py
> --ladder` (written 2026-09-15) hands the seat M1's bracket and every M2 component, names M3
> (no CATIA sheet metal until THE QUEUE E1) and M4 (no geometry) as skipped with reasons, and
> is queued as THE QUEUE B7. Its offline tests were written and not run, at the user's
> instruction. **Not claimable from Linux**: the seat run is the evidence.
> Tested by: `tests/test_interrogation.py`, `tests/test_measurement_elements.py`,
> `tests/test_seat_conformance.py`.

<!-- superseded 2026-09-15 -->
> PARTIAL (2026-09-08) — the OCCT half is green and reachable via `catia_analysis_part`,
> `catia_measure`, `catia_measure_item` and `catia_measure_between`. The cross-backend agreement
> half needs a Windows seat, same as E1 task 7, and is claimed at no gate before then.
> Tested by: `tests/test_interrogation.py`, `tests/test_measurement_elements.py`.

##### Phase E4 — Visual verification: the model looks at the model #####

> ✅ PHASE COMPLETE (2026-09-08) — all four tasks done and tested, **task 4 included since the
> same day**: a picture reaches the conversation on both backends now, by two deliberately
> different routes (in the step row on a CATIA seat, pinned above the composer on the open
> kernel, because the OCCT render endpoint has no history and a copy beside turn 3 would redraw
> itself into turn 9's part).
>
> *Corrected 2026-09-10.* This marker read "with **task 4** left `PARTIAL` on purpose … the OCCT
> render endpoint still has no frontend caller" for two days after that stopped being true. The
> sentence was not written from the code; it was written to satisfy
> `test_a_complete_phase_names_every_task_it_left_open`, which was reporting task 4 open because
> `scripts/plan_progress.py` counted the superseded `PARTIAL` sitting under this task's
> `<!-- superseded 2026-09-08 -->` comment alongside the `DONE` that replaced it. The parser now
> honours that comment. Worth keeping in view: a measurement tool that is wrong pushes its error
> *into* the document it measures, and the wrong sentence is the one a human then defends.

**~4 engineer-months.**

1. **Deterministic offscreen rendering** from canonical views (six orthographic, two isometric,
   plus section cuts) — fixed camera, lighting and resolution, so two renders of the same geometry
   are byte-identical.
   > DONE (2026-09-05, including section cuts). **Hidden-line removal, not OpenGL, and that is the
   > task's own requirement rather than a shortcut.** OCP exposes `V3d`/`AIS`/`OpenGl_GraphicDriver`
   > and a viewer does come up here — but this task asks for two renders to be *byte-identical*,
   > and a GL image is a function of the driver, the sampling and the display server, on a project
   > that develops on Linux and ships on Windows. HLR is arithmetic and the raster under it is
   > integer. Eight canonical views, each built from **three** HLR streams per side (sharp, smooth
   > and silhouette — taking only the sharp edges loses every curved outline, so a cylinder seen
   > from the side renders as nothing). Framing is derived from the part rather than chosen, and is
   > a *value*: `render_views` fits one frame over every view's extent so a six-view sheet is at
   > one scale, and `render_pair` puts two parts through one frame. Determinism is defended at each
   > step where it is easy to lose: no anti-aliasing, `floor(v+0.5)` rather than banker's rounding,
   > a dash phase carried along the whole polyline rather than restarted per segment, curve
   > flattening at a deflection *relative to model size*, and a hand-written PNG encoder — three
   > chunks, filter 0, fixed zlib level — because an outside encoder can add a timestamp chunk or
   > change its filter heuristic between versions and silently break the hash.
   > **Section cuts close this task, and the work is the vocabulary rather than the drawing:**
   > `mid_section` / `offset_section` / `section_named`, with the plane's normal pointing at the
   > material that is *removed* — `catia_split`'s own convention, because two conventions for one
   > question in one codebase is how a part ends up mirrored with every test green. That convention
   > is also what lets `natural_view` pick the camera without a second argument. A plane that
   > misses the part is **refused**, since an uncut part returned from a section call looks exactly
   > like a successful section of a solid one. The cut is a finite box rather than
   > `BRepPrimAPI_MakeHalfSpace` — OCCT's booleans are materially less robust against an infinite
   > solid and fail by returning the shape unchanged. The cut face is found by **geometry, not
   > boolean history** (history is per-operation and lost the moment the shape is passed on) and is
   > hatched at 45°, filling by the **even-odd rule across every wire at once**, so a bore falls
   > out of the parity arithmetic with nothing having to identify it as a hole.
   > **Writing the sections found that this task shipped rendering every part upside down.**
   > OCCT's `gp_Ax2` defines its Y axis as `direction × X`, the opposite of the up vector
   > `views.py` declares, so the top of a 40 mm box seen from the front came back at y = −40.
   > **No check could see it**: a consistently mirrored image is still byte-identical to itself so
   > determinism held, a diff of two mirrored renders is still correct so task 3 held, and a
   > wireframe of a plate is entirely plausible upside down. It is precisely the wrong-orientation
   > error a render hash exists to catch. Fixed in the projection rather than the raster, so view
   > millimetres do not lie, and pinned by `TestTheRenderIsTheRightWayUp`.
   > Tested by: `tests/test_render.py` (37 tests, offline). Code: `app/render/`.

2. **A vision-model check** — render, ask a VLM whether the result matches the request.
   > DONE (2026-09-05), and built around the task's own stated limitation rather than in spite of
   > it. A VLM will confidently approve a subtly wrong part, so `VisualReview` has **no `approved`
   > or `passed` property** for a caller to gate a release on — the flag that exists is
   > `objected`, and a test asserts the others do not appear. There are three outcomes and
   > **`unchecked` is never a pass**, the same rule `assertions.py` applies to an unmeasured
   > assertion: no vision model, an unreachable provider, a blank render, a model that says
   > 'unsure', and a model that says 'differs' while naming nothing specific all land there with
   > the reason in words. Nothing raises — a visual check improves an answer and must never be why
   > there is not one.
   > **The dangerous case is Ollama**, which does not refuse an image handed to a text-only model:
   > it drops it and answers anyway, so the shipping default (`qwen2.5-coder`, which has no eyes)
   > would return a confident description of nothing with no error and no flag — a check that
   > manufactures agreement, which is worse than no check. `_sees()` refuses on two *structural*
   > signals with no model-name list to go stale: `/api/show` publishes `capabilities`, and only a
   > multimodal model has a `projector_info` block at all. `AI_VISION_MODEL` names the model that
   > looks, because locally it is a second pull. Images are unlabelled on the wire, so **order is
   > the only thing tying an image to what it is a picture of** — the prompt names the order and
   > the code sends them in it. `num_ctx` is sized for the pictures as well as the words, because
   > Ollama truncates a prompt from the front in silence. The schema puts `describes` before
   > `verdict` so a constrained decoder must state what it sees before it judges, and the prompt
   > forbids reading any dimension off a drawing that has no scale. Three views by default, not
   > eight: images dominate the cost and three perpendicular directions already fix the silhouette.
   > Tested by: `tests/test_vision.py` (30 tests, offline). Code: `app/ai/vision.py`,
   > `LLMProvider.look`, `prompts.VISUAL_CHECK_SYSTEM`, `schemas.VisualCheck`.

3. **Render diffing** — before/after pixel diff, so review surfaces *what visibly changed*.
   > DONE (2026-09-05). **Diffs on ink, not shade**: a line that went from hidden to visible has
   > not moved, and flagging it would light up every part whose features merely reordered behind
   > one another. Added and removed are separate colours because "a pocket appeared" and "an edge
   > vanished" are different facts. Two renders framed differently are **refused** rather than
   > diffed — independently framed, a part 2 mm bigger changes every pixel and the diff says
   > nothing. Measured on a plate gaining a Ø14 pocket: 321 pixels arrived, 0 gone, 3.3% of the
   > ink. Tested by: `tests/test_render.py`. Code: `app/render/diff.py`.

4. **Renders flow into the conversation** — the user sees what the agent sees (P5 owns the
   surface).
   > DONE (2026-09-08) — both halves. Tested by: `Kryova-frontend`
   > `src/lib/kernel-render.test.ts` (14), `src/components/kernel-part-view.test.tsx` (12),
   > `src/hooks/use-catia-status.test.ts`; backend `tests/test_kernel_routes.py`. Code:
   > `Kryova-frontend`: `src/lib/kernel-render.ts`, `src/components/kernel-part-view.tsx`,
   > `api.kernelRender`, `types/catia.isLocalKernel`; backend `app/main.py` CORS.
   > **The OCCT half is not a copy of the CATIA one, and the difference is the design.** On a
   > seat the picture arrives *inside a tool result* and belongs to the step that produced it.
   > `GET /kernel/conversations/{id}/render` has no history — it draws the document as it stands
   > — so the same placement would have been a lie: a picture sitting beside turn 3 would
   > silently redraw itself into turn 9's part on the next build, and a transcript that rewrites
   > its own evidence is worse than one with no pictures in it. It is therefore pinned to the
   > live state, above the composer, exactly where the CATIA chip sits and for the same reason.
   > What to show is decided from `GET /catia/status`, which the chat already polls per
   > conversation, rather than by firing the render and reading its refusal: the endpoint's three
   > 409s (wrong backend, evicted, nothing built) are English written for a person, and the
   > status call carries the same three facts as data. Nothing is drawn on a CATIA deployment and
   > nothing is drawn before a part exists; an **evicted** part is said in words, because that is
   > a real loss the user has to know about and it is not the same fact as "nothing built yet".
   > **Two defects found on the way, both in the open kernel's existing presence in the product.**
   > (a) `X-Kryova-Blank` and `X-Kryova-View` were set by the render route and absent from the
   > CORS `expose_headers`, so no browser could read them — headers set for nobody. Without them
   > the only way to tell an empty frame from a drawn part is guessing from the compressed byte
   > count, which is a heuristic where the server has already measured the answer. `ETag` was
   > unexposed too, and it is the whole basis of the 304 the endpoint's docstring promises.
   > (b) `CatiaStatusOnline` was the only `connected: true` shape the frontend modelled, and the
   > open kernel is another one with **none** of the device fields — so `describe()` read
   > `device_name` off it and put the literal string *"undefined is connected, running CATIA"*
   > into the status chip's tooltip and its screen-reader text on every `GEOMETRY_BACKEND=occt`
   > deployment: a sentence naming a machine that does not exist, on the one backend where no
   > machine is involved. Fixed by teaching the union about the third shape, which made `tsc`
   > refuse the same mistake in `catia-bridge-panel.tsx` as well — that one was rendering an
   > empty version pill. 22 guards, each verified by breaking the thing it guards.

   <!-- superseded 2026-09-08 -->
   > PARTIAL (2026-09-07) — a picture reaches the conversation on the CATIA path. The transcript
   > renders the picture a tool returned instead of `JSON.stringify`-ing a media id and a byte
   > count. That closes the gap the standing rule had been sitting over — the whole product is
   > built on somebody *looking* at what `catia_capture_view` returns, and the product could not
   > show it. The detector claims only that a result *might* carry a picture and settles it by
   > measurement: it fetches the bytes and draws an `<img>` only once the blob's own MIME type
   > says `image/*`. It is deliberately not keyed to `catia_capture_view` by name and deliberately
   > not keyed on `width_px`/`height_px` — only the *mock* daemon populates those, so that
   > detector would have worked perfectly in tests and shown nothing on the machine with CATIA on
   > it. **The remaining half is the OCCT one**: `GET /kernel/conversations/{id}/render` has no
   > frontend caller, so an agent driving the open kernel still builds a part nobody in the
   > product can see. Code: `Kryova-frontend`: `src/lib/tool-media.ts`,
   > `src/components/tool-image.tsx`, `api.mediaBlob`.

**Creative leverage:** deterministic rendering makes a render hash part of the geometry's identity
— a third check, blind where mass and plan-digest are blind (mirrored, inside-out, wrong
orientation).

**Honest limitation:** a VLM will confidently approve a subtly wrong part. This catches gross
errors — which are the common ones. A filter, never a sign-off.

##### Phase E5 — Assertions, regression, and the self-correcting loop #####

> ✅ PHASE COMPLETE (2026-09-09) — all five tasks done and tested. The phase had stood at four
> of five since 2026-09-05 with **task 2 blocked on E11**, which closed the same day; the
> binding was already in the requirements model and what it was missing was a requirement's
> `gap` reaching `sensitivity.aim`, which is now pinned on a part OCCT actually builds.

**~4 engineer-months remaining.**

Landed as the foundation (2026-09-04): `assertions.py` (pass / fail / **unmeasured** — and
unmeasured is never a pass), `diff.py` (what changed, how far it reaches, `builds_the_same`),
`correct.py` (bounded loop with exact stopping rules — no-progress and cycle detection are *exact*
because the compiler is deterministic).

1. **An assertion library for machines, not parts**: interference-free through a motion range,
   stack-up within tolerance, first natural frequency above threshold, minimum wall, mass and cost
   budgets, factor of safety against a named load case.
   > DONE (2026-09-05). **It exists because `assertions.py` cannot express a machine.** That
   > checks a claim about a number already in a payload, which is right for a part and cannot say
   > whether an arm clears its frame through travel, whether six tolerances still fit, or whether
   > the first mode is above the drive frequency — **those claims must be *produced*, not read.**
   > So a machine check is a *measurement source*: it computes a number, files it under a path
   > with provenance, and the existing `Assertion` machinery compares it. No second comparison
   > language, and `UNMEASURED`/`gap`/the report come for free. **The tools are injected, never
   > imported** — the package's load-bearing property is that it runs offline with no kernel and
   > no solver, so reaching for `app.kernel` here would pull ~166 MB of OCP into every test in the
   > package. A tool that is absent is `unavailable` **with a reason naming what is missing**.
   > Eight checks: mass budget, envelope (three assertions, because "it does not fit" is not
   > actionable and "40 mm too long in Z" is), minimum wall (carrying the ray cast's own
   > `approximate` through, so a wall passing by 0.01 mm on a sampled measurement is never read as
   > passing), clearance through a motion range, first natural frequency, factor of safety,
   > buckling factor, and stack-up. **Clearance through motion is sampled and says so** — a
   > continuous swept-volume check is a different and much harder problem, so the moving part is
   > posed at N points, the answer is marked approximate, the note states that a collision between
   > two adjacent poses is invisible to it, and fewer than three samples is refused because that
   > is not a sweep. **Stack-up offers both methods and defaults to neither being silent**: worst
   > case is what a safety-critical fit is designed to, RSS is what a production run sees when
   > contributors are independent and is often half the size, so the method chosen is named in the
   > claim. **A cost budget is declared and honestly unavailable** — there is no cost model (E13
   > owns it), so it reports `UNMEASURED` with the reason rather than being quietly left out of
   > the library, and becomes real the day a tool answers `cost`.
   > Tested by: `tests/test_design_machine_checks.py` (33 tests, offline). Code:
   > `app/design/machine_checks.py`.

2. **Assertions bound to *requirements*** — "meets REQ-014", not "mass_kg <= 4.2".
   > DONE (2026-09-09) — unblocked by E11 closing the same day, and it is two halves rather than
   > one. The **readable** half is `Requirement.assertion()`: a requirement compiles to an
   > assertion named after its id, so a failing report says *"REQ-014 NOT MET — the plate shall
   > weigh no more than 0.30 kg"* and the engineer reading it knows whose requirement they are
   > about to argue with. There is deliberately no second verdict type anywhere in that path —
   > `Outcome` is `app.design.assertions`' own, so `UNMEASURED` still is not a pass one layer up.
   >
   > The **useful** half is that the same result carries a `gap`, and the gap is what
   > `sensitivity.aim` turns into a parameter and a distance. Pinned end to end on a real part:
   > a 60x40x20 steel plate weighs 0.37776 kg, REQ-014 asks for 0.30 kg, the requirement comes
   > back `FAILED` with a gap, and `aim` answers *reduce thickness_mm to 15.882* — which rebuilt
   > through OCCT weighs 0.30 kg. **Nothing in `app/design/` imports `app/requirements/` to do
   > that and nothing in `app/requirements/` imports a kernel**: the two layers meet at a number.
   >
   > Two things the test records rather than smooths over. Which parameter to move is **named,
   > not discovered** — a rectangular plate's mass is exactly as elastic in width as in height as
   > in thickness, so `most_influential` is a genuine tie and picking a winner from it would be
   > arbitrary dressed as analysis; `aim` takes the choice as an argument for this case. And a
   > first-order step aimed at a hard bound lands **on** it: the repaired plate measures
   > 0.30000000000000093 kg and a requirement with zero tolerance correctly refuses it, so the
   > requirement carries 1 g of slack. Loosening the comparison to make that pass would be a
   > retry counter deciding an engineering question.
   > Tested by: `tests/test_requirements_against_a_part.py::TestARequirementCanAimItsOwnRepair`.

3. **Diagnosis quality: sensitivity.** Which parameter moves this measurement most, computed by
   finite difference over the free geometry, so a repair is aimed rather than guessed. The
   literature's bluntest finding stands: a validator that cannot say *why* is a retry counter.
   > DONE (2026-09-05). `assertions.py` says the part is 3.1 kg over and `correct.py` can try
   > something and see whether the number moved, but neither can say which of eleven parameters to
   > move or how far. `sensitivity.py` finite-differences each free parameter against one
   > measurement and `aim()` turns a failing assertion's `gap` into a parameter and a distance. It
   > is affordable only because of Decision 1 — a probe was minutes of a CATIA workstation and is
   > a headless build here. Four things make the difference between a number and a lie, each
   > pinned by a test:
   > (a) **a build that fails at the perturbed value is not zero sensitivity** — stepping a fillet
   > past what the geometry carries is ordinary, and reporting 0.0 tells the loop to leave alone
   > the one parameter that is at its limit, so it comes back unprobed with the reason and ranks
   > last rather than first;
   > (b) **a topology change is not a derivative** — a step big enough to make a fillet swallow a
   > face compares two different parts, so face/edge/solid counts are differenced too and the
   > influence is refused with an actionable message; a payload carrying no counts is reported
   > `topology_unchecked` rather than assumed unchanged;
   > (c) **only free parameters are probed** — a parameter with an expression is a *consequence*,
   > and a derived one is **excluded with its formula in the reason** rather than dropped, because
   > absent from a ranking reads as "no influence", a different claim;
   > (d) **the ranking is by elasticity, not derivative** — ∂mass/∂radius is kg/mm and
   > ∂mass/∂angle is kg/degree, so "which matters most" is meaningless until the two are
   > dimensionless; the test that pins it has a tiny slope on a large parameter correctly outrank
   > a large slope on a small one.
   > Central differencing where both sides build, one-sided where only one does, with the scheme
   > recorded per parameter because the two are different orders of accuracy. `aim` refuses rather
   > than dividing by a negligible derivative, carries a first-order caveat on every suggestion,
   > and invents no baseline value when it was not given one.
   > Tested by: `tests/test_design_sensitivity.py` (34 tests, offline). Code:
   > `app/design/sensitivity.py`.

4. **The mission ladder as a permanent regression suite.**
   > DONE (2026-09-05). Decision 5 lists nine machines and calls each rung "a permanent regression
   > test", and until now nothing executed it: "M1 works" was a claim from the day somebody last
   > tried it by hand. `app/design/missions.py` declares all nine rungs, gives M1 a real
   > `DesignSpec`, and checks it against closed forms computed **from the same constants the spec
   > is built from** — so a changed dimension moves the design and its claims together, where a
   > number typed in by hand would stop describing the part and fail looking like a geometry bug.
   > M1 builds through the real `OcctRunner` in twelve calls and its eight claims hold: volume,
   > mass, surface area, thickness, footprint, one solid, eleven faces, and a centroid at
   > mid-thickness — the last three chosen because **a bore that stopped short keeps the volume
   > plausible** and only an independent quantity catches it. **The eight rungs that cannot be
   > built are `PENDING`, which is never a pass and never a skip** — `Outcome.UNMEASURED` applied
   > one level up, each naming the phase that owns the gap. But a pending rung does *not* make the
   > report red, because a suite that is red for the two years it takes to reach M9 is a suite
   > somebody switches off: `ok` answers the regression question and `complete` answers the
   > programme question, and the sentence a human reads is "1/9 rungs pass, 8 not yet buildable".
   > **A rung that claims to build and then does not is a failure whatever the reason** —
   > deliberately unlike `conformance.py`, which separates a coverage gap from a real stop because
   > it is asking which of two backends is behind; here the mission *declared* it builds, so an
   > operation that regressed into unimplemented has falsified the claim. Each rung gets its own
   > runner from a factory, because a mission that passed on the previous one's leftovers would
   > report the right volume for the wrong reason.
   > **Writing it corrected a claim this file would otherwise have carried:** the fillet-before-bore
   > order was justified as necessary, on the belief that the bare word `vertical` would catch the
   > bore's seam. Measured — it does not; bore-first gives the same volume to 1e-12 and the same
   > eleven faces. What *is* load-bearing is the `feature` scope: with a 20×20×10 boss on the slab,
   > scoped removes 171.68 mm³ and unscoped removes 386.28 mm³, having rounded the boss too and
   > reported success. The order now stands as machining order and says so.
   > Tested by: `tests/test_design_missions.py` (31 tests) — M1 green on the real kernel, 8 rungs
   > PENDING and counted. Five guards verified by breaking them; deleting the pending rungs from
   > the report flips `complete` to true, which is the exact false green the split exists to
   > prevent. Code: `app/design/missions.py`.

5. **`catia_set_parameter` on the open kernel** — without it tasks 1 and 3 are unreachable from a
   conversation, because both are loops that change a dimension and measure again.
   > DONE (2026-09-06, `b9b1cb9`) — and nothing had said so before. On `GEOMETRY_BACKEND=occt` the
   > sensitivity probe this plan justifies as "minutes of a CATIA workstation, a headless build
   > here" could be driven from the design IR and not from a conversation. Measured by rung 3 of
   > the ladder: told to adjust a thickness until the mass came right, the agent had no way to,
   > and padded the same sketch four times. **A part built in conversation has no parameter set,
   > so its build log is one** — every mutating call recorded, every numeric argument a dimension,
   > and setting one rewrites the call and replays the part from the top. That is this plan's own
   > "specification that is compiled", applied to a part assembled call by call rather than
   > compiled from a spec, and it inherits the property that matters: a recompiled spec has no
   > downstream edit to shatter, so replay allocates the same names in the same order and `Pad.1`
   > is still `Pad.1`. The replay builds into a *fresh* document and is swapped in only on
   > success, so a value the geometry cannot carry costs a refusal and nothing else.

**Creative leverage:** sensitivity is nearly free once geometry is free — a few hundred kernel
calls. On CATIA it was minutes of a workstation per probe. This is Decision 1 compounding.
## ERA III — PHYSICS THAT DECIDES

##### Phase E6 — The solver federation #####

> ✅ PHASE COMPLETE (2026-09-08) — all six tasks done and tested. It carried three named
> residuals and **all three have since been narrowed or closed, on two different machines**;
> what is left of each is written here rather than left to the reader to re-derive.
>
> 1. ~~**Nothing has been round-tripped through a real `ccx`.**~~ **CLOSED on the Windows
>    seat, 2026-09-09** (THE QUEUE A1–A5, ccx 2.23). The package is verified rather than
>    documented, and the run found two defects that were fatal to every quadratic beam deck
>    this repo had ever written — see task 3.
> 2. ~~**Nothing produces a shell or beam mesh.**~~ **HALF CLOSED on Linux, 2026-09-09.**
>    `gmsh_mesher.generate_shell_mesh` produces a `ShellMesh` in all four element types, so
>    task 3's element strategy is now exercised by a mesher and not only by authored meshes.
>    **A `BeamMesh` still has no producer** and its meshes are still authored.
> 3. ~~**Loads on 1-D and 2-D regions have no tributary-area rule.**~~ **HALF CLOSED on
>    Linux, 2026-09-09.** `app/solve/shell_loads.py` is the 2-D rule, with consistent-load
>    factors per element type. **The 1-D rule is still absent**, which is why
>    `write_frame_deck` still takes a nodal force vector rather than a `LoadCase`.
>
> The seam those three left open — nothing joined mesh, load and deck into a *run* — closed the
> same day: `app/solve/calculix/shell.py`. `ShellSolver` is **not** a `Solver` subclass, because
> the ABC is `solve(mesh: TetMesh, case: LoadCase)` and a shell needs its `ShellSection` as a
> third argument; `PlaneSolver` declined the same widening. **The residual that replaces it is
> honest and small: none of the shell path has ever been through a real `ccx`.** The deck it
> writes is pinned offline; what CalculiX does with that deck is THE QUEUE's A6 and belongs to
> the seat. That is a stated gap, not a gap the code pretends is closed.

**~7 engineer-months.**

1. **A `Solver` implementation backed by CalculiX across a subprocess boundary** — write `.inp`,
   run `ccx`, parse `.frd`/`.dat`. The `Solver` ABC does not change.
   > DONE (2026-09-06) — the registry landed, so `SOLVER_BACKEND=calculix` now reaches a real
   > solve. Verified against CalculiX 2.23: σ = 25.000000 MPa vs F/A to 5.7e-16 on both solvers
   > through the setting, versions recorded on the job row (`linear-static 0.2.0+sha`,
   > `calculix 2.23`). Tested by: `tests/test_solver_registry.py` (19 tests). Code:
   > `app/solve/calculix/`, `app/solve/registry.py`. Report: `docs/verification-2026-09-06/`.

2. **Loads and BCs from the *existing* `loads.py`/`selection.py` vocabulary** mapped onto CalculiX
   node/element sets — tributary-area distribution preserved, geometric selectors preserved. This
   surface must not be rewritten; it is what the agent drives.
   > DONE (2026-09-06). Tested by: `tests/test_solver_registry.py`. Code:
   > `app/solve/constraints.py`.

3. **Element strategy, informed by the CalculiX manual rather than habit.** **C3D10 is the
   documented recommended solid** (stable, robust); shells and beams are *expanded* internally
   (S8R → 20-node brick, B31 → C3D8I), which changes thickness-direction stress recovery and is a
   known source of surprise — record it in the integration notes and test against it. A frame
   meshed as solids is a mesh nobody can afford; beams and shells are not optional.
   > DONE (2026-09-08) — the strategy is one module, `app/solve/calculix/elements.py`, and the
   > expansion is a table rather than a comment: S3→C3D6, S4→C3D8I, S6→C3D15, S8R→C3D20R,
   > B31→C3D8I, B32→C3D20R, each with the consequence it carries for stress recovery. Three
   > things follow from the expansion and each is guarded. **`OUTPUT=2D` is written on every
   > expanded model** — ccx's default stores results at the *expanded* nodes, which outnumber
   > the submitted mesh's and are all in range, so `frd.displacements(frd, mesh.node_count)`
   > would read a different model's answer with nothing to complain about. **A `clamp` holds
   > all six degrees of freedom on a shell or a beam and three on a solid** (`constraints.
   > local_dofs`) — a clamp written as three is a pin, and a cantilever on a pin is a different
   > structure; a `custom` fixture naming three letters stays a pin, deliberately and testably.
   > **`check_restraints` gained a six-degree-of-freedom form**, without which a beam clamped at
   > one node reads as under-constrained and a straight beam reads as a degenerate mesh — two
   > refusals of correct models. Supporting vocabulary: `app/mesh/structural.py`
   > (`ShellMesh`/`BeamMesh`) and `app/solve/sections.py` (thickness, RECT/CIRC/PIPE/BOX
   > profiles with closed-form area and second moments, and the `n1` orientation an RHS is 2.25×
   > stiffer or softer for). Deck: `deck.write_frame_model`/`write_frame_deck`. Section
   > properties are checked against numerical integration over the real outline, not against the
   > formula they were written from. **Unverified until a seat run:** no `ccx` here, and no
   > mesher produces one of these meshes yet. Tested by: `tests/test_solver_calculix_elements.py`
   > (49), `tests/test_solver_sections.py` (26), `tests/test_mesh_structural.py` (35) — 14 guards
   > verified by breaking what they guard.
   > **VERIFIED ON THE SEAT (2026-09-09), and it took two defects with it.** ccx 2.23 now
   > reads every element this module chooses: `OUTPUT=2D` holds on both keywords — S4 9/9,
   > S8R 21/21, S3 9/9, S6 25/25 and the beam 5/5 nodes, all at the *submitted* numbering —
   > and the expansion table is confirmed by node count (B31→8, B32→20, S4→8). The two the
   > run found were invisible offline and both made a *beam* unsolvable.
   > **(a) `BeamMesh.connectivity` wrote `[start, end, middle]`.** ccx numbers a three-node
   > beam along the member, so the far end was read as the midside and the element folded
   > back on itself — `*ERROR in e_c3d: nonpositive jacobian determinant`. No quadratic beam
   > deck this repo ever wrote could solve, and the test asserting that order was wrong in
   > the same direction as the code. Shells were unaffected and are checked.
   > **(b) A hollow profile needs `B32R`.** CalculiX carries `SECTION=BOX` and `SECTION=PIPE`
   > on that element only, refusing B31 and B32 at parse time and naming it; the sweep across
   > B31/B32/B32R × RECT/CIRC/PIPE/BOX and one-to-eight data values shows it is the section
   > type that decides it, not the value count. So the RHS — the profile this task's own
   > vocabulary calls the workhorse of a welded frame — produced a deck the solver would not
   > read. `choose_element` now takes the section and substitutes B32R, and refuses a linear
   > mesh in words because there is no three-node element to substitute.
   > A4's original worry, the data-line order, is *correct as written*.
   > Tested by: `tests/test_solver_calculix_elements.py` (55) and
   > `tests/test_mesh_structural.py`. ~~**Still unverified:** no mesher produces one of these
   > meshes~~ — **a shell mesher landed on Linux the same day**
   > (`gmsh_mesher.generate_shell_mesh`, `tests/test_mesh_shell.py`), so S3/S4/S6/S8R are now
   > exercised on meshed geometry — a revolved hemispherical zone whose area the mesh recovers
   > to 0.1% — and not only on grids written out by hand. It settled the ordering debt
   > `app/mesh/structural.py` named: the gmsh-to-CalculiX midside permutation is the
   > **identity** for both shapes, and is re-checked by coordinate on every quadratic mesh
   > rather than assumed. **Still authored:** every *beam* mesh here, for want of a 1-D
   > mesher — which is the half of this residual that is still open.

4. **Analysis types unlocked by task 1**: nonlinear static, large deformation, plasticity, contact,
   bolt pretension, modal, buckling, transient dynamics, coupled thermal-stress.
   > DONE (2026-09-08) — thermal closed the open half, and it was a live defect rather than a
   > missing feature: `LoadCase.delta_t_k` reached the in-house solver and reached CalculiX
   > **not at all**, so one case returned `sigma = -E alpha dT` from one solver and exactly zero
   > from the other, both reporting success. `write_deck` now writes `*INITIAL CONDITIONS,
   > TYPE=TEMPERATURE`, `*EXPANSION, ZERO=` and `*TEMPERATURE` — all three, because the physics
   > reads a *difference* and two of those numbers are the two halves of it — and **refuses a
   > temperature change on a material with no expansion coefficient**, which ccx accepts and
   > answers with zero thermal stress. `write_frame_deck` carries the same cards. Tested by:
   > `tests/test_solver_calculix.py` (`TestATemperatureChangeReachesTheDeck`, 9) and
   > `tests/test_solver_calculix_elements.py`; every guard verified by breaking it.

5. **The hand-written solver as fast path and oracle** (Decision 2). Any linear static case must
   agree between it and CalculiX, and a disagreement is a bug in the integration.
   > DONE (2026-09-06). Tested by: `tests/test_solver_registry.py`. Code: `app/solve/oracle.py`.

6. **Solver-failure taxonomy**: non-convergence, singular stiffness, distorted elements, contact
   chatter — each mapped to a diagnosis the agent can act on, in the codebase's existing register
   ("say what to do next").
   > DONE (2026-09-06). Tested by: `tests/test_solver_registry.py`.

**Gate G1 opens after this phase.**
> RUN 2026-09-06, DID NOT PASS — rung 3 failed. Rung 3 is carried forward and G1 runs again.
> See `docs/verification-2026-09-06/`.
> **The gate is still the only thing that can verify this phase**: tasks 3 and 4 were both
> written from the manual on a machine with no `ccx`, and the re-run below settled neither —
> it ran the oracle isothermally and submitted no shell or beam deck. Those two items are
> listed with G1 in *Stop gates* and as `A2`/`A3` in THE QUEUE.
> **RE-RUN 2026-09-08, STILL DID NOT PASS** — `docs/verification-2026-09-08-G1/`. Rung 3 is
> discharged (`catia_set_parameter` verified end to end) and the `ccx`-vs-`linear_static`
> oracle passes, after this run found and fixed the reason it could never have: CalculiX
> truncates numeric fields at 20 characters, `deck._number` wrote `repr` at up to 23, and the
> offline suite could not see it because every mesh in it comes from an exact primitive whose
> coordinates are `5.0` and `200.0`. **Task 1's "verified against CalculiX 2.23" was true only
> of exact primitives** — the first real STEP import refused to solve at all.
> G1 now blocks on the load-bearing prompt: `draft_load_case`'s six absolute direction words
> cannot name the end of a beam that does not lie along X, and nothing sense-checks the result.
> **This phase is also still open** — task 3 is NOT STARTED and task 4 PARTIAL, so both G1 runs
> have tested a precondition that had not finished. C3D10 (task 3) would likely improve the
> 411-element tet4 numbers this gate had to judge.
> **Two of the three items G1 named are fixed (2026-09-09)** — the part-relative face vocabulary
> and the sense check on a drafted load case; see the G1 entry in *Stop gates*. The third, a
> convergence check in the request path, is E7's.
>
> **"Why not just drive CATIA's Analysis & Simulation?" — asked 2026-09-11, and measured
> rather than argued.** It is a fair question: CATIA ships GPS, GAS, ELFINI and Advanced
> Meshing Tools, `app/catia_kb/commands/analysis.py` already carries their vocabulary and
> failure modes, and Decision 1 says the customer works in CATIA. Probed on the seat
> (THE QUEUE **B6**): **the licence is present** — `Documents.Add("Analysis")` returns a real
> `CATAnalysis` — and **the automation surface is not**. The `AnalysisManager` object offers
> `AnalysisSets`, `AnalysisModels`, `Parameters` and `GetItem`, and **no `Compute`, no `Solve`,
> no factory**; `AnalysisFactory`, `CreateAnalysisCase` and `AnalysisEntities` are all
> `AttributeError`. V5 automation can navigate an existing `.CATAnalysis`; it cannot author a
> restraint, a load or a mesh, and it cannot start a solve. Driving GSA therefore means driving
> its **GUI**, one dialog at a time, on one seat — for a convergence study that is 3–5 solves
> and for an optimisation sweep hundreds.
> That is the same argument Decision 1 already makes about geometry, arriving at the same
> answer: **CATIA is the delivery target, not the engine.** Three things would break if it were
> the engine, and they are the three the product is for — a fingerprinted provenance chain
> (`app/verify/`) cannot bind a black box; CI has no CATIA, so every physics test would need a
> seat; and a customer or reviewer without GPS/EST could not re-run the study, which is what
> Decision 4 exists to prevent.
> **What is worth building, when someone wants it:** the mirror of the geometry story — Kryova
> solves, and the model *and its load case* land in CATIA for the customer to re-run there; and
> a GSA result as an **independent oracle** beside CalculiX in `app/solve/oracle.py`, which is
> the strongest form of a validation claim this product can make. Neither is scheduled, and
> neither is blocked by anything except somebody deciding it is worth a seat's time.

##### Phase E7 — Verification and validation *(needs an ME)* #####

> ✅ PHASE COMPLETE (2026-09-15) — all seven tasks done. **Task 1's last case, LE3, runs**: the
> full hemisphere in tri6 through `ShellSolver`, 184.97 mm against the published 185 mm on
> ccx 2.20-1 (measured on Linux in docker, one grid at h = 250 mm). **Its tests were written and
> not run**, at the user's instruction of 2026-09-15 that the Windows machine runs the tests, and
> the recorded V&V artefact is stale until that machine re-records it — THE QUEUE A6.
>
> The 2026-09-11 marker read as follows, heading hyphenated so the parser does not read it as
> live: *PHASE-COMPLETE (2026-09-11)* — **task 1 stays `PARTIAL` on one case, LE3, and that is
> the only thing open.** Tasks 2 through 7 are done and tested.
>
> **Task 7 was added and closed on the same day, and the reason is worth keeping.** The GUI run
> of 2026-09-10 (night) showed this phase's central claim being broken on the surface the user
> actually reads: the agent stated a pass/fail verdict against the engineer's own stress limit,
> with a quantified margin, from a single-grid solve whose own record said `converged: false`.
> Every layer underneath was honest — the result carried the flag, and the frontend already
> refuses to paint an unconverged number green. The chat read neither, and **the chat is the
> product.** Re-running it three times then found the larger half: the default linear-tet mesh
> was scattering peak stress by 2.8× across identical inputs and getting deflection wrong by
> 3.6× every time, while every NAFEMS case in this phase was validating itself with quadratic
> elements. Both are fixed; the marker is taken back because both are fixed, not because the
> deadline arrived.
>
> The 2026-09-09 marker read as follows and the rest of it still stands. Its heading is
> hyphenated on purpose: `scripts/plan_progress.py` recognises the unhyphenated spelling, so an
> archived marker reproduced verbatim would be read as a second live one — the "two statements,
> one of them stale" failure that parser exists to catch. (This sentence may not spell it
> either, for the same reason.)
>
> ✅ *PHASE-COMPLETE (2026-09-09)* — **on this machine.** Tasks 2, 3, 4, 5 and 6 are done and
> tested; task 1 stays `PARTIAL` on one case, **LE3**. The marker is taken here rather than
> withheld indefinitely because holding it for one case would make this phase indistinguishable
> from one with unwritten code in it. Precedent is E1, whose marker carries the same shape of
> residual. **Anyone reading this on the Windows machine: E7 is not finished until A1–A6 have
> run.**
>
> **This marker said LE3's remainder "is not work — it is a *hardware wait*", and that was
> wrong within the day.** The seat's A1–A5 run showed the shell *deck* was never the only
> obstacle, and A6 was reclassified from a tick to a phase task; two of its three prerequisites
> were then built on Linux the same day (a shell mesher and a shell load path — see E6's
> marker), and the seam followed within hours: `ShellSolver` joins mesh, load, deck, run and
> reader into one call, and no shell `.frd` reader was needed because the existing one keys off
> node count. **So the code half is done.** What LE3 waits on now is exactly two things:
> **sourcing** — its radius, thickness and hole angle are in a figure the cited Abaqus page does
> not put in its text, so they are not sourced and must not be recalled; and **hardware** —
> nothing in the shell path has ever been through a real `ccx`, and `tests/test_shell_solver.py`
> pins what the deck *says* rather than what the solver *does* with it. Anyone tempted to write
> "hardware wait" against a blocked case should check which of the three it actually is; for
> most of a day this one was neither.

**~4 engineer-months.**

1. **The NAFEMS standard benchmarks** (linear elastic, free vibration, thermal) as an automated
   suite. Reference values are reproduced in publicly readable vendor verification manuals
   (Abaqus, Ansys, DIANA) — a free, legitimate route to the targets.
   > DONE (2026-09-15) — **all five cases run, and `Blocker` is empty.** LE3, the hemisphere
   > under point loads, was the last, and it is encoded as the **whole hemisphere** rather than
   > the published quarter (`app/verify/le3_geometry.py`, `nafems.run_le3`). The quarter needs
   > rotational restraints on two symmetry edges, and ccx expands S6 into wedge solids tied by
   > knots, where a rotational restraint is the least trustworthy card in the deck; the full
   > model needs only three isostatic translational supports (E xyz, A yz, C z). Two
   > consequences, both measured before they were encoded: **each point load is 4 kN**, because a
   > point force on a symmetry plane of the quarter stands for twice itself on the whole body —
   > 2 kN per point gave a half-diametral change of 92.487 mm and 4 kN gave **184.97 mm** against
   > 185 mm (ccx 2.20-1 in docker, tri6, h = 250 mm) — and the quantity is **half the diametral
   > change A–A'**, which a rigid-body motion cannot move. The geometry is four revolved quarters
   > **sewn** with OCCT, because a gmsh-built four-patch model meshed as disconnected patches (65
   > duplicate nodes along the seams) and split the load at A between two unjoined nodes. Three
   > seams widened, stated: `RunProvenance.mesh` takes a `ShellMesh` (area, no sliver count);
   > `ShellMesh` gained `element_count`/`area` so a convergence study places it as a 2-D level;
   > `Blocker.NO_SHELL_SOLVER` was deleted with its last case. Sizes 500/355/250 mm on the 1.4
   > rule, stated before any sweep. **Not run here**: the tests below were written on Linux on
   > 2026-09-15 and not executed, at the user's instruction; the three-grid study has not been
   > recorded, and the fingerprint moved (`app/mesh/structural.py`, `app/verify/*`), so the
   > trust register publishes nothing until the Windows machine re-records (THE QUEUE A6).
   > Tested by: `tests/test_verify_le3.py`, `tests/test_verify_nafems.py`.

   <!-- superseded 2026-09-15 -->
   > PARTIAL (2026-09-09) — **four of five cases run; three validate and the fourth honestly
   > does not.** LE1 joined FV52 and LE10 once E7 task 5 built the element family it needs.
   > **LE11 now runs too, and reports `UNCONVERGED`** — which is the finding rather than a
   > failure to reach one. Every grid lands within 2% of the published −105 MPa, and the
   > convergence study still refuses to state a value: point A is a corner where the inner sphere
   > meets the base plane, so the quantity is a point stress in a steep gradient, and over a
   > seven-size sweep the answer scattered between −105.3 and −107.1 MPa with no three
   > consecutive levels monotone. A number picked out of that scatter would be worse than no
   > number. What the case reports is a **capability finding**: a tetrahedral mesh cannot state
   > this corner stress to better than its own noise, and the published solutions that can use
   > curved-hex or p-version elements. A case that runs and refuses is worth more than one that
   > sits blocked — it is measured.
   > **Only LE3 is blocked now**, on a shell solver, which needs `ccx`, which needs the Windows
   > machine; `Blocker` is down to one member because a blocker no case backs fails a test, and
   > both `NO_PLANE_STRESS_ELEMENT` and `GEOMETRY_NOT_BUILT` were deleted the day their case
   > started running.
   > `app/verify/nafems.py` holds five standard cases, and the route this task names is the one
   > used: every target is reproduced from a publicly readable vendor verification manual, cited
   > with the section, the NAFEMS publication it credits, the URL and the date it was read.
   > **FV52 — the simply supported "solid" square plate — runs and validates**: three
   > geometrically similar tet10 grids, Grid Convergence Index 1.05%, observed order 2.05, and
   > 43.624 Hz against the published 44.092 Hz, 1.06% inside a ±5% band fixed before the case was
   > first run. Its three rigid-body modes come back as NAFEMS's own reference row records them,
   > which is what makes mode 4 the fundamental and is asserted separately — a plate accidentally
   > clamped flat also produces a plausible frequency.
   > **LE10 — the thick elliptical plate under pressure — runs and validates too**, and it is the
   > harder of the two because it is a *stress* case on a curved solid: **−5.36376 MPa against the
   > published −5.38 MPa, 0.302%** inside a ±2% band that was fixed while the case was still
   > catalogued as blocked and was not touched afterwards. GCI 2.227%, observed order 2.31, three
   > levels at h = 245/165/115 mm, 44 s.
   > **Getting there cost three defects that all read as "the solver is a bit off"**, and they are
   > the reason this case is worth having. **(a)** Nodal stress was being built by averaging
   > element-centroid values, a ~25% under-read on a plate in bending that *shrinks* with
   > refinement — so it looks like a converging answer rather than an offset. tet10 stress is now
   > evaluated at each node's own natural coordinate (`_TET10_NATURAL_NODES`, derived from
   > `TET10_EDGES` rather than typed out); the centroid remains the superconvergent point and still
   > feeds the headline peak. **(b)** The mid-plane support had no geometry to land on: a one-piece
   > extrusion has no edge at mid-thickness, so the `uz` ring selected 2, 38 and 5 nodes on the
   > three meshes and the answer scattered between −3.2 and −6.1 MPa. The plate is now two
   > half-thickness solids fused, which leaves gmsh a real edge to put a node ring on. **(c)** A
   > coarse mesh of a curved solid is a *smaller part*, not a coarser mesh — at h = 300 the meshed
   > volume was 7% short of the exact 3.269e9 mm³ — so `run_le10` computes the volume from the
   > semi-axes and refuses a level more than 0.5% short, which `run_study` records as a level
   > failure rather than averaging into the trend.
   > Two seam changes were needed and are taken deliberately: `SolveOutput` gained an optional
   > `nodal_stress` tensor (optional so a surrogate still satisfies the `Solver` ABC), and the
   > region vocabulary gained `EllipticalWallSelector`, which selects on **normalised radius**
   > `|√((u/a)²+(v/b)²) − 1| ≤ tol` — a distance tolerance on an ellipse is a different band at
   > every point of the wall.
   > **One case is blocked, and the blocker is the useful output**: LE3 needs a shell
   > solver (E6's named residual — the deck writes one, nothing solves one). It still carries its
   > published target, because knowing the number we must eventually produce is most of the value
   > of a benchmark. `Blocker` is an enum so "two cases wait on one thing" is countable, and a
   > blocker no case backs fails a test — which is how `NO_PLANE_STRESS_ELEMENT` came to be
   > deleted the moment LE1 ran, rather than lingering as a claim about the product that nothing
   > backed.
   > **Two rules earned their keep the day they were written.** A value recalled rather than read
   > was wrong — a second vendor manual quotes 45.897 Hz for the same mode, our own answer
   > converges through 44.10, and the disagreement is recorded on the case rather than resolved by
   > preference. And the register's path scrubber matched the `s:/` inside `https://` and replaced
   > the whole string, so on a page whose entire value is checkable references every citation
   > would have published "[withheld: looked like a filesystem path]"; a drive letter is one
   > character and the lookbehind now says so.
   > **All three results reach the published register**, which is what makes any of this visible
   > outside the test suite — the register had read 0 of 11 since it was written and now reads
   > **2 of 11 analyses** (modal and linear-static) from **3 of 5 cases**; LE1 and LE10 are both
   > linear-static, so a third validated case is not a third validated analysis, and the register
   > counts what it says it counts. It still never runs a benchmark: results arrive as a
   > recorded artefact
   > (`data/verify/validation-outcomes.json`, written by `python -m app.verify.recorded`), because
   > a validation case is several solves and that page is served unauthenticated. **A recording is
   > a claim about the code that produced it**, so `recorded.py` fingerprints every source file
   > that decides an answer — `app/solve/`, `app/mesh/` and the four verification modules — and a
   > mismatch takes the page straight back to "nothing is validated" with the reason published,
   > rather than serving a green tick nobody has re-checked. The publisher's own modules are
   > deliberately outside the fingerprint: an artefact that goes stale when somebody rewords a
   > note is one people regenerate without reading, and then the guard is gone while still
   > appearing to be there. `--check` is instant and belongs in CI; the same comparison is a test,
   > so editing the modal solver without re-recording fails the suite (verified by doing it).
   > **Still open, and each has an owner rather than a shrug**: LE11 needs a per-node temperature
   > field, which is task 6 of this phase, so it is not a silent gap. LE3 needs a shell solver,
   > which is E6's residual and cannot be settled on Linux (no `ccx`), so it is in THE QUEUE as
   > **A6**. The NAFEMS *thermal* family is absent rather than blocked for the same reason as
   > LE11: no analysis here solves **for** a temperature field, `delta_t_k` applies one; task 6
   > owns it.
   > Tested by: `tests/test_verify_nafems.py` (67), `tests/test_verify_recorded.py` (14),
   > `tests/test_verify_register.py`, `tests/test_solver.py::TestTheNodalStressTensor`,
   > `tests/test_solve_selection.py::TestTheEllipticalWall`,
   > `tests/test_verify_convergence.py::TestTheStressComponentQuantity` — twenty-two guards
   > verified by breaking what they guard, and five more on LE1 (E7 task 5).
   > Code: `app/verify/nafems.py`, `app/verify/recorded.py`, `app/solve/linear_static.py`,
   > `app/solve/types.py`, `app/solve/selection.py`, `app/verify/quantities.py`.

2. **Mesh convergence automation**: refine until the answer stops moving, Richardson
   extrapolation, reported **Grid Convergence Index**. After this an unconverged number *cannot*
   be stated — the report machinery refuses.
   > DONE. Tested by: `tests/test_verify_*.py`. Code: `app/verify/`.

3. **Simulation provenance**: every result permanently bound to geometry version, mesh settings,
   material, load case, solver name+version, convergence evidence. `app/media/`'s content
   addressing is the substrate.
   > DONE. Tested by: `tests/test_verify_*.py`.

4. **A published validation register**: which analysis types are validated, against what, to what
   accuracy. In the product, not buried (P10 owns the surface).
   > DONE (2026-09-06). The register's denominator is **declared, not discovered**: eleven
   > analyses are listed, and one nobody has benchmarked gets a row with a reason rather than being
   > absent. It read **0 validated of 11** from the day it was written until 2026-09-08, which
   > was the correct answer for a codebase with no benchmark cases; with task 1's catalogue in
   > place it reads **2 of 11** — modal against NAFEMS FV52 and linear-static against LE10, each
   > with its deviation and its source published beside it — and the other nine are as visible as
   > they ever were. It still runs
   > nothing: the three blocked cases come from the catalogue and the validated two from a recorded
   > artefact whose fingerprint must still match the code. The runnable cases stay out of
   > `PUBLISHED_SUITE` by construction — it filters on `runnable`, so the day somebody makes LE10
   > executable the application still boots; selecting the catalogue wholesale would have turned
   > that day into an import-time refusal on a public route. The four
   > solver analyses carry closed-form *verification* published under its own key with the ASME
   > V&V 20 split stated, because a reader meeting them under a friendlier name would read them as
   > validation. Tested by: `tests/test_verify_*.py`. Code: `app/verify/register.py`.

5. **Plane-stress and plane-strain elements**, so a 2-D benchmark can be posed as the 2-D problem
   it is. This is the whole of what LE1 (the elliptic membrane) waits on, and it is the cheapest
   remaining route to a third validated analysis — the case is fully encoded, cited and carrying
   its target already; nothing but the element family is missing.
   > DONE (2026-09-09) — **the element family exists, is verified against closed form, LE1
   > validates through it, and a plane analysis can now be asked for through the API.**
   > Added earlier the same day because "LE1 is blocked" was recorded
   > on the case with no task anywhere that would ever unblock it, and a blocker nobody owns is a
   > silent gap. Both halves of that blocker fell.
   >
   > **The geometry half was a sourcing problem, and the fix generalises.** Three vendor manuals
   > quote LE1's target and none prints its ellipses — the Abaqus page says the curves are "given
   > above" beside a figure that is not in the text. The semi-axes are in FeenoX's committed
   > `examples/nafems-le1.geo`, and they are the same four the independently-sourced LE10 entry
   > already carried: **LE1 and LE10 are one plan geometry**, a 100 mm membrane and a 600 mm
   > plate, now written once as `ANNULUS_*` so the two cases cannot disagree about a shape they
   > share. When a reproducing manual omits geometry, read a free solver's test inputs.
   >
   > **The element half is four pieces.** `app/mesh/planar.py` — `TriMesh` (tri3/tri6), with
   > nodes carried as (n, 3) at z = 0 so the entire existing region vocabulary works on a plane
   > model unchanged, and a refusal rather than a projection if the mesh drifts off the plane.
   > `app/mesh/gmsh_mesher.generate_tri_mesh` — 2-D meshing, refusing a solid by name rather than
   > meshing its boundary into a closed shell that then fails an unreadable planarity check.
   > `app/solve/plane.py` — `PlaneState`, `PlaneCase`, and `PlanarSolver` as a **sibling** of
   > `Solver` for `ModalSolver`'s reason, with `SolveOutput` deliberately shared so the
   > verification, provenance and viewer paths need no fork. And dimension-awareness in
   > `app/verify/` — `h = (A/N)^(1/2)`, because the cube root on an area reports an observed
   > order inflated by exactly 3/2; measured on LE1's own recorded levels, the wrong root does
   > not merely inflate the order to 7.03, it pushes it past `MAXIMUM_CREDIBLE_ORDER` and
   > **refuses the case outright**.
   >
   > **LE1 validates: 92.40783 MPa against the published 92.7, −0.315%**, inside the ±2% band
   > fixed while the case was still catalogued as blocked. GCI 0.05%, three monotone grids, and
   > **4.1 seconds against LE10's 44** — the same curved boundary and the same class of answer on
   > a tenth of the degrees of freedom, which is the engineering argument for the family stated
   > as a measurement rather than a claim. The trust page reads **3 of 5 cases validated**;
   > analyses stay 2 of 11, because LE1 and LE10 are both linear-static.
   > **One caution is published rather than smoothed away**: the observed order is 4.69 against a
   > formal order of 2, so these grids are not strictly asymptotic and the GCI understates the
   > error — Richardson extrapolates them to ~92.46 where the reference is 92.7, five times the
   > 0.05% GCI. The study is handed the formal order so it says so itself.
   > Closed-form verification of the solver is separate from the benchmark and stronger than it:
   > σ = F/A and δ = FL/AE to 1e-9 on both orders, G recovered from pure shear, both idealisations
   > distinguished by SZZ and by the out-of-plane strain, restrained thermal stress in both, a
   > tri6 quadratic patch test, and Lame's thick-walled cylinder at **−0.360%** converging
   > 1.049% → 0.360% → 0.107%.
   > **The wiring landed 2026-09-09 and this task is now DONE.** It was `PARTIAL` for one day
   > because `PlaneSolver` was reachable from no route, job or registry — capability built and
   > never connected, this project's oldest failure mode, named rather than left to be found.
   > A simulation job now carries `analysis` (`solid` / `plane-stress` / `plane-strain`) and
   > `thickness_mm` (migration `490d3f517ca6`), the runner branches to a triangular mesh and the
   > plane solver, and the API takes both. **A 40 × 200 sheet pulled at 4 kN comes back at
   > 20.0 MPa through the real route**, which is `F/(w·t)` exactly.
   > Four decisions are recorded in the code rather than assumed. `analysis` is NOT NULL with a
   > server default of `solid`, so every row written before the column keeps the meaning it had —
   > and that is pinned by a test that clears the column and reads it back, because the *Python*
   > default is unreachable (the route always sets it) and cannot stand in for the server one.
   > `thickness_mm` is **refused** rather than defaulted on a plane run, since every stress in
   > one scales with it, and refused rather than ignored on a solid, since silently dropping it
   > leaves the engineer believing it was used. The job row records the solver that *actually*
   > ran, not the registry's backend — `SOLVER_BACKEND` chooses between the two solid solvers and
   > neither of them runs a plane model. And a plane run on a solid is refused by the mesher in
   > words, because meshing a solid's boundary succeeds and hands back a closed shell.
   > Tested by: `tests/test_simulations.py::TestAPlaneAnalysisCanBeAskedFor` (9), six guards
   > verified by breaking what they guard.
   > Tested by: `tests/test_solver_plane.py` (63), `tests/test_mesh_planar.py` (43),
   > `tests/test_verify_convergence.py` (+26), `tests/test_verify_nafems.py` (+21) — seventeen
   > guards verified by breaking what they guard, and one recorded as unpinned by a weak break
   > until the break was made to bite.

6. **Solve *for* a temperature field** — steady conduction with convection boundaries, so the
   thermal analyses are analyses rather than an applied `delta_t_k`. LE11 waits on this (plus its
   cylinder/taper/sphere geometry), and so does the entire NAFEMS *thermal* family, which task 1
   names and cannot encode. E10 task 1 records the same gap from the physics side; this task is
   the verification consequence and the two must move together.
   > DONE (2026-09-09) — superseding `IN PROGRESS` earlier the same day, which stood while the
   > solver existed and no request could reach it. **LE11's geometry is fully sourced** — the half
   > of this task that
   > was not physics — and the record is `docs/nafems-le11-geometry.md`, written so the encoding
   > lane needs to redo none of it. The LE1 technique worked again and then went one better:
   > FeenoX does commit `examples/nafems-le11.geo`, but **ESRD's StressCheck benchmarks guide
   > reprints the original NAFEMS dimensioned figure as an image**, which text extraction misses
   > and a 500 dpi render reads. That figure is the primary source and the solver inputs
   > cross-check it; nine of ten dimensions agree exactly across three independent documents.
   > **The tenth disagrees and was settled by arithmetic rather than preference**, which is the
   > part worth keeping: the figure prints the spherical band as 0.700 m and FeenoX builds it as
   > 1.0·cos45° = 0.707107. Taking 0.700 literally puts the junction at radius 0.994983 on a
   > sphere the same figure says has radius 1.0 — five millimetres off a surface it is supposed
   > to lie on — so 0.700 is the rounded annotation. Labelled INFERRED with the arithmetic shown.
   > FeenoX's −105.04 MPa corroborates but is explicitly *not* claimed as proof, because ESRD's
   > own runs from the printed figure land at −105.2 to −105.5 and the target cannot discriminate
   > between the two readings.
   > **One trap is flagged in capitals for whoever encodes it**: LE11's temperature field is
   > defined in **metres** and this codebase is mm-N-MPa with nothing converting, so the formula
   > must become `(sqrt(x²+y²)+z)/1000` in mm. Used unchanged it gives temperatures — and
   > stresses — 1000× too large, with no error anywhere.
   > **The physics landed the same day, and it is two things rather than one.** That distinction
   > was buried in this task and is worth stating: a temperature field can be **prescribed** — a
   > formula of position, which is what LE11 gives — or **solved** from boundary conditions.
   > LE11 was blocked on the first while this task named the second, so it needed far less than
   > the phase claimed.
   > **Prescribed** (`app/solve/thermal.py`, `app/solve/linear_static.py`): `thermal_strain`,
   > `thermal_load` and `thermal_stress_correction` take one number or one value per element —
   > identical arithmetic, since thermal strain is a local quantity and always was — and
   > `LinearStaticSolver.solve` takes an optional `temperatures=`. It is a solver argument and
   > **not** a field on `LoadCase`: a case is JSONB on the job row meant to be read by a person,
   > and one value per node is data the size of the mesh that stops matching it the moment either
   > changes. Verified against closed form twice, because the obvious check cannot do the job — a
   > bar held at both ends under an axial gradient carries *constant* stress at `-Eα` times the
   > **mean**, so replacing the field by its own average passes it, and that break ran green until
   > a transverse gradient was added (measured spread 90% of `EαΔT`; averaged, essentially zero).
   > **Solved** (`app/solve/conduction.py`): steady-state conduction on tet4 and tet10 —
   > Dirichlet, convection (Robin) and heat-flux boundaries over the existing `Selector`
   > vocabulary, plus a uniform volumetric source. Closed forms, not recorded output: the linear
   > bar profile to **6.6e-12 K** on both orders, the logarithmic tube wall at observed order
   > 1.79/1.64 (tet4) and 2.00 (tet10 — **the polygonal wall is the limit, not the element**,
   > which is documented because the obvious reading of those two numbers is that the quadratic
   > element is broken), the convecting-bar Biot tip temperature to 6.8e-12 K, and `q''x/k` to
   > 2.7e-11 K. A floating model — no fixed temperature and no *contributing* film — is refused by
   > name twice, structurally and by heat balance, because a declared film that selected no facets
   > is not an assembled one. **20 of 21 injected defects are caught by a named test and the 21st
   > is labelled unpinned in the source** rather than presented as verified.
   > Conductivity is on the case rather than on `Material`, deliberately: putting it there means
   > every entry in `materials.py` gains a transcribed value with a citation or an honest `None`,
   > which is its own pass. Watts enter the unit system here and convert exactly once, the way
   > density does in the CalculiX deck writer.
   > Tested by: `tests/test_conduction.py` (58), `tests/test_thermal.py` (21).
   > **Wired the same day.** `ConductionSolver` is now a **fourth ABC** beside `Solver`,
   > `ModalSolver` and `PlanarSolver` — `TetMesh` + `ThermalCase` in, `ThermalField` out — and it
   > keeps its **own** output type rather than sharing `SolveOutput`. That is the decision worth
   > recording: `PlanarSolver` shares it because a plane result genuinely *is* those four fields,
   > whereas a temperature field would have to ride in a `displacements` array under a name that
   > lies. The concrete class became `SteadyConductionSolver`, matching
   > `ModalSolver`/`ModalEigenSolver`, so the class name and the recorded `name` agree.
   > The registry gained a **parallel table** rather than an entry in `_FACTORIES`, and the
   > reasoning is the seam again: `_FACTORIES` is typed to `Solver` because that is what the
   > runner holds, so admitting a different ABC would make it return a union and push the branch
   > the four ABCs exist to prevent up into the factory. Asking for `calculix` is refused **by
   > name**, saying `*HEAT TRANSFER` exists in ccx and is not federated behind the seam yet,
   > rather than quietly handing back the in-house solver. `solver_version` is deliberately not
   > duplicated: a version is a fact about the backend, not about the analysis.
   > `observe.span("solve.conduction")` is declared and wired, and it reports
   > `degrees_of_freedom` even though it equals `nodes` here — one temperature per node against
   > three displacements is exactly what makes a conduction duration comparable with a static one.
   > Tested by: `tests/test_conduction.py` (69), `tests/test_solver_registry.py` (30),
   > `tests/test_observe_report.py`; seven guards verified by breaking what they guard.
   > **Wired to a request 2026-09-09, and that closes the task.** `analysis: "thermal-conduction"`
   > on a simulation solves for a temperature field through the real route: a 60 mm bar held at
   > 400 K and 300 K comes back at exactly those, having gone through the queue, the mesher, the
   > registry and the job row. Five decisions are in the code rather than assumed.
   > **A conduction job carries a `thermal_case` and no `load_case`** (migration `b941a651831a`,
   > which also makes `load_case` nullable): it reads no fixture, no force and no modulus, and an
   > empty `LoadCase` on that row would put a material and a set of fixtures nobody chose into the
   > provenance of a temperature field. Supplying one anyway is **refused**, as is a thermal case
   > on a structural run — both would be silently ignored while looking like part of the model.
   > **`CONDUCTION_BACKEND` is its own setting**: `SOLVER_BACKEND` names a solver chosen for a
   > different analysis, and a deployment that had set it to `calculix` for its structural work
   > must not thereby change what answers a temperature field. Asking for `calculix` here is still
   > refused **by name**, saying `*HEAT TRANSFER` exists in ccx and is not federated yet.
   > **A convergence study is refused rather than silently solved once**, because `run_study`
   > assesses the peak von Mises stress and a temperature field does not have one — a study whose
   > quantity was invented for it would be a number nobody asked for. And **the stored field
   > carries `temperatures_k` and `heat_flux_w_m2`, never zeroed displacements**: a reader that
   > finds zeros in `von_mises_nodal` cannot tell them from an answer.
   > The AI result interpreter, which is written entirely around fixtures, loads and a factor of
   > safety, refuses such a run in words instead of producing fluent prose about a load case that
   > does not exist.
   > Tested by: `tests/test_simulations.py::TestAConductionAnalysisCanBeAskedFor` (11) — five
   > guards verified by breaking what they guard.

7. **A verdict may not be stated from a solve that holds no evidence about itself.** The answer
   the agent writes must carry the result's own convergence basis whenever it states a pass, a
   fail, or a margin against a number the user gave — or say plainly that it cannot.
   > DONE (2026-09-11) — **both halves: the caveat that cannot be omitted, and the default mesh
   > that made it necessary.** Driven through the GUI on the seat before and after.
   > **The footnote.** `verification.unconverged_footnote` walks the tool results the turn
   > actually read and appends what the numbers rest on, on both the normal and the truncated
   > exits. It is a *server-appended footnote and not a prompt rule* for a measured reason: the
   > model already had `mesh_convergence` in front of it — `get_simulation` returns the whole
   > result — and wrote the verdict anyway. A caveat the answer cannot omit is the only kind
   > that survives a model having a bad day, which is exactly why `unverified_footnote` is
   > built the same way. It is silent when a study ran and converged: Decision 3 says an
   > unconverged number is worse than no number, not that every number needs a disclaimer.
   > It also names **slivers**, because that is where a bad peak stress comes from and the
   > mesher has counted them since it was written (`sliver_count`, shape < 0.1) with nothing
   > ever showing anyone.
   > **The default mesh, which turned out to be the bigger half.** Re-running the same request
   > three times exposed something worse than a missing caveat:
   >
   > | order | elements | min quality | peak stress | tip deflection |
   > |---|---|---|---|---|
   > | tet4 | 808 | 0.062 | 140.6 MPa (1.56×) | 0.314 mm (**0.27×**) |
   > | tet4 | 809 | 0.490 | 50.4 MPa (0.56×) | 0.321 mm (**0.27×**) |
   > | tet4 | 829 | 0.419 | 68.8 MPa (0.76×) | 0.331 mm (**0.28×**) |
   > | tet10 | 3,686 | 0.416 | 77.8 MPa (0.86×) | 1.145 mm (0.98×) |
   > | tet10 | 10,824 | 0.387 | 73.6 MPa (0.82×) | 1.129 mm (0.96×) |
   >
   > against beam theory's 90.0 MPa and 1.171 mm. Linear tets got the deflection wrong by
   > **3.6× systematically** and scattered the peak stress **2.8× across identical inputs** —
   > 50 to 141 MPa, every one of them reported as a verdict against a stated 150 MPa limit.
   > Slivers are not the explanation; the 50.4 MPa run had none. Linear tetrahedra are simply
   > too stiff in bending.
   > **What settles it is that the product already knew.** Every NAFEMS case in
   > `app/verify/nafems.py` passes `element_order=2` explicitly — so Kryova validated itself
   > with quadratic elements and served customers linear ones. A verification product cannot
   > ship a default whose answers it would not accept from itself. Defaulted to 2 in **both**
   > entry points, because the route and the agent tool default independently and the tool is
   > the one the agent calls. The cost (~2.5× DOFs and solve time) is stated rather than
   > hidden, and `element_order=1` is still there for a quick shape check.
   > **Not fixed, and named rather than left implicit:** the element *size* is still automatic
   > and takes no account of the part's thinnest section, so a slender part still gets very few
   > elements through it. tet10 makes that survivable rather than correct. That is the residual.
   > Tested by: `tests/test_unconverged_verdict.py` (16), `tests/test_simulations.py`
   > (`TestElementOrder`, inverted with the reason on it). Guard verified by breaking it — the
   > inverted convergence check fails nine tests including the converged-run case in the
   > opposite direction. Run: `docs/verification-2026-09-10-night/`.

   <!-- superseded 2026-09-11 -->
   > NOT STARTED — added 2026-09-11, measured through the GUI, `docs/verification-2026-09-10-night/`.
   > **What happened.** Asked for a cantilever that "has to stay under 150 MPa", the product
   > built it, solved it, and replied: *"PASSES. The peak stress of 140.6 MPa is below your limit
   > of 150 MPa. The bracket has approximately 9 MPa of margin."* The result it had just read
   > carried `mesh_convergence: {converged: false, basis: "single-grid"}` and the sentence
   > "Solved on one mesh. Nothing here measures how much the answer would move on a finer one, so
   > treat the numbers as indicative." Neither reached the answer.
   > **It was not a close call.** The mesh was 808 linear tet4 elements — one element through a
   > 10 mm thickness — and tet4 is pathologically stiff in bending: tip deflection came out
   > 0.31 mm against beam theory's 1.17 mm. Challenged in the same conversation the agent got it
   > entirely right, running tet10 at 5 mm and 3.5 mm for 1.14 and 1.13 mm, and saying plainly
   > that the first answer was not converged. So **the evidence exists, the agent can produce it,
   > and nothing made it look before issuing a verdict.**
   > **The direction was luck.** The coarse mesh *overstated* peak stress by ~90% (140.6 against
   > a converged ~74), so a part that passes comfortably was reported as passing with a thin
   > margin. The same error the other way fails a good part, or passes a bad one.
   > **This is not a UI gap.** `app/verify/` is honest, the job row is honest, and the frontend
   > already refuses to paint an unconverged number green (`unmeasured is amber and never
   > green`). Only the sentence the engineer actually reads is wrong, which makes it this
   > phase's problem rather than P5's: Decision 3 says an unmeasured claim is never a pass, and
   > this is that claim being made in prose.
   > **Two things to decide when it is built**, neither obvious: whether a single-grid run may
   > state a verdict *at all* or only a caveated observation; and whether the agent should
   > **run the study itself** when a stated limit is in play, rather than reporting and waiting
   > to be challenged. The second is the difference between honest and useful.
   > Related but separate, and worth its own task: **the default mesh for a slender part.**
   > `generate_tet_mesh` defaults to `element_order=1` with no size control, which is what
   > produced 808 elements here. The same class was already measured at gate G1 — "a factor of
   > safety of 1303 off one 411-element tet4 mesh". Whether the default becomes tet10, or a size
   > derived from the part's smallest dimension, is a real decision about cost against accuracy.

**Where this phase stands, and why the marker above says what it says.** Tasks 2 through 6 are
done. Task 1 is `PARTIAL` and **cannot be closed on this machine**: LE3 is a shell benchmark and
running one needs `ccx`, of which there is none on Linux. It is `A6` in
`docs/WINDOWS_VERIFICATION.md`. **But most of what stood between LE3 and a run turned out to be
Linux work rather than the wait** — a shell mesher and a shell load path, both built 2026-09-09
— and what is left before the seat is useful here is the `Solver`/`ShellMesh` seam, a shell
`.frd` reader, and LE3's unsourced geometry. LE11 is
encoded, cited, runs, and its recorded outcome is `unconverged` — the target quantity is a point
stress at a corner in a steep gradient and it scatters with where nodes land, so the study
correctly refuses to state a value from a non-monotone triple. That is a measurement, published
as one, and the honest state of the case rather than a gap.

**This is the phase the Linux stretch stops on**, and it is the right place to stop: everything
here that does not need hardware is closed, and what remains is exactly what the Windows seat
exists to settle.

**Creative leverage:** the provenance ledger is what makes output *signable*. An engineer signing
accepts liability; what they need is a complete, tamper-evident chain from requirement to number.

##### Phase E8 — Fatigue and durability *(needs an ME)* #####

**~5 engineer-months.**

**Structures fail from fatigue, not from a single static load.** If exactly one physics capability
is added, it is this one. **[pyLife](https://github.com/boschresearch/pylife)** (Bosch Research,
Apache-2.0) covers rainflow counting, load collectives, S-N handling, damage summation, equivalent
stress. **[FFPACK](https://pypi.org/project/ffpack)** and
**[fatpack](https://github.com/Gunnstein/fatpack)** supply Goodman/Soderberg corrections and
trilinear curves. **The library is 20% of this phase. The methodology is 80%, and it needs a real
analyst.**

1. **pyLife against the federation's stress output.**
   > DONE (2026-09-14) — **a fatigue history is read off the federation's own stress output.**
   > `app/fatigue/field.py` takes `SolveOutput.nodal_stress` (the (n, 6) tensor in Voigt order
   > SXX SYY SZZ SXY SYZ SZX, from the in-house solver and CalculiX alike) as one `LoadChannel`
   > per independently solved load, each with its dimensionless signal, superposes
   > σ(t) = Σ s_k(t)·σ_k, and reads a **signed** scalar at a node — the history `history.py` has
   > refused to take from von Mises since 2026-09-06. Three, each with its failure stated: the
   > normal stress on a declared plane (linear, so exact under any number of channels); the
   > principal stress of largest magnitude (the history reports how far the axis rotated, since a
   > rotating axis is not the stress on one plane); von Mises signed by the hydrostatic stress (the
   > history counts the sign flips, which manufacture a full-range cycle under torsion).
   > `ranked_nodes` chooses where to assess by range, 4,096 nodes at a time; `nearest_node`
   > reports its distance rather than hiding it. Refused by name: a solve with no tensor, a
   > hot-spot basis read from a nodal value, a component with no plane, channels whose node
   > counts differ (two meshes with one node count are not told apart; the caller owns that).
   > Verified on a real solve: a bar meshed tet4 and tet10 through `LinearStaticSolver`
   > reads F/A = 25 MPa at every node, a reversed signal reads ±25 MPa in both signed scalars, and
   > the Basquin damage pyLife counts equals the hand sum. 20 guards verified by breaking each and
   > watching a named test fail, restores checked by sha256.
   > **Not claimed**: a nonlinear solve (none exists behind the seam, and `LoadChannel.solver` is
   > carried for the refusal it will need); a critical-plane method for non-proportional loading;
   > **any product path** — the simulation archive stores von Mises and displacements and not the
   > tensor (`runner.py`), so nothing a user runs can be read into a history yet. That is task 6.
   > Tested by: `tests/test_fatigue_field.py`, `tests/test_fatigue_verification.py`.

   <!-- superseded 2026-09-14 -->
   > PARTIAL (2026-09-06) — **pyLife installs and imports on Python 3.14**, which was the open
   > question. Rainflow and damage federated to it per Decision 2, with the load-history/factor/
   > provenance vocabulary ours. Verified against closed form (exact cycle count, Miner
   > arithmetic). Tested by: `tests/` under `app/fatigue/`. Code: `app/fatigue/`.

2. **Mean-stress correction, surface finish, size and reliability factors.**
   > DONE (2026-09-14) — **every factor and correction the assessment applies is refused, stated
   > or sourced, and each refusal is pinned by a test that fails when it is removed.** The code
   > shipped on 2026-09-06 with its closed-form arithmetic tested and its refusals untested; the
   > earlier status named "`tests/` under `app/fatigue/`", which is no file. `tests/test_fatigue.py`
   > now holds them: an unsigned history is not counted; a flat history is `UNMEASURED`, never a
   > zero damage; the notch is applied exactly once (a Kt on a notch-root or hot-spot stress is
   > refused, a nominal stress with none is unmeasured); the surface and size factors cannot be
   > left out, and one factor given twice is refused rather than squared; a survival probability
   > needs the curve's scatter; a tensile mean needs FKM's M, or a written justification for
   > declaring it irrelevant; a missing pyLife is a finding naming it, not a crash; Kf taken as Kt,
   > Miner original and an unclosed residue are each stated.
   > **A defect fixed:** `WeldDetail.sn_curve` defaulted to a failure probability of 0.025, under
   > a test comment saying "95% survival". EN 1993-1-9:2005 §7.1 (NOTE 1 after Figure 7.2) says
   > Δσc was calculated "for a 75% confidence level of 95% probability of survival for log N" —
   > 0.05, now `EC3_FAILURE_PROBABILITY`, with the confidence level carried in the curve's source.
   > 22 guards verified by breaking each (19 in `assessment.py`, 1 in `factors.py`, 2 in
   > `material.py`). **Not claimed**: that any factor value suits any part. None is defaulted;
   > choosing them is the engineer's, which is why the phase is marked *needs an ME*.
   > Tested by: `tests/test_fatigue.py`, `tests/test_fatigue_verification.py`.

   <!-- superseded 2026-09-14 -->
   > PARTIAL (2026-09-06) — every factor a qualified engineer must choose is an explicit input
   > with a source field rather than a buried default.

3. **Weld classification** — BS 7608 / Eurocode 3 detail categories; where a welded frame lives or
   dies; judgement, not arithmetic. EN 1993-1-9:2005 was read on 2026-09-14, and the page map and
   notes are in `docs/eurocode3-fatigue-reading.md`: Tables 8.3–8.5, the shear curve, γMf, §8's
   range limit and interaction rule. All of that was encoded on 2026-09-15 (status below).
   **BS 7608 has not been read**, and nothing may claim it until someone does.
   > PARTIAL (2026-09-15) — **EN 1993-1-9's weld tables are in code, and a joint is classified
   > into the rows it can still be, never into one.** `app/fatigue/weld_catalogue.py` holds 91 rows
   > from Tables 8.3, 8.4, 8.5 and B.1. Each has its category, its details, its page, and its
   > description and requirements quoted. Every row was re-read on a page image before encoding;
   > the text layer is broken OCR, and one label (160) rendered as "180" until read at 400 dpi.
   > `classify(joint, facts, basis=…)` returns every row no known fact contradicts, with the
   > lowest named conservative per stress kind. It also lists the facts that would narrow the set,
   > and what each candidate's facts left unconfirmed. **The reading is literal**: a row is excluded
   > only by a condition that row states, so a one-sided weld checked by NDT keeps Table 8.3
   > detail 13's 36 row beside its 71. Extra candidates can only lower the conservative category.
   > Found on the page and encoded as found: Table 8.5 detail 1 covers ℓ < 50 and 50 < ℓ ≤ 80 and
   > nothing at ℓ = 50, so both neighbours come back flagged; Table 8.4 detail 4's 90 and 71 rows
   > overlap; a two-sided plate splice without NDT is in no row of Table 8.3; transverse
   > attachments stop at ℓ = 80. The basis is enforced: Tables 8.x for nominal stress (§7.1(4)),
   > B.1 for hot spot (§7.1(5)). `Candidate.detail` builds the curve input, needs the name of
   > whoever chose the row, and applies k_s where the row carries it (detail 17's eccentric form
   > included). `app/fatigue/eurocode3.py` holds the rest: the Figure 7.1 and 7.2 category sets;
   > γMf from Table 3.1 as a recommendation naming the National Annex; §7.2.1's 60% compressive
   > portion (as-welded refused, and the mean spent so no correction counts it twice); §8(1)'s
   > range limit; and §8(2)–(3) with exponents 3 and 5. `material.ShearDetail` is the m = 5 shear
   > curve, cut-off 0.457·Δτc. 49 guards verified by breaking each and watching a named test
   > fail, restores checked by sha256.
   > **Not claimed**: BS 7608 (not read); Tables 8.1, 8.2 and 8.6–8.10 (not read or not
   > encoded); Δσ_E,2 and Δτ_E,2 are inputs, because Annex A and the λ factors have not been
   > read; Table 8.5 detail 10 is referred to EN 1994-2 and refused; §7.1 NOTE 3's alternative for
   > asterisk details is not offered; applying NOTE 1's 95% survival to Δτc is a stated reading.
   > **Choosing the row is still the engineer's**, which is why the task stays *needs an ME*.
   > Tested by: `tests/test_fatigue_eurocode3.py`.

   <!-- superseded 2026-09-15 -->
   > NOT STARTED — needs an ME.

4. **Duty-cycle definition and damage over a real usage spectrum.**
   > DONE (2026-09-14) — **a duty cycle of operating modes is counted over a whole life without
   > expanding it, and the transition cycle between modes is in the count.** `app/fatigue/duty.py`:
   > a `Mode` is a signed block history repeated a sourced number of times per pass
   > (`Mode.from_hours` converts hours of use at a block length); a `DutyCycle` is the modes in
   > order, repeated `passes` times over the life. `count` returns one `Collective` for
   > `Assessment`, keeping the per-mode counts and the transitions apart for the reviewer. It rests
   > on two identities of the three-point count, count(Aʳ) = r·closed(A) + (r−1)·closed(R⧺R) and
   > the same over the sequence of the modes' residues, and both rest on residue(R⧺R) = R, which
   > is **checked on every count** and refused (`DutyError`) where it fails. Verified against
   > brute-force counting of the expanded history, cycle for cycle and in the residue: 60 random
   > duty cycles in the suite, 4,000 before the module was written, zero mismatches. A million
   > passes count in under 5 s and equal the exact linear growth of the expanded counts at
   > N = 2 and 3. ±100 MPa about 0 then ±50 MPa about 300 carries a 450 MPa transition range that
   > a per-mode sum caps at 200. 13 guards verified by breaking each.
   > **Not claimed**: a usage spectrum for any real machine — every repetition count is an input
   > with a source and none has been measured; sequence effects on damage (Miner is sequence-blind,
   > and the assessment says so); modes that interleave at random rather than in a fixed order.
   > Tested by: `tests/test_fatigue_duty.py`.

   <!-- superseded 2026-09-14 -->
   > NOT STARTED.

5. **Notch handling, hot-spot stress extrapolation.** Annex B's hot-spot categories are in
   `docs/eurocode3-fatigue-reading.md`. The extrapolation's read-out distances are not in the pages
   read there and must be sourced before a method claims any.
   > DONE (2026-09-15) — **the read-out distances are sourced, and both halves are in code.**
   > `app/fatigue/hotspot.py` holds IIW-1823-07's five surface-extrapolation rules, equations
   > (2.7)–(2.11) on pp. 24–25, read on page images because the equations have no text layer
   > (`docs/iiw-hot-spot-and-neuber-reading.md`). **The weights applied are the exact Lagrange
   > weights** through each rule's points, held to IIW's printed coefficients at the page's own
   > precision: the printed 1.67/−0.67 would miss a linear field by 0.002 of its slope per
   > thickness. `extrapolate` works instant by instant and refuses unsigned, nominal and
   > already-hot-spot readings, readings off their reference point beyond round-off, and readings
   > sampled at different instants. Its result is a signed `HOT_SPOT` history whose source names
   > what is not applied: IIW's thickness correction (on IIW's resistance side, not
   > half-applied to EN 1993-1-9 categories) and misalignment. `app/fatigue/notch.py` gives q
   > from Neuber's technical factor (NACA TN 2805 formula (1), with its stated ±10%/69% accuracy
   > carried in every source, and **no value of Neuber's constant held**, because Figure 3 is a
   > curve an engineer reads). It also gives the elastic-plastic notch stress by the extended
   > Neuber rule through pyLife, behind a new `FatigueBackend.extended_neuber`, **with the root
   > checked against ε·σ = L·K_p·e***: pyLife's Newton solve has a fixed iteration count. K_p has
   > no default. Interface change, stated: `FatigueBackend` gained an abstract method, so any
   > other backend must implement it. **Not run here**: the tests were written on Linux on
   > 2026-09-15 and not executed, at the user's instruction. **Not claimed**: FKM nonlinear
   > itself (not read), Peterson's q (no readable source), variable-amplitude notch memory,
   > strain-life damage.
   > Tested by: `tests/test_fatigue_notch.py`.

   <!-- superseded 2026-09-15 -->
   > NOT STARTED.

6. **Fatigue reaches the product.** Added 2026-09-14, when task 1 closed as a library. The
   simulation archive keeps von Mises and displacements and drops the tensor, so no run a user
   makes can be read into a history. Archive `nodal_stress`, serve a node's history and its
   assessment through a route, offer the agent a tool, and test the path through the agent
   (CLAUDE.md *Testing* item 8), not only through the function.
   > DONE (2026-09-15) — `app/simulation/runner.py` archives `nodal_stress_mpa`, the (n, 6)
   > Voigt tensor, beside von Mises. `app/simulation/fatigue.assess_run` is the one function the
   > route `POST /projects/{id}/simulations/{sim}/fatigue` and the agent tool `assess_fatigue`
   > both call: one solved load scaled by a signal, read at a node (or the nearest node to a
   > point, with the distance reported), assessed with the stated curve and factors. Refusals by
   > name: a run not a finished `solid`/`plane-stress`/`plane-strain` solve (409), an archive
   > with no tensor (409, "re-run"), a node off the mesh or a bad direction (422). The answer
   > carries the run's `result` block, so the loop's not-validated footnote fires on it.
   > Interface changes, stated: a new route, a new agent tool, and a new archive key; **runs
   > archived before this change are refused**, never assessed from von Mises. **Not run here**:
   > the tests were written on Linux on 2026-09-15 and not executed, at the user's instruction.
   > **Not claimed**: two independently varying loads (the library's multi-channel path is not
   > exposed), hot-spot or notch-strain reads through the route.
   > Tested by: `tests/test_simulation_fatigue.py`.

   <!-- superseded 2026-09-15 -->
   > NOT STARTED.

##### Phase E9 — Multibody dynamics: where load cases actually come from #####

**~6 engineer-months, needs an ME.**

Today load cases are hand-entered guesses. In reality they are *outputs* of the machine moving.

1. **Project Chrono** (BSD-3, UW-Madison) — multibody + FEA + FSI, Python API, template-based
   `Chrono::Vehicle` with ready suspension templates. Rejected: MBDyn (GPL; stronger on
   rotor/aeroelastic — kept in reserve for exactly that).
   > PARTIAL (2026-09-16) — **Chrono now runs, across a container boundary.** `app/dynamics/chrono/`
   > copies `app/solve/openfoam/`'s shape: `run.py` is the process boundary, `payload.py` the JSON
   > wire, `_entrypoint.py` the translation that executes *inside* the container (importing
   > nothing from `app`, shipped per run rather than baked into the image so a result's provenance
   > is this commit), `engine.py` the `DynamicsEngine`. The boundary is a **packaging** boundary,
   > not a licence one — Chrono is BSD-3; what forces it is that PyChrono ships through conda only,
   > and Decision 1 keeps conda out of this deployment. There is no published image, so
   > `scripts/chrono_image.sh` builds `kryova-chrono:9.0.1` from `mambaorg/micromamba:1.5.10`
   > once, never during a run. The engine is **named before it runs**, by the image's content id
   > and not its tag.
   > **The image was built here and the oracle runs were done here** — the five in THE QUEUE G6(b)
   > — and they agree with closed form: a pendulum released from horizontal gives a peak pivot
   > reaction of **29.4156 N against 3mg = 29.4200 N (0.015%)**; a 2 kg mass spun at 10 rad/s on a
   > 100 mm crank gives **20.00083 N against m ω² r = 20.0 N (0.004%)** and its force *vector*
   > tracks `KinematicEngine`'s to about 1%; a prismatic driven at 50 mm/s travels exactly 50 mm
   > in a second, so the millimetre-to-metre scale is right rather than plausible.
   > **Three defects were found by those runs and none was visible to the tests**, which is the
   > entry worth reading before trusting any of this: Chrono's **default iterative solver does not
   > satisfy a revolute constraint** here (the same pendulum reported **4286 N** and its rod
   > stretched from 0.5 m to 0.74 m) so a direct solver is now mandatory and its absence is a loud
   > warning; **`GetReaction2` was the wrong reaction** (it is the load on the parent — equal,
   > opposite, and the same magnitude, so every joint load would have been published
   > sign-reversed at exactly the right size); and reaction 1 is expressed in **frame 1**, which
   > the code rotated by frame 2, giving a constant force vector where the exact evaluator had one
   > sweeping round a circle — again at a magnitude agreeing to 0.004%.
   > **What is still not claimed:** joint **moments** against any closed form, and every case this
   > engine actually exists for — closed loops, contact, friction, springs, end stops. So
   > `engine.UNVERIFIED_NOTE` now states precisely what was checked and what was not, and
   > **`engines()` still puts this engine behind `KinematicEngine`** — not because Chrono is
   > unverified but because where both can answer the kinematic one is *exact* and this one
   > integrates. That ordering is permanent, not provisional. It is reached by name.
   > Also not claimed: spherical joints (refused by name), inertia tensors from the mass roll-up
   > (a body with none is sent as a point mass and the container substitutes a negligible isotropic
   > tensor, saying so in the warnings, because a *zero* tensor is a singular mass matrix rather
   > than a point mass), and a harmonic driver with a non-zero offset (refused, because Chrono's
   > sine carries no offset term and dropping it would shift the whole motion silently).
   > `app.dynamics.engine.ChronoEngine` — the in-process route — stays shut and unchanged: its
   > refusal is about `pip` and is still true.
   > **Not run here**: the tests were written on Linux on 2026-09-16 and not executed as pytest,
   > at the user's instruction; every assertion in them was measured first by one-off scripts
   > against the real modules, and the physics was measured against the real engine as above.
   > Tested by: `tests/test_dynamics_chrono.py`.

   <!-- superseded 2026-09-16 -->
   > BLOCKED — **`pip install pychrono` installs an unrelated package and succeeds.** The engine
   > probe checks the module really is Chrono. So there is no dynamics engine and the docstrings
   > say so.

2. **Mechanism definition derived from assembly constraints** — the kinematic model comes from the
   CAD, never modelled twice.
   > DONE (2026-09-15) — `app/dynamics/assembly.derive` builds a `Mechanism` from a
   > `ProductStructure`: each body's mass and centre from `app.assembly.mass.roll_up` over every
   > leaf under its occurrence path, each joint's origin and axis from the occurrence frames,
   > the joint itself declared once in the child part's own coordinates. Nothing is typed
   > twice. Refused by name: an unweighed part under a body (a partial mass gives a reaction
   > that is too small), a body inside a body, a parent that is not a body, a body on two
   > joints. Held to `m ω² r` for a rotor however it is placed and turned, and for a two-link
   > chain whose elbow distance comes from the graph. **Not claimed**: reading mate
   > constraints as joints (the product graph holds none; CATIA's are THE QUEUE E6), and
   > inertia tensors (the roll-up has none, so bodies are point masses and the notes say so).
   > **Not run here**: written on Linux on 2026-09-15 and not executed, at the user's
   > instruction. Tested by: `tests/test_dynamics_assembly.py`, `tests/test_dynamics_kinematics.py`.

   <!-- superseded 2026-09-15 -->
   > PARTIAL (2026-09-06) — kinematics tested against closed form: slider-crank at both dead
   > centres, the Grashof families each cross-checked against the solver. Tested by:
   > `tests/test_dynamics_*.py` (115 tests). Code: `app/dynamics/`.

3. **Motion-range simulation**: swept volume, interference through motion, travel and lock checks.
   > DONE (2026-09-15) — `app/dynamics/travel.py`. **Travel is exact**: a driver's extremes are
   > solved in closed form (a harmonic's crests from its phase, a table's at its knots), so a
   > stop passed between two samples is still found; limits carry a source. **Lock**:
   > `lock_sweep` walks a four-bar, reports the first angle it cannot assemble or drive at, and
   > the smallest transmission angle, held to the law-of-cosines value at a crank-rocker's
   > in-line positions; the criterion is the caller's with a source. **Swept volume** is the
   > union of the sampled poses fused in OCCT, a lower bound by construction, plus a sampled
   > envelope box. Interference through motion is `clearance.py` (2026-09-06). **Not run
   > here**: written on Linux on 2026-09-15 and not executed, at the user's instruction.
   > Tested by: `tests/test_dynamics_travel.py`, `tests/test_dynamics_clearance.py`.

   <!-- superseded 2026-09-15 -->
   > PARTIAL (2026-09-06) — clearance tested. Tested by: `tests/test_dynamics_*.py`.

4. **Joint-load extraction feeding FEA** — manoeuvre → MBD → reactions at every joint → FEA load
   case → stress → fatigue damage. The loop that closes the system; nothing else in this plan
   produces a defensible load case.
   > DONE (2026-09-15) — the loop closes: `app/dynamics/channels.plan` splits a mechanism's
   > cycle into load channels, each a `LoadCase` solved once and a signal of multiples, and
   > `CyclePlan.load_channels` hands them to `app.fatigue.field` for `history_at` and
   > `Assessment`. The split is exact by load kind: a plain force mount by world axis, a
   > bearing mount along its line as two one-sided channels (a turning bearing reaction is
   > refused, because its pressure distribution depends on direction), the body's `g − a`
   > field by axis. Held to one identity at every instant: the scaled channels reassemble to
   > the reaction, and through a real linear solve of a clamped bar the summed nodal stress
   > equals a direct solve of `loadcases.build` at three instants. **Not claimed**: a history
   > between samples (a lower bound on the range), joint moments, the `ω² r` field across a
   > spinning part. **Not run here**: written on Linux on 2026-09-15 and not executed, at the
   > user's instruction. Tested by: `tests/test_dynamics_channels.py`,
   > `tests/test_dynamics_kinematics.py`.

   <!-- superseded 2026-09-15 -->
   > PARTIAL (2026-09-06) — reactions tested against closed form: `ω²r`, and the pendulum period
   > pinned through the reaction (driven at the natural frequency the rod carries no tangential
   > force; the residual is `mg·A³/6` to 4 figures). Tested by: `tests/test_dynamics_*.py`.

5. **CATIA DMU Kinematics as an alternative backend where a seat exists.**
   > BLOCKED — needs a CATIA seat with the Kinematics workbench; THE QUEUE E6 (added
   > 2026-09-15) carries it with the test to hold it to.

   <!-- superseded 2026-09-15 -->
   > NOT STARTED.

6. **Inertia tensors in the mass roll-up, so a joint MOMENT has something to be computed
   from.** *(Task added 2026-09-16. It was not in the plan because task 2 recorded the gap in
   its own status — "the roll-up has none, so bodies are point masses and the notes say so" —
   and a gap recorded only inside a `DONE` is a gap nobody is going to close. It became
   load-bearing when E9.1 shipped: THE QUEUE G6 asks for a joint moment against a closed form,
   and moments are the one quantity neither dynamics engine has ever had verified.)*
   > DONE (2026-09-16) — `app/assembly/inertia.py` rolls the tensor up the way `mass.py` rolls
   > up the mass: each component measured once at `Detail.INERTIA`, placed by its occurrence's
   > frame, and assembled about any point by the parallel-axis theorem.
   > `dynamics.assembly.derive` takes a second measurer, `measure_inertia`, and gives each body
   > the diagonal about its own centre of mass in world axes at the assembled pose — a separate
   > argument from `measure` because that one answers at `Detail.FULL`, and a mass budget must
   > not silently start paying for a fourth integration per component.
   > **Four things measured against the real kernel before any test asserted them**, each of
   > which gives a plausible wrong moment rather than an error: OCCT's `MatrixOfInertia` is
   > about the **centre of mass** (a 60x40x20 box reports `V(b²+c²)/12`, not the corner value
   > four times larger); its off-diagonals are the **negated** products of inertia (`-∫xy dV` —
   > an L-plate whose analytic integral is `-2.0833e7` reports `+2.0833e7`); the conversion from
   > mm⁵ to kg.mm² is the component's own `mass_kg / volume_mm3`, so a mixed-material assembly
   > stays right and no `1e-9` constant sits in the code unchecked; and two boxes assembled by
   > parallel axis reproduce the tensor OCCT measures on the shape they fuse into, to 1e-15 of
   > its magnitude, while flipping the product sign moves `xy` from `+2.08e7` to `-2.08e7`.
   > **Refused rather than approximated**: `Body.inertia_kg_mm2` carries only the diagonal, and
   > an ordinary L-shaped link couples 18% of its largest moment into the other two axes, so
   > `body_diagonal` refuses above `MAX_PRODUCT_FRACTION` (1%) and `derive` records it as a note
   > — that body stays a point mass, the other bodies keep their tensors, and the note names the
   > two ways out. A caller's own tensor still wins over a measured one.
   > **Not claimed**: a full tensor on `Body` (the follow-up, and what would let a coupled link
   > carry its real inertia), and the moment itself against a closed form, which is THE QUEUE
   > G6 and needs the Chrono container on Windows.
   > **The tests were written on Linux and not run as pytest**, at the user's instruction; each
   > assertion was evaluated once by a one-off script and the closed forms were checked against
   > the real OCCT kernel. Tested by: `tests/test_assembly_inertia.py` (29),
   > `tests/test_dynamics_assembly.py::TestInertiaCanComeFromTheGeometryToo` (7).

##### Phase E10 — Thermal, flow, and optimisation #####

> ✅ PHASE COMPLETE (2026-09-14) — all tasks done and tested.

**~9 engineer-months.**

1. **Steady/transient conduction, convection BCs, thermal-stress coupling.**
   > DONE (2026-09-14) — **transient conduction reaches the product, and a structural run can
   > carry a thermal run's temperatures.** The earlier status below shipped the solver and said
   > in words that it was wired to nothing; this closes that.
   > **Asked for** by `analysis: "thermal-transient"` with a `transient_case` (migration
   > `5f1bc5c58c49`, which also adds `temperature_source`). A **sibling column, not a reuse of
   > `thermal_case`**: a steady `ThermalCase` validates a transient payload and drops the time
   > axis without a word. `TRANSIENT_CONDUCTION_BACKEND` is its own setting for the reason
   > `CONDUCTION_BACKEND` is. A study (`grids > 1`) is refused naming the time step as the second
   > discretisation one run cannot assess.
   > **The whole history is stored or the run is refused** — `(steps+1) × nodes` against
   > `MAX_TRANSIENT_VALUES`, with a `time_step_s` and `element_size_mm` that would fit named in
   > the message. Thinning the history to fit is sampling where the archive says measured, one of
   > the three forbidden speedups. The archive holds `times_s`, `temperature_history_k` and
   > `temperatures_k` = the final row, so every steady reader keeps working.
   > **Read** through `GET /simulations/{id}/temperature?step=N` (`SurfaceTemperature`); a step on
   > a steady run is refused ("no time axis"), a step past the end names the range.
   > **A live defect fixed on the way: both surface-field routes returned 500** on every
   > conduction run (`KeyError: 'displacements'` — the archive holds no displacements). They now
   > answer 409 pointing at the temperature route.
   > **Coupling** is `temperature_from: {simulation_id, step?, reference_temperature_k}` on a
   > solid structural run (`app/simulation/coupling.py`). **The field is used only on the mesh it
   > was solved on** — same geometry version, element size and order, checked at the route, and
   > the node array compared exactly at run time — because there is no interpolation, and a
   > nearest-node transfer between two gmsh meshes is a plausible wrong field. The source is
   > pinned by its archive's sha256, the cache key covers it (`KEY_VERSION` 2), and the reference
   > temperature is required with no default: `σ = −Eα(T − T_ref)` for a T_ref nobody stated is
   > a stress nobody chose. A solver that cannot read a field (CalculiX today) refuses by name via
   > `Solver.accepts_temperature_field`, rather than solving at room temperature. Refused with a
   > field: a convergence study, and `delta_t_k` beside one (the expansion counted twice).
   > **Agent**: `run_thermal_simulation` (steady or transient — its own tool rather than a mode
   > on `run_simulation`, whose one `LoadCase` is already the hardest argument the local model
   > writes) and `temperature_from` on `run_simulation`; both refuse with a valid example case.
   > Verified against closed forms: an insulated bar driven to 400 K, the restrained bar
   > `σ = −EαΔT` from a *computed* field equal to the `delta_t_k` path to 1e-9, a zero stress at
   > `T_ref = T`, and a transient's final step within 1e-4 of the steady answer. 23 guards
   > verified by breaking each and watching a named test fail, restores checked by sha256.
   > **Not claimed**: a field transferred between two meshes; CalculiX thermal-stress or
   > transient decks (E3 in THE QUEUE owns the conduction oracle against ccx); any GUI surface —
   > the frontend has the types and no panel. Written on Linux; no seat claim.
   > Tested by: `tests/test_simulations.py` (`TestATransientConductionRunCanBeAskedFor`,
   > `TestTheTemperatureAndSurfaceRoutesServeTheRightRuns`,
   > `TestAStructuralRunCanCarryAThermalRunsTemperatures`), `tests/test_simulation_cache.py`,
   > `tests/test_agent.py` (`TestRunThermalSimulation`, `TestRunSimulationCanCarryATemperatureField`),
   > `tests/test_conduction.py`.

   <!-- superseded 2026-09-14 -->
   > PARTIAL (2026-09-14) — **transient conduction is no longer missing.** `app/solve/
   > conduction.py` adds `TransientThermalCase`/`TransientThermalField` and
   > `BackwardEulerConductionSolver`, solving `rho cp dT/dt = div(k grad T) + q_v` with backward
   > Euler — chosen over Crank-Nicolson for unconditional stability and no spurious oscillation,
   > not for cost — over the same `ThermalBoundary` vocabulary (Dirichlet, convection, flux) the
   > steady solver already uses, so `SteadyConductionSolver`'s flux/convection assembly is reused
   > rather than duplicated. The consistent capacitance matrix reuses `modal._unit_mass_matrix`'s
   > analytic barycentric integration instead of re-deriving it — that integration is exact for
   > tet10 where a four-point Gauss rule is not, and this is the second matrix it feeds. The
   > system `(C/dt + K)` factorises once per solve and is reused every step (direct `splu` or
   > iterative Jacobi-CG past `_ITERATIVE_THRESHOLD_NODES`, matching the steady solver's
   > crossover).
   > Verified against the lumped-capacitance cooling curve `T(t) = T_inf + (T0-T_inf)e^{-t/tau}`,
   > `tau = rho cp V / (h A)`, forced into the low-Biot regime with an artificially high
   > conductivity rather than a claimed material property — first-order backward-Euler
   > truncation error measured directly (halving the time step roughly halved the deviation from
   > the closed form), and the test tolerance is set with margin over the measured error rather
   > than picked to make a run pass.
   > `TransientThermalField` carries **no `mesh_convergence`** — two independent discretisations
   > (mesh, time step) and one run assesses neither, so claiming convergence here would be exactly
   > the unmeasured claim Decision 3 forbids. There is deliberately no `_thermally_floating`-style
   > singularity refusal either: `C/dt + K` is SPD whenever `C` is, so an insulated part with a
   > source correctly produces unbounded rise rather than a false refusal.
   > Registered as its own ABC (`TransientConductionSolver` in `app/solve/base.py`, the fifth
   > sibling — not a mode switch on `ConductionSolver`, for the same reason the other four are
   > siblings rather than branches) and its own registry table
   > (`build_transient_conduction_solver` in `app/solve/registry.py`, lazily imported, never
   > chosen automatically).
   > **Deliberately not wired to `simulation/runner.py`, a route, a migration or an AI tool
   > schema** — this session's scope was the solver capability, not the delivery path; wiring it
   > in is its own unit of work with its own `TransientThermalCase` persistence question
   > (`ThermalCase`'s steady analysis needed a nullable-`load_case` migration for the same
   > reason). Thermal *stress* coupling (a computed steady field feeding `LinearStaticSolver`)
   > shipped 2026-09-09 and is unaffected by this addition. Written on Linux, not proved on a
   > seat — no CATIA or GUI claim rides with this.
   > Tested by: `tests/test_conduction.py` (+23), `tests/test_solver_registry.py` (+5). Code:
   > `app/solve/conduction.py`, `app/solve/base.py`, `app/solve/registry.py`.

   <!-- superseded 2026-09-14 -->
   > PARTIAL (2026-09-09) — **steady conduction is no longer missing**; the 2026-09-03 status
   > saying so was true for six days and is superseded. `app/solve/conduction.py` solves
   > `div(k grad T) = -q` on tet4 and tet10 with Dirichlet, convection (Robin) and heat-flux
   > boundaries over the existing `Selector` vocabulary, verified against closed forms rather
   > than recorded output — it was built under E7 task 6, which needed it for the NAFEMS thermal
   > family, and the full result is recorded there rather than repeated here.
   > Thermal *stress* also went from a single uniform `delta_t_k` to a temperature that varies
   > with position, which is the coupling half of this task: a prescribed field reaches
   > `LinearStaticSolver.solve` and a computed one has its own load and correction path.
   > **Still missing, and now the whole of it**: *transient* conduction. Everything above is
   > steady state — there is no time integration, no heat capacity and no initial condition, so
   > "how long until it reaches that temperature" is a question this cannot answer. That is the
   > next unit of work on this task and it is a genuine gap rather than a wiring one.

2. **CFD via OpenFOAM**, deliberately late — *the meshing is the hard part* — scoped first to
   cooling flow and ducting.
   > DONE (2026-09-14) — **laminar internal flow, and the heat it carries, through a real
   > OpenFOAM, held to four closed forms and wired through the job.** Scope is exactly the two
   > words the task names. *Ducting* is an incompressible laminar steady flow (`simpleFoam`,
   > SIMPLEC); *cooling flow* is passive forced convection on top of it — a `scalarTransport`
   > temperature with the wall at one temperature or through one heat flux.
   > **Federated, not linked** (Decision 4): OpenFOAM is GPL and runs as a separate process —
   > `opencfd/openfoam-default:2412` in Docker by default (`OPENFOAM_LAUNCHER=docker|local`,
   > `OPENFOAM_IMAGE`, `OPENFOAM_TIMEOUT_S`). The pipeline is blockMesh → snappyHexMesh →
   > checkMesh → simpleFoam → writeCellCentres → writeCellVolumes. The geometry is the job's own
   > tet mesh; its boundary is split into inlet, outlet and wall by the **existing `Selector`
   > vocabulary**, so a flow case names its openings the way a load case names a fixture.
   > **Held to closed forms on the real engine** (`tests/test_solver_openfoam.py`, measured on
   > 2412). Hagen–Poiseuille in a snapped round pipe (16 cells across, 49,920 cells, 125
   > iterations, ~31 s): developed gradient +0.42%, centreline −0.96%. The rectangular duct's
   > series solution from a tet mesh and `FaceSelector`s: −0.001%. Graetz, isothermal wall
   > (Nu = 3.657): +0.30%. Graetz, uniform flux (48/11): +1.4% to +2.4% by section, because a
   > wall face's temperature under a fixed gradient is first order on a snapped cell. The flux
   > energy balance: 0.085%.
   > **What a run reports and refuses.** A run that did not converge returns no pressure drop.
   > A temperature whose residual did not settle on the converged flow is refused outright;
   > the transport has `relTol 0` and a final solve after SIMPLE converges, so the written T
   > belongs to the written U. A laminar case above Re 2000 is refused before meshing, naming
   > the velocity that would fit. The mean coefficient is a log-mean h on an isothermal wall
   > only. It is withheld, with a warning, when the outlet has saturated to the wall
   > temperature or no heat moves. A flux wall reports its energy balance and warns above 2%.
   > Patch area is the snapped mesh's own (`# Area :` in the function object's header), not
   > the drawn one: 4706 vs 4712 mm² on the pipe.
   > **Asked for** by `analysis: "flow-laminar"` with a `flow_case` (migration
   > `9947d5ff2340`, a sibling JSONB column). `element_order` is refused if given as anything
   > but 1, and forced to 1 otherwise. A study (`grids > 1`) is refused, naming
   > `flow_case.cell_size_mm` as the discretisation that matters. A missing engine fails the
   > run **before meshing** with the install sentence. The version is the one the OpenFOAM
   > banner printed. The archive holds `cell_centres`, `cell_volumes_mm3`,
   > `cell_velocity_mm_s`, `cell_pressure_mpa` and, heated, `cell_temperatures_k`,
   > `wall_face_centres` and `wall_temperatures_k`. There are no nodal fields, and the
   > surface and temperature routes refuse a flow run by name. **The job cache keys a flow
   > run on the Docker image's content id** (`KEY_VERSION` 3); a local install cannot be
   > named before it runs, so its runs are never cached. `solve.openfoam.run` is metered as
   > solver seconds. The agent is offered `run_flow_simulation`, refused before anything
   > queues when the engine is absent. The verification register gains `laminar-flow`,
   > with steady and transient conduction beside it (denominator 11 → 14).
   > **Guards broken on purpose:** 33 mutations across case, dictionaries, readers, solver,
   > runner, schema, route, cache, tool, metering and launcher. 32 were caught by named tests;
   > one is equivalent (moving the energy transport after the pressure and flux tables, which
   > never read T — the ordering that matters, T before its readers, is pinned). Every restore
   > was sha256-checked.
   > **Not claimed:** turbulence, buoyancy, temperature-dependent properties, conjugate heat
   > transfer (no solid conduction), free surfaces, external flow, and any GUI surface. **Nothing
   > has run on Windows**, where OpenFOAM has no native release. That is THE QUEUE F1. CI
   > has no image, so the Docker-backed classes **skip there, and a skip is not a pass**.
   > Tested by: `tests/test_solver_openfoam.py`, `tests/test_simulations.py`
   > (`TestAFlowRunCanBeAskedFor`, `TestAFlowRunThroughTheRealEngine`),
   > `tests/test_simulation_cache.py`, `tests/test_agent.py` (`TestRunFlowSimulation`),
   > `tests/test_metering.py`, `tests/test_observe_report.py`.

   <!-- superseded 2026-09-14 -->
   > NOT STARTED.

3. **Optimisation: [OpenMDAO](https://openmdao.org/)** (NASA Glenn, Apache-2.0) as the MDO
   framework; SIMP and level-set topology optimisation; DOE; response surfaces; multi-objective
   trade-offs (mass vs stiffness vs cost). The capability that makes an AI designer *better* than
   a human rather than merely faster.
   > DONE (2026-09-14) — **every method the task names exists, and each is held to a closed
   > form or a theorem.** OpenMDAO is a real dependency now (`openmdao==3.45.1`, and
   > `pylife==2.3.1` beside it, in `requirements.txt` — `pyproject.toml` had declared both while
   > no requirements file named either), and `OpenMdaoDriver` agrees with `ScipyDriver` on the
   > same problem. `drivers.py`, `models.py` and `problem.py`, untested in the status below, are
   > tested.
   > **DOE** (`doe.py`): a full factorial with the bounds included, and a Latin hypercube with
   > one point per stratum in every projection, random within its stratum. The seed is required
   > and reproduces the plan; a survey over the problem's evaluation budget is refused, never
   > truncated; every point is built through the `Evaluator` and a failure is recorded with its
   > reason.
   > **Response surfaces** (`surface.py`): degree 1 or 2 least squares in normalised coordinates,
   > whose error basis is the exact leave-one-out residual from the hat matrix, checked against
   > brute-force refits. Refused: fewer than terms + 2 points, a rank-deficient design (the
   > two-level factorial asked for a quadratic is named), leverage 1. A prediction outside the
   > bounds is refused and one outside the sampled range is flagged extrapolated; every
   > prediction is an `Estimate` with a band, never a measurement.
   > **Multi-objective** (`pareto.py`): epsilon-constraint through `optimise()`. Anchors run
   > first and a missing anchor stops the study before any interior run; interior levels become
   > `<=`/`>=` assertions by sense; a failed sub-problem is a gap, not a point; dominated and
   > duplicate points are dropped and counted. Checked against the closed-form front
   > f₂ = (√f₁ − 2)². `ParetoFront` has no `best`, because a front is a choice for a person, and
   > a front read off a DOE survey is labelled *sampled*, not optimised.
   > **SIMP** (`topology.py`) on `app.solve.plane`'s own element matrices: `element_stiffness`,
   > `restrained_dofs` and `winding` are now public. **`plane.py` is fingerprinted**, so the V&V
   > artefact was re-recorded, with outcomes unchanged to round-off. OC update with a log-space
   > multiplier search, area-weighted cone density filter. The sensitivity is checked against a
   > central difference on every element; the step was measured, not picked: the error is
   > U-shaped with its minimum at 1e-4. Penalty 1 is the convex variable-thickness sheet, checked
   > against F²L/(E·v·H·t) from a random start — the *compliance* is unique and the layout is not
   > (any parallel full-length fibres), so the test reads the number. The penalised layout is
   > held to a theorem: never stiffer than the convex optimum under the same filter.
   > "Converged" needs the volume on target as well as the densities stopped.
   > **A defect found while pinning the filter:** `cKDTree.sparse_distance_matrix` already stores
   > each element's zero self-distance, so the `+ r·I` the filter added counted every element's
   > own weight twice. On a uniform mesh that is invisible, because rows still sum to one. A
   > graded-mesh test against the definition found it.
   > **Level set** (`levelset.py`): the reaction–diffusion form (Yamada et al., *CMAME* 2010) on
   > the element graph. It is black and white at every iterate. Void is an ersatz at 1e-3,
   > because 1e-9 made a crisp layout fail the equilibrium check (measured). The volume is met by
   > rank, because λ only shifts the solution. It is held to two theorems — the convex tension
   > bound, and never stiffer than the full design space — and the regularisation is measured
   > to shorten the boundary (314 → 145 edges). It is element-wise, so the boundary is
   > staircased, and its statement says so.
   > Every result says it is a concept on a mesh and not a part, and none carries an `optimum`,
   > `part` or `solution`. **52 guards were verified by breaking each and watching a named test
   > fail**, with restores checked by sha256. The 8 that no test caught at first each got one,
   > and that pass is what found the filter defect.
   > **Not claimed**: any of this reaching the agent, a route or the GUI — it is a library, as
   > the rest of `app/optimise/` has been since 2026-09-06; topology on solids (plane only); a
   > smooth level-set boundary; a stress or frequency objective. Written on Linux; no seat claim.
   > Tested by: `tests/test_optimise_problem.py`, `tests/test_optimise_models.py`,
   > `tests/test_optimise_drivers.py`, `tests/test_optimise_doe.py`,
   > `tests/test_optimise_surface.py`, `tests/test_optimise_pareto.py`,
   > `tests/test_optimise_topology.py`, `tests/test_optimise_levelset.py`,
   > `tests/test_optimise_honesty.py`, `tests/test_optimise_gradients.py`.

   <!-- superseded 2026-09-14 -->
   > PARTIAL (2026-09-06) — the honesty rules and the gradients now have tests, and
   > `design/sensitivity.py` finally has a caller outside a test (it had been listed beside
   > `app/render/` and `app/ai/vision.py` as capability wired to nothing). Gradients checked
   > against hand-differentiated functions whose partials *differ*, so a swap is caught, and
   > across variables scaled a thousand apart, so an absolute step masquerading as a relative one
   > is caught. **An ungradable point is `available=False` with a reason, never zeros** — a zero
   > gradient tells an optimiser it has arrived. `drivers.py`, `models.py`, `problem.py` still
   > untested. Tested by: `tests/test_optimise_honesty.py`, `tests/test_optimise_gradients.py`.
   > Code: `app/optimise/`.

4. **The surrogate flywheel.** Every FEA run is a labelled datapoint (geometry + loads →
   response). After a few thousand, a surrogate answers "roughly how stiff" in milliseconds, and
   the optimiser explores thousands of candidates before spending a real solve. Training data is a
   free by-product of use; the `Solver` ABC means the surrogate drops in as just another solver —
   which is what that seam was built for.
   > DONE (2026-09-14) — **every solved run is a labelled datapoint, and a surrogate trained on
   > them may rank and never decide.**
   > **Plan change, and why.** The task says the surrogate "drops in as just another solver".
   > It does not, on purpose. A `Solver` answers a job, and a job's output is stored as a field,
   > drawn on the part, interpreted into prose and fed to the verification register — every one
   > of which treats it as measured. A surrogate answering there would be the third forbidden
   > speedup in `docs/MAKING_IT_FASTER.md` (sampling where the provenance says measured), and it
   > would *decide*, which E22 task 1's rule forbids. So it lives beside the optimiser.
   > **Harvest** (`app/simulation/datapoints.py`, kept out of `app/optimise` so that package
   > stays free of sessions): an organisation's finished solid runs, newest first and bounded,
   > derived on read and never stored. It is scoped to the organisation, because one tenant's
   > solves are not another's training data. Each skipped run gets a reason:
   > * a cache hit, because the solve it copies is already counted;
   > * a run on a borrowed temperature field;
   > * a result without the response;
   > * a load with no single force (a pressure, a moment);
   > * a geometry with no bounding box.
   >
   > Each datapoint carries solver, solver version and mesh convergence, so the in-house
   > `solver_version=None` flagged under E10 task 1 surfaces as "(version not recorded)", not as
   > a version.
   > **Surrogate** (`app/optimise/flywheel.py`): a power law `log r = b₀ + Σ bᵢ log fᵢ`. That is
   > the shape linear elasticity already has, so on a textbook family it is exact and its
   > exponents are readable physics. Trained on 32 real `LinearStaticSolver` bar solves, the
   > exponents come back as +1 on force and length and −1 on modulus, width and height, within
   > 0.02. The error basis is the exact leave-one-out error in log space (checked against
   > brute-force refits), reported as a ×/÷ factor together with how many runs were single-grid,
   > whose own mesh error the band does not include. Refused: too few points, a constant
   > feature, collinear features, a non-positive feature or response (a zero displacement is a
   > fixture problem), leverage 1. A point outside the trained range is flagged.
   > **The rule as types** (`app/optimise/screening.py`):
   > * `Estimate` (provenance `approximated`, with a band and a basis sentence) has no
   >   `measured` or `passed`.
   > * `Ranking` has no `best` and always sends at least one candidate to be rebuilt.
   > * `screen` rebuilds the top `verify_top` through the `Evaluator` — counted against the
   >   budget and judged against the constraints — and `best_measured` reads only those.
   >
   > A test with a confidently backwards estimator shows the measurement winning.
   > `MAY_RANK_NEVER_DECIDE` is one server string.
   > **Not claimed**: a surrogate reachable by the agent or an API (E22 task 1 owns putting the
   > rule where users read it); any response other than the three stored scalars; features
   > beyond bounding box, volume, modulus, net force and element size — a geometry *family*
   > needs its own parameters, which a run does not record; a few thousand runs of data (the
   > test trains on 32).
   > Tested by: `tests/test_optimise_flywheel.py`, `tests/test_optimise_surface.py`
   > (`TestARankingMayRankAndNeverDecide`), `tests/test_simulation_datapoints.py`.

## ERA IV — ENGINEERING KNOWLEDGE

##### Phase E11 — The requirements model *(needs an ME)* #####

> ✅ PHASE COMPLETE (2026-09-09) — all four tasks done and tested. Two residuals are named in
> their status lines rather than hidden: tracing needs a spec, a plan or an assertion set,
> because a part built by conversation carries no authored rationale to scan (E16's memory
> work owns that), and the SysML v2 interop in task 1's brief is an **export**
> (`app/requirements/interop.py`) with the round-trip question recorded there rather than
> answered. An ME is still what turns a good requirements *model* into good requirements.

**~4 engineer-months.**

A machine is defined by a specification before geometry. Without this, "design me a press" has no
answerable meaning.

1. **Requirements with flow-down and validation flow-up**: target mass, duty cycle, envelope,
   regulatory regime, cost ceiling, service life. Aligned to **SysML v2** —
   **[SysON](https://mbse-syson.org/)** and **[Capella](https://mbse-capella.org/)** (both
   Eclipse, both free) are credible implementations with a published interop path.
   > DONE (2026-09-09) — flow-down landed 2026-09-06 with the graph (`parents`, `children_of`,
   > `ancestors_of`, `roots`, `leaves`, cycles refused at construction); **flow-up landed today,
   > and it was the half that made the report say the wrong thing about the requirement that
   > matters most.** A top-level requirement — "the press shall weigh under 850 kg" — names no
   > measurement of its own: it was decomposed into derived ones that do, and it is met when they
   > are. Verified requirement by requirement it came back NOT VERIFIED for ever, so **the single
   > requirement the customer signed was the one the report was silent about while everything
   > below it passed.** A requirement with nothing to measure and children in the set now takes
   > its verdict from them: all met is `PASSED`, any violated is `FAILED`, anything else is
   > `UNMEASURED` naming the children that are open.
   >
   > Four decisions ride with it. A derived verdict is **not a measurement** — it is sound only as
   > far as the decomposition is complete, which nothing here can check — so its evidence basis is
   > `by decomposition`, `Coverage` counts it in its own column, it never lands in
   > `by_measurement`, and the caveat is printed beside the verdict rather than kept in a
   > docstring. A **violated child fails its parent** even though the parent was never measured,
   > because the alternative is a report where the customer's requirement is silent and the
   > requirement it was broken into is red. A parent that *does* name a measurement keeps its own
   > number, since a measurement of the thing outranks an inference about it. And the roll-up runs
   > to a **fixed point** rather than in topological order — the graph is already refused if
   > cyclic, so iterating until nothing moves is exact, needs no ordering pass and resolves a
   > grandparent the round after its child.
   >
   > **One construction rule moved, and the guard did not weaken.** A requirement with no measure
   > had to name a capability in `needs`, which forced the top-level customer requirement to claim
   > a missing tool in order to be writable at all. The refusal is now `RequirementSet`'s, where
   > the decomposition links (which point *upward*) are visible: no measure, no `needs`, and
   > nothing decomposed from it is still refused as a wish, and the message names both ways out.
   > Tested by: `tests/test_requirements_verification.py` (+11), `tests/test_requirements_model.py`
   > (+2) — five guards verified by breaking them. Code: `app/requirements/model.py`,
   > `app/requirements/verification.py`.

2. **Requirement → assertion binding. A requirement nothing checks is a wish.**
   > DONE (2026-09-09) — the binding itself landed 2026-09-06 (`Requirement.assertion()`, so a
   > failing report says *"REQ-014 not met"* and not *"mass_kg <= 4.2 failed"*), and what closed it
   > today is that **the product can now be handed a specification.**
   > `POST /kernel/conversations/{id}/requirements` takes a `.kreq` document, measures the part the
   > conversation has built, and answers with the report, its coverage and its evidence. Until
   > then `app/requirements/` was 2,860 lines that **nothing outside a test had ever called** —
   > the same integration gap `render` and `measure` closed one layer down, and the reason both
   > those endpoints exist.
   >
   > It measures the part itself rather than accepting numbers, because a requirement verified
   > against a payload the client assembled is a claim about the client. Three refusals stay
   > distinct: a document that does not parse comes back 422 with **every** problem at once (an
   > engineer's document usually has several, and stopping at the first turns one editing session
   > into five), a requirement whose scan nobody ran is `UNMEASURED` with `scans_needed` naming
   > what to run, and a conversation with no part is the same 409 the other two give. The report is
   > bound to the backend, its version and the conversation, per Decision 3.
   > Tested by: `tests/test_kernel_routes.py` (+8), `tests/test_requirements_against_a_part.py`.

3. **Traceability**: every design decision to a requirement or a standard — what a signing
   engineer demands first.
   > DONE (2026-09-09) — and the status line was **stale rather than the work being missing**:
   > `app/requirements/trace.py` has answered both directions since 2026-09-06 and this session
   > audited it rather than rewriting it. *Why is this rib here* walks from a feature note up the
   > decomposition to the customer or the standard; *what satisfies REQ-014* walks down to the
   > features, plan calls and assertions that cite it. **The links are read out of the design, not
   > declared beside it** — `FeatureSpec.note`, `PlannedCall.note` and `Assertion.note` already
   > carry rationale, so a link cannot rot into disagreeing with the design. Precision is handled
   > by only recognising ids that are in the set (exact, case-sensitive, token-bounded), with the
   > loose pattern used solely to report **dangling** citations left by a renamed requirement; and
   > `untraced` names a requirement nothing cites, which is the output that earns the module — it
   > may be true by accident of the shape somebody drew.
   > **What it does not have, named rather than left to be found:** a part built by conversation
   > carries no authored rationale for it to scan, so tracing today needs a `DesignSpec`, a `Plan`
   > or an assertion set. Giving conversational calls a rationale note belongs with E16's memory
   > work, not here.
   > Tested by: `tests/test_requirements_trace.py` (17). Code: `app/requirements/trace.py`.

4. **Coverage reporting**: verified / by-what-evidence / unverified.
   > DONE (2026-09-09) — the split (a *violated* requirement is verified; only an unchecked one
   > damages coverage) and `scans_needed()` landed 2026-09-06. Two things closed it today.
   > **By-what-evidence was structurally present and empty in practice:** the OCCT measurement
   > payload attached no provenance at all — only the interrogation scans did — so every
   > requirement met by an exactly integrated volume reported its evidence as `unrecorded`, and
   > `Coverage.by_measurement` was zero for every real part. `metrology.measure` now records a
   > basis per path: `measured` for the integrations and the traversals, **`approximated` for the
   > oriented bounding box** (the box for a given orientation is exact; the *orientation* is a
   > search, and a billet bought from it is right to within how well that search did), and
   > `unavailable` with a reason for a mass with no density. And coverage gained its
   > `by_decomposition` column from task 1, so a reader can tell a number that was integrated from
   > one that was sampled from one that was never measured at all.
   > Tested by: `tests/test_requirements_verification.py`, `tests/test_kernel_routes.py`.

**This phase unblocked E5 task 2, which closed the same day.**

##### Phase E12 — Load cases, materials, and standard parts #####

> ✅ PHASE COMPLETE (2026-09-10) — all four tasks done and tested. **Task 3's premise turned out
> to be wrong and is superseded rather than met**: BOLTS is not on PyPI, has had no commit since
> May 2023, and — fatally — carries dimensions and no engineering data at all, which is precisely
> the half a bought-in part has to contribute here. That was measured on 2026-09-06 and written
> up in `app/parts/fasteners.py`; the set is first-party ISO instead, and `PartSource` is the
> seam a `.blt` importer would arrive through if that ever changes.

**~8 engineer-months, mostly needs an ME.**

1. **Load-case library** — standardised, per-domain, *executable*: pothole strike, panic braking,
   curb drop, proof/ultimate factors, press tonnage cycles. Today invented per conversation, so no
   two runs are comparable.
   > DONE (2026-09-10), superseding PARTIAL (2026-09-06) — the library *composes* the existing
   > vocabulary (Decision 2), pinned by reading the load types out of `types.Load`'s own union so
   > a new one automatically joins the set it must stay inside. One composed case is **actually
   > solved**: 12 MPa of pressure gives σ = 12.0 MPa exactly.
   > **The last gap the module named in its own docstring is closed**: a `LoadCase` carried
   > `name` and nothing that said which recipe and which factor produced its loads, so "this is
   > the 1.5 ultimate case" was a claim in a string rather than a record. `LoadCase.provenance`
   > exists and `compose(..., recipes=(...), factor=...)` fills it with each recipe's own source.
   > A case built by hand leaves it **`None`, meaning hand-authored and never unknown** —
   > stamping a recipe onto a case nobody derived that way would be a citation for work that did
   > not happen, which is the `app/verify/` rule applied to the input side. A `recipes` argument
   > naming something not in the library is **refused**, because a provenance nobody can look up
   > reads as checkable and is not. Tested by: `tests/test_load_library.py` (47).
   > **API note:** `LoadCase` gained an optional `provenance` object; it is JSONB on the job row
   > and mirrored in `../Kryova-frontend/src/types/api.ts`.

2. **Materials.** The honest research finding: **the open materials databases are the wrong kind of
   open** — Materials Project, AFLOW, OQMD, OPTIMADE are DFT/atomistic; superb, and useless for an
   engineering S-N curve. Free engineering sources (MakeItFrom, MatDat, ASM's free tier) are
   partial and licence-varied. Deliverable: the **schema, provenance model and ingestion path** —
   every property carries source and confidence; buying Granta/MatWeb later becomes data-loading,
   not re-architecture.
   > DONE (2026-09-10), superseding PARTIAL (2026-09-06) — all three deliverables the task names
   > are built and tested: the **schema** (`Property`, with no unit argument — the unit is looked
   > up from `PROPERTY_UNITS` by name, so a datasheet figure in GPa has nowhere downstream to
   > hide), the **provenance model** (`Source` + `Status`, where `SPECIFIED` is deliberately a
   > fourth member because a standard's minimum is a floor you may size to and a typical value is
   > not), and the **ingestion path** (`transcribe`, the one place a conversion happens).
   > What is *not* done is loading a commercial database, and the task says so itself: buying
   > Granta or MatWeb "becomes data-loading, not re-architecture". That is the state this task
   > was written to reach. Tested by: `tests/test_materials.py`. Code: `app/solve/materials.py`.

3. **Standard parts. 70%+ of any real machine is bought.**
   **[BOLTS](https://boltsparts.github.io/)** (open library of parametric ISO/DIN parts with
   dimension metadata) as the base; supplier CAD (TraceParts, McMaster) via *import*, respecting
   their terms, never redistribution.
   > DONE (2026-09-10), superseding PARTIAL (2026-09-06) — **and the BOLTS premise is
   > superseded, not met.** Checked 2026-09-06: not on PyPI under any name, no commit since May
   > 2023, and it carries `d1`, `k`, `s`, `e`, `l`, `pitch` and nothing else — everything needed
   > to *draw* a bolt and no proof load, property class, stress area, mass or torque. The half
   > BOLTS has is the cheap half. So the shipped set is first-party ISO 4014/4032/7089 in M5–M16
   > across classes 8.8/10.9/12.9, with ISO 898-1/898-2 strengths marked `SPECIFIED` because they
   > are minima a supplier is held to; mass is computed and marked `ESTIMATED`; tightening torque
   > is **not stored at all**, because it is not a property of the bolt. `PartSource` is the seam
   > a `.blt` importer would implement, and its LGPL data is not redistributed.
   > Extended 2026-09-10 with `app/parts/bearings.py` — see task 4. Tested by:
   > `tests/test_parts_catalogue.py`, `tests/test_bearings.py`. Code: `app/parts/`.

4. **A parts *selection* engine**: given load, speed, life — choose the bearing; never model what
   should be bought.
   > DONE (2026-09-10) — `app/parts/bearings.py`. **The line between the standard's arithmetic
   > and the manufacturer's data is the whole design.** ISO 281 rating life is arithmetic —
   > `L10 = (C/P)^p`, `p = 3` ball and `10/3` roller — reproduced from the standard and checked
   > against worked examples computed by hand in the test, the same treatment the solver gets
   > against closed-form solutions. **`C` and `C0` are the maker's numbers and are not shipped**:
   > they depend on internal geometry that differs between makers for the same ISO boundary
   > dimensions, and ISO 281's formula for `C` needs an `f_c` table indexed on geometry nobody
   > publishes. So `SHIPPED_BEARINGS` carries **ISO 15 boundary dimensions only** — a standard,
   > citable, identical for every maker — and `select` **refuses** on a bearing with no sourced
   > rating, naming which one is missing. A selection computed from an invented rating looks
   > exactly like engineering and is not.
   > Four more refusals that are the substance: nothing-fits and everything-that-fits-is-unrated
   > are **different `Refusal`s** with different fixes; a duty 4% past the largest bearing is
   > refused rather than rounded; a bearing that passes on life and fails the static check is
   > skipped, because a selection that fails a stated check is not a selection; and every
   > `Selection` carries `STANDING_CAVEATS` — L10 is a 90% population life and not a guarantee,
   > no `a_ISO` is applied (it needs two operating conditions nobody has stated), and
   > lubrication, temperature, limiting speed and fits are not checked. `X` and `Y` are
   > arguments with no defaults beyond the exact pure-radial case, for the same reason.
   > Tested by: `tests/test_bearings.py` (27).

**Gate G2 opens after E11 + E12.**

##### Phase E13 — Design rules, DFM, tolerance and cost *(needs an ME)* #####

**~9 engineer-months.**

1. **Design rules as assertions** — minimum wall by process, draft angles, bolt torque and preload,
   thread engagement, weld sizing, machining access — attached automatically from feature type +
   declared process, running in the E5 engine. **DFM becomes a red build.**
   > DONE (2026-09-15) — rules are attached from the declared process and the part's own feature
   > tools (`app/rules/processes.py`: a pocket brings the cutter-radius rule, a plain plate does
   > not), become `Assertion`s for the E5 loop through `Attachment.assertions()`, and run as a red
   > build on the live part at **`POST /kernel/conversations/{id}/rules`**, which runs the scans
   > the rules need itself. **No limit ships**: each is the caller's with its source, and a rule
   > the process needs with no limit is `unset`, which makes the report not ok. Bolt preload and
   > thread engagement are `app/rules/joints.py` over the catalogue bolt (VDI 2230 preload,
   > recorded approximated); tightening torque stays a derivation in `fasteners.tightening_torque`,
   > not a rule, because no limit exists to compare it to. **Plan change: weld sizing moves to
   > E17 task 3**, which owns the weldment model it needs, and the `welded` set says so on every
   > report. `engine.py` now has its consumer. **Tests written on Linux and not run there**, at
   > the user's instruction; they run on Windows. Tested by: `tests/test_rules_processes.py`,
   > `tests/test_rules_joints.py`, `tests/test_kernel_routes.py`
   > (`TestCheckingDesignRulesAgainstTheLivePart`).

   <!-- superseded 2026-09-15 -->
   > PARTIAL (2026-09-06). A rule naming a quantity outside `kernel/contract.py` is refused **at
   > construction**; the verdicts are literally `design.assertions.Outcome`; and **a rule resting
   > on a *sampled* bound cannot prove a pass** — `minimum_wall_mm >= 2.5` measuring 2.6 is
   > `PASSED` but *not proven*, because an upper bound from a finite ray set can only prove the
   > violation. **`engine.py` has no consumer anywhere in `app/`** — a verified module, not a
   > product path. Tested by: `tests/test_rules_*.py` (127 tests across this phase). Code:
   > `app/rules/`.

2. **Tolerance and GD&T** — stack-up (worst case and RSS), fit selection, datum schemes, FTA.
   *A drawing without tolerances is not a drawing.*
   > PARTIAL (2026-09-15) — **fit selection's arithmetic is in** (`app/rules/fits.py`): a hole
   > and shaft zone, each with its deviations and the source they were read from, give the largest
   > and smallest clearance and the fit's kind; `select_fit` keeps the candidates that always land
   > inside a sourced clearance requirement and chooses the widest combined tolerance, with every
   > rejection's reason in numbers. **No deviation ships.** `gdt.py` now has consumers
   > (`app/manufacture/inspection.py`, and the drawing's tolerance table). **Open:** ISO 286's
   > tables are not transcribed (a document), so zones are the caller's; and CATIA FTA is THE
   > QUEUE **E7**. **Tests written on Linux and not run there.** Tested by:
   > `tests/test_rules_fits.py`, and the files below.

   <!-- superseded 2026-09-15 -->
   > PARTIAL (2026-09-06) — stack-up and GD&T tested. GD&T deliberately evaluates no tolerance-zone
   > geometry, guarded three ways. **`gdt.py` has no consumer anywhere in `app/`.** Tested by:
   > `tests/test_rules_*.py`.

3. **Cost model** — material + process + tooling + assembly time, as an assertion. An agent that
   ignores cost confidently designs the unbuildable.
   > DONE (2026-09-15) — `app/rules/cost.py`: material (mass × stock factor × price/kg), process
   > (cycle and setup over the batch at a machine rate), tooling (amortised) and assembly (minutes
   > × labour rate), each with its formula and sources. **No rate ships.** A missing input is not
   > a zero: the total is unavailable naming it, and an explicit zero needs a source. A total is
   > recorded `APPROXIMATED`. `CostTools.cost` is what E5's `CostBudget` was waiting on, so a cost
   > budget now passes or fails where rates are given, is `UNMEASURED` where one is missing or the
   > currency differs (nothing converts), and **its value is now recorded approximated** (an
   > interface change in `machine_checks`). **Tests written on Linux and not run there.** Tested
   > by: `tests/test_rules_cost.py`.

   <!-- superseded 2026-09-15 -->
   > NOT STARTED — out of scope for now. E5 task 1's cost budget reports `UNMEASURED` until this
   > lands.

4. **Process-specific rule sets**: cast, machined, printed, sheet, moulded, welded.
   > DONE (2026-09-15) — `RULE_SETS` in `app/rules/processes.py`, one per process, each template
   > naming the measured quantity, the direction, the triggering features and why, and **no
   > number**. Cast and moulded: wall, draft, undercuts, inside radius. Machined: cutter radius
   > (pocket, groove, slot, rib, shell only), undercuts, X/Y/Z travel. Printed: wall, open edges,
   > build volume, with overhang named as not measured yet. Sheet carries no solid rules and names
   > `app/sheetmetal/` as where its checks live; welded checks the parent wall and names E17 task
   > 3. **Tests written on Linux and not run there.** Tested by: `tests/test_rules_processes.py`.

   <!-- superseded 2026-09-15 -->
   > NOT STARTED.

## ERA V — SCALE

##### Phase E14 — Product structure, decomposition and interface contracts #####

> ✅ PHASE COMPLETE (2026-09-09) — all six tasks done and tested, with two residuals named in
> their own status lines rather than hidden: **effectivity** (task 1) is not implemented and
> `Component.revision` is free text nothing selects on, and task 5's repository is
> **in-process, not distributed**, so persisting it is E15's storage question. Neither is a
> silent gap; both are written where the next session will read them.

**~9 engineer-months. Architecturally the most important era-V phase.**

1. **Product structure and BOM as first-class data** — assembly tree, effectivity, revisions,
   where-used. A conversation transcript is not a data structure.
   > DONE (2026-09-06) — the product *graph*: a bolt used 40 times is one component and 40
   > occurrences, and **occurrence numbers are declared, never positional**, so inserting a leg at
   > the head of a list renumbers nothing — the topological-naming answer one level up.
   > **Effectivity is not implemented and says so.** Tested by: `tests/test_assembly_structure.py`.
   > Code: `app/assembly/` (6 modules).

2. **Hierarchical decomposition with interface contracts.** One component at a time, against a
   contract: mounting points, envelope, mass budget, interface loads, clearances. How human teams
   partition work, and **the only way the context problem is solvable — no model size fixes it.**
   > DONE (2026-09-06) — interface contracts as assertions over a boundary that name **which
   > side** a change violated. Tested by: `tests/test_assembly_contracts.py`.

3. **The creative core: an interface contract is itself a compilable spec fragment.** Both sides
   compile against it; the swingarm's spec imports the pivot contract, so does the frame's. A
   violation is a **compile error at the interface naming both parties** — not a clash found in
   assembly three weeks later. Reuses the entire E1–E5 machinery.
   > DONE (2026-09-06) — plus a conservative broad-phase clash check and a mass roll-up. **A
   > partial result never gets the headline name**: an incomplete clash publishes
   > `checked_minimum_clearance_mm`, not `minimum_clearance_mm`, because both directions of that
   > error make the machine look safer. Tested by: `tests/test_assembly_clash.py`,
   > `tests/test_assembly_mass.py`.

4. **Change propagation.** `diff.py` already answers this within one part (`changed_calls`,
   `downstream`, `invalidates`); this lifts it to the product graph.
   > DONE (2026-09-06). Tested by: `tests/test_assembly_structure.py` (108 tests across tasks 1–4).

5. **Concurrency and locking**: multiple agents/users on one product without corruption.
   > DONE (2026-09-09) — `app/assembly/locking.py`. **The hole this closes is one no frozen data
   > structure can see**: two callers each read the head, each build a new `ProductStructure` from
   > it, each store theirs, and the second erases the first with nothing anywhere raising. Only a
   > repository can notice, so there now is one.
   >
   > **Two mechanisms, answering different questions, and they compose.** `ProductRepository.commit`
   > is optimistic: every commit names the revision it was written against, and one written against
   > anything but the head is refused with *what moved and who moved it*. That guard cannot be
   > forgotten — it applies to every write, including the ones nobody thought to lock. `LeaseBook`
   > is the early half: a component claimed by one author blocks another author's commit that
   > touches it, turning a conflict discovered after an hour into a refusal in the first second.
   > Holding a lease does **not** excuse a stale base, and a test pins that, because the tempting
   > simplification is to let one stand in for the other.
   >
   > Four decisions are in the code rather than assumed. Leases are per **component**, never per
   > occurrence — a bolt used forty times is one component, and locking `frame/leg.2` would let two
   > authors edit one design down two paths and call it disjoint. **There is no clock in the
   > module**: every call that cares takes `now`, because an expiry read from the machine's clock
   > cannot be tested without sleeping and cannot be reasoned about across two processes. A merge
   > combines two disjoint descendants of one base and **refuses by name** where both changed the
   > same component — nothing here can choose between two engineers' brackets, and a repository
   > that guessed would be worse than one that stopped — while two sides that made the *same*
   > change do not conflict, since components compare by value and a rebuild-from-spec workflow
   > would otherwise be unmergeable. And a commit that changed nothing is refused: a revision
   > recording no work makes every later "what happened here" misleading.
   >
   > **What it is not, stated rather than left to be discovered:** a distributed lock. The
   > repository is one in-process object, so two API workers each holding their own would each be
   > internally consistent and collectively wrong. Persisting it — a revisions table and a lease
   > row with the database as the arbiter — is the next question and belongs with E15's storage
   > work; it is written into `app/assembly/__init__.py` so the next session does not have to
   > rediscover it.
   > Tested by: `tests/test_assembly_locking.py` (40) — six guards verified by breaking what they
   > guard (the base check, lease enforcement at commit, expiry, who may release, the merge
   > refusal, and releasing leases only after a commit is accepted).

6. **The seat-side half: a conversation owns a *set* of CATIA documents with exactly one active.**
   > DONE (2026-09-06) — `CatiaDocument.is_active`, a partial unique index, migration
   > `c7e2a9d4f1b3`. On a seat a second `catia_new_part` adds a document and deactivates the
   > current one, nothing is abandoned, `catia_product_create` is bound as a `product`,
   > `catia_open_document name=` switches, and `catia_component_add kind=existing document=<name>`
   > resolves to the path the part was really saved under. **Measured need:** ladder prompt S2 on
   > the real seat, where one document per conversation left no route to a second part and the
   > agent called `catia_new_part` seven times at the refusal. The open kernel keeps its
   > one-document contract. Tested by: `tests/test_multi_document.py` (24 tests).

**Gate G3 opens after E14 + E9.**

##### Phase E15 — Throughput, storage and the compute fabric #####

**~6 engineer-months.**

1. **Batch compilation** — a `Plan` as one kernel session (OCCT); a CATScript executed once
   (CATIA), never 10⁵ COM calls.
   > PARTIAL (2026-09-10) — **the OCCT half is done and driven; the CATIA half is emitted and
   > has never been executed.** `app/design/batch.py`.
   > **What was actually missing on OCCT was the economics, not the session.** `OcctRunner`
   > already holds one `PartDocument` for its lifetime, so a plan run through one runner was
   > always one session — the OCAF labels that make naming work persist between calls. What made
   > a large plan expensive is `Detail.FULL`: every mutating call measures the whole shape
   > afterwards, because the interactive agent cannot react to a number it was not given, and
   > measuring integrates over the part. At 10⁵ operations that *is* the run. `plan_for` decides,
   > `build` executes, and the decision reaches the caller so the runner is constructed at the
   > right detail rather than the threshold living in two places. A test pins that **batching
   > does not change what gets built** — same plan digest, same calls — because a batch path
   > that quietly issued something different would break determinism (I5) invisibly.
   > **The CATIA half is a generated script and nothing has run it.** `as_catscript` emits a
   > compiled plan as one CATScript calling one dispatcher per operation — not inlining CATIA's
   > API, because the bridge already owns that mapping and a second copy would drift the day an
   > operation gained an argument. Arguments are emitted as resolved literals, so nothing is
   > recomputed at run time by a second compiler with its own opinion about `wall_mm * 2`. The
   > escaping is where a generated script goes wrong *silently* — a doubled quote, a `bool`
   > emitted as `1`, a list split on a comma inside a feature name — so that is what the tests
   > cover. **There is no seat on this machine, so it has never been driven, and a batch path
   > nobody has run is one nobody should ship on.** That is the residual.
   > Tested by: `tests/test_design_batch.py` (19). Code: `app/design/batch.py`.

2. **Simulation compute**: queue, autoscale, result caching keyed on the provenance digest.
   **FEA is not a request-path workload** and never becomes one.
   > DONE (2026-09-15) — **the application's half of autoscale is written: the job table
   > decides a worker count, and says why.** `app/jobs/autoscale.py` and
   > `GET /admin/compute/scaling` (staff only). **It recommends and changes nothing**, because
   > resizing a fleet is the orchestrator's job (an HPA on an external metric, KEDA, a scale-set
   > cron). The signal is the durable queue, not CPU, which sits at 100% on a solver worker
   > whether one job waits or four hundred. Five rules, written before any tuning:
   > - backlog ÷ `JOB_WORKERS`, rounded up;
   > - never below what is running, since a solve handed to ccx cannot be stopped;
   > - the oldest wait past `AUTOSCALE_TARGET_WAIT_S` adds one worker over the current size;
   > - scale down one step at a time;
   > - clamp to `[AUTOSCALE_MIN_WORKERS, AUTOSCALE_MAX_WORKERS]`, with `capped` saying when the
   >   ceiling clipped the answer.
   > Without `?current=` the wait and step rules cannot act, and the answer says so rather than
   > guessing a fleet size. **New surface, stated**: one staff route, `ComputeScalingRead`, and
   > three settings (documented in `.env.example`). No migration. The cache half stands as
   > recorded below. **Not claimed**: a fleet acting on the number (P9 task 3's image has never
   > been built into one). **Not run here**: tests written 2026-09-15 on Linux and not executed,
   > at the user's instruction.
   > Tested by: `tests/test_jobs_autoscale.py`, `tests/test_simulation_cache.py`,
   > `tests/test_solver_registry.py`, `tests/test_simulations.py`.

   <!-- superseded 2026-09-15 -->
   > PARTIAL (2026-09-14) — **the cache now binds the engine that computes an answer, read
   > before the run. For four days it bound nothing. Autoscale still needs a fleet this
   > deployment does not have.**
   > **The defect, found closing E10.2.** The status below says an unmeasured solver version
   > never matches a known one. The key hashed `job.solver_version`, but the runner sets that
   > only *after* the solve. So at key time it was empty on every job, and every key carried
   > the same "unknown" sentinel. Unknown matched unknown everywhere: a CalculiX upgrade, or a
   > fix to the in-house solver, would have been served the previous build's answers.
   > `test_two_unmeasured_versions_match_each_other` pinned that as a feature. A second defect
   > sat under the first. The runner asked the registry for the version of the solver's *name*
   > (`linear-static`), the registry keys versions on the *backend* (`internal`), and so
   > **every in-house result was stored with `solver_version` None**.
   > **What binds now.** `cache.engine_for(backend)` names what will compute the answer before
   > it runs:
   > - **CalculiX:** version *and* binary sha256 (`registry.calculix_identity`), because two
   >   builds of one release print one version.
   > - **In-house solvers:** `source_identity()` — V&V's fingerprint plus the runner and
   >   `coupling.py`, which decide what is stored — with numpy's and scipy's versions.
   > - **OpenFOAM:** its image id.
   > - **Every analysis:** the gmsh version as well.
   > An identity that cannot be read means **no key**: not looked up and not offered. The
   > backend is `runner.backend_for(analysis)`, read at run time, and the route uses the same
   > function. A plane run is keyed `internal` even on a CalculiX deployment, because
   > `_execute_plane` never reads `SOLVER_BACKEND`. **After the run, `cache.unbound`
   > re-checks**: if a different backend answered or the engine moved while the run was in
   > flight, the row's key is cleared. `registry.backend_of` maps solver names to backends,
   > and a test builds every solver every table makes to keep it complete. The per-binary
   > version cache is keyed on path, size and mtime, not the path, because a package upgrade
   > replaces the binary under a running server.
   > **Interface changes:**
   > - `cache.Inputs` loses `solver_version`, and `engine` becomes required.
   > - `KEY_VERSION` 4: every existing key misses once.
   > - `cache.UNKNOWN_VERSION` is removed.
   > - An in-house run's `solver_version` goes from null to `"<APP_VERSION>+<git sha>"`.
   > - A queued plane row names `internal` rather than `SOLVER_BACKEND`'s value.
   > No migration and no response shape change.
   > **Guards broken on purpose:** 17 mutations across the registry, the runner and the cache,
   > all caught by named tests, every restore sha256-checked.
   > **Flagged, not fixed:** the agent's `run_simulation` still labels a queued row
   > `linear-static` whatever `SOLVER_BACKEND` says (`app/ai/tools.py`). The runner overwrites
   > the label, and the key no longer reads it.
   > **Autoscale is still the residual**, for the reason below. P9 task 3's image is written and
   > has never been built into a fleet.
   > Tested by: `tests/test_simulation_cache.py`, `tests/test_solver_registry.py`,
   > `tests/test_simulations.py`.

   <!-- superseded 2026-09-14 -->
   > PARTIAL (2026-09-10) — **the cache is built and the queue already existed; autoscale needs
   > a fleet this deployment does not have.**
   > **Result caching keyed on the provenance digest** is `app/simulation/cache.py`, and it is a
   > liability before it is an optimisation: the failure mode of a cache in a verification
   > product is not "slow", it is a confident number computed under different conditions. So the
   > key covers exactly what the result depends on — geometry checksum, load case, thermal case,
   > element size and order, analysis, grid count, thickness, solver and solver version — and a
   > test asserts the parametrised list is *every field of the dataclass*, so an input added and
   > not keyed shows up as a failing test rather than as a wrong answer.
   > Three exclusions each of which would be a bug. **An unmeasured solver version does not match
   > a known one**: treating "we do not know which CalculiX" as a match is the exact claim
   > Decision 3 forbids. **The lookup is scoped to the organisation**, and that is a security
   > boundary rather than tuning — a result crossing a tenant boundary tells one customer that
   > another has a part with this checksum, load case and mass, which is a data leak wearing a
   > performance improvement. And **the copy keeps its own timings**: a result reappearing with
   > `finished_at` from three weeks ago makes the fleet's timing figures meaningless.
   > A hit is recorded as a hit (`cache_hit`, `cache_source_id`), and the source follows the
   > chain to a run that really solved, so "how much did we actually solve" stays answerable.
   > **Autoscale is the residual and it is a deployment, not a module.** `ThreadPoolJobQueue`
   > bounds concurrency in one process; scaling out needs a broker and machines to scale onto,
   > and writing a policy against neither would be an untested guess in the one place a wrong
   > guess costs money. P9 task 3's container image is its prerequisite.
   > Tested by: `tests/test_simulation_cache.py` (28). Code: `app/simulation/cache.py`,
   > `app/simulation/runner.py`, migration `753d57fdd4bb`.

3. **Geometry storage/versioning**: content-addressed CAD with **semantic diffs under a tolerance
   policy** (line diffs on CAD are meaningless; reviewers should see only consequential change).
   Extends `app/media/`.
   > DONE (2026-09-10) — content addressing already existed (`Media.sha256`, dedup for free);
   > the semantic diff is `app/geometry/semantic_diff.py`.
   > **The tolerance policy is the load-bearing part and it is relative, not absolute.** A
   > 0.1 mm³ change is nothing on a gearbox casing and is the whole part on an O-ring groove, so
   > there is no single absolute number that is right at both ends of the range. Every continuous
   > quantity is compared as a fraction of the **larger** of the two values — symmetric, so a
   > part that doubled and one that halved report the same movement, where dividing by `before`
   > would put one edit on each side of the threshold. An absolute floor handles the one case a
   > relative test cannot: a value that went to or from zero, where a hairline sliver would
   > otherwise report as an unbounded change and become every panel's headline.
   > **Counts are exact and are never tolerated.** There is no sense in which 41 faces is 40
   > faces to within a tolerance, and a policy that smoothed that over would hide the most
   > reviewable kind of change there is — including the one a volume comparison misses entirely,
   > a boolean that split the part in two without moving its volume.
   > **"No consequential change" is a positive answer**, said in words. A reviewer told the
   > re-export was clean has learned something; one shown a blank panel assumes it is broken.
   > And a comparison where a quantity was measured on one side only is reported **incomplete**
   > rather than clean — the same rule Decision 3 applies to an unmeasured assertion.
   > Tested by: `tests/test_geometry_semantic_diff.py` (19). Code:
   > `app/geometry/semantic_diff.py`.

4. **CATIA session pool, crash recovery, affinity** — a smaller problem now it is off the critical
   path.
   > PARTIAL (2026-09-10) — **affinity is built and is correctness rather than scheduling;
   > crash recovery is named and needs a seat to develop against.**
   > `app/catia/affinity.py`. A CATIA document is open on **one** workstation: it is not
   > replicated, it cannot be migrated, and a call routed elsewhere does not fail with "wrong
   > device" — it fails with "no such document", or worse, succeeds against a different part of
   > the same name. So a conversation with a document is pinned to the machine holding it, and
   > when that machine is offline the answer is `STRANDED`, naming it, rather than a fallback to
   > another seat. **That refusal is the module**: routing around an offline seat is a wrong
   > answer that looks like a working one, and the failure is silent — the agent reads a
   > plausible error, decides the pad failed, and rebuilds a part that already exists somewhere
   > nobody is looking at.
   > `STRANDED` and `NONE_ONLINE` are deliberately different answers: one is solved by
   > connecting any workstation, the other only by connecting *that* one, and collapsing them
   > into "no workstation" sends a user to start the machine that is already running.
   > Only the unpinned case is a choice, and it is least-loaded with ties broken by device id —
   > two workers must not pick differently for one conversation, or both open a document.
   > `rebalance_needed` reports an imbalance and never acts on one: moving a live conversation
   > between seats is exactly what `choose` refuses to do, so the honest response is to let it
   > drain.
   > **Still open: crash recovery and a real session pool.** `Outcome.stranded` names the state
   > — the document exists, the work is real, nothing can touch it until the machine returns —
   > and what the product should *do* about it (resume from `CatiaCheckpoint`, or offer to
   > rebuild elsewhere from the design record) needs a seat to develop against. `CatiaRegistry`
   > is also still in-process and says so in its own docstring: several workers need a shared
   > bus before a pool means anything.
   > Tested by: `tests/test_catia_affinity.py` (14). Code: `app/catia/affinity.py`.

5. **Observability**: per-operation success rates, agent trajectory traces, solver timings, failure
   taxonomy. Nothing measured op reliability before this, which made capability claims
   unfalsifiable.
   > DONE and fully wired (2026-09-06) — `~0.09 µs` per disabled span. All four spans the package
   > landed as catalogued holes are installed, and **the first number out of them is the one
   > Decision 1 rests on: a parametric rebuild costs 0.49 ms**, so a 200-value sweep is ~0.1 s of
   > rebuilds. That argument has been the basis of the OCCT decision since the plan was written
   > and had never been measured. Tested by: `tests/test_observe*.py` (68 tests). Code:
   > `app/observe/` (5 modules).

## ERA VI — THE AGENT

##### Phase E16 — Tool retrieval, planning and long-horizon memory #####

> ✅ PHASE COMPLETE (2026-09-10) — all six tasks done and tested, and the shape of what was
> built is a decision worth reading before anyone "improves" it. Tasks 2, 3 and 4 are all
> deliberately *restrained*, because Era VIII's research pass measured the alternative:
> memory scaffolds degraded long-horizon performance in all ten models tested, additional
> orchestration does not consistently help, and the strongest models fail hardest when they
> attempt the most ambitious multi-step strategies. So the server holds the plan and refuses
> out-of-order moves rather than generating or replanning; the state block carries the
> decisions rather than everything; and failure recovery escalates with a question built from
> the tool's own words rather than from a model call. **Task 1's residual is named and still
> open**: per-workbench sub-agents were never built, and the un-narrowed alternative is what
> `app/ai/tool_retrieval.py` already beats — that is a scope decision, not an unfinished one.
> The real ceiling behind task 1 is also unchanged and is not this phase's to close: PRO1's
> fourth run exhausted a 32,768-token window with four parts still to make, and what binds is
> the transcript rather than the offer.

**~10 engineer-months. Research-adjacent; the least predictable phase.**

1. **Tool retrieval at scale — a measured wall, not a worry.** 201 operations heading past 900.
   Published 2026 numbers: accuracy falls from **87.4% at 500 tools to 65% at 2,000**; definitions
   alone can consume 50k+ tokens before the user's request is read; retrieval errors account for
   about **half of agent failures** at scale; retrieve-then-rerank measured at ~76% where naive
   selection scored far lower.
   > DONE offline, PROVEN at a gate (2026-09-08). Built **lexically, not semantically**
   > (2026-09-06), for the reasons `app/retrieval/` chose lexical: the discriminating terms here
   > are exact (`fillet`, `helix`, `dépouille`, `M6`), the registry is already in memory so there
   > is no index to keep in step, and no embedding model ships with this deployment.
   > `app/catia_kb/recognise.py` supplies the domain layer — it already knows *bore* means Hole in
   > five interface languages — and the scored matches are backed by two tables the scoring cannot
   > express: **a floor of everything the frozen system prompts name** (withholding one of those
   > is what teaches a model to hallucinate a call) and **intent families** for tasks whose
   > vocabulary is disjoint from the tool that serves them ("adjust the thickness until it weighs
   > 2.4 kg" shares no word with `catia_set_parameter`).
   > Measured offline: 110 OCCT tools narrow to 17–39 per turn, every inclusion carrying the rule
   > that put it there. **The shipped selector's own defect was fixed by this work** — it was
   > withholding five to nine *prompt-named* tools on every realistic message,
   > `catia_set_parameter` among them on all five, which is the tool rung 3 is about and the one
   > the agent could not find for three sessions.
   > **Driven at last on 2026-09-08 and the hypothesis holds — with a new ceiling behind it.** The
   > narrowed offer (55 of 220 tools) was driven through the real GUI for the whole of Level 4;
   > `qwen3.5:9b` emitted correct structured calls at 50–52 tok/s for turns of forty steps, which
   > the un-narrowed payload never did. **What binds now is the transcript, not the offer**: PRO1's
   > fourth run built its first solid and then exhausted the 32,768-token window with four parts
   > still to make, refused loudly by `providers/ollama.py`.
   > **Per-workbench sub-agents remain open.** Tested by: `tests/test_ai_tool_selection.py`. Code:
   > `app/ai/tool_retrieval.py`. Reports: `docs/verification-2026-09-06/REPORT.md`,
   > `docs/verification-2026-09-08/REPORT.md`.

2. **Planning.** "Design a swingarm" → an ordered, dependency-aware task graph with checkpoints.
   > DONE (2026-09-10) — the sequencing half, and it is **deliberately not an autonomous
   > planner**. `app/ai/taskgraph.py` holds a plan the agent declared (`plan_work`) and enforces
   > its order (`update_task`); it does not generate one, re-generate one, or ask a model to
   > reason about it.
   > **The evidence for that restraint is in this document.** Era VIII records that memory
   > scaffolds degraded long-horizon performance in *all ten* models tested, that additional
   > orchestration does not consistently help, and that the strongest models show the highest
   > catastrophic-failure rates because they attempt the most ambitious multi-step strategies.
   > What is measured to help is shortening the horizon. So the model does the thinking and the
   > **server holds the list and refuses the moves that are out of order** — which is the one
   > thing a graph adds over the list `planning.py` already provides. `MAX_TASKS` is 40 for the
   > same reason: a plan longer than that is a work breakdown structure, and refusing is more
   > useful than accepting sixty steps nobody will follow.
   > Three refusals carry it. A task cannot be marked done while something it stands on is not,
   > and the message names which. A cycle is refused **at construction, naming the path** — "there
   > is a cycle" in a twenty-task plan sends somebody reading every edge. And a plan that is
   > entirely blocked says so rather than returning an empty ready-list, because a model handed
   > `[]` reads it as "nothing to do" and closes the turn reporting success.
   > `SKIPPED` is not `DONE`: it satisfies a dependency (the work downstream was cleared) while a
   > reader asking "was the sizing worked out" is still told no.
   > Tested by: `tests/test_taskgraph.py` (28), `tests/test_agent_planning_tools.py` (21). Code:
   > `app/ai/taskgraph.py`, migration `314c1981036f`.

   <!-- superseded 2026-09-10 -->
   > PARTIAL (2026-09-06) — a tested seam and one first step, **deliberately unwired**.
   > `app/ai/planning.py` turns a request into the list of requirements it states, with the numbers
   > and tolerances attached and a three-state record where "nobody checked" is never "fine". No
   > sequencing, no dependency graph, no replanning. Half-wiring it would add a schema to the
   > payload task 1 is shrinking, or describe machinery to the model that is not there.

3. **Long-horizon memory.** The 2026 literature converges on hierarchical working memory —
   subgoals as chunks with summarised observations (HiAgent-class results: ~2× success on
   long-horizon tasks). Kryova already has the right instinct: `resume.py` reads the operation log,
   not the transcript, because a trimmed window and an LLM paraphrase cannot be trusted about last
   week. That principle generalises to the whole design record.
   > DONE (2026-09-10) — the principle generalised to the whole design record, which is what
   > this task asked for and what `resume.py` alone could not give. Three accounts now reach the
   > state block from the *record* rather than from the transcript, and each answers a different
   > question no other can: `resume.py` says **what was done** (the operation log),
   > `_design_lines` says **what was decided** (the persisted spec's parameters), and
   > `_plan_lines` says **what is left and in what order** (the task graph). The hierarchy is the
   > chunking the 2026 literature describes — subgoals with their observations underneath — and
   > it is built from records nobody paraphrased.
   > **Parameters in the block, features behind a tool.** The feature list can be forty lines and
   > is one `read_design` away; the parameters are the decisions, they are short, and they are
   > what a later turn gets wrong. An agent told everything every turn stops being able to find
   > anything, which is the same reason `build_history` was kept out of the block in the first
   > place. A model asking the user for a wall thickness it set three turns ago is the failure
   > this closes, and it is the same shape as the one `resume.py` was written against.
   > Tested by: `tests/test_agent_planning_tools.py::TestTheStateBlock`. Code: `app/ai/state.py`,
   > `app/ai/resume.py`, `app/core/designs.py`.

   <!-- superseded 2026-09-10 -->
   > PARTIAL (2026-09-03) — resume-from-log shipped. Code: `app/ai/resume.py`.

4. **Failure recovery.** Diagnose → repair → bounded retry → escalate with a *specific* question
   (E5's machinery is the foundation).
   > DONE (2026-09-10) — the three behavioural guards bound the *retrying*; `app/ai/recovery.py`
   > is the last word, **escalate with a specific question**, which is what the bounded loop
   > could not produce on its own.
   > **The distinction is the whole module.** A turn that ends on a guard currently says the
   > agent stopped repeating itself. That is true and it is not answerable — the user reads it
   > and has no idea what to type. What they can act on is *"`catia_pad` on plate.profile failed
   > 3 times and each time the geometry will not take it. It said: 'Cannot be padded: open
   > profile.' Should I change the geometry to make it fit, or is the current shape the one you
   > want?"* — a subject, a cause, a verbatim quote and two options.
   > **Nothing here calls a model.** The question is built from the tool's own error text and the
   > arguments the call was made with, both already in hand. An LLM would put a paraphrase
   > between the user and the failure on the one screen where the exact wording is the evidence,
   > and would cost a model call at the moment the turn has already gone wrong.
   > Failures are counted **by (tool, kind), not by message**: two attempts at the same pad
   > failing with slightly different wording are one problem, and a counter keyed on the message
   > would never reach its bound — which is how a retry budget quietly stops existing. A failure
   > the taxonomy does not recognise escalates *anyway*, quoting verbatim and truncating visibly,
   > the same contract `app/solve/calculix/diagnose.py` holds: a taxonomy that labelled
   > everything would destroy the evidence the next pattern is written from.
   > The loop gained a fifth exit, `needs_input`, which ends the turn on the third repeat rather
   > than spending fifty more rounds on the same refusal first.
   > **Corrected 2026-09-11 — one of the three guards was punishing the correct recovery.**
   > `_refused_before` remembered a refused write and turned back any verbatim repeat, on the
   > stated grounds that *"the call did not run, so nothing about the part is different"*. That
   > premise was **assumed, never measured**, and it is false whenever another call has landed in
   > between. Measured on ladder L2, `qwen3.6:27b`, on the open kernel: `catia_pad` was refused —
   > *"Sketch 'sketch' has no closed profile ... Draw a rectangle, circle or polygon on it
   > first"* — the model did **exactly that** (`catia_sketch_rectangle` → `ok`, `profiles: 1`),
   > and the identical pad that would have built the block was turned back **unsent**, in 0 ms,
   > with no `CatiaOperation` row. The model concluded *"the part is empty"* and spent six steps
   > rebuilding. A guard may bound a loop; it may never punish a model for doing what a refusal
   > told it to do. The refusal is now remembered **with the mutation clock it was refused at**
   > and stands only while that clock has not moved — a successful mutating call moves it, a read
   > cannot. The S1 case this guard was written for is still caught: the repeat is dispatched
   > once, refused again by the real tool for the real reason, and re-armed at the new clock, so
   > a third verbatim send is blocked. One dispatched call is the right price for not blocking a
   > legitimate retry.
   > Tested by: `tests/test_recovery.py` (21),
   > `tests/test_agent.py::TestARefusedWriteIsNotRepeated` (8, three of them added 2026-09-11 and
   > verified by breaking the clock comparison — two fail, the six pre-existing stay green),
   > `tests/test_agent.py::TestOpeningDocumentsIsNotBuildingParts`. Code: `app/ai/recovery.py`,
   > `app/ai/agent.py`.

   <!-- superseded 2026-09-10 -->
   > PARTIAL (2026-09-08) — a third behavioural guard landed from the Level-4 runs:
   > **`MAX_EMPTY_DOCUMENTS`**, because opening a document is the one mutation that changes nothing
   > about the part, and both existing guards counted it as progress — five empty parts in one turn
   > tripped neither. Measured effect: one document instead of five.

5. **Human checkpoints.** Structured approval gates — a reviewable diff with a sign-off record, not
   a chat message (P5 owns the surface).
   > DONE (2026-09-10) — P5.5 built the record, the rules and the reviewer's page; this is the
   > agent reaching one. `request_approval` raises a gate and **ends the turn on it**, and the
   > ending is the point rather than a side effect: a checkpoint the agent announces and then
   > walks past is not a checkpoint, and a note in the system prompt asking it to stop is
   > something it is free to ignore and has. The loop reads `awaiting_approval` off the tool
   > result and breaks with `stop_reason: "awaiting_approval"`.
   > **The gate is pinned to the design as it stands**, or to the declared plan when there is no
   > design yet — both are digestible, and `core/gates.decide` re-digests what the decider is
   > looking at, so an approval cannot land on something that moved while it was pending.
   > A `checkpoint: true` task in the graph is the other half: `blocked_by` reports it as
   > blocking everything downstream, transitively, so a task three steps behind an unfinished
   > sign-off is told about the sign-off rather than about its immediate parent.
   > Tested by: `tests/test_agent_planning_tools.py::TestRequestingApproval`, `tests/test_gates.py`
   > (24). Code: `app/ai/tools.py`, `app/core/gates.py`, `app/ai/taskgraph.py`.

6. **Cost/time estimation before starting** — "this is four hours of compute and $X" (P8 owns the
   meter).
   > DONE (2026-09-10) — `estimate_cost`, and it **does no arithmetic**: it reads
   > `app/core/estimates.estimate_run` and passes each `Estimate.human()` sentence through as
   > written. That is P8.4's one-meter rule reaching the agent, the same way `cost-notice.tsx` is
   > it reaching the screen — assembling a sentence here from `units` and `unit` would be a third
   > place for the wording, and the honesty, to drift.
   > **Too little history comes back as such**, never as zero and never hidden: below three
   > comparable runs the estimator refuses, and the tool's own description tells the model not to
   > turn "we cannot estimate this yet" into a guess. A cost estimate is the one number in a
   > product nobody contradicts afterwards, which is exactly why a made-up one survives.
   > Tested by: `tests/test_agent_planning_tools.py::TestEstimatingCost`. Code: `app/ai/tools.py`,
   > `app/core/estimates.py`.

**Creative leverage:** the design record replaces the transcript. Rationale already travels in
`FeatureSpec.note`; extend to decisions, rejected alternatives and reasons, and *"why is this rib
here"* has an answer in six months — from the artefact.

## ERA VII — OUTPUT, AND THE MACHINES

##### Phase E17 — Manufacturing output #####

**~9 engineer-months.**

1. **Drawings with GD&T**: auto views, sections, details, dimension generation, FTA, BOM tables,
   title blocks. *Without this nothing leaves the building.*
   > PARTIAL (2026-09-15) — **GD&T and BOM tables now reach the sheet.** `Drawing` carries the
   > part's `Tolerancing` and its parts list; `dxf.py` draws a geometric-tolerance table (datums,
   > then each feature control frame as its compartments: feature, symbol, zone with Ø and the
   > material condition, datum references) and a parts list stacked on the title block, both on
   > declared layers, and `to_dict` carries both. Views, sections, details, traced dimensions and
   > the title block were already tested (below). **Open:** frames are tabulated by feature name,
   > not attached to the geometry by a leader, because nothing resolves a frame's feature name to
   > an edge on a view; the view layout does not reserve the tables' zones, so a crowded sheet can
   > overlap them; and CATIA FTA on the seat is THE QUEUE **E7**. **Tests written on Linux and not
   > run there.** Tested by: `tests/test_manufacture_drawing_tables.py`, and the files below.

   <!-- superseded 2026-09-15 -->
   > PARTIAL (2026-09-06) — dimensioning and sheet layout tested. **First and third angle
   > demonstrably place views on opposite sides**, so a convention that was stored and ignored is
   > caught: swapping them fails two tests, and a drawing read in the wrong convention is
   > manufactured **mirrored** with nothing looking wrong. A dimension traced to its parameter is
   > distinguishable from one measured off the solid; `locate.py` finds a dimension's own feature
   > in the geometry (radius matching, deduped by axis position, never by face) rather than
   > trusting where the design says it should be, and reports a dimension **unplaced** rather than
   > drawn somewhere plausible when it cannot.
   > **A same-radius coincidence in the mirror-orientation check was found and fixed 2026-09-06**
   > (`TestWhereAKnownFeatureLands`): two circles of equal radius closer together than their
   > diameter always intersect, so a single stray vertex of the *real* bore was passing a naive
   > nearest-point check for a deliberately-wrong mirrored centre 20 mm away. `_roundness` now
   > samples twelve angles around the candidate circle and takes the worst, which a coincidental
   > intersection cannot satisfy.
   > Tested by: `tests/test_manufacture_drawing.py`, `tests/test_manufacture_dimensions.py`,
   > `tests/test_manufacture_sheet.py` (154 tests across tasks 1–2). Code: `app/manufacture/`.

2. **Export**: STEP AP242 (the one that carries PMI), IGES, JT, 3MF/STL, DXF flat patterns —
   mostly native OCCT.
   > PARTIAL (2026-09-06) — DXF and STEP export tested through OCCT. **Open: STEP/DXF export from
   > a live CATIA seat.** Tested by: `tests/test_manufacture_export.py`. Code:
   > `app/manufacture/dxf.py`, `app/manufacture/export.py`.

3. **Weldments and tubing**: beads, symbols, cut lists, tube routing, and **weld sizing** (moved
   here from E13 task 1 on 2026-09-15: a weld's size is a property of a bead on a weldment, which
   this task creates). **A motorcycle frame is a tubular weldment.**
   > DONE (2026-09-15) — `app/manufacture/weldment.py`: members as `BeamSection`s between points;
   > a **cut list** grouping identical pieces, with two-member corners mitred at (180° − φ)/2 and
   > the long point extended by (d/2)·tan of it, d the depth in the joint's plane (refused by name
   > for a section rotated off that plane); three or more members at a node are listed coped, not
   > computed. **Beads** as fillet welds sized by throat, with leg a·√2 and deposited volume a²·L
   > per run; the **symbol** is carried as its ISO 2553 designation (`a4 fillet 40, both sides`) —
   > drawing the glyph on a sheet is E17 task 1's. **Weld sizing** by the throat-area method,
   > required throat = w / f_vw,d, where the design shear strength and any throat-to-wall ratio
   > are the caller's with sources; a weld with no declared force is unmeasured. Mass adds weld
   > metal to the members. `app/manufacture/tubing.py`: **tube routing** through waypoints on one
   > bend radius, with tangent lengths, overlapping bends refused, developed length and the LRA
   > bend table (signed rotation between bend planes); bender limits are the caller's. Not built
   > as a solid, and routing does not search around obstacles. **Tests written on Linux and not
   > run there**, at the user's instruction. Tested by: `tests/test_manufacture_weldment.py`.

   <!-- superseded 2026-09-15 -->
   > NOT STARTED.

4. **CAM**: **[OpenCAMLib](https://github.com/aewallin/opencamlib)** (LGPL) — drop-cutter and
   waterline primitives — plus machining features, stock, fixturing notes.
   > DONE (2026-09-15) — **Plan change: OpenCAMLib has no wheel for this project's Python 3.12**
   > (`pip` finds no matching distribution), so it cannot be federated. Drop-cutter is geometry,
   > not physics, so Decision 2 does not apply: `app/manufacture/dropcutter.py` computes it
   > exactly for flat and ball end mills (vertex, edge and facet contacts in closed form), held
   > to a brute-force oracle that fails on any gouge. The **waterline** is marching squares over
   > drop-cutter queries with every loop point bisected onto the true waterline, labelled sampled
   > with its step. `app/manufacture/cam.py`: **stock** as the smallest block in the caller's
   > list holding the part plus the caller's allowance (oriented box preferred); **operations**
   > from every feature tool; the **cutter** for a pocket as the largest in the caller's list no
   > wider than twice the measured minimum concave radius; **fixturing** by 3-2-1 over the datum
   > scheme; a zig-zag drop-cutter **finishing raster**. Not produced: feeds and speeds, setups by
   > approach direction, G-code. **Tests written on Linux and not run there.** Tested by:
   > `tests/test_manufacture_cam.py`.

   <!-- superseded 2026-09-15 -->
   > NOT STARTED.

5. **Inspection planning**: CMM points and measurement plans derived from the GD&T scheme.
   > DONE (2026-09-15) — `app/manufacture/inspection.py`, the first consumer of `app/rules/gdt.py`:
   > a plan that aligns on the datum features in precedence, measures size before any tolerance
   > at MMC or LMC (its bonus depends on it), then each feature control frame with what the CMM
   > evaluates for that characteristic and the datums it is evaluated in. Points are laid
   > cell-centred on the nominal plane or cylinder with the probe approach into the material.
   > **The count is the caller's sampling strategy with its source**; what is fixed is geometry
   > (3 points determine a plane, 5 a cylinder), so a form tolerance at that count is refused as
   > measuring zero on any part. Features with no geometry are unresolved, with no strategy
   > unset, and either makes the plan incomplete. No DMIS is written. **Tests written on Linux
   > and not run there.** Tested by: `tests/test_manufacture_inspection.py`.

   <!-- superseded 2026-09-15 -->
   > NOT STARTED.

6. **Technical documentation**: assembly instructions, exploded views, service manuals, parts
   catalogues. **Instructions for use it produces are a draft for a named person, never a
   finished document (`app/compliance/boundary.py`), and their digital delivery must pass
   `app.compliance.instructions.unmet`** — Article 10(7) of the Machinery Regulation, read from
   the Official Journal on 2026-09-14 (E19 task 4). The checker exists before this task does, on
   purpose.
   > DONE (2026-09-15) — `app/manufacture/documentation.py`, from `ProductStructure`: a **parts
   > catalogue** numbered over the BOM; an **assembly sequence** with sub-assemblies first and
   > the author's instance notes carried; an **exploded view** as placement data and, given each
   > part's shape, an OCCT compound drawn by `app.render` (deterministic); a **service manual**
   > draft (disassembly in reverse, service parts). Every document is a `Draft` with
   > `Stance.DRAFT_FOR_A_NAMED_PERSON`, refused without a named person or a product model, with
   > no field that marks it finished and a `not_written` list of what the manufacturer writes.
   > Digital delivery of instructions for use goes through `instructions.unmet` with the draft's
   > own model (`delivery_gaps`). Annex III's contents are not quoted in the codebase, so they
   > are not paraphrased here. **Tests written on Linux and not run there.** Tested by:
   > `tests/test_manufacture_documentation.py`.

   <!-- superseded 2026-09-15 -->
   > NOT STARTED.

**Gate G4 opens after E17 (with E17.3).**

##### Phase E17.3 — Sheet metal (pulled forward to run with Era IV) #####

> ✅ PHASE COMPLETE (2026-09-09) — all three tasks done and tested, with one residual named
> rather than hidden: **there is still no sheet-metal operation in the CATIA registry**, and
> there deliberately is not one. The registry is what the *seat* can be told to do, and a
> `catia_sheetmetal_wall` declared here would be a promise the bridge cannot keep — CATIA's
> SheetMetal Design workbench is real, the COM calls behind it are unwritten, and neither
> half can be verified on a machine with no seat. That work is **E1 in THE QUEUE**
> (`docs/WINDOWS_VERIFICATION.md`), where the other hardware-blocked items live, not a
> declaration made on Linux. What the phase promised — authoring wired to geometry — is met
> through the open kernel, which is what Decision 1 asks for anyway.

**Sequencing exception, and it is deliberate.** This belongs to E17 because it *is* manufacturing
output, and it runs early because the missions need it: the ladder promises M3 (enclosure) in
Era IV and M5 (press) in Era V, and both need sheet-metal authoring long before the rest of E17's
drawings-and-CAM work. It is tracked as its own phase for exactly that reason.
**[FreeCAD SheetMetal](https://github.com/shaise/FreeCAD_SheetMetal)** (LGPL) is the working
reference implementation.

1. **The arithmetic**: bend allowance `BA=(π/180)·θ·(r+K·t)`, setback, deduction, unfold,
   formability.
   > DONE (2026-09-06) — every number checked against arithmetic in the test. Tested by:
   > `tests/test_sheetmetal*.py` (147 tests). Code: `app/sheetmetal/` (6 modules).

2. **K-factor with the ANSI/DIN distinction** and radius-to-thickness-dependent material sheets.
   > DONE (2026-09-06). **There is no default K-factor anywhere** — a `Bend` requires one and a
   > `KFactor` requires a `Source`; `assumed()` demands a written reason and marks the pattern
   > provisional with an `UNMEASURED` finding. ANSI and DIN differ by a factor 1.211 at r/t=1.5,
   > which moves a 90° bend in 2 mm by 0.24 mm — the distinction is real and is carried.

3. **Wall, bend, flange as authoring operations wired to geometry.**
   > DONE (2026-09-09) — **a `SheetMetalPart` now builds as an OCCT solid, and the blank and the
   > solid are one calculation rather than two sets of numbers somebody typed twice.**
   >
   > The path is two modules and the split is the point. `app/sheetmetal/fold.py` places the part:
   > a frame per flange, a cylindrical sector per bend, holes carried onto their faces, and the
   > **closed-form volume** of the result. It imports no kernel, so the placement runs offline in
   > milliseconds like the rest of that package. `app/kernel/occt/sheetmetal.py` builds it — a box
   > per face, a swept annulus segment per bend, one fuse, then the holes cut — and measures
   > **exactly** the analytic volume on every case tried (L-bracket up and down, a 135° bend, a
   > two-bend channel, a four-walled enclosure, a 180° hem).
   >
   > **The two descriptions are tied by an identity, not by a tolerance.** Both layouts consume one
   > `unfold.tangent_extents` walk — extracted for this, so a leg cannot be 47.4 mm flat and
   > 48.0 mm folded — and the folded volume differs from `blank area × t` by exactly
   > `Σ θ·t²·w·(0.5 − K)`, which is **zero when K = 0.5** and is the closed form of the residual
   > `app/design/missions.py` publishes as `flat.volume_mismatch_mm3`. That number was M3's only
   > evidence that the drawing and the part were the same object; it is now a measurement of the
   > arithmetic.
   >
   > Four things are recorded in the code because each builds a plausible wrong part rather than
   > failing: the bend centre sits `t + r` beyond the frame plane bending **up** and `r` below it
   > bending **down** (swap them and every outside dimension is wrong by 2t, with the volume
   > right); the sector is swept along the **bend**, never along its rotation axis, which for an up
   > bend is `−v` (sweeping the axis mirrors the part about its own root at identical volume and
   > face count); `gp_Ax2(P, n, u)` is the frame that fills `u×[0,L], v×[0,W], n×[0,t]`, because
   > OCCT's Y is `main × X`; and the sector's arcs are built through a **midpoint**, since this
   > OCP build's two-point `GC_MakeArcOfCircle` returns the major arc for both senses — the same
   > trap `app/verify/le11_geometry.py` hit first.
   >
   > A hole declared on a face is **cut**, not ignored: a folded solid that quietly kept the
   > material would over-weigh the part and every mass, clash and packaging claim downstream reads
   > that weight. A hole in a bend zone is refused by the same message the blank gives, from the
   > same function. A part whose *blank* would overlap itself is **not** refused — it folds
   > perfectly well and only cannot be nested flat, and an over-refusal is the failure mode
   > `app/catia/` warns about.
   > Tested by: `tests/test_sheetmetal_fold.py` (27), `tests/test_kernel_sheetmetal.py` (18) —
   > six guards verified by breaking what they guard (bend centre, sweep direction, box frame,
   > mould-line vs tangent length, bend direction, and the blank/folded reconciliation), each
   > caught by a named test with the restore verified byte-for-byte.

##### Phase E18 — The machine missions #####

**~12 engineer-months across the ladder.**

Not new capability — **proof of it**, and the discovery of the twenty things nobody predicted. Each
mission runs end-to-end **in the product**, is reviewed by a real engineer, and stays green
forever: spec, geometry, mesh, analyses with convergence evidence, fatigue assessment, drawings
with tolerances, BOM with bought parts, cost estimate, requirements coverage — every number
traceable.

**M5 is the honest mid-point milestone**: structure, mechanism, sheet metal, bought parts, fatigue
and guarding at once. If M5 does not work, the phases before it were decoration.

1. **M1 — machined bracket.**
   > DONE (2026-09-05). Green on the real kernel. Tested by: `tests/test_design_missions.py`.

2. **M2 — welded frame / bench.** The first assembly.
   > DONE (2026-09-06) — and it passes *carrying* what it does not claim. Tested by:
   > `tests/test_mission_m2.py`.

3. **M3 — sheet-metal enclosure.** The folded enclosure E17.3 was pulled forward for.
   > DONE (2026-09-06), **and its finding is the important one: there is no sheet-metal operation
   > in the OCCT backend at all**, and `SheetMetalPart` cannot compile to a `DesignSpec` — so M3
   > declares the cover *twice*, as a fold tree and as a hand-drawn section, and the only thing
   > holding the two descriptions together is a volume residual. Tested by:
   > `tests/test_mission_m3.py`.

4. **M4 — gearbox.**
   > NOT STARTED — PENDING in the ladder report, naming the phase that owns the gap.

5. **M5 — sheet-metal stamping press.**
   > NOT STARTED — PENDING.

6. **M6 — belt conveyor system.**
   > DONE (2026-09-10) — **and it moved by its own rule rather than by anyone deciding it
   > should.** Its declared `needs` were E14.1 (product structure and BOM as first-class data)
   > and E12.3 (standard parts); both had landed, and nothing about a conveyor needs E13's
   > design rules or E9's multibody — which is exactly why it moves while M4, M5, M7 and M8 do
   > not. A rung whose stated prerequisites are met and which is still marked pending is a
   > ladder that has stopped measuring anything.
   > **This is the first rung whose difficulty is a *count*.** Nothing in a conveyor is
   > geometrically hard: rails, a leg pair every so often, a roller every so often. What is hard
   > is that the counts are a consequence of the length rather than numbers somebody typed. So
   > every occurrence in the product graph is placed inside a loop over a derived count, and the
   > mission's headline assertion is that **the mass rolled up from the graph equals the mass
   > computed from the pitches**. A 12-metre conveyor built to a 6-metre bill of materials is
   > the real-world failure, it is arithmetic rather than geometry, and computing the mass twice
   > from different directions is the only way to see it. A second assertion checks the same
   > thing a different way — the occurrence count against the BOM — because the two fail
   > differently: one catches a count that drifted, the other a part placed twice with a
   > compensating error elsewhere.
   > Both fenceposts are asserted longhand (`n` bays need `n + 1` leg pairs; `g` gaps need
   > `g + 1` rollers), because those are the two places a hand-written BOM is wrong once.
   > The roller is a **bought part modelled only as the envelope it occupies**, with the
   > catalogue mass — E12.3's whole argument is that a bought part is selected rather than
   > modelled, and a modelled one is how a BOM stops matching what anybody can order.
   > What it does **not** claim is recorded on the rung and reaches the public gallery: no load
   > case has been run, the rollers do not turn, the idler has no part number, there is no
   > quotation, and there is no weldment model or cut list.
   > Tested by: `tests/test_mission_m6.py` (21), `tests/test_design_missions.py`,
   > `tests/test_mission_m2.py::TestTheLadderItself`.

7. **M7 — 6-axis robot arm.**
   > NOT STARTED — PENDING, waiting on E9's multibody.

8. **M8 — motorcycle chassis + swingarm.**
   > NOT STARTED — PENDING.

**Ladder standing at 4/9** (M1, M2, M3, M6).
**Gate G5 opens after M2 upward.**

## ERA VIII — THE WORLD THIS HAS TO SURVIVE CONTACT WITH

*Added 2026-09-09 from a fan-out research pass over primary sources — regulation, standards
bodies, licence texts, benchmark papers and vendor announcements — with each claim put through
adversarial verification before it was written here. Every phase below exists because something
outside this repository is already true and the plan did not know it.*

**The era's argument in one sentence:** every era before this one makes the machine *right*;
this one makes the rightness **transferable** — to a market surveillance authority, to a
signing engineer, to a customer's licence auditor, and to anyone comparing us against a
competitor who has raised three hundred million dollars.

Four things the research changed, stated up front because they contradict what the earlier parts
of this plan assume:

1. **"STEP AP242 — the one that carries PMI" (E17 task 2) is a promise the kernel does not
   currently keep.** OCCT's STEP *writer* offers AP242 only as **`AP242DIS`** — the Draft
   International Standard schema — while for AP214 it offers a published-IS option; the reader
   is documented as supporting "some parts of AP242"; and the base translator carries geometry,
   topology and assembly structure only, with colours, names, layers, validation properties and
   GD&T all living in the separate XDE layer whose documented contract for PMI round-trip is one
   enumerating sentence. FreeCAD — the other large OCCT consumer — filed the same gap on
   2025-02-23, labelled it `3rd party: OCC`, and sized closing it at 350 hours. E21 owns this.
2. **Passing NAFEMS benchmarks is verification and can never be validation**, in ASME's own
   words, and the benchmark *targets themselves have been corrected twice* — P18 Revision 2
   changed both the magnitude and the location of LE10's target stress. E7's suite is right to
   exist and is citing numbers whose revision it does not record. E20 owns this.
3. **The agent literature has moved from "worry" to "measurement", and two of the mitigations
   this plan reaches for are measured as not working**: memory scaffolds degraded long-horizon
   performance in **all ten** models tested, additional orchestration does not consistently help,
   and the strongest models show the *highest* catastrophic-failure rates (up to 19%) because
   they attempt the most ambitious multi-step strategies. What is measured to work is shortening
   the horizon — which is exactly E14's interface contracts, so the plan's instinct was right and
   its reasoning was under-evidenced. E22 owns this.
4. **The regulation moved in 2026 and the dates now land inside this plan's window.** The
   Machinery Regulation applies from January 2027, its annexes list *software ensuring safety
   functions* and *safety components with self-evolving behaviour* (Annex II, the indicative list
   of safety components — not Annex I, as this paragraph first said; corrected 2026-09-14 from the
   Official Journal, see E19 task 2), and the
   EU AI Act's obligations for AI embedded in Annex I products were pushed to **2 August 2028**
   by an Omnibus in force since 27 July 2026 — with the binding technical content for
   AI-in-machinery still unwritten, owed by delegated act to that same date. E19 owns this.

**~28 engineer-months.** It is the cheapest era per unit of commercial consequence in this
document and the easiest one to keep postponing, because nothing in it makes a part.

##### Phase E19 — Conformity: the Machinery Regulation, the AI Act, and what a signature means in law #####

> ✅ PHASE COMPLETE (2026-09-14) — all six tasks done and tested. Every date and clause is now
> read from the Official Journal (Regulation (EU) 2023/1230, its corrigendum, and Regulation
> (EU) 2026/1744), and the reading corrected the plan in four places, each recorded in its task.
> **The phase proof is still owed**: nobody who has taken a machine through CE marking has read
> the register, the boundary or the technical-file export, and until someone does, all of it is
> Kryova's reading of the law rather than advice anybody can rely on.

**~5 engineer-months. Needs a compliance-literate engineer for tasks 2 and 6; the rest is ours.**

**The question it answers:** when a customer takes a machine Kryova designed through CE marking,
what does Kryova have to hand them — and what must it never have claimed?

**Read the Official Journal text, not a summary.** The research that produced this phase returned
**two different application dates** for Regulation (EU) 2023/1230 — 20 January 2027 and
14 January 2027, one source explicitly calling the other "a widely repeated error" — from sources
that were otherwise accurate. That disagreement is why task 1 exists and why no date in this
phase is written as settled until it has been read off the OJ text itself. It is the
documentation-first rule (Part 2) applied to law.

1. **Pin the dates and the repeal from the primary text.** Article by article, from
   [the OJ consolidated text](https://eur-lex.europa.eu/eli/reg/2023/1230/oj/eng): the
   application date, the repeal of Directive 2006/42/EC, and the articles that already applied
   from January 2024 (notified bodies, Articles 26–42). Recorded as a dated register in the repo
   with the clause number beside every claim — the same shape as the validation register, for the
   same reason.
   > DONE (2026-09-14) — **both disputed dates are in the Official Journal, and the register now
   > says exactly how.** The Regulation as published (OJ L 165, 29.6.2023) reads "shall apply from
   > 14 January 2027"; the corrigendum (OJ L 169, 4.7.2023, p. 35) item 10 replaces it with
   > **20 January 2027**, along with thirteen other dates. So the "widely repeated error" is a
   > reading of the uncorrected text — which the ELI link above serves. Every provision in
   > `app/compliance/eu_machinery_regulation.py` is now `PRIMARY_TEXT` with its clause and the
   > words read; a corrected one keeps its published date and names the corrigendum item. The
   > shared construction rules moved to `app/compliance/provisions.py`: no source, no provision;
   > primary text needs an Official Journal source, a clause and a quote; a stated date must appear
   > in its own quote as the OJ spells it; a correction names what it moved, both or neither.
   > **Two corrections to the previous status:** the application date is **Article 54, second
   > paragraph** (the old module cited Article 52), and Article 51(1)'s repeal of 73/361/EEC
   > carries no date of its own, so none is recorded. One oddity kept, not smoothed: corrigendum
   > item 12 moves Article 54(b) three years, not six days. EUR-Lex returns an empty body to
   > curl/WebFetch; a rendered extract of the CELEX HTML works, and each source says so. All nine
   > construction guards verified by breaking them. Tested by:
   > `tests/test_compliance_eu_machinery_regulation.py` (18).

   <!-- superseded 2026-09-14 -->
   > PARTIAL (2026-09-14) — `app/compliance/eu_machinery_regulation.py` records the five dated
   > facts this task names (OJ publication 2023-06-29, entry into force 2023-07-19, Articles
   > 26-42 from 2024-01-20, the Article 51 repeal and Article 52 application date both
   > 2027-01-20) as a `DatedProvision` table modelled on `nafems.SOURCES` — every entry refuses
   > construction with no source, and a `LegalBasis` enum distinguishes `PRIMARY_TEXT` from
   > `SECONDARY_CORROBORATED`. **It is PARTIAL, not DONE, because the task's own standard —
   > read off the OJ text itself — was not met.** `WebFetch` was tried against eur-lex.europa.eu
   > in four URL forms this session and every one returned empty content (documented in the
   > module docstring); the dates instead rest on two independent secondary sources (EU-OSHA,
   > a Rockwell Automation PDF read page-by-page) plus a web-search corroboration for the
   > article numbers, all marked `SECONDARY_CORROBORATED` and pinned as such by a test that
   > fails the day anything here claims otherwise. What this session did settle, to the
   > standard the phase header sets: **20 January 2027, not 14** — every source found agrees,
   > and 14 January appears nowhere in the module. Remaining work: read the actual OJ
   > consolidated text (needs network access this sandbox does not have, or a human pasting the
   > text in) and upgrade the five entries to `PRIMARY_TEXT` with article/paragraph citations.
   > Tested by: `tests/test_compliance_eu_machinery_regulation.py` (9).

2. **Decide in writing whether anything Kryova produces is an Annex I item.** *(Corrected
   2026-09-14 against the Official Journal: the two items below are **Annex II** — the indicative
   list of safety components — items 18 and 19, not Annex I. Annex I Part A carries the
   self-evolving machine-learning safety components at items 5 and 6; Annex I Part B's 18 and 19
   are ROPS and FOPS; recital (55) limits third-party assessment of safety software to
   self-evolving systems.)* The list as this task first gave it: item 18
   **"software ensuring safety functions"** and item 19 **"safety components with fully or
   partially self-evolving behaviour"**. Kryova's own position is almost certainly *neither*: it
   is a design tool, and the machine it helps design is the regulated product. **But "almost
   certainly" is not a position**, and Decision 5's honest-scope statement is the natural home
   for the answer. The output of this task is a written boundary — what Kryova may generate, what
   it must refuse to generate unattended, and the sentence a salesperson is allowed to say.
   > DONE (2026-09-14) — **decided: Kryova is neither an Annex I category nor an Annex II safety
   > component**, because it is design software a manufacturer uses and nothing it produces runs
   > on the machine — and no code path in the repository emits controller logic, PLC code or
   > firmware, which a reader can check. `app/compliance/boundary.py` writes the position down
   > with the clauses quoted, states what it rests on (the day a feature emits logic that runs on
   > a machine, the position is re-decided before that feature ships), and sorts outputs three
   > ways: **may generate** (drawings, explanations, design calculations — Annex IV A(c), (d),
   > (g)); **draft for a named person only** (risk assessment, instructions for use — A(b), (i));
   > **never** (safety-function software or logic, an EU declaration of conformity, a CE marking,
   > a statement that a machine complies — Annex II 18, Annex I A5/A6 and B17, Article 10(2)).
   > `SALES_SENTENCE` is the one sentence a salesperson may say. It is held mechanically:
   > `FORBIDDEN_CLAIMS` is scanned over every string constant in `app/` outside
   > `app/compliance/` (35,219 strings, zero hits), no tool the agent is offered may be named for
   > a never-output, and a new public commitment,
   > `the-manufacturer-makes-the-conformity-claims`, names the test. What it cannot hold: the
   > model's free text. A chat answer is not scanned, and the commitment is worded so it does not
   > claim otherwise. The frontend carries no such copy today (grepped), and nothing enforces that
   > there. Five guards verified by breaking them. Tested by: `tests/test_compliance_boundary.py`
   > (27).

   <!-- superseded 2026-09-14 -->
   > NOT STARTED.

3. **The technical file is an export format, because the law can demand our output.** Verified
   against the OJ text: the manufacturer keeps the technical documentation and the EU declaration
   of conformity available to market surveillance authorities for **at least 10 years**, and
   *"where relevant, the source code or the programming logic included in the technical
   documentation shall, upon a reasoned request, be made available to the competent national
   authorities"* where needed to check the Annex III essential health and safety requirements.
   A machine designed here therefore has a plausible path to a national authority reading a
   `DesignSpec`. The consequence is a deliverable, not a worry: the design record must export as
   a coherent technical file — geometry, provenance, analyses with their convergence basis,
   requirement coverage, and the plan calls that produced them — and it must still open in ten
   years, which is an argument about formats (E21) and about storage (E15).
   > DONE (2026-09-14) — `GET /designs/{conversation_id}/technical-file` downloads Kryova's
   > **contribution** to the technical file, organised by **Annex IV, Part A, points (a) to (o)**
   > — all fifteen read from the Official Journal and quoted in the file. **No point is claimed in
   > full**: Kryova contributes to (a), (c), (d) and (g) and says, point by point, what only the
   > manufacturer can supply; the other eleven are listed as the manufacturer's rather than left
   > out, and (m) — safety software source code — is answered: Kryova produces none. Six
   > artefacts from the record: the head spec, the build plan compiled from it, the revision
   > history, the operation log (failures included), the analyses run in the design's project
   > (each naming its geometry and carrying `NOT_VALIDATED`), and the approval gates. UTF-8 JSON
   > with a format version; every artefact carries the SHA-256 of its canonical JSON and the file
   > a digest over the rest, checked by `technical_file.verify` — integrity, not a signature.
   > **Three things it does not do, and the file lists them:** drawings and STEP are generated
   > by a build and not stored, so they are absent; requirement coverage is not stored, so it is
   > absent; and nothing keeps the export for Article 10(3)'s ten years — the format half is done,
   > the storage half (E15) is not. A simulation records no link to a design revision, so the
   > file says the analyses are the project's and the link is not recorded. The analyses are
   > withheld when the exporter cannot read the project (found while writing it: a conversation
   > can name a project its owner has since lost access to). The spec panel has an export button.
   > Seven guards verified by breaking them. Tested by: `tests/test_compliance_technical_file.py`
   > (18), `tests/test_designs.py::TestTheTechnicalFile` (6),
   > `Kryova-frontend/src/components/design/spec-panel.test.tsx` (2).

   <!-- superseded 2026-09-14 -->
   > NOT STARTED.

4. **Digital instructions, to the conditions the regulation actually sets.** Instructions for use
   may be supplied digitally — but they must be printable, downloadable and storable on a device,
   kept accessible online for the expected lifetime of the machine and **at least 10 years** after
   it is placed on the market, and supplied on **paper on request at no extra cost**. That is a
   hard constraint on E17 task 6's technical documentation, and it is cheaper to build it in than
   to discover it during a customer's audit. *(Corrected 2026-09-14 against Article 10(7): paper
   is due "at the request of the user at the time of the purchase … free of charge within one
   month", not on request at any time.)*
   > DONE (2026-09-14) — `app/compliance/instructions.py` checks a manufacturer's digital
   > delivery plan against **Article 10(7)**, quoted clause by clause, and `unmet` returns each
   > failure with the words it fails. The reading found two conditions this task left out: the
   > machine (or, only where that is not possible, its packaging or a document) must be **marked
   > with how to reach the instructions**, and the instructions must **clearly describe the model**
   > they belong to; and the print/download/save condition applies to a copy **embedded in the
   > machine's software**. Online is owed until the later of the expected end of life and ten years
   > after placing (`required_online_until`); ten years from 29 February is taken as 1 March, and
   > one month as 28 days — both choices stated as choices, not as rules read. **Nothing generates
   > instructions yet**, so the checker has no caller; E17 task 6's text now names it as the gate.
   > It checks a declared plan and cannot see whether a URL is online in 2037. Ten guards verified
   > by breaking them. Tested by: `tests/test_compliance_instructions.py` (21).

   <!-- superseded 2026-09-14 -->
   > NOT STARTED.

5. **"Substantial modification" is a trap this product walks into by design.** Article 3(16)
   defines it to include modification **"by physical or digital means"** after placing on the
   market, not foreseen by the manufacturer, that creates a new hazard or increases an existing
   risk — and whoever performs it becomes the manufacturer of the modified machine. Kryova's whole
   value proposition is regenerating a machine from a changed requirement. The deliverable is the
   distinction, in the product: a *design-time* regeneration of a machine not yet placed on the
   market, versus a change to one already in service — which is a different legal act and must
   read differently on screen.
   > DONE (2026-09-14) — **schema and API change:** `design_documents` gains
   > `placed_on_market_on` and `placed_on_market_recorded_by_id` (migration `b5d0e3dae224`,
   > nullable, no backfill), recorded by `PUT /designs/{conversation_id}/placed-on-market` (a
   > future date is 422; there is no clear). `DesignRead` gains `placed_on_market_on` and a
   > computed `modification` notice; `RevisionRead` gains `after_placing_on_market`. From the
   > recorded day — **the same day counts as after** — the notice, the panel and the agent's
   > `set_design_parameter` result all say the change is not design-time work. **The reading
   > sharpened the distinction the task drew:** after placing there are two acts, not one — the
   > manufacturer changing its own design for machines still to be produced (Article 10(4)'s
   > series-production duty) and a change to a machine in service, which is a substantial
   > modification under Article 3(16) if unforeseen and hazard-creating, making whoever does it
   > the manufacturer (Article 18). Kryova cannot tell them apart and does not assess hazards, and
   > the notice says both. Article 3(16) is quoted to the end of point (b); the rest was not
   > captured, and Article 10(4)'s article is placed from the numbered paragraphs around it —
   > both stated in the module. `test_the_api_has_no_way_to_post_a_whole_spec` now allows exactly
   > this one PUT and pins its body to one date field. Seven backend and two frontend guards
   > verified by breaking them. Tested by: `tests/test_compliance_modification.py` (10),
   > `tests/test_designs.py::TestPlacingOnTheMarket` (9),
   > `Kryova-frontend/src/components/design/spec-panel.test.tsx` (5).

   <!-- superseded 2026-09-14 -->
   > NOT STARTED.

6. **The AI Act register, kept current because it is still moving.** As of the research date the
   position is: the **AI Omnibus entered into force 27 July 2026**, extending timelines;
   high-risk AI **embedded in Annex I products (machinery explicitly named) applies from
   2 August 2028**; standalone Annex III high-risk applies from **2 December 2027**; the
   machinery-specific technical requirements are to be written into the Machinery Regulation by
   **delegated act, deadline 2 August 2028**, and *are unwritten now*; in the interim a
   manufacturer may rely on harmonised standards or common specifications under the AI Act for
   presumption of conformity; and a manufacturer performs **one** conformity assessment under the
   Machinery Regulation rather than two. **The narrowing matters as much as the dates**: AI used
   solely for user assistance, performance optimisation, efficiency, automation, convenience or
   quality control is not high-risk merely by being embedded. Two cautions ride with this: one
   claim in the set (the 2028 date) survived adversarial verification only **2–1**, and the
   Omnibus was a provisional political agreement before it was law, so a version of this
   paragraph was true and stale within weeks. Hence a *register with dates and sources*, re-read
   at every legislative change, rather than a paragraph in a plan.
   > DONE (2026-09-14) — **the agreement is now law and the dates are read from it**: Regulation
   > (EU) 2026/1744 (Digital Omnibus on AI), OJ L, 2026/1744, 24.7.2026, in force 27 July 2026
   > (its Article 4, third day). `app/compliance/eu_ai_act.py` quotes: AI Act Article 113 as
   > replaced by Article 1(40) — Annex III high-risk from **2 December 2027**, Annex I from
   > **2 August 2028**, Articles 102–110 from 27 July 2026, Article 5(1)(ba)/(bb) from 2 December
   > 2026; and the Machinery Regulation's new Article 8 paragraphs (Article 3(1)) — the Commission
   > **shall** adopt delegated acts adding AI requirements to Annex III, and "those delegated acts
   > shall apply by 2 August 2028", **in the operative text, not only a recital** (a first draft
   > of the module said otherwise; a second reading caught it before commit). The delegation runs
   > five years from 27 July 2026 (Article 47(2) as replaced by Article 3(3)(a)). **The narrowing
   > is quoted whole**: Article 6(1a) excludes systems "solely used for non-safety related
   > aspects", and **Article 6(1b) — which this task left out — keeps any system whose failure
   > "would endanger health and safety" a safety component.** The harmonised-standards interim is
   > a recital's words and is labelled so. **Not found in what was read:** that a manufacturer
   > performs one conformity assessment rather than two; it sits in `NOT_FOUND`, never in a
   > provision. **The register expires:** `REVIEW_BY` is 2027-03-14, and
   > `test_the_register_has_been_read_recently_enough_to_be_believed` goes red the day after, on
   > purpose — the fix is to re-read EUR-Lex, not to move the date. Recital numbers were not
   > captured and are not guessed. Three expiry guards verified by breaking them. Tested by:
   > `tests/test_compliance_eu_ai_act.py` (15).

   <!-- superseded 2026-09-14 -->
   > NOT STARTED.

**Phase proof:** a package from the mission ladder (M2 upward) is reviewed by someone who has
taken real machinery through CE marking, against the register this phase produces, and their
findings are recorded — including the ones we cannot fix. A compliance claim nobody outside this
repository has read is the same class of evidence as a test suite nobody has run.

**Risks:** writing law into code and having it change (mitigated by the register being data, not
prose); over-claiming compliance, which is worse than claiming none; and the honest risk that a
proper answer to task 2 narrows what the product may say about itself.

##### Phase E20 — Simulation credibility as somebody else's standard #####

> ✅ PHASE COMPLETE (2026-09-14) — all five tasks done and tested. What the phase proof still
> needs is a reader outside the repository: every word is now traceable to a document, and
> nobody who owns one has checked the tracing. Two residuals are named in task 4 — the nightly
> corpus jobs have not yet run on GitHub, and the Windows seat's CalculiX 2.23 is not covered.

**~5 engineer-months. Needs an ME for task 3.** *(It did not, in the end: ASME's published
definitions decided task 3's one judgement call — see its status.)*

**The question it answers:** when Kryova says an analysis is *verified*, does that word mean what
it means to the people who define it — and can we name the document?

1. **Adopt ASME's vocabulary exactly, including where it excludes us.** The solid-mechanics
   portfolio is **V&V 10-2019** (*Standard for Verification and Validation in Computational Solid
   Mechanics*), **V&V 10.1-2012** (the worked illustration) and **VVUQ 10.2-2021** (the role of
   uncertainty quantification); the fluids/heat base is **V&V 20-2009** — over fifteen years old —
   extended by **VVUQ 20.1-2024**; terminology is **VVUQ 1-2022**. Two corrections to a natural
   assumption ride with this: **V&V 40-2018 is scoped to medical devices** and citing it as our
   risk-informed credibility framework would be borrowing another industry's standard, and the
   ASME subcommittee on machine learning, **VVUQ 70, has no published standard** — so there is no
   ASME-normative basis for validating an ML surrogate, which bounds what E10 task 4 may ever
   claim. Deliverable: the trust surface (P10 task 3) names the document behind every word it
   uses.
   > DONE (2026-09-14) — **and it corrected a live mis-citation rather than only adding new
   > ones.** `app.verify.__init__`, `benchmarks.py` and `register.py` had all said "the ASME V&V
   > 20 split" for the general verification/validation distinction since the package was
   > written. That is imprecise in the direction that matters once a reader can click through:
   > **V&V 20-2009 is scoped to fluids and heat transfer**, and every benchmark this codebase
   > actually runs — LE1, LE3, LE10, LE11, FV52 — is solid mechanics, V&V 10-2019's territory.
   > The document that defines the *terms* "verification"/"validation"/"UQ" themselves,
   > discipline-unspecified, is **VVUQ 1-2022**, and that is now what the split is credited to.
   > `app/verify/standards.py` is the one place this is said, so the three modules cite it rather
   > than each carrying a slightly different paraphrase — the same argument `nafems.SOURCES`
   > makes for a citation repeated inline. It also records why V&V 20-2009 stays cited elsewhere:
   > `app.verify.convergence`'s Grid Convergence Index procedure genuinely is Celik et al. 2008 /
   > V&V 20's own method, borrowed as a numerical-error estimator regardless of which physics
   > produced the mesh — removing that citation would have overcorrected.
   > `NOT_OURS["vv-40"]` and `NO_STANDARD["vvuq-70"]` record the two documents by name, with the
   > reason neither may be reached for — VVUQ 70 is the ceiling `E10 task 4`'s own text already
   > pointed at before this task existed to name it.
   > Tested by: `tests/test_verify_standards.py` (10) — including that no live module in
   > `app/verify/` still carries the uncorrected phrase, checked against the source text rather
   > than trusted from memory. Code: `app/verify/standards.py`, `app/verify/__init__.py`,
   > `app/verify/benchmarks.py`, `app/verify/register.py`.

2. **E7's NAFEMS targets must carry the revision they came from.** P18 — *The Standard NAFEMS
   Benchmarks* — has been corrected at least twice: **Revision 2** corrected LE8, LE10, LE11 and
   T3, with LE10's target changing in **both magnitude and location**, and **Revision 3**
   corrected LE4, LE7, LE9 and LE10. Our targets are reproduced from vendor verification manuals
   (E7's deliberate, legitimate route, since P18 itself is £45 and not openly licensed), and a
   vendor manual reproduces whichever revision it was written against. So each case records the
   manual, its date, **and the P18 revision that manual reproduces** — and a case that cannot
   establish the third says so rather than implying it.
   > DONE (2026-09-14) — **derived from the existing citation text, not added as a second
   > field.** Every `Target.source` in `nafems.SOURCES` already stated its revision in prose
   > ("reproducing NAFEMS publication TNSB Rev. 3, ...") because that is where a reviewer reads
   > it from; a hand-typed `revision="3"` beside it would be a second place for the two to
   > disagree the day one is edited and the other is not. `p18_revision`/`p18_revision_of`
   > extract it with a regex instead, scoped to the citation actually compared against (a case's
   > `Target.source`) rather than every string in `SOURCES` — several entries there
   > (`feenox-le1-geometry`, `esrd-le11-geometry`, ...) are geometry cross-checks that mention
   > NAFEMS by name without reproducing the TNSB publication's own revision, and are not what a
   > published number is checked against.
   > **The finding: every target source in the catalogue reproduces Revision 3**, which is the
   > later, more-corrected revision (LE10, the case where Revision 2 and Revision 3 disagree, is
   > among them) — so nothing here is silently comparing against a superseded number. Checked
   > against the value rather than merely for presence, so a future source crediting a different
   > revision is a change the test asks about rather than one that passes quietly.
   > Tested by: `tests/test_verify_nafems.py::TestEveryTargetNamesWhichP18RevisionItReproduces`
   > (4). Code: `app/verify/nafems.py`.

3. **Say "verification" and "validation" the way ASME does, everywhere the product speaks.**
   Verification asks whether the computational model fits the mathematical description;
   validation asks whether it represents the real-world application; UQ asks how parameter
   variation moves the answer. Kryova has closed-form benchmarks and no experimental data, so
   **everything it has ever done is verification**, and the validation register's own name is a
   claim it must not make on its own behalf. Part 4 already lists physical testing among the
   things no amount of code fixes; this task makes the product say so at the point a user reads a
   number.
   > DONE (2026-09-14) — **the one judgement in the task was settled by ASME's words rather than
   > an engineer's opinion, which is why it needed no ME.** `app/verify/standards.py` now quotes
   > *verification*, *code verification*, *solution verification* and *validation* as V&V 10 and
   > V&V 20 define them — read second-hand from ASME's public VVUQ page and Sandia SAND2016-5342C
   > (a V&V 20 committee member's slides), and saying so, because the standards themselves are
   > sold and were not read. On those definitions a NAFEMS target and a closed-form identity are
   > both answers to a *mathematical* problem, so agreeing with either is **code verification**; a
   > GCI study is **solution verification**; validation compares against *experimental data*, and
   > Kryova has none. `KRYOVA_EVIDENCE` files the evidence that way with validation **empty**,
   > `NOT_VALIDATED` is the one sentence, and importing the module **fails** the day validation
   > evidence is recorded without that sentence being rewritten.
   > **Public API, enum and artefact changes — every one a rename the correction required:**
   > `/trust/validation-register` → `/trust/verification-register` (the old path is a 404, not a
   > redirect: a URL reading "validation" that still resolved would keep the claim alive);
   > `Outcome.VALIDATED="validated"` → `AGREED="agreed"`; `Standing` `validated`/`unvalidated` →
   > `agrees`/`unconfirmed`; register JSON `analyses_validated`/`not_validated` →
   > `analyses_agreeing`/`unconfirmed`, plus a `validation` block and an `evidence` label on every
   > benchmark (`code-verification`) and convergence (`solution-verification`) entry; trust index
   > `validation_headline`/`everything_validated` → `verification_headline`/
   > `every_analysis_agrees_with_a_benchmark`, plus `validation`; the artefact
   > `data/verify/validation-outcomes.json` → `benchmark-outcomes.json` (`SCHEMA_VERSION` 2,
   > re-recorded: 3/5 agreed, fingerprint current); commitment `what-does-not-agree-is-published`;
   > guide `check-what-is-verified`, which now lists validation under `not_covered`.
   > **The sentence reaches every surface that shows a number, as the server's string:** the trust
   > index and register; a computed `validation` field on `SimulationRead`, `SharedPackage` and
   > `ResultInterpretation` — computed, so no client can send a softer one, and absent from the
   > validation-mode schema the providers constrain decoding with, so the model is never asked to
   > write it; and `not_validated_footnote`, appended by the loop on both exits that put numbers on
   > screen whenever the turn read a solve. **Converged runs get it too** — `unconverged_footnote`
   > states missing solution verification, which a study supplies; this states missing
   > validation, which nothing in the product can. Frontend: the verification panel, the result
   > interpretation card (beside "Passes", above the caveat) and the shared package page (above the
   > results table), each in amber.
   > Every guard was broken and watched to fail, then restored by `cp` and compared: both agent
   > exits, the computed field, the import refusal, the headline clause and a published key
   > renamed back to `unvalidated` (backend); the panel line, the interpretation line and the
   > shared-page line (frontend).
   > Tested by: `tests/test_verify_standards.py` (10 → 24), `tests/test_verify_register.py` (+6),
   > `tests/test_trust.py` (+2, including a walk of every trust page for "validated" outside the
   > statement), `tests/test_unconverged_verdict.py` (+7), `tests/test_agent_verification.py` (+4,
   > through `run_agent`), `tests/test_verify_benchmarks.py`, `tests/test_verify_nafems.py`,
   > `tests/test_verify_recorded.py`; frontend `src/components/verification/verification-panel.test.tsx`
   > (+3), `src/components/result-interpretation.test.tsx` (1),
   > `src/app/shared/[token]/page.test.tsx` (2). Code: `app/verify/standards.py`,
   > `app/verify/{benchmarks,nafems,convergence,register,recorded,commitments,__init__}.py`,
   > `app/api/routes/trust.py`, `app/schemas/{simulation,sharing}.py`, `app/ai/{schemas,verification,agent}.py`,
   > `app/handbook/{guides,gallery}.py`; frontend `src/types/api.ts` and the three components above.

   <!-- superseded 2026-09-14 -->
   > NOT STARTED.

4. **Inherit the solvers' own corpora, and be precise about what they prove.** Two findings, both
   verified: CalculiX's manual frames its **528 examples** as feature smoke-tests and installation
   checks — *"suitable to test distinct features … to check whether the installation of CalculiX
   is correct"* — with no reference solutions, tolerances, accuracy criteria or NAFEMS cases
   anywhere in the list; and code_aster ships a public validation manual of **1,262 documented
   cases** organised V0–V9 (V3 being linear statics, the family Kryova lives in) under its own
   naming scheme, so mapping them onto NAFEMS is our work, and the widely mirrored copies are
   version-stale. Deliverable: both corpora run in CI against our pinned solver builds as a
   third-party regression, with a written statement of what a green run does and does not
   establish. The assumption this kills is *"we ship CalculiX, so the solver is verified"* — the
   solver's own documentation refuses that reading.
   > DONE (2026-09-14) — **both corpora run against pinned builds with recorded baselines, and
   > both "verified findings" in this task's text turned out wrong when the corpora were opened.**
   > **The task text, corrected:** CalculiX 2.20's example archive holds **559 decks, not 528,
   > and it does carry references and a tolerance** — a `.dat.ref`/`.frd.ref` beside most decks,
   > and `compare` + `datcheck.pl`/`frdcheck.pl` flagging any number more than 0.1 % of its
   > block's largest value from the reference. What the manual's framing gets right is the
   > conclusion, for a different reason: **every reference is CalculiX's own earlier output**, so
   > a pass is regression and installation evidence and never code verification. And code_aster
   > 18.0.12 installs **4,540 test cases, every one labelled `verification`** in its `.export`,
   > not the 1,262-case manual figure; each check names where its reference came from, so the
   > honest unit is the *check by reference kind*, not the passed case.
   > **What shipped.** `app/verify/corpora.py` (standard library only, because the CalculiX job
   > runs under `debian:bookworm-slim`'s own Python 3.11) runs each corpus with **the
   > maintainer's own harness** — `compare`'s per-release rules parsed out of the archive being
   > run, the Perl checkers invoked as shipped; code_aster through its own `run_ctest` — refuses
   > a binary that is not the pinned build, and writes a report whose "establishes / does not
   > establish" statement is **derived from the run's own counts**, never typed.
   > **Baselines** (`data/verify/corpora/`): **calculix-2.20** on `calculix-ccx 2.20-1` (the
   > fleet image's build) — 543 reproduced, 6 deviated, 1 no output, 1 no reference, 4 line-count
   > mismatches, 4 skipped by `compare` itself; the unmodified `compare` on the same build flags
   > **the same twelve cases**, five of them restart decks that fail by construction.
   > **code_aster-18.0.12** on conda-forge `py312_nompi_h60fb801_0`, the V3 `ssl` family's 664
   > sequential cases — **661 reproduced**; 15,378/15,378 `ANALYTIQUE` checks agreed (code
   > verification *of code_aster*, for the models those cases pose), 19,053 non-regression, 881
   > same-solver, 768 `SOURCE_EXTERNE` left unclassified, 92 Python assertions counted as
   > `unstated`. The three that do not reproduce are findings, kept in the file: `ssll501a`/`b`
   > stop with `<F>_ERROR` because **the conda-forge package ships no `share/aster/tests_data/`**
   > (a packaging gap, not a solver result), and `ssls131a`'s only check sits behind a branch that
   > did not run.
   > **Nightly** (`.github/workflows/nightly.yml`): one job per corpus, each failing when a case
   > the baseline reproduced does not. code_aster is created from an **explicit conda lock**
   > (`code_aster-18.0.12.explicit.txt`, all 74 packages hashed) with micromamba fetched by
   > version and checked by SHA-256, because a `code-aster=<build>` spec re-solves nightly and
   > lets openblas and mumps move underneath an unchanged pin.
   > **Five defects found while building it, three of them only by running it**, each now pinned:
   > `run_ctest` meets an existing results directory with a y/n prompt and, with no terminal,
   > came back as **664 cases with no output and exit 0** (refused, stdin closed, a run with no
   > `.mess` raises); its JUnit file records a failed case's time as limit × 1.1 (`ssll501a`:
   > 66.0 against 1.65 s actually — times now come from ctest's own lines); `TestCase` assertions
   > print no `REFERENCE` and were missed (`sslp306a` read as holding no check); `aster_version`
   > first read a file with no version in it; and a `--only` run compared with a whole baseline
   > would report every omitted case as a regression (refused).
   > **Decisions, stated:** only the V3 `ssl` family's *sequential* cases run (the seven parallel
   > ones need MPI, which the nompi build lacks; the statement says so); **time limits are scaled
   > ×4**, recorded in the report, and a comparison across factors is refused before the run —
   > a time limit is not a check of an answer, one reproduced case already used 0.76 of its own
   > limit on the recording machine, and at ×1 `sslv154b` stopped with `<S>_CPU_LIMIT` at 104 s
   > of 110. **Only the fleet's CalculiX is covered**: the Windows seat's 2.23 is a different
   > build and nothing here speaks for it.
   > **Not yet run on GitHub** — committed, not pushed. Both jobs' steps were reproduced
   > locally: the CalculiX job in `debian:bookworm-slim` with the exact apt pin, twice (the
   > second time as the nightly command with `--baseline`, exit 0); the code_aster environment
   > created from the lock with the pinned micromamba. 29 guards were broken and watched to
   > fail, each restored by `cp` and checked by hash.
   > **And the full suite found one thing no corpus test could:** the reference-manual scan
   > walks `data/` whole, so the lock — a `.txt` — became a 26th "manual" offered to the agent
   > and the index read as stale. `settings.knowledge_exclude` now keeps `data/verify/` out.
   > Tested by: `tests/test_verify_corpora.py` (80),
   > `tests/test_retrieval.py::TestCorpus::test_verification_artefacts_are_not_offered_as_manuals`.
   > Code: `app/verify/corpora.py`,
   > `data/verify/corpora/{calculix-2.20.json,code_aster-18.0.12.json,code_aster-18.0.12.explicit.txt}`,
   > `.github/workflows/nightly.yml`, `app/verify/__init__.py`, `app/core/config.py`,
   > `app/retrieval/service.py`.

   <!-- superseded 2026-09-14 -->
   > NOT STARTED.

5. **Solution verification, which is the open half of G1.** Numerical error is what verification
   bounds, and the accepted instrument is mesh refinement toward convergence — but NAFEMS' own
   simulation-governance working group states that actionable published guidance on error
   estimators is thin *even inside the ASME and NAFEMS corpus*, and that refinement is
   cost-constrained on real problems, which is exactly what `MAX_ELEMENTS` encodes. E7's
   convergence machinery exists and is validated; nothing in the request path runs a study. Until
   it does, **every stress this product reports comes from one mesh** and must be labelled as
   such — the third open item from the 2026-09-08 gate report, restated here because it is a
   credibility obligation and not only a feature.
   > DONE (2026-09-14) — **already built, under E7 task 7, and this task is satisfied by that
   > work rather than needing a second implementation.** `verification.unconverged_footnote`
   > (`app/ai/verification.py`) walks every tool result the turn actually read and appends what
   > the numbers rest on whenever a solve carries `mesh_convergence: {converged: false, ...}` —
   > server-appended rather than a prompt rule, because the seat run that motivated it
   > (`docs/verification-2026-09-10-night/`) showed the model *had* the block in front of it and
   > wrote a pass/fail verdict anyway. It is silent only when a study ran and converged, matching
   > Decision 3: an unconverged number is worse than no number, not "every number needs a
   > disclaimer". Seat-verified before and after, DONE 2026-09-11 under E7 task 7 — see that
   > entry for the full account, including the default-mesh defect the same seat run found beside
   > it. Nothing new was written for this task; it is recorded here so E20's own denominator does
   > not show an open item the plan already closed under a different phase.
   > Tested by: `tests/test_unconverged_verdict.py` (see E7 task 7). Code:
   > `app/ai/verification.py`, `app/ai/agent.py`.

**Phase proof:** a results page states a number, and every word around it — *verified*, *not
validated*, *converged to X% on N refinements*, *against P18 Rev. 3 via the Ansys manual of
&lt;date&gt;* — is traceable to a document a reader can buy or open. Nothing in the sentence is a
word we coined.

##### Phase E21 — Data and interchange under licence #####

**~7 engineer-months. Mostly integration; the licence work is not optional and does not compress.**

**The question it answers:** can a machine designed here leave the building — as data a
manufacturer's system reads, with material allowables somebody is allowed to use — without either
lying about fidelity or breaching somebody's licence?

1. **AP242 as it actually is in our kernel, not as the register describes it.** OCCT's writer
   exposes AP242 only as the **draft** schema (`AP242DIS`) while exposing a published-IS option
   for AP214; its reader is documented as covering *"some parts of AP242"*; the base
   `STEPControl` translator carries geometry, topology and assembly structure only, with colours,
   names, layers, validation properties and GD&T requiring **XDE** (`STEPCAFControl`); and the
   OCCT user guide names GD&T in a single enumerating sentence with no specification of semantic
   tolerance, datum, dimension or saved-view round-trip. FreeCAD's tracker records the same gap
   from the same layer (`3rd party: OCC`), sized at 350 hours. Deliverable: **a measured
   round-trip matrix** — what survives a Kryova→STEP→Kryova and Kryova→STEP→CATIA trip, by
   entity class — published as a capability statement. Where PMI does not survive, E17 task 1's
   drawings remain the carrier of tolerance, and the plan says so instead of implying MBD.
   > PARTIAL (2026-09-10), superseding `PARTIAL (2026-09-09)` — **the XDE half is now measured,
   > the lead that stood open resolves as our own authoring, and the trip has a defect in it
   > that neither `CARRIED` nor `LOST` could express.** What remains open is the
   > cross-implementation half, which needs the seat.
   >
   > **The matrix exists and is measured, and the AP242 finding is now
   > this build's own rather than a citation.** `app/manufacture/interop.py` performs the trip
   > through the shipped export path and reports per entity class, with `NOT_ATTEMPTED` kept
   > distinct from `LOST` — "we wrote it and it vanished" is a defect in OCCT, "we never wrote
   > it" is scope, and collapsing the two files a bug report against somebody else's code for
   > work we have not done.
   >
   > **Measured on OCP 7.9.3.1 / OCCT 7.9.3, and re-measured by the test rather than recorded:**
   > `write.step.schema` accepts `AP242DIS` and **refuses `AP242IS` and `AP242`**, while
   > accepting the published-IS spelling `AP214IS` for AP214. The asymmetry is the evidence: OCCT
   > knows how to name a published IS and does not do it for AP242. So no sentence in this
   > product may claim an AP242 **IS** edition (Ed.1 2014, Ed.2 2020, Ed.3 2022,
   > ISO 10303-242:2025), and `test_this_build_cannot_write_a_published_ap242_edition` **fails on
   > purpose** the day a future OCCT gains the spelling — which is how the claim gets widened
   > deliberately instead of staying narrow for years after it needed to.
   > The header of a file written that way says three things that do not agree: `FILE_SCHEMA`
   > names `…MIM_LF {1 0 10303 442 1 1 4 }`, `APPLICATION_PROTOCOL_DEFINITION` says
   > *'international standard'*, and the year beside it is **2013** — before AP242's first IS
   > publication. The module records the strings and adjudicates nothing, because the only body
   > that can settle what the file *is* is the system reading it.
   >
   > **The XDE half is measured now, and the lead recorded on 2026-09-09 was ours.** The
   > "flatness tolerance produced no `GEOMETRIC_TOLERANCE` entity" was two mistakes stacked:
   > the tolerance had been bound to a label the shape tool does not know (OCCT drops it and
   > returns success), and **AP242 writes the concrete subtype `FLATNESS_TOLERANCE` — the
   > literal string `GEOMETRIC_TOLERANCE` appears in a correct file zero times**, so the probe
   > was grepping for a string that is absent on success. Bound to the part label, the tolerance
   > is written. `app/manufacture/xde.py` is that path — names, colours, layers, validation
   > properties, assembly occurrences and a geometric tolerance through `STEPCAFControl_Writer`
   > — and `measure_metadata_round_trip` is its matrix. **Nothing in the product exports through
   > it**; wiring it in is E17's, and the row below is why that has not happened.
   >
   > **Six classes moved from "nobody tried" to a measurement.** Part names, assembly
   > occurrences (`instance.1`/`instance.2` back with their names, 2
   > `NEXT_ASSEMBLY_USAGE_OCCURRENCE`), colours, layers and validation properties (volume, area
   > and centroid exact) all **carry**. Three traps found on the way, each of which returns a
   > wrong answer rather than an error: a colour written as `ColorGen` **comes back as
   > `ColorSurf`+`ColorCurv`** and a caller asking for the type it wrote is told there is no
   > colour; `GetLayers(shapeLabel, seq)` returns **`True` with an empty sequence** and the
   > assignment is reachable only through `GetShapesOfLayer_s`; and `SetPropsMode(True)`
   > **computes nothing** — it is a permission to transfer `XCAFDoc_Volume`/`Area`/`Centroid`
   > attributes that must already be on the document, and with the mode on and the attributes
   > absent the file gets no `PROPERTY_DEFINITION` and nothing says so.
   >
   > **Semantic PMI is neither carried nor lost, and the vocabulary gained a third verdict for
   > it.** A flatness tolerance authored at **0.05 mm reads back as 50.0 mm** — ×1000 — because
   > the writer emits the magnitude unchanged under `SI_UNIT($,.METRE.)` while the model's own
   > length unit is `SI_UNIT(.MILLI.,.METRE.)`. **A value written and read by one build, through
   > that build's own writer and reader, does not survive its own round trip**, which is what
   > makes this a defect rather than a convention we misread. `Carriage.CORRUPTED` exists
   > because `LOST` would have been a lie in the safe direction: an absent tolerance gets
   > queried by the receiving system, and one that arrives a thousand times too loose gets
   > manufactured to. **Nothing compensates for it** — scaling by 1/1000 on the way out would
   > put a number in the file that no part of this codebase believes and would hide it from the
   > system that needs to know (the units rule, and the `/1000` the results page once shipped).
   > The consequence for the product: **semantic PMI may not be claimed**, the drawing stays the
   > carrier of tolerance as this task's own text says, and `write_step_with_metadata` **refuses
   > a tolerance under AP214**, where OCCT accepts the request, returns `RetDone` and writes no
   > tolerance at all.
   >
   > **The cross-implementation half — Kryova→STEP→CATIA — needs the seat and is untouched.** A
   > round trip through one implementation is the weakest interoperability evidence there is:
   > both ends share the same bugs, and this trip now has a measured one. THE QUEUE section D
   > carries the row; the ×1000 makes it the highest-value STEP measurement on that list,
   > because whether CATIA reads 0.05 or 50 decides whether the defect is OCCT's writer or its
   > reader.
   > Tested by: `tests/test_manufacture_xde.py` (20 tests) and
   > `tests/test_manufacture_interop.py` (33 tests) — five guards verified by breaking what they
   > guard (the AP214 refusal, the `IsAttribute` guard, the assembly part-label descent, the
   > colour-type sweep and the `CORRUPTED` verdict), 17 failures and one deliberate segfault
   > observed, all three files restored byte-for-byte. Code: `app/manufacture/interop.py`,
   > `app/manufacture/xde.py`.

2. **Pin which AP242 edition anything here means.** The community site lists editions 1 (2014),
   2 (2020) and 3 (2022) with edition 4 in development; the ISO catalogue shows
   **ISO 10303-242:2025** as current, revising the 2022 text — so the community site is stale
   against ISO, and a plan that cites "AP242" without an edition is citing four different
   documents. Ed2/Ed3 are where 3D PMI, additive, harness and composite data live; Ed4 adds 3D
   assembly constraints, visual issue management and bounding-box/LOD, and pulls **AP243
   (MoSSEC)** into its interoperation set — with **AP209** remaining the protocol for analysis
   data. Two consequences: the normative text must be **bought** and cannot be redistributed by
   this repository, and FEA interchange is AP209/AP243 territory rather than something AP242 will
   grow into.
   > DONE (2026-09-15) — **"AP242" here means the first edition, and every STEP this build writes
   > now says so from its own header.** STEP Tools' AP242 notes (read 2026-09-15) list the ASN.1
   > identifiers `{1 0 10303 442 1 1 4}` (first edition), `{… 442 3 1 4}` (second) and
   > `{… 442 4 1 4}` (third). The same page says the schema *name* has not changed between
   > editions, so the identifier is the only thing in a file that can name one. The second
   > edition's arc is 3, not 2, so it is a table and not a formula. OCCT 7.9.3 writes the
   > first-edition identifier (measured). So the build's `AP242DIS` spelling, the header's
   > *'international standard'* with year 2013, and the identifier are three labels that do
   > not agree, as task 1 recorded. The identifier is the one a receiving system matches on.
   >
   > Shipped:
   > - `export.AP242_EDITIONS`, with its source and read date.
   > - `object_identifier_of` and `ap242_edition_of`, which read a `FILE_SCHEMA` string.
   >   An identifier the table does not hold is edition `None`, never the nearest one.
   > - **Interface change:** `StepExport` gains `object_identifier` and `ap242_edition`, and
   >   `to_dict` carries both. They are read back from the written file by
   >   `with_declared_edition`, in `write_step` and in `xde.write_step_with_metadata`.
   > - **Interface change:** the open kernel's `catia_export_step` result gains `step_schema`
   >   and `ap242_edition`.
   >
   > AP214 carries `{1 0 10303 214 1 1 1 1}`. AP203 carries no identifier, and its record says
   > `None`.
   >
   > **What was read about ISO, and what was not.** The ISO catalogue entry for 93277 (search
   > snippet, 2026-09-15) gives its life cycle as "Previously ISO 10303-242:2025 / Now
   > ISO/CD 10303-242". So the 2025 edition is published and a further revision is at committee
   > draft. NIST's 2024 slides (Barnard/Feeney) list the 2014, 2020 and 2022 editions. No
   > identifier for the 2025 edition was read, so a file declaring one reads as edition `None`.
   > The normative text is ISO's and is sold. Its price was not read. The ISO catalogue page
   > carries a notice restricting use of ISO content for AI and machine learning, including
   > prompting AI tools. So nothing from the standard's text is quoted here, and only the
   > catalogue's life-cycle line was read. What Ed.4 adds (AP243, assembly constraints, LOD) is
   > the plan's earlier summary and was not re-read.
   >
   > Tests written on Linux and not run as pytest. The writer's identifiers and the pure
   > functions were checked by a one-off script. Tested by:
   > `tests/test_manufacture_step_edition.py` and `tests/test_geometry_backends.py`
   > (`TestThePartCanReachTheSolver`).

   <!-- superseded 2026-09-15 -->
   > NOT STARTED.

3. **Tessellated STEP, which OCCT does support, wired to the viewer and the attachment path.**
   `read.step.tessellated` / `write.step.tessellated` (0 = off, 1 = on, 2 = `OnNoBRep`, the write
   default) cover the tessellated shape/shell/solid entities. This is a real capability sitting
   unused: it is a route for a heavy assembly to travel to P6's viewer, and a route for a
   customer's tessellated STEP to arrive through P4 without a B-rep rebuild.
   > PARTIAL (2026-09-14) — everything the superseded status below records, **plus a defect it shipped with, found by the
   > full suite the same day.** `write_step(..., tessellated=ON)` ran `BRepMesh_IncrementalMesh`
   > on the shape it was *handed*, which attaches the triangulation in place; the tests hand it
   > `built()`'s cached bracket, and `BRepBndLib` measures a triangulation (enlarged by its
   > deflection) in preference to the surfaces. So every drawing made later in the same process
   > drew a 120 mm edge as `120.207` — `test_manufacture_sheet.py` failed in the full run and
   > passed alone. The export now meshes a `BRepBuilderAPI_Copy`, and the caller's shape is left
   > untouched; broken back and watched to fail two new tests and the sheet test. What was
   > measured about primitives, the bracket and `OnNoBRep` is unchanged by the fix (the copy is
   > meshed and written exactly as the original was), and so is what remains open.
   > Tested by: `tests/test_manufacture_export_tessellated.py` (9 — `TestExportingLeavesThePartAlone`
   > added) and `tests/test_manufacture_sheet.py`. Code: `app/manufacture/export.py`.

   <!-- superseded 2026-09-14 -->
   > PARTIAL (2026-09-14) — **the parameter is plumbed and measured, and the measurement came
   > back partial rather than rounded up.** `write_step`/`read_step` gained a `tessellated=`
   > parameter (`TessellatedWrite.OFF`, the unchanged default; `ON`; `ON_NO_BREP`), applied
   > through `configure_writer`'s existing refuse-if-OCCT-did-not-listen contract.
   > **`ON` attaches a tessellated representation beside the BRep — on an OCCT primitive.** A
   > box, a cylinder, a filleted box and a boolean-cut box all wrote a `TESSELLATED_SOLID` entity
   > alongside `ADVANCED_FACE` when meshed first and written with `ON`, and the BRep a plain
   > reader recovers is unperturbed (volume, extent and face count all round-trip, same tolerance
   > as the existing schema tests). **The identical setting, on this codebase's own multi-feature
   > `bracket()` fixture — six bolt holes on a pattern, four corner fillets, an edge break —
   > writes no `TESSELLATED_SOLID` at all**, even though every one of its 34 faces carries a
   > `Poly_Triangulation` on inspection. Curvature, one fillet, one boolean cut and a finer
   > deflection were each tried in isolation against a primitive and none broke it alone, so the
   > cause is not isolated — a real Kryova part cannot yet be relied on to carry the payload, and
   > nothing here claims it does. Both findings are pinned as tests rather than one being assumed
   > from the other.
   > `OnNoBRep` measured as a no-op on every shape tried, primitive or not — it only suppresses
   > BRep on a shape that carries none, which nothing this codebase's kernel produces and which
   > this session did not construct to check.
   > **Wiring to P6's viewer and P4's attachment path is a second, separate residual**, and not
   > worth doing until the bracket-shaped gap above closes — a real Kryova part is exactly what
   > both callers would hand it.
   > Tested by: `tests/test_manufacture_export_tessellated.py` (7) — kept out of
   > `tests/test_manufacture_export.py` because that file's own `importorskip("ezdxf")` silently
   > skips every STEP assertion in it too, `ezdxf` being not installed here (see *Known
   > landmines* item 6). Code: `app/manufacture/export.py`.

4. **The materials licence position, written before any data is bought.** Two findings that
   settle E12 task 2's shape. **MatWeb cannot seed anything**: the licence is personal,
   non-transferable and non-sublicensable, caps a locally stored subset at **500 materials**,
   forbids mass export and building any public database, terminates on breach without notice —
   and, decisively, has the user agree **not to rely on the data for structural or engineering
   decisions and calculations**, AS IS, with no pedigree of the kind a credibility argument
   needs. **MMPDS is buyable and expensive**: Volume I 2025 is 2,744 pages at **$939**, the 2026
   edition 2,784 pages at **$1,049** — an ~11.7% annual rise, so it is a recurring line item, not
   a purchase — DRM-locked and licensed per named seat with no site, redistribution or API
   licence at retail, geo-restricted from a named list of countries, and decomposable into
   chapters (steel alloys from ~$399) for a platform that needs one family. The deliverable is
   therefore **not data**: it is the provenance and entitlement model E12 task 2 already
   sketched, hardened so that a customer's licensed allowables can be used **inside their
   deployment without Kryova ever holding, redistributing or seeing them**, and so that every
   property on screen names its source and the right under which it is being shown.
   > PARTIAL (2026-09-15) — **every material number now names the right it is shown under.
   > Found: two of the eight shipped materials cite a source whose licence forbids what Kryova
   > does with them.**
   >
   > **Read on 2026-09-15:**
   > - **MatWeb's License Agreement**, from the Wayback Machine's 2026-02-03 capture
   >   (matweb.com answered 403). It forbids selling, redistributing or making the content
   >   "available to anyone else". It caps a personal database at 500 materials. It says "you
   >   shall not rely on the MatWeb™ materials database or its content in making structural or
   >   engineering decisions and calculations". It also forbids AI/ML training, evaluation and
   >   embeddings. So the plan's summary holds, and the licence has since gained the AI/ML
   >   clause.
   > - **MMPDS-2026 Volume I**, from the Accuris store: $1,049.00, 2,784 pages, published
   >   2026-07-01. It is sold as a DRM-secured PDF or a finite set of single-user licences, with
   >   a restricted-country list.
   >
   > The plan's 2025 figure ($939) was not re-read.
   >
   > **Shipped:**
   > - `materials.Right` (`REDISTRIBUTABLE`, `CUSTOMER_LICENCE`, `NOT_ESTABLISHED`) on every
   >   `Source`, with `terms`, `licensee` and `reliance_disclaimed`. `REDISTRIBUTABLE` needs
   >   quoted terms, and a customer licence needs its licensee. `Source.to_dict`, and so every
   >   property's, carries the right.
   > - `Property.is_design_basis` is false for a source whose terms disclaim reliance.
   > - `app/solve/material_licences.py`: the readings, and `customer_records` /
   >   `load_customer_records`. These read a customer's licensed-allowables file kept in their
   >   deployment. Every property goes through `transcribe`, is set to `CUSTOMER_LICENCE` (a
   >   file cannot choose its right), names the licensee, and may not reuse a shipped slug.
   > - A test fails if a tracked JSON looks like such a file.
   > - **Interface change:** `Source` gains four defaulted fields, and every material
   >   property's `source` dict gains `right`, plus `terms`, `licensee` and
   >   `reliance_disclaimed` where set.
   >
   > **Found, flagged and not silently fixed:** `steel-1018` and `stainless-304` ship values
   > cited to MatWeb, in this repository and in its solvers. Their source now quotes the two
   > clauses and is never a design basis. Replacing the numbers needs a source whose terms have
   > been read and allow it. No source in the shipped set has had its terms read (ASM Aerospace
   > Specification Metals, the polymer datasheets, the elasticity text), so every shipped right
   > is `NOT_ESTABLISHED`, and a test holds that until one is read.
   >
   > **Open:**
   > - Nothing consults `customer_records` yet: `materials.resolve`, the agent's material
   >   tools and the routes see only the shipped set, and no deployment setting names the file.
   > - The frontend's material display was not checked for the new fields.
   > - The MatWeb-cited values need a replacement source.
   > - MatWeb's database-licensing terms and MMPDS's own licence text were not read.
   >
   > **The V&V artefact was already stale** (`app/verify/recorded --check`, before this change).
   > `app/solve/materials.py` is fingerprinted, so Windows re-records it with THE QUEUE A6.
   > Tests written on Linux and not run as pytest; each test body was run once by a one-off
   > script. Tested by: `tests/test_material_licences.py`.

   <!-- superseded 2026-09-15 -->
   > NOT STARTED.

5. **Establish what free-and-normative fatigue data actually exists, as a spike.** The plan
   currently gestures at Eurocode detail categories and the FKM guideline; the research pass
   returned **nothing verified** on either's licence terms or free availability, which is itself
   the finding — an assumption with no source behind it. This task is the spike: what
   EN 1993-1-9's detail categories are, what the FKM guideline costs and permits, and what may be
   *implemented* (a method is not copyrightable; its text is) versus *reproduced*. E8 task 3's
   weld classification depends on the answer.
   > PARTIAL (2026-09-15) — **what exists, what it costs and what the code takes is now recorded
   > and held by a test; what the code may take is a question for counsel.**
   > `app/fatigue/entitlements.py` registers every document `app/fatigue/` takes from:
   > - EN 1993-1-9, whose tables are encoded, read from a third-party copy of a Public.Resource.Org
   >   compilation;
   > - IIW-1823-07, whose hot-spot table is encoded, from a third-party copy;
   > - NACA TN 2805, from NTRS;
   > - the FKM methods pyLife implements.
   >
   > It also records the FKM 7th edition (EN), **EUR 320.00 incl. VAT, 232 pp.**, read on
   > vdmashop.de, from which nothing is taken, and BS 7608, which is not held. A test scans every
   > URL cited in `app/fatigue/` against the register, and every module naming FKM against it.
   > `docs/fatigue-data-licences.md` is the reading record, and it quotes TRIPS Art. 9(2) from the
   > WTO. **Found, and the plan had it wrong:** FKM methods already reach the product through
   > pyLife (Apache-2.0): FKM-Goodman and the extended Neuber rule. Neither guideline has been
   > read. **Not settled, needs counsel or the publisher:**
   > - whether encoding a standard's tables is a method or an expression (TRIPS 9(2) does not
   >   say);
   > - reliance on third-party copies;
   > - FKM's terms, which are not on the shop page;
   > - NTRS's and IIW's terms, not looked up.
   >
   > C-588/21 P was read only as the Court's reproduced summary, because the judgment itself could
   > not be fetched. Tests written on Linux and not run as pytest; the URL scan was checked by a
   > one-off script. Tested by: `tests/test_fatigue_entitlements.py`.

   <!-- superseded 2026-09-15 -->
   > NOT STARTED.

6. **QIF, for E17 task 5's inspection plans, with its state read correctly.** The DMSC download
   page — carrying a 2026 copyright — still advertises **QIF 3.0 (December 2018)** as current, is
   free but form-gated with **no licence or redistribution grant stated on the page**, and points
   implementers at a public QIF Community GitHub for free read/write tooling; MBC and DMIS are
   distributed through the same channel as separately versioned standards. So the metrology
   interop stack is three documents, not one, and the redistribution question is unanswered on
   the page and must be asked before anything is vendored.
   > PARTIAL (2026-09-15) — **the state is read and the redistribution question has an answer
   > for the community copies; no QIF is written yet.** `app/manufacture/qif.py` records, each with
   > its page:
   > - DMSC's download is still "ANSI QIF 3.0 released December 2018", free behind a form, and
   >   states no terms.
   > - The homepage adds ISO 23952:2020.
   > - The QIF Community repository, linked from DMSC's own page and last committed 2026-03-20, is
   >   **Boost Software License 1.0**. The exception is the CodeSynthesis C++ bindings, and it names
   >   paths that no longer match the tree.
   > - **The QIF 3.0 schemas in that repository are modified copies**
   >   (`bindings/CPP-Kramer/schema/*.xsd` beside `.xsdOrig`; `QIFDocument.xsd` differs by 1,272
   >   diff lines, key selectors included). So validating against them is not validating against
   >   QIF 3.0 as published.
   >
   > The rule this settles, held by a test: a schema enters the repository only under `data/qif/`,
   > beside its licence. **Open:**
   > - a QIF writer for E17.5's plans;
   > - a schema validator (neither lxml nor xmlschema is installed, so this is a dependency
   >   decision);
   > - the unmodified schemas' terms (DMSC's form);
   > - ISO 23952's current status (iso.org refused the fetch);
   > - MBC and DMIS, not read.
   >
   > Tests written on Linux and not run as pytest. Tested by: `tests/test_manufacture_qif.py`.

   <!-- superseded 2026-09-15 -->
   > NOT STARTED.

**Phase proof:** a full machine leaves Kryova as STEP with its edition named, is opened by a
system nobody here controls, and a written matrix says in advance exactly which of its
tolerances, datums, colours and assembly relationships survived — and the file agrees with the
matrix.

##### Phase E22 — The measured ceiling: surrogates, generation, and the long-horizon agent #####

**~8 engineer-months. Research-adjacent, like E16, and it is largely *measurement* work.**

**The question it answers:** what is the published, adversarially-checked ceiling on the two
capabilities this plan is betting on — a surrogate that stands in for a solver, and an agent that
holds intent across a machine — and how does Kryova measure its own distance from it?

1. **An acceptance rule for surrogates, derived from the best published example rather than from
   hope.** NVIDIA's DoMINO on the DrivAerML benchmark reports surface relative-L2 errors of
   **0.15 for pressure and 0.21–0.34 for wall shear** — 12–50%, not sub-1% — while reaching
   **R² = 0.96 on drag**, the one integrated quantity a designer acts on; and the authors
   themselves record **non-monotonic errors when ranking successive designs**, which is precisely
   the use a design loop puts a surrogate to. It is trained on 500 morphs of **one** car, and the
   paper reports **no wall-clock inference time, no training time and no speedup factor** despite
   claiming real-time inference. The rule this yields for E10 task 4: **a surrogate may rank, and
   may never decide**; every surrogate answer carries its error basis; and the decision point
   always spends a real solve. With VVUQ 70 unpublished (E20 task 1) there is no standard to
   appeal to, so this rule is ours and must be written where users read it.
   > DONE (2026-09-15) — **the rule is published on `/trust/commitments`, beside the paper it
   > came from, and the code that holds it was already there.** `app/optimise/screening.py` has
   > enforced it by types since E10.4 (`Estimate` has no `passed`, `Ranking` has no `best`, `screen`
   > returns only rebuilt candidates, `MAY_RANK_NEVER_DECIDE` rides every ranking). What was missing
   > was the place users read, so `app/verify/commitments.py` now carries
   > `an-approximation-may-rank-and-never-decide`, MECHANICAL, enforced by `screening.py` and
   > `tests/test_optimise_surface.py`. **The paper was re-read for it** (arXiv:2501.13350v1, Table 1
   > and section 4.1): pressure 0.1505, wall shear 0.2124 to 0.3359, drag R² 0.96, 500 morphs of
   > one car with 10% held out, and no inference time, training time or speedup. Two corrections to
   > the task text above, not silently absorbed: the authors' words are "smaller directional
   > changes between successive designs shows some oscillatory behavior", not "non-monotonic
   > errors"; and "12–50%" is not in Table 1's surface rows, which run from 0.1181 (area-weighted
   > pressure) to 0.3359, so the page quotes the rows and not the range. The test was written on
   > Linux and not run (the user's rule). Tested by:
   > `tests/test_trust.py::TestWhatKryovaWillNotClaim::test_the_surrogate_rule_is_a_public_commitment_with_its_source`,
   > `tests/test_optimise_surface.py::TestARankingMayRankAndNeverDecide`.

   <!-- superseded 2026-09-15 -->
   > NOT STARTED.

2. **Measure silent corruption, because that is the failure mode of this interaction model.** The
   2026 benchmark literature on LLM-authored CAD is blunt about where it breaks: on an
   execution-verified corpus of ~17,900 CadQuery programs across 106 industrial part families
   (half of them anchored to real ISO/DIN/EN/ASME/IEC standards), **roughly 64% of nominally
   successful natural-language edits silently corrupt geometry the instruction did not target**;
   frontier models reach only **~0.27 IoU** generating parametric code from part images, with a
   **15–20 point gap** between reasoning about a part from code (0.84) and from an image (0.58);
   invalidity on advanced-feature tiers runs **68–70%**; and a specialised model achieves a **2%**
   invalidity rate while producing the *worst* geometric fidelity of anything measured — so
   *"it produced a valid solid"* is not a metric, it is a distractor. Kryova's answer already
   exists in E5's assertions and E4's visual verification; what does not exist is the number.
   Deliverable: a harness that measures **silent-corruption rate on edits** against our own
   registry, run like a benchmark and published like one.
   > PARTIAL (2026-09-15, afternoon) — **the case set and the command exist; the number does not.**
   > `app/design/corruption_cases.py` holds twelve instructions over designs the mission ladder
   > builds: M1's bracket, M3's cover and M6's roller. Each carries the reference edit, and each is
   > EXACT against itself (one-off script, 2026-09-15). The cases were chosen to probe five things:
   > - one parameter reaching one feature;
   > - a formula consequence (`m6-od` must move the bore, which is `od − 2·wall`);
   > - removing a feature and removing one of a repeated pair;
   > - a material change that must leave every feature alone;
   > - an edit the parameter table cannot make.
   >
   > `python -m app.ai.design_editor --out <file>` runs the set against the configured provider.
   > It writes the report, with the editor named by provider, model, effort and a hash of the
   > system prompt. **Found while building it, flagged, not fixed:** M3's spec declares
   > `thickness_mm`, `width_mm`, `lip_mm` and `radius_mm`, and no feature reads any of them,
   > because `_m3_spec` writes the section from literals. Setting one through
   > `set_design_parameter` or the panel's `PATCH` builds the same cover. `m3-thicker` pins that
   > as NO_CHANGE, and `height_mm` alone moves only the glands. **Still open:** the run on the
   > local model (THE QUEUE D2) and publishing the number. Tests written on Linux and not run as
   > pytest. Tested by: `tests/test_design_corruption.py`,
   > `tests/test_design_corruption_cases.py`.

   <!-- superseded 2026-09-15 -->
   > PARTIAL (2026-09-15) — **the harness is built and exact; the number does not exist yet.**
   > `app/design/corruption.py` runs a set of `EditCase`s (a design, an instruction, and the
   > reference edit an engineer would make) through any `Editor` and classifies each result after
   > compilation, where a feature moved iff its resolved calls differ: FAILED, NO_CHANGE, EXACT,
   > CLEAN (right features, wrong values) or CORRUPTING (named features the reference does not
   > move). The targets are *derived from the reference*, so a formula consequence counts as
   > targeted and nobody types the list. `corruption_rate` is corrupting over nominal successes and
   > is `None`, never 0, when nothing succeeded; `exact_rate` sits beside it so an editor that never
   > edits cannot score perfect. A formula rewritten as its own value is not corruption; an
   > unrequested material change or removed feature is. `app/ai/design_editor.py` is the model
   > editor (whole spec in, whole spec out through `LLMProvider.complete`, parsed by
   > `DesignSpec.from_dict`). **Still open:** a case set of real instructions over real designs
   > (today's cases are test fixtures), the run against the local model (THE QUEUE D2), and
   > publishing the number. The tests were written on Linux and not run (the user's rule).
   > Tested by: `tests/test_design_corruption.py`.

   <!-- superseded 2026-09-15 -->
   > NOT STARTED.

3. **Re-found E14 and E16 on what the horizon literature measures, including where it refutes
   this plan's instincts.** Failures compound **non-linearly** with task length, with an abrupt
   transition from partial robustness to near-systematic failure past a small compositional
   depth; a 3,100-trajectory / 700-task attribution study splits failures **72.5% process-level
   vs 27.5% design-level** across seven categories, with subplanning, catastrophic forgetting,
   history-error accumulation and memory limitation dominant; reliability decay is
   **domain-stratified**, with code/tool-driving domains falling from 0.90 to 0.44 on a graceful
   degradation score across duration buckets while document processing stays flat; the strongest
   models show the **highest catastrophic-failure rates, up to 19%**; **memory scaffolds degraded
   long-horizon performance in all ten models tested**; added orchestration does not reliably
   help; and single-attempt benchmark scores cannot predict long-task reliability, with capability
   and reliability rankings inverting at long horizons. Two consequences, and they point opposite
   ways: **E14's interface contracts are the mitigation the evidence supports** — they shorten
   every horizon rather than trying to survive a long one — and **E16 task 3's hierarchical
   memory is now a claim under contest** and must be measured on our own traces before it is
   built out. Deliverable: Kryova's agent measured per duration bucket, on our missions, with
   model selection driven by that and never by a leaderboard.
   > PARTIAL (2026-09-15) — **the harness reads our own traces and reports per bucket; the
   > numbers need runs, and every literature figure is recorded as unsourced.** Tests written
   > on Linux and **not run** (the user's rule; Windows runs them, THE QUEUE D6).
   > `app/verify/horizon.py`.
   > **The task text named the wrong source and the work found it.** It said "`CatiaOperation`
   > rows and turn events". `TurnEvent` is a **ten-minute resume buffer, pruned aggressively**
   > — its own docstring says so — so a study over it would measure the last ten minutes of
   > traffic and report it as the fleet's history. The durable pair is `ConversationMessage`
   > (written as each step completes) and `CatiaOperation` (`resume.py`'s record of what was
   > done), and that is what `read_traces` reads.
   > **The unit is a turn and its duration is wall-clock**, user message to the last message
   > before the next one — the span the user waits through. A conversation is the wrong unit;
   > it spans idle days. The loop's own control messages are user-role, so the split skips
   > them by `prompts.CONTROL_NOTE`; splitting on them would report one long turn as several
   > short ones, which is the direction that flatters this product.
   > **`completion_rate` is defined and is explicitly not a success rate.** It is the fraction
   > of turns ending with an answer and none of the three recorded failure signals (budget
   > exhausted, a tool whose last word was an error, no answer). Whether the part was *right*
   > is not in these rows — a turn that confidently builds the wrong bracket completes
   > perfectly — so `success_rate` is reported only over turns carrying a ground-truth label
   > (`measure(outcomes=...)`, which E18's missions are the labelled set for) and is `None`
   > everywhere else. `NOT_A_SUCCESS_RATE` is one string carried by every report, for
   > `standards.NOT_VALIDATED`'s reason. An empty bucket reports `None`, never 0.0.
   > **Every literature figure this task quotes is `Basis.UNSOURCED`, and the type refuses to
   > let one carry a value.** Nobody in this repository has opened those papers; the plan's own
   > prose is the only record, and the studies are not even named in it. `Figure` therefore
   > refuses a `READ` basis with no URL and date, and refuses an `UNSOURCED` one with a number
   > — `nafems.Target`'s rule one level earlier. **Reading them is the other half of this task
   > and is not Linux work.** The most consequential is the memory-scaffold claim, which E16.2
   > already cites as the reason `taskgraph.py` does not generate plans and which holds E16
   > task 3 open.
   > **Also deliberately not done:** the default buckets are chosen against *this product's*
   > clock (a local-model turn is 4–7 minutes), **not** the cited study's, whose boundaries are
   > unknown. Nothing here may be plotted against those figures until they are read, and the
   > module says so.
   > **Flagged, not fixed:** `_was_truncated` reads the closing summary's prompt text out of
   > the transcript, because `AgentReply.truncated` is returned to the caller and never
   > written down. A column would be exact; the string match is a proxy and is named as one.
   > Tested by: `tests/test_verify_horizon.py` (22, written on Linux and not run).

   <!-- superseded 2026-09-15 -->
   > NOT STARTED.

4. **Finish the tool-retrieval argument at the half the papers skip: arguments.** The published
   measurements back E16 task 1 squarely — tool descriptions cost **~200 tokens each**, so a
   100-tool offer is ~20k tokens before the request is read; an adaptive shortlist averaging
   ~8 tools scored **93.1 ± 0.5%** gold-tool selection against a 370-tool registry where showing
   everything scored lower; the loss concentrates on the **hard** cases (60.9% on
   medium-difficulty queries even when the right tool was always on screen); and on a
   3,251-tool registry a **fixed K = 5 beat the learned policy** on coverage, so adaptivity is
   not free. But every one of those papers **explicitly scopes out argument correctness** — and
   in CAD a wrong numeric argument is not a failure, it is a plausible wrong part, which is this
   codebase's entire threat model. Deliverable: selection accuracy *and* argument accuracy
   measured together, on the registry, per turn.
   > PARTIAL (2026-09-15, afternoon) — **the case set and the command exist; the model's rates
   > do not.** `app/ai/argument_cases.py` holds twelve modelling requests over
   > `ToolBox.every_tool()` (233 tools). Each gold argument was read from the live schemas, and a
   > test re-checks name, type, enum and `required` against them. Rules for the set:
   > - a number is gold only when the request states it, and then exactly;
   > - an argument whose default already does what was asked is not gold;
   > - a feature reference is gold only when `context` names it.
   >
   > **The one number without a model:** the deployed selector offers the gold tool in **12 of
   > 12** cases at `DEFAULT_LIMIT` = 40. That is offer recall on a set written by the same session
   > that wrote the selector's registry, so it is a floor check, not a retrieval result.
   > `python -m app.ai.argument_cases --out <file>` runs the set against the configured provider
   > and names the chooser by provider, model, limit and prompt hash. **Still open:** the run on
   > the local model (THE QUEUE D3) and publishing its numbers. Tests written on Linux and not run
   > as pytest. Tested by: `tests/test_ai_argument_accuracy.py`,
   > `tests/test_ai_argument_cases.py`.

   <!-- superseded 2026-09-15 -->
   > PARTIAL (2026-09-15) — **the harness is built; no rate exists yet.**
   > `app/ai/argument_accuracy.py` scores each turn three ways, each over its own denominator:
   > whether `tool_retrieval.select` *offered* the gold tool (a retrieval miss, not a model miss),
   > whether the chooser *selected* it (by name, offered or not, because `ToolBox.call` accepts
   > either), and whether every gold argument arrived right given the right tool. Numbers compare
   > within the tolerance the case states; a number sent as a string is wrong; booleans are not
   > integers; extra arguments are listed, not scored. The report carries offer recall, selection
   > accuracy, selection given offered, argument accuracy given the tool, and end to end, each
   > `None` when its denominator is empty, bound to the chooser's name and the case set's digest.
   > `model_chooser` makes `LLMProvider.chat` the chooser. **Still open:** a case set of real
   > requests with gold calls over the real registry, the run against the local model (THE QUEUE
   > D3), and publishing the numbers. The tests were written on Linux and not run (the user's
   > rule). Tested by: `tests/test_ai_argument_accuracy.py`.

   <!-- superseded 2026-09-15 -->
   > NOT STARTED.

**Phase proof:** three numbers exist for Kryova that today exist only for other people's systems —
silent-corruption rate on edits, success by task-duration bucket, and argument accuracy given the
right tool — each measured by a harness in this repository, each re-measured on every model
change, and each published.

##### Phase E23 — The claim only a free stack can make #####

**~3 engineer-months, then continuous.**

**The question it answers:** with a competitor holding a $2.4 billion valuation and the CAD
incumbents shipping agents, what is left that Kryova can say that is both true and unavailable to
them?

1. **A competitor register, dated and sourced, kept like the validation register.** What the
   research found, as of mid-2026: **Zoo** runs a **proprietary, closed-source geometry engine**
   built from scratch — not OCCT, not Parasolid — with an open-source *client*, a canonical text
   representation (KCL) that every GUI action emits, metered per-second API billing plus
   "reasoning minutes", a free and open text-to-CAD that produces **single objects and cannot do
   assemblies**, and a product FAQ that **admits its agent produces incorrect geometry and designs
   that may be unmanufacturable or unsafe**. **PTC/Onshape** had exactly one AI feature generally
   available in March 2026 — a documentation assistant — with agentic CAD stated as *in
   development*, **MCP** named as the third-party integration surface, and a stated moat that is
   *data architecture*, not model quality: a fileless history capturing every modelling action,
   including failures. **FreeCAD 1.1** (2026-03-25) announced **no AI feature at all**.
   **PhysicsX** raised **$300M at ~$2.4B (2026-06-08)** for pre-trained "Large Physics Models" —
   surrogates, not a CAD or solver stack — and its announcement contains **no mention of
   certification, V&V or validation**. Each of those is a checkable claim with a date, and each
   will rot; the deliverable is the register, not this paragraph.
   > DONE (2026-09-15) — **the paragraph above is now a register, `app/verify/competitors.py`:
   > fourteen claims, each re-read at its source on 2026-09-15 and quoted in the page's words.**
   > Every claim records its standing (`confirmed` / `changed` / `not re-read`) and how it was
   > read (the page itself, or only a search engine's extract of it), and a claim that was not
   > re-read may carry no quote and must say why. **Nine confirmed, three changed, two not
   > re-read.** Changed: Zoo has supported assemblies in Design Studio since v1.0 (mates still on
   > its roadmap; whether its agent generates one was not read); **Zoo also speaks MCP**, so E23.3
   > is no differentiator against Zoo; and PTC announced Onshape Labs, an early-access AI
   > programme, on July 14, 2026 (the March state itself was not re-read). Not re-read: Onshape's
   > "data architecture" moat (no page stating it was found), and **FreeCAD 1.1 announcing no AI
   > feature**, because an absence needs the whole announcement and neither page could be read —
   > the blog answered 429 and the wiki serves an anti-AI-scraping challenge, which was respected
   > rather than worked around. The PhysicsX absence *was* checked over the whole page. **Checked
   > again at 18:00 the same day by exact string match against freshly fetched pages**: all eleven
   > quotes marked as read from the page match (markup aside), the PhysicsX page contains none of
   > `certif`/`verif`/`validat` even in its scripts, and both PTC claims were moved from search
   > extract to page read, because the pages now serve and the extract had dropped the spaces
   > around the dateline's dash. The FreeCAD release date is recorded as the two sources give it,
   > a day apart. **Not yet on the trust surface**; that is the phase proof. **The tests were
   > written on Linux and not run** (the user's rule); Windows runs them. Tested by:
   > `tests/test_verify_competitors.py`.

   <!-- superseded 2026-09-15 -->
   > NOT STARTED.

2. **Read the map before believing the position.** The register above says the funded competition
   is buying **speed and design exploration**, and that nobody in it is selling **credibility**.
   That is the gap Decision 3 already aims at, and this task is the discipline of re-checking
   quarterly whether it is still open — because a plan that assumes an unoccupied niche for four
   years without looking is how a differentiator becomes an assumption.
   > PARTIAL (2026-09-15) — **the quarterly re-check is enforced; the judgement it feeds is not
   > recorded anywhere.** `competitors.REVIEW_BY` is 2026-12-15, a quarter after the reading, and
   > `is_current` plus a test that fails the day after tell whoever sees it to re-read every
   > source and update what moved, never to move the date alone (the shape of
   > `eu_ai_act.REVIEW_BY`). What is left: a dated answer to "is nobody selling credibility still
   > true?", written by a person from each quarter's register rather than derived by code. The
   > first reading, as observations and not a verdict: PhysicsX sells speed (its models "predict
   > physical behavior in seconds rather than hours or days") and its announcement mentions no
   > certification, verification or validation — the only page that absence was checked on;
   > Zoo's FAQ still says its agent may "produce incorrect geometry"; and MCP is no longer a gap
   > anyone can occupy alone. **The test was written on Linux and not run.**
   > Tested by: `tests/test_verify_competitors.py`.

   <!-- superseded 2026-09-15 -->
   > NOT STARTED.

3. **Speak MCP, because the incumbent chose it.** Kryova's registry is already a declarative
   table of operations with schemas; exposing it as an MCP server is a small piece of work with
   an outsized consequence — a customer's own agent, or Onshape's, can drive Kryova's kernel, and
   the interop surface is one somebody else is standardising rather than one we invented. Scoped
   behind the same tenancy and CSRF guarantees as the HTTP API; it is another caller, not another
   trust boundary.
   > DONE (2026-09-15) — **Kryova is an MCP server for one conversation's tools, on revision
   > 2026-07-28, read from modelcontextprotocol.io that day.** That revision removed the
   > `initialize` handshake and protocol sessions, so the server is stateless: every request
   > carries its version and client capabilities in `_meta`, the `MCP-Protocol-Version`,
   > `Mcp-Method` and `Mcp-Name` headers must match the body (`-32020`), an unsupported version
   > is `-32022` with the supported list, a legacy `initialize` is told which version to speak,
   > and an unknown method is 404. `app/ai/mcp.py` is the wire format with no session in it;
   > `POST /mcp/conversations/{id}` (`app/api/routes/mcp.py`) binds it to `CurrentUser`, the chat
   > API's own ownership check (404 cross-tenant), a new `mcp_requests_per_minute` budget and an
   > `Origin` check against `cors_origins`. `tools/list` is exactly `ToolBox.every_tool()`, sorted;
   > `tools/call` goes through `ToolBox.call` and commits either way, as the agent loop does. A
   > mutating tool needs `_meta["kryova/allowMutations"]: true`, the MCP form of the chat API's
   > `allow_mutations`. Decisions: one endpoint per conversation (the conversation owns the
   > document); `draft_load_case` is not offered (the caller brings its own model); input
   > validation is top-level only (required and undeclared names), because no JSON Schema library
   > is installed; replies are always `application/json`, with no SSE, subscriptions, resources or
   > prompts. **Interface change:** a new route and a new setting. **The tests were written on Linux
   > and not run** (the user's rule); Windows runs them. Tested by: `tests/test_mcp.py`.

   <!-- superseded 2026-09-15 -->
   > NOT STARTED.

4. **Enter a public benchmark and publish the score, including when it is bad.** An
   execution-verified CadQuery benchmark on 106 industrial part families exists, and it runs on
   the exact stack this plan chose. Running it against Kryova's pipeline converts "AI-native" from
   an adjective into a number, and publishing the number — beside the silent-corruption rate from
   E22 — is a thing a company with a valuation to defend structurally cannot do first. That
   asymmetry, not the technology, is what open source is actually for here.
   > PARTIAL (2026-09-15) — **the benchmark is found, named and read; the scoring arithmetic
   > is implemented and checked against closed-form overlaps; no case has been run.**
   > `app/verify/benchcad.py`. Tests written on Linux and **not run** (THE QUEUE D7).
   > **It is BenchCAD**, which the plan described without naming: *"BenchCAD: A
   > Comprehensive, Industry-Standard Benchmark for Programmatic CAD"*,
   > <https://arxiv.org/abs/2605.10865> and <https://benchcad.com/>, **read 2026-09-15**.
   > 17,900 execution-verified CadQuery programs across **106 industrial part families**,
   > 52 of them anchored to 47 ISO/DIN/EN/ASME/IEC standards. **Code MIT, data CC-BY-4.0** —
   > which matters to this phase, because the argument is about what a free stack may publish.
   > **The scoring rule as published:** IoU-score = voxel Intersection over Union × execution
   > success rate, graded on *executed geometry, not appearances*, with a no-tools and an
   > agentic track. The sentence admits two readings — mean IoU over executed cases × the
   > execution rate, or mean IoU over all cases scoring a non-execution as zero — and **they
   > are the same number** (`(ΣIoU/executed)×(executed/total)` = `ΣIoU/total`), so the
   > ambiguity does not reach the arithmetic. `score` computes it once and reports both
   > factors beside it, because a low score being bad geometry and a low score being a
   > failure to build call for opposite work.
   > **`voxel_iou` is verified against closed form, not recorded output:** a part against
   > itself is exactly 1.0, two 10 mm cubes offset 5 mm are exactly 1/3 (500 mm³ over
   > 1,500 mm³), disjoint parts are 0.0. Both solids are sampled on **one** grid over the
   > union box — a per-solid grid would score two shapes that never touch. `ON` counts as
   > inside, or a grid aligned with a machined part's faces scores it against itself below 1.
   > **What is recorded as unknown rather than assumed** (`SCORING_RULE_CAVEAT`): the pages
   > were read, the benchmark's *scoring code* was not, so the voxel pitch, the alignment
   > convention and whether parts are posed canonically are unknown here — and each moves the
   > number. The licence is MIT, so that code can be read; until it is, a number from this
   > module is Kryova's arithmetic of the published rule and not a reproduction of the
   > harness, and it says so. `NOT_SUBMITTED` rides every report: a local run is not an entry.
   > **Still open, and none of it is Linux work:** obtaining the dataset, writing the adapter
   > from a case to a Kryova request (BenchCAD is image → CadQuery; Kryova is a conversation →
   > OCCT/CATIA, so the adapter is a real piece of design, not a wrapper), and the run itself,
   > which is 106 families against a local model. THE QUEUE D7.
   > Tested by: `tests/test_verify_benchcad.py` (17, written on Linux and not run).

   <!-- superseded 2026-09-15 -->
   > NOT STARTED.

**Phase proof:** a page on the public trust surface (P10 task 3) carries our benchmark score, our
verification coverage and our refusals, dated, beside a register of what everyone else claims —
and an engineer choosing between us and a funded competitor can tell which of the two is telling
them what it cannot do.
---

# PRODUCT TRACK

*The platform that makes the engineering usable, sellable and safe. P-phases run in parallel with
the engineering track; each names what it gates and what gates it.*

##### Phase P1 — Identity, sessions and tokens done right #####

> ✅ PHASE COMPLETE (2026-09-10) — all eight tasks done and tested. Two things it forced that
> were not in the task list and are worth knowing: **this service had no mail transport at all**
> (task 5 cannot exist without one, and neither can P2's invitations, P3's impersonation notice
> or P10's status comms), so `app/mail/` was built here and is shared; and **the password reset
> was not revoking sessions** — it cleared `refresh_token_hash`, a column nothing has read since
> task 1 replaced it, so a password changed *because* it was stolen left every device family
> alive. Found while wiring the theft notice, fixed here, pinned by
> `TestChangingAPasswordEndsEveryDevice`.

**~3 engineer-months. Starts immediately; blocks any external user.**

**The question it answers:** can a person trust Kryova with an account, on several devices,
against an attacker who steals a token?

**What was good and what was wrong.** Good: typed JWTs whose refresh/access distinction is actually
checked, bcrypt with prehash, hashed reset tokens, cookie sessions. Wrong: **one refresh-token hash
on the user row** — a second device's login silently revoked the first; no rotation family, so a
stolen refresh token was a 30-day capability with no detection; no absolute session lifetime;
`SECRET_KEY="changeme"` booted; the rate limiter trusted `X-Forwarded-For` and lived in one process.

1. **Sessions become rows.** A `sessions` table: user, device label, family id, current
   refresh-token hash, previous-hash (grace), created, last-used, absolute-expiry, revoked-at, IP,
   user-agent. Login creates a session; each device has its own.
   > DONE (2026-09-06) — the recorded defect is fixed. `refresh_token_hash` held one slot per
   > *user*, so a second device silently ended the first, and a stolen token and the real one wrote
   > to the same slot — whoever refreshed last won, and nothing noticed a token had been used
   > twice. Now a row per device family. Tested by: `tests/test_auth_sessions.py` (39 tests). Code:
   > `app/models/session.py`, `app/core/sessions.py`.

2. **Rotation with reuse detection.** Every refresh **rotates** the token; presenting an
   already-rotated token is a **theft signal that revokes the whole family** and forces
   re-authentication. Rotation without the detection half is theatre. Access tokens stay short
   (≤15 min); an absolute session cap (e.g. 30 days) ends even a perfectly rotated chain.
   > DONE (2026-09-06) — rotation per use, and a replay outside a 10 s race window revokes the
   > whole family. Tested by: `tests/test_auth_sessions.py` — **8 mutations run against the guards,
   > 8 caught**; the two that first escaped are recorded in the build plan.

3. **Session management UX** (frontend): device list with last-seen, "sign out this device", "sign
   out everywhere". Backed by revocation that actually revokes — the session row is the truth, not
   the cookie.
   > DONE (2026-09-10), superseding PARTIAL (2026-09-06) — `components/account/device-list.tsx`,
   > wired into `/dashboard/settings`. Each row carries last-seen as *relative* time against the
   > reader's own clock, which is why `relativeTime` is a pure function taking `now` rather than
   > reading one. "Sign out everywhere" `router.replace`s rather than pushing: this device is
   > signed out too, so leaving the dashboard in history means the back button lands on a page
   > that can only fail. Tested by: `../Kryova-frontend/src/lib/format.test.ts`; the revocation
   > underneath is `tests/test_auth_sessions.py`.

4. **Startup refusals.** `SECRET_KEY` unset or `"changeme"` ⇒ the server does not start, with a
   message that says what to do. Same for an empty CORS origin list in production mode.
   > DONE (2026-09-06). Tested by: `tests/test_auth_sessions.py`.

5. **Email verification and password flows hardened**: verification required before first project
   creation (not before first look — friction where it protects, not where it annoys); the reset
   flow already hashes one-time tokens, keep; add resend throttling.
   > DONE (2026-09-10). **The blocker was that this service had no way to send email**, so
   > `app/mail/` was built first: a `Mail`/`Delivery` pair with no behaviour, three transports
   > (`smtp` / `console` / `memory`), and every message the product sends as a named builder so
   > the set is enumerable rather than a grep. **Production refuses to start on a transport that
   > delivers to nobody** — same class as `SECRET_KEY=changeme` booting, and for the same reason:
   > on `console` a password reset still returns 204 and still tells the user to check an inbox.
   > The resend throttle is **per account, not per IP**, because somebody using us to post mail
   > at a stranger picks the address and not the network. A resend *replaces* the outstanding
   > link rather than adding one. Verification gates `POST /projects` and nothing else
   > (`deps.VerifiedUser`); an unverified account can still sign in and look around. Migration
   > `549ab806da4b` backfills every existing account as verified — see its docstring for why
   > locking existing users out of their own projects is not a security improvement.
   > Tested by: `tests/test_mail.py` (42), `tests/test_auth_verification.py` (31).

6. **Rate limiting that survives deployment reality**: keyed on the *authenticated principal* where
   one exists, on the connecting IP otherwise, `X-Forwarded-For` honoured **only** from a declared
   trusted-proxy list, and backed by a shared store so multiple workers enforce one budget.
   > DONE (2026-09-10), superseding NOT STARTED — **and that status line was two-thirds stale**:
   > the shared Redis store and the trusted-proxy rule had both shipped, and the entry claiming
   > "the in-process limiter that trusts `X-Forwarded-For` is still there" was describing code
   > that no longer existed. What was genuinely missing is the *key*: every limit counted against
   > the address, including on authenticated routes, and one office behind one NAT is a single IP
   > and a whole engineering team. `limit_key` now answers `scope:user:<id>` where a principal
   > exists and `scope:ip:<addr>` otherwise, decoding the token itself so it needs no
   > `get_current_user` to have run first. Applied to the two expensive routes —
   > `/ai/chat`(+`/stream`, sharing one budget) and `POST …/simulations`. `client_ip` moved into
   > `api/rate_limit.py`: three modules had each grown their own copy of a security decision.
   > Tested by: `tests/test_rate_limit.py` (16).

7. **Second factor (TOTP)** — standard `pyotp`-class implementation, recovery codes, and the
   decision recorded that WebAuthn/passkeys are the follow-on, not the first ship.
   > DONE (2026-09-10). Hand-written RFC 6238 rather than `pyotp` — fifteen lines of HMAC and a
   > modulo, checked against **the RFC's own appendix-B vectors**, against a supply-chain edge on
   > a security primitive. Three things that are not in the RFC and are the difference between a
   > real second factor and a decorative one: **an accepted code is burned** (`last_step`, so a
   > code read over a shoulder is not replayable for the remaining 90 s of its window),
   > comparison is constant-time, and **the shared secret is AES-256-GCM sealed at rest** with a
   > key HKDF'd from `SECRET_KEY` — which defends a stolen database and explicitly not a stolen
   > host, and says so. Ten single-use recovery codes, hashed like refresh tokens. `/auth/login`
   > now returns **a union**: a session, or `202` + `MfaChallenge` with no cookies set (see the
   > API-shape note below). Turning the factor *off* requires a code, because a hijacked session
   > must not be able to remove the defence that exists for hijacked sessions.
   > **WebAuthn/passkeys are the follow-on and not this ship** — they need attestation handling,
   > a credential table and a browser surface with no server-side fallback, which is a phase and
   > not a task. Tested by: `tests/test_totp.py` (30), `tests/test_auth_verification.py`.

8. **Token custody in both clients.** Web: httpOnly cookies as today, never storage. Tauri: the
   same cookie flow through its webview, with the OS keychain via Tauri's secure storage if a
   native token cache is ever needed — never a JSON file.
   > DONE (2026-09-10) — **as a guard, because the rule already held and nothing kept it
   > holding.** Measured: zero `localStorage`/`sessionStorage`/`indexedDB` in `src/`, and
   > `src-tauri/` handles no credential at all — Tauri renders this same frontend in a webview
   > and the cookie flow works there unchanged, so the "OS keychain if a native cache is ever
   > needed" clause stays conditional and the way to keep it conditional is to not need one.
   > `src/lib/token-custody.test.ts` scans every non-test source file for those three APIs and
   > for a hand-written `Authorization:` header, **and asserts it found files at all** so an
   > empty pass cannot be a vacuous one. Verified by writing `localStorage.setItem("kryova_token",
   > …)` into `api-client.ts` — the exact bug this codebase shipped once — and watching it fail.
   > Tested by: `../Kryova-frontend/src/lib/token-custody.test.ts`.

**API shape changed here, plainly:** `POST /auth/login` used to answer `SessionRead` and now
answers `SessionRead | MfaChallenge`, `202` for the second. `UserRead` gained `is_verified`.
A client that assumes the old shape reads `user` as undefined rather than failing loudly, so the
frontend narrows on the literal `mfa_required` — `isMfaChallenge` in `types/api.ts`, and
`tsc` caught the one call site that had not.

**Phase proof:** a stolen refresh token replayed after rotation kills the family and the attacker's
session, the user sees it in the device list, and the audit log (P3) records it. A demo of this
exact sequence is part of the phase's acceptance.

##### Phase P2 — Organisations, teams, roles and sharing #####

> ✅ PHASE COMPLETE (2026-09-10) — all six tasks done and tested, and the phase proof closed
> with them. Two things this phase forced that were not in its task list: **`create_invitation`
> could not actually invite anybody** until P1.5 gave the service a mail transport, so P2.1 had
> shipped a declaration rather than a capability; and **`test_tenancy_rls.py` applied one
> migration's policies to the test schema while production had three**, found because P2.5 made
> it a third — see task 5.

**The question it answers:** can a *team* — not a lone user — own a machine programme, with the
right people able to do the right things and nobody able to see across a tenant boundary, even
through an application bug?

1. **The model.** `organisations`, `memberships(user, org, role)`, projects owned by organisations
   (personal projects = an implicit personal org, so there is exactly one ownership model).
   Invitations by email with expiring signed tokens; joining flows in the frontend.
   > DONE (2026-09-06) — migration `1b07f4f27e89` backfilled 24 users → 24 orgs, 82 projects
   > placed, **0 orphans**, verified against the live database. Tested by: `tests/test_tenancy.py`,
   > `tests/test_organisations.py` (52 tests across this phase). Code:
   > `app/models/organisation.py`.

2. **Roles, two-layered.** *Platform roles*: `owner / admin / member / viewer` govern the
   organisation itself (billing, members, deletion). *Domain roles*: `engineer` (author designs,
   run simulations), `reviewer` (comment, approve gates, cannot edit geometry), `operator` (run
   released missions, cannot alter them). The approval gates in E16.5/P5 read the domain role — a
   review sign-off from someone without `reviewer` is not a sign-off.
   > DONE (2026-09-06). Tested by: `tests/test_tenancy.py::TestRoles`.

3. **Postgres RLS as the safety net.** Policies on every tenant-owned table; tenant context
   supplied per request via **`SET LOCAL` inside the request's transaction** and therefore
   discarded at COMMIT — safe under transaction-pooling PgBouncer, and the *only* sanctioned use of
   `SET` in this codebase. Application-level scoping remains primary; RLS exists for the day the
   application is wrong.
   > DONE (2026-09-06); **enforcement now verified locally (2026-09-08).** Policies are `ENABLE`d
   > and `FORCE`d on all 11 tenant tables. It was **deployed but INERT** for a year of Neon:
   > `neondb_owner` holds `BYPASSRLS`, which outranks both ENABLE and FORCE, so the first isolation
   > run **passed vacuously against a database enforcing nothing**. Switching this machine to a
   > local PostgreSQL whose application role is `NOBYPASSRLS` flipped
   > `test_the_application_role_must_not_bypass_row_level_security` from xfail to **XPASS** — the
   > policies really enforce here. **CI enforces too as of 2026-09-08** — `ci.yml` bootstraps the
   > service container as `postgres` and creates the application role
   > `LOGIN ... CREATEDB CREATEROLE NOBYPASSRLS`, owning both databases so `FORCE ROW LEVEL
   > SECURITY` does real work. Verified by reproducing both configurations in Docker against
   > `postgres:17`: under the old one the role is `rolsuper=true rolbypassrls=true` and the test
   > **xfails**; under the new one it is `false false` and the test **XPASSes**, with the whole
   > database job green (`alembic upgrade head`, `alembic check`, 688 passed). A dedicated CI step
   > fails the job if the connecting role can ever bypass again — the failure mode being guarded
   > is not a red build but a *green* one over assertions that have quietly stopped meaning
   > anything, which is what happened for a year.
   > **One place is still inert and it is the one that matters most: Neon in production.**
   > `neondb_owner` cannot drop `BYPASSRLS`, so this needs the application to connect as the
   > `kryova_app` role `core/database.py` already documents. The `xfail(strict=False)` marker
   > records exactly that and must not be deleted to make a run tidy.
   > Tested by: `tests/test_tenancy_rls.py` (16 tests). Code: `app/core/database.py`
   > `tenant_scope()`, `.github/workflows/ci.yml`.

4. **404-not-403, systematised.** The existing rule (`get_owned_project`) generalised to org-scoped
   resources: a resource outside your tenant does not exist. RLS makes the lie consistent.
   > DONE (2026-09-06). Tested by: `tests/test_tenancy.py`.

5. **Sharing and hand-off.** Transfer a project between orgs (with provenance intact); read-only
   share links for a released design package, expiring, revocable — the artefact a supplier or
   customer sees, without an account requirement for viewing.
   > DONE (2026-09-10). `GET /share/{token}` is **the only route in this service that answers
   > with no principal**, and it is built to have the least possible reach rather than to be
   > carefully guarded: it takes a token and *no id*, so there is nothing for a caller to
   > substitute and no comparison for us to get wrong. **Every refusal is one refusal** —
   > expired, revoked, never existed, project since transferred all give the same 404 with the
   > same sentence, because telling a holder their token *was* valid is what makes guessing
   > worth continuing. The token is stored hashed like every other credential here; the raw
   > value exists in one response and nowhere else. CAD download is **off by default** and
   > refused as a 404 rather than a 403, so a recipient sent a results package does not learn
   > the geometry sits behind a flag. A transfer moves everything keyed to the project, leaves
   > `owner_id` alone (provenance, not permission) and **revokes every live link**, because a
   > link the previous tenant issued would otherwise be a window into the new one.
   > **RLS:** `share_links` joins the tenant set and composes with the public route because the
   > shipped predicate passes when no tenant context is set; `project_transfers` uses the
   > existing via-project join. Adding a third `rls_statements()` exposed that
   > `tests/test_tenancy_rls.py` applied only the *first* migration it found, so the test schema
   > had one migration's policies while production had three — and which one depended on
   > filename sort order. It now applies all of them.
   > Tested by: `tests/test_sharing.py` (24). Code: `app/core/sharing.py`,
   > `app/api/routes/sharing.py`, `app/models/sharing.py`, migration `40108d7a30d1`.

6. **Frontend surfaces**: org switcher, member management, role assignment, invitation flows,
   pending-invite states — all in the existing dashboard design language.
   > DONE (2026-09-10). `/dashboard/organisations` — team switcher, members with **two role
   > columns** because the platform and domain ladders are two questions (collapsing them would
   > mean promoting somebody to admin so they could approve a design, which is the conflation
   > the two-layer model exists to prevent), invitations with pending state and withdrawal, and
   > a create-team form. `/invitations/accept` handles the case that actually breaks this flow:
   > a signed-out invitee must sign in **and come back with the token**, so the sign-in link
   > carries `?next=` — which meant fixing `?next=` on `/register`, where it was dead. Sharing
   > and transfer surfaces live on the project page. Tested by:
   > `../Kryova-frontend/src/components/sharing/share-panel.test.tsx` (9).

**Phase proof:** the cross-tenant test suite — two orgs, adversarial queries at every endpoint,
zero leakage, all misses reading as 404. Run in CI forever.
> DONE (2026-09-10), superseding PARTIAL (2026-09-08) — the suite exists, passes, and proves
> **both** halves in CI: the application scoping and the RLS net under it, since the suite
> connects as a `NOBYPASSRLS` role (task 3). Tasks 5 and 6 closing is what moves this off
> `PARTIAL`, and task 5 strengthened the proof rather than merely completing it: the isolation
> fixture now applies *every* shipped RLS migration to the test schema instead of the first one
> it happened to find.

**Gate GP1 opens after P1 + P2.**

##### Phase P3 — The admin panel and operations console #####

> ✅ PHASE COMPLETE (2026-09-10) — all seven tasks done and tested. One thing it forced that was
> not in the task list: `components/ui/input.tsx` spread `{...props}` *after* `className`, so any
> caller passing one silently replaced the whole style string — the field kept its label and lost
> its border, height and focus ring. `Button` had always merged. Found by using the primitive as
> documented; fixed and pinned in `input.test.tsx`.

**~4 engineer-months. Needs P1, P2.**

**The question it answers:** can the people running Kryova support users, control rollout, and
investigate incidents — with power that is bounded, visible and recorded?

*(Admin UI lives in the existing frontend under an `/admin` route group, gated by staff claims in
`proxy.ts` server-side — never a client-side-only gate. No second web app: Decision 6.)*

1. **The audit log, first.** Append-only, hash-chained (each entry carries the previous entry's
   hash, so tampering breaks the chain visibly), covering: auth events, admin actions, permission
   changes, impersonation start/end, quota changes, destructive operations, sign-offs. Written from
   day one of P3 because every later task must land in it. Admin reads it in the panel; org owners
   read their own org's slice.
   > DONE (2026-09-06) — **append-only in the database, not by convention**: a
   > `BEFORE UPDATE OR DELETE` trigger plus a statement-level `TRUNCATE` trigger, installed by
   > migration `2f3f8aadb319` *and* by `create_all`, so the guarantee holds in the test schema too.
   > Verified on the live database, where UPDATE, DELETE and TRUNCATE all come back
   > `RestrictViolation: audit_events is append-only`. Every entry carries the previous entry's
   > SHA-256, because the table's owner can drop the trigger and the application currently connects
   > as that owner: **the trigger prevents, the chain detects**, and the tests drop the trigger to
   > prove both halves. Tested by: `tests/test_audit.py` (25 tests). Code: `app/models/audit.py`,
   > `app/core/audit.py`.

2. **Staff roles.** `support` (read, impersonate-read-only), `operator` (quotas, flags),
   `platform_admin` (all, including suspension). Staff status is separate from any org membership —
   being staff grants nothing *inside* a tenant without impersonation.
   > DONE (2026-09-06). Tested by: `tests/test_admin.py` (40 tests). Code: `app/api/deps.py`
   > `require_staff`.

3. **Impersonation done right.** A separate token carrying **both identities** (acting staff +
   subject user), **read-only by default**, time-boxed, visually bannered in the frontend, every
   request audit-logged with the staff actor. Write-mode impersonation requires a second
   confirmation and a reason string, and notifies the user by email after the fact.
   > DONE (2026-09-06) — impersonation is a **row, not a claim**: read-only by default, refused
   > before the route function is entered, escalated only by a platform administrator with a second
   > reason, and revocable at once. Both identities are on every row and **there is no single
   > `user_id` column to collapse them into**. Tested by: `tests/test_admin.py`.

4. **User and org administration**: search, view, suspend/reactivate (suspension revokes all
   session families — P1 machinery), storage/compute quota adjustment, manual verification, GDPR
   deletion with a grace window (soft-delete, then hard purge job through `MediaService` so blob
   refcounting holds).
   > DONE (2026-09-10), superseding PARTIAL (2026-09-06). `app/core/lifecycle.py` is the **only**
   > writer of `is_active`, `suspended_at` and the deletion columns — a second writer is a second
   > chance to leave them disagreeing, and an account with `suspended_at` set and `is_active`
   > still true is visibly suspended in the console and fully usable through the API.
   > **Suspension revokes every session family**, which is what makes it immediate rather than
   > merely recorded: setting the flag alone leaves live access tokens working for their fifteen
   > minutes. **Deletion is scheduled, never done** — the grace window *is* the feature, `purge`
   > is refused before the date with a message naming it, and an emergency uses `suspend`, which
   > is immediate and reversible. The purge goes through `MediaService`, which refcounts, so a
   > user whose STEP file is byte-identical to another tenant's does not take theirs with them;
   > it returns a `PurgeReport` rather than saying "done", because the interesting failures here
   > are partial ones. Both suspension and scheduled deletion email the person it happened to.
   > Cancelling a deletion does **not** lift a suspension that predates it. Tested by:
   > `tests/test_platform.py` (44 across this phase).

5. **Feature flags**: per-tenant and per-user overrides, kill switches, percentage rollouts.
   Server-evaluated — the flag state rides to the frontend with the session, so the UI and the API
   always agree on what is on.
   > DONE (2026-09-10). One evaluator, `app/core/flags.py`, and `GET /platform/state` is its
   > answer; nothing re-decides in the browser, because a flag the UI reads differently from the
   > API is a feature that is half on — a button whose endpoint refuses, or an endpoint nobody
   > can reach. **The kill switch outranks every override**, and that asymmetry is the point: an
   > operator whose feature is hurting people needs one action that turns it off for everybody,
   > including the tenants somebody specially enabled it for. **The rollout is a hash, never a
   > draw**: `sha256(key + subject)` bucketed into 100, so a subject gets the same answer forever
   > and widening a percentage only ever *adds* people — the property that makes a staged rollout
   > safe to run forwards. Bucketed on the organisation where there is one, because a team where
   > three of five engineers have a feature cannot talk to itself about the product. Keying on
   > flag *and* subject stops one unlucky tenant being in every early rollout. An unknown key is
   > False, never an error, so removing a flag while code still checks it does not become an
   > outage. Tested by: `tests/test_platform.py`.

6. **The operations dashboard**: job queues, solver failure rates by taxonomy class, per-op success
   rates (E15 task 5's data), storage growth, active sessions — the panel where "is Kryova healthy"
   has one answer.
   > DONE (2026-09-10), superseding PARTIAL (2026-09-06) — `GET /admin/health` is the one answer
   > to "is Kryova healthy": queue depth by status, jobs and success rate in a window, the ten
   > most common failures, storage and its growth, live sessions, account counts, whether this
   > deployment can send email at all, and whether maintenance is on. Two honesty rules carried
   > from `read_organisation_usage`: **`success_rate` is `None`, not 0.0, when nothing finished**
   > — no runs to judge and every run failing are opposite states, and a dashboard showing 0% on
   > a quiet night sends somebody hunting an outage that is not there — and **`failure_grouping`
   > states how the failures were grouped**, which is by the runner's recorded message because
   > that is what the schema holds. A taxonomy class on the job row is E15 task 5; that is a gap
   > in the data, named here rather than papered over. Tested by: `tests/test_platform.py`.

7. **Announcements and maintenance mode**: a banner the backend serves and both clients render;
   read-only mode that refuses mutations with an honest message instead of erroring.
   > DONE (2026-09-10). **A maintenance mode that returns 500 is an outage with a nicer name**,
   > so this refuses with `503`, a `Retry-After`, and the operator's user-facing sentence — on
   > mutating methods only. Reads pass through untouched, which is the whole value: somebody who
   > tries to start a simulation during a migration is *told what is happening*, on a working
   > application, instead of staring at a broken one. The check lives in `get_current_user`
   > rather than a middleware because the decision needs to know whether the caller is staff, and
   > that row is one the dependency has already paid to be able to read. **Staff are let through
   > by default** — the people who fix the incident are the ones holding grants, and a read-only
   > mode that locks them out is one somebody works around by turning it off. `reason` (the
   > operator's note) and `message` (what a user is told) are separate columns and `reason` is
   > **not in the response model at all**, so it cannot leak by omission. `GET /platform/state`
   > is readable signed out and *during* maintenance, deliberately: the endpoint that explains
   > the window must not be one of the things the window refuses. Tested by:
   > `tests/test_platform.py`; the banner by
   > `../Kryova-frontend/src/components/platform-banner.test.tsx`.

**Phase proof:** a support engineer resolves a real user issue via read-only impersonation; the
user's org owner can see that it happened, when, and by whom, in their own audit view.

##### Phase P4 — File attachments: reading what users hand us #####

**~5 engineer-months. Needs P1; feeds the agent immediately; drawing understanding matures with
Era IV.**

**The question it answers:** a user drops a supplier datasheet PDF, a load-case spreadsheet, a
photo of a failed weld, a STEP file and a scanned drawing into the conversation. Does Kryova
*understand* them — and stay safe while doing so?

1. **Ingestion.** Attachments ride the existing chunked-upload path into the content-addressed
   store (dedup for free; the same datasheet attached twice costs one blob). Type sniffing by
   content, size/type limits, per-org storage quotas (P3). Every attachment is a first-class
   object: owner, conversation link, extraction status, provenance.
   > DONE (2026-09-10) — the row was the missing half, and `SourceRef.attachment_id` had said so
   > since P4.2: its docstring read "None until P4.1 has a table". `app/models/attachment.py`
   > is that table.
   > **An attachment that could not be read still gets a row and still appears in the list.**
   > Refusing the upload loses the fact that the user handed us something and expects it to have
   > been seen — and "why is my STEP file not in the list" is a much harder question to answer
   > than "why does it say *not a document*".
   > **`UNSUPPORTED` is not a kind of `FAILED`**, and the panel colours them differently. A STEP
   > file in the document slot is geometry: the file is fine, it is in the wrong place, and the
   > detail names what to do with it instead. Grouping the two tells somebody their file is
   > corrupt when it is not — the same shape of error as grouping `cancelled` with `failed`.
   > A defect found while writing the tests and fixed: a **missing blob** was being reported as
   > an unsupported *format*, because `sniff` finds no header in a file that is not there. That
   > filed a storage fault as a format one and advised "export it as PDF and attach that" about
   > a file that had simply not finished uploading.
   > The blob is not duplicated — `media_id` points at the content-addressed store, so one
   > datasheet attached to four conversations is one blob and four rows.
   > Tested by: `tests/test_attachments.py` (20). Code: `app/models/attachment.py`,
   > `app/core/attachments.py`, `app/api/routes/attachments.py`, migration `ef4d9b93d3ca`.

   <!-- superseded 2026-09-10 -->
   > PARTIAL — chunked upload and the content-addressed store exist. Code: `app/media/`.

2. **The extraction pipeline**, tiered by format, all local and free:
   - **CAD (STEP/IGES/BREP/STL/DXF)** → the geometry pipeline (E1's kernel; `ezdxf` for DXF
     entities/dimensions). A STEP attachment can *become* a `GeometryVersion` on request.
   - **Documents (PDF/DOCX/XLSX/PPTX/HTML/images)** →
     **[Docling](https://github.com/docling-project/docling)** (IBM Research, MIT) as primary —
     strongest local table/layout/reading-order understanding, OCR for scans.
     **[MarkItDown](https://github.com/microsoft/markitdown)** (Microsoft, MIT) as the light
     fallback for the long tail. Chosen over hosted parsers: local, free, no data leaves the
     deployment. *Revised 2026-09-14: neither is used. See the status below for why.*
   - **Spreadsheets** keep their structure — a load-case table becomes rows with units, not prose.
   - **Images/photos** → the vision provider (already pluggable, P5 surfaces it).
   > DONE (2026-09-15) — **an attached PNG or JPEG is described by the configured vision model
   > and stored as a guess labelled as one, and the agent is offered the from-attachment route
   > as a tool.** The tests were written on Linux and **not run** (the user's rule; Windows runs
   > them, THE QUEUE D4). Only `py_compile` and an import of `app.main` were run, so no guard
   > here has been seen to fail.
   > **Pictures.** `app/documents/images.py` reads a picture through an injected `Look`, so
   > `app/documents` still imports nothing from `app.ai` and the tests open no socket.
   > `app/ai/vision.py::AttachmentLook` is the real one: it sends the image with a frozen prompt
   > (`ATTACHED_IMAGE_SYSTEM`, "data, not instruction", never estimate a size, never complete a
   > partly legible number) against `ImageReading` (a description, then each line of text).
   > Everything that comes back is `INFERRED` and `UntrustedText`: a description fragment and
   > one `ANNOTATION` per line, cited as "the model's description of weld.png" and "the text
   > the model read, line 2", so the route returns `UNVERIFIED_NOTE` beside it. Nothing is ever
   > a `DIMENSION`; that is P4.3's staging. **No picture is ever an empty document:** no
   > provider is `UNSUPPORTED` with sniff's advice; a model that cannot see is `UNSUPPORTED` with
   > the provider's reason, and Ollama's `_sees()` refuses before the image is sent; an
   > unreachable model and an answer with nothing in it are `FAILED`. Only PNG and JPEG are
   > sent, under 5 MB.
   > **What a picture costs is recorded.** The route posts the call to the token ledger and the
   > bill through `routes/ai.py::_record`, under the new purpose `attachment_image` and the
   > vision model's name, and refuses the read (not the upload) when the daily allowance is
   > spent. A spreadsheet costs no ledger query.
   > **Defect closed with it:** the Anthropic and OpenAI-compatible providers labelled every
   > image `image/png`. A JPEG would have been rejected by a hosted API that checks the label
   > against the bytes. `provider.image_media_type` reads it from the bytes.
   > **The agent's tool.** `import_geometry_from_attachment` calls
   > `attachments.geometry_version_from`, which the route now calls too, so the button and the
   > agent cannot disagree about what a part is. Omitting the id uses the one part attached to
   > the conversation; two parts is a refusal naming both with ids, never a guess. Access is
   > the route's rule (`MEMBER` of the owning organisation), and a foreign attachment or
   > project is refused in the same words as a made-up id. Mutating, like
   > `sync_geometry_from_catia`.
   > **Interface changes.** `POST /attachments` now answers `ready` with `reliability:
   > inferred` for a PNG or JPEG where a vision model is configured (it was `unsupported`), and
   > the request waits on that model call. `LLMProvider.look` takes JPEG as well as PNG. A new
   > agent tool. `routes/ai.py::_record` and `_meter_tokens` take an optional `model`. No
   > migration, no enum.
   > **Flagged, not fixed:** (1) a picture blocks the request for the model call, seconds to a
   > minute on a local model; `ExtractionStatus.PENDING` is there for moving it to the job
   > queue. (2) `ToolBox._project`, used by every other project tool, asks for the project's
   > *owner*, while the HTTP layer asks for membership, so an organisation member can run a
   > simulation through the button and not through the agent. (3) Nothing quotes an
   > attachment's content into the agent's turn, and the frontend never calls
   > `createAttachment` (task 7 below, and task 6's correction).
   > Still Windows: THE QUEUE C3 (files saved by Microsoft Office) and D4 (a real vision model
   > reading a real photograph, and running these tests).
   > Tested by: `tests/test_attachments.py` (`TestAPictureIsDescribedByAModelThatCanSee`,
   > `TestAPictureNobodyCouldReadIsNotAnEmptyPicture`, `TestAPictureAttachedThroughTheRoute`,
   > `TestTheAgentCanMakeAnAttachedPartGeometry`), `tests/test_vision.py`
   > (`TestAnAttachedPictureIsDescribed`, `TestTheImageIsLabelledByWhatItIs`), all written on
   > Linux and not run, plus the files named in the statuses below.

   <!-- superseded 2026-09-15 -->
   > PARTIAL (2026-09-15) — **an attached part now becomes a geometry version on request, and a
   > CSV attached through the product is finally read as a table. Images are the one reader-side
   > item left.**
   > **A defect that made the 2026-09-14 status below half true.** Detection falls back to the
   > file's extension for CSV, TSV, STL and IGES. But the attachment route read the stored blob,
   > which is named by its digest and has no extension. So every CSV attached through
   > `POST /attachments` was read as plain text, and the CSV reader, with its cells, headings and
   > numbers, only ever ran in tests that called it on a named file. That is CLAUDE.md testing
   > item 8 again: the reader was proved and the path was not. `kinds.sniff` now takes the name
   > the file arrived with, reads only its extension, and still lets content win (a PDF named
   > `.csv` is a PDF). Both callers pass it.
   > **`POST /projects/{id}/geometry/from-attachment`** takes an attachment id and registers the
   > blob that is already stored, with no second upload. The version and the attachment share one
   > blob. Access comes from the path: the project needs write access and the attachment must be
   > the caller's, and either miss is 404. Only an attachment detected as STEP, IGES or STL is
   > accepted; anything else is refused naming what it was read as. A part that does not inspect
   > is refused, and its blob is kept, because it is still an attachment. The reader's refusal of
   > solid geometry now says the attachment itself can become a version.
   > **Security gap closed with it:** `POST /attachments` stored any `project_id` as given. An
   > attachment could be filed under another tenant's project, and a real id answered 201 where a
   > made-up one hit the foreign key, which told a stranger which ids exist. It now needs write
   > access to that project, and a miss is 404.
   > **Still open:** images to the vision provider (the result would be `INFERRED`). The agent is
   > not offered the new route as a tool. OCR for scans belongs to task 3. No file here was saved
   > by Microsoft Office (THE QUEUE C3). Everything the status below lists as shipped still stands.
   > Tested by: `tests/test_attachments.py` (`TestAnAttachedPartBecomesGeometry`,
   > `TestAStoredBlobIsReadByTheNameItArrivedWith`,
   > `TestAnAttachmentCannotBeFiledUnderSomebodyElsesProject`), and the files named below.

   <!-- superseded 2026-09-15 -->
   > PARTIAL (2026-09-14) — **spreadsheets, CSV, Word, PowerPoint and HTML are now read into
   > cells, slides and paragraphs, each located. Images and STEP-to-geometry are still open.**
   > **Docling and MarkItDown were dropped, and the plan above is revised to match.** Both turn a
   > document into Markdown, which loses exactly what task 4 needs: "cell C7 of loads.xlsx" cannot
   > be recovered from a Markdown table. Docling's standard extra also requires torch,
   > torchvision, transformers, accelerate and rapidocr (read from the 2.127.0 wheel metadata).
   > MarkItDown sends Word through mammoth to HTML first. What replaced them:
   > - `app/documents/tables.py`: `.xlsx` through `openpyxl`, and CSV/TSV through the standard
   >   library. A typed number is `MEASURED`; a string, a CSV cell and a saved formula result are
   >   `TRANSCRIBED`.
   > - `app/documents/office.py`: `.docx` and `.pptx` read straight from the XML with `zipfile`
   >   and `xml.etree`. `python-docx` was rejected because `Document.paragraphs` skips content
   >   controls.
   > - `app/documents/webpage.py`: HTML through `html.parser`.
   > - `app/documents/structure.py`: what all three share, namely the plain-number rule, the
   >   heading-row rule, the package guards (25 MB declared size, any DTD refused, Strict OOXML
   >   refused by name) and a fragment budget that says where it stopped.
   > **What a sender may not have seen is read, and said.** Hidden and very hidden sheets, hidden
   > rows and columns, tracked insertions, hidden Word text, hidden slides, speaker notes,
   > comments with their authors, custom properties, and HTML hidden by attribute or inline
   > style are all read, located and counted in a note. Tracked deletions and HTML comments are
   > counted but not read, and scripts and styles are skipped. A formula with no saved result
   > and an error cell become `Unread`, with the formula quoted. A note names a sheet by number,
   > never by name, because notes render outside the quote fence.
   > **Five defects found, each on a file a real program wrote** (`tests/data/documents/`,
   > pandoc 3.1.3 and LibreOffice 26.2.5.2):
   > - **A Windows-1252 CSV was refused as a binary file** by `kinds._is_texty`. Single-byte text
   >   is now text, and the reader notes that it guessed the encoding.
   > - **A title row spanning a table was taken as column A's heading** on the LibreOffice docx.
   >   A heading row must now cover every column the row below fills.
   > - **LibreOffice's speaker notes were dropped without a sign.** They sit in an ordinary text
   >   box, and the first reader read only the `body` placeholder.
   > - **That fix was then missing from commit `3d6fa76`.** The committed reader still had the
   >   `body`-only filter, the docstring said otherwise, and its own test failed. The mutation run
   >   is what showed it.
   > - **`ezdxf` was imported and listed nowhere**, so DXF attachment reading could never run. The
   >   59 tests then in `test_documents_readers.py` and the 44 in `test_manufacture_export.py` had been
   >   skipping on every environment built from the requirements. `ezdxf` 1.4.4 and `openpyxl`
   >   3.1.5 are now listed, and both are MIT.
   > **Interface changes, plainly.** No migration.
   > - `GET /attachments/{id}/content`: each `fragments[]` entry gains `cells` (text, heading,
   >   number, reliability, where, cite), and each `unread[]` entry gains `where`. Rows extracted
   >   before today carry neither until they are read again.
   > - `DocumentKind.HTML` is a new enum value.
   > - `Locator` gains `slide`, `part`, `table`, `column` and `paragraph`. `Fragment` gains `cells`.
   > **Flagged, not fixed:** the frontend's `ExtractedFragment` type has no `cells`, so the panel
   > still renders a table row as one line.
   > **Guards broken on purpose:** 44 mutations across the five modules, `kinds.py`, `readers.py`
   > and `core/attachments.py`, all caught by named tests, with every restore sha256-checked.
   > Three were re-run after the commit fix, because their first "catch" was the pre-existing
   > failure.
   > **Still open:**
   > - **Images to the vision provider.** The result would be `INFERRED`.
   > - **A STEP attachment becoming a `GeometryVersion` on request.** The geometry attach route
   >   refuses a non-CAD media kind today, and three places create a version.
   > - **OCR for scans.** That belongs to task 3.
   > - **None of these files was saved by Microsoft Office.** That is THE QUEUE C3.
   > Tested by: `tests/test_documents_tables.py` (44), `tests/test_documents_office.py` (38),
   > `tests/test_documents_webpage.py` (37), `tests/test_documents_readers.py` (61),
   > `tests/test_attachments.py::TestATableIsStoredAsCells` (3). Code: `app/documents/`.

   <!-- superseded 2026-09-14 -->
   > PARTIAL (2026-09-06) — the readers are tested now, **including that text-bearing DXF entities
   > are no longer silently dropped**, which had been reading a drawing full of MULTILEADER notes
   > and TOLERANCE frames as an empty document. Code: `app/documents/`.

3. **Engineering-drawing understanding**, staged honestly: text-layer PDFs → Docling now; scanned
   drawings → OCR now; **dimension/GD&T extraction** → a later, research-adjacent task (layout
   detection + a document transformer / fine-tuned VLM — Donut/Florence-2-class), explicitly *not*
   promised early, because a wrongly read tolerance is worse than an unread one. Until then,
   extracted drawing content is labelled "unverified read — confirm dimensions before use".
   > PARTIAL (2026-09-10) — **the labelling is built; the extraction itself is deliberately
   > still staged.** `Reliability.INFERRED` means the characters were *guessed at* rather than
   > transcribed, and `attachments.UNVERIFIED_NOTE` is the sentence that travels with them:
   > "confirm every dimension against the drawing before using it". It is one string in one
   > place, because a safety label with two wordings has two standards, and it is returned by
   > the API rather than composed by the client for the same reason. The panel renders it
   > **above** the extracted content, not below — a caveat under something already believed is
   > a footnote, which is the argument P5.4 makes about factors of safety.
   > `dimensions_in` surfaces the fragments a reader classified as dimensions, with their
   > locators, and there is deliberately **no way to apply one**: no route, no button, no tool.
   > That is this task's promise kept rather than broken early — a wrongly read tolerance is
   > worse than an unread one, and `test_nothing_here_turns_a_dimension_into_a_parameter`
   > asserts the absence rather than trusting it.
   > **Still open, and it is the research-adjacent half named in the task**: layout detection
   > and a document transformer (Donut/Florence-2-class) that reads dimensions and GD&T frames
   > properly. Nothing here attempts it.
   > Tested by: `tests/test_attachments.py::TestTheUnverifiedReadLabel`,
   > `::TestDimensionsAreCandidatesOnly`,
   > `../Kryova-frontend/src/components/attachments/attachment-panel.test.tsx` (9).

4. **Provenance-tagged facts.** Everything extracted enters the conversation as quoted material
   with a source pointer (file, page/sheet/cell). When an extracted number flows into a design
   parameter, the *spec records the source* — `FeatureSpec.note` and the requirement links (E11
   task 3) already give it somewhere to live. "Where did 42 mm come from?" must answer "cell C7 of
   loads.xlsx, attached 2026-09-05".
   > DONE (2026-09-10) — `attachments.cite_fact` produces exactly that line, and the place for
   > it to live now exists: P5.3 persisted the `DesignSpec`, so a parameter's description is a
   > durable slot rather than an in-memory one.
   > The citation comes from `SourceRef.cite()` — one implementation, already sanitising the
   > user-supplied filename — rather than being assembled at the call site, so a second wording
   > cannot drift from the first.
   > **An inferred read carries its warning *inside* the citation.** A provenance line reading
   > only "cell C7 of loads.xlsx" for a number OCR guessed at would be a citation that made an
   > unverified value look checked, which is worse than no citation: it is the form of evidence
   > without the substance.
   > Tested by: `tests/test_attachments.py::TestProvenanceTaggedFacts`.

5. **The injection boundary (Decision 8).** Extracted text is *data*: rendered as quoted context,
   never merged into system instructions; the agent may not take a tool action whose sole
   justification is attachment text without surfacing that justification for the user's approval
   where it matters. Test fixtures include hostile documents ("ignore previous instructions…")
   asserted inert — a regression suite, not a hope.
   > DONE (2026-09-06), **and the guards are structural rather than filters**: `UntrustedText` does
   > not subclass `str`, so concatenation raises and `f"{x}"` yields a description;
   > `render_into_user_message` is the only accessor that returns payload characters and it
   > **requires the user's own message**, so no call exists that puts attachment content in a
   > system prompt. Forged `[attachment: …]` headers are defanged. Tested by:
   > `tests/test_documents_injection.py` (32 tests).

6. **Frontend**: drag-drop into the conversation, upload progress (chunked client exists),
   extraction status, an attachment panel per conversation, inline previews (tables, images,
   geometry via P6 viewer), and "insert as parameter / as requirement / as load case" affordances —
   the moment extraction earns its keep.
   > PARTIAL (2026-09-15) — **the composer now creates a document attachment; the insert
   > affordances and inline previews are still deliberately absent.** The tests were written
   > on Linux and **not run** (the user's rule; Windows runs them). `tsc --noEmit` is clean.
   > **What closed.** `AttachPill` routes on the filename: a part (STEP/IGES/STL) goes to
   > `uploadGeometryFile` and becomes a geometry version of the project, and everything else
   > goes to the new `chunked-upload.ts::uploadDocumentFile`, which uploads the blob and calls
   > `createAttachment` with the conversation id. `chat-view.tsx` bumps an `attachRevision` so
   > the panel refreshes at once rather than on the next finished turn — a file that stays
   > invisible until you send a message reads as an upload that failed.
   > **The `accept` filter was the whole bug and is gone.** It listed only the CAD extensions,
   > so the file dialog would not *offer* a spreadsheet; the backend sniffs content and
   > records what it found, and a narrower opinion in the client made the wider one
   > unreachable. An `unsupported` row is reported as such in the composer line, never as a
   > plain "Attached".
   > **Always chunked, whatever the size.** The single-shot route is `uploadGeometry`, which
   > makes a geometry version — the one thing a document must not become — and the chunk loop
   > is the only path yielding a bare media id.
   > **`DocumentUploadTransport` is a second interface rather than two more methods** on
   > `ChunkedUploadTransport`: a document never calls `attachGeometry` and a part never calls
   > `createAttachment`, and widening the shared one made ten existing stubs fail to compile,
   > which `tsc` caught.
   > **Still open here, and unchanged:** "insert as parameter / requirement / load case" stays
   > unbuilt for task 3's reason (the values are candidate readings), and inline previews are
   > P6's viewer. Drag-and-drop still reaches only the geometry path.
   > Tested by: `../Kryova-frontend/src/components/chat/attach-pill.test.tsx` (9, written on
   > Linux and not run), `../Kryova-frontend/src/components/attachments/attachment-panel.test.tsx` (9).

   <!-- superseded 2026-09-15 -->
   > PARTIAL (2026-09-15) — **correction: the composer never creates a document attachment.**
   > The status below says the document path reuses `AttachPill`. It does not.
   > `api-client.ts::createAttachment` is defined and nothing calls it: `AttachPill` uploads
   > geometry and inserts "Attached … — geometry vN" into the message. So from the GUI a
   > spreadsheet, a PDF or a photograph never becomes an attachment, and the panel only ever
   > shows rows made through the API. Found 2026-09-15 while giving the agent the
   > from-attachment tool (task 2). Open here: the composer calls `createAttachment` with the
   > conversation id for anything that is not a part, and the panel refreshes. Everything else
   > below still stands.
   > Tested by: `../Kryova-frontend/src/components/attachments/attachment-panel.test.tsx` (9).

   <!-- superseded 2026-09-15 -->
   > PARTIAL (2026-09-10) — **the panel is built; the insert affordances are deliberately not,
   > and inline previews wait on P6.**
   > `components/attachments/attachment-panel.tsx` lists what this conversation was handed,
   > with the extraction status, the reader that produced it, what was seen and *not*
   > interpreted, and the fragment citations. It renders nothing until something is attached —
   > a permanent empty box above every composer is furniture, and furniture is what makes people
   > stop reading the area a real panel will appear in.
   > **"Insert as parameter" is the one affordance that is not built, and that is task 3's
   > decision reaching the UI rather than an omission.** The values it would insert are
   > candidate readings; a button that turned one into a design parameter would be the product
   > acting on an unverified number, which is precisely what "a wrongly read tolerance is worse
   > than an unread one" forbids. It becomes buildable when dimension extraction stops being
   > staged. Inline previews of tables and geometry are P6's viewer, not this panel's.
   > Upload progress and drag-drop remain as they were — `AttachPill` and `chunked-upload.ts`
   > already carry the geometry path, and the document path reuses it.
   > Tested by: `../Kryova-frontend/src/components/attachments/attachment-panel.test.tsx` (9).

7. **What was attached reaches the agent's turn.** *(added 2026-09-15)* A `READY` attachment
   in the conversation is quoted into the user turn through `quote_for_user_turn`, which
   Decision 8 makes the only way payload characters reach a model. Each fragment carries its
   citation, and an `INFERRED` read carries `UNVERIFIED_NOTE` inside the quote. A read tool
   returns fragments beyond the turn's budget by locator. Without this, task 4's first sentence
   ("enters the conversation as quoted material") describes a path nothing takes, and the phase
   proof's load-case spreadsheet cannot be read by the agent at all.
   > DONE (2026-09-15) — **the spreadsheet now reaches the model, quoted and cited, and the
   > path is the only one there is.** The tests were written on Linux and **not run** (the
   > user's rule; Windows runs them, THE QUEUE D4). `py_compile`, an import of `app.main`, the
   > frontend's `tsc --noEmit` and a scripted exercise of the quoting path were run, so no
   > guard here has been *seen* to fail.
   > **Where it happens.** `app/ai/attached.py::for_turn` is read in `stream_agent` *before*
   > the user's message is appended, and the block it returns is passed to `build_messages`
   > for every round of that turn. It is **not persisted**: the inventory is rebuilt each
   > turn, so storing it would leave one copy per turn in the transcript and replay all of
   > them on the next. The record of what was attached is the `Attachment` row — the argument
   > `app/ai/resume.py` makes about `CatiaOperation`.
   > **Three decisions, each a trade-off rather than a discovery.** *(1) Content is quoted on
   > the turn after it was attached; the inventory goes every turn.* Quoting everything every
   > turn is 12,000 characters per step against a local model where prompt re-processing
   > dominates (CLAUDE.md testing item 11), and quoting once with no way back is what
   > `resume.py` exists to prevent, because the window trims. So: content once, a one-line
   > inventory always, and `read_attachment` for the rest. *(2) "New" is "created since the
   > user last spoke"* — no column, no migration, exactly the right question, and a tie
   > quotes twice rather than never. *(3) A `FAILED` or `UNSUPPORTED` attachment is named
   > with its reason*, because an agent silent about the PDF someone dropped in reads as the
   > product having ignored it.
   > **The read tool.** `read_attachment` reaches any fragment of any of the caller's
   > attachments by `where` (the locator as the citations spell it), `contains`, `offset` and
   > `limit`. Read-only. Access is the **owner's**, not the project's membership: an
   > attachment is a file a person handed over, and a shared conversation does not make one
   > member's datasheet readable by another. A foreign id and a made-up one get one sentence.
   > **A new boundary type.** A tool result is not a user turn, so `quote_for_tool_result`
   > returns a `ToolResultBlock` whose one accessor is `into_tool_result()`. Returning a
   > `str` would have made `system_prompt() + quote_for_tool_result(...)` compile, which is
   > the mistake `UserTurnBlock` exists to make unwritable.
   > **Found and closed on the way: the AST guard `quoted.py` promised did not exist.** Its
   > docstring had said since P4.5 that `tests/test_documents_injection.py` "walks the AST of
   > every module under `app/` and fails if this name is called outside this package". Nothing
   > walked anything, and `app/core/attachments.py` had been calling `raw_for_analysis` since
   > P4.1. The walk is now written, with that module on a two-entry allow-list and a test that
   > the matcher can see the construction it looks for. A claimed guard that does not exist is
   > worse than an absent one (CLAUDE.md *Do not* item 8).
   > **Interface changes.** `build_messages` takes `attached=`; `quote_for_user_turn` takes
   > `notes=`; `serialise` stores a structured `locator` per fragment beside the rendered
   > `where` (rows written before today quote fine and their header degrades to the filename,
   > because a locator parsed back out of prose would sometimes be wrong). One new agent tool.
   > No migration, no enum, no route.
   > **Flagged, not fixed:** the inventory counts against no budget, so a conversation with
   > many large filenames spends context on names; `MAX_LISTED` (20) bounds it and nothing
   > measures it.
   > Tested by: `tests/test_attachments_turn.py` (30, written on Linux and not run), and
   > `tests/test_documents_injection.py::TestTheOneAccessorIsNotCalledWhereItShouldNotBe`.

   <!-- superseded 2026-09-15 -->
   > NOT STARTED — found 2026-09-15: nothing outside `app/documents` calls
   > `quote_for_user_turn` or `render_into_user_message`, and `routes/ai.py` never reads an
   > attachment. Today the agent learns about an attachment only from the user's words and
   > from `import_geometry_from_attachment`'s refusal, which lists what is attached.

**Phase proof:** the hostile-document suite passes; a load-case spreadsheet becomes a named,
provenance-tagged load case applied to a design; a STEP attachment becomes geometry through the
same kernel as everything else.

##### Phase P5 — The conversation and agent experience #####

> ✅ PHASE COMPLETE (2026-09-10) — all seven tasks done and tested, and every one of the three
> residuals the 2026-09-10 partials named was in the *backend*, not the client: token streaming
> needed a provider contract with a shape for a partial answer, reconnect-and-resume needed an
> event cursor that survives a different worker, and the editable spec panel needed a
> `DesignSpec` the server actually stores. All three landed the same day. One thing this phase
> deliberately does **not** deliver: a percentage inside a solve. `app/simulation/progress.py`
> reports stage, and grid *k* of *n* for a study, and refuses a numerator with no denominator —
> for a linear-static run CalculiX reports a single increment, so a fraction would be invented.
> That is a scope decision recorded here rather than an open task.

**~5 engineer-months. Continuous; the frontend face of E4, E5 and E16.**

**The question it answers:** does working with the agent feel like working with a competent
colleague — legible, interruptible, honest about uncertainty — rather than watching a terminal
scroll?

1. **Streaming done properly**: token streaming and step events over SSE (fits the existing
   poll-schedule/api-client machinery; WebSockets only if bidirectionality is ever actually
   needed), reconnect-and-resume — `conversation-resume` exists and is tested, so extend, don't
   replace.
   > DONE (2026-09-11) — both halves built and now **driven through the GUI on the Windows
   > seat**, which found that one sentence of the status below was false and load-bearing.
   > **"Tool calls arrive on the final Ollama chunk" is wrong, and `stream_chat` was written to
   > it.** Measured on the seat against Ollama and `qwen3.5:9b`: a 102-chunk reply carried the
   > tool call whole on **chunk 101 with `done: false`**, and the `done` chunk that followed
   > carried none. Reading only the final object therefore discarded **every** tool call, so the
   > agent saw a text-only turn and the product answered "I'll create the part…" with **zero
   > steps run** — reproduced on three prompts, one as simple as a 40 mm cube. The entire
   > local-model path did nothing, and had done nothing since P5.1 landed.
   > It was invisible from below by construction: the non-streaming `chat` path is fine because
   > the whole body carries the calls, and that is the path every provider test drove; a mocked
   > stream written to the same wrong assumption passes; and **nothing goes red** — the turn
   > completes and only the "nothing measured this" footnote hints that no work happened.
   > Isolated rather than guessed: raw Ollama emits a correct call with 1 tool, with 37 tools,
   > and through Kryova's full 23,369-character system prompt non-streamed; only the streaming
   > assembly loses it. Calls are now collected from *whichever* chunk carries them, and a call
   > repeated on both a mid-stream chunk and `done` runs once.
   > The rule that survives unchanged is the one that matters: `Finished.turn.text` is the
   > answer and the deltas are never reassembled — the reason is simply that the calls are not
   > *in* the deltas, not that they are on the last chunk.
   > **The frontend half of the same turn was also wrong**: `use-agent-chat.ts` carried
   > `stopReason` through an allow-list of three, so E16.4's `needs_input` and P5.5's
   > `awaiting_approval` were dropped and a turn that stopped at step 12 of 60 to ask a question
   > was captioned "ran out of tool rounds. Ask for one thing at a time".
   > Tested by: `tests/test_ollama_streaming_tool_calls.py` (5, one of them the double-count
   > guard), `../Kryova-frontend/src/components/chat/chat-view.test.tsx` (2 added). Verified by
   > breaking both, and end to end through the browser — the prompt that ran 0 steps before the
   > fix ran 6 after it. Run: `docs/verification-2026-09-10-night/`.

   <!-- superseded 2026-09-11 -->
   > DONE (2026-09-10) — both halves built, and each was exactly where the superseded status
   > said it was rather than in the client.
   > **Token streaming is a provider-contract change** (`app/ai/provider.py`): `stream_chat`
   > yields zero or more `TextDelta` then exactly one `Finished`, and the Ollama provider
   > implements it against `/api/chat` with `stream: true`. Two decisions carry the design. The
   > default implementation on the base class emits **no deltas at all** rather than the whole
   > answer as one — otherwise "the model wrote this in one go" is indistinguishable from "this
   > provider does not stream", and the UI renders the text twice. And `Finished.turn.text` is
   > the answer, never the joined deltas: tool calls arrive on the *final* Ollama chunk, so a
   > caller reassembling deltas would hold a second copy that drifts, and would silently drop the
   > half of the turn that does the work. A stream that breaks part-way falls back to one whole
   > non-streaming request, which is only safe *because* of that rule.
   > **Reconnect-and-resume is a table**, `turn_events`, and it is the mirror of P5.6's
   > cancellation column: there the reader had to escape the runner's long transaction, here the
   > *writer* does. A reconnect lands on whichever worker is free, so an in-process ring buffer
   > would resume perfectly under `--workers 1` and nothing in production. Every event is
   > recorded before it is yielded — so an event the client saw is always one that is stored —
   > and carries a `seq` monotonic **per conversation, not per turn**, because a cursor has to be
   > unambiguous across a turn boundary. `GET /ai/conversations/{id}/stream?after=N` replays and
   > then follows; it is a **GET**, which is what makes it a resume rather than a second turn,
   > and `tests/test_turn_events.py` asserts no POST exists at that path.
   > **A retention gap is reported, never smoothed over.** Events are kept ten minutes; a client
   > away longer is sent `resume_gap` and told to reload, because a turn handed back with a
   > silent bite out of the middle renders as an agent that skipped three steps. Recording is
   > also allowed to fail without taking the turn down — the `seq` is then omitted rather than
   > invented, and the client reads a missing cursor as "cannot resume from here".
   > Tested by: `tests/test_turn_events.py` (16), `tests/test_ai.py`. Code:
   > `app/ai/turn_events.py`, `app/ai/provider.py`, `app/ai/providers/ollama.py`,
   > `../Kryova-frontend/src/lib/agent-stream.ts`.

   <!-- superseded 2026-09-10 -->
   > PARTIAL (2026-09-10) — step events stream over SSE and `conversation-resume` rehydrates a
   > reopened conversation; both are tested. **Two halves are genuinely absent and neither is a
   > frontend gap.** *Token* streaming: the wire carries whole `narration` and `message` events,
   > because `app/ai/provider.py` returns a completed turn — streaming tokens means changing the
   > provider contract, not the client. *Reconnect-and-resume mid-turn*: a dropped stream loses
   > the live view, though nothing is lost from the record — every step is persisted as it
   > happens, so reopening shows what really ran. Closing it needs an event cursor on the wire so
   > a reconnect can say where it got to.

2. **The step surface**: `agent-step-list` grows into the run view — plan steps, live geometry
   operations, solver progress, per-step timing, failure taxonomy classes surfaced in plain
   language.
   > DONE (2026-09-10) — all five named surfaces render; the PARTIAL below already recorded the
   > fifth landing and was left unconverted. Plan steps, live geometry operations and per-step
   > timing are in `agent-step-list`; the failure taxonomy reaches the results page through
   > `job.error`; and solver progress is written by `app/simulation/progress.py` and rendered on
   > the simulation page by `describeProgress` as a stage — grid *k* of *n* only for a study, and
   > "Meshing and solving" for a run that has not reported, never stage zero. The page's rendering
   > of it had no test until this entry; three were added.
   > Tested by: `tests/test_simulation_progress.py` (10),
   > `../Kryova-frontend/src/app/dashboard/projects/[projectId]/simulations/[simulationId]/page.test.tsx`
   > (`SimulationPage progress`, 3), `../Kryova-frontend/src/components/agent-step-list.test.tsx` (20).

   <!-- superseded 2026-09-10 -->
   > PARTIAL (2026-09-10) — four of the five named surfaces are there. Plan steps and live
   > geometry operations render in `agent-step-list`; per-step timing is on every row
   > (`durationMs`); and the **failure taxonomy already reaches the user in plain language** by a
   > route that was easy to miss — `app/solve/calculix/diagnose.py` classifies a failed `ccx`
   > run, `solver.py` raises `SolverError(_with_evidence(failure))`, the runner writes that into
   > `job.error`, and the results page renders it. So a singular system arrives as the missing
   > restraint rather than as `returncode 201`. The run view also gained this phase's task 4, 6
   > and 7 surfaces.
   > **Live solver progress landed 2026-09-10, and it is a stage report rather than a bar.**
   > `app/simulation/progress.py` writes `{stage, detail, index, total}` onto the job — meshing,
   > solving, reading, storing — and for a convergence study, which grid of how many. It
   > **carries no percentage inside a stage**, and a test asserts that: for the linear-static
   > workload CalculiX reports a single increment, so a fraction would be invented, and an
   > invented bar over a twenty-minute solve teaches a user to predict a finish time nobody
   > measured. A count is refused unless both halves are present — a numerator with no
   > denominator is not progress. Every write opens **its own session**, the mirror of P5.6's
   > cancellation read: the runner holds one transaction for the whole job, so a progress update
   > written inside it would be invisible until the run was over. A failed progress write is
   > swallowed; it is worth a few seconds of a watcher's patience and never a run.
   > Tested by: `tests/test_simulation_progress.py` (10). Code: `app/simulation/progress.py`,
   > `app/simulation/runner.py`, migration `b06890354021`.
   > Superseded 2026-09-10: "steps and transcript exist" undercounted what shipped and named no
   > residual.

3. **The design as an artefact, visibly.** The spec (the IR) rendered beside the chat: parameters
   editable with units checked, features with their rationale notes, references navigable; **spec
   diffs rendered like code review** (what changed, what it reaches — `diff.py` already computes
   both). The conversation is the *log*; the spec is the *truth*; the UI must make that hierarchy
   legible.
   > DONE (2026-09-10) — **the diff half was built first; the editable panel followed once the
   > spec was persisted.** `components/gates/spec-diff.tsx` renders `SpecDiff.to_dict()` as a code review
   > and the gate page is its first caller. It computes nothing: `app/design/diff.py` already
   > works out both halves, and a second implementation on the client would be a second answer to
   > "does this change the part" — the two would disagree the first time somebody edited a
   > feature's rationale note, which moves both digests while building a byte-identical part.
   > `plan_changed: false` is therefore rendered as the useful answer it is ("this edit builds the
   > same part"), not as an empty state.
   > **`downstream` is kept visibly apart from `changed_calls`**, under different wording, because
   > that is the column a reviewer misses: nobody edited those features, they stand on something
   > that was edited, and they may come out a different shape. A review showing only what was
   > typed is the one that approves a wall thickness and silently moves the bore pattern cut into
   > it.
   > **The blocker named above was removed on 2026-09-10 and the panel is built.**
   > `DesignSpec` is persisted: `app/models/design.py` holds one `DesignDocument` per
   > conversation with an **append-only** `DesignRevision` chain, and
   > `components/design/spec-panel.tsx` renders it beside the composer with the parameters
   > editable.
   > **Three rules make the chain a history rather than a log of saves.** A save whose spec is
   > byte-identical to the head writes *nothing* — the agent re-saves after every step and most
   > steps do not touch the design, so appending each time gives six hundred revisions of which
   > four matter. A revision's summary is written **at the time** from `app/design/diff.py`, not
   > derived on read: recomputing means recompiling both specs to render a list, and gives a
   > *different answer* after an operation registry change, because the diff is made after
   > compilation on purpose. And a spec that does not compile is refused **before** the chain is
   > touched, so a refusal never leaves the design in a state no build produced.
   > **The client cannot POST a spec, and a test asserts no such route exists.** The only write
   > is a single parameter (`PATCH /designs/{id}/parameters/{name}`); a route taking a whole spec
   > would let a client author a design the server never compiled, and the first malformed one
   > would arrive as a compile error against a revision already written. A *derived* parameter is
   > refused with the formula named, and the panel disables that field rather than letting it
   > fail on submit — showing `= thick_mm / 2` in place of an input says what to change instead,
   > which an error afterwards does not.
   > The panel computes nothing: it renders the diff the server returns, and names the
   > `downstream` features by name — the ones nobody edited that stand on something that was.
   > Tested by: `tests/test_designs.py` (35), `src/components/design/spec-panel.test.tsx` (9),
   > `src/components/gates/spec-diff.test.tsx` (6). Code: `app/models/design.py`,
   > `app/core/designs.py`, `app/api/routes/designs.py`, migration `1ef5f6401c1f`.

   <!-- superseded 2026-09-10 -->
   > PARTIAL (2026-09-10) — the diff half only; the editable panel was blocked on a persisted
   > spec.

4. **The verification surface**: assertion dashboard (pass / fail / **unmeasured** rendered as
   first-class — unmeasured is amber, never green), requirement coverage, provenance drill-down
   from any number to its evidence chain (E7 task 3), convergence badges on simulation results.
   > DONE (2026-09-10) — `components/verification/verification-panel.tsx`:
   > `VerdictBadge`, `ConvergenceBadge`, `ProvenanceNote` and `VerificationSummary`, wired into
   > the results page **above the numbers, not below them**. "Can this number be leaned on" is a
   > question to answer before somebody reads a factor of safety, not a footnote under one they
   > have already believed.
   > **Unmeasured is amber and never green**, which is Decision 3 rendered and is what three of
   > the tests assert from different directions. A single-grid solve earns `one mesh —
   > unverified`, however fine the mesh was, because a single solve holds no evidence about its
   > own discretisation error — measured at gate G1, where a factor of safety of 1303 was reported
   > off one 411-element tet4 mesh. `converged` is the only state that earns green.
   > The summary names what the result is *bound to* — solver, version (or "version not
   > recorded", never a guess), geometry version, element size and order — because a result bound
   > to nothing is a result nobody can reproduce, which Decision 3 treats as no result. Tested by:
   > `src/components/verification/verification-panel.test.tsx` (14), each verified by breaking the
   > thing it guards.

5. **Approval gates as UI**: a gate is a page — the diff, the affected assertions, the cost/time
   estimate of what follows, an approve/reject with the actor recorded (P2 domain roles; P3 audit).
   Not a chat message that scrolls away.
   > DONE (2026-09-10) — `app/models/gates.py`, `app/core/gates.py`, `app/api/routes/gates.py`,
   > migration `01f9a909fad0`, and `/dashboard/approvals` with `SpecDiff` as its evidence panel.
   > **A gate is a row, and that is the whole design.** Asking in the conversation and reading the
   > answer back out of it fails three ways a row does not: the transcript is trimmed (`ai/resume`
   > exists because of exactly that), an LLM paraphrase of an approval is not an approval, and
   > nobody can afterwards answer "who signed this off and what did they see" — which is the
   > question actually asked, and only ever after something has gone wrong.
   > Three rules, each pinned by a test that fails when the check is removed. **The subject is
   > pinned, not referenced**: `subject_digest` is taken when the gate is raised and `decide`
   > re-digests what the decider is looking at now, so an approval cannot land on a design that
   > moved while it was pending — a gate approving "the current spec" is a signature on a blank
   > page. **A rejection carries a reason**, because the agent's next move depends entirely on
   > why. **`EXPIRED` is not a fourth way of saying no**: `decided_by_id` stays null, because
   > filling it would put a name against a decision nobody made.
   > **This is the first reader of `Membership.domain_role`**, whose own docstring has said
   > "16.5/P5 read this column" since P2.2 while nothing did. `None` means *not stated* and does
   > not qualify — the column was made nullable precisely so that could be said. `ALLOW_SELF_APPROVAL`
   > defaults false so a team gets four-eyes unconfigured, and exists at all because most
   > self-hosted installs have one engineer and a review process that cannot be completed is one
   > people route around entirely, losing the record as well as the second opinion.
   > Tested by: `tests/test_gates.py` (24).

6. **Interruption and steering**: stop a run cleanly (the bounded loops make this safe), edit a
   parameter mid-mission, resume without loss.
   > DONE (2026-09-10) — `app/core/interruption.py`, `POST /ai/conversations/{id}/cancel`,
   > `POST …/simulations/{id}/cancel`, and a two-press stop button in the composer.
   > **The signal is a database column, not an in-process flag**, and that is the load-bearing
   > decision: whoever presses stop is served by one worker and the turn is streaming from
   > another, so an in-memory registry works perfectly under `--workers 1` and silently does
   > nothing in production — the worst available failure shape for a button people press when
   > something is already going wrong. Both readers go to the database rather than to an
   > attribute their session loaded, and `tests/test_interruption.py` simulates the stale reader
   > explicitly.
   > **The frontend already had a stop and it did not stop anything.** It aborted the fetch, and
   > its own comment admitted "stopping the stream does not stop the seat" — the agent carried on
   > driving CATIA with nobody watching. Now the first press asks the loop to stop and the client
   > *keeps listening*, so the loop ends at its next step boundary, writes what happened into the
   > transcript and closes the stream itself. The old abort survives as the second press, for a
   > stream that has gone quiet.
   > **Two honest partials, both stated in the product rather than smoothed over.** A tool call
   > already in flight finishes: half an applied CATIA operation is worse to own than four more
   > seconds of waiting. And a simulation stops at a *stage* boundary — after meshing, between the
   > grids of a study — because meshing and solving are each a single call into gmsh or CalculiX
   > this process cannot reach into, so a solve already handed to CalculiX finishes **and is
   > still billed**. The button says so. A cancel does not make machine time retroactively free,
   > and implying it did is the kind of small lie a billing dispute is built out of.
   > `JobStatus.CANCELLED` is terminal and is **not** a kind of `FAILED` — a failure is the
   > product not working, a cancellation is it doing what it was told, and grouping them puts a
   > user who changed their mind into the fleet's failure rate.
   > **"Edit a parameter mid-mission" was the one residual and it landed 2026-09-10**, once the
   > design record existed for it to edit. A parameter change is a revision like any other — so a
   > run in flight sees it the next time it reads the spec, and an audit afterwards sees who
   > moved it and what moved with it. The `author` column says *user* or *agent* and null
   > `author_id` means the agent rather than "unknown": conflating the two makes "did a person do
   > this" unanswerable, which is the question an audit of a signed-off design is entirely about.
   > Both surfaces exist — the panel's field and the agent's `set_design_parameter` — and both go
   > through `app/core/designs.set_parameter`, so the rule refusing a derived parameter cannot
   > soften on one of them.
   > Tested by: `tests/test_interruption.py` (16), `src/components/chat/chat-view.test.tsx` (3
   > new), `src/types/api.contract.test.ts`.

7. **Cost/time honesty**: before a long run, the estimate (E16 task 6 / P8); during, elapsed vs
   estimate; after, actuals — the trust habit that makes P8's billing uncontroversial.
   > DONE (2026-09-10) — `components/verification/cost-notice.tsx`: `CostNotice` above the run
   > button on the simulate page, `ElapsedAgainstEstimate` for a run in flight.
   > **The number comes from the meter that bills**, which is P8.4's one-meter rule reaching the
   > screen. The component does no arithmetic at all: the backend projects from the tenant's own
   > recorded runs and returns a finished `sentence`, and rendering that sentence is what stops
   > the estimate and the bill from drifting apart. Assembling our own from `units` and `unit`
   > would be a second place for the wording — and the honesty — to diverge.
   > **Too little history is shown as such, never as zero and never hidden.** "We cannot estimate
   > this yet, because you have run two simulations and we need three" is useful; a blank where a
   > cost should be is not, and an invented figure is worse than either, because a cost estimate
   > is the one number in a product nobody contradicts afterwards — which is exactly why a made-up
   > one survives. Overrun is a fact rather than an alarm: the estimate is a median, so half of
   > all runs exceed it by construction, and colouring that red would teach people the product is
   > usually broken. It turns amber at twice the estimate, where the run genuinely is unusual.
   > **`ProjectRead` gained `organisation_id`** for this: the estimate is per tenant and a page
   > holding only a project id could not ask what a run would cost. `owner_id` is not a
   > substitute — since P2 a project belongs to an organisation and can be transferred between
   > them.

##### Phase P6 — The viewer at machine scale #####

**~6 engineer-months. Grows with E1 (tessellation source), E14 (product structure), P4 (CAD
attachments).**

**The question it answers:** can a user *see* a 2,000-part machine in the browser — orbit it
smoothly, open its tree, section it, measure it, watch stress paint onto it — on an ordinary
laptop?

**The stance:** the hand-written WebGL 1 viewer is a strength for what it does today (a part, a
stress field) and respects the frontend's minimalism doctrine. Machine scale is a different problem
class: **server-side preparation, client-side streaming.** The kernel we now own tessellates; the
client renders what it is sent, at the detail the view deserves.

1. **The tessellation service** (backend): OCCT shape → glTF with **Draco** (70–95% geometry
   reduction, 14-bit quantisation) or **meshopt** (lossless, GPU-direct decode) — measure both on
   real Kryova parts and pick per-asset; multi-LOD generation at export; instancing extraction
   (BOLTS parts repeat thousands of times — one mesh, N transforms). Cached in the
   content-addressed store keyed on geometry digest + tessellation params, so a part is tessellated
   once ever.
   > PARTIAL (2026-09-16) — **the assembly half is served too: a product structure is now one GLB
   > scene.** `GET /kernel/conversations/{id}/assembly/scene?level=0|1|2` draws the assembly a
   > conversation is composing — each leaf component tessellated once, each occurrence a node on
   > it, so **forty bolts are one bolt's bytes**. That is the instancing extraction the task asks
   > for, and it falls out of the glTF node graph rather than being a pass over it: measured on a
   > real bench assembly, eight further occurrences of an existing component grew the file by
   > **908 bytes**. `scene_for` had existed since 2026-09-15 with no caller anywhere outside a
   > test — the same integration gap `app/api/routes/kernel.py` was created for one layer down.
   > **The level means what it means for a part, and that is a decision**: each component is
   > tessellated by `display.display_mesh` against *its own* bounding-box diagonal, so a bolt is
   > as smooth relative to itself as the frame is. Deflecting everything against the assembly's
   > diagonal would make a 5 mm bolt in a 3 m machine coarser than the bolt — not a level of
   > detail but a different part. The stated consequence is that **an assembly GLB at level N is
   > the union of its distinct components at level N**, so this route does not make a 2,000-part
   > machine cheap; choosing a level per component by screen coverage is P6.2's streaming
   > question and lives in the client. `X-Assembly-Triangles` reports the number so a caller sees
   > it rather than infers it. Measured on a bench of one flat rail and two identical bushings:
   > **512 / 228 / 108 triangles** at levels 0 / 1 / 2, in 14,148 / 7,344 / 4,464 bytes, and the
   > same assembly draws to the same bytes twice.
   > **Refused by name:** a conversation with no assembly (a part is not an assembly of one);
   > an assembly with nothing placed in it (with no instances the walk yields the root as its own
   > leaf occurrence); a component with no geometry, named — a machine drawn without a part looks
   > complete and is not; a level outside 0–2; the CATIA backend; another user's conversation,
   > 404 rather than 403.
   > **Not cached, deliberately**: the part route keys its GLB on the stored file's sha256, and an
   > assembly held in memory has no stored bytes to key on — a key computed from the meshes would
   > cost the tessellation it was meant to save. A cache arrives with persistence, which
   > `assembly_ops` records as needing a model and a migration.
   > **Interface changes:** a new route; `OcctRunner.assembly` (a conversation can hold two things
   > now, and a route reading `_context` directly would be the first caller outside the class to
   > know its shape); `assembly_ops.product_of` as the public way to a validated structure.
   > **Still open:** Draco and meshopt (not installed, unmeasured on real parts); a miss builds
   > the GLB in memory; display rows outlive a deleted project's files; display rows appear in
   > `GET /media`; no assembly is persisted, so the scene dies with the conversation.
   > **Not run here**: tests written on Linux on 2026-09-16 and not executed as pytest, at the
   > user's instruction; all 40 claims were measured first against the real kernel and the real
   > route function. Tested by: `tests/test_kernel_routes.py::TestDrawingAWholeAssembly`,
   > `tests/test_render_gltf.py`, `tests/test_geometry.py::TestTheDisplayMesh`.

   <!-- superseded 2026-09-16 -->
   > PARTIAL (2026-09-15, afternoon) — **a stored STEP version is now served as a GLB, made once
   > per file and level; compression is still unmeasured.** The route is
   > `GET /projects/{id}/geometry/{n}/display?level=0|1|2`. `app/render/display.py` defines three
   > levels. Each level's linear deflection is a fraction of the bounding-box diagonal (0.0002,
   > 0.001, 0.005) and its angle also coarsens (0.1, 0.35, 1.0 rad), because the tighter
   > deflection decides. A Ø10 cylinder and one 300× larger both give 500/212/92 triangles.
   > **The key needs no shape:** the stored file's sha256, the level's definition and the GLB
   > layout version. So a hit is found without opening the file, and a second upload of the
   > same bytes is a hit too. The GLB is stored through `MediaService` as a `MediaKind.MESH` row
   > named `display-<key>.glb`, owned by the file's owner. `X-Display-Cache: hit|miss` and
   > `X-Display-Key` say which happened.
   >
   > **Refused:**
   > - IGES and STL versions, told to send STEP;
   > - a level outside 0–2;
   > - an unreadable file, with the reader's own message.
   >
   > **Open:**
   > - Draco and meshopt (not installed, and unmeasured on real parts);
   > - an assembly GLB from the product structure through a route (`scene_for` exists, and no
   >   route stores components);
   > - a miss builds the GLB in memory;
   > - a display row outlives a deleted project's files until its owner is deleted (nothing
   >   sweeps it);
   > - the display rows appear in `GET /media`.
   >
   > **Interface change:** a new route, and `MediaKind.MESH` is used for the first time (the enum
   > value already existed; no migration). Tests written on Linux and not run as pytest. The
   > levels, the scale invariance and a gmsh-written STEP read from a digest-named path were
   > checked by a one-off script. Tested by: `tests/test_render_gltf.py`,
   > `tests/test_geometry.py::TestTheDisplayMesh`.

   <!-- superseded 2026-09-15 -->
   > PARTIAL (2026-09-15) — tessellation, levels, instancing, GLB and the key shipped; compression
   > and the store did not. `app/kernel/occt/tessellate.py` meshes a `BRepBuilderAPI_Copy`, so the
   > caller's shape keeps no triangulation. It applies each face's location and rewinds REVERSED
   > faces; a 10×20×30 box encloses exactly 6,000 mm³. `levels_of_detail` gives one mesh per
   > deflection. **The tighter deflection decides**: at 0.05 rad a Ø10 cylinder was 1,004
   > triangles at 0.01, 0.1 and 1 mm alike, and at 1.5 rad 280/88/32. `app/render/gltf.py` writes
   > GLB from the glTF 2.0 specification (read 2026-09-15). Vertex data stays mm and Z-up, and one
   > root node carries ×0.001 and Z→Y with a positive determinant, because glTF flips winding
   > under a negative one; a mirrored instance frame is refused for that reason. `scene_for` makes
   > each leaf component one mesh and each occurrence one node named by its path. Forty bolts are
   > one bolt's bytes. Output is byte-deterministic. `cache_key` covers geometry digest, both
   > deflections and a layout version. **Core glTF has no LOD, so it is one file per level.**
   > **Open:** Draco and meshopt are not installed, and the task asks for both measured on real
   > parts before choosing. Nothing stores the GLB under the key or serves it (no route), and
   > nothing yet supplies a *shape* digest to key on. The frontend half is P6.2.
   > **Interface change:** `occt/binding.py` registers `BRepBuilderAPI_Copy`,
   > `BRepMesh_IncrementalMesh`, `TopAbs_Orientation` and `TopLoc_Location`.
   > Tests written on Linux and not run as pytest (user's rule); the volume, instance count,
   > untouched caller shape, location offset and level counts were checked by a one-off script.
   > Tested by: `tests/test_render_gltf.py`.

   <!-- superseded 2026-09-15 -->
   > NOT STARTED.

2. **The streaming scene** (frontend): assembly loads structure-first (the tree and bounding boxes
   immediately), meshes stream by priority (frustum + screen-space size), LOD switches by distance.
   Target: first meaningful paint of a 2,000-part machine under 2 s on a mid-range laptop;
   interaction never below 30 fps, measured in CI against a reference assembly — a performance
   *assertion*, in the house style.
   > PARTIAL (2026-09-15) — **the ordering is built and pure; the fps and first-paint targets
   > are unmeasured and are a hardware job.** `../Kryova-frontend/src/lib/scene-streaming.ts`.
   > Tests written on Linux and **not run** (the user's rule); `tsc --noEmit` clean. QUEUE G1.
   > **The problem at machine scale is ordering, not drawing**, so that is what this is: what
   > to fetch next and at which level, as arithmetic over bounding boxes and a camera, with no
   > WebGL, no fetch and no React in it. Pure for `app/design/`'s reason — the ordering is the
   > part worth testing and it is untestable once tangled with a GL context.
   > **Structure-first is a consequence rather than a phase.** A part with no mesh still has a
   > box from the tree, so it is drawn as a box and ordered like anything else; there is no
   > separate structure pass to fall out of step with the streaming one.
   > **Four decisions.** LOD thresholds are in *part radii*, not millimetres, so one table
   > serves an M6 nut and a 2 m weldment — a millimetre threshold loads the weldment at its
   > coarsest level from across the room. A part already at a finer level than it now deserves
   > is **left alone**, so flying away never spends a round trip on a worse mesh. A part under
   > ~8 px gets no mesh at all, which on an assembly is most of the parts. Culling is a
   > half-space test against the view direction, not a six-plane frustum: the cheap half is
   > what matters on an interior view, and a full frustum needs the projection matrix, which
   > belongs to the renderer this module stays out of.
   > **The comparator is total**, including for parts containing the camera, because
   > `Infinity - Infinity` is NaN and a NaN comparator leaves the order to the engine — a
   > viewer that loads something different first on every frame.
   > **No runtime dependency added** (the doctrine is three).
   > **Not done here:** wiring it to the viewer and the `?level=` route, and the targets
   > themselves — *first paint of a 2,000-part machine under 2 s, never below 30 fps* — which
   > are measurements on real hardware against a reference assembly that does not exist yet.
   > The task asks for them in CI; CI has no GPU, so where that assertion runs is an open
   > question recorded here rather than answered.
   > Tested by: `../Kryova-frontend/src/lib/scene-streaming.test.ts` (20, written on Linux and
   > not run).

   <!-- superseded 2026-09-15 -->
   > NOT STARTED.

3. **The renderer decision, made explicitly**: extend the hand-rolled WebGL viewer to WebGL 2
   (instancing, better attribute handling) as the default path — it keeps the dependency doctrine
   and the team knows every line. Adopt a library only if 6-months-in measurements show the
   hand-rolled path cannot hold the fps target on M5-class assemblies; that decision point is
   scheduled and its criteria written now, so it is a measurement, not a mood. WebGPU is the
   follow-on where available, behind capability detection.
   > PARTIAL (2026-09-16) — **the decision point is scheduled and its criteria are written,
   > which is what this task actually asks for; nothing has been adopted and nothing has been
   > measured.** `docs/RENDERER_DECISION.md`.
   > **Date: 2027-03-16**, six months after the first viewer task landed, with two named
   > early triggers (two successive months past the fail threshold, or a P6 task needing more
   > than two engineer-weeks of something a library supplies outright). Six criteria with
   > pass/fail bands and a deliberately wide *marginal* middle — first paint, 95th-percentile
   > and worst frame time, section latency, GPU memory, and pick-to-highlight — each a median
   > of five runs, never one.
   > **Two prerequisites are missing and are recorded rather than assumed**, which is the
   > substantive finding here and both are now in QUEUE G1:
   > - **The reference assembly does not exist and cannot be M5.** The plan names M5's
   >   stamping press; M5 is not built and is blocked on E13, and the largest assembly this
   >   repository can produce today is **M6 at three components** against a 2,000-part target
   >   (measured 2026-09-16). So §4 defines the assembly by its *properties* and permits a
   >   synthetic one, labelled synthetic in the run log.
   > - **No reference laptop is named anywhere in this repository** (checked 2026-09-16).
   >   P6.2 says "a mid-range laptop", the phase proof says "the reference laptop", and
   >   neither is a machine. A frame-time threshold with no hardware behind it is not a
   >   threshold.
   > **If either is still missing on the date, the outcome is "no decision", recorded** —
   > deciding without the measurement is exactly what writing the criteria in advance is for.
   > §7 lists what is explicitly *not* a reason to adopt a library, including the two that
   > have historically ended this argument: a hard bug, and the renderer being unpleasant to
   > work in.
   > **The default stance is stated and argued**: keep the hand-rolled WebGL 2 path, because
   > the viewer's hard problem is *what to fetch and at what detail* (`scene-streaming.ts`),
   > which no library helps with — a library would replace the part that works and leave the
   > part that is hard.
   > **Not done here:** the WebGL 2 upgrade itself (instancing, attribute handling) and the
   > WebGPU capability-detection follow-on; the viewer is still the single-part WebGL 1
   > `webgl-stress-viewer.tsx`. This task is the *decision procedure*, and no measurement can
   > happen on Linux.

   <!-- superseded 2026-09-16 -->
   > PARTIAL — the single-part WebGL viewer exists and is tested. Code:
   > `webgl-stress-viewer.tsx`.

4. **Engineering interactions**: section planes, exploded views (from the assembly structure,
   animated), measure (point-point, edge, face-face — against real geometry via a backend query,
   not against the decimated mesh), hide/isolate by subtree, camera bookmarks per conversation
   ("the view we were talking about").
   > PARTIAL (2026-09-16) — **all five interactions have their logic, on both sides of the wire;
   > none of them has a control in the viewer.** Frontend
   > `../Kryova-frontend/src/lib/viewer-interactions.ts` (33 tests, written on Linux and not
   > run; `tsc --noEmit` and `eslint` clean). Backend
   > `GET /kernel/conversations/{id}/measure/between` and `.../measure/element`
   > (16 tests, written on Linux and not run as pytest; `py_compile` clean, both routes in the
   > OpenAPI document). QUEUE G3.
   > **Measure is a backend query and adds no measurer.** The routes call the live runner with
   > `catia_measure_between` and `catia_measure_item` — the operations the agent already has,
   > behind `BRepExtrema_DistShapeShape` and a real boolean for the overlap. The task's own
   > words are "against real geometry … not against the decimated mesh", and the reason is that
   > the viewer's mesh detail comes from screen size (task 2), so a browser-side distance would
   > be wrong by the chord error *and would change when the camera moved*. A second caller, not
   > a second implementation: two measurers agree today and disagree after a fix to one. Both
   > are `GET`s and neither operation is in `RECORDED`, so a viewer may poll one while the user
   > drags a selection without journalling a step into the part.
   > **The four client-side interactions are one module because they are one state**, and the
   > bookmark is why: a bookmark that stored only the camera restores a solid block where the
   > user saw a bore, or a machine with forty fasteners that were hidden when it was taken. It
   > carries the sections, the hidden set and the explode factor, and deep-copies both ways so
   > dragging a slider cannot rewrite a saved view.
   > **Four traps pinned.** A section plane's normal points at the material *removed*
   > (`catia_split`'s convention — two conventions for one question is how a part ends up
   > mirrored with every test green). Sides are decided on the box's **corners**, because a long
   > member through the cut reads as wholly on one side from its centre and then vanishes from
   > the section view. A component centred on the assembly's centre does not move when exploded,
   > because normalising a zero vector is `NaN` and a `NaN` translation removes the part from
   > the view with nothing raising — on a symmetric machine that part is the main shaft. **Hide
   > wins over isolate**, and an isolated root that is not in the tree shows nothing rather than
   > falling back to the whole machine.
   > **No runtime dependency added** (the doctrine is three).
   > **Not done here, and it is the larger half:** no control exists for any of it — no section
   > UI, no explode animation, no tree gutter, no bookmark panel — and bookmarks are persisted
   > nowhere (no model, no route; a bookmark today lives as long as the tab). Exploded views are
   > computed from bounding boxes rather than from the product structure's own frames, because
   > no route serves assembly components yet (task 1's open list says the same). And measure has
   > only one direction: an element **name** in, a number out. Turning a click on a triangle
   > into that name is E2 task 1's face predicate and does not exist — so the route is
   > reachable from the agent's vocabulary and not yet from a pick.
   > Tested by: `../Kryova-frontend/src/lib/viewer-interactions.test.ts` (33),
   > `tests/test_kernel_routes.py::TestMeasuringBetweenTwoElements`, `::TestMeasuringOneElement`
   > (16) — all written on Linux and not run.

   <!-- superseded 2026-09-16 -->
   > NOT STARTED.

5. **Results on geometry**: the existing stress-field rendering generalised — scalar fields
   (stress, displacement, thickness, fatigue damage) on the streamed meshes, shared colour-scale
   legend, probe-a-value. The `surface-field` code is the seed.
   > PARTIAL (2026-09-16) — **any per-node scalar field colours, with a legend that states its
   > own bounds and a probe that can say "not measured"; nothing is wired to the viewer yet.**
   > `../Kryova-frontend/src/lib/scalar-field.ts`. Tests written on Linux and **not run** (the
   > user's rule; Windows runs them); `tsc --noEmit` and `eslint` clean. QUEUE G2.
   > **It is a sibling of `surface-field.ts`, not a widening of it**, because that module is a
   > wire format (a packed binary header with a JSON fallback) and this one is presentation.
   > Five kinds — stress, displacement, thickness, damage, temperature — differ by a name, a
   > unit and a palette, never by a second renderer. `magnitudeField` is the bridge from what a
   > solve returns (xyz per node) to what a colour bar can show.
   > **Three honesty rules, each the reason a plausible picture would otherwise be wrong.** An
   > unmeasured node is `NaN` and gets `ABSENT` grey rather than the bottom of the scale, and it
   > is left out of the fitted range, because the bottom of the scale is a reading and "nobody
   > computed this" is not one; `probeNode` answers `measured: false` instead of 0 and refuses a
   > node index that is not in the field. A fitted range carries `auto: true` and the legend
   > prints the caveat, so a reader comparing two screenshots knows the colours do not mean the
   > same thing in both. **Damage is never fitted** — `FIXED_RANGES` pins it to 0–1, because
   > fitting 0–0.02 across the palette paints a part that will last fifty lifetimes in the same
   > red as one about to crack. Thickness's ramp is reversed on purpose (thin is the problem),
   > which is exactly the assumption a caller reading "high is red" everywhere would get wrong.
   > **No runtime dependency added** (the doctrine is three); the palettes are arithmetic.
   > **Not done here:** the viewer does not call it — no legend component, no probe UI, and
   > nothing routes a thickness or damage field to the frontend (thickness is sampled and
   > `app/fatigue/` has no per-node route). Those are the wiring half of this task.
   > Tested by: `../Kryova-frontend/src/lib/scalar-field.test.ts` (24, written on Linux and not
   > run).

   <!-- superseded 2026-09-16 -->
   > NOT STARTED.

6. **Tree ↔ 3D ↔ spec, one selection model**: click a part in the tree, it highlights in 3D and the
   spec panel scrolls to its feature; select a face in 3D, the predicate that would name it (E2
   task 1) is offered.
   > PARTIAL (2026-09-16) — **both directions have their logic and the 3D → name half is
   > built end to end; no surface calls any of it yet.** Frontend
   > `../Kryova-frontend/src/lib/selection-model.ts`; backend
   > `app/kernel/occt/propose.py`, the face partition in `app/kernel/occt/tessellate.py`,
   > and `GET /kernel/conversations/{id}/selection/face`.
   >
   > **The decision this task asked for, and the premise it was given was wrong.** The
   > continuation prompt said the 3D → name half was blocked on "E2 task 1's face
   > predicate, which does not exist". E2 task 1 is `DONE (2026-09-05)` and predicate
   > *selection* very much exists (`app/kernel/selection.py`, `occt/resolve.py`). What did
   > not exist is the **inverse**: given a face, the predicate that names it. So the
   > blocker was real and it was a different thing, and it is written here rather than
   > closed silently. It is now built, which also unblocks **P6.4's measure from a pick** —
   > that route takes an element *name* and there was no way to get one from a click.
   >
   > **A pick is answerable because every triangle now records its face.**
   > `TriangleMesh.face_of_triangle` labels each triangle with an ordinal into
   > `topology.faces()` — deliberately the de-duplicated map order that `resolve.py`
   > filters, not the order of the `explore_oriented` walk the mesher itself uses, so a
   > pick and a predicate are the same numbering. Verified geometrically rather than by
   > bookkeeping: each face's triangles reconstruct that face's own area (exact on the six
   > planes, within the chord error on the bore). Shifting the ordinal by one makes
   > `test_each_faces_triangles_reconstruct_that_faces_area` fail with face 0 reporting the
   > bore's 752.1 mm² instead of its 800.0 — watched, then restored and re-checked by hash.
   >
   > **The proposer generates liberally and verifies by resolving**, so it holds no
   > geometric reasoning of its own and cannot drift from `resolve.py`. A candidate that
   > does not select the pick is dropped, not corrected.
   >
   > **Four things it refuses to get plausibly wrong**, each measured on 2026-09-16:
   > - **A curved face is never named by a direction.** `face_normal` reads the normal at
   >   the parametric centre, which on a cylinder is wherever the seam fell — the bore of a
   >   60×40×20 plate reports (1, 0, 0) and is caught by `normal: "+x"` alongside the +x
   >   wall. On a part with no +x wall that candidate would verify as *unique* and be
   >   offered as the best name for a bore.
   > - **A face nothing describes says so.** `best` is `None` for one of two identical
   >   bores; the ambiguity is reported with its count ("selects 2 faces including this
   >   one") rather than a name meaning both.
   > - **A positional name is separated from a durable one.** An `inside` box always
   >   resolves uniquely, so a `best` that accepted one would never be `None` and the
   >   ambiguity would never surface. `best` requires `stable`; `positional` is its own
   >   property and carries the caveat.
   > - **The name offered is the engineer's, never the kernel's.** It offered
   >   `of: "Pad.1"` for a pad the user called `slab` until fixed — the positional
   >   fragility `app/design/` exists to remove, arriving through the UI instead of a plan.
   >   Found by probing the real runner: `_feature_owning` called `document.features()`,
   >   which does not exist, so `getattr(..., lambda: [])()` returned `[]` for every
   >   document and no feature name was ever offered. Nothing went red.
   >
   > **`display.display_mesh` is now the one definition of "the mesh at level N"**, because
   > a pick is a triangle index into the mesh the client was served; tessellating a second
   > time with deflections computed a second way lands the pick on a different face,
   > silently, since every index still exists.
   >
   > **The round trip is proved, not assumed:** the offered argument for the top face goes
   > straight into `catia_shell_faces` as `open_faces` and builds an 11-face shell.
   >
   > **Unpinned and labelled rather than claimed:** the ordinal is looked up in the map
   > instead of counted off the walk, which matters only where one face has two parents.
   > Five shapes were tried on 2026-09-16 (bored plate, two compounds, a fuse, a sewing)
   > and none produces that, so no test fails when the lookup is replaced by a counter.
   >
   > **Not done here, and it is the larger half:** no surface calls any of this. There is
   > no tree component, no spec panel wiring and no pick handler in the viewer — the same
   > open half P6.2, P6.4 and P6.5 carry, and QUEUE G4 is where it is measured.
   > **Interface change:** a new route, and `TriangleMesh` gains a field (defaulted, so a
   > hand-built mesh stays valid and refuses a pick by name).
   > Tests written on Linux and **not run** as pytest or vitest (the user's rule); the
   > claims were each checked against the real kernel by one-off script first, and
   > `py_compile`, `tsc --noEmit` and `eslint` are clean.
   > Tested by: `tests/test_kernel_propose.py` (15),
   > `tests/test_kernel_routes.py::TestNamingAPickedFace` (10),
   > `../Kryova-frontend/src/lib/selection-model.test.ts` (19).

   <!-- superseded 2026-09-16 -->
   > NOT STARTED.

**Phase proof:** M5's full assembly — structure paint <2 s, orbit at 30+ fps on the reference
laptop, stress overlay on the frame, a section through the die set, all in the browser; the same
scene in the Tauri app.

##### Phase P7 — The desktop app and the workstation bridge #####

**~3 engineer-months. Extends what exists; the CATIA bridge's natural home.**

1. **Signed auto-update**: Tauri v2 updater with the offline signing keypair; **private-key custody
   written down** (lost key = no more updates for installed apps, ever — key in a hardware token or
   sealed secret store, never CI plaintext); staged rollout channels (stable/beta); `latest.json` +
   signatures published per release (P9's pipeline builds it).
   > NOT STARTED — blocked behind P9 task 4's finding.

2. **The bridge, integrated**: the CATIA daemon (`scripts/catia_bridge/`) ships with/beside the
   desktop app on workstation installs; the `catia-bridge-panel` grows into a first-class status
   surface (connection, seat language, document binding, pending approvals). The tier/approval model
   already exists — the desktop UI is where destructive-tier approvals belong.
   > PARTIAL — the Tauri shell and bridge panel exist. Code: `src-tauri/`,
   > `catia-bridge-panel.tsx`.

3. **Desktop-only powers, used sparingly**: local file open/save into the attachment pipeline, OS
   notifications for long-run completion, deep links (`kryova://run/...`) from CI or email into the
   app.
   > PARTIAL (2026-09-16) — **the decidable half is written and the native half is not.**
   > `../Kryova-frontend/src/lib/desktop-powers.ts`. Tests written on Linux and **not run**
   > as vitest (the user's rule); `tsc --noEmit` and `eslint` clean, and all 23 claims were
   > executed against the real module with a one-off script.
   > **The deep link is the security half of this task, and it is treated as one.** The link
   > arrives from an email or a CI comment, the person clicking it cannot read it first, and
   > the app it opens is already signed in — the threat `safe-redirect.ts` handles for
   > `?next=`, arriving through the operating system instead of the browser, and worse in one
   > way because there is no URL bar. So `parseDeepLink` is an **allow-list of shapes**: three
   > read targets (`run`, `conversation`, `project`), exactly one id segment, every other
   > scheme and target refused by name. **Nothing a link reaches performs an action** — a link
   > that ran a simulation or accepted an invitation would be a one-click cross-site request
   > against an open session, so a link opens a page and the user presses the button.
   > **Two parsing facts measured against node's WHATWG `URL` rather than assumed:**
   > `kryova://run/x` parses with host `run` and path `/x` while `kryova:/run/x` has an empty
   > host and path `/run/x` — both spellings occur, so segments are read from the two joined;
   > and **`%2e%2e%2f` survives in `pathname` un-decoded**, so the id is decoded *before* it
   > is validated, or a check sees a harmless token and the app navigates to `../`.
   > **A notification is only for someone who walked away** — unfocused window *and* over 30 s
   > — because an app that produces noise gets its notifications switched off at the OS level,
   > after which the one that mattered never arrives. A cancellation is never notified, and no
   > result number goes in the body: a stress figure with no mesh, material or load case beside
   > it is the unqualified number `app/verify/` exists to prevent, and a notification has no
   > room for the qualification.
   > **Not done here, and it needs the Windows machine:** every native call. `src-tauri` has
   > only `tauri-plugin-shell`, and this needs `dialog`, `fs`, `notification` and `deep-link`
   > added to `Cargo.toml`, the `default.json` capability widened, and the `kryova` scheme
   > registered in `tauri.conf.json`. **Deliberately not written blind** — Rust cannot be
   > compiled or checked here, and an unbuildable `src-tauri` would block the Windows session
   > rather than help it. QUEUE G5.
   > **Interface change:** none; nothing existing is touched.
   > Tested by: `../Kryova-frontend/src/lib/desktop-powers.test.ts` (23, written on Linux and
   > not run).

   <!-- superseded 2026-09-16 -->
   > NOT STARTED.

4. **Offline honesty**: what works without the backend (viewing cached designs, reading docs) and
   what does not (everything else), stated in the UI rather than discovered by timeout.
   > PARTIAL (2026-09-16) — **the table and the verdicts exist; no surface shows them yet.**
   > `../Kryova-frontend/src/lib/offline-capability.ts`. Tests written on Linux and **not
   > run** as vitest (the user's rule); `tsc --noEmit` and `eslint` clean, and all 20 claims
   > were executed against the real module with a one-off script.
   > **"Does this work offline" is a fact somebody knows when they build the feature and
   > nobody can infer afterwards**, so it is a table of ten capabilities written in advance
   > rather than a guess made at the call site. One table, not one decision per screen —
   > that is how two screens come to disagree about the same feature and the user meets both.
   > **A capability the table does not list reads as *unavailable*.** Defaulting a missing row
   > to "works offline" would turn forgetting to add one into a promise the app cannot keep,
   > which is the exact failure the module was written to remove.
   > **Three states kept apart that are usually collapsed into one:**
   > - `unknown` is the startup state and is **not** offline. Before the first probe the
   >   honest position is that the backend is probably fine, and greying the interface out on
   >   launch would make every cold start look like an outage.
   > - `unreachable` (nothing answered) and `failing` (a 5xx) get different sentences, because
   >   "no connection" invites checking the network and "the server is having trouble" invites
   >   waiting, and swapping them wastes the user's time in both directions.
   > - `cached` is enabled **and marked degraded**, so a short list cannot read as the whole
   >   list. "Your last three designs" is honest; an empty list captioned "designs" is not.
   > **`navigator.onLine` is a fast negative only** — `true` on a captive-portal wifi reaches
   > nothing — and **a 401 or 404 is not offline**: the server is up and answering correctly
   > about something else, and switching the whole interface off because one probe was
   > unauthorised is the obvious wrong reading. Only 5xx is `failing`.
   > **Nothing is queued for later, and the refusals say so.** An offline queue is a real
   > feature with real consequences (a design edit replayed against a part that moved
   > underneath), and pretending to accept a write is worse than refusing it.
   > **The banner counts rather than adjectives** — "7 of 10 features need the server" is
   > checkable and "limited functionality" is not.
   > **Not done here:** no component renders any of it — no banner, no per-control disabling,
   > no capability panel — and nothing probes, so no caller ever moves the state off
   > `unknown`. The probe belongs beside `api-client.fetchWithRefresh`. QUEUE G5.
   > **Interface change:** none.
   > Tested by: `../Kryova-frontend/src/lib/offline-capability.test.ts` (20, written on Linux
   > and not run).

   <!-- superseded 2026-09-16 -->
   > NOT STARTED.

5. **The Windows installer.**
   > PARTIAL (2026-09-05) — `npm run desktop:msi` produces `Kryova_0.2.0_x64_en-US.msi` (3.8 MB) on
   > this machine, release profile, WiX candle+light, ~41 s Rust compile. **Built is not
   > installed**: nothing has run the installer and confirmed the app starts from it, so task 1 and
   > P9 task 4 both remain open and a release still needs a human.

##### Phase P8 — Billing, quotas and metering #####

> ✅ PHASE COMPLETE (2026-09-10) — all four tasks done and tested. **The price list is
> deliberately still empty, and that is the completed state rather than a gap**: plan allowances
> are now settings, because Decision 4 makes this product free and open and a self-hosted install
> has no price list at all — its operator decides what its own users may do. Every default is
> `0` (unset), so a fresh deployment behaves exactly as it did before allowances existed, and no
> number in this repository is one Kryova is claiming.

**~3 engineer-months. Needs P2 (orgs), P3 (flags/quotas); ships before general availability.**

1. **Meter what costs**: solver-seconds by class (a CalculiX nonlinear minute ≠ a linear static
   second), geometry-operation batches, storage-bytes, seats. Usage accumulates locally and posts in
   aggregates — the high-volume pattern; never one event per action.
   > **DONE (2026-09-10) — the last three meters are wired and `unwired_meters()` is now empty.**
   > *AI tokens* go through `routes/ai._meter_tokens`, which is the single funnel every AI path
   > already used; the daily budget in `app/ai/usage.py` is deliberately **not** merged with it,
   > because one is a per-user ceiling read before the next call and the other is a per-tenant
   > bill summed over a period, and neither is the shape the other needs. *CATIA seat time* goes
   > through `dispatch._meter_seat_time`, at the one place every dispatch path — seat, open
   > kernel, refusal, failure — already converges; a second timing site would give two numbers
   > for one event, which task 4 forbids. It is `APPROXIMATED` and always will be: it sums the
   > calls Kryova drove and a seat is also occupied between them. *Kernel operations* needed no
   > new meter, only a usage scope around dispatch, which the seat-time work opened.
   > A tenant that cannot be resolved is **skipped, never guessed at** — a wrong organisation on
   > an invoice is worse than a missing line. Tested by: `tests/test_billing*.py`,
   > `tests/test_metering.py`, `tests/test_payments.py` (28 across this phase). Code:
   > `app/core/metering.py`, `app/models/billing.py`, `app/api/routes/billing.py`, migration
   > `b3d7c1f4a920`.

   <!-- superseded 2026-09-10 -->
   > PARTIAL (2026-09-06) — metering, the usage ledger, rollups and per-tenant quotas shipped.
   > **Metering is *always on*, independent of tracing**: `app.observe.collect.add_listener` lets a
   > listener see every span whether or not `collect()` is running, so a bill is never gated on an
   > operator having turned tracing on for the request that happened to be billable.
   > `app.simulation.runner` is the first wired consumer — `usage_scope` posts a job's meshing and
   > solve time to the ledger in a `finally`, so a solve that fails after nine minutes is still
   > billed for the nine minutes, and the metering write goes through its own session (`LedgerSink`)
   > rather than the job's transaction, so a metering fault can never roll back the result it was
   > measuring.

2. **Plans**: free tier (real but bounded — M1-class work, community support), team, and enterprise
   (SSO later, audit-log export from P3 task 1, custom quotas). **Stripe** metered billing +
   prepaid credits for compute bursts — the hybrid the compute-heavy SaaS pattern converged on.
   > DONE (2026-09-10) — `app/core/payments.py` is the seam: `none` (the default, and the only
   > one a self-hosted install needs), `stripe`, and `fake` for tests. Stripe does metered
   > billing in **aggregate meter events per sealed period**, never one per action, and prepaid
   > credit through a payment intent; amounts are integer minor units end to end, which is
   > Stripe's own representation, so the boundary is a transfer and not a conversion. The
   > `stripe` package is imported **inside each method** and declared optional in
   > `pyproject.toml` — a missing package is a `ProviderResult` naming what is absent, not an
   > ImportError at startup, which is the `ezdxf` landmine not repeated.
   > Three rules the routes keep: the payment is taken **before** the balance moves (the other
   > order hands out credit whenever the provider is unreachable); a provider refusal leaves the
   > plan alone (a plan recorded locally that the provider refused is a tenant nobody is charging
   > for); and a build with no provider **refuses** a credit purchase rather than granting it,
   > because a deployment that cannot take money must not be a way to mint credit.
   > **Not exercised against a live Stripe account** — the adapter is written from the API and
   > tested against a stub, and what it does with real keys is a measurement nobody on this
   > machine can make. Tested by: `tests/test_payments.py`.

3. **Enforcement with dignity**: quota exhaustion returns the honest envelope (what ran out, what it
   costs to continue, what remains free), never a bare 429; estimates before expensive runs (P5
   task 7) mean nobody is surprised.
   > DONE (2026-09-10) — `POST …/simulations` refuses an exhausted allowance with
   > `QuotaDecision.detail()`: the meter, what was used, the allowance, what remains, the credit
   > balance and the plan. **`402`, not `429`** — this is not "too fast", it is "there is none
   > left", and a 429 invites a retry that can only fail. Checked against `SOLVER_SECONDS` alone,
   > because one limit per action is what a person can act on. A deployment with no allowances is
   > unaffected: `check_quota` answers *allowed* with a reason rather than denying what it has no
   > policy for, which is what stops a quota system stopping the product the day it is switched
   > on. Tested by: `tests/test_payments.py::TestARefusalIsAnEnvelope`.

4. **The bridge between metering and trust**: the same meter that bills is the meter shown in cost
   estimates. One number, two uses; divergence is a bug class of its own.
   > DONE (2026-09-10) — `app/core/estimates.py`. An estimate is a **projection of the same
   > `Meter` values the ledger holds**, from this tenant's own recorded rows, in the ledger's own
   > scaled integer units so the two can be compared without a conversion between them. That
   > identity is the task.
   > **An estimate with too little history carries no number** — `basis` is `unavailable` with a
   > reason, in the vocabulary `kernel/provenance.py` already uses. A cost estimate is the most
   > tempting place in the product to invent a figure: it is advisory, shown before anything
   > happens, and never contradicted afterwards, which is exactly why an invented one survives.
   > The **median**, not the mean, so one 40-minute convergence study in a history of two-second
   > solves does not produce an estimate higher than anything that has ever happened. This
   > tenant's history only, never the fleet's. **An estimate is never a limit** — `check_quota`
   > decides what may run; wiring a projection into an enforcement decision would make it
   > binding. Tested by: `tests/test_payments.py::TestAnEstimateComesFromTheMeterThatBills`.

##### Phase P9 — Delivery: CI/CD, environments, and operational safety #####

**~3 engineer-months. Starts immediately.**

1. **Frontend CI, week one.**
   > DONE (2026-09-06) — and it turned out to be mostly done already (the workflow was added
   > 2026-08-29; this plan's v2 claim that `.github/` did not exist was simply wrong). Lint, `tsc`,
   > vitest and build run on every push, now with SHA-pinned actions, the Node version in `.nvmrc`
   > so CI and `nvm use` agree, and a check that the repo still has exactly three runtime
   > dependencies — the one design rule in that repo that nothing else notices being broken.
   > **Still open: preview deployments per PR.** Code: `../Kryova-frontend/.github/workflows/ci.yml`,
   > `scripts/check-dependencies.mjs`.

2. **Backend CI that is honest about the database.**
   > DONE (2026-09-06). Three jobs: `lint` (ruff + `mypy app`), `offline` (3,327 tests, no
   > connection, and it prints how many database tests it did *not* run), `database` (PostgreSQL 17
   > service container + `alembic upgrade head` + `alembic check` + the other 430 tests).
   > **`--database-only` refuses to start without a real PostgreSQL**, so the SQLite fallback can no
   > longer wear the name of the database suite. Tested by:
   > `tests/test_repository_hygiene.py::TestContinuousIntegration` — verified by breaking all five
   > guards. Code: `.github/workflows/ci.yml`, `scripts/pytest_split.py`.
   > **CLOSED 2026-09-08 — RLS now enforces in CI.** The job bootstraps the container as
   > `postgres` and creates the application role `NOBYPASSRLS`, owning both databases, plus a step
   > that fails the job if the connecting role can bypass. Both configurations were reproduced in
   > Docker to prove the change is real and not cosmetic: old = `rolsuper/rolbypassrls true true`
   > and the RLS test xfails, new = `false false` and it XPASSes, whole database job green.
   > See P2 task 3.
   > **CLOSED 2026-09-08 — the 12 failures on `main` are fixed** (commit `4121c77`). One was a
   > real defect in `app/ai/agent.py`: the verification nudge discarded the "nothing has actually
   > been done" message the exhausted correction loop had just written, so a model that ran
   > nothing closed with "Done." and a footnote. One was a tripwire firing correctly
   > (`KryovaFaceMap` added to the frozen VBA library — reviewed and accepted). One was a stale
   > BM25 index. The other nine were tests that had rotted: a fixture whose user message carried a
   > measurable requirement, a stub one call short of the code it fakes, a volume oracle coupled
   > to how many times the code reads a volume, a negative probe naming a tool the system prompt
   > has since started teaching, and a `caplog` assertion over every logger rather than the one
   > under test. **None of the nine was wrong about what it claimed** — each was wrong about how
   > it checked it, which is why they all failed on a change to something else.
   > **STILL OPEN: CI did not catch any of them.** Twelve tests were red on `main` for a
   > fortnight. `ci.yml` runs on push to `main` and on pull requests, so either the runs were
   > failing unread or they were not running — worth an hour with `gh run list` before trusting
   > this pipeline as a gate. A gate nobody reads is the same as no gate.

3. **Backend images that carry the fleet**: containers with OCCT + gmsh + CalculiX + (later) Chrono
   pinned — the determinism substrate (E1 task 7) and the deploy artefact are the same thing. GPL
   components live in their own layers/processes per Decision 4.
   > PARTIAL (2026-09-15) — everything the superseded status below records, **plus the first
   > thing the nightly build found.** Run 34822694239 (2026-09-14) built the image and its health
   > check failed honestly: `gmsh will not import: libgomp.so.1: cannot open shared object file`.
   > The builder stage had GCC's OpenMP runtime only because the compiler brought it, so the
   > runtime stage now installs `libgomp1` and says why. **Still PARTIAL:** nothing has built the
   > image since, and the next missing shared object, if there is one, is only visible to the
   > next nightly run. The test was written on Linux and not run (the user's rule).
   > Tested by: `tests/test_delivery.py::TestTheDockerfile::test_the_runtime_stage_installs_the_openmp_runtime_gmsh_links`,
   > `::TestTheContainerHealthCheck`, `::TestTheDockerfile`. Code: `Dockerfile`.

   <!-- superseded 2026-09-15 -->
   > PARTIAL (2026-09-10) — **the image is written and its health check is real; nothing has
   > built it yet, and an image nobody has built is a Dockerfile.** `nightly.yml` is where it
   > gets built and health-checked; until that has run green the residual stands.
   > Two stages, and the split is about size rather than tidiness: `cadquery-ocp` is ~166 MB and
   > drags ~640 MB of unused VTK behind it, so the build toolchain never reaches the shipped
   > layer. **GPL components sit at the process edge, which is where Decision 4 puts the
   > boundary**: CalculiX is a system package invoked as a subprocess and never linked, and
   > removing it yields an image whose `find_ccx` returns `None` and whose error message says so
   > — the honest degradation the code already implements.
   > **The health check asks whether the container can do the work, not whether it started.**
   > `scripts/container_health.py` calls `find_ccx` and imports gmsh — the same code the solver
   > and the mesher use — because a file at `/usr/bin/ccx` that will not execute passes a `stat`
   > and fails every job. A green container that cannot solve anything is the most expensive
   > kind of green there is: the fleet looks healthy and every run fails. OCCT is *optional* and
   > its absence is reported rather than fatal, matching the contract `app/kernel/` already
   > keeps — a CATIA-only image is a healthy image.
   > It runs as a non-root user and **does not migrate on start**: N replicas racing one
   > migration, and task 4 gates production migrations on a step somebody watches.
   > Tested by: `tests/test_delivery.py::TestTheContainerHealthCheck`, `::TestTheDockerfile`
   > (10). Code: `Dockerfile`, `scripts/container_health.py`.

4. **Environments**: staging with seeded demo orgs and the mission suite running nightly against
   it; production migrations gated on `alembic check` and a rollback note per migration.
   > PARTIAL (2026-09-10) — **the nightly suite and the rollback-note rule exist; staging does
   > not, because there is nowhere to deploy it to.**
   > `.github/workflows/nightly.yml` builds the fleet image, health-checks it, and runs the
   > mission ladder at 03:00 UTC — late enough that a day's merges are in, early enough that a
   > failure is on a screen before anyone starts. It also prints **what each rung does not
   > claim**, because "M2 passed" must never be readable as "the welds are sized". Nightly
   > rather than per-push on purpose: the missions compile real designs against a real kernel,
   > and a slow gate on every push is one that gets trimmed until it proves nothing.
   > `alembic check` has gated the database job since P9.2. The **rollback note per migration**
   > is now enforced: `tests/test_delivery.py::TestMigrationRollbackNotes` walks every migration,
   > finds the ones whose `downgrade` drops a table, column or constraint, and requires the
   > docstring to say what that would cost. A `downgrade` that silently drops the table holding
   > every approval anybody ever signed is a rollback nobody should run without being told, and
   > being told at 3am by reading DDL is not being told.
   > **Fourteen historical migrations are grandfathered by name and the list may only shrink.**
   > They are not back-filled because a rollback note invented by somebody who did not write the
   > migration reads as considered and is a guess — on the one document consulted during an
   > outage. A separate test asserts every grandfathered name still exists, so the list cannot
   > quietly become permanent by going stale.
   > **Still open: staging itself.** Seeded demo orgs and a nightly run *against a deployed
   > environment* need an environment, which this deployment has not got. What runs nightly is
   > the offline half of the ladder, which is the half that exists.
   > Tested by: `tests/test_delivery.py::TestMigrationRollbackNotes` (2). Code:
   > `.github/workflows/nightly.yml`.

5. **Desktop release pipeline**: tauri build matrix (Windows first — the CATIA audience), signing,
   `latest.json` publication, channel promotion (beta → stable) as a pipeline step.
   > BLOCKED, and the reason is not scheduling (decided 2026-09-06). `src-tauri/src/lib.rs`
   > resolves both checkouts and node through `option_env!`, fixed at compile time by
   > `scripts/desktop-build.mjs`, and `tauri.conf.json` sets
   > `frontendDist: "http://localhost:3000"` — so the desktop app is a shell that launches a dev
   > server from paths baked in at build time. **An MSI built on a runner therefore ships pointing
   > at `D:\a\...`, and on a customer machine starts nothing and times out.** Publishing it would be
   > the "green on a suite nobody ran" failure in artefact form. This task begins with bundling the
   > frontend statically and packaging the backend (task 3), not with a release workflow. Meanwhile
   > `../Kryova-frontend/.github/workflows/desktop.yml` type-checks the Rust shell on Windows
   > (`workflow_dispatch` only, no artefact, gates nothing) so `src-tauri/` cannot rot silently.

6. **Backups and restore *drills***: PITR verified by actually restoring; blob-store backup with
   refcount integrity check; a written RTO/RPO and a quarterly drill that proves it.
   > PARTIAL (2026-09-16) — **the drill has now been run, for the first time, against a real
   > PostgreSQL (16.15, local), and running it found four defects that reading it had not.** All
   > four are fixed; the residual is unchanged and is stated at the end.
   > 1. **`alembic upgrade head` migrated `DATABASE_URL`, not the restore target.** Alembic's
   >    `env.py` reads the setting, and the drill ran it with the ambient environment — so on a
   >    machine where `DATABASE_URL` points at production, the drill's "migrations: ok" tick
   >    meant *production* had been migrated. Proved rather than reasoned about: with
   >    `DATABASE_URL` aimed at a bystander database, the bystander came back holding all 38
   >    tables while the restore target held none and the drill reported success. `_alembic_env`
   >    now passes the target through the environment, and the bystander stays at 0 tables.
   > 2. **The application role cannot take the backup at all.** 14 of the 38 tables carry
   >    `FORCE ROW LEVEL SECURITY` and the role is `NOBYPASSRLS` by design (Database item 3), so
   >    plain `pg_dump` stops on the first of them: `query would be affected by row-level
   >    security policy for table "approval_gates"`. A drill that restores a dump nobody can take
   >    proves nothing. `--enable-row-security` succeeds, and it is complete **only** because
   >    every policy here has an "unset means everything" branch — with no `SET LOCAL` in force
   >    the policies admit every row. That is a property of these policies, not of the flag, so
   >    `check_dumpable` states it and a policy that loses that branch silently starts taking
   >    partial backups.
   > 3. **A blob check that could not run rendered as a tick.** The query failed and the report
   >    said `0 referenced blob(s) missing`, which reads as "nothing is lost". `_check_blobs` now
   >    returns why it could not answer, and the drill records a finding instead of a zero.
   > 4. **`report()` and `to_dict()` printed the database password**, in the output whose whole
   >    purpose is to be pasted into a ticket. Redacted at both, verified by grepping the
   >    password out of both forms: 0 occurrences, and `--json` shows
   >    `postgresql://kryova:***@localhost:5432/kryova_drill_restore?sslmode=disable`.
   >
   > **A fifth finding was mine and was wrong, and it is recorded because the correction is the
   > useful part.** Unqualified table names in the count queries looked broken; the real run
   > counted correctly (`organisations: 2`, `users: 3`), because `search_path`'s `"$user"`
   > resolves to the `kryova` schema — role and schema happen to share a name. So it is a
   > fragility, not a defect: rename either and every count silently reads another schema's
   > rows. `_qualified` now qualifies them explicitly.
   > **Still open, and unchanged by this run: a real backup, a real PITR restore, and a quarterly
   > cadence somebody owns.** What ran was a `pg_dump`/`pg_restore` round trip on a scratch
   > database. PITR needs an archive, and there is not one yet.
   > Tests were written on Linux and not run as pytest (the user's rule); the drill itself was
   > run, which is where all five findings came from.
   > Tested by: `tests/test_delivery.py::TestTheRestoreDrill` (6),
   > `tests/test_delivery.py::TestWhatTheFirstRealDrillRunFound` (8). Code:
   > `scripts/restore_drill.py`.

   <!-- superseded 2026-09-16 -->
   > PARTIAL (2026-09-10) — **the drill is written and its refusals are tested; it has never
   > been run against a real backup, because there is no backup to run it against yet.**
   > `scripts/restore_drill.py`. Not a script that checks a dump exists: it restores one into a
   > throwaway database, runs the migrations forward against it, counts what came back, and
   > cross-checks the blob store against the rows pointing into it. **A backup nobody has
   > restored is not a backup** — it is a file that is probably a backup, and the difference is
   > discovered at the worst possible moment.
   > **The refcount check reports two things apart, and that is the design.** A dump and a blob
   > copy are taken at different instants by different tools, so they disagree at the edges by
   > construction. A referenced blob that is missing is **data loss**; a stored blob nothing
   > references is wasted disk. A single "integrity: FAIL" would bury the first under the
   > second, so orphans are reported and deliberately do **not** fail the drill — failing over
   > waste teaches people to ignore drill failures, which is how the one that matters gets
   > ignored too.
   > **RTO 240 minutes and RPO 15 minutes are written down here rather than in a wiki**, because
   > a target nobody can find is a target nobody is measured against, and because this script is
   > what proves or disproves them. The RPO figure is a continuous-archiving one: a nightly dump
   > alone has an RPO of a day, so `--check-archiving` verifies `archive_mode` rather than
   > assuming it.
   > It **refuses a target whose URL contains `prod`, `production`, `live` or `main`** — blunt on
   > purpose, because a false positive costs a rename and a false negative is the incident the
   > drill exists to prevent.
   > **Still open: a real backup, a real PITR restore, and a quarterly cadence somebody owns.**
   > Tested by: `tests/test_delivery.py::TestTheRestoreDrill` (6). Code:
   > `scripts/restore_drill.py`.

7. **Secrets and supply chain**: no default secrets boot (P1 task 4), dependency pinning + audit in
   CI, SBOM for the desktop app (enterprise buyers ask), release notes generated from the merge log.
   > PARTIAL (2026-09-10) — **release notes are generated now; the desktop SBOM still waits on
   > task 5.** `scripts/release_notes.py` reads the first-parent log between two tags, so a
   > merged branch appears once, as its merge commit. Commits are grouped by the prefixes this
   > history actually carries. Nothing is dropped: an unprefixed commit lands under *Other changes*.
   > The output is deterministic, with no timestamp unless `--date` is given. **It is a script, not
   > yet a CI step** — nothing runs it when a tag is cut, and wiring it in is what remains of
   > this line besides the desktop SBOM.
   > Tested by: `tests/test_delivery_release.py` (20), `tests/test_delivery.py::TestTheSBOM` (6).

   <!-- superseded 2026-09-10 -->
   > PARTIAL (2026-09-10) — **SBOM, licence check and audit are in CI; release notes are not,
   > and the desktop SBOM waits on task 5.**
   > No-default-secrets boot shipped with P1.4 and dependency pinning was already the rule
   > (`requirements.txt` pins exact versions; the frontend pins its three). What is new is the
   > `supply-chain` job: `scripts/sbom.py` emits CycloneDX **from the installed environment, not
   > from `requirements.txt`** — the file lists direct dependencies and a vulnerability lives in
   > a transitive one, most obviously the VTK that `cadquery-ocp` drags in.
   > **The licence check is Decision 4 made checkable by somebody who does not trust us**, which
   > is the entire point of a free stack. A copyleft package that is *linked* rather than invoked
   > as a subprocess fails the job, and so does one whose licence nobody has established — an
   > unchecked package is a hole in the claim rather than an absence of one. Exactly one package
   > is accounted for today (gmsh, GPL, called through its API in a subprocess-isolated critical
   > section) and it is **flagged in the file with its reason rather than hidden**. LGPL is
   > deliberately not treated as a concern: linking is what it permits, and flagging it would be
   > noise that teaches people to ignore the flag.
   > The SBOM carries **no timestamp**, so two runs of an unchanged environment produce an
   > identical document — diffing two SBOMs is the main thing anybody does with one, and a field
   > that changes every run makes "the dependencies moved" indistinguishable from "somebody
   > re-ran the script".
   > `pip-audit` runs **advisory rather than blocking**, and that is a stated choice: a new CVE
   > in a transitive dependency would otherwise turn every unrelated pull request red overnight,
   > which is how a security signal gets muted by the people it is for. Promoting it is a
   > decision to take once somebody owns triaging it.
   > **Still open: release notes from the merge log, and an SBOM for the desktop app** — the
   > latter waiting on task 5, since there is no desktop artefact to describe.
   > Tested by: `tests/test_delivery.py::TestTheSBOM` (6). Code: `scripts/sbom.py`,
   > `.github/workflows/ci.yml`.

##### Phase P10 — Documentation, onboarding and the trust surface #####

> ✅ PHASE COMPLETE (2026-09-10) — all four tasks done and tested, and the phase now has one
> property worth naming: **every page it ships is derived from something that cannot drift.**
> The trust register reads module constants, the mission gallery reads `LADDER`, the API
> reference reads this deployment's own OpenAPI document, the guides' routes are checked against
> the running router, and the onboarding checklist reads the account's real contents. Nothing on
> any of them is a second copy of a claim made elsewhere, which matters more here than anywhere
> else in the product: documentation is the part most likely to quietly stop being true, and
> these are the pages an outsider reads *before* they can check anything for themselves.
>
> The other thread running through all four is **publishing what is not claimed alongside what
> is** — `Mission.unproven` in the gallery, `not_covered` in the guides, the twelve commitments,
> and a status page that will not infer an outage from a failure rate. A page that is convincing
> and incomplete is worse than one that is neither.

**~2 engineer-months, then continuous.**

1. **In-product onboarding**: the existing setup wizard grows into first-run success — a guided M1
   in under ten minutes, on the free tier, no sales call.
   > DONE (2026-09-10) — `src/components/onboarding/first-run.tsx`, on the projects page, three
   > steps: create a project, upload a CAD file, run a linear-static solve.
   > **Every tick is derived from what the account actually contains** — a project row, a
   > geometry version, a *succeeded* simulation — and never from a stored "seen the onboarding"
   > flag. That is the whole design, and two failures follow from getting it wrong: a
   > flag-driven checklist can be fully ticked by somebody who has done none of it, telling a
   > stuck user they are finished; and it can be dismissed by somebody who is still stuck,
   > leaving nothing on screen about it. So there is no dismiss button and no `localStorage`
   > (which `token-custody.test.ts` would catch anyway, and the reasoning above is why it
   > should). It disappears on its own when there is a run that really succeeded.
   > A **queued or failed** run does not tick the last step: a job that fails after nine minutes
   > is not a completed first run, and ticking it would congratulate somebody at the moment they
   > need help most. Tested by: `src/components/onboarding/first-run.test.tsx` (6).

2. **The docs site**: task-oriented docs, the mission gallery (every ladder mission as a worked,
   forkable example), API reference from the OpenAPI schema that already exists.
   > DONE (2026-09-10) — `app/handbook/` + `GET /handbook/*` + `/docs`, unauthenticated for the
   > same reason `trust` is: documentation behind a login can only be read by people who already
   > bought, and the reader it needs — an engineer working out whether this does what they need —
   > is exactly the person without an account.
   > **Nothing on it is written twice, and that is the only defence that does not rely on
   > somebody remembering.** The mission gallery is derived from `app.design.missions.LADDER`,
   > the suite that decides whether this product works; the API reference is built from this
   > deployment's own OpenAPI document; and **every guide step that names a route is checked
   > against the running router** by `tests/test_docs.py`. A guide telling somebody to POST to an
   > endpoint this build does not serve is worse than no guide — they believe it, it fails, and
   > they conclude the product is broken.
   > **`not_covered` and `not_claimed` are required fields, not decoration.** `Mission.unproven`
   > already models what a rung does not claim, and a gallery that printed the passes and dropped
   > the caveats would be the most misleading page in the product because it would be the most
   > convincing one. The same for the guides: the stop-a-run guide says stopping is not a refund,
   > which is the thing a reader would otherwise get wrong about their own bill.
   > Named `handbook`, not `docs`, because `app/documents/` already exists and parses customer
   > drawings — two packages one letter apart doing unrelated things is a mis-import in every
   > future session. Tested by: `tests/test_docs.py` (30), each guard verified by breaking it.

3. **The trust pages, the differentiator**: the **validation register** (E7 task 4) published —
   which analyses are validated against what, to what accuracy; the **"what Kryova will not claim"**
   page (Decision 5's scope, the sign-off model, the unmeasured-is-never-green rule) as public
   commitments; a changelog that names accuracy-affecting changes loudly.
   > DONE (2026-09-06) — the validation register, twelve commitments each **MECHANICAL with the
   > files that enforce it or POLICY with none** (a mechanical one naming no file is refused at
   > construction), and a typed accuracy changelog. **Public and unauthenticated, defended**: a page
   > claiming verification is the product, behind a login, can only be read by people who already
   > bought. Paid for structurally — no `DbSession`, no `CurrentUser`, an allowlist on provenance,
   > and a test that walks each route's dependency graph. Tested by: `tests/test_trust.py`. Code:
   > `app/api/routes/trust.py`, `app/verify/{register,commitments,changelog}.py`.

4. **Status and comms**: status page, incident history, the P3 task 7 announcement machinery's
   public face.
   > DONE (2026-09-10) — `app/core/status.py`, `GET /status`, and `/status` in the frontend.
   > **It carries none of the fleet's numbers, and that is asserted by name.** `GET /admin/health`
   > has queue depth, storage bytes, live sessions and user totals; on a public URL those are the
   > size of the business and the hours nobody is watching, and not one of them answers "is it
   > working". `tests/test_docs.py` names each forbidden field rather than reading the current
   > output and agreeing with it.
   > **Degraded is never inferred from a failure rate.** There is deliberately no threshold in
   > the module, and a test parses its AST to keep it that way: a rule nobody agreed to would put
   > this product on a public outage page for a quiet hour with two bad runs. A window is
   > declared by a person, who also has to write the sentence explaining it. The one automatic
   > transition is a **critical announcement with no maintenance window** — that is how an
   > operator says "something is wrong and we have not gone read-only", and without it the page
   > would read "operating normally" above a banner saying solves are failing.
   > This publishes P3.7's records rather than introducing a second place to declare an incident;
   > an incident declared twice is described two ways, and the version customers read is the one
   > nobody updates. The customer-facing `message` is published and the operator's `reason` never
   > is. **When the status page cannot reach the API it says so** instead of showing a spinner or
   > a green panel from stale state — the one case a status page must get right.
   > **Two latent defects found on the way, in code from earlier phases.** `Announcement.level`
   > and `ShareLink.revocation` were enum-typed columns stored as bare `String`, so a row loaded
   > from the database handed back a plain `str`: `announcement.level.value` raised
   > `AttributeError` on any deployment that had actually published an announcement, while
   > passing in every test that wrote and read one in a single session. Both now use the new
   > `models/types.EnumText`, which is `MessageRoleType` generalised — this is the same bug class
   > for the third and fourth time. No migration: the DDL is unchanged.
---

## Part 3 — Technology register

Every dependency choice, why, and what was rejected. All free; licences noted because they
constrain architecture (Decision 4).

1. **CAD kernel — OCCT via `cadquery-ocp` (OCP).** LGPL. The only mature open B-rep kernel; OCCT
   8.0 adds `BRepGraph` history. Rejected: `pythonocc-core` (conda-only, not on PyPI — the E1 spike
   finding), pyOCCT (not needed), CGAL (mesh, not B-rep), Manifold (no NURBS).
2. **2D constraints — PlaneGCS.** LGPL. Full vocabulary, proven detachable. Rejected: SolveSpace's
   solver (narrower; a sketcher that cannot express tangency is not a sketcher).
3. **Meshing — gmsh** *(in use)*. GPL. Industrial grade. Subprocess boundary.
4. **FEA workhorse — CalculiX.** GPL. Abaqus-style decks, 25 years of validation; C3D10 the
   recommended solid; the shell/beam expansion caveat recorded. **Subprocess only.**
5. **FEA specialist — code_aster.** GPL. Fracture, cyclic plasticity. **Subprocess only.**
6. **Multiphysics — Elmer.** LGPL. FSI, coupled fields.
7. **CFD — OpenFOAM.** GPL. The standard; late; the meshing is the hard part.
8. **Fatigue — pyLife** (+ FFPACK, fatpack). Apache-2.0. Bosch Research; turns the fatigue phase
   from bespoke work into integration plus methodology.
9. **Multibody — Project Chrono.** BSD-3. Python API, vehicle templates. MBDyn (GPL) reserved for
   rotordynamics. **Caveat: `pip install pychrono` installs an unrelated package and succeeds** —
   the engine probe checks the module really is Chrono.
10. **Optimisation — OpenMDAO.** Apache-2.0. NASA Glenn; SIMP/level-set precedent.
11. **CAM — OpenCAMLib.** LGPL. Drop-cutter/waterline, FreeCAD-proven.
12. **Sheet metal — FreeCAD SheetMetal as reference.** LGPL. Working unfold, ANSI/DIN K-factor.
13. **Standard parts — BOLTS.** Open. ISO/DIN parametric parts plus metadata.
14. **Requirements — SysML v2 via SysON / Capella.** EPL. Eclipse, free.
15. **Document understanding — Docling primary, MarkItDown fallback.** MIT / MIT. Local and free;
    Docling strongest on tables/layout/OCR, MarkItDown widest format tail. Rejected: hosted parsers
    (data leaves the deployment).
16. **DXF — ezdxf.** MIT. The standard Python DXF library, R12→R2018.
17. **Mesh compression — Draco and/or meshopt.** Apache-2.0 / MIT. Measured per-asset; LOD and
    instancing on top.
18. **Retrieval — BM25** *(in use)* plus rerank. Own. Exact-term regime; built and tuned.
19. **Payments — Stripe**, metered plus credits. Commercial service — the one paid *service*; no
    free equivalent worth its risk. Rejected: bespoke billing.
20. **Materials data — schema and provenance now, ingest later.** **An honest gap**: the open
    databases are DFT/atomistic and useless for an engineering S-N curve.

**Licence obligations, restated because they bind:**

1. GPL ⇒ separate process, file/CLI boundary, always.
2. LGPL ⇒ dynamically linked, replaceable.
3. `data/bm25/`'s tracked Dassault PDFs ⇒ resolve before publishing the repo (history rewrite).
4. Dependency docs ingested into our BM25 index ⇒ check each licence permits the copy; where it
   does not, index a pointer, not the text.

---

## Part 4 — Effort, honestly

**Engineering track — ~151 engineer-months:**

1. Era I, geometry engine (E1–E2) — 14. Hard, foundational, unavoidable.
2. Era II, perception (E3–E5) — 11. Best value per effort.
3. Era III, physics (E6–E10) — 31. Mostly integrate; ~15 needs an ME.
4. Era IV, knowledge (E11–E13) — 21. Nearly all needs domain engineers.
5. Era V, scale (E14–E15) — 15. Architecture.
6. Era VI, agent (E16) — 10. Research-adjacent, least predictable.
7. Era VII, output and missions (E17–E18) — 21. Integration plus discovery.
8. Era VIII, the world outside (E19–E23) — 28. Regulation, credibility, licensed data, measured
   ceilings, positioning. Cheap per unit of commercial consequence, and the easiest to postpone.

**Product track — ~38 engineer-months:**

1. P1 identity and sessions — 3. Standard, must be flawless.
2. P2 orgs and tenancy — 4. Standard plus RLS discipline.
3. P3 admin and audit — 4. Standard, enterprise-gating.
4. P4 attachments — 5. Integration plus one research tail (drawings).
5. P5 agent UX — 5. Product craft, continuous.
6. P6 viewer at scale — 6. Hard graphics engineering.
7. P7 desktop — 3. Extends what exists.
8. P8 billing — 3. Standard, pre-GA.
9. P9 delivery — 3. Starts week one.
10. P10 docs and trust — 2+. Continuous.

**Programme total ≈ 189 engineer-months** — about 8–10 engineers × ~2 years, in ideal conditions.
(It was 161 until Era VIII was added on 2026-09-09; the 28 months that arrived with it are work
that was always going to be required and was simply not written down.)

**Ideal conditions do not exist.** With hiring, rework and discovery: **4–7 calendar years** to the
full ambition — with sellable, honest intermediate products from the first year (M1–M3 class work
on the product track's platform is already a real tool). The ~36 engineer-months needing a real
analyst do not compress, and the plan would be dishonest if it pretended a library substitutes for
judgement.

**Not software — no amount of code fixes these:**

1. Domain engineers on staff.
2. Physical testing — rigs, strain gauges, correlation.
3. Homologation — ECE, FMVSS, Machinery Directive, CE.
4. Professional liability — someone licensed signs, which is a legal fact.
5. Supplier relationships and manufacturing capacity.
6. Aesthetic judgement — Class-A surfacing is craft; taste does not automate.

---

## Part 5 — Sequencing, and the interleave

1. **Week one, in parallel**: E1's binding spike and P9's frontend CI. One de-risks the keystone;
   the other stops the only untested-in-CI codebase from rotting.
2. **E1 before everything engineering.** Every later phase is priced in geometry operations; E1
   makes them free.
3. **P1–P2 before any external user** — auth and tenancy are not retrofittable with dignity.
4. **Era II before Era III**: measure before asserting; assert before simulating decisions.
5. **P4 early** — attachment understanding multiplies the agent's usefulness immediately, and the
   injection boundary must exist before the feature is loved.
6. **E7 before any accuracy claim.** Until then numbers are plausible, not right.
7. **E14 before anything past M4.** The context problem is solved by contracts, not model size.
8. **E17.3 (sheet metal) runs with Era IV, not Era VII** — M3 and M5 need it. The rest of E17 stays
   where it is.
9. **Era VI and P5 run continuously** — the 900-tool wall and the UX both arrive regardless.
10. **P8 before GA; P3 before the first supported customer; P10's trust pages with the first public
    accuracy claim.**
11. **Missions continuously, in-product, never at the end.** A mission that has not run in the
    product is a phase that has not been tested.

### The two facts to keep in view

1. **Tool capability is not agent capability.** Whether an LLM can hold coherent design intent
   across 10⁵–10⁶ operations is unproven by anyone. E14 and E16 are the attempt to make it true;
   this document does not claim they are solved.
2. **The honest product is a force multiplier.** *80% of the engineering in a tenth of the time; a
   licensed engineer signs.* Everything here — both tracks — serves making that 80% trustworthy
   enough to be worth the engineer's 20%.
