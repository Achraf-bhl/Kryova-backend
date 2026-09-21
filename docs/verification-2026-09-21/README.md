# Gate G1 — 2026-09-21 — backend: catia — model: qwen3.5:9b

**Verdict: still FAIL, and closer than it has ever been.** Both defects yesterday's run
named are fixed and measured working. What stops it now is one step further in.

Backend `c8737d3`, live CATIA V5-R33 through the bridge, driven in the browser.
Screenshot: `G1-run5-surface-peak.png`.

## The prompt (unchanged from yesterday, deliberately)

> I need a mild steel mounting bracket: a flat bar 180 mm long, 50 mm wide and 12 mm
> thick, bolted down at one end with a 400 N load hanging off the free end. It has to
> stay under 120 MPa. Build it, run the stress as a three-grid convergence study, and
> tell me whether it holds.

## What is now fixed and measured

**E7.8 — the agent can wait for its own run.** The step list shows
*"Waiting for the analysis to finish — Done"*. The turn that died twice yesterday on
`MAX_IDENTICAL_READS` now completes.

**E7.9 — the headline reaches the surface.** Against my own `M·c/I` of **60.0 MPa**:

| | |
|---|---|
| Element centroid peak | 51.94 MPa (−13%) |
| **Surface (nodal) peak** | **59.78 MPa (−0.4%)** |
| Factor of safety, surface | 6.19 (against the centroid's flattering 7.12) |
| Deflection | 0.51697 mm against my 0.514 (+0.6%) |
| Mesh | 4 521 elements, tet10 |

Yesterday the same bar reported **41.03 MPa and called it converged**. The headline is now
within half a percent of closed form.

## What stopped it this time

**The study refused to certify, and was right to.** `converged: false`, with the reason:

> The observed order of convergence is 11.083, above the credible ceiling of 6.0.
> Differences that large between grids are being fitted as if they were discretisation
> error; the GCI computed from such an order collapses towards zero and would report
> confidence that is not there. Re-run with grids further apart.

That is the convergence machinery working — it declined a suspiciously perfect fit rather
than publishing a tiny GCI. Note that yesterday's run *did* report `converged` at GCI 1.03%
on the centroid quantity; a refusal here is the more trustworthy behaviour, not a regression.

**And it exposed the next gap, which is the direct consequence of E7.9.**
`quantities._max_von_mises` reads `result.max_von_mises_mpa` — the **element** value — so
the study converges on the centroid stress while the answer now quotes and judges the
**surface** one. *The evidence and the claim are about different numbers.* Filed as
**E7 task 10**; it is one line to change and its consequences are not, because a surface
peak moves with the position of a node and is noisier across remeshes.

**A model-quality observation, not filed as a product defect:** the answer opened
*"Verdict: … margin of more than 8x the allowable stress limit. No redesign needed."* — and
8× matches neither factor of safety (7.12 element, 6.19 surface). The footnote immediately
below correctly states the run is not converged and that no verdict may rest on it, so the
guard held; the prose above it did not. Same shape as the green `✓ PASS` chip noted
yesterday.

## One run was discarded under the ladder's own rule

The first attempt today failed differently: the model created sketch `plate_profile`, then
tried to create it again, was refused cleanly — *"two sketches with one name cannot be told
apart by any tool here. Draw into it with `catia_sketch_rectangle(sketch='plate_profile', …)`"*
— and repeated the identical call three times until it ran out of steps.

Checked before blaming the model: each conversation has its **own** CATIA document
(`mounting_bracket.CATPart`, with `-3`/`-4`/`-6` suffixes on the older ones), so the binding
is correct and the part was not inherited dirty. That makes it `FAIL — model`, which the
ladder says to re-run once. The re-run is the one recorded above.

## Where G1 stands

| | |
|---|---|
| Rung 3 | discharged 2026-09-08 |
| Oracle `ccx` vs `linear_static` | passes |
| E7.7 — no verdict from an unconverged solve | **holds** |
| `grids` reachable by the agent | **fixed** `20d1107` |
| E7.8 — the agent can wait | **fixed** `ac4542b` |
| E7.9 — headline reaches the surface | **fixed** `c8737d3`, 0.4% from closed form |
| E7.10 — study and verdict assess the same number | **open** |
| Model states a verdict in prose above its own footnote | open, model-quality |

Two of the three things that stopped this gate are gone and the third is named. The next
run should be driven once E7.10 lands.
