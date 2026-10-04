# The reference machine — named 2026-09-22, and what that does and does not settle

**Master plan P6 task 2, and the re-decision half of task 3.** Written 2026-09-22, in the
shape `RENDERER_DECISION.md` established: the criteria first, so the reading on the day is
a reading rather than an argument.

**§1 and §2 were written before the machine was named and deliberately refused to name
it.** The user delegated that choice on 2026-09-22 — *"Rename the reference machine so P6.2
and P6.3 are unblocked and finished"* — reversing the instruction of the audit brief the
day before. The machine is named in **§1a**, by me, on that authority and on that date,
which is recorded here because "who chose the machine the promise rests on" is a question
somebody will ask.

---

## 1. The decision that was required

> **Reference machine required:** one named laptop of the class Kryova promises the viewer
> will be usable on — make, model, GPU, driver version and screen resolution, all recorded
> here. P6 task 2's own words are *"a mid-range laptop"*, and the phase proof says *"the
> reference laptop"*, definite article. **No machine was named anywhere in this repository**
> (checked 2026-09-16 and again 2026-09-22).
>
> **Blocked until the machine is named:** P6 task 2's two numeric targets, and P6 task 3's
> *re-decision* criteria, which are expressed in those numbers.

**A frame-time threshold with no machine behind it is not a threshold.** That is the whole
reason this was a blocker rather than a to-do: the number 30 fps means nothing until
somebody says 30 fps *on what*, and any machine chosen after the measurement is chosen to
make the measurement pass.

---

## 1a. The machine, named

| Property | Value |
|---|---|
| Make and model | **Lenovo Legion Pro 5 16ADR10** (type 83LT) |
| CPU | AMD Ryzen 9 8940HX |
| **GPU used for the measurement** | **AMD Radeon 610M** (integrated, driver 32.0.21030.13004) |
| GPU *not* used | NVIDIA GeForce RTX 5070 Laptop (driver 32.0.16.1692) |
| RAM | 31.3 GB |
| OS | Windows 11 Home, 10.0.26200 |
| Browser | Microsoft Edge **153.0.4234.32** |
| Resolution for the measurement | **1920×1080 at 100%** (panel is 2560×1600 — it must be set) |
| Power | Mains, not battery |

**This is the workstation this project is developed on, and §1 of the previous draft
excluded it for having a discrete GPU. That exclusion was wrong, and the correction is the
reason naming it is defensible at all:** the machine has *both* adapters, and the display
is driven by the **integrated Radeon 610M**. A browser pinned to that adapter is measuring
integrated graphics, which is what P6.2 is about.

### What it is an honest proxy for, and what it is not

**The two numbers are not equally trustworthy on this machine, and that has to be said
before either is taken.** Splitting them is the whole value of naming this machine rather
than pretending it is a mid-range laptop:

| P6.2 number | Bound by | On this machine |
|---|---|---|
| **Interaction never below 30 fps** under orbit | the GPU | **Representative.** The Radeon 610M is a 2-CU part, at or *below* the Iris Xe / Radeon 780M band §2 asks for. A frame time measured here is not flattering. |
| **First meaningful paint < 2 s** of a 2,000-part scene | CPU, memory and parse | **Optimistic.** A Ryzen 9 with 31 GB is far above the target class. A pass here says little about a 16 GB mid-range machine. |

So the reading rule, which applies to every run logged against this machine:

> **A miss is conclusive and a pass is not.** If the frame time or the paint misses its
> target here, it misses on the target class too. If the paint *passes* here, that is
> evidence about this machine and not about the promise, and it stays labelled that way
> until somebody runs it on a 16 GB machine.

That asymmetry is the same one `app/kernel/`'s sampled answers carry — an upper bound from
a finite ray set can prove a violation and cannot prove a pass — and it is recorded for the
same reason.

### What naming it does not do

**It does not make P6.2 measured.** The machine was the blocker; the measurement is the
task, and it has not been taken. A status line saying otherwise would be the exact failure
the audit brief of 2026-09-21 named: *"Do not claim completion based only on code
existing."* The five steps in §6 are what remain, and step 3 is the one that costs time.

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

**The machine named in §1a meets the GPU, resolution, browser, driver and power rows and
exceeds the RAM row.** It is kept here as the *target class* rather than rewritten to
match what was chosen, because a requirements table edited to fit the machine in hand
stops being a requirement.

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

## 6. What remains, now the machine is named

Nothing here needs redesigning. In order, with step 1 done on 2026-09-22:

1. ~~Record make, model, GPU, driver, resolution and browser build.~~ **Done — §1a.**
2. Serve the reference assembly (`app/render/reference.py`) through the viewer at
   `?level=0|1|2` — `app/render/display.py` already serves the levels.
3. Take the two numbers with the browser's own profiler, on mains, **with the display set
   to 1920×1080 and Edge pinned to the integrated adapter**. Pinning is the step that is
   easy to skip and invisible afterwards: Windows hands a browser the discrete GPU by
   default on a machine that has one, and a frame time taken on an RTX 5070 and filed
   against the Radeon 610M is the exact number this whole document exists to prevent.
   Check it in `edge://gpu` before believing any reading.
4. **Record them even if they miss**, and label the paint number with §1a's asymmetry.
   The status line says the targets are unmeasured; the honest replacement is a
   measurement, not a removal.
5. Answer §4's CI question, or record that it is still open.
