# Kryova — Build Plan

The short-term working queue: **one phase at a time, green before the next.**
[KRYOVA_MASTER_PLAN.md](KRYOVA_MASTER_PLAN.md) is the controlling document and carries the
phase status board (Part 2) — that board is *current state*. This file is the *history*:
one line lands in **Done** for every board change that is not `not started`.

This file was created on 2026-09-05, after `CLAUDE.md` and the master plan had both been
referring to it for some time and it did not exist. Everything before that date is
reconstructed from the board and `git log`, and is marked as such — a history that says
where it came from is worth more than one that quietly implies it was written as it
happened.

---

## Now

**E4 — Visual verification.** **4.1 and 4.3 landed 2026-09-05.** `app/render/` renders
eight canonical views deterministically and diffs two of them. What is left:

- **4.2 — the vision-model check.** Render, then ask a VLM whether the result matches the
  request. The provider layer is already pluggable and defaults to local Ollama with no
  key, so this is a prompt, a schema for the answer, and a place to put it — not new
  infrastructure. It is a **filter, never a sign-off**: a VLM will confidently approve a
  subtly wrong part, and the phase says so.
- **4.4 — renders into the conversation**, so the user sees what the agent sees. Product
  Track P5 owns the surface, so this waits on it rather than growing its own.
- Section cuts, named in 4.1 alongside the eight views and not yet built: a cut is a
  boolean against a half-space and then the same projection, so the work is the vocabulary
  for saying *where* to cut, not the rendering.

**E2 closed on 2026-09-05** — `*E2`, Proof green. Its capability list and its Proof are
both done; what remains under it are refusals with stated reasons, each raised where it
happens, none of which block the star:

- OCCT's own limits: `continuity='curvature'` on a fill (`BRepFill_Filling` answers "the
  continuity is not G0 G1 or G2"), a loft `closed` along a spine, more than one guide;
- `catia_extrapolate` on a freeform face (OCCT's `ExtendSurfByLength` is inert through
  these bindings) and with `up_to` (an iterative solve — extend generously and cut back
  with `catia_split`);
- `catia_draft` in reflect-line mode: the silhouette exists, but OCCT's draft takes a
  neutral *plane* where the mode pivots about a curve on the face, so it means building a
  ruled surface and replacing the face — surfacing work, not an argument;
- a reflect line at an angle other than 90° (the iso-angle contour is marched face by face
  and comes back sampled);
- a thin-walled `catia_rib`/`catia_slot`, `control="reference_surface"` on a swept
  feature, and the surface half of `catia_thickness`.

**One vocabulary decision is open and deliberately unmade**: the bare word `vertical`
matches a vertical bore's **seam**. A seam is a fact about the parameterisation — nothing
meets there and OCCT will not fillet it — so a design that rounds "the vertical edges"
gains an edge the day somebody drills a hole. But `boss#vertical` naming a cylinder's seam
is how `catia_measure_item` reports a boss's height today, and seven tests rest on
behaviour around it. Narrowing the word is a decision about what the vocabulary *means*,
not a bug fix, and it wants making on purpose rather than in passing.

**Testing moves to the Windows seat.** The user has a Windows machine with CATIA and the
bridge, and asked (2026-09-05) that the suites be run there against the real application
rather than repeatedly here. Offline work continues to be written to be testable; the
verification pass happens on the seat. That also unblocks the two things this machine
never could do — the CATIA-seat halves of E1's and E3's conformance runs.

## Next

**E4 — Visual verification.** **4.1 and 4.3 landed 2026-09-05.** `app/render/` renders
eight canonical views deterministically and diffs two of them. What is left:

- **4.2 — the vision-model check.** Render, then ask a VLM whether the result matches the
  request. The provider layer is already pluggable and defaults to local Ollama with no
  key, so this is a prompt, a schema for the answer, and a place to put it — not new
  infrastructure. It is a **filter, never a sign-off**: a VLM will confidently approve a
  subtly wrong part, and the phase says so.
- **4.4 — renders into the conversation**, so the user sees what the agent sees. Product
  Track P5 owns the surface, so this waits on it rather than growing its own.
- Section cuts, named in 4.1 alongside the eight views and not yet built: a cut is a
  boolean against a half-space and then the same projection, so the work is the vocabulary
  for saying *where* to cut, not the rendering.

---

## Done

Newest first. Each line names the board row it moved and the commit that moved it.

- **2026-09-05** — E4 → **4.1 and 4.3: the system can look at the model.** `app/render/`,
  five modules, no new dependency. **Hidden-line removal rather than OpenGL**, and that is
  the phase's requirement rather than a shortcut: OCP exposes the GL viewer and it comes
  up on this machine, but 4.1 wants two renders of the same geometry to be byte-identical
  so that a render hash can join mass and plan-digest as a third identity check — and a GL
  image depends on the driver, the sampling and the display server, on a project that
  develops on Linux and ships on Windows. HLR is arithmetic; the raster under it is
  integer. Eight views, each from three HLR streams per side, because taking only the
  sharp edges loses every curved silhouette. The same shape renders identically twice, a
  part rebuilt from scratch matches, a part with a pocket differs. Framing is a value, not
  a step: `render_views` fits one frame over every view so a sheet is at one scale, and
  `render_pair` puts two parts through one frame, which is the whole of what makes a diff
  mean anything. **4.3 diffs ink rather than shade** — a line that went from hidden to
  visible has not moved — with added and removed in separate colours, and refuses two
  renders that were framed differently rather than reporting the framing as the change.
  Measured: a plate gaining a Ø14 pocket is 321 pixels arrived, 0 gone, 3.3% of the ink.

- **2026-09-05** — **`*E2` — the phase Proof, written and green.** A 60×40×20 plate whose
  four vertical corners carry 2, 3, 4 and 5 mm — one call, edges chosen by predicate,
  radii matched to the selection order — compiled from a `DesignSpec` and run through the
  real `OcctRunner`; then the *same spec* with a through-notch inserted ahead of the
  fillets, recompiled and rebuilt from nothing. Volume exact against
  `blank − Σh·r²(1−π/4) − notch` both times; the corners come back with the radii the
  design gave them. The renumbering is measured rather than assumed — the plate's vertical
  edges move from 0, 1, 4, 7 to 5, 7, 19, 23 and the design still finds them.
  **Running it found four disagreements between layers that were each right alone**, none
  of which any existing test could see: `catia_fillet.radius_mm` declared a number so the
  kernel's per-edge list was unreachable from a spec (`feature_length_per_entity`);
  `catia_fillet.feature` **declared and silently dropped**, so the design suite's own
  bracket fixture was rounding every vertical edge on the part and reporting success
  (`_scoped_selector`); `Document.feature` looking up only the build name while a compiled
  design renames everything to its own, which made `feature#selector` invisible to an
  authored part; and `topology.shape_list` refusing any list longer than two on the belief
  that OCP had no iterator — it does, and a slot cut *through* a part turns one face into
  five, so an ordinary notch was unbuildable. Left deliberately unmade: whether the bare
  word `vertical` should stop matching a cylinder's seam. 2021 offline tests green, ruff
  clean, mypy clean.

- **2026-09-05** — E2 → **2.6's last capability: a run of boundary**. `catia_boundary`
  takes `limit_from` (where the run starts — an element, and the boundary edge nearest it
  is the seed), `limit_to` (where it stops) and `propagation`. The walk goes outward from
  the seed in both directions and is kept in **connection order** rather than collected as
  a set, because `limit_to` has to cut it and because anything sweeping along it needs to
  know which edge follows which. Verified on a sheet whose boundary is line → arc → line
  all tangent, with creases at the ends: tangency picks out exactly those three edges
  (25 + 15 + πr/2, exact) where point continuity takes the whole 115.708 mm loop, and
  stopping on the arc gives 25 + πr/2. Two cases refused rather than guessed, both for the
  reason `catia_split` gives about which side of a cut survives — a `limit_to` on a run
  that closes into a **loop** (two ways round, nothing chooses), and a **branch vertex**
  where three free edges meet, verified on three blades sharing a root edge where point
  continuity from one tip returns 50 mm and not a millimetre of the other two. Endpoints
  are matched on a micron grid rather than by `IsSame`, because
  `ShapeAnalysis_FreeBounds` rebuilds the boundary and a corner comes back as two vertices
  that are equal to within tolerance and identical to nothing. Six guards, each verified
  by breaking it; the branch stop did not bite until test geometry with a real branch
  existed, which is why the vane is in there. **This closes 2.6's capability list and
  opens the honest question the board now carries: E2's Proof has never been written.**

- **2026-09-05** — E2 → **2.6: `catia_extrapolate`, the last operation the phase owed**.
  Coverage 107 → **108/201**. Three cases, and the route this file recommended for them
  was wrong: `GeomLib::ExtendCurveToPoint` and `ExtendSurfByLength` are OCCT's own answer
  and are **inert through OCP**, which passes their `Handle(Geom_...)&` by value — each
  builds the extension and drops it, silently, with bounds and poles and type unchanged
  afterwards. `test_occts_own_extenders_do_nothing_through_these_bindings` measures that,
  so if a future OCP fixes it the claim in the code fails rather than quietly rotting.
  What runs instead is **widening the parameter range**, which for a conic or an analytic
  surface *is* the extension — a quarter of a Ø20 circle extended 5 mm is 5 mm more of
  that circle, no join, exact — with `GCPnts_AbscissaPoint` turning a length into the
  right parameter step (a circle's parameter is an angle and an ellipse's is neither).
  Where the basis stops at its own end, a curve gets a real piece built from its end
  conditions: a straight segment for `tangent`, an **arc of the osculating circle** for
  `curvature`, swept by `length/radius` so it is G2 and exactly the length asked for by
  construction. Analytic faces widen too, gated on the parameter running at **one speed
  along the boundary** — so a cone extends along its slant to the frustum formula and is
  refused around its axis quoting both speeds, 5 mm per unit at one end of that edge and
  20 at the other. Which end of a curve moves is the one facing what `boundary` names, for
  the reason `catia_curve_connect` states; on a face, `boundary` must pick out exactly one
  of four sides, and **being past a bound beats lying on one** — a point above a sheet sits
  exactly on a side edge's extended line when its x happens to be 0, and reading that as
  "the u edge" widened the face sideways and reported success. Seven guards, each verified
  by breaking it and watching a named test fail; the seventh (the osculating circle's
  frame) did *not* bite at first — a backwards arc is exactly as long as a forwards one —
  so the test now measures where the extension reached, not only how long it is.
  `curve_chain`/`curve_ends`/`CurveEnd` promoted out of `curves.py`'s privates, since
  "which end is the end" must have one answer. Two stale refusals corrected: `boundary`'s
  `limit_*` no longer says "once catia_split lands" (it landed the same day), and the
  module docstring's list of what is missing is current again.

- **2026-09-05** — housekeeping, no board row: **mypy is clean, and CLAUDE.md no longer lists
  errors to expect.** The seven carried in `app/solve/` were two real defects wearing a
  type-checker's clothes. `_bearing` asked `hasattr(where, "axis_point")` — which accepts
  anything that later grows the attribute and tells neither the reader nor mypy which selector
  a bearing load actually needs; it now tests `isinstance(..., CylinderSelector)`. And
  `Fixture.dofs` is `list | None` only at the boundary, since None is how "not given" is
  spelled in a request and `_resolve_dofs` fills it before any solver runs — three assembly
  routines each assumed that silently, so the invariant is now written once as `Fixture.held`.
  Both verified by breaking them: inverting the selector test fails
  `test_it_refuses_a_non_cylindrical_region` and both bearing-distribution tests; making
  `held` return all three axes fails `test_a_roller_really_does_let_the_face_slide` and nine
  others. 988 offline tests green. Frontend `eslint.config.mjs` also ignores `.remember/**` —
  flat config does not skip dot-directories the way eslintrc did, so a hook writing a bare
  timestamp into a file named `last-ndc.ts` was being linted as our source.

- **2026-09-05** — E2 → **2.6 continues: propagation and sewing**. `catia_sew_surface`
  (coverage 106 → **107/201**) and `propagation` on `catia_extract`. Tangent propagation is
  what makes "the rounded end of this part" a selection instead of an enumeration, and the
  one thing that had to be got right is **where tangency is measured**: at the shared edge,
  not between the faces' own normals. A fillet's normal at its parametric centre is 45°
  from the flat face it runs into, so `classify.edge_is_convex` — which uses centre normals
  and is right about the question it answers — calls every fillet a sharp corner here.
  Verified on a flared post whose base, quarter-round fillet and wall all meet smoothly and
  whose top rim does not: tangent propagation returns base + fillet + wall to 1e-16 against
  Pappus, point continuity adds the top disc. `catia_sew_surface` trims a solid to a
  surface, reusing `catia_split`'s stated side rule; `remove` and `reversed` each flip it
  and compose. A surface clear of the part is refused with the reason, because that is the
  case CATIA answers by *adding* material. Six guards, all six verified by breaking them —
  a seventh (three tangency samples per edge rather than one) was **removed from the
  harness and labelled in the code as unpinned**, because every analytic pair of surfaces
  that meets tangentially does so along the whole edge and nothing in the suite can tell
  one sample from three. Flagged, not fixed: `block#vertical` handed to
  `catia_fillet_edges` rounds all twelve edges of a box, not the four vertical ones.

- **2026-09-05** — E2 → **2.6 continues: the steered surfaces**. `catia_surface_loft`
  now takes a `spine` and a `guide`, and `catia_surface_fill` meets its supports
  tangentially. No new operations, so coverage stays at **106/201** — what changed is that
  three arguments the vocabulary declares stopped being refused. A spine is a different
  algorithm rather than a refinement (the sections are swept, not interpolated): two 5 mm
  circles at the ends of a quarter arc give the torus segment Pappus predicts to 1e-9,
  where the free loft of the same sections is 23% smaller. A guide flaring 5→15 over 60 mm
  gives the cone's `π(r₁+r₂)·slant` to one part in 10⁵ — and it only does so with
  `ContactOnBorder`; with `NoContact` the guide merely turns the section about the spine,
  which for a circular section changes *nothing at all* and returns the unguided surface
  reporting success. Two OCCT traps in the fill. **Handing `MakeFilling` a boundary edge
  with no parameter curve on the support segfaults** — `Add` accepts it quietly, the
  process dies inside `Build()`, and there is no exception to catch, so the check has to
  come first; boundary edges are matched to the support's own edges by geometry (length and
  midpoint), never by position in the two lists. **And OCCT reports a tangency it did not
  deliver**: a cylinder's rim asks the patch to leave straight up, the plate solver gives
  up, and it returns `IsDone()` true with a flat disc 82.5° out — where the same call on a
  spherical opening lands within 1e-4°. So the fill measures what it achieved, reports
  `tangent_error_deg`, and refuses a patch that missed. G2 is refused with what OCCT itself
  says. Also fixed: a loft section may be a bare wireframe curve and not only a sketch —
  without that, the spine and guide arguments were unreachable from the curve vocabulary.
  Seven guards, all seven verified by breaking them.

- **2026-09-05** — E2 → **2.6 continues: the reflect line, and the wireframe family closes**.
  `catia_curve_reflect_line` plus `radius_mm` on `catia_curve_polyline`. Coverage 105 →
  **106/201**, and every wireframe operation the registry declares is now implemented. A
  reflect line is the silhouette as *geometry* — the parting line a mould splits along —
  and two things separate it from the hidden-line drawing OCCT computes it with. It keeps
  the **hidden** part (`ShowAll`, not `Hide`: two fused spheres give one equator with
  visibility on and both with it off, and a parting line does not stop existing because
  something is in front of it). And it keeps only what lies on a **curved** face, because
  HLR calls a box's eight boundary edges "outline" and a box has no reflect line at all —
  a polyhedron does its turning at edges that already exist. Without that filter every
  prismatic part appears to have a parting line. Verified against a sphere's great circle
  from three directions and a cylinder's two straight edges. A general angle is refused
  *before anything is resolved*, since no correction to the surface makes an unanswerable
  angle work. The polyline now rounds its own corners, **each in the plane of its own two
  segments** — two consecutive segments always share a plane, a path that turns out of one
  does not, and rounding a 3D path in one fitted plane puts every arc slightly wrong while
  measuring exactly right. Trims accumulate along a run, the wrap-around corner of a closed
  path is rounded too, and two collinear segments are no corner rather than a failure.
  Seven guards, all seven verified by breaking them. The draft's reflect-line refusal was
  **corrected rather than removed**: the silhouette exists now, so the real reason is that
  OCCT's draft takes a neutral *plane* where the mode wants a curve on the face.

- **2026-09-05** — E2 → **2.6 continues: the joins and the spiral**.
  `catia_curve_{corner,connect,spiral}`. Coverage 102 → **105/201**, which leaves
  `catia_curve_reflect_line` as the only wireframe operation still refused. A corner is an
  arc tangent to two curves, exact against `2πr/4` between perpendicular legs, and it
  leaves both inputs untouched — `trim` decides what the *new* element contains, never
  what the old ones are, because a step that edited an earlier one would make the same
  plan mean something different the second time it ran. A connect is a Bézier of the
  lowest degree that carries the continuity asked for: 1, 3 or 5. **The curvature case is
  where the arithmetic bites** — the source states its second derivative in its own
  parameter and the join runs on [0, 1], so the affine reparameterisation factor
  `(s/|d1|)²` is load-bearing; without it the curve is out by the square of the chord
  length, invisible at unit scale and wrong by four orders on a 100 mm join. Across a 60°
  gap in a 10 mm circle the quintic carries the circle's own 0.1/mm and the cubic leaves a
  0.0068/mm step: identical in a shaded view, and exactly the break a reflection shows.
  The operation reports both numbers rather than claiming G2. A spiral is the one curve
  here no kernel holds exactly, so it is fitted and says so — and the honesty has a trap
  of its own: measured at the interpolation knots the fit error reads 1e-14 against the
  9.4e-5 mm it is really out by between them, a factor of 10⁹, so a self-measurement taken
  at the points it was given would report machine zero and be believed. That one took two
  attempts to guard: the first breakage (sampling coarsely) still landed between the
  knots because `GeomAPI_Interpolate` parameterises by chord length, and the test's floor
  of "greater than zero" passed on 1e-14. Nine guards, all nine verified by breaking them.
  Also refactored: `curve_spline` and the spiral now share one interpolator, and the
  polyline's `radius_mm` refusal names what actually exists now.

- **2026-09-05** — E2 → **2.6 continues: the associative curves and planes**.
  `catia_curve_{project,parallel,offset_3d,combine}`,
  `catia_plane_{normal_to_curve,tangent_to_surface,mean}` and `catia_planes_between`.
  Coverage 94 → **102/201**. `plane_normal_to_curve` is the one that earns the rest: it
  places a sweep profile square to its path, so the helix built earlier is now something
  a section can be swept along, and the plane's normal carries the lead angle
  `atan(p/2πr)` exactly. Nine guards, every one verified by breaking it and watching the
  test fail. Three worth naming. **A face is a trimmed piece of an unbounded surface** —
  a live defect the probe found in already-shipped code: `GeomAPI_ProjectPointOnSurf`
  answers for the surface a face was cut out of, so a point beside a cylinder projected
  onto the *infinite plane* of its top disc, 20 mm past the rim and nearer than the wall,
  and `catia_point_on_surface`, `catia_line_normal` and the new tangent plane all agreed
  on a place that is not on the part. `closest_on_surface` now measures against the real
  boundary. **An offset has a side and OCCT does not take it from the argument given** —
  it reads the wire's own winding and never sees the named support; the rule is stated
  here, measured on the built result and mirrored when it went the other way, so the same
  L offsets 77.854 mm on a support facing up and 60 mm on one facing down. The first
  attempt at that guard did not bite, because OCCT already normalises *closed* wires — the
  winding test was testing nothing, and the discriminating case is the support, not the
  curve. **A best-fit plane is an inertia question asked backwards**: the principal axis
  of greatest moment is the covariance's smallest eigenvector, so OCCT computes it exactly
  and the kernel still needs no numpy (checked against `numpy.linalg.svd` to the last
  digit). Points on one line are refused — every plane through a line fits equally well.
  A projected curve is an OCCT B-spline fit (~1 part in 10⁷) and the docstring and the
  test tolerance both say so; `curve_combine` with no directions extrudes each view along
  its own plane, checked against the Steinmetz curve (two ellipses, semi-axes r and r√2).

- **2026-09-05** — E2 → **2.6 continues: the derived anchors**.
  `catia_point_{on_curve,on_surface,centre}` and
  `catia_line_{between,direction,normal,tangent}`. Coverage 87 → **94/201**. What these
  are for is associativity: a point measured once and typed as a coordinate is right
  until the part changes and wrong silently afterwards, and `catia_point_on_curve` is
  right afterwards too. Four traps, each verified by breaking it: **a point on a curve
  walks the whole chain**, not its first edge — halfway along an L of 30 then 40 is 5 mm
  up the second leg, and the first-edge answer (15 mm along the first) is the number
  nobody would question; **the chain is walked in connection order**, because
  `topology.explore` returns edges in *build* order and the two genuinely differ (the
  test fails when swapped); both `ratio` and `distance_mm` are **arc length**, never
  parameter, since a B-spline's parameter is not proportional to its length; a normal is
  read **at the point** rather than at the face centre, identical on a flat wall and a
  different fastener axis on a cylinder; and a point offset along a surface is
  **projected back onto it**, or it is a point in the air that still reads as being on
  the face. `catia_point_centre` refuses anything without an exact centre — a straight
  line gets a refusal rather than its midpoint.
- **2026-09-05** — E2 → **2.6 continues: wireframe curves** (`occt/operations/curves.py`).
  `catia_curve_{helix,circle,polyline,spline,section,intersect,extremum}`. Coverage 80 →
  **87/201**. Until these, every curve in a design came from a planar sketch or a surface
  boundary, so a genuinely 3D path was not expressible at all — a helix cannot be
  sketched, which is the reason the registry gives this its own module. Checked against
  `n·√(pitch² + (2πr)²)` for the length and `r + h·tan(taper)` for the cone, not against
  recorded output: a helix of the wrong pitch and one of the right pitch are the same
  picture. Four traps, each verified by breaking it: **`Geom2d_Line` normalises the
  direction it is given**, so sweeping the pcurve 0 → 2πn builds the right shape and a
  16% wrong length (265.5 mm measured against 314.8); a cone's v runs along the **slant**,
  so climbing `pitch` per turn in height means climbing `pitch/cos(taper)` in v; the
  cylinder's X is aimed at `start_point` rather than left to OCCT, or the helix is right
  in shape and wrong in **phase**; and `BRepLib.BuildCurves3d` is load-bearing — without
  it the edge has no 3D curve at all, measures the right length, reports a box 1.7 mm too
  big in every direction, and makes anything that sweeps along it raise
  `Standard_NullObject` somewhere else entirely. That last one passed every test until
  `catia_measure_item` was widened to report `bounding_box_mm` for a curve, which is the
  cheapest question that tells the two apart. **Response-shape note:** that widening adds
  `bounding_box_mm` to `catia_measure_item`'s payload for an edge element; nothing is
  removed and the path is already in the measurement contract.
- **2026-09-05** — E2 → **2.6 continues: the trimming family**. `catia_split`,
  `catia_trim`, `catia_untrim`, `catia_disassemble`, `catia_healing` and
  `catia_surface_analysis`. Coverage 74 → **80/201**. The question every one of these has
  to answer is "which piece did you mean", and CATIA answers it by where the user clicked;
  there is no click here, so the rule is written down instead — cells ordered by the
  signed distance of their centre from the cutting plane, `first` the side its normal
  points away from — and a cutter with no plane is **refused**, not resolved by whichever
  piece OCCT happened to list first. Verified against the frustum closed form on each side
  of a cut cone, and 600/1000/1600 on a flat panel. Three more traps, each pinned by a test
  that fails when the fix is removed: **cells are not connected components** (a split
  shell's halves share the cut edge, so `domains` correctly returns 1 while the caller
  plainly wants the two faces — this cost a debugging session); **a side means *every*
  cell on it**, because a surface crossing the plane twice is cut into three and keeping
  the furthest one silently drops material; and **`untrim` on a plane is not refused by
  OCCT** — `MakeFace` reports success and hands back a face of area 8 × 10¹⁰⁰, which flows
  into a mass and a bounding box looking like a measurement all the way. `catia_healing`
  refuses to run without a stated `merging_distance_mm` rather than falling back to join's
  tight one and closing nothing, and `catia_surface_analysis(kind='connect')` reports the
  smallest tolerance that *would* join the pieces — the exact argument healing takes, so
  the analysis hands the repair its own parameter instead of saying "there is a gap".
- **2026-09-05** — E2 → **2.6 started**: surfaces exist, and become material only when
  asked. `PartDocument` gained a construction store separate from its bodies, so building
  a skin leaves the part's mass exactly where it was — a surface that quietly became the
  active body would report a part with no solid, which reads like a failed feature rather
  than like a skin waiting to be closed. Ten operations land: extrude, revolve, offset,
  fill, loft, join, extract, boundary, and the two crossings back into material,
  `catia_close_surface` and `catia_thick_surface`. Each checked against the closed form —
  2πrh for a revolved line, π(R+r)·slant for a lofted frustum, and a truncated cone built
  *entirely* as skin then closed into h/3·π(R²+Rr+r²) to 5e-13. Coverage 64 → **74/201**.
  Two OCCT traps found and pinned, both verified by removing the fix and watching the
  right tests fail: **`MakeThickSolidBySimple` returns the solid inside-out**, which
  `BRepCheck_Analyzer` calls valid and which makes a later fuse *silently* return the
  wrong answer (a 1,000 mm³ block fused onto the uncorrected plate measured −4,800 — no
  error, block gone), and **`MakeFilling` approximates even a dead-flat boundary**, so a
  patched circular hole measured 314.1595 mm² against πr² = 314.1593 and carried a
  bounding box half as big again as the disc. A third fix has its own guard:
  `topology.connected_pieces`, because a connexity check written as a shell count reported
  "0 pieces" for two sheets that never met — `explore` flattens, so two disconnected
  shells and one shell of two faces are indistinguishable through it. `catia_extract` is
  where `feature#selector` pays for itself, taking `block#top` off a solid as a surface of
  its own.
- **2026-09-05** — E2 → **2.5 DONE**: stiffeners and the parting draft, the two Part
  Design features whose extent their own arguments do not state. `catia_stiffener`
  thickens an open profile and grows it past the part, subtracts the part, and keeps the
  pieces the profile reaches — so the gusset is exactly the void the walls close
  (½·b·h·t, exact) and stays right when a wall moves. Which way it grows is *stated*
  (sketch normal × the profile's chord, `reversed` to flip) rather than sniffed for,
  because the corner a stiffener fills is empty and every cheap material test answers
  about somewhere the stiffener is not; what replaces the sniffing is a check with a real
  answer — a piece that reaches the far end of its own sweep never met material and is
  refused, naming `reversed` as the fix. `catia_draft` gained its `parting` element: both
  sides taper away from the plane, which is what a two-part mould needs and what one
  taper cannot express, verified against the frustum closed form per side. Built by
  drafting the whole part twice and keeping one half of each, *not* by splitting first —
  splitting would ask the face selector to match halves that did not exist when the
  design named anything. A `neutral` element may now be a planar face of the part; that
  refusal had been pointing at Phase 2.2 since before 2.2 was built. Coverage 63 →
  **64/201**. Both guards were verified by breaking what they protect: removing the
  overrun check and giving both draft halves the same pull direction each fail exactly
  one test.
- **2026-09-05** — E2 → *2.5 partial: swept features, drawn curves, threads*
  (`catia_rib`, `catia_slot`, `catia_thread`, and the open-curve sketch vocabulary —
  line, polyline, arc, three-point arc, ellipse, spline, axis). Coverage 53 → **63/201**.
  Drawn segments now chain, so four `catia_sketch_line` calls make a profile a pad can
  extrude; ribs verified against Pappus's theorem; a thread is an annotation that
  provably does not change the mass, and an unreadable designation reports no pitch
  rather than a guessed one. Measurement contract 1.1 → 1.3, and the version is now
  checked at import against the newest entry — it had already drifted once, which would
  have put a version into a provenance record in which four of its own quantities did
  not exist.
- **2026-09-05** — E2 → *2.5 continues: pad limits, multi-body, listings, solid combine*
  (`7ef04a6`). `up_to_next` / `up_to_last` / `up_to_plane` resolved against the geometry,
  all exact against hand-computed volumes. `up_to_next` means opposite things for a pad
  and a pocket and is tested both ways. Coverage 30 → 53/201.
- **2026-09-05** — E2 → *2.5 started: patterns, transforms, holes, thickness*
  (`eb4d89a`). A pattern repeats the *material a feature added*, recovered as a
  generation difference, so one implementation covers pad/pocket/shaft/boolean.
- **2026-09-05** — E1 → **DONE**, E2 → *2.1–2.4 DONE*, E3 → *OCCT side DONE*
  (`3b5faf0`). The OCCT kernel, interrogation, the measurement contract and the selection
  vocabulary land together. `feature#selector` resolves. Five regressions the phase
  introduced were caught by the first `pytest` run after it and fixed in the same commit.
- **2026-09-04** — E5 → *partial: foundation shipped*. `app/design/{assertions,diff,correct}.py`,
  109 tests, all offline. 5.1–5.4 remain open.
- **2026-09-03** — Design IR: a part is a specification the compiler builds, not a tree it
  edits (`2c54287`). This is what E2 and E5 are both built on.

### Reconstructed, not contemporaneous

Everything above 2026-09-05 was written on 2026-09-05 from the board and `git log`. The
dates and commits are real; the wording is not what was recorded at the time, because
nothing was.

---

## Known documentation gaps

Recorded here rather than fixed silently, because each is a decision someone has to make:

- `CLAUDE.md` references **`KRYOVA_PRD.md`** and **`KRYOVA_STATE_OF_THE_PROJECT.md`**.
  Neither exists in this repository. Either write them or stop pointing at them — a
  reference to a missing document sends the next reader looking for context that is not
  there, which is worse than saying the context does not exist.
- `KRYOVA_CAPABILITY_ROADMAP.md` is named as the audit E2 grew out of and is likewise
  absent from the working tree.
- `ADDED_SYMBOLS.md` is an untracked one-off dump of the symbols added between
  2026-09-01 and 2026-09-03. It is not referenced by anything; delete it or track it
  deliberately.
