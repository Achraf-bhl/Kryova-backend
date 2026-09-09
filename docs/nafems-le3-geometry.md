# NAFEMS LE3 — "Hemispherical shell with point loads": sourcing record

**Status: geometry fully sourced.** The sphere radius, the shell thickness and the modelled
fraction were each read off a document that is cited below, and each is corroborated by at
least three documents that did not copy one another — including a reproduction of the
**original NAFEMS dimensioned figure** as a scanned image, and a **committed Abaqus input deck
whose node coordinates were checked numerically in this task**.

**The polar hole does not exist.** The task that commissioned this record asked for "the polar
hole half-angle". There is no hole in NAFEMS LE3: the shell is closed at the pole, and point E
*is* the pole. Four independent sources say so (§1.5), one of them by node coordinates. The
widely-repeated "hemisphere with an 18° hole" is a **different benchmark** being labelled LE3;
§7 records where that claim was found and why it is not LE3's geometry.

**One value is INFERRED, not printed anywhere:** that the stated 10 m radius is the
**mid-surface** radius. The reasoning is in §1.7. Every source consulted models LE3 with shell
elements, where a single radius and a thickness are the whole specification and the question
does not arise — so **if LE3 is encoded as a shell, §1.7 can be ignored entirely.** It matters
only if someone builds LE3 as a solid, where the shell spans 9 980 mm to 10 020 mm.

**What blocks `run_le3` is not geometry.** The catalogue entry already records the blocker
correctly as `Blocker.NO_SHELL_SOLVER`. §6 says what is left once that lifts, and it is about
element behaviour and tolerance, not about dimensions.

Written 2026-09-09, to the pattern of `docs/nafems-le11-geometry.md`. Nothing here is recalled:
if a number is not attributed to a document in §1–§4, it is not in this file.

---

## 1. The geometry

LE3 is a **hemispherical shell** of constant thickness, closed at the pole, with a free
equatorial edge. Four radial point loads act on that free edge — two outward, two inward, at
right angles. Geometry and loading are both doubly symmetric, so **one quarter (a 90° sector)**
is modelled, bounded by the two meridians AE and CE and by the equator arc AC.

### 1.1 The primary source — the original NAFEMS dimensioned figure

TechSoft3D's *"The Standard NAFEMS Benchmark Tests for HOOPS Solve"* report reproduces, on its
page 6, a **scan of the original NAFEMS drawing** for LE3 — not a redraw. Rendered from the PDF
at 900 dpi and read directly. The two views and their annotations, verbatim:

| Annotation on the figure | View | What it dimensions |
|---|---|---|
| `x 2 + y 2 + z 2 = 100` | quarter view, leader to the shell surface | the sphere the shell lies on |
| `r = 10m` | quarter view, leader to the same surface | sphere radius |
| `Thickness = 0.04m` | beneath the full-hemisphere view | shell thickness |
| `2KN` | quarter view, arrow at **C** pointing *inward* | load at C |
| `2KN` | quarter view, arrow at **A** pointing *outward* | load at A |
| `z` `y` `x` (axis triad at the origin) | quarter view | axes: **z** polar, **x** through A, **y** through C |
| `E` | both views, on the polar axis | the pole |
| `A`, `C` | both views, on the equator | the loaded points |

The page's caption is `Find the displacement in x- direction of point A of hemisphere shown
below`, and the reference line under it is
`NAFEMS Finite Element Methods & Standards, The Standard NAFEMS Benchmarks, Test No. LE3.
Glasgow: NAFEMS, Rev. 3, 1990`.

Read straight off that table:

- `x² + y² + z² = 100` is the sphere of radius `√100` = **10 m** centred on the origin, which
  is the same 10 m the `r = 10m` annotation gives. The figure states the radius **twice, in two
  forms**, and they agree. This is the strongest single line in the whole record: an equation
  is not a rounded annotation.
- **Neither view has a hole.** In the quarter view the two meridian edges converge to a single
  dot at E on the polar axis. In the full-hemisphere view the dome closes at E, with only the
  axis tick passing through it. Both were inspected at 900 dpi.

> **Do not trust the rest of that page.** The HOOPS report's *prose* around this figure is
> corrupt: it states the loading as `Uniform normal pressure of 1 MPa on the upper surface of
> the plate` (LE3 has no plate and no pressure — this is copy-paste from the preceding
> benchmark), describes the model as `plane stress quadrilateral elements` for a shell, and
> spells symmetry `sylletry`. The **figure** is a clean scan of the NAFEMS original and is what
> is cited; the surrounding text is a transcription artefact and is used for nothing.

### 1.2 The second dimensioned figure and its text — ESRD

ESRD's benchmarks guide (the primary source for LE11) carries LE3 on its page 12. Its **model
description**, verbatim:

> - 90° sector of hemispherical shell of R = 10 m with a constant thickness T = 0.04 m.
> - Linear elastic analysis, Young's modulus = 68.25 GPa, Poisson's ratio = 0.3.
> - Uz = 0 at point E.
> - Symmetry boundary conditions along edges AE and CE.
> - Concentrated point loads of Fx = 2 kN at point A, Fy = -2 kN at point C.
> - Objective of the analysis is to compute the radial displacement at point A.

Its figure (a StressCheck render, not the NAFEMS original) is annotated `T = 0.04 m`,
`R = 10 m`, `90°` (as the angle at the origin between the radii to A and to C), `2 kN` at A
outward, `-2 kN` at C inward, `E` at the top with a dashed line down the polar axis, and an
axis triad marked `Z` up, `Y`, `X`. Rendered at 500 dpi and inspected around E: **the surface
converges to the single point E on the axis. No hole.**

Unlike the LE11 entry, ESRD's LE3 text states the dimensions outright, so the figure is
corroboration here rather than the only carrier.

### 1.3 The cross-check — a committed Abaqus input deck

`Tests/Abaqus/Quad4_shell.inp` and `Tests/Abaqus/Tri3_shell.inp` in the `c3m-labs/ImportMesh`
repository are complete LE3 decks, committed as mesh-reader fixtures. Their headers, verbatim:

```
*HEADING
: NAFEMS TEST LE3, Hemispherical Shell with Point Loads  [S4]
```
```
*HEADING
: NAFEMS TEST LE3, Hemispherical Shell with Point Loads  [S3R]
```

The model cards, verbatim (identical in both files):

```
*MATERIAL,NAME=A1
*ELASTIC,TYPE=ISO
 6.825E+07,     0.3000,     0.0000
*SHELL SECTION,MATERIAL=A1,ELSET=EALL
 4.000E-02,
*NSET,NSET=ZCONSTR
 8,64
*NSET,NSET=XSYM
 18,20,22,25,26,92,96,100,104,108,121,122,123,124
*NSET,NSET=YSYM
 8,11,14,23,25,64,69,74,79,84,109,113,117,121
*NSET,NSET=DISPSET
8,64
*BOUNDARY,OP=NEW
 ZCONSTR,3
 XSYM,XSYMM
 YSYM,YSYMM
*CLOAD,OP=NEW
    8,     1,  2.000E+00
   18,     2, -2.000E+00
   64,     1,  2.000E+00
   92,     2, -2.000E+00
```

and the three nodes that matter, verbatim (the trailing triplet on each line is the unit
normal, which is the coordinate divided by 10 — itself a statement that the radius is 10):

```
    8,    10.0000,     0.0000,     0.0000,     1.0000,     0.0000,     0.0000
   18,     0.0000,    10.0000,     0.0000,     0.0000,     1.0000,     0.0000
  121,     0.0000,     0.0000,    10.0000,     0.0000,     0.0000,     1.0000
```

**This is a construction, so it fixes what a figure leaves implicit.** All 80 nodes of each
deck were read and measured in this task; the measurements are:

| Measured over all 80 nodes | Value |
|---|---|
| distance from origin, min / max | 9.999935 / 10.000061 (the 4-dp printing of a 10.0 sphere) |
| polar angle from +z, min / max | **0.0000° / 90.0000°** |
| azimuth in the equator plane, min / max | **0.000° / 90.000°** |
| min x, min y, min z | 0.0000, 0.0000, 0.0000 |

A minimum polar angle of exactly 0° is node 121 at `(0, 0, 10)` — **the pole itself is a mesh
node**. There is no hole. The azimuth range 0°–90° with `min x = min y = 0` is the quarter
model, occupying the first octant.

Deck units are **m and kN**: `6.825E+07` kN/m² = 68.25 GPa, `4.000E-02` m = 40 mm,
`2.000E+00` kN = 2 kN, and the reported displacement 0.185 is then in metres. Every one of
those matches the figures of §1.1–§1.2, which is what makes the unit reading safe.

Two details the deck settles that nothing else consulted does:

- **Each deck holds two meshes.** Element sets `S4_P2`/`S4_P4` (and `S3_P2`/`S3_P4`) are two
  discretisations of the same quarter model with disjoint node numbering, which is why nodes
  64 and 92 duplicate the coordinates of 8 and 18 and why there are four `*CLOAD` lines rather
  than two. `DISPSET = 8,64` is point A in each of the two meshes. **Do not read the four
  loads as a full-hemisphere model.**
- **The quarter model applies the full 2 kN, not half of it.** A and C sit on the symmetry
  planes, and the deck loads them at `2.000E+00` — the same magnitude the figure prints for
  the physical load. ESRD's quarter-model description does the same (`Fx = 2 kN at point A`).
  Halving them for the symmetry plane would be wrong here; two independent quarter-model
  sources do not halve.

### 1.4 Two further text reproductions

**Altair OptiStruct OS-V: 0030, "Radial Point Load on a Hemisphere"**, verbatim:

> The hemisphere is 10m in radius and 0.04 m in radial thickness.
> […] only a quarter of the hemisphere is modeled
> Two pairs of identical loads, 4000 N, are applied at the free edge of the hemisphere […] are
> at right angles to each other. One pair of the loads is directed inwards (toward the center)
> of the hemisphere, while the second pair is directed outward from the center
> Symmetric boundary constraints are applied on edges AE and CE
> The z-translation at point E is fixed, and all displacements on edge AC are free
> The target is x-translation at point A, with a target value of 0.185 m

No hole is mentioned. Note `all displacements on edge AC are free` — the **equator is a free
edge**, which no other source states in so many words.

**DIANA FEA verification manual, LE3** (`3cs12` / `3cs48`), verbatim:

> One quarter sector of hemisphere
> Symmetry conditions along AE and CE. Zenith E vertically supported.
> Radial loads: outwards at A, inwards at C.

`Zenith E` is a fourth source placing E at the pole. DIANA gives no radius or thickness.

### 1.5 The geometry, resolved — build from this

In **millimetres** (this repo is mm-N-MPa; every source states LE3 in metres, so each value is
the sourced metre figure × 1000, shown).

| Quantity | Value | Where it comes from |
|---|---|---|
| Sphere radius (mid-surface) | **10 000 mm** (10 m) | NAFEMS figure `x²+y²+z²=100` **and** `r = 10m`; ESRD text `R = 10 m` and figure `R = 10 m`; Altair `10m in radius`; deck nodes measured at 10.000 |
| Shell thickness | **40 mm** (0.04 m) | NAFEMS figure `Thickness = 0.04m`; ESRD text and figure `T = 0.04 m`; Altair `0.04 m in radial thickness`; deck `*SHELL SECTION … 4.000E-02` |
| Polar hole half-angle | **none — there is no hole** | NAFEMS figure, both views close at E; ESRD figure closes at E; deck node 121 = `(0,0,10)`, measured min polar angle 0.0000°; DIANA `Zenith E` |
| Fraction modelled | **one quarter — a 90° sector** | ESRD `90° sector` and the figure's `90°` annotation; Altair `only a quarter of the hemisphere is modeled`; DIANA `One quarter sector of hemisphere`; deck azimuth measured 0.000°–90.000° |
| Polar extent | **0° (pole) to 90° (equator)** | deck, measured 0.0000°–90.0000°; consistent with "hemisphere" in all four texts |
| R / t | 250 | derived: 10 000 / 40 |
| Point **A** | `(10 000, 0, 0)` mm | deck node 8; NAFEMS figure puts A on the x axis at the equator |
| Point **C** | `(0, 10 000, 0)` mm | deck node 18; NAFEMS figure puts C on the y axis at the equator |
| Point **E** | `(0, 0, 10 000)` mm | deck node 121; NAFEMS figure, ESRD figure, DIANA `Zenith` |
| Edge **AE** | meridian in the plane y = 0 | ESRD, Abaqus, Altair, DIANA all name it; deck `YSYM` nset is the y = 0 nodes |
| Edge **CE** | meridian in the plane x = 0 | as above; deck `XSYM` nset is the x = 0 nodes |
| Edge **AC** | equator arc, z = 0 — **free** | Altair `all displacements on edge AC are free`; no source restrains it |

> **Axis convention.** All five sources agree and none has to be relabelled: **z is the polar
> axis, x passes through A, y passes through C.** The NAFEMS figure's triad, ESRD's `Z`/`Y`/`X`
> triad, Abaqus's `ux = 185 mm at point A` with `symmetry about the z–x plane` along AE, and
> the deck's node coordinates all say the same thing. This is unlike LE11, where ESRD's axial
> axis was y and everyone else's was z. **There is no axis ambiguity in LE3.**

### 1.6 Why "no hole" is stated this firmly

Because it is the one thing a reader is most likely to "know" wrongly, the evidence is spelled
out rather than summarised:

1. The **original NAFEMS drawing** (§1.1) draws the quarter sector with edges AE and CE meeting
   at a point, and separately draws the whole hemisphere closed at the top. A hole large enough
   to matter (18° is 3.09 m across on a 10 m sphere) would be unmissable in either view.
2. The **committed deck** (§1.3) has a node at exactly `(0, 0, 10)` and a measured minimum
   polar angle of 0.0000°. A mesh cannot have a node at the centre of a hole.
3. **DIANA** calls E the `Zenith` and supports it vertically. A zenith is a point, not a rim.
4. **Every** source restrains a *point* E — `Uz = 0 at point E` (ESRD),
   `ux=uy=uz= 0 at point E` (Abaqus), `The z-translation at point E is fixed` (Altair). If
   there were a polar hole, E would be an edge and the restraint would be stated on an edge,
   as AE and CE are.

### 1.7 INFERRED — the 10 m is the mid-surface radius

No source consulted says the words "mid-surface". Every source consulted models LE3 with
**shell** elements, where the modelled surface is the reference surface and the thickness is a
property of it, so the distinction never has to be printed. Building LE3 as a *solid* forces
the question, and the answer taken here is that **10 m is the mid-surface radius, giving an
inner radius of 9 980 mm and an outer radius of 10 020 mm.** Inferred from the cited documents,
not from memory:

- The deck's `*SHELL SECTION` (§1.3) carries **no offset parameter**. Abaqus's shell reference
  surface without an offset is the mid-surface, so the deck's nodes — measured at radius 10.0 —
  are on the mid-surface by the deck's own construction.
- The deck prints a unit normal on every node line equal to the coordinate ÷ 10. The shell is
  therefore thickened symmetrically about that surface along ±n; nothing in the deck biases it
  to one side.
- Altair's wording is `0.04 m in radial thickness` — thickness measured *radially* about a
  radius of 10 m, with no inner/outer radius given, which is only a complete specification if
  the 10 m is the middle.

The consequence is 20 mm either way on a 10 000 mm radius (0.2%). It is recorded as INFERRED
because it is a genuine choice, and because if a later lane instead treats 10 m as the outer
radius the whole shell moves inward by 40 mm.

---

## 2. Material, loads and boundary conditions

### 2.1 Material

| Quantity | Value | Sources |
|---|---|---|
| Young's modulus | 68.25 GPa = 68 250 MPa | Abaqus `Young's modulus = 68.25 GPa`; ESRD `Young's modulus = 68.25 GPa`; Altair `E: 68.25 GPa`; HOOPS text `E = 68.25 x 10³ Mpa`; deck `6.825E+07` kN/m² |
| Poisson's ratio | 0.3 | Abaqus; ESRD; Altair `υ: 0.3`; HOOPS `v = 0.3`; deck `0.3000` |

These were already cited in `app/verify/nafems.py` under `abaqus-le3`; the four further
sources here agree exactly and are recorded so the entry is no longer single-sourced.

### 2.2 Loads

Four radial point loads on the free equatorial edge of the **full** hemisphere, `2 kN` each,
alternating in sign every 90°: outward at A and at its antipode (±x), inward at C and at its
antipode (±y).

| | Value | Sources |
|---|---|---|
| At **A** | `+2 kN` along **+x** (radially **outward**) | NAFEMS figure `2KN` outward at A; ESRD `Fx = 2 kN at point A`; Abaqus `2 kN outward at A`; deck `8, 1, 2.000E+00` |
| At **C** | `−2 kN` along **+y**, i.e. 2 kN radially **inward** | NAFEMS figure `2KN` inward at C; ESRD `Fy = -2 kN at point C`; Abaqus `inward at C`; deck `18, 2, -2.000E+00` |
| In the quarter model | the **full** 2 kN at each, not halved | ESRD (a quarter-model description) prints 2 kN; the deck applies `2.000E+00`. §1.3. |

The full-hemisphere view of the NAFEMS figure shows all four arrows; the quarter model sees
two of them.

> **The one load disagreement.** Altair writes `Two pairs of identical loads, 4000 N`. Read as
> 4000 N *per load* that contradicts every other source. Read as a *pair total* — two loads of
> 2 kN making up each 4 kN pair — it agrees with all of them, and Altair's own next sentence
> (`One pair … directed inwards … the second pair … directed outward`) describes exactly the
> four-arrow full-hemisphere figure. Taken as a pair total. **2 kN per point is the value**,
> carried by four sources including the original figure and a committed deck.

### 2.3 Boundary conditions

| Constraint | Where | Sources |
|---|---|---|
| symmetry about the **z–x** plane | edge **AE** (y = 0) | Abaqus `Along edge AE, symmetry about the z–x plane`; ESRD; Altair; DIANA; deck `YSYM,YSYMM` on the y = 0 nodes |
| symmetry about the **y–z** plane | edge **CE** (x = 0) | Abaqus `Along edge CE, symmetry about the y–z plane`; ESRD; Altair; DIANA; deck `XSYM,XSYMM` on the x = 0 nodes |
| **free** | edge **AC** (the equator, z = 0) | Altair `all displacements on edge AC are free`; no other source restrains it |
| axial restraint to kill the last rigid-body mode | **see below — sources disagree on where** | — |

The two symmetry planes hold five of the six rigid-body modes. The sixth — rigid translation
along **z** — has to be removed somewhere, and the sources **do not agree where or by how
much**:

| Source | What it says, verbatim | Reading |
|---|---|---|
| ESRD | `Uz = 0 at point E.` | one dof, at the pole |
| Altair | `The z-translation at point E is fixed` | one dof, at the pole |
| DIANA | `Zenith E vertically supported.` | one dof, at the pole |
| Abaqus | `ux=uy=uz= 0 at point E.` | three dofs, at the pole |
| the deck | `*NSET,NSET=ZCONSTR` = `8,64`; `ZCONSTR,3` | one dof (z), at **point A** |

**These are not in conflict about the physics, only about the bookkeeping.** E lies on both
symmetry planes, so ux and uy are already zero there by symmetry; Abaqus's `ux=uy=uz=0` is the
same restraint written out redundantly. The deck differs more interestingly: it leaves the pole
free in z and pins z at A instead — E is in both the `XSYM` and `YSYM` nsets, so the deck's
pole is held in x and y and free in z. **Any single z restraint at a point that is not
otherwise loaded in z removes the same mode and adds no reaction**, so all five are the same
model. Recorded in full because a later reader comparing this file against the Abaqus page will
otherwise think one of them is wrong.

Prefer `uz = 0 at E` for the encoding: it is what three of the four *published* sources say,
and E is the one point that is unambiguous in every reproduction.

---

## 3. The target

| | |
|---|---|
| Quantity | displacement of point **A** in the **x** direction — which at A is the radial direction |
| Point | **A** = `(10 000, 0, 0)` mm, on the free equator, on the x axis |
| Value | **185 mm** (0.185 m) outward |
| Tolerance | 2% (as already recorded in `app/verify/nafems.py`) |
| Basis | NAFEMS reference solution, TNSB Rev. 3, October 1990 |

Sources, verbatim: Abaqus `ux= 185 mm at point A`; ESRD `Radial displacement at point A is 185
mm.`; Altair `The target is x-translation at point A, with a target value of 0.185 m`; HOOPS
results table `Theory … 0.185`; DIANA reference column `0.185`; PrePoMax forum thread
`ux= 185 mm at point A`.

185 mm is **1.85% of the radius**, which is what makes this a bending-dominated test rather
than a small-perturbation one.

Published reproductions, for calibrating what a converged answer looks like:

| Source | Result | vs 185 mm |
|---|---|---|
| ESRD StressCheck, quad/tri, 16 elements | 184.3 mm | −0.4% (their figure) |
| ESRD StressCheck, quad/tri, 64 elements | 184.4 mm | −0.3% (their figure) |
| DIANA `3cs48`, 48 × CQ40S | 183 mm | −1.1% |
| MSC Nastran (via the HOOPS report) | 179 mm | −3.2% |
| HOOPS Solve | 176 mm | −4.9% |
| DIANA `3cs12`, 12 × CQ40S | 134 mm | −28% |

That last row is the point of the benchmark: a coarse shell mesh on this geometry loses a
quarter of the answer to membrane locking. **A 2% tolerance is passed only by the p-version and
by well-refined shells** — two of the six published reproductions above miss it.

ESRD attaches a caveat that any implementation lane must read, verbatim:

> Note: Point loads are inadmissible input data for hierarchic shell models because the strain
> energy associated with a point load is not finite and therefore the corresponding
> displacements cannot be finite. However when point loads are used for computing displacements
> (as in this benchmark problem) the divergence in the data of interest is extremely slow and
> the reported results compare well with the reference solution.

See §6 — for a **solid** model that caveat is considerably worse than "extremely slow".

---

## 4. Agreement matrix

Rows are the values; columns are the five independent documents. "—" means the document does
not carry that value at all.

| | NAFEMS figure (via HOOPS) | ESRD | Abaqus deck (measured) | Altair | DIANA | Agree? |
|---|---|---|---|---|---|---|
| Sphere radius | `x²+y²+z²=100`, `r = 10m` | `R = 10 m` | nodes at 10.000 | `10m in radius` | — | ✅ exact, 4 sources |
| Thickness | `Thickness = 0.04m` | `T = 0.04 m` | `4.000E-02` | `0.04 m` | — | ✅ exact, 4 sources |
| Hole at the pole | none drawn, both views | none drawn; E a point | node at `(0,0,10)`, 0.0000° | not mentioned | `Zenith E` | ✅ no hole, 4 sources |
| Fraction modelled | 90° quarter drawn | `90° sector` | azimuth 0–90° | `a quarter` | `One quarter sector` | ✅ exact, 5 sources |
| Axis convention | z polar, x→A, y→C | `Z`/`Y`/`X` triad, same | node 8 = A on +x | implied by `x-translation at A` | — | ✅ no relabelling needed |
| E is the pole | on the axis | on the axis | `(0,0,10)` | `point E` | `Zenith` | ✅ exact, 5 sources |
| Young's modulus | — | `68.25 GPa` | `6.825E+07` kN/m² | `68.25 GPa` | `elastic isotropic` | ✅ exact, 3 sources (+ Abaqus page, + HOOPS text) |
| Poisson's ratio | — | `0.3` | `0.3000` | `0.3` | — | ✅ exact |
| Load magnitude | `2KN` at A and at C | `2 kN` / `-2 kN` | `2.000E+00` | `4000 N` per **pair** | `radial loads` | ⚠️ Altair's wording — resolved in §2.2 |
| Load direction | outward A, inward C | `Fx = 2`, `Fy = -2` | `+2` in dof 1 at node 8, `−2` in dof 2 at node 18 | one pair in, one out | `outwards at A, inwards at C` | ✅ exact |
| Symmetry on AE, CE | — | both | `YSYM`, `XSYM` | both | both | ✅ exact, 4 sources |
| Equator AC free | drawn as a free rim | — | unrestrained | `all displacements … free` | — | ✅ exact |
| The last z restraint | — | `Uz = 0 at E` | `uz = 0` at **A** | `z-translation at E` | `E vertically supported` | ⚠️ different points — equivalent, §2.3 |
| Target | — | `185 mm` | `DISPSET` at A | `0.185 m` | `0.185` | ✅ exact, 5 sources (+ Abaqus page) |
| Mid-surface vs outer radius | — | — | no `*SHELL SECTION` offset | `radial thickness` | — | ⚠️ **INFERRED**, §1.7 |

Three rows are not ✅, and all three are named honestly above: Altair's `4000 N` (a pair total,
§2.2), the location of the z restraint (equivalent, §2.3), and the mid-surface reading (a
genuine inference with no source, §1.7).

---

## 5. Drafted `SOURCES` entries

To paste into `app/verify/nafems.py`, in the register of the entries already there. **The
existing `abaqus-le3` entry is unchanged** — it carries the material, the loads, the boundary
conditions and the target, and it honestly does not carry the geometry.

```python
    "nafems-le3-figure": (
        "The original NAFEMS LE3 dimensioned figure, reproduced as a scan on page 6 of "
        "TechSoft3D 'The Standard NAFEMS Benchmark Tests for HOOPS Solve' (report for "
        "HOOPS Solve 2.13.0), which cites 'NAFEMS Finite Element Methods & Standards, The "
        "Standard NAFEMS Benchmarks, Test No. LE3. Glasgow: NAFEMS, Rev. 3, 1990'. The "
        "primary geometry source: it states the sphere twice and in two forms, as the "
        "equation x^2 + y^2 + z^2 = 100 and as r = 10m, with Thickness = 0.04m, 2KN "
        "outward at A and 2KN inward at C, an axis triad with z polar / x through A / y "
        "through C, and both the quarter view and the full-hemisphere view drawn CLOSED "
        "AT THE POLE — LE3 has no polar hole. Only the figure is cited: the surrounding "
        "prose on that page is corrupt (it states the loading as a 1 MPa pressure on a "
        "plate, copy-pasted from the preceding benchmark). Read at "
        "https://docs.techsoft3d.com/hoops/mesh/_static/benchmark_reports/"
        "benchmark_results_2.13.0.pdf on 2026-09-09."
    ),
    "esrd-le3-geometry": (
        "ESRD 'Benchmarks Guide — The Standard NAFEMS Benchmarks: Linear Elastic Tests' "
        "(2018), section 'NAFEMS LE3: Hemispherical Shell with Point Loads', page 12, "
        "reproducing NAFEMS publication TNSB Rev. 3, 'The Standard NAFEMS Benchmarks', "
        "October 1990. States the geometry in text rather than only in a figure: '90 deg "
        "sector of hemispherical shell of R = 10 m with a constant thickness T = 0.04 m', "
        "'Uz = 0 at point E', 'Symmetry boundary conditions along edges AE and CE', "
        "'Concentrated point loads of Fx = 2 kN at point A, Fy = -2 kN at point C', "
        "'Radial displacement at point A is 185 mm'. Its figure closes at E, confirming "
        "there is no polar hole. Reports StressCheck 184.3 mm (16 elements) and 184.4 mm "
        "(64 elements), and warns that point loads are inadmissible data for a hierarchic "
        "shell model because the strain energy of a point load is not finite. Read at "
        "https://www.esrd.com/wp-content/uploads/dlm_uploads/"
        "Benchmarks-Guide-Standard-NAFEMS-Benchmarks-Linear-Elastic-Tests.pdf "
        "on 2026-09-09."
    ),
    "abaqus-le3-deck": (
        "Committed Abaqus input decks for NAFEMS LE3, `Tests/Abaqus/Quad4_shell.inp` "
        "(*HEADING ': NAFEMS TEST LE3, Hemispherical Shell with Point Loads  [S4]') and "
        "`Tests/Abaqus/Tri3_shell.inp` ('[S3R]'), in the c3m-labs/ImportMesh repository, "
        "where they serve as mesh-reader fixtures. The construction that fixes what the "
        "figure leaves implicit, in m-kN units: *ELASTIC 6.825E+07 kN/m^2 and 0.3000, "
        "*SHELL SECTION 4.000E-02 with no offset parameter, *CLOAD 2.000E+00 in dof 1 at "
        "node 8 = (10, 0, 0) = A and -2.000E+00 in dof 2 at node 18 = (0, 10, 0) = C, "
        "XSYMM on the x = 0 nodes (edge CE), YSYMM on the y = 0 nodes (edge AE), and dof 3 "
        "fixed at A. All 80 nodes measured in this task: distance from origin 9.999935 to "
        "10.000061, polar angle from +z 0.0000 to 90.0000 deg, azimuth 0.000 to 90.000 "
        "deg — node 121 is (0, 0, 10), THE POLE ITSELF IS A MESH NODE, so there is no "
        "polar hole. Each file holds two discretisations with disjoint numbering (element "
        "sets _P2 and _P4), which is why four *CLOAD lines appear for a two-load model. "
        "Read at "
        "https://raw.githubusercontent.com/c3m-labs/ImportMesh/master/Tests/Abaqus/"
        "Quad4_shell.inp and .../Tri3_shell.inp on 2026-09-09."
    ),
    "altair-le3": (
        "Altair OptiStruct verification problem OS-V: 0030 'Radial Point Load on a "
        "Hemisphere', reproducing NAFEMS LE3. Carries 'The hemisphere is 10m in radius and "
        "0.04 m in radial thickness', 'only a quarter of the hemisphere is modeled', "
        "E = 68.25 GPa and nu = 0.3, 'Symmetric boundary constraints are applied on edges "
        "AE and CE', 'The z-translation at point E is fixed, and all displacements on edge "
        "AC are free' — the only source consulted that states the equator is a free edge — "
        "and 'The target is x-translation at point A, with a target value of 0.185 m'. "
        "Writes the loads as 'Two pairs of identical loads, 4000 N', which is a pair total "
        "of two 2 kN loads and not 4000 N per point; see the sourcing record section 2.2. "
        "Its reference line names NAFEMS R0015 (a natural-frequency publication), which is "
        "a slip on that page — every other source cites TNSB Rev. 3. Read at "
        "https://help.altair.com/hwsolvers/os/topics/solvers/os/"
        "nafems_test_problem_le3_r.htm on 2026-09-09."
    ),
    "diana-le3": (
        "DIANA FEA verification manual, NAFEMS benchmarks section, tests 3cs12 and 3cs48 "
        "for LE3: 'One quarter sector of hemisphere', 'Symmetry conditions along AE and "
        "CE. Zenith E vertically supported.', 'Radial loads: outwards at A, inwards at C', "
        "target displacement uX at A of 0.185. Carries no dimensions, and is cited for two "
        "things only: it calls E the *zenith*, corroborating that LE3 is closed at the "
        "pole, and its results calibrate the tolerance — 12 curved shell elements give "
        "0.134 (-28%) where 48 give 0.183 (-1.1%). Read at "
        "https://manuals.dianafea.com/d107/en/1092500-1093515-nafems-benchmarks.html "
        "and https://manuals.dianafea.com/d105/Verify/Verifyse5.html on 2026-09-09."
    ),
```

---

## 6. What is still missing, and what actually blocks `run_le3`

**Nothing about the shape is missing.** The radius, the thickness, the absence of a hole, the
90° sector, the polar extent, the three named points, the two symmetry edges, the free equator,
the material, both loads, the boundary conditions, the target quantity, its location and its
value are all sourced above. The single INFERRED item is the mid-surface reading (§1.7).

The blocker of record on the `nafems-le3` case is `Blocker.NO_SHELL_SOLVER`, and it is the
right one: LE3 is a shell benchmark and its whole subject is shell bending. **Encode LE3 as a
shell, not as a solid.** A shell encoding takes the geometry above literally — the 10 m sphere
*is* the modelled surface and the 40 mm is a `ShellSection` property — and §1.7's inference
never has to be made. Three things to know when the shell lane reaches this case; none of them
is a sourcing gap:

1. **The load is a true nodal point force, and that is unusually convenient here.** A and C are
   single points, so the "consistent edge load" problem that `app/solve/shell_loads.py`
   documents for distributed edge tractions does not arise: the benchmark specifies a force at
   a node, which is what a nodal force vector holds exactly. The mesh only has to put a node on
   A and on C — which it will, since both are corners of the quarter model. Do **not** spread
   these over a patch; the reference solution is for point loads.
2. **Do not halve the loads for the symmetry planes.** §1.3 and §2.2: two independent
   quarter-model sources apply the full 2 kN. Halving is the plausible-looking mistake here and
   it would report roughly half the displacement.
3. **The 2% tolerance is tight, and deliberately so.** Two of the six published reproductions
   in §3 miss it (MSC Nastran at −3.2%, HOOPS Solve at −4.9%) and DIANA's coarse mesh misses it
   by 28%. That spread is the benchmark working as intended — membrane locking is an
   order-of-magnitude failure, as the existing `tolerance_reason` says. **Do not loosen it to
   make an element pass**; an element that needs 5% here is telling you something true. Expect
   to need a well-refined mesh of a locking-resistant element, and calibrate against the §3
   table rather than against the 185 mm alone.

Should anyone build LE3 as a **solid** instead, two further warnings apply, and they are why
the shell route is the one to take: a concentrated force on a 3-D elastic body has a genuine
displacement singularity, so `u_x` at the loaded node *diverges* under refinement rather than
converging on 185 mm (ESRD's note in §3 that the divergence is "extremely slow" is a property
of shell theory and does not carry over); and R/t = 250 means resolving a 40 mm wall over a
10 m quarter sphere, which is a mesh in the millions of elements against a `MAX_ELEMENTS`
ceiling of 400k. Both are estimated here from the sourced R and t, not published statements.

Genuinely absent from the record, none of which blocks anything:

1. **The original NAFEMS TNSB Rev. 3 (October 1990) document** was not obtained; it is not
   freely available. Everything above is from reproductions that cite it — although §1.1 is a
   *scan of the original figure*, which is as close as the other benchmarks in this catalogue
   get. `Target.basis` should stay `PUBLISHED` with the reproducing manual named, exactly as
   `abaqus-le1` and `abaqus-le11` do.
2. **No source states the mid-surface explicitly** (§1.7).
3. **No source consulted gives a full labelled point table.** Letters B, D, F and G appear in
   Altair's meshing sentence (`equally spaced nodes on edges AC, CE, EA, BG, DG, and FG`) and
   are never defined; they belong to a fuller figure that Altair does not reproduce. A, C and E
   — the only three that carry a load, a restraint or the target — are unambiguous in five
   sources. Select them by geometric selector, as this repo does anyway.

---

## 7. Sources that did not yield geometry

Recorded so nobody spends the time again.

- **The Abaqus Benchmarks Guide LE3 page**
  (`https://abaqus-docs.mit.edu/2017/English/SIMACAEBMKRefMap/simabmk-c-le3.htm`) — re-read in
  this task and confirmed: it carries the material, the boundary conditions, the loads and the
  target, and **states no dimension at all**. Its geometry is in a figure the page refers to
  (`The model is illustrated in the figure above`) and does not serve as text. This is what
  already sits in `SOURCES["abaqus-le3"]` and it is honestly described there.
- **FeenoX** — has no LE3 example. The `examples/` directory of `seamplex/feenox` was listed in
  full; it has `nafems-le1`, `nafems-le10` and `nafems-le11` and nothing hemispherical. FeenoX
  is a solid/thermal code without shell elements, which is presumably why. **The technique that
  worked for LE1 and LE11 does not work for LE3** — the substitute is §1.3.
- **The Abaqus v6.6 mirror at `classes.engineering.wustl.edu`** — TLS certificate chain does
  not verify; would not fetch.
- **SOLIDWORKS Simulation "Hemisphere Under Point Loads"**
  (`help.solidworks.com/2025/English/simconntutorial/r_hemisphere_point_loads.htm`) — returns
  only the help-portal shell; no technical content is served to a fetcher.
- **Karman Mechanics' code_aster LE3 article** (`karman-mechanics.hu`) — domain does not
  resolve (`ESERVFAIL`).
- **The PrePoMax forum thread on LE3** — carries the target (`ux= 185 mm at point A`) and the
  TNSB Rev. 3 citation, and discusses two attached `.inp` files, but states no dimension in its
  text and the attachments were not retrieved.
- **GitHub code search** for LE3 solver inputs returned essentially two things: the ImportMesh
  decks used in §1.3, and the "18° hole" claim below. There is no Code_Aster, CalculiX, FEBio
  or Kratos LE3 case that surfaced.
- **The "18° hole" claim — a different benchmark wearing LE3's name.** `lambdaclass/stabileo`
  (`engine/tests/BENCHMARK_REFERENCES.md`, and a matching comment in
  `engine/tests/validation/benchmarks/shell_benchmark.rs`, vendored again in
  `isaackogan/stabileo-engine`) says verbatim:

  > ### Hemisphere with 18° Hole (NAFEMS LE3)
  > - **Problem**: Hemisphere R=10, t=0.04, 18° hole at apex. Quarter model with symmetry.
  >   Diametral point loads F=2 at equator.
  > - **Material**: E=68.25, ν=0.3 (same as MacNeal-Harder hemisphere)
  > - **Value**: Same physics as pinched hemisphere (R/t=250) but avoids pole singularity,
  >   improving mesh quality.

  Its radius, thickness, material and target all match LE3; only the hole does not. Its own
  text gives the game away — it calls the variant "the same physics as" the pinched hemisphere
  and says the hole exists to avoid the pole singularity, which is the standard rationale for
  the *pinched-hemisphere* problem, not for LE3. **This is not a source and the 18° is not
  LE3's**, against four sources in §1.5–§1.6 including the original figure and a deck with a
  node at the pole. It is recorded because the claim is in public repositories and will be
  found again. The attribution to MacNeal & Harder (1985) was **not** independently verified in
  this task — the primary paper was not obtained, so nothing here asserts what that problem's
  geometry is, only that LE3's is not it.
