# EN 1993-1-9:2005 (fatigue of steel) — reading record for master plan E8.3 and E8.5

**Status: Tables 8.3–8.5, B.1, 3.1, §7.2.1 and §8 encoded on 2026-09-15** (master plan E8.3), in
`app/fatigue/weld_catalogue.py` and `app/fatigue/eurocode3.py`, with the shear curve in
`app/fatigue/material.py`. Every row was re-read on its page image before it reached code. Written
first on 2026-09-14, so the session that built the catalogue did not have to find the standard
again. The hot-spot method (E8.5) has not used it yet.

**Three things the re-reading found, which the notes below did not say:**

- **Read a figure's labels at 400 dpi.** At 110 dpi Figure 7.1's top curve label reads "180". At
  400 dpi it is 160, which is the value the standard defines. A test pins the set.
- **Table 8.5 detail 1 has a gap.** Its rows are "ℓ<50 mm" and "50<ℓ≤80", so ℓ = 50 mm is in
  neither. The catalogue returns both rows, flagged `on_boundary`, instead of choosing one.
- **Some rows overlap, and some joints are in no row.** Table 8.4 detail 4 gives 90 for
  "r/ℓ ≥ 1/3 or r>150mm" and 71 for "1/6 ≤ r/ℓ ≤ 1/3", so r/ℓ = 1/3, or r > 150 with a small r/ℓ,
  is both. Table 8.3 requires NDT for every two-sided plate splice, so one without NDT is in no row.
  Transverse attachments (Table 8.4 details 6–8) stop at ℓ = 80 mm.

§7.2.1 is on page 17 (PDF page 1042), not page 18; Figure 7.4 is on page 18. §7.1(4) and (5) on page
17 say which tables are for nominal stress and that Annex B is for geometric stress.

**These are notes, not quotations, except where quotation marks say otherwise.** The PDF's text layer
is a poor OCR (`,6,Gc` for Δσc), so the tables were read from page images rendered at high
resolution. **Before a category, a limit or a factor reaches code, re-read that row on the page named
here and quote it.** The page map makes that a two-minute job. A number recalled from this file
instead of the page is exactly the failure `app/verify/` exists to prevent.

**BS 7608 was not read and is not held.** The master plan's E8.3 names BS 7608 alongside Eurocode 3.
Nothing in this repository encodes BS 7608, and nothing may claim it until someone reads it.

## Source

BS EN 1993-1-9:2005, incorporating corrigendum AC2, in the Public.Resource.Org compilation "Eurocode
3: Design of steel structures" ("Published under the rule of law"). Read 2026-09-14 from
`https://gaprojekt.com/wp-content/uploads/2021/11/Eurocode-3-Design-Of-Steel-Structures.pdf`.
A university copy (`library.um.edu.mo`) failed its TLS certificate check and was not used.

| PDF page | Standard page | Holds |
|---|---|---|
| 1036 | 11 | Table 3.1, γMf |
| 1039 | 14 | §7.1 (1)–(2) |
| 1040 | 15 | Figure 7.1, §7.1 (3) |
| 1041 | 16 | Figure 7.2, NOTES 1–3 |
| 1042 | 17 | Figure 7.3, §7.1(4)–(5), §7.2.1 |
| 1043 | 18 | Figure 7.4, §7.2.2, §8 |
| 1047–1048 | 22–23 | Table 8.3, transverse butt welds |
| 1049 | 24 | Table 8.4, weld attachments and stiffeners |
| 1050 | 25 | Table 8.5, load-carrying welded joints |
| 1058 | 33 | Annex B, Table B.1, hot-spot categories |

Render a page with `pdftoppm -f <pdf page> -l <pdf page> -r 200 -png <pdf> page`.

## §7 — the curves

- **7.1(1)**: the category number is the reference fatigue strength at 2 million cycles, in N/mm².
- **7.1(2)**, direct stress: Δσ_R^m·N_R = Δσ_C^m·2·10⁶ with m = 3 for N ≤ 5·10⁶. Constant-amplitude
  limit Δσ_D = (2/5)^(1/3)·Δσ_C ≈ 0.737·Δσ_C. **Shear**: Δτ_R^m·N_R = Δτ_C^m·2·10⁶ with **m = 5 for
  N ≤ 10⁸**, and cut-off Δτ_L = (2/100)^(1/5)·Δτ_C ≈ 0.457·Δτ_C. Encoded as `ShearDetail`.
- **7.1(3)**, direct stress under a spectrum that crosses Δσ_D: m = 3 to 5·10⁶, then
  Δσ_R^m·N_R = Δσ_D^m·5·10⁶ with m = 5 from 5·10⁶ to 10⁸, and cut-off
  Δσ_L = (5/100)^(1/5)·Δσ_D ≈ 0.549·Δσ_D.
- **Figure 7.1**, direct-stress categories: 160, 140, 125, 112, 100, 90, 80, 71, 63, 56, 50, 45, 40,
  36. **Figure 7.2**, shear categories: 100 and 80. Catalogue rows are checked against these sets.
  `WeldDetail` still accepts any positive number, because §7.1(5)'s NOTE lets a National Annex give
  categories for details the tables do not cover.
- **NOTE 1** (p. 16), quoted: Δσc for a detail determined from tests was calculated "for a 75%
  confidence level of 95% probability of survival for log N, taking into account the standard
  deviation and the sample size and residual stress effects", with not fewer than 10 data points,
  per annex D of EN 1990. **Encoded** as a failure probability of 0.05. The code said 0.025 until
  2026-09-14.
- **NOTE 3**: details marked with an asterisk sit one category lower than their 2·10⁶ strength would
  put them. The alternative raises them one category, provided Δσ_D is taken as the strength at 10⁷
  cycles with m = 3 (Figure 7.3).
- **7.2.1**: a non-welded or stress-relieved welded detail may count its compressive portion at 60%,
  so Δσ = |σ_max| + 0.6·|σ_min| (Figure 7.4). An as-welded detail may not.
- **7.2.2**, eq. (7.1): Δσ_C,red = k_s·Δσ_C, with the size factor k_s given per detail in Tables
  8.1–8.10.

## §8 and Table 3.1 — the verification

- **8(1)**: Δσ ≤ 1.5·f_y for direct stress and Δτ ≤ 1.5·f_y/√3 for shear, under the frequent load
  ψ₁·Q_k.
- **8(2)**: γ_Ff·Δσ_E,2 / (Δσ_C/γ_Mf) ≤ 1.0, and the same for τ.
- **8(3)**, eq. (8.3): (γ_Ff·Δσ_E,2/(Δσ_C/γ_Mf))³ + (γ_Ff·Δτ_E,2/(Δτ_C/γ_Mf))⁵ ≤ 1.0, unless Tables
  8.8 or 8.9 say otherwise for the detail.
- **Table 3.1**, recommended γ_Mf: damage-tolerant 1.00 (low consequence) and 1.15 (high); safe-life
  1.15 (low) and 1.35 (high). Its NOTE lets the National Annex choose the method and the values, so
  these are recommendations and must be recorded as such.

## Table 8.3 — transverse butt welds (pp. 22–23)

Size effect for t > 25 mm on most rows: k_s = (25/t)^0.2.

| Category | Detail | Requirements, abridged |
|---|---|---|
| 112 | 1–4 | no backing bar; ground flush; run-on/off pieces removed; welded from both sides; NDT. Detail 3: rolled sections cut and welded |
| 90 | 5–7 | weld convexity ≤ 10% of weld width with a smooth transition; run-on/off removed; both sides; NDT; details 5 and 7 in the flat position |
| 90 | 8 | as detail 3 with cope holes; ground flush; rolled sections of equal dimensions with no tolerance differences |
| 80 | 9–11 | convexity ≤ 20%; not ground flush; run-on/off removed; both sides; NDT. Detail 10: convexity ≤ 10% |
| 63 | 12 | full cross-section butt weld of rolled sections without a cope hole; run-on/off; both sides |
| 36 | 13 | welded from one side only, without a backing strip |
| 71 | 13 | one side only, when full penetration is checked by appropriate NDT |
| 71 | 14–15 | with a backing strip; its fillet welds end ≥ 10 mm from the edges; tack welds inside the butt weld |
| 50 | 16 | on a permanent backing strip; its fillet ends < 10 mm from an edge, or good fit not guaranteed |
| 71 | 17 | different thicknesses without a transition, centrelines aligned; k_s = (25/t₁)^0.2 / (1 + 6e/t₁ · t₁^1.5/(t₁^1.5 + t₂^1.5)) |
| 40 | 18 | at intersecting flanges |

Detail 19, with a transition radius, is classified as Table 8.4 detail 4.

**What this shows for E8.3.** One geometry, a transverse butt weld, spans 36 to 112 depending on facts
no CAD model holds: which side it was welded from, whether it was ground, the backing strip, the NDT
applied, and the convexity achieved. Classifying from attributes is therefore sound only if every
attribute that separates two rows is an input with a source. Where an attribute is unknown, the
honest answer is the set of candidate rows, with the lowest named as the conservative choice, and
never a pick.

## Table 8.4 — attachments and stiffeners (p. 24)

- **Detail 1**, longitudinal attachment, by length L: ≤ 50 → 80; 50–80 → 71; 80–100 → 63; > 100 → 56.
  Applies when the attachment's thickness is less than its height; otherwise use Table 8.5 detail 5
  or 6.
- **Detail 2**, longitudinal attachment to a plate or tube, L > 100 and α < 45°: 71.
- **Detail 3**, gusset with a radius transition, r > 150, reinforced: 80.
- **Detail 4**, gusset welded to a plate or flange edge: r/l ≥ 1/3 or r > 150 → 90; 1/6 ≤ r/l ≤ 1/3
  → 71; r/l < 1/6 → 50. Smooth radius, ground.
- **Detail 5**, as welded, no radius: 40.
- **Details 6–8**, transverse attachments (welded to a plate, vertical stiffeners, diaphragms), by
  l: ≤ 50 → 80; 50–80 → 71. Ends ground to remove undercut.
- **Detail 9**, shear studs on the base material: 80.

## Table 8.5 — load-carrying welded joints (p. 25)

- **Detail 1**, cruciform and tee joints, toe failure, full-penetration butt and all partial
  penetration, by l and t: 80 for l < 50 (all t); 71 for 50–80; 63 for 80–100; 56 for 100–120;
  56 for l > 120 with t ≤ 20; 50 for 120 < l ≤ 200 with t > 20, or l > 200 with 20 < t ≤ 30;
  45 for 200 < l ≤ 300 with t > 30, or l > 300 with 30 < t ≤ 50; 40 for l > 300 with t > 50.
  Requirements: inspected free of discontinuities and misalignment outside EN 1090's tolerances; a
  modified nominal Δσ; a partial-penetration joint needs two assessments (root: 36* for Δσ_w and 80
  for Δτ_w; toe: Δσ in the load-carrying plate); misalignment of the load-carrying plates ≤ 15% of
  the intermediate plate's thickness.
- **Detail 3**, root failure, partial penetration or fillet: 36*.
- **Detail 5**, overlapped fillet lap joint: 45*.
- **Detail 8**, continuous fillet welds carrying shear flow between web and flange: 80, m = 5, with Δτ
  from the weld throat area.
- **Detail 11**, tube socket, 80% full-penetration butt: 71. **Detail 12**, tube socket, fillet: 40.

## Annex B, Table B.1 — hot-spot categories (p. 33)

| Category | Detail |
|---|---|
| 112 | full-penetration butt, ground flush both sides, NDT |
| 100 | full-penetration butt, not ground |
| 100 | cruciform, full-penetration K-butt, toe angle ≤ 60° |
| 100 | non-load-carrying fillet, toe angle ≤ 60° |
| 100 | bracket ends; ends of longitudinal stiffeners |
| 100 | cover plate ends |
| 90 | cruciform, load-carrying fillet, toe angle ≤ 60° |

NOTES to B.1: misalignment is not covered; initiation at the root is not covered; the toe angle is
to EN 1090. **For E8.5**: Annex B gives the categories to use with a hot-spot stress. The read-out
distances of the extrapolation are not in the pages above. They were not found in this reading and
must be sourced (IIW recommendations are the usual reference) before a hot-spot method claims any.
