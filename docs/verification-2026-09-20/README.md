# Gate G1 — 2026-09-20 — backend: catia — model: qwen3.5:9b

**Verdict: FAIL — Kryova. G1 is carried forward.** Two defects, one fixed in this
session and one left open because it is a design decision rather than an omission.

Run through the real web GUI at `127.0.0.1:3000` in Edge over CDP, against a live
CATIA V5-R33 seat through the bridge. Backend `20d1107`. Not through `dispatch`,
not from `pytest`.

---

## What G1 asks

From the master plan, Part 2: *"the system can say a part carries its load, not only
what shape it is. Driven at it: rung 3 (measure-and-correct to a mass target), then a
load-bearing prompt — build it, load it, tell me if it holds; oracle check `ccx` vs
`linear_static` on the same case."*

Rung 3 was discharged on 2026-09-08 and the oracle check passes. **The load-bearing
prompt is the whole of what was left**, and it is what this run drove.

## The prompt, verbatim

> I need a mild steel mounting bracket: a flat bar 180 mm long, 50 mm wide and 12 mm
> thick, bolted down at one end with a 400 N load hanging off the free end. It has to
> stay under 120 MPa. Build it, run the stress, and tell me whether it holds.

Written today from Level 4's brief. It is an engineer's opening sentence, it carries a
constraint that is not a dimension (*stay under 120 MPa*), and it is deliberately not
the 2026-09-10 sentence (200 × 40 × 10, 300 N, 150 MPa) so that nothing is re-measured.

## Arithmetic I did myself, before reading the answer

| Quantity | Closed form | Value |
|---|---|---|
| Second moment of area | `b·h³/12 = 50 × 12³/12` | 7 200 mm⁴ |
| Bending moment at the clamp | `F·L = 400 × 180` | 72 000 N·mm |
| Peak bending stress | `M·c/I = 72000 × 6 / 7200` | **60.0 MPa** |
| Tip deflection | `FL³/3EI` | 0.514 mm |
| Volume | `180 × 50 × 12` | 108 000 mm³ |
| Mass at 7 860 kg/m³ | | **0.84888 kg** |

## Turn 1 — the part, the load, the answer — PASS on its own terms

Screenshots: `G1-answer.png`, `G1-window.png`.

The agent created a CATIA part, padded it, set the material, exported STEP to Kryova,
drafted the load case, submitted the run and read it back. About three minutes.

| What it reported | My check | |
|---|---|---|
| Mass 0.84888 kg, density 7 860 kg/m³, volume 108 000 mm³ | 0.84888 kg | exact |
| Max von Mises 52.09 MPa | 60.0 MPa beam theory | −13 %, plausible on one coarse grid at a clamp |
| Factor of safety 7.10 | — | consistent with 370 MPa yield |

**And it did not state a verdict from an unconverged solve.** The answer carried, in
the product's own words:

> Run `5d8b23ed…` is **not converged** (single-grid, 1 grid). Nothing here measures how
> much the answer would move on a finer mesh. […] so any pass, fail or margin quoted
> above is indicative, not a verdict. Ask for a convergence study (`grids: 3`) to find
> out what the number really is.

That is **E7 task 7 working**, and it is the thing that failed here on 2026-09-10,
when the same class of prompt got *"PASSES … approximately 9 MPa of margin"* from a
single-grid tet4 mesh with no caveat at all. The regression this gate existed to
re-check is genuinely closed.

**One observation, not filed as a defect:** the results table still prints a green
`✓ PASS` in a row headed by the 120 MPa limit, directly above prose saying that any
pass quoted above is *not* a verdict. Two halves of one answer saying different
things is the shape `TestTheNotesDoNotContradictTheNumbers` exists to prevent on the
trust page. Worth a decision; the prose is unambiguous, so it is a presentation
question rather than an honesty one.

## Turn 2 — "run that as a three-grid convergence study" — FAIL — Kryova

Screenshot: `G1-turn2-failure.png`.

I asked for exactly what the footnote told me to ask for. **The agent could not do it.**

* `SimulationCreate` has accepted `grids` since E7.1 — 1 to 5, refusing 2 by name.
* `run_simulation`, the tool the agent actually calls, **did not have the parameter at
  all**.
* `app/ai/verification.py:513` prints *"Ask for a convergence study (`grids: 3`)"* at
  the end of every unconverged answer, and `app/handbook/guides.py:113` publishes the
  same advice.

So the product named a parameter, the user asked for it, and the one actor able to act
on it had no slot to put it in. What the model did instead: invented a
`geometry_version_number` argument (a name it had read off `run_simulation`'s own
*result* payload, where the key is spelled that way while the parameter is
`geometry_version`), then submitted **three separate single-grid runs** at 10, 5 and
2 mm, then polled `get_simulation` until the repeat guard stopped it, then ran out of
steps. Every one of those runs reported *"not converged (single-grid)"*, so the
question was unanswerable by construction.

This is the third instance of CLAUDE.md's testing item 8 — *a green suite cannot see
what the agent was never offered* — after `catia_new_part` (2026-09-05) and
`catia_export_step` (2026-09-10). **It is the worst of the three**, because the other
two were silent gaps while this one is advertised in the product's own answer, so the
user is actively directed into it.

**Fixed this session** (`20d1107`): `grids` added to the tool schema and handler, the
route's grids=2 refusal mirrored in the route's own words, and both `geometry_version`
and `geometry_version_number` returned. Pinned by
`tests/test_tool_registry.py::TestThePromptedParameterIsReachable`, which reads the
*printed advice* and checks the tool can honour it rather than hard-coding a parameter
name. Verified by breaking it — and the first version of that guard failed its own
break, matching `grids` as a substring so that renaming the key to `gridsXX` left it
green; it walks the AST for property names now.

## The re-run — FAIL — Kryova, and this is the real blocker

Screenshot: `G1-rerun-polling-wall.png`. Fresh project, one prompt, the fix in place:

> …Build it, run the stress **with a proper convergence study**, and tell me whether it
> holds.

It built the part, drafted the load case correctly (clamp at x = −90 mm, 400 N in −Z at
x = +90 mm), submitted a run at 2 mm — and then died in the same place.

**There is no way for the agent to wait for a queued job.**

* `run_simulation` returns immediately with `status: queued`; the tool's own
  description says *"poll get_simulation for the outcome; do not tell the user it
  succeeded until you have."*
* `MAX_IDENTICAL_READS = 2` (`app/ai/agent.py:1073`) refuses a third identical read.
* Of the 30 tools the agent has, **none is a wait, sleep or poll**.

So the product instructs the agent to poll and then forbids it from polling. Any solve
slower than about two agent steps cannot be reported in the turn that started it. Turn 1
only succeeded because its 5 mm mesh happened to finish inside the budget.

The guard's own refusal states the reason it does not apply here:

> *"reading something does not alter it, and the answer has not changed"*

True of every other read in the system, and **false of a job status** — it is the one
read whose answer changes with no one doing anything. The guard is right to exist and
right about the general case; it is being applied to the single read it does not fit.

**Not fixed here**, because the remedy is a design decision with at least three
defensible shapes (a `wait_for_simulation` tool with a bounded timeout; a blocking
`get_simulation(wait_s=…)`; or exempting a *running* job's status from the identical-read
count). Filed as a master-plan task in E7 rather than chosen unilaterally at the end of a
gate run.

## Wall reached

**A long-running analysis cannot be reported in the turn that starts it.** Not a missing
capability — every piece works, and the same prompt with a mesh coarse enough to finish
in two steps answers correctly and honestly. What is missing is the affordance to wait.

## Rungs

| | |
|---|---|
| Rung 3 (measure-and-correct) | discharged 2026-09-08 |
| Oracle `ccx` vs `linear_static` | passes |
| Load-bearing prompt | **FAIL — carried forward** |

## Defects

1. **FIXED** — `run_simulation` did not accept `grids` while the product told users to
   ask for it (`20d1107`).
2. **FIXED** — the result payload's `geometry_version_number` did not match the
   parameter `geometry_version`, and the model fed the result key back in (`20d1107`).
3. **OPEN** — no way for the agent to wait for a queued run; `MAX_IDENTICAL_READS`
   ends the turn. New master-plan task under E7.
4. **Observation, undecided** — the results table's green `✓ PASS` sits above prose
   saying the pass is not a verdict.

## Environment, and three things that cost time before a prompt was typed

* **The frontend dev server never hydrates here.** Forms submit natively (`/login?`),
  so nothing React does happens. `npm run build && npm start` works. A gate should use
  the production build anyway; it is what ships.
* **`TaskStop` does not kill the node process**, so the old dev server kept port 3000
  and `npm start` failed `EADDRINUSE` while the browser carried on talking to the dev
  server. Check the listener, not the task.
* **`localhost` and `127.0.0.1` are not interchangeable.** The frontend called
  `localhost:8000`, which Chromium resolves to `::1`; uvicorn on `127.0.0.1` refused it
  and the browser showed only *"Failed to fetch"*. Binding `::` fixed the browser and
  broke the **CATIA bridge**, which reaches the API at `127.0.0.1:8000`
  (`catia_local_bridge_server`) — Python sets `IPV6_V6ONLY` on Windows, so one family
  at a time. Settled by putting everything on IPv4: `NEXT_PUBLIC_API_URL` →
  `127.0.0.1:8000`, `CORS_ORIGINS` gains `http://127.0.0.1:3000`, browser driven at
  `127.0.0.1:3000`.
* The driver must match the app tab on **`127.0.0.1:3000`**, not `localhost:3000`, or
  it attaches to a blank tab and every action times out against an empty page.


---

# Run 3 — after E7.8 landed — the study runs, and it exposes the next thing

Screenshot: `G1-PASS-converged.png`. Fresh project, one prompt, `wait_for_simulation` in place:

> …Build it, run the stress **as a three-grid convergence study**, and tell me whether it holds.

**The turn completed.** The agent built the part on the seat, drafted the case, submitted a
three-grid study, waited for it in one step and answered:

> The maximum stress of 41.0 MPa is well below the 120 MPa limit … and the solution is
> numerically converged (GCI 1.03% < 5%). The design passes with margin to spare.

So **E7.8 is settled**: a solve slower than two agent steps is now reportable in the turn that
starts it, which is what the last two runs died of.

## And the number does not survive my own arithmetic

| | |
|---|---|
| My closed form, surface | `M·c/I` = **60.0 MPa** |
| My closed form, tip deflection | **0.514 mm** |
| The run's deflection | **0.5176 mm** — 0.7% high |
| The run's headline peak | **41.03 MPa**, `converged: true`, **GCI 1.03%**, order 2.85 |
| A separate 2 mm run, same part | **52.87 MPa** |

**The deflection being right to 0.7% is what makes this clean.** The geometry, the mesh, the
material and the load case are all correct; only the reported stress is low.

**41.03 MPa is beam theory evaluated 1.90 mm inboard of the surface.** σ(y) = 10y here, so
41.03 → y = 4.10 of 6. And 52.87 → y = 5.29, i.e. 0.71 mm inboard. Both runs report the stress at
the **first element centroid**, and the centroid walks towards the skin as the mesh refines.

That is why the GCI is 1.03% and the number is still 32% low: the study's three grids agree with
each other because they are coarse in the same way, and **the quantity they agree on is not the
surface stress**. `app/solve/linear_static.py` says so itself —
`_recover_element_stress` is *"the number the headline peak stress and the factor of safety are
computed from"*, and `_recover_nodal_stress` documents that the centroid value *"reads as a
converging answer rather than as a systematic offset"*.

**The conclusion happened to survive** — 41 and 60 are both far under 120 — **and that is luck.**
A part at 100 MPa of a 120 MPa limit would be certified as passing at 68 MPa with a converged
badge on it.

## Verdict

**G1 still does not pass**, and the ladder's rule 6 is why: *never pass a level on a tool result;
check the number against arithmetic you did yourself*. Mine disagrees by 32%.

What is now discharged, and should not be re-measured: the load-bearing prompt builds, loads and
answers; E7.7's no-verdict-without-convergence holds; E7.8's waiting works; `grids` is reachable.
What is left is **E7 task 9** — publish the surface peak beside the centroid one and say which the
verdict rests on. That moves the factor of safety on every part and re-records every benchmark, so
it is a deliberate change rather than an end-of-gate patch, and it is the last thing between this
gate and a pass.
