# NAFEMS LE11 — "Solid cylinder/taper/sphere — temperature": sourcing record

**Status: geometry fully sourced.** Every dimension below was read off a document that is
cited, and every dimension is corroborated by at least two independent documents. One
number — the height of the spherical band — is printed inconsistently by the primary figure;
§6 settles it by arithmetic on the figure's own other annotations and says exactly what was
inferred and from what.

Written 2026-09-09 for the lane that encodes LE11 in `app/verify/nafems.py`. Nothing here is
recalled: if a number is not attributed to a document in §1–§4, it is not in this file.

The technique that worked is the LE1 one — when the reproducing manuals omit the geometry,
read a free solver's committed test input — but it worked twice over here, because ESRD's
StressCheck benchmarks guide reproduces the **original NAFEMS dimensioned figure** as an
image. That figure, not the solver inputs, is the primary source; the solver inputs are what
cross-check it.

---

## 1. The geometry

LE11 is a **hollow** body of revolution, a quarter model (90° sector). Read bottom to top,
its meridian is a spherical band, then a taper, then a straight cylinder. It is *not* solid
through the axis — the name "solid cylinder" distinguishes it from a shell benchmark, not
from a hollow one. The base annulus and the top annulus are both axially restrained (§3).

### 1.1 The primary source — the dimensioned figure

ESRD's benchmarks guide, page 32, carries the NAFEMS figure with every dimension. Rendered
from the PDF at 500 dpi and read directly; the annotations are, verbatim:

| Annotation on the figure | What it dimensions |
|---|---|
| `0.7071 m` | horizontal, axis → inner cylindrical surface |
| `0.2929 m` | horizontal, inner cylindrical surface → outer cylindrical surface |
| `1.0 m` (radius arrow from origin) | inner spherical surface |
| `1.4 m` (radius arrow from origin) | outer spherical surface |
| `45°` (angle from the horizontal axis) | to the inner sphere / inner cylinder junction |
| `0.700 m` | axial, base → top of the spherical band — **see §6, this one is rounded** |
| `0.345 m` | axial, lower half of the taper |
| `0.345 m` | axial, upper half of the taper |
| `0.400 m` | axial, height of the straight cylinder |
| `1.0 m` (bottom) | horizontal, axis → point **A** |
| `0.4 m` (bottom) | horizontal, point **A** → outer edge of the base |

Two consequences read straight off that table:

- outer cylinder radius = `0.7071 + 0.2929` = **1.0 m** exactly;
- outer radius at the base = `1.0 + 0.4` = **1.4 m**, which is the outer sphere radius, so
  the outer sphere meets the base plane at the base's outer edge.

The taper is dimensioned as two bands of `0.345 m`. There is no geometric feature at the
line between them — the outer profile is one straight segment across both. It is a mesh
partition line in the original figure; the taper's axial extent is **0.690 m**.

> **Axis convention.** ESRD's figure uses **y** as the axial direction
> (`Δθ = √(x²+z²) + y`, target `σ_y`). Abaqus, FeenoX and FeaTool use **z** as the axial
> direction (`Δθ = √(x²+y²) + z`, target `σ_zz`). Same body, relabelled. **Everything below
> this line uses the z-axial convention**, because that is the one the repo's existing
> `abaqus-le11` citation and the target quantity already use.

### 1.2 The cross-check — FeenoX's committed Gmsh input

`examples/nafems-le11.geo` from the FeenoX repository, verbatim (points and curves only;
the meshing directives are omitted as irrelevant to the shape):

```gmsh
// NAFEMS LE11 benchmark structured curved hexahedral mesh
SetFactory("OpenCASCADE");

Point(1) = {0, 0, 0};

Point(2) = {1.000, 0, 0};   // A
Point(3) = {1.400, 0, 0};   // B

Point(4) = {1.000*Sin(Pi/4), 0, 1.000*Sin(Pi/4)};
Point(5) = {Sqrt(1.400^2-(1.000*Sin(Pi/4))^2), 0, 1.000*Sin(Pi/4)};

Point(6) = {1.000*Sin(Pi/4), 0, 1.000*Sin(Pi/4)+.345+.345};
Point(7) = {1.000,           0, 1.000*Sin(Pi/4)+.345+.345};

Point(8) = {1.000*Sin(Pi/4), 0, 1.000*Sin(Pi/4)+.345+.345+.400};
Point(9) = {1.000,           0, 1.000*Sin(Pi/4)+.345+.345+.400};

Circle(1) = {2, 1, 4};
Circle(2) = {3, 1, 5};
Line(3) = {4, 6};
Line(4) = {6, 8};
Line(5) = {8, 9};
Line(6) = {9, 7};
Line(7) = {7, 5};
Line(8) = {3, 2};
Line(9) = {4, 5};
Line(10) = {6, 7};

Curve Loop(1) = {8, 1, 9, -2};
Plane Surface(1) = {1};

Curve Loop(2) = {7, -9, 3, 10};
Plane Surface(2) = {2};

Curve Loop(3) = {6, -10, 4, 5};
Plane Surface(3) = {3};

Extrude {{0, 0, 1}, {0, 0, 0}, Pi/2} {
  Surface{1}; Surface{2}; Surface{3}; Layers{12/Mesh.MeshSizeFactor}; Recombine;
}

Physical Surface("xz") = {3, 2, 1};
Physical Surface("yz") = {8, 12, 16};
Physical Surface("xy") = {4};
Physical Surface("HIH'I'") = {15};

Physical Volume("bulk") = {1, 2, 3};
```

This is a *construction*, so it fixes things the figure leaves to be worked out: the
90° revolve about z, the two arcs sharing the origin as centre, the taper as one straight
segment, and the fact that the sphere/taper junction and the sphere/cylinder junction lie at
the **same** height. It also names `Point(2) = {1.000, 0, 0}` as `A` and `Point(3)` as `B`.

### 1.3 The second cross-check — FeaTool's script

FeaTool's "Temperature Loading of a Tapered Cylinder" builds the same body in a meridian
workplane, from a rectangle, two circles and a polygon, then revolves 90°. Its numbers (its
axial axis is `-y`; signs flipped here to the z-up convention):

- rectangle `x ∈ [0.7071, 1.4]`, axial `∈ [0, 1.79]`
- inner circle centre `(0,0)` radius `1`; outer circle centre `(0,0)` radius `1.4`
- polygon `(0.7071, 0.7)`, `(1.2124, 0.7)`, `(1, 1.39)`, `(1, 1.79)`, `(0.7071, 1.79)`
- revolution: 90° about the axis through the origin

### 1.4 The meridian, resolved — build from this

`(r, z)` in **metres**, z axial, revolve 90° about z. Provenance per point in the last
column; "derived" means arithmetic on cited numbers, shown.

| Pt | r | z | Where it comes from |
|---|---|---|---|
| **A** | 1.000000 | 0.000000 | figure `1.0 m` bottom dim; FeenoX `Point(2) // A` |
| **B** | 1.400000 | 0.000000 | figure `1.0 + 0.4`; FeenoX `Point(3)` |
| **C** | 0.707107 | 0.707107 | inner sphere/cylinder junction — see §6 |
| **D** | 1.208305 | 0.707107 | derived: `√(1.4² − 0.707107²)`; FeenoX `Point(5)` |
| **E** | 0.707107 | 1.397107 | derived: `z_C + 0.345 + 0.345`; FeenoX `Point(6)` |
| **F** | 1.000000 | 1.397107 | figure `0.7071+0.2929`; FeenoX `Point(7)` |
| **G** | 0.707107 | 1.797107 | derived: `z_E + 0.400`; FeenoX `Point(8)` |
| **H** | 1.000000 | 1.797107 | FeenoX `Point(9)` |

Edges of the meridian face:

| Edge | Kind | Source |
|---|---|---|
| A → C | circular arc, centre `(0,0)`, radius **1.0** | figure `1.0 m` arrow; FeenoX `Circle(1) = {2,1,4}` |
| B → D | circular arc, centre `(0,0)`, radius **1.4** | figure `1.4 m` arrow; FeenoX `Circle(2) = {3,1,5}` |
| C → G | straight, r = 0.707107 (inner cylindrical surface) | figure `0.7071 m`; FeenoX `Line(3)+Line(4)` |
| D → F | straight (**the taper**) | figure; FeenoX `Line(7) = {7,5}` |
| F → H | straight, r = 1.0 (outer cylindrical surface) | figure `0.7071+0.2929`; FeenoX `Line(6)` |
| A → B | straight (base annulus, z = 0) | figure; FeenoX `Line(8)` |
| G → H | straight (top annulus) | FeenoX `Line(5)` |

The taper is **not** tangent to the outer sphere — checked: the perpendicular distance from
the origin to the line D→F is 1.361 m, not 1.400 m. D is a corner, and the outer profile has
a slope discontinuity there. Do not "improve" the model by filleting or by making the taper
tangent.

FeenoX splits C→G at E and the meridian face into three surfaces purely to get a structured
hex mesh. The solid is one body; the split is not geometry.

---

## 2. Material and temperature field

| Quantity | Value | Sources |
|---|---|---|
| Young's modulus | 210 GPa = 210 000 MPa | ESRD; Abaqus; Altair `210 x 10³ MPa`; FeenoX `E = 210e3*1e6` Pa |
| Poisson's ratio | 0.3 | ESRD; Abaqus; Altair; FeenoX |
| Coefficient of thermal expansion | 2.3 × 10⁻⁴ /°C | ESRD; Abaqus; Altair; FeenoX `alpha = 2.3e-4` |

**Temperature field** (z-axial convention), with `x, y, z` in **metres** and Δθ in **°C**:

```
Δθ(x, y, z) = sqrt(x² + y²) + z
```

Sources: FeenoX `T(x,y,z) = sqrt(x^2 + y^2) + z`; Altair `T°C = (x2 + y2)1/2 + z`; ESRD's
figure `Δθ = √(x² + z²) + y` (their axial is y — same field). The Abaqus page renders it as
`Δθ = (x2+y2)+z`, which is that page's HTML losing the radical: three of the four sources
show the square root and the fourth is the one with the mangled markup, so the square root is
not in doubt.

It is a *temperature change*, not an absolute temperature — every source writes it as `Δθ`
or as a gradient applied to an unstressed body, so the stress-free reference temperature is
**0** and the field is the ΔT directly. No separate reference temperature is specified by any
source consulted.

> **The field is written in metres, and this repo is mm-N-MPa.** Building the part in mm and
> using the formula unchanged gives temperatures 1000× too large and therefore stresses 1000×
> too large. With coordinates in mm the same physical field is
> `Δθ = (sqrt(x² + y²) + z) / 1000`. This is arithmetic on the cited formula, not a new
> source. Flagging it because "nothing in the codebase converts" makes it the most likely way
> to encode this benchmark wrongly and get a plausible number.

---

## 3. Boundary conditions

Quarter model; the two cut planes are symmetry planes, and **both** the base annulus and the
top annulus are restrained axially.

| Constraint | Face | Sources |
|---|---|---|
| `u_x = 0` | plane x = 0 (symmetry) | Abaqus; ESRD; FeenoX `BC yz u=0` |
| `u_y = 0` | plane y = 0 (symmetry) | Abaqus; ESRD; FeenoX `BC xz v=0` |
| `u_z = 0` | plane z = 0 — the **base annulus**, r ∈ [1.0, 1.4] | Abaqus; ESRD; FeenoX `BC xy w=0` |
| `u_z = 0` | the **top annulus**, r ∈ [0.7071, 1.0] | Abaqus `face HIH′I′`; ESRD `face BCDE`; FeenoX `BC HIH'I' w=0` |

The top face is named `HIH'I'` by Abaqus and FeenoX and `BCDE` by ESRD — a labelling
difference between reproductions, not a different face. ESRD's 3-D view marks B and C on the
outer top rim and E and D on the inner top rim with a `90 degrees` arc between them, which
identifies it unambiguously as the top annulus of the straight cylinder.

Nothing else is restrained and there is no mechanical load. The entire loading is the
temperature field of §2.

---

## 4. The target

| | |
|---|---|
| Quantity | direct stress in the **axial** direction, `σ_zz` (ESRD's `σ_y`, same component) |
| Point | **A**, the lower inside corner: `(r, z) = (1.0, 0)` m, i.e. `(x,y,z) = (1.0, 0, 0)` m |
| Value | **−105 MPa** |
| Basis | NAFEMS reference solution, TNSB Rev. 3, October 1990 |

Sources: Abaqus `Target solution: Direct stress, σzz = −105 MPa at point A`; ESRD
`Direct stress in y-direction at point A is -105 MPa`; Altair target `-105 MPa`; bconverged
`The target vertical component of stress at the lower inside corner is: -105 MPa`; FeenoX
`PRINTF "sigma_z(A) = %.2f MPa" sigmaz(1,0,0)/1e6`.

A is on the inner spherical surface where it meets the base plane. It is a corner of the
domain, so the stress there is mesh-sensitive — which is the whole point of the benchmark.
Published reproductions, for calibration of what a converged answer looks like:

| Source | Result | vs −105 MPa |
|---|---|---|
| FeenoX (structured curved hex27) | −105.04 MPa | +0.04% |
| ESRD StressCheck, minimum hexa (8 elements) | −105.2 MPa | 0.19% |
| ESRD StressCheck, dense hexa (216 elements) | −105.4 MPa | 0.38% |
| ESRD StressCheck, minimum tetra (317 elements) | −105.5 MPa | 0.48% |
| ESRD StressCheck, dense tetra (3531 elements) | −105.4 MPa | 0.38% |
| Abaqus C3D20, fine mesh | −103.26 MPa | −1.7% |
| Abaqus C3D20R, fine mesh | −99.60 MPa | −5.1% |

Note what that table says about tolerance: the *p*-version and curved-hex results sit within
half a percent, and Abaqus's quadratic bricks are 1.7% low on a fine mesh. A convergence
gate tighter than about 2% will fail on element quality rather than on correctness.

---

## 5. Drafted `SOURCES` entries

In the register of the existing entries in `app/verify/nafems.py`. `abaqus-le11` is already
present and is unchanged — it covers the material, the boundary conditions and the target,
and it is honestly described there; it simply does not carry the geometry.

```python
    "esrd-le11-geometry": (
        "ESRD 'Benchmarks Guide — The Standard NAFEMS Benchmarks: Linear Elastic Tests' "
        "(2018), section 'NAFEMS LE11: Solid Cylinder/Taper/Sphere - Temperature Loading', "
        "page 32, reproducing NAFEMS publication TNSB Rev. 3, 'The Standard NAFEMS "
        "Benchmarks', October 1990. The primary geometry source: it reprints the original "
        "dimensioned figure, which the other reproducing manuals omit — inner sphere radius "
        "1.0 m, outer sphere radius 1.4 m, inner cylinder radius 0.7071 m, outer cylinder "
        "radius 0.7071 + 0.2929 = 1.0 m, 45 deg to the inner sphere/cylinder junction, "
        "axial bands 0.700 (see note) + 0.345 + 0.345 + 0.400 m, point A at radius 1.0 m on "
        "the base plane with 0.4 m from A to the outer edge. Read at "
        "https://www.esrd.com/wp-content/uploads/dlm_uploads/"
        "Benchmarks-Guide-Standard-NAFEMS-Benchmarks-Linear-Elastic-Tests.pdf "
        "on 2026-09-09."
    ),
    "feenox-le11-geometry": (
        "FeenoX (Seamplex, GPL-3.0) `examples/nafems-le11.geo`, the Gmsh input for its "
        "NAFEMS LE11 verification case, which fixes what the dimensioned figure leaves "
        "implicit: a 90 deg revolve about z, both arcs centred on the origin, the taper as "
        "one straight segment, and the spherical band's height as 1.000*Sin(Pi/4) rather "
        "than the figure's rounded 0.700 m. Names Point(2) = {1.000, 0, 0} as A. Read at "
        "https://raw.githubusercontent.com/seamplex/feenox/main/examples/nafems-le11.geo "
        "on 2026-09-09."
    ),
    "feenox-le11-model": (
        "FeenoX (Seamplex, GPL-3.0) `examples/nafems-le11.fee`, the solver input for its "
        "NAFEMS LE11 verification case: T(x,y,z) = sqrt(x^2 + y^2) + z with the mesh in "
        "metres, E = 210e3 MPa, nu = 0.3, alpha = 2.3e-4 /degC, w = 0 on both the xy plane "
        "and the face HIH'I', u = 0 on yz, v = 0 on xz, and the target read as "
        "sigmaz(1,0,0). Reports sigma_z(A) = -105.04 MPa against the -105 MPa reference. "
        "Read at "
        "https://raw.githubusercontent.com/seamplex/feenox/main/examples/nafems-le11.fee "
        "and https://www.seamplex.com/feenox/examples/mechanical.html on 2026-09-09."
    ),
    "featool-le11-geometry": (
        "FEATool Multiphysics documentation, 'Temperature Loading of a Tapered Cylinder' "
        "(NAFEMS LE11), the independent geometry cross-check: meridian rectangle "
        "r in [0.7071, 1.4] over an axial extent of 1.79 m, circles of radius 1 and 1.4 "
        "centred on the origin, taper polygon through (0.7071, 0.7), (1.2124, 0.7), "
        "(1, 1.39), (1, 1.79), (0.7071, 1.79), revolved 90 deg; E = 210e9 Pa, nu = 0.3, "
        "alpha = 2.3e-4, T = sqrt(x^2+y^2)+z, reference -105e6 Pa. Read at "
        "https://www.featool.com/doc/Structural_Mechanics_07_temperature_loading1 "
        "on 2026-09-09."
    ),
    "altair-le11": (
        "Altair OptiStruct verification problem OS-V: 0070 'Solid Cylinder/Taper/Sphere - "
        "Temperature', reproducing NAFEMS publication TNSB Rev. 3, October 1990. Carries "
        "the material (210 x 10^3 MPa, 0.3, 2.3 x 10^-4 /degC), the temperature field "
        "T degC = (x2 + y2)1/2 + z and the -105 MPa target, but no dimensions. Read at "
        "https://2021.help.altair.com/2021/hwsolvers/os/topics/solvers/os/"
        "nafems_test_problem_le11_r.htm on 2026-09-09."
    ),
```

---

## 6. Cross-check — where documents agree, and the one place they do not

**Agreements.** Every dimension is carried by at least two documents that did not copy each
other, and they agree exactly:

| Dimension | ESRD figure | FeenoX `.geo` | FeaTool | Agree? |
|---|---|---|---|---|
| Inner sphere radius | `1.0 m` | `Circle(1)` through `{1.000,0,0}` | circle radius `1` | ✅ exact |
| Outer sphere radius | `1.4 m` | `Circle(2)` through `{1.400,0,0}` | circle radius `1.4` | ✅ exact |
| Inner cylinder radius | `0.7071 m` | `1.000*Sin(Pi/4)` = 0.707107 | rectangle `xmin 0.7071` | ✅ exact |
| Outer cylinder radius | `0.7071+0.2929` = 1.0 | `Point(7)/(9)` x = `1.000` | polygon x = `1` | ✅ exact |
| Taper axial extent | `0.345 + 0.345` = 0.690 | `.345+.345` | `1.39 − 0.7` = 0.69 | ✅ exact |
| Cylinder axial extent | `0.400 m` | `.400` | `1.79 − 1.39` = 0.40 | ✅ exact |
| Radius of A | `1.0 m` | `Point(2) // A` = 1.000 | target read at `(-1,0,0)` | ✅ exact |
| Outer radius at base | `1.0 + 0.4` = 1.4 | `Point(3)` = 1.400 | rectangle `xmax 1.4` | ✅ exact |
| Sector angle | `90 degrees` on the 3-D view | `Extrude {... Pi/2}` | revolution `90°` | ✅ exact |

Material, temperature field, boundary conditions and target each have **four** independent
sources (§2–§4) and no disagreement, beyond the Abaqus page's HTML dropping the radical from
the temperature formula and Altair's table header saying `σ_zz` where its body text says
`σ_yy` — both are transcription artefacts of the reproducing page, resolved by the majority.

**The one disagreement — the height of the spherical band.**

| Source | Band height | Taper top | Total height | Outer sphere/taper junction radius |
|---|---|---|---|---|
| ESRD figure, as printed | 0.700 | 1.390 | 1.790 | — |
| FeaTool | 0.7 | 1.39 | 1.79 | 1.2124 |
| FeenoX `.geo` | 0.707107 | 1.397107 | 1.797107 | 1.208305 |

FeaTool is **not** an independent third vote here: `√(1.4² − 0.7²)` = 1.21244, which is
exactly its `1.2124`, so FeaTool is the figure's printed `0.700` carried through
consistently. FeenoX's `1.208305` is `√(1.4² − 0.707107²)`, the same construction from
`0.707107`. So this is one binary choice — is the band `0.700` or `1.0·cos 45°`? — and every
other difference follows from it.

**INFERRED: the band height is `1.0 · cos 45° = 0.707107 m`, and the figure's `0.700 m` is a
rounded annotation.** Inferred from the figure's *own* other annotations, not from memory:

- the figure gives the inner cylinder radius as `0.7071 m` (four decimals) and annotates the
  junction with `45°` on a sphere of radius `1.0 m`. A point at radius 0.7071 on a sphere of
  radius 1.0 is at height `√(1.0² − 0.7071²)` = 0.707107. It cannot be at 0.700.
- taking `0.700` literally instead makes the figure unclosable: the point
  `(r, z) = (0.7071, 0.700)` is at radius `√(0.7071² + 0.700²)` = 0.994983 m from the origin,
  5.0 mm off the 1.0 m sphere the same figure says it lies on. The `0.700` is the only
  annotation that has to give.
- FeenoX, implementing 0.707107, reports −105.04 MPa against the −105 MPa reference.

That last bullet is corroboration, not proof — ESRD's StressCheck, working from the printed
figure, lands at −105.2 to −105.5 MPa, so **the target does not discriminate between the two
readings**. The argument that settles it is the geometric one in the first two bullets. The
difference is 7.1 mm in 1797 mm (0.4%) and does not move point A, which sits on the base
plane at the other end of the body.

**Where there is only one source.** Two items, both minor:

1. The **split of the taper into two 0.345 m bands** appears only on the ESRD figure. FeenoX
   copies the arithmetic (`.345+.345`) rather than independently confirming a feature there,
   and FeaTool has no intermediate point. Treated above as a mesh partition with no geometric
   meaning, which is consistent with all three (none of them puts a slope change there).
2. That FeenoX's `Physical Surface("HIH'I'") = {15}` is the **top** annulus is read from the
   surface belonging to the extrusion of `Surface(3)`, the cylinder region — it is consistent
   with Abaqus and ESRD naming the same restraint on the top face, but the gmsh surface
   numbering itself was not independently verified. The boundary condition is not in doubt
   (three sources); only the gmsh tag number is uncorroborated, and the encoding lane will not
   use gmsh tags.

---

## 7. What is still missing

**Nothing needed to build, load and score the benchmark.** Specifically not missing: the
meridian profile, the sector angle, the material, the temperature field, all four boundary
conditions, the target quantity, its location and its value.

Three things are genuinely absent, and none of them blocks encoding:

1. **The original NAFEMS TNSB Rev. 3 (October 1990) document itself** was not obtained — it
   is not freely available. Everything above is from reproductions that cite it. This is the
   same footing as the four benchmarks already in the catalogue, and `Target.basis` for the
   −105 MPa figure should stay `PUBLISHED` with the reproducing manual named, exactly as
   `abaqus-le1` and the others do.
2. **No source states a stress-free reference temperature explicitly.** All four write the
   field as a temperature *change* (`Δθ`), so it is 0 by construction. Recorded as reasoning
   in §2 rather than as a citation, because it is not printed anywhere.
3. **The letters B…I labelling the faces are not consistently defined across reproductions**
   (Abaqus/FeenoX `HIH'I'`, ESRD `BCDE`), and no source consulted prints a full labelled
   vertex table. This is a naming gap only — the restrained faces are unambiguous (§3), and
   the encoding lane should select them by geometric selector, which is what this repo does
   anyway.

**Sources that did not yield geometry**, recorded so nobody spends the time again: the Abaqus
Benchmarks Guide LE11 page (re-read; text carries the material, BCs, temperature field and
target, and states no dimensions — the geometry is in a figure the page does not serve);
Altair OS-V: 0070 (material, field and target only, "no exact dimensions"); bconverged's
LE11 page (target only — its `le11_calculix.zip` and `le11_ansys.zip`, which would have
carried node coordinates, return HTTP 406 then 500 and could not be downloaded); MSC Nastran
2023.3 verification guide LE11 (page would not fetch); TechSoft3D HOOPS benchmark report PDF
(image-only, no extractable text); OnScale Solve validation page (domain no longer resolves —
its cached search summary was the source of the "reference temperature is 0 °C" claim, which
is *not* used above for that reason).
