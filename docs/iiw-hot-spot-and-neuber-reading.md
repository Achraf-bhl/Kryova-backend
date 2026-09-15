# IIW hot-spot extrapolation and Neuber's technical factor — reading record

Master plan E8.5, read 2026-09-15. The page map behind `app/fatigue/hotspot.py` and
`app/fatigue/notch.py`. Nothing in either module was recalled; this file says where each
number sits so a reviewer can open the same page.

## 1. IIW-1823-07 — the structural hot-spot stress

*Recommendations for fatigue design of welded joints and components*, A. Hobbacher,
IIW-1823-07 (ex XIII-2151r4-07/XV-1254r4-07), December 2008. Read from
<https://svv.cz/files/IIW182307FatigueRecomm20121017.pdf>.

**The equations are pictures with no text layer**, so every coefficient below was checked on a
300 dpi render of its page, not on extracted text.

| What | Where | Encoded as |
|---|---|---|
| Hot-spot types "a" (toe on plate surface) and "b" (toe at plate edge) | Table {2.2}-1, p. 21 | `HotSpotType` |
| Principal stress "within ±60°" of the perpendicular to the toe | p. 20 | the caller's choice, through `field.Scalar` |
| Misalignment modelled explicitly or applied as km, not in the extrapolation | p. 22 | named as not applied in every source |
| (2.7) σhs = 1.67·σ0.4t − 0.67·σ1.0t, fine mesh ≤ 0.4t | §2.2.3.4, p. 24 | `Method.FINE_LINEAR` |
| (2.8) σhs = 2.52·σ0.4t − 2.24·σ0.9t + 0.72·σ1.4t | §2.2.3.4, p. 24 | `Method.FINE_QUADRATIC` |
| (2.9) σhs = 1.50·σ0.5t − 0.50·σ1.5t, coarse mesh, element length t | §2.2.3.4, p. 24 | `Method.COARSE_LINEAR` |
| Wall-thickness correction required for type "a" by surface extrapolation | p. 24 | not applied, named |
| (2.10) σhs = 3·σ4mm − 3·σ8mm + σ12mm, fine mesh ≤ 4 mm | §2.2.3.4, p. 25 | `Method.EDGE_FINE_QUADRATIC` |
| (2.11) σhs = 1.5·σ5mm − 0.5·σ15mm, coarse mesh, element length 10 mm | §2.2.3.4, p. 25 | `Method.EDGE_COARSE_LINEAR` |
| Type "b": "the stress distribution is not dependent on plate thickness"; correction n = 0.1 | p. 25 | positions in mm; correction not applied |
| Summary table of the rules | Table 2.2-2, p. 26 | cross-check of the five rows above |
| Strain gauges: "the stresses at the required positions can then be read from the fitted curve" | §2.2.3.5 | why a reading off its point must be interpolated by the caller |
| Thickness correction on the resistance side, equation (3.6) | §3.5.2, p. 77 | **not read as an image, not encoded** |

The weights applied are the exact Lagrange weights through each rule's reference points;
`tests/test_fatigue_notch.py` holds every one to the printed coefficient at the page's precision.

## 2. NACA TN 2805 — Neuber's technical factor

P. Kuhn and H. F. Hardrath, *An engineering method for estimating notch-size effect in fatigue
tests on steel*, NACA Technical Note 2805, October 1952. Read from
<https://ntrs.nasa.gov/api/citations/19930083528/downloads/19930083528.pdf>.

| What | Where | Encoded as |
|---|---|---|
| K_N = 1 + (K_T − 1) / (1 + π/(π − ω)·√(A/R)) | formula (1), p. 4 | `neuber_sensitivity`, `neuber_concentration` |
| ω, the flank angle | Figure 2 | `flank_angle_deg`, no default |
| √A against ultimate strength for steels, a plotted curve (inches, ksi) | Figure 3, p. 27 | **no value held**; the engineer reads it into `NeuberConstant.source` |
| The constant was "obtained by a trial-and-error process" | p. 7 | stated in the module docstring |
| ±10% for 69% of tests excluding radii ≤ 0.01 in, 56% with none excluded | pp. 8–9 | `NEUBER_ACCURACY`, repeated in every source |
| Scope: steel, "in the region of concern herein (that is, N = 10⁷)" | p. 9 | stated in the module docstring |

## 3. What was not read

- **FKM nonlinear (2019)** itself. The extended Neuber rule is as pyLife's `ExtendedNeuber`
  implements it, with pyLife's own citation of §2.5.7 eqs 2.5-45/46 (`EXTENDED_NEUBER`).
- **Peterson's** notch-sensitivity formula: no readable document was found, so it is not offered.
