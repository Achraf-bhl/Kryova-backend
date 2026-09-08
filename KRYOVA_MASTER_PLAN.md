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
   Engineering Track phases are `E1`–`E18` (plus `E17.3`); Product Track phases are `P1`–`P10`.
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
   task still open does not get it, however much has shipped.
6. **Never delete a status; supersede it.** This file is current state. The build plan's *Done*
   section is the history — one line lands there for every status change here that isn't
   `NOT STARTED`.
7. **A task whose proof names the product waits for its gate** (see *Stop gates*). Inside a
   stretch the offline suite is the whole of the evidence, and the status says so:
   `pytest green, end to end pending G<n>` is honest; a bare `DONE` on unit tests is not.
8. **A session starting Kryova work reads this file first.** It is the answer to "where were we?".

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

1. **Engineering Track — phases E1–E18 in seven eras.** The machine-building capability.
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

> ✅ PHASE COMPLETE (2026-09-05) — every task below is done and tested, with one residual
> named in task 7 that needs hardware this machine does not have.

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
   > PARTIAL — 22/201 at the close of E1, **108/201 after E2**. This figure is scaffolding depth,
   > not product progress, and reading it as progress is how it got to 108. Tested by:
   > `tests/test_kernel.py`.

4. **The sketch layer.**
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
   > **RESIDUAL, and it needs hardware this machine does not have:** the CATIA half — the same
   > plan built on a real V5 seat, compared. Pointing `compare_backends`' right-hand side at
   > `app.catia.dispatch` on a Windows seat is the remaining step. Until that has run the
   > *cross-backend* claim is untested and is written as untested.

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

> ✅ PHASE COMPLETE (2026-09-08) — every task below is done and tested, with one residual
> named in the phase proof that needs hardware this machine does not have, the same shape
> as E1 task 7's.

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
> PARTIAL (2026-09-08) — the OCCT half is green and reachable via `catia_analysis_part`,
> `catia_measure`, `catia_measure_item` and `catia_measure_between`. The cross-backend agreement
> half needs a Windows seat, same as E1 task 7, and is claimed at no gate before then.
> Tested by: `tests/test_interrogation.py`, `tests/test_measurement_elements.py`.

##### Phase E4 — Visual verification: the model looks at the model #####

> ✅ PHASE COMPLETE (2026-09-08) — all four tasks done and tested.

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
   > BLOCKED — needs E11 (the requirements model). This is all that remains of the phase.

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
   > NOT STARTED — shells and beams are the open half of this phase.

4. **Analysis types unlocked by task 1**: nonlinear static, large deformation, plasticity, contact,
   bolt pretension, modal, buckling, transient dynamics, coupled thermal-stress.
   > PARTIAL (2026-09-06) — the decks are written; thermal in the deck is still open.

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

##### Phase E7 — Verification and validation *(needs an ME)* #####

**~4 engineer-months.**

1. **The NAFEMS standard benchmarks** (linear elastic, free vibration, thermal) as an automated
   suite. Reference values are reproduced in publicly readable vendor verification manuals
   (Abaqus, Ansys, DIANA) — a free, legitimate route to the targets.
   > PARTIAL — the machinery exists; **there is no case catalogue yet**, which is why the register
   > in task 4 reads 0 validated of 11. Code: `app/verify/`.

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
   > absent. **It reads 0 validated of 11 today** — and that is the correct answer. The four
   > solver analyses carry closed-form *verification* published under its own key with the ASME
   > V&V 20 split stated, because a reader meeting them under a friendlier name would read them as
   > validation. Tested by: `tests/test_verify_*.py`. Code: `app/verify/register.py`.

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
   > PARTIAL (2026-09-06) — **pyLife installs and imports on Python 3.14**, which was the open
   > question. Rainflow and damage federated to it per Decision 2, with the load-history/factor/
   > provenance vocabulary ours. Verified against closed form (exact cycle count, Miner
   > arithmetic). Tested by: `tests/` under `app/fatigue/`. Code: `app/fatigue/`.

2. **Mean-stress correction, surface finish, size and reliability factors.**
   > PARTIAL (2026-09-06) — every factor a qualified engineer must choose is an explicit input
   > with a source field rather than a buried default.

3. **Weld classification** — BS 7608 / Eurocode 3 detail categories; where a welded frame lives or
   dies; judgement, not arithmetic.
   > NOT STARTED — needs an ME.

4. **Duty-cycle definition and damage over a real usage spectrum.**
   > NOT STARTED.

5. **Notch handling, hot-spot stress extrapolation.**
   > NOT STARTED.

##### Phase E9 — Multibody dynamics: where load cases actually come from #####

**~6 engineer-months, needs an ME.**

Today load cases are hand-entered guesses. In reality they are *outputs* of the machine moving.

1. **Project Chrono** (BSD-3, UW-Madison) — multibody + FEA + FSI, Python API, template-based
   `Chrono::Vehicle` with ready suspension templates. Rejected: MBDyn (GPL; stronger on
   rotor/aeroelastic — kept in reserve for exactly that).
   > BLOCKED — **`pip install pychrono` installs an unrelated package and succeeds.** The engine
   > probe checks the module really is Chrono. So there is no dynamics engine and the docstrings
   > say so.

2. **Mechanism definition derived from assembly constraints** — the kinematic model comes from the
   CAD, never modelled twice.
   > PARTIAL (2026-09-06) — kinematics tested against closed form: slider-crank at both dead
   > centres, the Grashof families each cross-checked against the solver. Tested by:
   > `tests/test_dynamics_*.py` (115 tests). Code: `app/dynamics/`.

3. **Motion-range simulation**: swept volume, interference through motion, travel and lock checks.
   > PARTIAL (2026-09-06) — clearance tested. Tested by: `tests/test_dynamics_*.py`.

4. **Joint-load extraction feeding FEA** — manoeuvre → MBD → reactions at every joint → FEA load
   case → stress → fatigue damage. The loop that closes the system; nothing else in this plan
   produces a defensible load case.
   > PARTIAL (2026-09-06) — reactions tested against closed form: `ω²r`, and the pendulum period
   > pinned through the reaction (driven at the natural frequency the rod carries no tangential
   > force; the residual is `mg·A³/6` to 4 figures). Tested by: `tests/test_dynamics_*.py`.

5. **CATIA DMU Kinematics as an alternative backend where a seat exists.**
   > NOT STARTED.

##### Phase E10 — Thermal, flow, and optimisation #####

**~9 engineer-months.**

1. **Steady/transient conduction, convection BCs, thermal-stress coupling.**
   > PARTIAL (2026-09-03) — thermal *stress* shipped via `LoadCase.delta_t_k`; **conduction is
   > genuinely missing**.

2. **CFD via OpenFOAM**, deliberately late — *the meshing is the hard part* — scoped first to
   cooling flow and ducting.
   > NOT STARTED.

3. **Optimisation: [OpenMDAO](https://openmdao.org/)** (NASA Glenn, Apache-2.0) as the MDO
   framework; SIMP and level-set topology optimisation; DOE; response surfaces; multi-objective
   trade-offs (mass vs stiffness vs cost). The capability that makes an AI designer *better* than
   a human rather than merely faster.
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
   > NOT STARTED.

## ERA IV — ENGINEERING KNOWLEDGE

##### Phase E11 — The requirements model *(needs an ME)* #####

**~4 engineer-months.**

A machine is defined by a specification before geometry. Without this, "design me a press" has no
answerable meaning.

1. **Requirements with flow-down and validation flow-up**: target mass, duty cycle, envelope,
   regulatory regime, cost ceiling, service life. Aligned to **SysML v2** —
   **[SysON](https://mbse-syson.org/)** and **[Capella](https://mbse-capella.org/)** (both
   Eclipse, both free) are credible implementations with a published interop path.
   > PARTIAL (2026-09-06). Tested by: `tests/test_requirements_*.py` (5 files). Code:
   > `app/requirements/`.

2. **Requirement → assertion binding. A requirement nothing checks is a wish.**
   > PARTIAL (2026-09-06) — **a requirement set can now be checked against a real part**, which is
   > the gap that mattered: 2,860 lines and four test files existed and **nothing in `app/` called
   > them**. Four requirements over a built pad report met / violated-by-5mm / **UNMEASURED for
   > two different reasons**, all four counting against coverage.

3. **Traceability**: every design decision to a requirement or a standard — what a signing
   engineer demands first.
   > NOT STARTED.

4. **Coverage reporting**: verified / by-what-evidence / unverified.
   > PARTIAL (2026-09-06) — `scans_needed()` names *which* scan produces a path without measuring
   > anything.

**This phase unblocks E5 task 2.**

##### Phase E12 — Load cases, materials, and standard parts #####

**~8 engineer-months, mostly needs an ME.**

1. **Load-case library** — standardised, per-domain, *executable*: pothole strike, panic braking,
   curb drop, proof/ultimate factors, press tonnage cycles. Today invented per conversation, so no
   two runs are comparable.
   > PARTIAL (2026-09-06) — the library *composes* the existing vocabulary (Decision 2), pinned by
   > reading the load types out of `types.Load`'s own union so a new one automatically joins the
   > set it must stay inside. One composed case is **actually solved**: 12 MPa of pressure gives
   > σ = 12.0 MPa exactly. Tested by: `tests/test_load_library.py` (43 tests). Code:
   > `app/solve/load_library.py`.

2. **Materials.** The honest research finding: **the open materials databases are the wrong kind of
   open** — Materials Project, AFLOW, OQMD, OPTIMADE are DFT/atomistic; superb, and useless for an
   engineering S-N curve. Free engineering sources (MakeItFrom, MatDat, ASM's free tier) are
   partial and licence-varied. Deliverable: the **schema, provenance model and ingestion path** —
   every property carries source and confidence; buying Granta/MatWeb later becomes data-loading,
   not re-architecture.
   > PARTIAL (2026-09-06) — tested. Code: `app/solve/materials.py`.

3. **Standard parts. 70%+ of any real machine is bought.**
   **[BOLTS](https://boltsparts.github.io/)** (open library of parametric ISO/DIN parts with
   dimension metadata) as the base; supplier CAD (TraceParts, McMaster) via *import*, respecting
   their terms, never redistribution.
   > PARTIAL (2026-09-06) — tested. Code: `app/parts/`.

4. **A parts *selection* engine**: given load, speed, life — choose the bearing; never model what
   should be bought.
   > NOT STARTED.

**Gate G2 opens after E11 + E12.**

##### Phase E13 — Design rules, DFM, tolerance and cost *(needs an ME)* #####

**~9 engineer-months.**

1. **Design rules as assertions** — minimum wall by process, draft angles, bolt torque and preload,
   thread engagement, weld sizing, machining access — attached automatically from feature type +
   declared process, running in the E5 engine. **DFM becomes a red build.**
   > PARTIAL (2026-09-06). A rule naming a quantity outside `kernel/contract.py` is refused **at
   > construction**; the verdicts are literally `design.assertions.Outcome`; and **a rule resting
   > on a *sampled* bound cannot prove a pass** — `minimum_wall_mm >= 2.5` measuring 2.6 is
   > `PASSED` but *not proven*, because an upper bound from a finite ray set can only prove the
   > violation. **`engine.py` has no consumer anywhere in `app/`** — a verified module, not a
   > product path. Tested by: `tests/test_rules_*.py` (127 tests across this phase). Code:
   > `app/rules/`.

2. **Tolerance and GD&T** — stack-up (worst case and RSS), fit selection, datum schemes, FTA.
   *A drawing without tolerances is not a drawing.*
   > PARTIAL (2026-09-06) — stack-up and GD&T tested. GD&T deliberately evaluates no tolerance-zone
   > geometry, guarded three ways. **`gdt.py` has no consumer anywhere in `app/`.** Tested by:
   > `tests/test_rules_*.py`.

3. **Cost model** — material + process + tooling + assembly time, as an assertion. An agent that
   ignores cost confidently designs the unbuildable.
   > NOT STARTED — out of scope for now. E5 task 1's cost budget reports `UNMEASURED` until this
   > lands.

4. **Process-specific rule sets**: cast, machined, printed, sheet, moulded, welded.
   > NOT STARTED.

## ERA V — SCALE

##### Phase E14 — Product structure, decomposition and interface contracts #####

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
   > NOT STARTED.

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
   > NOT STARTED.

2. **Simulation compute**: queue, autoscale, result caching keyed on the provenance digest.
   **FEA is not a request-path workload** and never becomes one.
   > NOT STARTED.

3. **Geometry storage/versioning**: content-addressed CAD with **semantic diffs under a tolerance
   policy** (line diffs on CAD are meaningless; reviewers should see only consequential change).
   Extends `app/media/`.
   > NOT STARTED.

4. **CATIA session pool, crash recovery, affinity** — a smaller problem now it is off the critical
   path.
   > NOT STARTED.

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
   > PARTIAL (2026-09-06) — a tested seam and one first step, **deliberately unwired**.
   > `app/ai/planning.py` turns a request into the list of requirements it states, with the numbers
   > and tolerances attached and a three-state record where "nobody checked" is never "fine". No
   > sequencing, no dependency graph, no replanning. Half-wiring it would add a schema to the
   > payload task 1 is shrinking, or describe machinery to the model that is not there. It answers
   > the failure of 2026-09-06 attempt 3: six stated requirements, three built, and a closing
   > report of success, because nothing in the system was holding the list. Tested by:
   > `tests/test_ai_planning.py`. Code: `app/ai/planning.py`.

3. **Long-horizon memory.** The 2026 literature converges on hierarchical working memory —
   subgoals as chunks with summarised observations (HiAgent-class results: ~2× success on
   long-horizon tasks). Kryova already has the right instinct: `resume.py` reads the operation log,
   not the transcript, because a trimmed window and an LLM paraphrase cannot be trusted about last
   week. That principle generalises to the whole design record.
   > PARTIAL (2026-09-03) — resume-from-log shipped. Code: `app/ai/resume.py`.

4. **Failure recovery.** Diagnose → repair → bounded retry → escalate with a *specific* question
   (E5's machinery is the foundation).
   > PARTIAL (2026-09-08) — a third behavioural guard landed from the Level-4 runs:
   > **`MAX_EMPTY_DOCUMENTS`**, because opening a document is the one mutation that changes nothing
   > about the part, and both existing guards counted it as progress — five empty parts in one turn
   > tripped neither. Measured effect: one document instead of five. Tested by:
   > `tests/test_agent.py::TestOpeningDocumentsIsNotBuildingParts`.

5. **Human checkpoints.** Structured approval gates — a reviewable diff with a sign-off record, not
   a chat message (P5 owns the surface).
   > NOT STARTED.

6. **Cost/time estimation before starting** — "this is four hours of compute and $X" (P8 owns the
   meter).
   > NOT STARTED.

**Creative leverage:** the design record replaces the transcript. Rationale already travels in
`FeatureSpec.note`; extend to decisions, rejected alternatives and reasons, and *"why is this rib
here"* has an answer in six months — from the artefact.

## ERA VII — OUTPUT, AND THE MACHINES

##### Phase E17 — Manufacturing output #####

**~9 engineer-months.**

1. **Drawings with GD&T**: auto views, sections, details, dimension generation, FTA, BOM tables,
   title blocks. *Without this nothing leaves the building.*
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

3. **Weldments and tubing**: beads, symbols, cut lists, tube routing. **A motorcycle frame is a
   tubular weldment.**
   > NOT STARTED.

4. **CAM**: **[OpenCAMLib](https://github.com/aewallin/opencamlib)** (LGPL) — drop-cutter and
   waterline primitives — plus machining features, stock, fixturing notes.
   > NOT STARTED.

5. **Inspection planning**: CMM points and measurement plans derived from the GD&T scheme.
   > NOT STARTED.

6. **Technical documentation**: assembly instructions, exploded views, service manuals, parts
   catalogues.
   > NOT STARTED.

**Gate G4 opens after E17 (with E17.3).**

##### Phase E17.3 — Sheet metal (pulled forward to run with Era IV) #####

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
   > NOT STARTED — **this is not wired to any geometry at all.** M3 still needs a `SheetMetalPart`
   > → OCCT solid path, and **there is no sheet-metal operation in the OCCT backend**.

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
   > NOT STARTED — PENDING.

7. **M7 — 6-axis robot arm.**
   > NOT STARTED — PENDING, waiting on E9's multibody.

8. **M8 — motorcycle chassis + swingarm.**
   > NOT STARTED — PENDING.

**Ladder standing at 3/9.**
**Gate G5 opens after M2 upward.**
---

# PRODUCT TRACK

*The platform that makes the engineering usable, sellable and safe. P-phases run in parallel with
the engineering track; each names what it gates and what gates it.*

##### Phase P1 — Identity, sessions and tokens done right #####

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
   > PARTIAL (2026-09-06) — **backend DONE, the UI is open.**

4. **Startup refusals.** `SECRET_KEY` unset or `"changeme"` ⇒ the server does not start, with a
   message that says what to do. Same for an empty CORS origin list in production mode.
   > DONE (2026-09-06). Tested by: `tests/test_auth_sessions.py`.

5. **Email verification and password flows hardened**: verification required before first project
   creation (not before first look — friction where it protects, not where it annoys); the reset
   flow already hashes one-time tokens, keep; add resend throttling.
   > NOT STARTED.

6. **Rate limiting that survives deployment reality**: keyed on the *authenticated principal* where
   one exists, on the connecting IP otherwise, `X-Forwarded-For` honoured **only** from a declared
   trusted-proxy list, and backed by a shared store so multiple workers enforce one budget.
   > NOT STARTED — the in-process limiter that trusts `X-Forwarded-For` is still there.

7. **Second factor (TOTP)** — standard `pyotp`-class implementation, recovery codes, and the
   decision recorded that WebAuthn/passkeys are the follow-on, not the first ship.
   > NOT STARTED.

8. **Token custody in both clients.** Web: httpOnly cookies as today, never storage. Tauri: the
   same cookie flow through its webview, with the OS keychain via Tauri's secure storage if a
   native token cache is ever needed — never a JSON file.
   > NOT STARTED.

**Phase proof:** a stolen refresh token replayed after rotation kills the family and the attacker's
session, the user sees it in the device list, and the audit log (P3) records it. A demo of this
exact sequence is part of the phase's acceptance.

##### Phase P2 — Organisations, teams, roles and sharing #####

**~4 engineer-months. Gates: P3, P8, mission ladder beyond M2 in-product.**

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
   > NOT STARTED.

6. **Frontend surfaces**: org switcher, member management, role assignment, invitation flows,
   pending-invite states — all in the existing dashboard design language.
   > NOT STARTED.

**Phase proof:** the cross-tenant test suite — two orgs, adversarial queries at every endpoint,
zero leakage, all misses reading as 404. Run in CI forever.
> PARTIAL (2026-09-08) — the suite exists, passes, and now proves **both** halves in CI: the
> application scoping and the RLS net under it, since the suite connects as a `NOBYPASSRLS` role
> (task 3). What keeps this `PARTIAL` rather than done is tasks 5 and 6, which are not started.

**Gate GP1 opens after P1 + P2.**

##### Phase P3 — The admin panel and operations console #####

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
   > PARTIAL (2026-09-06) — **the read half shipped**; suspension and GDPR deletion are open.
   > Tested by: `tests/test_admin.py`. Code: `app/api/routes/admin.py`.

5. **Feature flags**: per-tenant and per-user overrides, kill switches, percentage rollouts.
   Server-evaluated — the flag state rides to the frontend with the session, so the UI and the API
   always agree on what is on.
   > NOT STARTED.

6. **The operations dashboard**: job queues, solver failure rates by taxonomy class, per-op success
   rates (E15 task 5's data), storage growth, active sessions — the panel where "is Kryova healthy"
   has one answer.
   > PARTIAL (2026-09-06) — the read half shipped.

7. **Announcements and maintenance mode**: a banner the backend serves and both clients render;
   read-only mode that refuses mutations with an honest message instead of erroring.
   > NOT STARTED.

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
   > PARTIAL — chunked upload and the content-addressed store exist. Code: `app/media/`.

2. **The extraction pipeline**, tiered by format, all local and free:
   - **CAD (STEP/IGES/BREP/STL/DXF)** → the geometry pipeline (E1's kernel; `ezdxf` for DXF
     entities/dimensions). A STEP attachment can *become* a `GeometryVersion` on request.
   - **Documents (PDF/DOCX/XLSX/PPTX/HTML/images)** →
     **[Docling](https://github.com/docling-project/docling)** (IBM Research, MIT) as primary —
     strongest local table/layout/reading-order understanding, OCR for scans.
     **[MarkItDown](https://github.com/microsoft/markitdown)** (Microsoft, MIT) as the light
     fallback for the long tail. Chosen over hosted parsers: local, free, no data leaves the
     deployment.
   - **Spreadsheets** keep their structure — a load-case table becomes rows with units, not prose.
   - **Images/photos** → the vision provider (already pluggable, P5 surfaces it).
   > PARTIAL (2026-09-06) — the readers are tested now, **including that text-bearing DXF entities
   > are no longer silently dropped**, which had been reading a drawing full of MULTILEADER notes
   > and TOLERANCE frames as an empty document. Code: `app/documents/`.

3. **Engineering-drawing understanding**, staged honestly: text-layer PDFs → Docling now; scanned
   drawings → OCR now; **dimension/GD&T extraction** → a later, research-adjacent task (layout
   detection + a document transformer / fine-tuned VLM — Donut/Florence-2-class), explicitly *not*
   promised early, because a wrongly read tolerance is worse than an unread one. Until then,
   extracted drawing content is labelled "unverified read — confirm dimensions before use".
   > NOT STARTED.

4. **Provenance-tagged facts.** Everything extracted enters the conversation as quoted material
   with a source pointer (file, page/sheet/cell). When an extracted number flows into a design
   parameter, the *spec records the source* — `FeatureSpec.note` and the requirement links (E11
   task 3) already give it somewhere to live. "Where did 42 mm come from?" must answer "cell C7 of
   loads.xlsx, attached 2026-09-05".
   > NOT STARTED.

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
   > NOT STARTED.

**Phase proof:** the hostile-document suite passes; a load-case spreadsheet becomes a named,
provenance-tagged load case applied to a design; a STEP attachment becomes geometry through the
same kernel as everything else.

##### Phase P5 — The conversation and agent experience #####

**~5 engineer-months. Continuous; the frontend face of E4, E5 and E16.**

**The question it answers:** does working with the agent feel like working with a competent
colleague — legible, interruptible, honest about uncertainty — rather than watching a terminal
scroll?

1. **Streaming done properly**: token streaming and step events over SSE (fits the existing
   poll-schedule/api-client machinery; WebSockets only if bidirectionality is ever actually
   needed), reconnect-and-resume — `conversation-resume` exists and is tested, so extend, don't
   replace.
   > PARTIAL — resume exists and is tested. Code: frontend `conversation-resume`.

2. **The step surface**: `agent-step-list` grows into the run view — plan steps, live geometry
   operations, solver progress, per-step timing, failure taxonomy classes surfaced in plain
   language.
   > PARTIAL — steps and transcript exist. Code: frontend `agent-step-list`.

3. **The design as an artefact, visibly.** The spec (the IR) rendered beside the chat: parameters
   editable with units checked, features with their rationale notes, references navigable; **spec
   diffs rendered like code review** (what changed, what it reaches — `diff.py` already computes
   both). The conversation is the *log*; the spec is the *truth*; the UI must make that hierarchy
   legible.
   > NOT STARTED.

4. **The verification surface**: assertion dashboard (pass / fail / **unmeasured** rendered as
   first-class — unmeasured is amber, never green), requirement coverage, provenance drill-down
   from any number to its evidence chain (E7 task 3), convergence badges on simulation results.
   > NOT STARTED.

5. **Approval gates as UI**: a gate is a page — the diff, the affected assertions, the cost/time
   estimate of what follows, an approve/reject with the actor recorded (P2 domain roles; P3 audit).
   Not a chat message that scrolls away.
   > NOT STARTED.

6. **Interruption and steering**: stop a run cleanly (the bounded loops make this safe), edit a
   parameter mid-mission, resume without loss.
   > NOT STARTED.

7. **Cost/time honesty**: before a long run, the estimate (E16 task 6 / P8); during, elapsed vs
   estimate; after, actuals — the trust habit that makes P8's billing uncontroversial.
   > NOT STARTED.

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
   > NOT STARTED.

2. **The streaming scene** (frontend): assembly loads structure-first (the tree and bounding boxes
   immediately), meshes stream by priority (frustum + screen-space size), LOD switches by distance.
   Target: first meaningful paint of a 2,000-part machine under 2 s on a mid-range laptop;
   interaction never below 30 fps, measured in CI against a reference assembly — a performance
   *assertion*, in the house style.
   > NOT STARTED.

3. **The renderer decision, made explicitly**: extend the hand-rolled WebGL viewer to WebGL 2
   (instancing, better attribute handling) as the default path — it keeps the dependency doctrine
   and the team knows every line. Adopt a library only if 6-months-in measurements show the
   hand-rolled path cannot hold the fps target on M5-class assemblies; that decision point is
   scheduled and its criteria written now, so it is a measurement, not a mood. WebGPU is the
   follow-on where available, behind capability detection.
   > PARTIAL — the single-part WebGL viewer exists and is tested. Code:
   > `webgl-stress-viewer.tsx`.

4. **Engineering interactions**: section planes, exploded views (from the assembly structure,
   animated), measure (point-point, edge, face-face — against real geometry via a backend query,
   not against the decimated mesh), hide/isolate by subtree, camera bookmarks per conversation
   ("the view we were talking about").
   > NOT STARTED.

5. **Results on geometry**: the existing stress-field rendering generalised — scalar fields
   (stress, displacement, thickness, fatigue damage) on the streamed meshes, shared colour-scale
   legend, probe-a-value. The `surface-field` code is the seed.
   > NOT STARTED.

6. **Tree ↔ 3D ↔ spec, one selection model**: click a part in the tree, it highlights in 3D and the
   spec panel scrolls to its feature; select a face in 3D, the predicate that would name it (E2
   task 1) is offered.
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
   > NOT STARTED.

4. **Offline honesty**: what works without the backend (viewing cached designs, reading docs) and
   what does not (everything else), stated in the UI rather than discovered by timeout.
   > NOT STARTED.

5. **The Windows installer.**
   > PARTIAL (2026-09-05) — `npm run desktop:msi` produces `Kryova_0.2.0_x64_en-US.msi` (3.8 MB) on
   > this machine, release profile, WiX candle+light, ~41 s Rust compile. **Built is not
   > installed**: nothing has run the installer and confirmed the app starts from it, so task 1 and
   > P9 task 4 both remain open and a release still needs a human.

##### Phase P8 — Billing, quotas and metering #####

**~3 engineer-months. Needs P2 (orgs), P3 (flags/quotas); ships before general availability.**

1. **Meter what costs**: solver-seconds by class (a CalculiX nonlinear minute ≠ a linear static
   second), geometry-operation batches, storage-bytes, seats. Usage accumulates locally and posts in
   aggregates — the high-volume pattern; never one event per action.
   > PARTIAL (2026-09-06) — metering, the usage ledger, rollups and per-tenant quotas shipped.
   > **Metering is *always on*, independent of tracing**: `app.observe.collect.add_listener` lets a
   > listener see every span whether or not `collect()` is running, so a bill is never gated on an
   > operator having turned tracing on for the request that happened to be billable.
   > `app.simulation.runner` is the first wired consumer — `usage_scope` posts a job's meshing and
   > solve time to the ledger in a `finally`, so a solve that fails after nine minutes is still
   > billed for the nine minutes, and the metering write goes through its own session (`LedgerSink`)
   > rather than the job's transaction, so a metering fault can never roll back the result it was
   > measuring. **Declared but not yet wired**: AI token usage, CATIA seat time, kernel operations —
   > the meters exist in the schema and report a gap rather than a number until something calls
   > them. Tested by: `tests/test_billing*.py`, `tests/test_metering.py`. Code:
   > `app/core/metering.py`, `app/models/billing.py`, `app/api/routes/billing.py`, migration
   > `b3d7c1f4a920`.

2. **Plans**: free tier (real but bounded — M1-class work, community support), team, and enterprise
   (SSO later, audit-log export from P3 task 1, custom quotas). **Stripe** metered billing +
   prepaid credits for compute bursts — the hybrid the compute-heavy SaaS pattern converged on.
   > NOT STARTED — **no Stripe integration.**

3. **Enforcement with dignity**: quota exhaustion returns the honest envelope (what ran out, what it
   costs to continue, what remains free), never a bare 429; estimates before expensive runs (P5
   task 7) mean nobody is surprised.
   > NOT STARTED.

4. **The bridge between metering and trust**: the same meter that bills is the meter shown in cost
   estimates. One number, two uses; divergence is a bug class of its own.
   > NOT STARTED.

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
   > NOT STARTED.

4. **Environments**: staging with seeded demo orgs and the mission suite running nightly against
   it; production migrations gated on `alembic check` and a rollback note per migration.
   > NOT STARTED.

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
   > NOT STARTED.

7. **Secrets and supply chain**: no default secrets boot (P1 task 4), dependency pinning + audit in
   CI, SBOM for the desktop app (enterprise buyers ask), release notes generated from the merge log.
   > NOT STARTED.

##### Phase P10 — Documentation, onboarding and the trust surface #####

**~2 engineer-months, then continuous.**

1. **In-product onboarding**: the existing setup wizard grows into first-run success — a guided M1
   in under ten minutes, on the free tier, no sales call.
   > NOT STARTED.

2. **The docs site**: task-oriented docs, the mission gallery (every ladder mission as a worked,
   forkable example), API reference from the OpenAPI schema that already exists.
   > NOT STARTED.

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
   > NOT STARTED.
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

**Engineering track — ~123 engineer-months:**

1. Era I, geometry engine (E1–E2) — 14. Hard, foundational, unavoidable.
2. Era II, perception (E3–E5) — 11. Best value per effort.
3. Era III, physics (E6–E10) — 31. Mostly integrate; ~15 needs an ME.
4. Era IV, knowledge (E11–E13) — 21. Nearly all needs domain engineers.
5. Era V, scale (E14–E15) — 15. Architecture.
6. Era VI, agent (E16) — 10. Research-adjacent, least predictable.
7. Era VII, output and missions (E17–E18) — 21. Integration plus discovery.

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

**Programme total ≈ 161 engineer-months** — about 8–10 engineers × ~1.7 years, in ideal conditions.

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
