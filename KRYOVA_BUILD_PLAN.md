# Kryova build plan

Working queue for the roadmap in *"everything needed to build real machines from the chat"*
(2026-09-03). **This file is a queue, not an archive**: a phase is deleted from *Queue* the
moment it is finished and tested, and one line about it is appended to *Done*. If a phase is
still below, it is still owed.

> **Two phase numberings exist and they are not the same. Do not conflate them.**
>
> - **This file's Phase 1, 2, 3…** are *work batches* — what was picked up in what order, sized to
>   be finishable. They are historical and local to this queue.
> - **[KRYOVA_MASTER_PLAN.md](KRYOVA_MASTER_PLAN.md)'s Phase 1–18 (engineering) and P1–P10
>   (product)** are the *programme* phases. That document is the controlling plan and states the
>   goal, the eight architectural decisions, and the technology choices. Product phases P1
>   (auth/sessions), P9.1 (frontend CI) and the Phase 1.0 binding spike are flagged there as
>   immediate starts.
>
> Mapping so far: this file's Phase 1 + Phase 2 together deliver the foundation of the master
> plan's **Phase 5** (assertions, diff, self-correction) and part of its **Phase 2** (semantic
> naming). This file's **Phase 3 = master plan E1**, the OCCT kernel keystone — **done
> 2026-09-05**, with the CATIA-seat half of its conformance run outstanding because it needs a
> Windows seat. Geometry is now free, headless and deterministic, which is the assumption
> everything the master plan prices was resting on.

**Rules of the queue**

- One phase at a time, finished and green before the next is started.
- "Finished" means: implemented, `pytest` green, `ruff check app/ tests/` clean, and `mypy app/`
  with no *new* errors against the baseline recorded below.
- Anything that needs a real CATIA seat, a licence, or an external binary is not in the queue —
  it is in *Blocked on the physical world* at the bottom, so it is never quietly counted as done.

**Baselines at the start of this work** (commit `03d238e`)

| Check | State |
|---|---|
| `pytest` (full, incl. Neon) | 2169 passed, 4 skipped, ~161 s |
| `ruff check app/ tests/` | clean |
| `mypy app/` | **7 pre-existing errors** — `solve/loads.py:205` (×4 union-attr), `solve/{linear_static,modal,buckling}.py` (`_dof_indices` arg-type). Not mine; do not be surprised by them, do not let the count grow. |

**Current** (2026-09-05, after Phase 7): `pytest` **3024 passed, 4 skipped, 220 s** —
the *full* suite, Neon half included · ruff clean on `app/ tests/ scripts/` · mypy still
exactly those 7 · `alembic check` reports no drift · the retrieval index is current
(4,906 passages, 25 documents) · `scripts/catia_bridge/generated_tools.py` matches its
generator, verified by importing both and diffing `TOOLS` rather than reading the patch
(200 tools, none lost, no schema changed — the 7,957-line diff is formatting only) ·
every operation handler names a real registry operation · every selector vocabulary word
is decidable · `app/design/` still imports without pulling in OCP.

The one gap between the registry's 201 operations and the bridge table's 200 is
`catia_status`, which is `server_only` by design — it answers with CATIA closed.

One trap this merge left, recorded because it will happen again: `scripts/catia_bridge/generated_tools.py`
came back from the PR #3 merge **stale**, and `tests/test_bridge_table_is_generated.py` was the
only thing that caught it. The tool set and every schema were identical — the merge had simply
committed a copy written by an older generator (single quotes, unwrapped lines), so the diff was
7,000 lines of pure formatting. Fix is `venv/bin/python scripts/gen_bridge_tools.py`; the thing
worth remembering is that the diff size says nothing about whether the daemon lost a tool, and
the two must be compared by importing both and diffing `TOOLS`, not by reading the patch.

---

## Corrections to the roadmap document

The roadmap is stale in five places. Recorded here so effort is not spent twice.

| Roadmap says | Actually true on this branch |
|---|---|
| D3 modal & buckling — *missing, 1 mo* | **Shipped.** `app/solve/modal.py`, `app/solve/buckling.py`, verified against Euler-Bernoulli and Euler critical load. |
| D7 thermal — *missing, 3 mo* | **Shipped** for thermal *stress*. `app/solve/thermal.py` via `LoadCase.delta_t_k`, verified against `σ = −EαΔT`. Transient conduction is genuinely missing. |
| C1 geometric interrogation — *missing, 1 mo* | **Mostly shipped.** `catia_measure`, `catia_measure_between`, `catia_measure_item`, `catia_analysis_part`, `catia_sketch_analysis`, `catia_surface_analysis`, `catia_assembly_analysis`, `catia_assembly_clash`. |
| A2 reference geometry — *missing, 1 mo* | **Shipped.** `catia_plane_offset`, `plane_angle`, `plane_through_points`, `plane_normal_to_curve`, `plane_tangent_to_surface`, `plane_mean`, `axis_system`, the point and line families. |
| `CATIA_V5_FULL_COVERAGE.md` is stale at 39 tools | Still stale. Regenerate it from `app.catia.ops.registry.summary()` or delete it. **Queued in Phase 10.** |
| B2 "every created entity gets a stable name at creation" | **Not reachable as written.** Only 65 of 201 operations take a `name`; every core Part Design feature takes none. Phase 1 delivers B2 with a following `catia_feature_rename` instead. Adding `name` to the ~39 backend methods is the better long-term fix and is blocked behind A1. |

The roadmap's own framing survives all five corrections: Layers **B** and **F2** are still at
zero, and they are still what decides whether this is a product.

---

## Done

- **2026-09-05 · Phase 7 — `feature#selector`: the entities *of* a feature.** Master plan
  **E2.2**, reserved since the design IR was written and refused with a message pointing
  at A3. `naming.descendants_of` / `contribution_of`, `Feature.contributed_faces/edges`,
  `Predicate.of`, `selectors.parse_sub_entity`.

  **The test that matters is `slab#top`.** On a pad with a boss standing on it, the word
  `top` gives the boss's top face — the highest thing on the part. `slab#top` gives the
  *annulus*, 1200 − π·36 mm², which no predicate over the whole part can name. Anything
  that quietly widened `of` to the part would pass every other test and fail that one.

  Three things this rests on:

  1. **`Modified()` returning nothing does not mean the face vanished.** It is OCCT's
     convention for *unchanged* — the face survives as the same `TShape` and the
     algorithm has nothing to report. `IsDeleted()` distinguishes them. Reading
     `Modified()` alone is the obvious implementation and loses every face an operation
     left alone: on a boss fused to a pad, that is the barrel and the top, i.e. the
     entire feature. Measured before it was designed around.
  2. **A contribution must be carried through every later feature.** Fusing the boss
     *replaces* the pad's top face with the annulus, so a feature holding its original
     handle would have `slab#top` resolve to nothing while looking perfectly healthy —
     the topological naming problem in miniature, with the same answer: follow the
     history. `set_result(evolved_by=maker)` re-maps every earlier feature's contribution
     on each operation.
  3. **`axis`/`side` are measured against the feature, not the part.** `boss#top` builds
     a compound of just that feature's faces and finds the extreme of *that*; measuring
     against the whole shape would make `slab#top` empty, since the slab's top is not the
     part's top. This is why the restriction cannot be one more filter in the chain.

  **An operation that does not record its contribution refuses, never widens.** The
  tempting fallback — ignore `of` and select across the part — gives the right answer on
  the example everyone tests with and the wrong one on any part where the feature is not
  the extreme.

  **Completed the same session: every geometry operation now records.** The rule the five
  remaining judgements follow — *a face the operation created is always its own; a face it
  altered is its own only when altering it was the point*:

  | operation | its own faces | why |
  |---|---|---|
  | boolean | descendants of the tool body | same as a pad's blank |
  | fillet / chamfer | the **generated** blend surfaces | the trimmed neighbours stay with whatever built them |
  | draft | the walls it was **asked** to tilt | see below |
  | shell (opened) | generated inner walls **+** the modified rim | producing the wall cross-section *is* the operation |
  | shell (closed) | descendants of the offset solid under the **cut** | the result comes from a different algorithm, so the join's faces are not in it at all |

  Checked against areas, not recorded output: a fillet's four blends are
  4 × ¼ × 2π·3 × 20 = 376.991 mm²; an open shell's inner surface is 3432 mm² (five walls
  plus a 264 mm² rim); a closed one is 3856 mm², the six faces of a 36×26×16 void.

  **Two OCCT quirks found by measuring rather than by reading:**

  - **`BRepOffsetAPI_DraftAngle.Modified()` under-reports** — 2 of a box's 6 faces after
    tapering four walls. It belongs to the `BRepBuilderAPI_ModifyShape` family, whose
    answer is `ModifiedShape()`, and that reports all six. Reading `Modified()` alone left
    every *earlier* feature holding faces the draft had already replaced. `descendants_of`
    now prefers `ModifiedShape` where it exists; only that family exposes it, so the
    boolean, fillet and offset paths are untouched.
  - **A taper propagates along tangent-continuous neighbours.** Drafting the four walls of
    a filleted block tilts *eight* faces — the walls and the blends between them. That is
    documented OCCT behaviour and matches CATIA, so the operation is right; but the blends
    are a side effect, and the draft claims only the four it was asked for.

  `pytest` over the ten offline files: **362 passed**. ruff clean; mypy at the 7-error
  baseline.

- **2026-09-05 · Phase 6 — constructed planes, and sketching on them.** Master plan
  **E2.4** (the plane half). `app/kernel/occt/reference.py` + `operations/reference_ops.py`,
  `catia_plane_offset` wired. Coverage 24 → 25 of 201.

  **What this actually unlocks: the second feature of a real part.** Until now every
  sketch sat on `XY`, `YZ` or `ZX`, so every profile in a design passed through the world
  origin. That is enough for a first pad and nothing after it — a boss on top of a pad
  needs a plane at the top of the pad, and there was no way to say so.
  `test_a_boss_can_be_built_on_top_of_a_pad` is the phase in one assertion, checked
  against 40×30×20 + π·6²·10 rather than a recorded number.

  A constructed plane is a **frame, not a face** — it carries a `gp_Ax3` and no geometry,
  adds nothing to the part, and is held on the document under the design's own name,
  exactly as in CATIA where a plane is a construction element rather than a body.

  Three things that are load-bearing and easy to get wrong:

  1. **The local X axis is inherited from the reference, and that is why offsets
     compose.** `gp_Ax3(point, normal)` invents an X from the normal if you let it, which
     silently rotates the sketch frame — so a rectangle placed `at (10, 5)` on a plane
     offset from `XY` would land somewhere other than directly above the same rectangle
     on `XY`, and the boss comes out square to nothing in particular.
  2. **An origin plane cannot be shadowed by a constructed one.** `XY` is vocabulary, not
     a name; `app/design/names.py` already refuses it as a semantic name for this reason,
     and the resolver checks the vocabulary *first* so the guarantee is local rather than
     trusted from another module. Tested by constructing a plane called `XY` 50 mm away
     and confirming a sketch on `XY` still lands on the real one.
  3. **A sketch's `origin` shifts along the plane's own axes, not the world's** — fixed
     here rather than noted. The argument means *where the sketch's own (0, 0) sits on
     the support*, and the two readings agree on `XY`, where the frame axes are the world
     axes, and disagree on `YZ`: local (10, 5) is world (0, 10, 5), while the old
     world reading put it at (10, 5, 0), which is off the plane entirely. Nothing pinned
     the old behaviour, and `point_on` already applied the local rule to every 2D point
     drawn on a sketch, so the sketch's own origin now obeys the rule its contents obey.

  Offsetting from, or sketching on, a **planar face** is refused naming `feature#selector`
  (Phase 2.2) rather than approximated by the nearest origin plane — which would build at
  a height nobody chose.

  **Completed the same session** with points and axis systems, closing 2.4 for everything
  that does not need a named face: `catia_point_at` (absolute, or measured from another
  point), `catia_point_between` (ratio unbounded on purpose — extrapolation past an end
  is a real thing to want), `catia_plane_through_points`, `catia_plane_angle`, and
  `catia_axis_system`. Coverage 25 → **30 of 201**.

  Three decisions in that half worth keeping:

  - **A hinge axis may be a world axis or an axis system's** (`"X"`, or `"frame.x"`).
    A world axis only ever tilts a plane through the world *origin*, so the second form
    is the one that matters — and it swings the plane's origin as well as its normal,
    which the test checks against hand arithmetic rather than a recorded triple.
  - **`catia_axis_system(set_current=True)` is refused, not ignored.** Making an axis
    system current silently changes the frame every *later* operation is read in, and the
    design IR has no way to record it — so the same plan would mean different things on
    the two backends, which is exactly the divergence the two-backend design exists to
    prevent.
  - **A reference point is coordinates, not a `TopoDS_Vertex`.** Holding it as a shape
    would invite it into booleans and into the face and edge counts, where it means
    nothing and changes the determinism digest.

  The three reference operations still unimplemented — `catia_point_centre`,
  `catia_point_on_curve`, `catia_point_on_surface` — every one needs a named face, curve
  or surface, so they are blocked on 2.2/2.6 rather than on effort.

  `pytest` over the ten offline files: **351 passed**. ruff clean; mypy at the 7-error
  baseline.

- **2026-09-05 · Phase 5b — `catia_draft`, the repair half of the draft loop.** Master
  plan **E2.5** (Part Design completion), taken because it closes the loop E3.2 opened:
  the analysis names the walls that will drag in the mould, and this tapers them.
  `BRepOffsetAPI_DraftAngle` in `occt/operations/dressup.py` — draft sits with fillet and
  chamfer for the reason CATIA puts it in the same toolbar, since it tilts faces that
  already exist rather than adding a feature. Coverage 23 → 24 of 201.

  **The two halves verify each other, which is the point.** The feature tapers with
  OCCT's `DraftAngle`; the analyser measures the result from surface normals it computes
  independently. Taper a wall 3° and the analyser must then read 3° on it — if either had
  the sign, the neutral plane or the angle convention wrong, they would disagree. Volume
  is checked against the wedge ½·h·(h·tan θ)·depth, not a recorded number.

  `neutral` is required rather than defaulted: the neutral plane is the section that
  keeps its size while everything else pivots about it, so choosing one silently changes
  every dimension downstream. A neutral plane taken from a *face* is refused pointing at
  Phase 2.2, and `reflect_line`/`variable` modes are refused naming what each needs.

  **The gap this exposed was closed in the same session rather than logged.** Writing the
  draft test showed the predicate vocabulary could not say *"the vertical walls"*:
  `normal` matches a direction within a tolerance, so it names one wall, and the
  selection a mould designer actually wants had no spelling. `parallel_to` and
  `perpendicular_to` now do, for faces and edges both:

  - **For a face they describe its plane, not its normal** — a face *parallel to* Z has a
    normal *perpendicular* to Z. That is the convention every CAD system uses and the
    inverse of the arithmetic, so the inversion is absorbed once in `_filter_by_orientation`
    rather than by every caller. `parallel_to: "z"` is the four walls of a block;
    `perpendicular_to: "z"` is its top and bottom.
  - **For an edge they are tested along its whole length.** A horizontal circle is
    perpendicular to Z at every point and matches; an arc that climbs matches neither.
    A single end-to-end comparison cannot express this and a closed edge cannot even
    provide one — the same closed-edge problem that broke `vertical`/`horizontal`.
  - **Unsigned, and a bare axis is accepted.** A wall parallel to Z is parallel to it
    whichever way it faces, so demanding `"+z"` would imply a distinction that does not
    exist and turn "the walls" back into four selections. `normal` still requires a sign,
    because there the sign is the whole question.

  **One bug caught by reasoning before it ever ran:** testing perpendicularity as *"not
  within tolerance of parallel"* accepts everything from the tolerance to 90°, so a 45°
  face passes as parallel and the predicate selects nearly every face on the part.
  Perpendicular needs its own bound, `|dot| ≤ sin(tol)`. A 45°-tilted box is the
  regression test.

  `pytest` over the nine offline files: **321 passed**. ruff clean; mypy at the 7-error
  baseline.

- **2026-09-05 · Phase 5 — geometric interrogation, the measurement contract, and
  provenance.** Master plan **E3.1–3.5**, OCCT side.

  `app/kernel/occt/interrogate/` (8 modules) answers what a part can be *made into*, as
  against `metrology.py` next door, which answers what it *is*: wall thickness by ray
  cast, draft against a pull direction, undercuts by visibility, curvature and the cutter
  radius it implies, dihedral continuity, B-rep validity, and clearance/interference
  between two bodies. `sampling.py` and `raycast.py` are the shared machinery — one seam
  where a future BVH goes in without any analysis above it changing. Reached through
  `catia_analysis_part`, which the registry already declared and nothing implemented
  (coverage 22 → 23 of 201). `metrology.oriented_bounding_box` closes 3.1: a 45°-rotated
  10×20×30 block still measures 30×20×10, where the axis-aligned box reads 21×21×30 —
  the difference between buying the right billet and the wrong one.

  **`app/kernel/contract.py` (3.4)** is the written vocabulary an assertion may read —
  path, unit, meaning, version. `undocumented_paths()` is asserted empty over every scan,
  so a backend that invents a spelling is caught by a test instead of in review.
  **`app/kernel/provenance.py` (3.5)** carries *how each number was got* — measured,
  approximated, or unavailable-with-a-reason — as a sidecar, so paths like
  `bounding_box_mm.size[2]` keep working. `assertions.py` now reads it per path: mass
  integrated exactly is no longer tainted by a ray-cast thickness beside it, and an
  unmeasured claim quotes the backend's own reason ("this shape encloses no solid, so it
  has no volume") instead of "nothing reports that".

  **Draft picks its own basis** rather than declaring one: exact on planar faces, sampled
  on curved ones, so a machined block gets an exact answer and a filleted part an honest
  approximation.

  **Six bugs, five of them silent, and four were mine from Phase 4** — found only because
  this was the first run of `pytest` since that phase, which shipped on ruff and mypy
  alone:

  1. **`document.measure()` cached the feature list with the geometry.** The cache is
     invalidated by `set_result`, i.e. by geometry — but a rename changes names without
     changing geometry, and the compiler emits a rename after almost every feature. So
     every payload after the first rename reported the *old* names beside correct
     numbers, with nothing to say which to believe. Only the geometric half is cached now.
  2. **A symmetric pad extruded the profile, then extruded the resulting solid.** OCCT
     refuses that with `Standard_NoSuchObject: Solids are not Processed`, thrown from the
     constructor before any `IsDone()` could be checked. It translates the profile and
     extrudes once now.
  3. **`catia_shell` with no faces named did not hollow anything.**
     `MakeThickSolidByJoin` with an empty list *shrinks* the solid — a 40×30×20 box at
     2 mm came back as a plain 36×26×16 block, six faces, 14,976 mm³ where the wall is
     9,024, and nothing in the result said so. (`MakeThickSolidBySimple`, the API that
     sounds right, does not complete at all on this input.) The offset solid is now cut
     from the original: twelve faces, 9,024 mm³, closed.
  4. **`OperationNotSupported` was lowercasing its own explanation.** `str.capitalize()`
     uppercases the first character and lowercases every other one, so
     `BRepFeat_MakePrism` reached the user as `brepfeat_makeprism` and `Phase 2.5` as
     `phase 2.5` — destroying exactly the proper nouns someone would search for. Fixed in
     the exception so no call site can reach for the wrong method again.
  5. **`vertical`/`horizontal` stopped working on closed edges** once Phase 4
     de-duplicated traversal: a circle has one vertex, so an endpoint comparison had
     nothing to compare, and a cylinder's rims were skipped. The obvious repair —
     bounding box — is also wrong: OCCT's `Bnd_Box` carries ±1e-7 even with `SetGap(0)`,
     so a perfectly flat circle measures 2e-7 tall and every rim reads as vertical. The
     edge's own curve is sampled instead: exact, and right for arcs too.
  6. **`up_to_next` was still refusing with "needs face selection (Phase 2.1)"** after
     2.1 shipped. The real blocker is `BRepFeat_MakePrism` with an until-shape. A stale
     "blocked on X" outlives X and sends the next reader to rebuild what already exists.

  The curvature sign convention is in the source as a measured table, not a guess:
  against the **outward** normal, negative is convex and positive is concave — the
  opposite of the intuitive reading, and a REVERSED face flips the raw value again.
  Getting either half wrong reported a solid cylinder's barrel as concave.

  `pytest` over the nine offline files: **310 passed**. ruff clean; mypy at the recorded
  7-error baseline. (The Neon half was deferred at the time and has since been run —
  see the verification note below.)

- **2026-09-05 · Phase 4 — predicate selection and per-entity parameters.** Master plan
  **E2.1 and E2.3**, which also closes the A3 groundwork Phase 2 still owed.

  `app/kernel/selection.py` declares a backend-neutral predicate vocabulary —
  *edges longer than 10 mm*, *faces normal to +Z*, *cylindrical faces of Ø6*, *entities
  at the top* — deliberately mirroring `app/solve/types.py`'s region selectors, because
  selecting a face to fillet and selecting one to load are the same question asked of
  different layers, and this repo's rule is that selection is geometric and never by
  face id. `occt/resolve.py` evaluates it, `occt/classify.py` measures what it asks
  about. **Every word in the registry's vocabulary is now decidable** —
  `unsupported_words()` returns empty, where `convex`, `concave`, `top` and `bottom`
  were all refused a day ago. Per-edge fillet radii (2.3) work, and `catia_shell` can
  open named faces, which was refused for want of face selection.

  **Three real bugs found, two of them silent:**

  1. **`TopExp_Explorer` visits a sub-shape once per owning parent.** A closed box
     explores as 24 edges and 48 vertices instead of 12 and 8. That had been doubling
     `edge_count` in every measurement since the kernel landed — and therefore poisoning
     the determinism digest that hashes it — as well as feeding each edge to a fillet
     twice. `topology.explore` now de-duplicates through `TopExp.MapShapes`;
     `explore_oriented` is the deliberate exception, kept because a face's boundary
     orientation is exactly what convexity needs.
  2. **"At the top" must mean lying *in* the top plane, not reaching it.** A box's side
     wall runs bottom to top, so it touches z-max; testing only the near end made
     `{"axis":"z","side":"max"}` select the top face *and all four sides*, and a shell
     asked to open the top opened five faces and left the bottom slab (2400 mm³ where
     7152 was right).
  3. **Convexity needs orientation, and two plausible tests do not work.**
     `cross(n₁,n₂)·tangent` using the edge as stored in a shape map gave 7 of 12 on a
     box where all edges are convex, because a map keeps one arbitrary orientation.
     Probing along the bisector of the two outward normals cannot work *at all*: at a
     convex edge it points out of the material and at a concave edge into the cavity —
     both outside the solid. The test that works takes the edge **as oriented within one
     of its faces**; all three attempts are written up in `classify.edge_is_convex` so
     the next person does not repeat them.

- **2026-09-05 · Phase 3 — the OCCT kernel backend. DONE** (master plan **E1**), with one
  residual that needs hardware: the CATIA-seat half of the conformance run.

  **22 of 201 operations**, chosen so a real part is buildable rather than to pad the count:
  sketching (create, rectangle, circle, polygon, slot, close), sketch-based solids (pad, pocket,
  shaft, groove), primitives, fillet, chamfer, boolean, shell, translate, measure, rename,
  list_features. The **M1 bracket** — sketch → pad → through-pocket → corner fillets — compiles
  from a `DesignSpec` and builds, with volumes matching closed-form exactly.

  Workstreams: 1.0 ✅ (spike), 1.1 ✅ (kernel service on the `CallRunner` seam), 1.2 🟡 22/201,
  1.3 🟡 parametric profiles done / PlaneGCS deferred to `catia_sketch_constrain` alone,
  1.4 ✅ (persistent naming), 1.5 ✅ (`app/kernel/conformance.py`), 1.6 ✅
  (`app/kernel/determinism.py`).

  **The 1.3 finding is the one worth carrying:** the registry's sketch vocabulary is
  *dimension-driven*, not constraint-driven — a rectangle takes a width and a height, so it is
  fully determined and there is nothing for a solver to solve. Every profile that feeds a pad or
  a shaft was therefore buildable without PlaneGCS. The solver is owed for exactly one operation,
  `catia_sketch_constrain`, which refuses by name until it lands.

  Also learned: a symmetric pad must extrude *half* each way or the part comes out twice as
  thick; `at` means the centre everywhere in this vocabulary, so a corner-anchored rectangle
  would put every profile half a width out; a through-all depth is computed from the part's own
  bounding diagonal rather than a large constant, because a constant generous for a bracket is
  not generous for a chassis; and re-running `catia_sketch_create` during a regeneration must
  clear the profile list, or every rebuild doubles the outline.

- **2026-09-05 · Phase 3 (first increment) — the OCCT kernel foundation.** New
  `app/kernel/` package: `errors` · `measurement` (backend-neutral payload + conformance
  comparison) · `occt/{binding,topology,metrology,naming,selectors,document,runner}` ·
  `occt/operations/{context,document_ops,primitives,dressup,transforms,inspection}`.
  A compiled `DesignSpec` now builds real geometry through the existing `CallRunner` seam,
  is measured, and is checked by the existing assertion engine — with nothing in
  `app/design/` knowing which kernel ran. `tests/test_kernel.py` covers it; ruff clean and
  mypy unchanged at the 7-error baseline (none in `app/kernel/`).

  **1.0 spike passed and it changed the binding choice.** `pythonocc-core` is not on PyPI
  (conda-only), so the binding is `cadquery-ocp` (OCP, OCCT 7.9.3). Persistent naming
  survives a parametric rebuild, verified by recovered-face area against a closed form.
  Three naming rules discovered the hard way are written up in `occt/naming.py`; two of
  them fail by making `Solve()` report success while resolving to nothing.

  Four things worth carrying forward:

  1. **Coverage is 9 of 201 operations and the number is measured, not claimed** —
     `OcctRunner.coverage()` counts handlers against the registry, and an import-time check
     refuses a handler key that names no real operation (dead code that would look live).
  2. **Everything sketch-based is still blocked on PlaneGCS (1.3).** The 9 implemented ops
     are exactly the ones needing no 2D profile. That is why the proof design is a
     cylinder-and-fillet rather than the M1 bracket.
  3. **Measurement had to become tiered for latency.** Every mutating call returns
     post-state, and volume/centre-of-mass are full integrations; at 10⁵ operations that
     *is* the run. `measurement.Detail` gates it, `PartDocument` caches per level, and an
     explicit `catia_measure` always computes the full payload regardless of the batch
     default.
  4. **A plan usually *ends* on `catia_feature_rename`.** Returning a bare acknowledgement
     there makes every assertion on the design come back UNMEASURED, because
     `BuildReport.last_result()` is what the assertion engine reads. Rename returns full
     post-state like every other mutating op.

- **2026-09-04 · Phase 2 (four of five items) — the executor, B4, C3, C4.** New modules in
  `app/design/`: `execute` (walks a plan, resolves the one late-bound name from what the run
  reported, stops at the first failure naming the feature) · `diff` (B4: what changed and how
  far it reaches) · `assertions` (C3) · `correct` (C4). 109 new tests across four files, all
  offline — no DB, no CATIA, no model. Full suite **2849 passed, 4 skipped**; ruff clean; mypy
  unchanged at the 7-error baseline, none of them in `app/design/`.

  Four things learned that are worth carrying forward — they bear directly on the OCCT
  backend in Phase 3, because a second compilation target is exactly what they constrain:

  1. **Impact analysis had to be computed after compilation, not before.** Comparing spec text
     would need a rule to get from "`wall_mm` changed" to "these features moved". Comparing the
     two *compiled* plans makes it exact: by then every parameter is a literal in the argument
     list of the calls that use it, so a feature moved iff its resolved calls differ. A
     parameter nothing reads reaches nothing and says so, with no usage graph to maintain.
  2. **A reference is not always a read, and that distinction is load-bearing.**
     `catia_sketch_rectangle(sketch=@profile)` draws *into* the profile; `catia_pad(sketch=@profile)`
     extrudes what it finds. Widen the rectangle and the pad is a different shape while its own
     call is byte-identical — so following `@` references alone reports the rectangle as the
     only thing that moved and leaves every solid built on it looking current. The two are told
     apart by something the compiler already computes: a feature that creates a tree element
     gets an allocated name, and one that does not is *unaddressable* precisely because its
     effect landed on something else.
  3. **`Plan.digest()` cannot answer "does this build the same part?" — it covers each call's
     `note`.** Rationale rides into the plan on purpose (and `DesignSpec.digest()` covering it
     is a deliberate, tested contract), so rewriting a comment moves both digests while building
     identical geometry. Left both digests alone and added `diff.builds_the_same`, which
     compares tools and resolved arguments only. Every geometry question goes through it —
     whether to rebuild, whether a stored result went stale, whether a repair was a no-op. A
     digest comparison in the correction loop costs exactly one wasted build per no-op repair,
     which is what `test_rewriting_only_a_note_counts_as_no_progress` pins.
  4. **The loop's stopping rules are exact, not heuristic, and that is a gift from
     determinism.** A repair that compiles to the same buildable plan cannot produce a
     different outcome, so it ends the loop without being counted as an attempt; a plan already
     tried is a cycle. Both fall out of the compiler being deterministic. The attempt cap is
     only the backstop. A repair that *does not compile* is deliberately a normal attempt — the
     compiler's error already names the feature and says what to do, and that is the most
     useful feedback in the system, so it goes back round as the next brief.

- **2026-09-03 · `03d238e`** — Reconciled the branch's five hand-rolled assembly tools with
  main's 201-op generated registry. The old implementations were shadowing the new
  registry-driven methods of the same name on both `CatiaCom` and `MockCatia` rather than
  erroring.

- **2026-09-03 · Phase 1 — Layer B foundation (B1 core, B2, B3).** New `app/design/` package:
  `errors` · `names` (semantic naming) · `params` (parameter graph with dimensional checking) ·
  `spec` (the declarative design) · `compile` (deterministic lowering to a call plan). Tests
  green at **2485 passed, 4 skipped**, ruff clean, mypy unchanged at the 7-error baseline,
  99% line coverage across the new package. Also backfilled the branch's untested guard rails:
  `app/catia/ops/registry.py` 69% → **100%**, `app/catia/ops/spec.py` 91% → **100%**,
  `app/solve/selection.py` 87% → 96%.

  Three things learned that the roadmap did not know, kept here because they change Phase 2:

  1. **Only 65 of 201 operations can name what they create.** Every core Part Design feature —
     pad, pocket, hole, fillet, shell, chamfer, draft, the booleans, the transformations,
     the patterns — takes no `name` parameter, so B2 as written ("named at creation") is not
     reachable through them. The compiler follows each with a `catia_feature_rename`, using
     operations that already ship and are already tested, rather than adding a `name` to
     ~39 backend methods that nobody can validate against a real seat until A1 is done.
  2. **A plan needs exactly one late-bound value.** A rename must say which feature to rename,
     and a fresh pad is called whatever CATIA invented. Predicting `Pad.1` is the positional
     fragility Layer B exists to remove, so the plan carries `Created(feature)` and `bind()`
     resolves it from what the creating call reported. Everything else in a plan is a literal.
  3. **Sketch-internal operations are unaddressable and are reported as such.** A rectangle
     drawn inside a sketch leaves no tree row, so nothing can refer to it. Emitting a rename
     and hoping would push the failure back to the workstation.

---

## Queue

### Phase 2 — Regeneration and self-correction *(one item left)*

*The executor, B4, C3 and C4 landed on 2026-09-04 — see Done. What is still owed:*

- **A3 groundwork.** `SemanticName` already reserves the `feature#selector` spelling and
  refuses it with a message pointing here. Predicate selection is what makes it resolve.

  Left rather than rushed, and the reason is worth recording: everything else in Phase 2 is
  decidable from the spec alone, which is why all 109 of its tests run with no seat. A
  predicate — *all edges longer than 10 mm*, *all faces normal to +Z*, *all holes of Ø6* —
  is decidable only against geometry that exists, so it needs `catia_list_edges` /
  `catia_list_faces` answering from a real part. That puts its acceptance behind **A1**,
  which is the one thing on this document that no amount of code here finishes. Building the
  resolver against the mock first would produce a predicate language validated only against
  a mock's idea of an edge, which is how a schema that passes every test refuses every real
  part. Design the vocabulary now; land it when A1 has run.

  The one piece that is decidable today and would not be wasted: the *parser* for
  `feature#selector`, and the compile-time refusal of a selector that names a feature which
  is unaddressable or not yet built. That is the same class of check the compiler already
  does for `@` references and belongs beside them.

### ~~Phase 3 — The OCCT kernel backend~~ ✅ **DONE 2026-09-05** — see *Done* above

*Left here only to record the one residual: `compare_backends` pointed at a real CATIA seat on
Windows. That is the cross-backend half of E1's proof and it needs hardware, not code.*

<details><summary>Original scope, for reference</summary>

*Master plan **Phase 1**. Read [KRYOVA_MASTER_PLAN.md](KRYOVA_MASTER_PLAN.md) Part 0, Decision 1
before starting — this reverses the project's centre of gravity and the reasoning matters.*

Add a second compilation target to `app/design/compile.py`'s plan: **OCCT** via `pythonocc-core`,
driven through the `CallRunner` seam `app/design/execute.py` already defines. Make it the primary
target; CATIA becomes the delivery/interop backend.

It is placed here, ahead of the physics and knowledge phases that used to be next, for one
reason: **everything after it is priced in geometry operations.** While a geometry operation costs
a workstation-second and a licence, optimisation, sensitivity analysis, geometry CI and the
self-correction loop built in Phase 2 are all unaffordable. This phase makes them free.

Order within it: kernel service → operation mapping (mission-led, M1 first) → sketch layer
(PlaneGCS) → **persistent topological naming** (the hard part) → the two-backend conformance
harness → determinism in CI.

*The old Phase 3 (fatigue, mesh convergence, provenance) is master plan Phases 7–8 and moves
after this.*

</details>

### Phase 4 — The physics that actually decides

*Covers D4, D10, D11.*

- **D4 fatigue** — rainflow counting, S-N and ε-N curves, Goodman/Gerber/Soderberg mean-stress
  correction, Miner's rule, BS 7608 / Eurocode 3 weld classes, surface-finish and size factors,
  damage summation over a duty cycle. The roadmap's highest-value physics, and a press frame is
  a fatigue machine before it is anything else.
- **D10 mesh convergence** — refine until the answer stops moving; Richardson extrapolation and
  a reported GCI. An un-converged number stated with confidence is worse than no number.
- **D11 provenance** — every result bound to geometry version, mesh settings, material, load
  case, solver version and convergence evidence.

### Phase 5 — Engineering knowledge

*Covers E1, E2, E3.*

- **E1 requirements model** — target mass, duty cycle, envelope, regulatory regime, cost ceiling;
  flow-down to component constraints and validation flow-up.
- **E2 load-case library** — standardised, per-domain cases so results are comparable between runs.
- **E3 materials** — expand the 8 hard-coded keys into a real table carrying S-N data,
  temperature dependence and cost. (Buying MatWeb/Granta is the right answer eventually; the
  schema is the deliverable here.)

### Phase 6 — Sheet metal, and the stamping machine

*Covers A10 (Sheet Metal subset) and **mission 3**.*

- Sheet Metal ops: wall, bend, flange, unfold/flat pattern, bend allowance and K-factor.
- **Mission 3** — the end-to-end test prompt: a fully functional sheet-metal stamping machine
  with every parameter chosen here (press force, stroke, die set, frame, drive). It has to run
  against everything Phases 1–5 built, or those phases were decoration.

### Phase 7 — Scale

*Covers F1, F2, F3, F4.* Product structure and BOM as first-class data · hierarchical
decomposition with interface contracts (**the only way the context problem is solvable**) ·
change propagation · batched operations, because 10⁵ COM round trips is 28 hours of pure latency.

### Phase 8 — The agent layer

*Covers H1, H2, H3.* Planning into a dependency-aware task graph · retrieval over the op
registry, which is already at 201 and heading for 900 · failure diagnosis and bounded retry.

### Phase 9 — Output

*Covers G1, G2.* Drawing generation with GD&T · export suite (STEP AP242, IGES, JT, 3MF, DXF).

### Phase 10 — Housekeeping the roadmap earned

- Regenerate or delete `CATIA_V5_FULL_COVERAGE.md`; a wrong map is worse than none.
- **I4 observability** — per-op success rates and a failure taxonomy. Nothing currently measures
  op reliability, which is why A1's pass/fail matrix has nowhere to land.
- **I5 determinism** — same spec plus same version ⇒ same geometry, byte for byte.

---

## Blocked on the physical world

Not in the queue, and not counted as done, because no amount of code in this repo finishes them.

- **A1 — validate the 201 ops against the real seat.** *(Phase 3's two-backend conformance
  harness turns most of this into an unattended comparison; what is left is the residue that
  genuinely needs a human at a seat.)* A licensed V5-R33 is on this machine, so
  this is *possible*, but it is a long supervised run producing a pass/fail matrix, not an edit.
  It blocks any honest claim about capability. Needs Phase 9's observability to have somewhere
  to record the result.
- **D1 — CalculiX.** Needs the binary installed and on PATH. Keep `loads.py` and `selection.py`;
  swap the kernel under them.
- **D2 — NAFEMS benchmarks**, **D6 multibody**, **D8 CFD**, **E4–E7**: need an ME, a data
  licence, or both.
- Domain engineers · physical testing · homologation · professional liability · supplier
  relationships · aesthetic judgement. Listed in the roadmap for the right reason; repeated here
  so the queue above is never mistaken for the whole job.
