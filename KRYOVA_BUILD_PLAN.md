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

**E2 — Selection & authoring vocabulary.** 2.1–2.5 are done and **2.6 is started**: a
surface is now a thing the document holds, ten GSD operations build and consume one, and
both crossings back into material work. The decision recorded here on 2026-09-05 — defer
2.6 or build it — was answered by building the half that everything else in the phase was
waiting on.

What 2.6 still owes for `*E2`, each a live `OperationNotSupported` naming its own reason:

- **steered surfaces** — `catia_surface_loft` with `guides` or a `spine` (a swept
  construction with rails, not a section loft), and `catia_surface_fill` with `tangent` or
  `curvature` continuity (each boundary edge has to be paired with the face it lies on);
- **the rest of the trimming family** — `catia_extrapolate`, `catia_sew_surface`, and
  `propagation` on `catia_extract` / `catia_boundary`. Split, trim, untrim, disassemble
  and healing shipped 2026-09-05;
- **the derived wireframe curves** — `catia_curve_{project,parallel,offset_3d,combine,
  connect,corner,spiral,reflect_line}`, plus `catia_plane_{normal_to_curve,
  tangent_to_surface,mean}` and `catia_planes_between`. The drawn curves landed
  2026-09-05 (helix, circle, polyline, spline) with section, intersection and extremum,
  and the anchors with them (`catia_point_{on_curve,on_surface,centre}`,
  `catia_line_{between,direction,normal,tangent}`), so a 3D path and the points that
  position it are both expressible now; what is left is the curves *derived from
  surfaces*;
- `catia_draft` in `reflect_line` mode (needs `catia_curve_reflect_line`);
- a thin-walled `catia_rib`/`catia_slot` (`thick=true` needs a *2D* profile offset in the
  sketcher — the surface offset that shipped today answers a different question);
- `control="reference_surface"` on a swept feature (OCCT takes a surface as a sweep
  reference only when every spine edge already lies on one of its faces, so the spine must
  be *derived from* the surface);
- the surface half of `catia_thickness` (offsetting a curved face rather than sweeping a
  planar one along its normal).

Three of those reasons were **corrected** today rather than inherited: `up_to_surface`,
`thick=true` and `reference_surface` all said they were waiting for constructed surfaces,
which now exist. A stale "blocked on X" outlives X and sends the next reader to rebuild
something that is already there.

Hardware-blocked and **not** counted against the star: the CATIA-seat halves of E1's and
E3's conformance runs need a Windows seat.

## Next

**E4 — Visual verification.** Nothing started. E3's OCCT side is complete, so the
measurement vocabulary a visual check would assert against already exists.

---

## Done

Newest first. Each line names the board row it moved and the commit that moved it.

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
