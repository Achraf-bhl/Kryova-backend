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
- **the trimming family** — `catia_split`, `catia_trim`, `catia_healing`, `catia_untrim`,
  `catia_disassemble`, `catia_extrapolate`, `catia_sew_surface`, and `propagation` on
  `catia_extract` / `catia_boundary`;
- **the wireframe curves they are built on** — `catia_curve_*`, `catia_line_*`,
  `catia_point_on_*`, `catia_plane_normal_to_curve` and their kin, none started;
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
