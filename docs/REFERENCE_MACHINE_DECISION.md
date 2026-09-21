# The reference machine — required, unnamed, and awaiting a product decision

**Master plan P6 task 2, and the re-decision half of task 3.** Written 2026-09-22, in the
shape `RENDERER_DECISION.md` established: the criteria first, so the reading on the day is
a reading rather than an argument.

**This document does not choose the machine.** Choosing it is a product decision about who
Kryova promises performance to, and that is not a decision code can take. What this
document does is make the choice *cheap to act on*: everything the measurement needs
exists, so the day a machine is named the six numbers can be taken without redesigning
anything.

---

## 1. The decision required

> **Reference machine required:** one named laptop of the class Kryova promises the viewer
> will be usable on — make, model, GPU, driver version and screen resolution, all recorded
> here. P6 task 2's own words are *"a mid-range laptop"*, and the phase proof says *"the
> reference laptop"*, definite article. **No machine is named anywhere in this repository**
> (checked 2026-09-16 and again 2026-09-22).
>
> **Current workstation excluded because: it has a discrete GPU.** It is the opposite of
> the machine the target is about. A frame-time measured here would be a number about this
> desk, published as a promise to a customer on integrated graphics.
>
> **Blocked until the machine is named:** P6 task 2's two numeric targets, and P6 task 3's
> *re-decision* criteria, which are expressed in those numbers.

**A frame-time threshold with no machine behind it is not a threshold.** That is the whole
reason this is a blocker rather than a to-do: the number 30 fps means nothing until
somebody says 30 fps *on what*, and any machine chosen after the measurement is chosen to
make the measurement pass.

---

## 2. What the machine must have

Stated as requirements rather than a product, because several machines satisfy them and
picking between those is the part that is somebody's call.

| Property | Requirement | Why |
|---|---|---|
| GPU | **Integrated** (Intel Iris Xe / AMD Radeon 780M class or similar) | P6.2 says mid-range laptop. A discrete card measures a different customer. |
| Resolution | **1920×1080**, at 100% scale | The fps target is per frame, and a frame is pixels. A 4K panel is a different measurement, and scaling changes it again. |
| RAM | 16 GB | A 2,000-part scene plus a browser. 8 GB would measure swapping. |
| Browser | One named build, pinned | A browser upgrade moves WebGL performance; the machine alone does not pin the number. |
| Driver | Version recorded | Same reason. GPU drivers move frame times materially. |
| Power | **Measured on mains, not battery** | Laptops throttle on battery. Unstated, this alone can move a result past the threshold. |

**One property deliberately not required:** a CATIA licence. The viewer is browser-side and
the reference machine is not a seat.

---

## 3. What will be measured on it

Two numbers, both from P6 task 2, both against the reference assembly that already exists.

1. **First meaningful paint of a 2,000-part machine, target < 2 s.**
2. **Interaction never below 30 fps**, under orbit.

**The scene to measure them on is built and pinned.** `app/render/reference.py` generates
the assembly `RENDERER_DECISION.md` §4 specifies — 2,000 occurrences, 120 distinct
components, 99% instanced, 5 deep — and `tests/test_render_reference.py` holds it to every
row, pinned by digest `2c6d3f5c8d9d534ccbbe0aeb6d58f4ab` so two runs a month apart compare.
Nothing in it is random: the varied transforms are arithmetic on the index, because a
random transform satisfies the wording and destroys the artefact.

**One row that scene cannot meet, recorded rather than fudged:** its parts are boxes, and a
box is twelve triangles at every deflection, so the 8–12 M triangle band is unreachable by
three orders of magnitude. This scene measures **ordering, streaming, instancing and tree
depth**. It does **not** measure the triangle budget, and a run log quoting that row off it
is quoting the wrong scene.

---

## 4. The open question this decision does not settle

P6 task 2 asks for the fps assertion **in CI**, and **CI has no GPU**. Naming the machine
does not answer that. Three shapes, none chosen here:

- the assertion runs on the reference machine on a schedule, not per commit;
- CI asserts only the parts that are machine-independent (ordering, request counts, byte
  budgets) and the frame-time assertion lives elsewhere;
- a GPU runner is provisioned, which is a cost decision.

This is recorded so the next reader does not take "name the machine" to have settled it.

---

## 5. What is *not* blocked by this — a correction

**It would be convenient to say all six P6 tasks wait on this decision. They do not**, and
the distinction matters because four of them are takeable now.

| Task | Blocked by the machine? | What actually remains |
|---|---|---|
| **P6.1** tessellation | **No** | Display rows outlive a deleted project's files; no assembly is persisted, so the scene dies with the conversation. Backend work. |
| **P6.2** streaming scene | **Yes** | The module and the reference assembly exist. Only the two numbers remain, and they need the machine. |
| **P6.3** renderer decision | **Partly** | The decision itself is *made* — keep the hand-rolled WebGL 2 path. Unbuilt: the WebGL 2 upgrade (instancing, attribute handling) and WebGPU capability detection. Only the *re-decision criteria* need the machine. |
| **P6.4** interactions | **No** | Logic exists on both sides; **no control exists** — no section UI, no explode animation, no tree gutter, no bookmark panel, and bookmarks persist nowhere. Frontend work. |
| **P6.5** results on geometry | **No** | `scalar-field.ts` exists and is tested; the viewer does not call it — no legend, no probe UI — and nothing routes thickness or damage to the frontend. Wiring. |
| **P6.6** one selection model | **No** | The server half is real (`propose.py`, the face partition, the route). **No surface calls any of it.** Frontend work. |

So the honest statement is: **one task is blocked by the machine, one is half-blocked, and
four are blocked by unbuilt frontend UI that this workstation can build.** Building a
legend or a tree gutter does not need the reference laptop; only *measuring frame times on
one* does.

---

## 6. When the machine is named

Nothing here needs redesigning. In order:

1. Record make, model, GPU, driver, resolution and browser build in §2's table.
2. Serve the reference assembly (`app/render/reference.py`) through the viewer at
   `?level=0|1|2` — `app/render/display.py` already serves the levels.
3. Take the two numbers with the browser's own profiler, on mains.
4. **Record them even if they miss.** The status line says the targets are unmeasured; the
   honest replacement is a measurement, not a removal.
5. Answer §4's CI question, or record that it is still open.
