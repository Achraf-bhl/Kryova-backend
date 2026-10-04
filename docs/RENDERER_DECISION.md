# The renderer decision — scheduled, with its criteria written in advance

**Master plan P6 task 3.** Written 2026-09-16, before any of the measurements it describes.

That order is the whole point. "Should we adopt a rendering library?" is a question people
answer with a feeling — the hand-rolled path feels creaky, or a library feels heavy — and
the feeling arrives *after* someone has spent a week fighting a bug. Criteria written
afterwards get shaped by the week. So they are written here first, with a date, and the
decision on that date is a reading rather than an argument.

---

## 1. What is being decided

**Whether Kryova's viewer keeps its hand-written WebGL renderer or adopts a third-party
one** (three.js and babylon.js are the candidates; nothing else is in scope).

**What is not being decided here:**

- Whether to use WebGPU. That is a *follow-on* behind capability detection, on either
  path, and it is not an alternative to this question.
- Whether the streaming and LOD design is right. That is P6 task 2 and it is renderer-
  independent by construction — `scene-streaming.ts` contains no WebGL and would be
  carried across unchanged.
- Anything about the server side. Tessellation, GLB packaging and the display cache do
  not change with the client's renderer.

## 2. The default, and why it is the default

**The hand-rolled WebGL 2 path continues unless the criteria below are met.** Two reasons,
and only the second is load-bearing:

1. The frontend has **three runtime dependencies by doctrine** (`next`, `react`,
   `react-dom`), CI fails if the count changes, and a fourth is a named decision in the
   master plan. three.js is ~600 kB minified and pulls its own ecosystem behind it.
2. **More importantly: the viewer's hard problem is not drawing.** It is deciding what to
   fetch and at what detail (`scene-streaming.ts`), and no library helps with that. A
   library would replace the part that is already working and leave the part that is hard.

Adopting one is therefore a real cost with a narrow benefit, and the bar is set accordingly
high. That is a stance, and the criteria below are what would overturn it.

## 3. The decision date

**2027-03-16**, six months after P6's first viewer task landed (2026-09-15).

**Brought forward automatically** if either trigger fires, because waiting for a date when
the answer is already in is its own kind of mood:

- the measured frame time on the reference assembly (§4) exceeds the fail threshold (§5)
  on **two** successive months' measurements; or
- implementing a P6 task requires writing something a library provides outright and the
  estimate exceeds **two engineer-weeks** — skinned instancing, order-independent
  transparency and shadow mapping are the realistic candidates.

**Not brought forward by**: a bug, however annoying; a single bad measurement; a new
library release; or anybody's preference. Those are §7.

## 4. The reference assembly — and the fact that it does not exist

Every number below is measured on one assembly, because a threshold against an unnamed
scene is not a threshold.

**The plan names M5's stamping press.** It cannot be used, and that has to be said plainly
rather than discovered in March.

*Corrected 2026-09-17:* the sentence here used to read "M5 is not built and is blocked on
E13". **M5 landed on 2026-09-16 and M8 on 2026-09-17**, so that is no longer true — and it
changes nothing, which is the point worth keeping. M5 is **eleven** parts and M8 is
**eight** occurrences, against a two-thousand-part target. The largest assembly this
repository can produce is not three components any more; it is eleven. The gap was never
about which rung had landed, and a real machine of this *size* is still several eras away.

**The synthetic generator is therefore built and is the reference assembly**, as option 2
below always intended: `app/render/reference.py`, pinned by digest
`2c6d3f5c8d9d534ccbbe0aeb6d58f4ab`, held to every row of the table by
`tests/test_render_reference.py`. Measured: **2,000 occurrences, 120 distinct components,
99% instanced, 5 deep.**

A criterion that cannot be measured until an unrelated phase lands is a mood with a date
on it. So the reference assembly is defined **by its properties**, and there are two ways
to obtain one:

| Property | Value | Why this value |
|---|---|---|
| Occurrences | 2,000 | P6 task 2's own target. |
| Distinct components | 120 | A real machine reuses heavily; this is the ratio that makes instancing matter. |
| Instanced fraction | ≥ 90% of occurrences | Fasteners and standard parts dominate. Below this, instancing is untested. |
| Triangles, level 0, total | 8–12 M | What the display levels give for parts of this size. |
| Triangles after LOD at the default view | ≤ 1.5 M | What the renderer is actually asked to draw. |
| Deepest tree nesting | ≥ 4 | Exercises `subtree`, explode and hide/isolate. |

1. **Preferred: M5**, the day it exists. It is a real machine and its part mix is real.
2. **Until then: a synthetic assembly** matching the table, generated from
   `app.design.missions`' M6 by replication with varied transforms. It is a weaker
   artefact — its part mix is not a real machine's — and any measurement taken on it is
   **labelled synthetic in the run log**, because a threshold met on a synthetic scene and
   missed on a real one is exactly the confusion this document exists to prevent.

**Building the synthetic generator is a prerequisite of the decision, not part of it.** It
is recorded as QUEUE G1's first step; if March arrives and no reference assembly exists,
the honest outcome is §6's *"no decision"* and not a guess. **Done 2026-09-17** — so that
outcome is no longer the likely one, and what remains is the measuring.

**One row of the table this scene cannot meet, stated rather than fudged.** The parts are
**boxes**, and a box tessellates to twelve triangles at every deflection there is. So the
8–12 M triangle row is a statement about real parts with curvature, and a synthetic scene
of prisms reaches ~24,000 — three orders of magnitude under it. Widening the band, or
giving the generator curved parts chosen to hit the number, would both be fitting the
artefact to the threshold. The honest position is that **this scene measures ordering,
streaming, instancing and tree depth, and does not measure the triangle budget**; the
triangle rows wait for a real machine, and any run log that quotes them off this scene is
quoting the wrong scene.

## 5. The criteria, and the thresholds

Measured in the shipped Tauri build and in Edge, with the GPU in its default power profile
and no other application drawing.

**On what machine is undefined, and that is a second prerequisite.** P6 task 2 says "a
mid-range laptop" and the phase proof says "the reference laptop"; **no machine is named
anywhere in this repository** (checked 2026-09-16 — `docs/WINDOWS_VERIFICATION.md` names
none, and the only hardware this project has stated is the Windows workstation, which is
the opposite of mid-range: it has a discrete card and a CATIA seat). A frame-time threshold
without a machine is not a threshold, so before the first measurement:

> **Name the reference laptop** — make, GPU, driver version and screen resolution — in
> `docs/WINDOWS_VERIFICATION.md`, and measure on that one. Integrated graphics at
> 1920×1080 is the intent. A number taken on the workstation measures the workstation and
> answers a question nobody asked.

Recorded as the second prerequisite in QUEUE G1 alongside the reference assembly.

| # | What | Pass | Fail |
|---|---|---|---|
| C1 | First meaningful paint (structure and boxes visible) | ≤ 2.0 s | > 3.0 s |
| C2 | Frame time while orbiting, 95th percentile | ≤ 33 ms (30 fps) | > 50 ms (20 fps) |
| C3 | Frame time while orbiting, worst frame in 30 s | ≤ 100 ms | > 250 ms |
| C4 | Time to first frame after a section plane moves | ≤ 100 ms | > 400 ms |
| C5 | Peak GPU memory | ≤ 1.5 GB | > 3 GB |
| C6 | Time to highlight after a pick (P6.6's selection) | ≤ 50 ms | > 200 ms |

Between pass and fail is **marginal**: it means keep the hand-rolled path and re-measure at
the next date. Marginal is not a failure and is not a pass, and it is deliberately a wide
band — a renderer that is occasionally 35 ms is not a reason to take on a dependency.

**Each number is a median of five runs on a cold profile**, and the run log records all
five. A single run is not a measurement, and the spread is usually the interesting part.

## 6. What each outcome means

- **All six pass** → the hand-rolled path is kept, this document is re-dated, and the next
  review is twelve months out rather than six.
- **Any criterion fails, and profiling attributes it to the renderer** → adopt three.js
  behind the existing seam. This is the only outcome that adds a dependency, it is a named
  master-plan decision, and the frontend's dependency-count CI check is updated in the same
  change rather than after it.
- **Any criterion fails, and profiling attributes it to streaming, tessellation or the
  network** → fix that. A library would not have helped, and adopting one would have hidden
  the real cause behind a rewrite. **This is the most likely failure and the easiest to
  misread**, which is why C1 and C2 are separated: C1 is dominated by fetch and decode, C2
  by draw.
- **Marginal** → keep, re-measure in three months.
- **No reference assembly exists, or no reference laptop has been named** → **no
  decision**, recorded as such, and supplying the missing one becomes the next P6 task.
  Deciding without the measurement is the failure mode this whole document is built to
  prevent, and "we ran out of time" is not a reason to permit it once.

## 7. What is explicitly not a reason to adopt a library

Written down because each of these has ended this argument in somebody's favour before,
and none of them is a measurement:

- "Everyone uses three.js."
- A hard bug in the hand-rolled path. Fix the bug; a rewrite carries its own bugs and they
  are in code nobody here has read.
- A feature a library has that nothing has asked for.
- The renderer being unpleasant to work in. Real, and it is an argument for refactoring it.
- A benchmark from someone else's scene.
- A single measurement past a threshold. Every threshold above is two successive months or
  a median of five.

## 8. Where the measurements go

`docs/verification-<date>/renderer-<date>.md`, one row per criterion, with the five runs,
the hardware, the browser build, whether the assembly was real or synthetic, and the
profiler attribution for anything that failed. The run log is the artefact; this document is
only the rule.
