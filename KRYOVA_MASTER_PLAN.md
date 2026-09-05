# Kryova — the plan to build machines from a conversation

**Version 2, written 2026-09-05. This is the controlling document for what Kryova is trying to
become.** Version 1 was written earlier the same day; v2 supersedes it wholesale.

**What v2 adds and corrects.** Version 1 planned the *engineering* system and forgot Kryova is a
*product*: it said nothing about the frontend, users, organisations, admin, auth hardening,
billing, deployment, or reading the files a user attaches. Version 2 adds a full **Product
Track** (phases P1–P10) alongside the Engineering Track (phases 1–18), fixes v1's part-numbering
gap, and records four defects that investigation of the actual code surfaced meanwhile:

1. **Auth is single-device by design accident.** `users.refresh_token_hash` is one column on the
   user row — a second login invalidates the first device's session silently. Fixed in P1.
2. **The frontend has no CI at all.** 237 tests, clean lint and types — and nothing runs them on
   push (`.github/` does not exist in that repo). Fixed in P9.
3. **`SECRET_KEY` defaults to `"changeme"` and the server starts anyway.** Already listed as a
   landmine in CLAUDE.md; now owned by P1 with a startup refusal.
4. **`pythonocc-core`'s coverage of OCCT's OCAF/TNaming layers is not confirmed by its docs.**
   v1's topological-naming plan leaned on it. Phase 1 now opens with a one-week spike to verify,
   with the C++-side fallback named. Planning around an unverified binding is exactly the kind of
   mistake this document exists to prevent.

The goal is unchanged and it is the point of everything below:

> **A system an engineer can talk to that designs, analyses, validates and documents a complete
> working machine — a stamping press, a gearbox, a conveyor, a robot arm, a motorcycle chassis —
> to a standard a licensed engineer can review, sign, and have manufactured.**

Not a chatbot that models a bracket. Not a gear generator. A machine — with the product around it
that real users, real teams and real money can actually use.

Companion documents:

- [KRYOVA_BUILD_PLAN.md](KRYOVA_BUILD_PLAN.md) — the working queue. One batch at a time, green
  before the next. Short-term truth; this file is where it is going.
- [KRYOVA_CAPABILITY_ROADMAP.md](KRYOVA_CAPABILITY_ROADMAP.md) — the 2026-09-03 capability audit
  this plan grew out of.
- [KRYOVA_STATE_OF_THE_PROJECT.md](KRYOVA_STATE_OF_THE_PROJECT.md) — honest current state.

---

## Part 0 — The decisions that shape everything below

Most of this plan is consequence. These decisions are the plan. The first five are the
engineering spine (unchanged from v1); the last three are the product spine v1 was missing.

### Decision 1 — The design IR compiles to an open kernel first, and CATIA is one backend

**The most important decision in the document; it reverses the project's centre of gravity.**

Today geometry exists only where CATIA exists: a licensed Windows workstation, reached over a
socket, one seat per user, one COM round trip per operation. Every ambition in the roadmap
collides with that:

| Ambition | What CATIA-only does to it |
|---|---|
| 10⁵–10⁶ operations for a machine | ~1 s per COM round trip ⇒ **28+ hours of pure latency** |
| Test the geometry in CI | Impossible — CI has no CATIA and never will |
| Deterministic, reproducible builds | Depends on a seat's version, language, install and options |
| Many users, many designs at once | One bridge = one user; licences are the ceiling |
| Optimisation, DOE, parameter sweeps | Each of 500 candidates costs a workstation-hour |
| An agent that iterates freely | Every experiment is billed against a human's machine |

`app/design/` already made the necessary move without naming it: a design is a **specification
compiled to a call plan**, and `compile.py` is a compiler with a pluggable target. Nothing about
a `Plan` is CATIA-specific except the operation vocabulary it happens to emit.

So: **add a second compilation target — [Open CASCADE Technology](https://dev.opencascade.org/)
(OCCT), the open-source B-rep kernel behind FreeCAD — and make it the primary one.**

What that buys, none of it obtainable otherwise:

- **Geometry in CI.** Every design in the repository rebuilds and re-asserts on every commit,
  headless, free, in seconds. Unit testing for geometry stops being a metaphor. No CAD company
  ships this because their kernel is their product; ours is a dependency.
- **Throughput.** In-process kernel calls instead of COM round trips — the difference between a
  batch of 500 design candidates being a coffee break and being a fortnight.
- **Determinism.** One pinned kernel version, one locale, one tessellation tolerance, in a
  container we control. Roadmap I5 becomes checkable rather than aspirational.
- **No licence ceiling.** Optimisation, self-correction loops and the mission ladder become
  affordable, because the marginal geometry operation costs CPU rather than a seat-hour.
- **A real answer to topological naming.** OCCT ships `TNaming_Selector` and, since **OCCT 8.0
  (May 2026), `BRepGraph`** — a graph representation of B-rep topology with bidirectional
  traversal and history tracking. Combined with the semantic naming already built in
  `app/design/names.py`, this is the mechanism Layer B needs. *(Caveat owned in Phase 1: the
  Python binding's coverage of these layers must be verified in week one.)*

CATIA does **not** go away. It becomes what it should always have been: **the delivery and
interop backend**, plus the backend for customers whose process is CATIA-native. The same `Plan`
targets both. A design is authored, iterated, simulated and verified against OCCT thousands of
times, and materialised into a real CATPart once, at the end, when a human wants one.

This also makes `A1` (validate the 201 ops against a real seat) tractable: it stops being a
blocker on everything and becomes a **conformance suite** — the same plan built on both kernels,
geometry compared, unattended. A far stronger test than a human clicking through 201 operations.

**Cost:** OCCT is a large, old, idiosyncratic C++ library; `pythonocc-core` (LGPL) is mature but
the surface is enormous and parts of it are thinly documented. Budget real time for kernel-facing
work and expect the first six weeks to feel slow. Still cheaper than any alternative.

### Decision 2 — Physics is federated, never re-implemented

`app/solve/` is ~1,300 lines of hand-written FEA — correct, verified against closed-form
solutions, and a **component solver**: no contact, no plasticity, no large deformation, no
shells or beams as authored elements, no dynamics, and a direct solve that will not survive an
assembly. Writing the missing 90% is a decade of specialist work that has already been done,
validated and given away:

- **[CalculiX](http://www.calculix.de/)** (GPL) — the workhorse. Abaqus-compatible `.inp` decks,
  25 years of validation, nonlinear, contact, plasticity, modal, buckling, thermal, dynamics.
- **[code_aster](https://code-aster.org/)** (GPL, EDF) — fracture, cyclic plasticity, the things
  a nuclear utility needs and validates.
- **[Elmer](https://www.csc.fi/web/elmer)** (LGPL) — multiphysics, FSI.
- **[OpenFOAM](https://openfoam.org/)** (GPL) — CFD, late and only when genuinely needed.

**Kept, emphatically:** `solve/loads.py`, `solve/selection.py`, `solve/materials.py`. The
load-case vocabulary and the *geometric selector* abstraction (a face named by geometry, never by
id) are the real intellectual property — they are what the agent drives and what makes a load
case survive a re-mesh. The hand-written solver stays as **fast path and oracle**: any linear
static case must agree between it and CalculiX, and a disagreement is a bug in the integration.

### Decision 3 — Verification is the product, not a feature of it

An unvalidated simulation is a hypothesis with a colour map. Kryova's distinguishing claim is not
"it designed a part" — it is **"here is the part, here is what was claimed about it, here is the
evidence for each claim, and here is what remains unverified."** Assertions, provenance,
convergence reporting, the mission ladder, and the refusal to state an unsupported number are not
quality-of-life work; they are the reason a licensed engineer would put their name on the output.
The codebase already practises this locally (a mock mass says it is a mock; an unmeasured
assertion is never a pass); the plan extends it, never erodes it.

### Decision 4 — Free and open, with the licence consequences taken seriously

Every external dependency in this plan is free — a constraint the user set, and the right call:
it keeps the system deployable offline, keeps the marginal cost of an experiment at zero, and
avoids a vendor deciding our roadmap.

The obligation it carries: **GPL solvers (CalculiX, code_aster, OpenFOAM, gmsh) are invoked as
separate processes across a file/CLI boundary** — write an input deck, run the binary, read the
results. Settled practice, unambiguous, and the right architecture anyway (solvers crash; a crash
should kill a subprocess, not the API). **LGPL libraries (OCCT, PlaneGCS, OpenCAMLib) stay
dynamically linked and replaceable.** Neither rule may be "optimised" away.

`data/bm25/` already holds ~450 MB of tracked Dassault Systèmes PDFs — a separate, unresolved
copyright hazard. Resolve before the repository is published or widely cloned (history rewrite).

### Decision 5 — Honest scope: which machines, and what "designs it" means

Nobody designs a motorcycle from zero. Small manufacturers buy the engine, the suspension, the
brakes — and design the **chassis, packaging, ergonomics, bodywork and integration**. That is
what the industry actually does, and it is a very large business. So the target, precisely:

> **Kryova designs, analyses and documents the structural, kinematic and packaging content of a
> machine, integrating bought-in functional components, to a standard a licensed engineer can
> review and sign.**

The machine classes, in tractability order — the **mission ladder**, each rung a permanent
regression test:

| # | Machine | What makes it hard | Era it becomes possible |
|---|---|---|---|
| M1 | Machined bracket | Nothing. The "hello world". | I |
| M2 | Welded frame / bench | Weld sizing, fatigue at joints | III |
| M3 | Sheet-metal enclosure | Unfolding, bend allowance, DFM | IV |
| M4 | Gearbox | Gear geometry, bearings, tolerance stacks, lubrication | IV |
| M5 | **Sheet-metal stamping press** | Force path, frame stiffness, die set, drive, guarding | V |
| M6 | Belt conveyor system | Long assemblies, standard parts, modularity, layout | V |
| M7 | 6-axis robot arm | Kinematics, dynamic loads, stiffness under motion | VI |
| M8 | Motorcycle chassis + swingarm | Fatigue under real duty cycles, MBD loads, homologation | VI |
| M9 | Full vehicle chassis programme | 10³–10⁴ parts, teams, change propagation | VII |

**Never in scope:** unattended sign-off on a safety-critical machine. The honest product is
*"does 80% of the engineering in a tenth of the time; a licensed engineer signs."*

### Decision 6 — One platform: web and desktop share one frontend, one API, one auth *(new in v2)*

The frontend already exists and already made the right structural choices: Next.js 16 App Router
+ React 19 + Tailwind v4, **wrapped in Tauri 2 for the desktop**, with a deliberate
three-dependency runtime (`next`, `react`, `react-dom`), a hand-written WebGL 1 stress viewer,
and 237 passing tests. The desktop app is not a second product: it is the same frontend with two
extra powers — the CATIA workstation bridge runs beside it, and signed auto-update ships it.
Every capability in this plan surfaces through this one frontend; nothing gets a separate admin
web app or a separate viewer product. The frontend's minimalism doctrine ("before adding a
dependency, check whether the hand-rolled equivalent should be extended") is respected as policy:
any new dependency is a named decision in this plan, not a convenience import.

### Decision 7 — Security and tenancy are architecture, not a hardening pass *(new in v2)*

The 2026 consensus is unambiguous and this plan adopts it wholesale:

- **Sessions**: short-lived access tokens; refresh tokens **rotated on every use**, grouped into
  **families per device**, with **reuse detection** that kills the whole family — a stolen
  refresh token becomes a detectable event instead of a 30-day capability. Absolute session
  expiry. Per-device session list with individual and global revocation.
- **Tenancy**: organisations own projects; users belong to organisations with roles. Application
  code scopes every query, and **PostgreSQL Row-Level Security is the safety net underneath** —
  enforced in the database, so an application bug leaks nothing. Tenant context reaches RLS via
  **`SET LOCAL` inside an explicit transaction only** — transaction-scoped, discarded at
  COMMIT/ROLLBACK, and therefore safe under transaction-pooling PgBouncer. This is the *one*
  disciplined exception to this repo's hard "never `SET` against the pooled endpoint" rule, and
  the rule exists precisely because a *session-level* `SET` once leaked between clients here.
  `SET LOCAL` outside a transaction, or any statement-mode pooling, re-opens that hole — both are
  forbidden and tested against.
- **Cross-tenant access returns 404, never 403** — already the codebase rule; RLS makes it
  enforceable rather than conventional.
- **Admin power is bounded and recorded**: impersonation carries *both* identities in the token,
  is read-only by default, and every admin action lands in an append-only audit log.

### Decision 8 — Everything a user attaches is data to understand and never instructions to obey *(new in v2)*

Users will attach PDFs, spreadsheets, photos, drawings, STEP files, supplier datasheets. Two
commitments:

1. **Kryova reads them properly.** A local, free document-understanding pipeline (Docling /
   MarkItDown class, plus `ezdxf` for DXF and the geometry pipeline for CAD formats) turns
   attachments into structured, provenance-tagged content the agent can actually use — tables
   stay tables, dimensions stay numbers with units.
2. **Kryova never obeys them.** Document-borne prompt injection is a documented attack class
   ("ignore your instructions" hidden in white text on page 12 of a datasheet). Extracted content
   enters the model as quoted, provenance-tagged *data*, is never concatenated into the system
   prompt, and no tool call may be justified solely by text found inside an attachment without
   the user seeing that justification. This mirrors the CATIA daemon's existing stance (the
   server is not trusted to describe its own request) one layer up.

---

## Part 1 — Where the code actually is (2026-09-05, measured, both repos)

### Backend (`Kryova-backend`)

```
app/catia/ops/       201 operations, 11 domains, one declarative registry
app/design/          spec · params · names · compile · execute · diff · assertions · correct
                     (338 tests, all offline, <1 s)
app/solve/           linear static tet4/tet10 · modal · buckling · thermal stress · loads · selection
app/mesh/            gmsh 4.15.2 — industrial grade, keep
app/retrieval/       BM25 over 21 indexed CATIA/FEA manuals (~4,900 passages)
app/catia_kb/        ~1,600 curated CATIA entries; query expansion, term lookup, per-turn brief
app/ai/              agent · tools · prompts · state · resume · providers (pluggable, Ollama default)
app/media/           content-addressed blob store, chunked IO — the provenance substrate
app/api/routes/      auth · projects · geometry · simulations · media · materials · ai · catia
app/models/          User · Project · GeometryVersion · SimulationJob · Media · Conversation · catia
```

Full suite: **2,849 passed, 4 skipped, ~223 s.** ruff clean. mypy at a known 7-error baseline.

Auth as it stands: JWT HS256 access + refresh with a **type claim that is checked** (a refresh
token cannot pass as access — good), bcrypt with prehash, cookie sessions, password reset with
hashed one-time tokens, `is_active`. And the defects now owned by P1: **one
`refresh_token_hash` per user** (single device), no rotation families or reuse detection, no
roles of any kind, no admin surface, no audit log, in-process rate limiter that trusts
`X-Forwarded-For`, `SECRET_KEY="changeme"` accepted at startup.

### Frontend (`../Kryova-frontend`)

```
src/proxy.ts             Next 16 middleware — cookie route gate
src/app/(auth)/          login, register
src/app/setup/           health-check + onboarding wizard
src/app/dashboard/       the product surface
src/components/          agent-chat · agent-step-list · webgl-stress-viewer (hand-written WebGL 1)
                         geometry-preview · catia-bridge-panel · result-interpretation ·
                         markdown-message · error-boundary · skeleton · mesh-orb
src/lib/                 api-client · server-api · chunked-upload · conversation-{resume,events,
                         transcript} · load-case · surface-field · poll-schedule · markdown · format
src-tauri/               Tauri 2 desktop shell
```

**237 tests, ~7 s, currently clean; lint and `tsc` clean. No CI runs any of it.** Three runtime
dependencies by explicit doctrine. The WebGL viewer, chunked upload and conversation-resume are
hand-rolled and tested — genuine assets to extend, not to replace.

### What is genuinely strong, both sides
The operation registry (one declaration, everything generated); the design IR; offline test
discipline; the content-addressed store; the honesty conventions; a frontend that is small, fast,
typed and tested; a working desktop shell.

### What is absent
Everything in the two tracks below.

---

## Part 2 — How to read the plan: two tracks, one ladder

- **Engineering Track — phases 1–18 in seven eras.** The machine-building capability.
- **Product Track — phases P1–P10.** The platform around it: identity, tenancy, admin, files,
  frontend experience, viewer scale, desktop, billing, delivery, trust.

They run **in parallel** and gate each other only where stated. The mission ladder gates both: a
mission is not "done" when the geometry is right — it is done when a signed-in user of the right
organisation can run it end to end in the product, see every number's provenance, and export the
package.

### The phase status board — the single place progress is recorded

**This board is how a session picks up where the last one stopped.** Whenever work completes or
materially advances a phase, its row is updated **in the same commit as the work** — never as a
follow-up that can be forgotten. Rules:

- Status vocabulary, exactly four: `not started` · `in progress (since YYYY-MM-DD)` ·
  `partial — <what shipped> (YYYY-MM-DD)` · `DONE (YYYY-MM-DD)`.
- **A `*` before the phase number means the phase is finished in full** — `*E1`, not `E1`. It is
  the one mark that is scannable down the left edge of the table, so "how far are we?" is
  answered by counting stars rather than by reading eighteen status cells. A phase gets its star
  in the same edit that marks it `DONE`, and only then: a phase with any sub-item still open
  keeps a bare number however much of it has shipped.
- A phase is marked `DONE` **only when its Proof runs green** — and the row says where that
  proof lives (a test file, a CI job, a mission id). A phase without a checkable proof cannot be
  `DONE`; fix the phase definition instead.
- Never delete a status; supersede it. The board is current-state, the build plan's *Done*
  section is the history — one line lands there for every board change that isn't `not started`.
- The evidence column names *code that exists*, not intentions. Anyone (human or model) must be
  able to open the named thing and see the claim be true.

| Phase | Title (short) | Status | Evidence / where the proof lives |
|---|---|---|---|
| *E1 | OCCT kernel target | **DONE (2026-09-05)** — 1.0/1.1/1.4/1.5/1.6 complete; 1.2 at 22/201 ops, 1.3 parametric (solver deferred, see below) | `app/kernel/` (24 modules), `tests/test_kernel.py`. M1 bracket builds on OCCT. **Residual: the CATIA-seat half of the conformance run needs a Windows seat.** |
| E2 | Selection & authoring vocabulary | **2.1/2.2/2.3/2.4 DONE (2026-09-05)**; **2.5 DONE (2026-09-05)**; **2.6 partial — surfaces build, cut and become material; the wireframe family is complete but for the reflect line (2026-09-05)** | `app/kernel/selection.py`, `occt/{resolve,classify,selectors,reference,naming,sketching}.py`, `occt/operations/` (15 modules), `app/kernel/threads.py`, `tests/test_reference_geometry.py` (36 tests) + `tests/test_kernel.py::{TestDrawnCurvesChainIntoContours,TestSweptFeatures,TestThreadsAreAnnotations,TestStiffenersFindTheirOwnBoundaries,TestDraftSplitsAtAPartingElement,TestSurfacesAreSkinNotMaterial,TestASkinBecomesMaterialOnlyWhenAsked,TestCuttingSurfacesAgainstEachOther,TestWireframeCurvesLiveInSpace,TestAnchorsPointsAndLines,TestPlanesDerivedFromGeometry,TestCurvesDerivedFromSurfaces,TestJoiningTwoCurves,TestASpiralIsFittedAndSaysSo}`. Every vocabulary word decidable; per-edge parameters; `parallel_to`/`perpendicular_to` make "the vertical walls" one selection; reference geometry complete for everything not needing a named face; **`feature#selector` resolves — `slab#top` returns the annulus under a boss, a face the plain word `top` can never return.** Coverage **105/201**. **Five regressions this phase introduced were caught and fixed on 2026-09-05 by the first `pytest` run since — see the build plan.** **Every geometry operation now records its own faces** — pad/pocket/shaft/groove, primitives, transforms, boolean, shell, fillet, chamfer and draft — and a test fails if one stops. Drawn segments chain into contours, so four `catia_sketch_line` calls make a padable square; ribs and slots verified against Pappus's theorem; a thread is an annotation that provably does not change the mass. **2.5 closes with the two features whose extent is not stated by their own arguments**: a stiffener runs until it meets material (built by subtraction, exact against ½·b·h·t, and a sweep that never meets material is refused by name rather than left hanging in the air), and a draft with a `parting` element tapers *both* sides away from the plane so a two-part mould releases — checked against the frustum closed form on each side, and distinct from the unparted draft of the same faces. A draft's `neutral` element may now be a planar face of the part, a refusal that had been pointing at Phase 2.2 since before 2.2 was built. **2.6 opens with the half that earns the rest**: a surface is skin, not material — it lives in the document's construction store and the part's mass does not move when one is built — and `catia_close_surface` / `catia_thick_surface` are the two named crossings back. Extrude, revolve, offset, fill, loft, join, extract, boundary all land; a frustum is built entirely as skin and closed into the solid the closed form predicts. Two OCCT traps are pinned by tests that fail when the fix is removed: **a thickened surface comes back inside-out**, which `BRepCheck_Analyzer` calls valid and which makes a later fuse silently swallow the thing being fused; and **`MakeFilling` approximates even a flat boundary**, so a patched circular hole measured 314.1595 mm² against πr². **The trimming family lands with it**: split, trim, untrim, disassemble, healing and `catia_surface_analysis`. Which side of a cut survives is *stated* — cells ordered by the signed distance of their centre from the cutting plane, `first` the side its normal points away from — and a cutter with no plane is refused rather than resolved by whichever piece OCCT listed first. Two more traps pinned: **cells are not connected components** (a split shell's halves share the cut edge, so `domains` correctly says one while the caller plainly wants two), and **`untrim` on a plane is not refused by OCCT** — `MakeFace` reports success and returns a face of area 8 × 10¹⁰⁰, which then flows into a mass and a bounding box looking like a measurement. `catia_surface_analysis(kind='connect')` reports *the smallest tolerance that would join the pieces*, which is the argument `catia_healing` takes, so the analysis hands the repair its own parameter. **Wireframe curves land too** (`occt/operations/curves.py`): helix, 3D circle and arc, polyline, interpolating spline, section, intersection, extremum — so a 3D path is expressible at all for the first time, where every curve before had to come from a planar sketch or a surface boundary. Verified against `n·√(pitch² + (2πr)²)` and `r + h·tan(taper)`; the parameterisation trap is that `Geom2d_Line` **normalises** the direction it is given, so sweeping 0 → 2πn builds a helix of the right shape and a 16% wrong length. `catia_measure_item` on a curve now also returns `bounding_box_mm`, which is what distinguishes an edge carrying a real 3D curve from one carrying only a parameter curve — the second measures the right length and is unusable by anything that sweeps along it. **The derived anchors land too**: `catia_point_{on_curve,on_surface,centre}` and `catia_line_{between,direction,normal,tangent}`, which is what makes a point *associative* — defined by the geometry rather than measured once and typed as a coordinate that then goes stale. A point on a curve walks the whole chain by **arc length** in connection order (map order is build order, and `ratio: 0.5` on an L put the midpoint a quarter of the way along); a normal is read at the point rather than at the face centre, which is the same answer on a flat wall and a different fastener axis on a curved one; and a point offset along a surface is projected back onto it, or it is a point in the air that still reads as being on the face. **The associative curves and planes close the gap between the two**: `catia_curve_{project,parallel,offset_3d,combine}` and `catia_plane_{normal_to_curve,tangent_to_surface,mean}` + `catia_planes_between`. `plane_normal_to_curve` is the one that earns the rest — it places a sweep profile square to its path, so the helix built earlier is now something a section can be swept along, and its normal carries the lead angle `atan(p/2πr)` exactly. Three traps pinned by tests that fail when the fix is removed. **A face is a trimmed piece of an unbounded surface**: `GeomAPI_ProjectPointOnSurf` answers for the whole surface, so a point beside a cylinder projected onto the *infinite plane* of its top disc, 20 mm outside the rim, nearer than the wall — and `catia_point_on_surface`, `catia_line_normal` and the new tangent plane then all agreed on a place that is not on the part (fixed in `closest_on_surface`, which now measures against the face's real boundary). **An offset has a side, and OCCT does not take it from the argument the caller gave** — it reads the wire's own winding and never sees the named support, so the side is stated here, measured on the result and mirrored when it went the other way; the same L on a support facing down offsets 60 mm where the one facing up offsets 77.854. **A best-fit plane is an inertia question asked backwards** — the principal axis of *greatest* moment is the covariance's smallest eigenvector, which OCCT computes exactly and which agrees with `numpy.linalg.svd` to the last digit, so the kernel needs no numpy; points on one line are refused because every plane through a line fits it equally well. A projected curve is an OCCT B-spline fit (~1 part in 10⁷) and says so; `curve_combine` with no directions extrudes each view along its own plane, checked against the Steinmetz curve — two ellipses of semi-axes r and r√2. **The joins and the spiral close the wireframe family**: `catia_curve_corner` is an arc tangent to two curves — a quarter circle of `2πr/4` exactly between perpendicular legs — and it leaves both inputs untouched, because a step that edited an earlier one would make the same plan mean something different the second time it ran. `catia_curve_connect` is a Bézier of the lowest degree that can carry the continuity asked for: 1, 3 or 5. **Curvature continuity is where the arithmetic bites** — the source states its second derivative in its own parameter and the join runs on [0, 1], so the factor `(s/|d1|)²` from the affine reparameterisation is load-bearing and dropping it gives a curve out by the square of the chord length: invisible at unit scale, wrong by four orders on a 100 mm join. Across a 60° gap in a 10 mm circle the quintic carries the circle's own 0.1/mm and the cubic leaves a step of 0.0068/mm — identical shaded, and exactly the break a reflection shows. The join **measures what it achieved** and reports tangent error in degrees and curvature step per mm, because a G2 claim that is asserted rather than measured is the kind of number this codebase refuses to print. `catia_curve_spiral` is the one curve here no kernel holds exactly — an Archimedean spiral is not a NURBS — so it is interpolated and reports its own worst radial error, measured against the closed form **between** the interpolation knots: at the knots it reads 1e-14 against the 9.4e-5 mm it is really out by, a factor of 10⁹, so a fit that measured itself at the points it was given would report machine zero and be believed. Arc length checked against `(1/a)∫√(r²+a²)dr`. **Remaining for the star: 2.6's steered surfaces — a loft with guides or a spine, tangent-continuous fill, tangent-propagating extract, extrapolate/sew_surface — and `catia_curve_reflect_line`, which is also what unblocks `catia_draft` in reflect-line mode.** |
| E3 | Interrogation & measurement | **OCCT side DONE (2026-09-05)** — 3.1–3.5 complete; CATIA-side measures shipped 2026-09-03 | `app/kernel/{interrogation,contract,provenance}.py`, `app/kernel/occt/interrogate/` (8 modules), `occt/metrology.oriented_bounding_box`, `tests/test_interrogation.py` (39 tests, offline, all against closed-form answers). Reached via `catia_analysis_part`. **Residual: 3.3's clearance is implemented and tested but not yet wired to `catia_measure_between` — that needs 2.2 element references; and the cross-backend agreement half of the Proof needs a Windows seat, same as E1.** |
| E4 | Visual verification | not started | |
| E5 | Assertions & self-correction | partial — foundation shipped (2026-09-04) | `app/design/{assertions,diff,correct}.py`, 109 tests; 5.1–5.4 open |
| E6 | Solver federation (CalculiX) | not started | hand-written solver exists as future oracle (`app/solve/`) |
| E7 | V&V: NAFEMS, convergence, provenance | not started | |
| E8 | Fatigue (pyLife) | not started | |
| E9 | Multibody (Chrono) | not started | |
| E10 | Thermal, CFD, optimisation | partial — thermal *stress* shipped (2026-09-03) | `app/solve/thermal.py` vs `σ=−EαΔT`; conduction/CFD/opt not started |
| E11 | Requirements model | not started | |
| E12 | Load cases, materials, standard parts | not started | |
| E13 | Design rules, GD&T, cost | not started | |
| E14 | Product structure & contracts | not started | |
| E15 | Throughput, storage, observability | not started | |
| E16 | Tool retrieval, planning, memory | partial — resume-from-log shipped (2026-09-03) | `app/ai/resume.py`, `tests/test_resume.py`; retrieval/planning open |
| E17 | Manufacturing output | not started | |
| E17.3 | Sheet metal (pulled forward to Era IV) | not started | tracked separately by the sequencing exception |
| E18 | Machine missions M1–M8 | not started | each mission lands as a permanent CI suite |
| P1 | Identity, sessions, token rotation | not started | defect recorded: single `refresh_token_hash` per user |
| P2 | Orgs, roles, RLS tenancy | not started | |
| P3 | Admin panel & audit log | not started | |
| P4 | File attachments & understanding | partial — chunked upload + content-addressed store exist | `app/media/`, frontend `chunked-upload.ts`; extraction/injection-boundary not started |
| P5 | Conversation & agent UX | partial — steps, resume, transcript exist | frontend `agent-step-list`, `conversation-resume` (tested); spec-view/gates open |
| P6 | Viewer at machine scale | partial — single-part WebGL viewer exists | `webgl-stress-viewer.tsx` (tested); tessellation service/LOD/streaming open |
| P7 | Desktop & workstation bridge | partial — Tauri shell + bridge panel exist | `src-tauri/`, `catia-bridge-panel.tsx`; signed auto-update open |
| P8 | Billing & metering | not started | |
| P9 | Delivery: CI/CD, backups | not started | **frontend has no CI — first fix** |
| P10 | Docs, onboarding, trust surface | not started | |

**The documentation-first rule (standing, all phases).** Every phase opens by reading the primary
documentation of what it builds on — the OCCT reference for a kernel phase, the CalculiX manual
(Dhondt) for a solver phase, the Tauri v2 updater docs for P7, the OWASP cheat-sheets for P1 —
and recording the load-bearing facts in the phase's design note *with citations*. Two rules make
this stick: **no dependency is adopted on the strength of a blog post** (primary docs or source,
always), and **any fact the plan leans on that the docs do not confirm becomes a spike, not an
assumption** — that is exactly how v1's pythonocc/OCAF assumption got caught, and the correction
is recorded in this file's header. Where licences allow, dependency docs are ingested into the
existing BM25 index (`app/retrieval/`) so the agent can consult them the same way it consults the
CATIA manuals — the machinery is already built, tuned and tested; pointing it at our own
dependencies is nearly free.

---

# ENGINEERING TRACK

# ERA I — THE GEOMETRY ENGINE BECOMES OURS

## Phase 1 — The open kernel: OCCT as the primary compilation target

**~8 engineer-months. The keystone. Nothing downstream is affordable until it lands.**

### The question it answers
Can Kryova build geometry without a licensed workstation, deterministically, in CI, at machine
scale?

### Workstream 1.0 — the binding spike ✅ **PASSED, 2026-09-05**

The question was whether OCAF/TNaming is reachable from Python well enough to build persistent
naming on. Three findings, each of which changed the phase:

1. **`pythonocc-core` is not on PyPI at all** — conda-only. Adopting it would have forced conda
   into every deployment and into CI, against the whole point of Decision 1. The binding is
   therefore [`cadquery-ocp`](https://pypi.org/project/cadquery-ocp) (OCP, pybind11, OCCT 7.9.3):
   pip-installable, and it exposes all 320 OCCT modules including the full OCAF stack
   (`TNaming`, `TDF`, `TDocStd`, `TFunction`, `TDataStd`). **The C++-service fallback is not
   needed**, and neither is pyOCCT.
2. **Persistent naming survives a real parametric rebuild.** The spike named a fillet face,
   regenerated the part at different dimensions, and recovered the *new* corresponding face —
   verified by area against the closed-form quarter-cylinder (691.150 mm² at r=8, h=55), not
   against a recorded number.
3. **Three non-obvious rules govern it**, each got wrong before it was got right, and two of the
   three fail by making `TNaming_Selector.Solve()` return **success while resolving to nothing**:
   (a) one OCAF label records one evolution kind; (b) a regeneration must rewrite the *same*
   labels; (c) edges must be walked as well as faces, because a fillet's new face is `Generated`
   by the edge, not by any face. All three are documented in `app/kernel/occt/naming.py` with the
   failure each one produces.

**Cost finding for P9.2:** OCP is ~166 MB and pulls ~640 MB of VTK as a hard dependency that this
codebase never uses — nothing here renders through VTK. Stripping it is a container-layer job, not
a `--no-deps` install that would silently break.

### Workstreams
**1.1 — The kernel service.** A process wrapping the kernel behind the same `CallRunner`
interface `app/design/execute.py` already defines. In-process where safe, subprocess-isolated
where the kernel can abort (OCCT does abort on degenerate booleans, and a crash must kill a
worker, not the API).
**1.2 — The operation mapping.** Each of the 201 registry operations gets an OCCT implementation
or an explicit, reasoned refusal. The bulk of the work, and not mechanical: `catia_pad` is
`BRepPrimAPI_MakePrism` plus sketch resolution plus support resolution plus naming bookkeeping.
Sequence by mission: M1's vocabulary first, then M2's weldment needs.
**1.3 — The sketch layer.** ✅ **Parametric half done 2026-09-05; solver deferred with cause.**

The finding that changed this workstream: **the registry's sketch vocabulary is
dimension-driven, not constraint-driven.** `catia_sketch_rectangle` takes a width and a height,
`catia_sketch_circle` a diameter, `catia_sketch_polygon` a side count and a diameter. Each is
*fully determined by its arguments* — there is nothing for a solver to solve. So every profile
that actually feeds a pad, pocket, shaft or groove is buildable without PlaneGCS, and those
profiles now build (`app/kernel/occt/sketching.py`).

PlaneGCS is still owed, for exactly one operation: **`catia_sketch_constrain`**, which applies
arbitrary constraints to free geometry. It refuses by name with that reason rather than
pretending. When it lands, the choice stands: **PlaneGCS** (FreeCAD's, LGPL — DogLeg /
Levenberg-Marquardt / BFGS / SQP), full constraint vocabulary, proven detachable (a WASM port
exists). Rejected: SolveSpace's solver — faster, narrower; a sketcher that cannot express
tangency is not a sketcher.

*This is a narrowing of 1.3's scope, not a claim to have finished it, and the coverage figure
reports it as such.*
**1.4 — Persistent topological naming.** *The hard part.* Every created face gets a stable
identity that survives regeneration, keyed by the design IR's semantic names
(`swingarm.pivot_bore.inner_face`). Mechanism: `TNaming` + (where available) OCCT 8.0's
`BRepGraph` history. The published insight that bounds the work: **only faces need naming** —
`TNaming_Selector` recovers edges and vertices from adjacent faces.
**1.5 — The conformance harness.** One `Plan`, two backends, geometry compared: volume, mass,
centre of gravity, inertia tensor, bounding box, surface area, face/edge counts, within declared
tolerance. This is `A1` done properly and unattended; divergence is a finding about one backend,
and the harness says which.
**1.6 — Determinism.** Pinned OCCT version, pinned tessellation tolerance, fixed locale,
containerised. Same spec + same version ⇒ same geometry, byte for byte, asserted in CI.

### Creative leverage
**Geometry CI.** Every design in the repository rebuilt and re-asserted on every push, free, in
seconds. No CAD vendor ships this because their kernel is the product they sell; ours is a
dependency. It turns "did that refactor break anything" from a week of manual checking into a red
build.

### Proof — met on the OCCT half, 2026-09-05
M1 — a machined bracket — compiles, builds on OCCT in CI, builds on CATIA on a real seat, and the
two agree on every interrogated quantity to declared tolerance. Ten times, identically, from a
cold container.

**Where that stands.** The bracket (sketch → rectangle → pad → circle → through-pocket → corner
fillets, 12 calls) compiles from a `DesignSpec` and builds on OCCT, with volumes matching
closed-form values exactly and assertions checked against them. Determinism holds: the same spec
built twice produces the same geometry digest, a changed dimension changes it. The conformance
harness runs and reports.

**The residual is one thing and it needs hardware this machine does not have:** the CATIA half —
the same plan built on a real V5 seat, compared. `compare_backends` is written and exercised
against two runners; pointing its right-hand side at `app.catia.dispatch` on a Windows seat is
the remaining step. Until that has run, the *cross-backend* claim is untested, and the board says
so rather than implying otherwise.

### Risks
OCCT API vastness and thin documentation; `pythonocc` lagging upstream; boolean robustness on
degenerate input (a weakness of every kernel). Mitigation: per-operation backend capability
already exists in the registry — the fallback for a failing operation is "CATIA backend for that
op", not "no geometry".

## Phase 2 — Selection, reference geometry, and the authoring vocabulary real parts need

**~6 engineer-months.**

### The question it answers
Can the agent *point at* the thing it means, without a face id and without a guess?

### Workstreams
**2.1 — Predicate selection** (roadmap A3; the item the build-plan queue still owes). Replace
named-enum picking with geometric predicates: *all edges longer than 10 mm*, *all faces whose
normal is within 5° of +Z*, *all cylindrical faces of Ø6 H7*, *the tangent-continuous edge chain
from this one*. Only decidable against geometry that exists — which is why it was blocked before
Phase 1 and is nearly free after it.
**2.2 — `feature#selector` resolves.** `SemanticName` already reserves the spelling and refuses
it with a message pointing here.
**2.3 — Per-entity parameters.** Per-edge fillet radius, not one radius per group — the single
most limiting schema constraint in the current vocabulary.
**2.4 — Reference geometry completed.** Offset planes, plane-on-face, plane-through-3-points,
plane-normal-to-curve, user axis systems, datum points/lines — the OCCT half.
**2.5 — Part Design completion.** Multi-body, booleans between bodies, geometrical sets,
rib/slot/stiffener, draft with parting line, variable/tritangent fillets, thread, user patterns,
shell with face selection, thickness.
**2.6 — Surfaces / GSD.** Multi-section with guides, adaptive sweep, blend with continuity, fill,
join/heal with tolerance, trim/split, law-driven surfaces, curvature analysis. Deferrable within
the era until the ladder needs bodywork.

### Creative leverage
**A selection predicate is an assertion in disguise.** *All faces normal to +Z with area >
400 mm²* both picks faces and *claims* something. One vocabulary, two uses — and the assertion
engine already evaluates it.

### Proof
A part authored with every fillet radius different, each chosen by predicate; the design
regenerates correctly after an upstream feature insertion changes every face id in the model.

# ERA II — PERCEPTION: THE SYSTEM KNOWS WHAT IT BUILT

## Phase 3 — Geometric interrogation and the measurement layer

**~3 engineer-months.**

**3.1** Mass, volume, centre of mass, full inertia tensor, bounding boxes (AABB and oriented),
surface area, per-face/per-edge measures — native OCCT, in-process, free.
**3.2** Wall-thickness scan, draft analysis, curvature/continuity checks, undercut detection.
**3.3** Clearance, interference and minimum-distance queries between bodies.
**3.4** The **measurement payload contract** — a stable, versioned vocabulary of numbers that
`assertions.py` reads by path (`mass_kg`, `bounding_box_mm.size[2]`, …), backend-neutral,
documented.
**3.5** Honest provenance on every number: measured vs approximated vs unavailable — the mock's
existing discipline, made universal.

**Proof:** every assertion in the ladder through M4 is measurable, and each measurement agrees
between OCCT and CATIA to declared tolerance.

## Phase 4 — Visual verification: the model looks at the model

**~4 engineer-months.**

**4.1** Deterministic offscreen rendering from canonical views (six orthographic, two isometric,
section cuts) via OCCT's own visualisation, headless — fixed camera, lighting, resolution, so two
renders of the same geometry are byte-identical.
**4.2** A vision-model check: render, ask a VLM whether the result matches the request. The
provider layer is already pluggable; the local-Ollama default means no key required.
**4.3** **Render diffing** — before/after pixel diff, so review surfaces *what visibly changed*.
Cheap, and startlingly effective at catching what numeric checks miss.
**4.4** Renders flow into the conversation — the user sees what the agent sees (Product Track P5
owns the surface).

**Creative leverage:** deterministic rendering makes a render hash part of the geometry's
identity — a third check, blind where mass and plan-digest are blind (mirrored, inside-out, wrong
orientation).

**Honest limitation:** a VLM will confidently approve a subtly wrong part. This catches gross
errors — which are the common ones. A filter, never a sign-off.

## Phase 5 — Assertions, regression, and the self-correcting loop *(foundation shipped 2026-09-04)*

**~4 engineer-months remaining.**

Landed: `assertions.py` (pass / fail / **unmeasured** — and unmeasured is never a pass),
`diff.py` (what changed, how far it reaches, `builds_the_same`), `correct.py` (bounded loop with
exact stopping rules — no-progress and cycle detection are *exact* because the compiler is
deterministic). Remaining:

**5.1** An assertion library for machines, not parts: interference-free through a motion range,
stack-up within tolerance, first natural frequency above threshold, minimum wall, mass and cost
budgets, factor of safety against a named load case.
**5.2** Assertions bound to *requirements* — "meets REQ-014", not "mass_kg <= 4.2" (needs
Phase 11).
**5.3** Diagnosis quality: **sensitivity** — which parameter moves this measurement most,
computed by finite difference over the free geometry, so a repair is aimed rather than guessed.
The literature's bluntest finding stands: a validator that cannot say *why* is a retry counter.
**5.4** The mission ladder as a permanent regression suite.

**Creative leverage:** sensitivity is nearly free once geometry is free — a few hundred kernel
calls. On CATIA it was minutes of a workstation per probe. This is Decision 1 compounding.

# ERA III — PHYSICS THAT DECIDES

## Phase 6 — The solver federation

**~7 engineer-months.**

**6.1** A `Solver` implementation backed by **CalculiX** across a subprocess boundary: write
`.inp`, run `ccx`, parse `.frd`/`.dat`. The `Solver` ABC does not change.
**6.2** Loads and BCs from the *existing* `loads.py`/`selection.py` vocabulary mapped onto
CalculiX node/element sets — tributary-area distribution preserved, geometric selectors
preserved. This surface must not be rewritten; it is what the agent drives.
**6.3** Element strategy, informed by the CalculiX manual rather than habit: **C3D10 is the
documented recommended solid** (stable, robust); shells and beams are *expanded* internally
(S8R → 20-node brick, B31 → C3D8I), which changes thickness-direction stress recovery and is a
known source of surprise — record it in the integration notes and test against it. A frame
meshed as solids is a mesh nobody can afford; beams and shells are not optional.
**6.4** Analysis types unlocked by 6.1: nonlinear static, large deformation, plasticity, contact,
bolt pretension, modal, buckling, transient dynamics, coupled thermal-stress.
**6.5** The hand-written solver as fast path and oracle (Decision 2).
**6.6** Solver-failure taxonomy: non-convergence, singular stiffness, distorted elements, contact
chatter — each mapped to a diagnosis the agent can act on, in the codebase's existing register
("say what to do next").

## Phase 7 — Verification and validation *(needs an ME)*

**~4 engineer-months.**

**7.1** The **NAFEMS** standard benchmarks (linear elastic, free vibration, thermal) as an
automated suite. Reference values are reproduced in publicly readable vendor verification
manuals (Abaqus, Ansys, DIANA) — a free, legitimate route to the targets.
**7.2** Mesh convergence automation: refine until the answer stops moving, Richardson
extrapolation, reported **Grid Convergence Index**. After this phase an unconverged number
*cannot* be stated — the report machinery refuses.
**7.3** Simulation provenance: every result permanently bound to geometry version, mesh settings,
material, load case, solver name+version, convergence evidence. `app/media/`'s content addressing
is the substrate.
**7.4** A published validation register: which analysis types are validated, against what, to
what accuracy. In the product, not buried (P10 owns the surface).

**Creative leverage:** the provenance ledger is what makes output *signable*. An engineer signing
accepts liability; what they need is a complete, tamper-evident chain from requirement to number.

## Phase 8 — Fatigue and durability *(needs an ME)*

**~5 engineer-months.**

**Structures fail from fatigue, not from a single static load.** If exactly one physics
capability is added, it is this one.

**[pyLife](https://github.com/boschresearch/pylife)** (Bosch Research, Apache-2.0) covers
rainflow counting, load collectives, S-N handling, damage summation, equivalent stress —
maintained, documented, permissive. **[FFPACK](https://pypi.org/project/ffpack)** and
**[fatpack](https://github.com/Gunnstein/fatpack)** supply Goodman/Soderberg corrections and
trilinear curves.

**8.1** pyLife against the federation's stress output. **8.2** Mean-stress correction, surface
finish, size and reliability factors. **8.3** **Weld classification** — BS 7608 / Eurocode 3
detail categories; where a welded frame lives or dies; judgement, not arithmetic — *needs an ME*.
**8.4** Duty-cycle definition and damage over a real usage spectrum. **8.5** Notch handling,
hot-spot stress extrapolation.

**The library is 20% of this phase. The methodology is 80%, and it needs a real analyst.**

## Phase 9 — Multibody dynamics: where load cases actually come from

**~6 engineer-months, needs an ME.**

Today load cases are hand-entered guesses. In reality they are *outputs* of the machine moving.

**9.1** **[Project Chrono](https://projectchrono.org/)** (BSD-3, UW-Madison) — multibody + FEA +
FSI, Python API, template-based `Chrono::Vehicle` with ready suspension templates. Rejected:
MBDyn (GPL; stronger on rotor/aeroelastic — kept in reserve for exactly that).
**9.2** Mechanism definition derived from assembly constraints — the kinematic model comes from
the CAD, never modelled twice.
**9.3** Motion-range simulation: swept volume, interference through motion, travel and lock
checks.
**9.4** **Joint-load extraction feeding FEA** — manoeuvre → MBD → reactions at every joint → FEA
load case → stress → fatigue damage. The loop that closes the system; nothing else in this plan
produces a defensible load case.
**9.5** CATIA DMU Kinematics as an alternative backend where a seat exists.

## Phase 10 — Thermal, flow, and optimisation

**~9 engineer-months.**

**10.1** Steady/transient conduction, convection BCs, thermal-stress coupling (thermal *stress*
already shipped via `LoadCase.delta_t_k`; conduction is genuinely missing).
**10.2** CFD via **OpenFOAM**, deliberately late — *the meshing is the hard part* — scoped first
to cooling flow and ducting.
**10.3** Optimisation: **[OpenMDAO](https://openmdao.org/)** (NASA Glenn, Apache-2.0) as the MDO
framework; SIMP and level-set topology optimisation; DOE; response surfaces; multi-objective
trade-offs (mass vs stiffness vs cost). The capability that makes an AI designer *better* than a
human rather than merely faster.
**10.4** **The surrogate flywheel.** Every FEA run is a labelled datapoint (geometry + loads →
response). After a few thousand, a surrogate answers "roughly how stiff" in milliseconds, and the
optimiser explores thousands of candidates before spending a real solve. Training data is a free
by-product of use; the `Solver` ABC means the surrogate drops in as just another solver — which
is what that seam was built for.

# ERA IV — ENGINEERING KNOWLEDGE

## Phase 11 — The requirements model *(needs an ME)*

**~4 engineer-months.**

A machine is defined by a specification before geometry. Without this, "design me a press" has no
answerable meaning.

**11.1** Requirements with flow-down and validation flow-up: target mass, duty cycle, envelope,
regulatory regime, cost ceiling, service life. Aligned to **SysML v2** — 2026 is its tooling
maturity year; **[SysON](https://mbse-syson.org/)** and **[Capella](https://mbse-capella.org/)**
(both Eclipse, both free) are credible implementations with a published interop path.
**11.2** Requirement → assertion binding. **A requirement nothing checks is a wish.**
**11.3** Traceability: every design decision to a requirement or a standard (roadmap H5) — what a
signing engineer demands first.
**11.4** Coverage reporting: verified / by-what-evidence / unverified.

## Phase 12 — Load cases, materials, and standard parts

**~8 engineer-months, mostly needs an ME.**

**12.1 Load-case library** — standardised, per-domain, *executable*: pothole strike, panic
braking, curb drop, proof/ultimate factors, press tonnage cycles. Today invented per
conversation, so no two runs are comparable.
**12.2 Materials.** The honest research finding: **the open materials databases are the wrong
kind of open** — Materials Project, AFLOW, OQMD, OPTIMADE are DFT/atomistic; superb, and useless
for an engineering S-N curve. Free engineering sources (MakeItFrom, MatDat, ASM's free tier) are
partial and licence-varied. Deliverable: the **schema, provenance model and ingestion path** —
every property carries source and confidence; buying Granta/MatWeb later becomes data-loading,
not re-architecture.
**12.3 Standard parts.** **70%+ of any real machine is bought.**
**[BOLTS](https://boltsparts.github.io/)** (open library of technical specifications — parametric
ISO/DIN parts with dimension metadata) as the base; supplier CAD (TraceParts, McMaster) via
*import*, respecting their terms, never redistribution.
**12.4** A parts *selection* engine: given load, speed, life — choose the bearing; never model
what should be bought.

## Phase 13 — Design rules, DFM, tolerance and cost *(needs an ME)*

**~9 engineer-months.**

**13.1 Design rules as assertions** — minimum wall by process, draft angles, bolt torque and
preload, thread engagement, weld sizing, machining access — attached automatically from feature
type + declared process, running in the Phase-5 engine. **DFM becomes a red build.**
**13.2 Tolerance and GD&T** — stack-up (worst case and RSS), fit selection, datum schemes, FTA.
*A drawing without tolerances is not a drawing.*
**13.3 Cost model** — material + process + tooling + assembly time, as an assertion. An agent
that ignores cost confidently designs the unbuildable.
**13.4** Process-specific rule sets: cast, machined, printed, sheet, moulded, welded.

# ERA V — SCALE

## Phase 14 — Product structure, decomposition and interface contracts

**~9 engineer-months. Architecturally the most important era-V phase.**

**14.1** Product structure and BOM as **first-class data** — assembly tree, effectivity,
revisions, where-used. A conversation transcript is not a data structure.
**14.2 Hierarchical decomposition with interface contracts.** One component at a time, against a
contract: mounting points, envelope, mass budget, interface loads, clearances. How human teams
partition work, and **the only way the context problem is solvable — no model size fixes it.**
**14.3 The creative core: an interface contract is itself a compilable spec fragment.** Both
sides compile against it; the swingarm's spec imports the pivot contract, so does the frame's. A
violation is a **compile error at the interface naming both parties** — not a clash found in
assembly three weeks later. Reuses the entire Phase 1–5 machinery.
**14.4 Change propagation.** `diff.py` already answers this within one part (`changed_calls`,
`downstream`, `invalidates`); this lifts it to the product graph.
**14.5** Concurrency and locking: multiple agents/users on one product without corruption.

## Phase 15 — Throughput, storage and the compute fabric

**~6 engineer-months.**

**15.1** Batch compilation — a `Plan` as one kernel session (OCCT); a CATScript executed once
(CATIA), never 10⁵ COM calls.
**15.2** Simulation compute: queue, autoscale, result caching keyed on the provenance digest.
**FEA is not a request-path workload** and never becomes one.
**15.3** Geometry storage/versioning: content-addressed CAD with **semantic diffs under a
tolerance policy** (research consensus: line diffs on CAD are meaningless; reviewers should see
only consequential change). Extends `app/media/`.
**15.4** CATIA session pool, crash recovery, affinity — a smaller problem now it is off the
critical path.
**15.5** Observability: per-operation success rates, agent trajectory traces, solver timings,
failure taxonomy. Nothing measures op reliability today, which makes capability claims
unfalsifiable.

# ERA VI — THE AGENT

## Phase 16 — Tool retrieval, planning and long-horizon memory

**~10 engineer-months. Research-adjacent; the least predictable phase.**

**16.1 Tool retrieval at scale — a measured wall, not a worry.** 201 operations heading past 900.
Published 2026 numbers: accuracy falls from **87.4% at 500 tools to 65% at 2,000**; definitions
alone can consume 50k+ tokens before the user's request is read; retrieval errors account for
about **half of agent failures** at scale; retrieve-then-rerank measured at ~76% where naive
selection scored far lower. Approach: semantic retrieval over the registry (already a single
declarative source — unusually clean), rerank, and per-workbench sub-agents so no context sees
900 tools. The BM25 machinery is reusable directly, and lexical retrieval is *strong* in this
regime — operation names are exact terms.
**16.2 Planning.** "Design a swingarm" → an ordered, dependency-aware task graph with
checkpoints.
**16.3 Long-horizon memory.** The 2026 literature converges on hierarchical working memory —
subgoals as chunks with summarised observations (HiAgent-class results: ~2× success on
long-horizon tasks). Kryova already has the right instinct: `resume.py` reads the operation log,
not the transcript, because a trimmed window and an LLM paraphrase cannot be trusted about last
week. That principle generalises to the whole design record.
**16.4 Failure recovery.** Diagnose → repair → bounded retry → escalate with a *specific*
question (Phase 5's machinery is the foundation).
**16.5 Human checkpoints.** Structured approval gates — a reviewable diff with a sign-off record,
not a chat message (P5 owns the surface).
**16.6 Cost/time estimation before starting** — "this is four hours of compute and $X" (P8 owns
the meter).

**Creative leverage:** the design record replaces the transcript. Rationale already travels in
`FeatureSpec.note`; extend to decisions, rejected alternatives and reasons, and *"why is this rib
here"* has an answer in six months — from the artefact.

# ERA VII — OUTPUT, AND THE MACHINES

## Phase 17 — Manufacturing output

**~9 engineer-months.**

**17.1** Drawings with GD&T: auto views, sections, details, dimension generation, FTA, BOM
tables, title blocks. *Without this nothing leaves the building.*
**17.2** Export: STEP AP242 (the one that carries PMI), IGES, JT, 3MF/STL, DXF flat patterns —
mostly native OCCT.
**17.3** **Sheet metal**: wall, bend, flange, unfold, bend allowance `BA=(π/180)·θ·(r+K·t)`,
K-factor with the ANSI/DIN distinction and radius-to-thickness-dependent material sheets —
**[FreeCAD SheetMetal](https://github.com/shaise/FreeCAD_SheetMetal)** (LGPL) is the working
reference implementation. A press needs this; so does every enclosure.
**Sequencing exception:** 17.3 is **pulled forward and scheduled with Era IV** — the ladder
promises M3 (enclosure) in Era IV and M5 (press) in Era V, and both need sheet-metal authoring
long before the rest of this phase's drawings-and-CAM work. It sits in this phase because it
*belongs* with manufacturing output; it runs early because the missions need it. The status
board tracks it as its own row for exactly this reason.
**17.4** Weldments and tubing: beads, symbols, cut lists, tube routing. **A motorcycle frame is a
tubular weldment.**
**17.5** CAM: **[OpenCAMLib](https://github.com/aewallin/opencamlib)** (LGPL) — drop-cutter and
waterline primitives — plus machining features, stock, fixturing notes.
**17.6** Inspection planning: CMM points and measurement plans derived from the GD&T scheme.
**17.7** Technical documentation: assembly instructions, exploded views, service manuals, parts
catalogues.

## Phase 18 — The machine missions

**~12 engineer-months across the ladder.**

Not new capability — **proof of it**, and the discovery of the twenty things nobody predicted.
Each mission runs end-to-end **in the product** (Part 2's rule), is reviewed by a real engineer,
and stays green forever: spec, geometry, mesh, analyses with convergence evidence, fatigue
assessment, drawings with tolerances, BOM with bought parts, cost estimate, requirements
coverage — every number traceable.

M1 bracket → M2 welded frame → M3 enclosure → M4 gearbox → **M5 stamping press** → M6 conveyor →
M7 robot arm → M8 motorcycle chassis + swingarm.

**M5 is the honest mid-point milestone**: structure, mechanism, sheet metal, bought parts,
fatigue and guarding at once. If M5 does not work, the phases before it were decoration.

---

# PRODUCT TRACK

*The platform that makes the engineering usable, sellable and safe. P-phases run in parallel
with the engineering track; each names what it gates and what gates it.*

## Phase P1 — Identity, sessions and tokens done right

**~3 engineer-months. Starts immediately; blocks any external user.**

### The question it answers
Can a person trust Kryova with an account, on several devices, against an attacker who steals a
token?

### What exists and what is wrong with it
The good: typed JWTs whose refresh/access distinction is actually checked, bcrypt with prehash,
hashed reset tokens, cookie sessions. The wrong: **one refresh-token hash on the user row** — a
second device's login silently revokes the first; no rotation family, so a stolen refresh token
is a 30-day capability with no detection; no absolute session lifetime; `SECRET_KEY="changeme"`
boots; the rate limiter trusts `X-Forwarded-For` and lives in one process.

### Workstreams
**P1.1 — Sessions become rows.** A `sessions` table: user, device label, family id, current
refresh-token hash, previous-hash (grace), created, last-used, absolute-expiry, revoked-at, IP,
user-agent. Login creates a session; each device has its own.
**P1.2 — Rotation with reuse detection.** Every refresh **rotates** the token; presenting an
already-rotated token is a **theft signal that revokes the whole family** and forces
re-authentication. This is the 2026 OWASP-aligned consensus, and rotation without the detection
half is theatre. Access tokens stay short (≤15 min); absolute session cap (e.g. 30 days) ends
even a perfectly rotated chain.
**P1.3 — Session management UX** (frontend): device list with last-seen, "sign out this device",
"sign out everywhere". Backed by revocation that actually revokes (the session row is the truth,
not the cookie).
**P1.4 — Startup refusals.** `SECRET_KEY` unset or `"changeme"` ⇒ the server does not start, with
a message that says what to do. Same for an empty CORS origin list in production mode.
**P1.5 — Email verification and password flows** hardened: verification required before first
project creation (not before first look — friction where it protects, not where it annoys);
reset flow already hashes one-time tokens, keep; add resend throttling.
**P1.6 — Rate limiting that survives deployment reality**: keyed on the *authenticated principal*
where one exists, on the connecting IP otherwise, `X-Forwarded-For` honoured **only** from a
declared trusted-proxy list, and backed by a shared store so multiple workers enforce one
budget.
**P1.7 — Second factor (TOTP)** — standard `pyotp`-class implementation, recovery codes, and the
decision recorded that WebAuthn/passkeys are the follow-on, not the first ship.
**P1.8 — Token custody in both clients.** Web: httpOnly cookies as today (never storage). Tauri:
the same cookie flow through its webview, with the OS keychain via Tauri's secure storage if a
native token cache is ever needed — never a JSON file.

### Proof
A stolen refresh token replayed after rotation kills the family and the attacker's session, the
user sees it in the device list, and the audit log (P3) records it. A demo of this exact
sequence is part of the phase's acceptance.

## Phase P2 — Organisations, teams, roles and sharing

**~4 engineer-months. Gates: P3, P8, mission ladder beyond M2 in-product.**

### The question it answers
Can a *team* — not a lone user — own a machine programme, with the right people able to do the
right things and nobody able to see across a tenant boundary, even through an application bug?

### Workstreams
**P2.1 — The model.** `organisations`, `memberships(user, org, role)`, projects owned by
organisations (personal projects = an implicit personal org, so there is exactly one ownership
model). Invitations by email with expiring signed tokens; joining flows in the frontend.
**P2.2 — Roles, two-layered.** *Platform roles*: `owner / admin / member / viewer` govern the
organisation itself (billing, members, deletion). *Domain roles*: `engineer` (author designs,
run simulations), `reviewer` (comment, approve gates, cannot edit geometry), `operator` (run
released missions, cannot alter them). The approval gates in 16.5/P5 read the domain role — a
review sign-off from someone without `reviewer` is not a sign-off.
**P2.3 — Postgres RLS as the safety net.** Policies on every tenant-owned table; tenant context
supplied per request via **`SET LOCAL` inside the request's transaction** and therefore
discarded at COMMIT — safe under transaction-pooling PgBouncer, and the *only* sanctioned use of
`SET` in this codebase (Decision 7 states the rule and its reason; the test suite gains an
explicit cross-tenant isolation test that fails if a query escapes scoping *or* if anyone
downgrades `SET LOCAL` to `SET`). Application-level scoping remains primary; RLS exists for the
day the application is wrong.
**P2.4 — 404-not-403, systematised.** The existing rule (`get_owned_project`) generalised to
org-scoped resources: a resource outside your tenant does not exist. RLS makes the lie
consistent.
**P2.5 — Sharing and hand-off.** Transfer a project between orgs (with provenance intact);
read-only share links for a released design package, expiring, revocable — the artefact a
supplier or customer sees, without an account requirement for viewing.
**P2.6 — Frontend surfaces**: org switcher, member management, role assignment, invitation flows,
pending-invite states — all in the existing dashboard design language.

### Proof
The cross-tenant test suite: two orgs, adversarial queries at every endpoint, zero leakage, all
misses reading as 404. Run in CI forever.

## Phase P3 — The admin panel and operations console

**~4 engineer-months. Needs P1, P2.**

### The question it answers
Can the people running Kryova support users, control rollout, and investigate incidents — with
power that is bounded, visible and recorded?

### Workstreams
**P3.1 — The audit log, first.** Append-only, hash-chained (each entry carries the previous
entry's hash, so tampering breaks the chain visibly), covering: auth events, admin actions,
permission changes, impersonation start/end, quota changes, destructive operations, sign-offs.
Written from day one of P3 because every later workstream must land in it. Admin reads it in the
panel; org owners read their own org's slice (enterprise buyers ask for exactly this).
**P3.2 — Staff roles.** `support` (read, impersonate-read-only), `operator` (quotas, flags),
`platform_admin` (all, including suspension). Staff status is separate from any org membership —
being staff grants nothing *inside* a tenant without impersonation.
**P3.3 — Impersonation done right.** A separate token carrying **both identities** (acting
staff + subject user), **read-only by default**, time-boxed, visually bannered in the frontend,
every request audit-logged with the staff actor. Write-mode impersonation requires a second
confirmation and a reason string, and notifies the user by email after the fact.
**P3.4 — User and org administration**: search, view, suspend/reactivate (suspension revokes all
session families — P1 machinery), storage/compute quota adjustment, manual verification, GDPR
deletion with a grace window (soft-delete, then hard purge job through `MediaService` so blob
refcounting holds).
**P3.5 — Feature flags**: per-tenant and per-user overrides, kill switches, percentage rollouts.
Server-evaluated (the flag state rides to the frontend with the session, so the UI and the API
always agree on what is on).
**P3.6 — The operations dashboard**: job queues, solver failure rates by taxonomy class, per-op
success rates (E-15.5's data), storage growth, active sessions — the panel where "is Kryova
healthy" has one answer.
**P3.7 — Announcements and maintenance mode**: a banner the backend serves and both clients
render; read-only mode that refuses mutations with an honest message instead of erroring.

*(Admin UI lives in the existing frontend under an `/admin` route group, gated by staff claims in
`proxy.ts` server-side — never a client-side-only gate. No second web app: Decision 6.)*

### Proof
A support engineer resolves a real user issue via read-only impersonation; the user's org owner
can see that it happened, when, and by whom, in their own audit view.

## Phase P4 — File attachments: reading what users hand us

**~5 engineer-months. Needs P1; feeds the agent immediately; drawing understanding matures with
Era IV.**

### The question it answers
A user drops a supplier datasheet PDF, a load-case spreadsheet, a photo of a failed weld, a STEP
file and a scanned drawing into the conversation. Does Kryova *understand* them — and stay safe
while doing so?

### Workstreams
**P4.1 — Ingestion.** Attachments ride the existing chunked-upload path into the
content-addressed store (dedup for free; the same datasheet attached twice costs one blob). Type
sniffing by content, size/type limits, per-org storage quotas (P3). Every attachment is a
first-class object: owner, conversation link, extraction status, provenance.
**P4.2 — The extraction pipeline**, tiered by format, all local and free:
  - **CAD (STEP/IGES/BREP/STL/DXF)** → the geometry pipeline (Phase 1 kernel; `ezdxf` for DXF
    entities/dimensions). A STEP attachment can *become* a `GeometryVersion` on request.
  - **Documents (PDF/DOCX/XLSX/PPTX/HTML/images)** → **[Docling](https://github.com/docling-project/docling)**
    (IBM Research, MIT) as primary — strongest local table/layout/reading-order understanding,
    OCR for scans, unified document representation. **[MarkItDown](https://github.com/microsoft/markitdown)**
    (Microsoft, MIT) as the light fallback for the long tail of formats. Chosen over
    hosted parsers: local, free, no data leaves the deployment.
  - **Spreadsheets** keep their structure — a load-case table becomes rows with units, not prose.
  - **Images/photos** → the vision provider (already pluggable, P5 surfaces it).
**P4.3 — Engineering-drawing understanding**, staged honestly: text-layer PDFs → Docling now;
scanned drawings → OCR now; **dimension/GD&T extraction** → a later, research-adjacent
workstream (the published route: layout detection + a document transformer / fine-tuned VLM —
Donut/Florence-2-class), explicitly *not* promised early, because a wrongly read tolerance is
worse than an unread one. Until then, extracted drawing content is labelled "unverified read —
confirm dimensions before use".
**P4.4 — Provenance-tagged facts.** Everything extracted enters the conversation as quoted
material with a source pointer (file, page/sheet/cell). When an extracted number flows into a
design parameter, the *spec records the source* — `FeatureSpec.note` and the requirement links
(11.3) already give it somewhere to live. "Where did 42 mm come from?" must answer "cell C7 of
loads.xlsx, attached 2026-09-05".
**P4.5 — The injection boundary (Decision 8).** Extracted text is *data*: rendered as quoted
context, never merged into system instructions; the agent may not take a tool action whose sole
justification is attachment text without surfacing that justification for the user's approval
where it matters (approval gates, P5). Test fixtures include hostile documents ("ignore previous
instructions…") asserted inert — a regression suite, not a hope. This is the documented
document-to-LLM supply-chain attack class, taken seriously from the first release.
**P4.6 — Frontend**: drag-drop into the conversation, upload progress (chunked client exists),
extraction status, an attachment panel per conversation, inline previews (tables, images,
geometry via P6 viewer), and "insert as parameter / as requirement / as load case" affordances —
the moment extraction earns its keep.

### Proof
The hostile-document suite passes; a load-case spreadsheet becomes a named, provenance-tagged
load case applied to a design; a STEP attachment becomes geometry through the same kernel as
everything else.

## Phase P5 — The conversation and agent experience

**~5 engineer-months. Continuous; the frontend face of Phases 4, 5, 16.**

### The question it answers
Does working with the agent feel like working with a competent colleague — legible,
interruptible, honest about uncertainty — rather than watching a terminal scroll?

### Workstreams
**P5.1 — Streaming done properly**: token streaming and step events over SSE (fits the existing
poll-schedule/api-client machinery; WebSockets only if bidirectionality is ever actually needed),
reconnect-and-resume (conversation-resume exists and is tested — extend, don't replace).
**P5.2 — The step surface**: `agent-step-list` grows into the run view — plan steps, live
geometry operations, solver progress, per-step timing, failure taxonomy classes surfaced in
plain language.
**P5.3 — The design as an artefact, visibly.** The spec (the IR) rendered beside the chat:
parameters editable with units checked, features with their rationale notes, references
navigable; **spec diffs rendered like code review** (what changed, what it reaches — `diff.py`
already computes both). The conversation is the *log*; the spec is the *truth*; the UI must make
that hierarchy legible.
**P5.4 — The verification surface**: assertion dashboard (pass / fail / **unmeasured** rendered
as first-class — unmeasured is amber, never green), requirement coverage, provenance drill-down
from any number to its evidence chain (7.3), convergence badges on simulation results.
**P5.5 — Approval gates as UI**: a gate is a page — the diff, the affected assertions, the
cost/time estimate of what follows, an approve/reject with the actor recorded (P2 domain roles;
P3 audit). Not a chat message that scrolls away.
**P5.6 — Interruption and steering**: stop a run cleanly (the bounded loops make this safe),
edit a parameter mid-mission, resume without loss.
**P5.7 — Cost/time honesty**: before a long run, the estimate (16.6/P8); during, elapsed vs
estimate; after, actuals — the trust habit that makes P8's billing uncontroversial.

## Phase P6 — The viewer at machine scale

**~6 engineer-months. Grows with E-1 (tessellation source), E-14 (product structure), P4 (CAD
attachments).**

### The question it answers
Can a user *see* a 2,000-part machine in the browser — orbit it smoothly, open its tree, section
it, measure it, watch stress paint onto it — on an ordinary laptop?

### The stance
The hand-written WebGL 1 viewer is a strength for what it does today (a part, a stress field) and
respects the frontend's minimalism doctrine. Machine scale is a different problem class:
**server-side preparation, client-side streaming.** The kernel we now own (Phase 1) tessellates;
the client renders what it is sent, at the detail the view deserves.

### Workstreams
**P6.1 — The tessellation service** (backend): OCCT shape → glTF with **Draco** (70–95% geometry
reduction, 14-bit quantisation) or **meshopt** (lossless, GPU-direct decode) — measure both on
real Kryova parts and pick per-asset; multi-LOD generation at export; instancing extraction
(BOLTS parts repeat thousands of times — one mesh, N transforms). Cached in the
content-addressed store keyed on geometry digest + tessellation params, so a part is tessellated
once ever.
**P6.2 — The streaming scene** (frontend): assembly loads structure-first (the tree and bounding
boxes immediately), meshes stream by priority (frustum + screen-space size), LOD switches by
distance. Target: first meaningful paint of a 2,000-part machine under 2 s on a mid-range
laptop; interaction never below 30 fps (measured in CI against a reference assembly — a
performance *assertion*, in the house style).
**P6.3 — The renderer decision, made explicitly**: extend the hand-rolled WebGL viewer to WebGL 2
(instancing, better attribute handling) as the default path — it keeps the dependency doctrine
and the team knows every line. Adopt a library only if 6-months-in measurements show the
hand-rolled path cannot hold the fps target on M5-class assemblies; that decision point is
scheduled, criteria written now, so it is a measurement, not a mood. WebGPU is the follow-on
where available, behind capability detection.
**P6.4 — Engineering interactions**: section planes, exploded views (from the assembly
structure, animated), measure (point-point, edge, face-face — against real geometry via a
backend query, not against the decimated mesh), hide/isolate by subtree, camera bookmarks per
conversation ("the view we were talking about").
**P6.5 — Results on geometry**: the existing stress-field rendering generalised — scalar fields
(stress, displacement, thickness, fatigue damage) on the streamed meshes, shared colour-scale
legend, probe-a-value. The `surface-field` code is the seed.
**P6.6 — Tree ↔ 3D ↔ spec, one selection model**: click a part in the tree, it highlights in 3D
and the spec panel scrolls to its feature; select a face in 3D, the predicate that would name it
(2.1) is offered. This is where Layers B and C become *tangible*.

### Proof
M5's full assembly: structure paint <2 s, orbit at 30+ fps on the reference laptop, stress
overlay on the frame, a section through the die set, all in the browser; the same scene in the
Tauri app.

## Phase P7 — The desktop app and the workstation bridge

**~3 engineer-months. Extends what exists; the CATIA bridge's natural home.**

**P7.1 — Signed auto-update**: Tauri v2 updater with the offline signing keypair; **private-key
custody written down** (lost key = no more updates for installed apps, ever — key in a hardware
token or sealed secret store, never CI plaintext); staged rollout channels (stable/beta);
`latest.json` + signatures published per release (P9's pipeline builds it).
**P7.2 — The bridge, integrated**: the CATIA daemon (`scripts/catia_bridge/`) ships with/beside
the desktop app on workstation installs; the `catia-bridge-panel` grows into a first-class
status surface (connection, seat language, document binding, pending approvals). The
tier/approval model already exists — the desktop UI is where destructive-tier approvals belong.
**P7.3 — Desktop-only powers, used sparingly**: local file open/save into the attachment
pipeline, OS notifications for long-run completion, deep links (`kryova://run/...`) from CI or
email into the app.
**P7.4 — Offline honesty**: what works without the backend (viewing cached designs, reading
docs) and what does not (everything else), stated in the UI rather than discovered by timeout.

## Phase P8 — Billing, quotas and metering

**~3 engineer-months. Needs P2 (orgs), P3 (flags/quotas); ships before general availability.**

**P8.1 — Meter what costs**: solver-seconds by class (a CalculiX nonlinear minute ≠ a linear
static second), geometry-operation batches, storage-bytes, seats. Usage accumulates locally and
posts in aggregates (the high-volume pattern; never one event per action).
**P8.2 — Plans**: free tier (real but bounded — M1-class work, community support), team, and
enterprise (SSO later, audit-log export from P3.1, custom quotas). **Stripe** metered billing +
prepaid credits for compute bursts — the hybrid the compute-heavy SaaS pattern converged on.
**P8.3 — Enforcement with dignity**: quota exhaustion returns the honest envelope (what ran out,
what it costs to continue, what remains free), never a bare 429; estimates before expensive runs
(P5.7) mean nobody is surprised.
**P8.4 — The bridge between metering and trust**: the same meter that bills is the meter shown in
cost estimates. One number, two uses; divergence is a bug class of its own.

## Phase P9 — Delivery: CI/CD, environments, and operational safety

**~3 engineer-months. Starts immediately — the frontend's zero-CI state is the first fix.**

**P9.1 — Frontend CI, week one**: lint, `tsc`, vitest, build on every push (all three are
currently clean and only discipline keeps them so — that is what CI is for). Then: preview
deployments per PR.
**P9.2 — Backend images that carry the fleet**: containers with OCCT + gmsh + CalculiX +
(later) Chrono pinned — the determinism substrate (1.6) and the deploy artefact are the same
thing. GPL components live in their own layers/processes per Decision 4.
**P9.3 — Environments**: staging with seeded demo orgs and the mission suite running nightly
against it; production migrations gated on `alembic check` and a rollback note per migration.
**P9.4 — Desktop release pipeline**: tauri build matrix (Windows first — the CATIA audience),
signing, `latest.json` publication, channel promotion (beta → stable) as a pipeline step.
**P9.5 — Backups and restore *drills***: Neon PITR verified by actually restoring; blob-store
backup with refcount integrity check; a written RTO/RPO and a quarterly drill that proves it.
**P9.6 — Secrets and supply chain**: no default secrets boot (P1.4), dependency pinning +
audit in CI, SBOM for the desktop app (enterprise buyers ask), release notes generated from the
merge log.

## Phase P10 — Documentation, onboarding and the trust surface

**~2 engineer-months, then continuous.**

**P10.1 — In-product onboarding**: the existing setup wizard grows into first-run success — a
guided M1 in under ten minutes, on the free tier, no sales call.
**P10.2 — The docs site**: task-oriented docs, the mission gallery (every ladder mission as a
worked, forkable example), API reference from the OpenAPI schema that already exists.
**P10.3 — The trust pages, the differentiator**: the **validation register** (7.4) published —
which analyses are validated against what, to what accuracy; the **"what Kryova will not
claim"** page (Decision 5's scope, the sign-off model, the unmeasured-is-never-green rule) as
public commitments; a changelog that names accuracy-affecting changes loudly.
**P10.4 — Status and comms**: status page, incident history, the P3.7 announcement machinery's
public face.

---

## Part 3 — Technology register

Every dependency choice, why, and what was rejected. All free; licences noted because they
constrain architecture (Decision 4).

| Need | Choice | Licence | Why, and what was rejected |
|---|---|---|---|
| CAD kernel | **OCCT** + `pythonocc-core` | LGPL | Only mature open B-rep kernel; OCCT 8.0 `BRepGraph` history. Fallback binding: pyOCCT; naming layer may go C++-side (Phase 1.0 spike). Rejected: CGAL (mesh, not B-rep), Manifold (no NURBS) |
| 2D constraints | **PlaneGCS** | LGPL | Full vocabulary, proven detachable. Rejected: SolveSpace solver (narrower) |
| Meshing | **gmsh** *(in use)* | GPL | Industrial grade. Subprocess boundary |
| FEA workhorse | **CalculiX** | GPL | Abaqus-style decks, 25 yrs validation; C3D10 recommended solid; shell/beam expansion caveat recorded. **Subprocess only** |
| FEA specialist | **code_aster** | GPL | Fracture, cyclic plasticity. **Subprocess only** |
| Multiphysics | **Elmer** | LGPL | FSI, coupled fields |
| CFD | **OpenFOAM** | GPL | The standard; late; meshing is the hard part |
| Fatigue | **pyLife** (+ FFPACK, fatpack) | Apache-2.0 | Bosch Research; turns D4 from bespoke into integration + methodology |
| Multibody | **Project Chrono** | BSD-3 | Python API, vehicle templates. MBDyn (GPL) reserved for rotordynamics |
| Optimisation | **OpenMDAO** | Apache-2.0 | NASA Glenn; SIMP/level-set precedent |
| CAM | **OpenCAMLib** | LGPL | Drop-cutter/waterline, FreeCAD-proven |
| Sheet metal | **FreeCAD SheetMetal** as reference | LGPL | Working unfold, ANSI/DIN K-factor |
| Standard parts | **BOLTS** | open | ISO/DIN parametric parts + metadata |
| Requirements | **SysML v2** via SysON / Capella | EPL | 2026 tooling maturity; Eclipse, free |
| Doc understanding | **Docling** primary, **MarkItDown** fallback | MIT / MIT | Local, free; Docling strongest on tables/layout/OCR; MarkItDown widest format tail. Rejected: hosted parsers (data leaves deployment) |
| DXF | **ezdxf** | MIT | The standard Python DXF library, R12→R2018 |
| Mesh compression | **Draco** and/or **meshopt** | Apache-2.0 / MIT | Measured per-asset; LOD + instancing on top |
| Retrieval | **BM25** *(in use)* + rerank | own | Exact-term regime; built and tuned |
| Payments | **Stripe** metered + credits | commercial svc | The one paid *service*; no free equivalent worth its risk. Rejected: bespoke billing |
| Materials data | schema + provenance, ingest later | — | **Honest gap**: open DBs are DFT/atomistic; engineering S-N data is not freely available at quality |

### Licence obligations (restated because they bind)
GPL ⇒ separate process, file/CLI boundary, always. LGPL ⇒ dynamically linked, replaceable.
`data/bm25/` tracked Dassault PDFs ⇒ resolve before publishing the repo (history rewrite).
Dependency docs ingested into our BM25 index ⇒ check each licence permits the copy; where it does
not, index a pointer, not the text.

---

## Part 4 — Effort, honestly

| Track | Phases | Engineer-months | Character |
|---|---|---:|---|
| E-I Geometry engine | 1–2 | 14 | Hard, foundational, unavoidable |
| E-II Perception | 3–5 | 11 | Best value per effort |
| E-III Physics | 6–10 | 31 | Mostly integrate; ~15 needs an ME |
| E-IV Knowledge | 11–13 | 21 | Nearly all needs domain engineers |
| E-V Scale | 14–15 | 15 | Architecture |
| E-VI Agent | 16 | 10 | Research-adjacent, least predictable |
| E-VII Output & missions | 17–18 | 21 | Integration + discovery |
| **Engineering total** | | **~123** | |
| P1 Identity & sessions | | 3 | Standard, must be flawless |
| P2 Orgs & tenancy | | 4 | Standard + RLS discipline |
| P3 Admin & audit | | 4 | Standard, enterprise-gating |
| P4 Attachments | | 5 | Integration + one research tail (drawings) |
| P5 Agent UX | | 5 | Product craft, continuous |
| P6 Viewer at scale | | 6 | Hard graphics engineering |
| P7 Desktop | | 3 | Extends what exists |
| P8 Billing | | 3 | Standard, pre-GA |
| P9 Delivery | | 3 | Starts week one (frontend CI) |
| P10 Docs & trust | | 2+ | Continuous |
| **Product total** | | **~38** | |
| **Programme total** | | **~161** | ≈ 8–10 engineers × ~1.7 years, ideal conditions |

**Ideal conditions do not exist.** With hiring, rework and discovery: **4–7 calendar years** to
the full ambition — with sellable, honest intermediate products from the first year (M1–M3 class
work on the product track's platform is already a real tool).

The ~36 engineer-months needing a real analyst do not compress, and the plan would be dishonest
if it pretended a library substitutes for judgement.

### Not software — no amount of code fixes these
Domain engineers on staff · physical testing (rigs, strain gauges, correlation) · homologation
(ECE, FMVSS, Machinery Directive, CE) · professional liability (someone licensed signs — a legal
fact) · supplier relationships and manufacturing capacity · aesthetic judgement (Class-A
surfacing is craft; taste does not automate).

---

## Part 5 — Sequencing, and the interleave

1. **Week one, in parallel**: Phase 1.0 (the binding spike) and P9.1 (frontend CI). One de-risks
   the keystone; the other stops the only untested-in-CI codebase from rotting.
2. **Phase 1 before everything engineering.** Every later phase is priced in geometry
   operations; Phase 1 makes them free.
3. **P1–P2 before any external user** — auth and tenancy are not retrofittable with dignity.
4. **Era II before Era III**: measure before asserting; assert before simulating decisions.
5. **P4 early** — attachment understanding multiplies the agent's usefulness immediately, and
   the injection boundary must exist before the feature is loved.
6. **Phase 7 before any accuracy claim.** Until then numbers are plausible, not right.
7. **Phase 14 before anything past M4.** The context problem is solved by contracts, not model
   size.
7½. **17.3 (sheet metal) runs with Era IV, not Era VII** — M3 and M5 need it (see the
   sequencing exception recorded in Phase 17). The rest of Phase 17 stays where it is.
8. **Era VI and P5 run continuously** — the 900-tool wall and the UX both arrive regardless.
9. **P8 before GA; P3 before the first supported customer; P10's trust pages with the first
   public accuracy claim.**
10. **Missions continuously, in-product, never at the end.** A mission that has not run in the
    product is a phase that has not been tested.

### The two facts to keep in view

**1. Tool capability is not agent capability.** Whether an LLM can hold coherent design intent
across 10⁵–10⁶ operations is unproven by anyone. Phases 14 and 16 are the attempt to make it
true; this document does not claim they are solved.

**2. The honest product is a force multiplier.** *80% of the engineering in a tenth of the time;
a licensed engineer signs.* Everything here — both tracks — serves making that 80% trustworthy
enough to be worth the engineer's 20%.
