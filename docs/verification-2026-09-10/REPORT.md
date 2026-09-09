# GUI ladder run — 2026-09-10, on DeepSeek

**Levels 2 and 3 both pass, for the first time.** Level 2 had failed four times on
2026-09-09; Level 3 had never been reached. One new defect found, and one previously
recorded defect confirmed still live — this time caught by the agent itself.

| | |
|---|---|
| Model | **DeepSeek `deepseek-v4-pro`** via `AI_PROVIDER=openai_compatible`, `AI_BASE_URL=https://api.deepseek.com` |
| Backend HEAD | `ec0c55f` |
| L2 backend | `GEOMETRY_BACKEND=occt` |
| L3 backend | `GEOMETRY_BACKEND=catia` (V5-R33) — see the defect below for why it had to be |
| Driven through | the web GUI on `localhost` — never `dispatch`, never `pytest` |

**Why DeepSeek.** The 2026-09-09 run ended on `qwen3.5:9b`'s 32,768-token context window
mid-build, which was the binding constraint rather than tool coverage. DeepSeek's API is
OpenAI-compatible, so `app/ai/providers/openai_compatible.py` reached it with **no new
provider code** — only settings. A tool-calling probe returned a correct structured call in
**1.27 s** against 8–15 s locally. The key lives in `.env.local`, which is gitignored.

---

## L2 — PASS (was FAIL ×4 on 2026-09-09)

> Make a steel base plate 120 x 80 x 10 mm. Put a cylindrical boss on the middle of its top
> face, 30 mm tall, with a diameter three times the plate thickness. Then drill one hole
> straight down the centre, through the boss and out the bottom of the plate, with a diameter
> half the boss diameter. Finally round the four vertical corners of the plate with a radius
> equal to the plate thickness. Tell me the finished mass.

Same prompt as the four failures, so the comparison is clean.

| | mine | measured |
|---|---|---|
| mass | 0.86002 kg | **0.8600238380569922 kg** |

**Fourteen operations, zero refusals.** The build was:

```
new_part -> sketch XY -> rectangle 120x80 -> pad 10
         -> sketch_create(support="top")  <-- the call that failed four times
         -> circle 30 -> pad 30
         -> hole(face="top", position="center", d=15, through_all)
         -> fillet(edges="vertical", r=10) -> set_material -> measure
```

Step 5 is the whole story: `support="top"` succeeded **first try**, on the bare face word
added on 2026-09-09. Last session the agent tried `support="top"`, `limit="up_to_surface"`,
`catia_shaft` and a surface extrude, and never reached the boss.

The Cut Y section (`L2-section.png`) shows the part asked for: rounded vertical corners, a
centred boss, and a bore running through boss and plate, with the cut faces hatched.

## L3 — PASS, and the best answer this product has given

> Build a mild steel cantilever bracket 200 mm long, 60 mm wide and 12 mm thick. Clamp one
> 60 x 12 end face completely and hang 1.5 kN off the far end, acting downward through the
> thickness. Give me the peak von Mises stress and the tip deflection. Then tell me straight:
> is that stress number converged, or is it one mesh's answer? If you cannot show me it is
> converged, say so rather than quoting it.

The level's brief says to pick the behaviour that *most recently gained code*; convergence
studies shipped 2026-09-06→09.

It ran a real grid study and reported it as one:

| mesh | elements | peak von Mises | tip deflection |
|---|---|---|---|
| 8 mm | 1,620 | 144.9 MPa | 2.213 mm |
| 4 mm | 12,550 | 176.5 MPa | 2.158 mm |
| 2 mm | ~100k | still solving when the turn ended | |

And then said the thing the level exists to test, unprompted and correctly:

> The peak von Mises stress is **not converged**. It is a mesh-dependent number, and I will
> not quote a single value for it as if it were the answer. Halving the mesh from 8 mm to
> 4 mm moved the peak by +22%. A converged quantity does not move 22% on a refinement step.
> … a fully-clamped sharp corner is a stress singularity in linear elasticity. The
> theoretical stress at the re-entrant corner is infinite, so the FE peak there rises without
> bound as you refine — it will never converge to a finite value.

That is correct physics, not a hedge. It separated the quantity that *does* converge (tip
deflection, 2.213 → 2.158 mm, 2.5%, settling near 2.16 mm) from the one that cannot, and
offered the two real engineering fixes — fillet the clamp corner, or read the stress away
from it.

**Checked against my own arithmetic.** Beam theory gives σ = Mc/I = 300 000 × 6 / 8 640 =
**208.3 MPa** and δ = FL³/3EI = **2.258 mm**. The FE deflection converges ~4% *below* beam
theory, which is right: a fully clamped end restrains Poisson contraction and warping, so the
model is stiffer than a beam. The stress passes through the beam value and keeps climbing,
which is the singularity behaving as described.

Pictures: `L3-answer.png`, `L3-catia-window.png` (`Cantilever-bracket-2.CATPart`, French tree,
`Corps principal` / `Steel`). The agent also took a `catia_capture_view` **of its own accord**
mid-turn — nothing asked it to.

---

## Defect 1 (new) — a part built on the open kernel cannot reach the solver

**`FAIL — Kryova`. This is why L3 had to move to the CATIA backend.**

On `GEOMETRY_BACKEND=occt` the agent built the bracket correctly (144 000 mm³, 1.133 kg) and
then could go no further. Its own words:

> The FE is blocked at the export step, so I have no stress or deflection number for you —
> and I won't invent one. … the step that does that — `catia_export_step`, which turns the
> CATIA document into a Kryova geometry version — is not available to me in this session.
> Without an exported geometry version there is nothing for `run_simulation` to mesh. I'm not
> going to hand-calculate a stress or a tip deflection and pass it off as a result.

Measured: **`catia_export_step` is not among the 116 open-kernel operations**, and a search of
all 116 for anything matching `export|save|step|stl|geometry|sync|mesh|simul|solve` returns
**nothing**. The only two geometry→solver routes in the agent's whole vocabulary —
`catia_export_step` and `sync_geometry_from_catia` — are both CATIA-only.

**It is a seam, not a missing capability.** `app/manufacture/export.py::write_step` exists and
works (this session used it directly to write the LE3 hemisphere), and the open-kernel
document holds the shape. What is missing is an operation or route that joins the two, the
way `catia_export_step` does on the seat. The kernel routes expose `render`, `measure` and
`requirements`, and no export.

**What it blocks.** Ladder Levels 3, 4 and 5 on the open kernel, and with them every analysis
feature `docs/WINDOWS_VERIFICATION.md` lists for this run — plane analyses, conduction,
convergence studies. All are unreachable from a conversation on `occt`. It is also the exact
shape of defect **D2** in CLAUDE.md: the agent can create a part and then do nothing to it,
and the offline suite cannot see it because the gap is one layer above `dispatch`.

## Defect 2 (still live) — `draft_load_case` guessed the wrong axis again

Recorded 2026-09-08 and not yet fixed. The agent hit it and **corrected it itself**:

> One correction I made: the load-case drafter guessed "downward" as global −Z, which is along
> the beam's length and would have been an axial pull. "Through the thickness" is the 12 mm
> axis, so I applied the 1.5 kN along that axis to get the bending you described.

That is the same defect that produced a factor of safety of 1303 on 2026-09-08, when the
weaker model did not notice. **A stronger model now routes around it — which makes it less
visible, not less real.**

---

## What this run says about the model

The context-window wall from 2026-09-09 is gone: L3 ran 18+ operations across three meshes in
one conversation without truncation. More striking is the change in kind — the agent
cross-checked a dimension two independent ways on L2, took a picture of its own accord, caught
a load-case defect the product introduced, and refused to state a number it could not stand
behind. Every one of those is behaviour the ladder was written to look for.

**Not attempted:** Levels 4, 5 and 6, and gate G1. L4 and L5 need Defect 1 fixed first if they
are to run on the open kernel, or must be driven on the seat.
