# Verifying Kryova on the Windows seat

Everything in this repo was **written on Linux and lint/type-checked there**. This is the
runbook for the machine that can actually run it: Windows, with CATIA installed and the
bridge available.

Read the honest expectation first, then work down the tiers. Each tier is independent —
a failure in one does not block the next.

**Tiers 1 and 2 have now run here (2026-09-09) and are green; the RESULTS section below
records what they found. Tier 3 (the CATIA seat) and tier 4 (the vision model) have not,
and neither has Job 3, the GUI ladder.** So "most of it has never been executed" is no
longer true of the offline and database halves and remains exactly true of everything that
needs the seat.

---

## READ THIS FIRST — you are the Windows session, and this is your brief

**The Linux stretch stopped on 2026-09-09 at phase E7.** Ten of twenty-nine phases are
complete, the whole suite is green on Linux (7,000+ tests), `ruff` and `mypy` are clean, and
**everything that could be closed without hardware has been closed.** What is left in the
plan for this machine is not leftovers — it is the work that was *deliberately deferred*
because a Linux box cannot do it honestly.

You have three jobs, in this order, and the order matters:

1. **Verify.** Run the tiers below. Every claim written on Linux is a claim about code that
   has mostly never executed on a real seat. A failure here is information, not a setback.
2. **Finish THE QUEUE.** Every item is work a Linux session was stopped on. Sections A–D are
   *measurements* — run the thing, record what happened. **Section E is code you have to
   write**, because writing it on Linux would have meant guessing at an API nobody could
   call.
3. **Drive the product through the GUI.** Not `pytest`, not `dispatch` — the actual web
   application at `localhost`, as a user. `docs/GUI_PROMPT_LADDER.md` is the method, and it
   has hard rules: **one prompt per level, a screenshot every time, and you do not move to
   the next level until the current one passes.**

**Three standing rules for this machine, none of them optional.**

* **A run with no picture has not been verified, it has been believed.** Every prompt you
  drive through the GUI ends with a screenshot saved into `docs/verification-<date>/`.
  Where CATIA is involved, two: `catia_capture_view` (the part as CATIA draws it, through
  the product's own tool) *and* a screenshot of the application window.
* **Test in the GUI, never through the dispatcher.** Calling `dispatch` directly tests the
  tools; it does not test the product, and every defect that has mattered here lived between
  the model and the tools.
* **Write down what you did in the same commit as the work.** A status line in
  `KRYOVA_MASTER_PLAN.md`, a line in `KRYOVA_BUILD_PLAN.md`'s *Done*, and a checked box here.
  A session that finishes work and leaves the plan unchanged has thrown away the only thing
  that lets the next session start.

**If something is slow on that machine, read
[MAKING_IT_FASTER.md](MAKING_IT_FASTER.md) before changing anything.** Two facts from it apply
directly to a gate run: the model is 4–7 minutes a turn and every other subsystem is seconds,
so a slow gate is almost always the model rather than the product; and a timing taken while
Ollama is on the CPU measures patience, not the application — check `ollama ps` and
`nvidia-smi` first.

**What "done" means for an item here.** Either a measurement recorded with the number and
the date, or a defect recorded with what was expected and what happened. Never "looked
fine". If an item cannot be settled, say what blocked it and leave the box unchecked — an
item quietly ticked is worse than one left open, because nobody re-examines it.

---

## THE NEXT WINDOWS SESSION — written 2026-09-10 after the third Linux stretch

**Read this first. The 2026-09-09 section below is history; its order is superseded by this
one.** Since the last seat session (L2 and L3 passed, L4–L6 not attempted) Linux closed P5 and
E16. **None of it has been driven through the GUI.**

**Four unfinished lanes were deliberately not pushed:**
- E15 task 1's CATIA half (batch execution on the seat);
- E15 task 2's database-backed job queue;
- attachment tables and load-table import (P4 tasks 2, 3 and 6);
- P9's container-health pins.

They are in `git stash` on the Linux machine and do not build yet. Do not test them, and do not
start those tasks here: a second copy would collide with the first.

**Start here, always:** `git fetch --all && git pull` on **both** repos, then `scripts\setup.ps1`
(re-running it is the update), then `alembic upgrade head` and `alembic check`. There is no new
migration; the head stays `ef4d9b93d3ca`.

### The order, and why it is this order

1. **Regression first.** Backend `pytest` (check it ran on PostgreSQL: the RLS and JSONB tests
   run rather than skip), `ruff`, `mypy`, `app.verify.recorded --check`,
   `scripts.plan_progress --check`. Frontend `npm run test`, `lint`, `type-check`. A red here is
   written up as expected-versus-actual before anything else happens.
2. **The GUI ladder, from Level 1**, with prompts written against the list below.
3. **Then the next phases**, seat-bound work first: E15 task 4 (the CATIA session pool and crash
   recovery — it needs a seat to write against), THE QUEUE section E, then **P7** (the desktop
   app and the bridge; task 1 stays blocked behind P9 task 4).

### What the GUI must be driven through this time

The wall the 2026-09-10 run hit is gone: **a part built on `occt` now reaches the solver**
(`tests/test_geometry_backends.py::TestThePartCanReachTheSolver`), so Levels 3–5 are reachable
on the open kernel for the first time. Write one prompt per level against these:

- **Level 1–2:** the editable spec panel — change a parameter from the panel mid-conversation
  and check that the part, the revision summary and the mass agree (P5.3). The agent's own plan
  with a checkpoint that stops the turn (E16.2).
- **Level 3:** a retry that keeps failing must end in a specific question (E16.4). Stopping a
  running simulation must say the solve already handed to CalculiX runs out (P5.6).
- **Level 4:** on `occt`, part → STEP → mesh → solve → results, watching the stage line change
  (P5.2, never a percentage). Re-run the identical case and confirm a cache hit (E15.2).
- **Level 5:** every number in the answer traces to a run, a measurement or a citation, and an
  approval gate shows exactly what was signed (P5.5).
- **Level 6:** M4 (gearbox) or M5 (stamping press). Record the rung reached.

**Pictures:** a screenshot every time a prompt finishes, and with CATIA involved two —
`catia_capture_view` through the product, and the CATIA window itself. Also capture each new
surface as it is exercised: the progress line, the stop button, the gate page and the spec panel. Everything goes in `docs/verification-<date>/`.

---

## THE NEXT WINDOWS SESSION — written 2026-09-09 after the second Linux stretch

**Read this before THE QUEUE.** The seat has had exactly one session (2026-09-09, tiers 1–2
green, A1–A5 ticked). Since then Linux has gone as far as it can go a second time, and the
result is that **the shell path is now built end to end and has never been executed.** That is
this session's centre of gravity.

**Start here, always:** `git fetch --all && git pull` on **both** repos (`Kryova-backend` and
`Kryova-frontend`). The backend moved a long way on 2026-09-09 and a session that starts on a
stale tree will "find" defects that were fixed before it sat down.

**Do not run a smoke test and call it verified.** The bar on this machine is the one in *What
"done" means* above: a number and a date, or a defect with expected-versus-actual.

### The order, and why it is this order

1. **Regression first — tiers 1 and 2 below.** Fast, and it tells you whether the Linux stretch
   broke anything on a real Postgres. **Check which engine tier 2 ran on before believing it**
   — `TEST_DATABASE_URL` unset silently falls back to in-memory SQLite, which is how the first
   seat session got a meaningless green. Expected: ~7,177 passed on Linux at the point of
   handover; a lower count is a collection error, not a smaller suite.
2. **A6 — run a shell deck through `ccx`. This is the new work and the highest value.**
   Everything but the run exists: `generate_shell_mesh` meshes a curved surface,
   `shell_loads.py` distributes the load, `ShellSolver.solve(mesh, case, section)` joins it up.
   `tests/test_shell_solver.py` pins what the deck *says*; nothing has checked what `ccx` does
   with it. Work up: solve a flat plate whose answer you can compute by hand, **then** attempt
   NAFEMS LE3. Note LE3 additionally needs its geometry sourced — see the A6 row.
3. **Queue E3 — the conduction oracle against `ccx`.** A1 and A2 have passed, so this is
   unblocked and it converts the in-house conduction solver from *verified against mathematics*
   into *cross-checked against another implementation*.
4. **Tier 3 and section B — the CATIA seat.** B1, B2, B3 have never run at all; `compare_backends`
   has never had a real seat on its right-hand side, and **Decision 1 rests on it**.
5. **Job 3 — the GUI ladder, and gate G1.** G1 ran twice and did not pass either time. Two of
   its three open items were closed on Linux on 2026-09-09, and A2/A3/A4 discharged the thermal
   and shell/beam deck items, so **G1 is dischargeable at this run for the first time.**
6. **D1** (`ollama pull llava`) and **queue E1** (sheet metal at the seat) if time remains.

### What the GUI must be driven through, specifically

`docs/GUI_PROMPT_LADDER.md` is the method and its three rules are hard. The ladder's own run log
says the next run is the first under that method and that the prompts must be written against
**what shipped between 2026-09-06 and 2026-09-09**, which is now:

- **plane analyses** (`analysis: "plane-stress" | "plane-strain"` with a `thickness_mm`),
- **conduction** (`analysis: "thermal-conduction"` with a `thermal_case`),
- **convergence studies** (`grids: 3` on a simulation — the answer must state its own mesh
  dependence rather than a single-grid number),
- **the requirements check** and **sheet-metal folding**,
- **the assembly repository** (a product holding two authors without one erasing the other),
- **the resume block** — reopen a conversation after real time has passed and confirm the
  frontend says where the work got to, not merely what was said.

Each of those is a *level*: one prompt, one screenshot, no moving on until it passes. A level
that half-passes has not passed.

---

## THE QUEUE — everything a Linux session could not finish

**Maintained as of 2026-09-09, the day the Linux stretch stopped. This is the list to work
from when you sit at that machine, and it is now the *whole* of what Linux left undone.**

Every entry here is work that was *stopped on Linux by missing hardware*, not work that was
skipped. Each one names the claim that is currently unverified, so a run either turns it into
a measurement or turns it into a defect. **A Linux session that gets stopped by hardware adds
a row here in the same commit** — an unrecorded blocker is one that gets rediscovered from
scratch, which has already cost this project two sessions.

Ordered by what a run settles per minute spent, not by phase number.

**Two rows closed on Linux the day after they were written, and both closed the same way**: the
thing they were waiting for turned out to be obtainable here after all. C1a's semi-axes were in
the manual that reproduces the target, and C1b's were in an open-source solver's committed test
input. Before working an item, spend five minutes asking whether it is genuinely blocked on this
machine — a queue that accumulates items nobody re-examines is a list of excuses.

### A. CalculiX — needs `ccx` on PATH (there is none on the Linux machine)

`app/solve/calculix/` was written **entirely from the CalculiX manual**, with a `[M]` citation
per keyword and an `[S]` where the manual stops and ccx's source answers.

**A1–A5 ran on the seat on 2026-09-09 against ccx 2.23, and the package is now verified rather
than documented.** Each box below carries its numbers. The run found two defects that no
offline test could have seen, both fatal to beams — see A4 — and A6 remains open with its
estimate corrected. `ccx` here is `C:\tools\calculix\bin\ccx.exe`, on both the Windows PATH the
server inherits and the Git Bash one.

- [x] **A1 — Any deck at all.** MEASURED 2026-09-09, ccx 2.23. The solid deck for a
      100 mm bar in tension solved and the `.frd` read back at the submitted node
      count (81/81). σ = F/A 25.000 → **25.0888 MPa (0.355%)**; δ = FL/AE 0.012195 →
      **0.012491 mm (2.43%**, the peak including the far corner's Poisson
      contraction). The oracle ran and agreed, displacement matching to six
      significant figures.
- [x] **A2 — The thermal cards (E6.4, written 2026-09-08).** MEASURED 2026-09-09 —
      **the first thermal comparison ever run.** An axially restrained bar at ΔT = 50 K:
      closed form σ = −EαΔT = −119.925 MPa, in-house **119.925000 (0.0000%)**,
      CalculiX **119.925000 (0.0000%)**. Oracle `ran=True agrees=True`, and
      `uniform_field=True`, so the stress was genuinely *judged* rather than reported.
      The cards written blind on 2026-09-08 are correct.
- [x] **A3 — `OUTPUT=2D` on a shell deck and on a beam deck (E6.3).** MEASURED
      2026-09-09, and the claim holds on both keywords. Through Kryova's own
      `write_frame_deck`, every shell element reported at the **submitted** node
      count — S4 9/9, S8R 21/21, S3 9/9, S6 25/25 — and the beam deck 5/5. So the
      quiet failure this item exists for (a reader indexing a `.frd` by the input
      mesh's numbering and silently getting the expanded model's) cannot happen.
- [x] **A4 — `*BEAM SECTION` data-line order.** MEASURED 2026-09-09. The order is
      right — profile dimensions first, direction cosines of `n1` on the following
      line — and a deck written exactly that way solves. **But running it found two
      defects that mattered far more than the order, neither visible offline:**
      (a) **CalculiX carries `SECTION=BOX` and `SECTION=PIPE` on `B32R` only**, and
      Kryova wrote `B31`/`B32`, so an RHS — the profile `sections.py` calls "the
      workhorse of a welded frame" — produced a deck refused at parse time:
      *"*BEAM SECTION of type BOX can only be used for B32R elements."* Swept across
      B31/B32/B32R × RECT/CIRC/PIPE/BOX and one-to-eight data values, so it is the
      section type that decides it and not the value count. `choose_element` now
      takes the section and substitutes B32R, refusing a *linear* mesh in words
      because there is no three-node element to substitute.
      (b) **`BeamMesh.connectivity` wrote `[start, end, middle]`** where ccx reads a
      beam row along the member, so the far end was taken for the midside node and
      the element folded back on itself: `*ERROR in e_c3d: nonpositive jacobian
      determinant`. **No quadratic beam deck this repo ever wrote could solve.** The
      test asserting that ordering was wrong in the same direction as the code,
      which is why nothing offline saw it. Shells were unaffected and are checked.
- [x] **A5 — The expansion pairs.** MEASURED 2026-09-09 by dropping `OUTPUT=2D` and
      counting the nodes the `.frd` carries per element: **B31 → 8 (C3D8I)**,
      **B32 → 20 (C3D20R)**, **S4 → 8 (C3D8I)** — the documented pairs hold.
      **B32R → 20 (C3D20R)**, the same expansion as B32, which is what makes the A4
      substitution above a change of element and not a change of answer. S3/S6/S8R
      solve through the product's writer at their submitted node counts (A3) but
      their expanded counts were not separately read back; that half is unticked in
      spirit and worth ten minutes next session.
- [ ] **A6 — NAFEMS LE3, the hemisphere.** MEASURED 2026-09-09 (second seat session):
      **a shell deck solves, LE3 does not reproduce 185 mm, and the box stays unticked.**

      *The half that works.* A flat cantilever plate under pressure was solved first, as the
      order requires, and all four shell element types went through ccx 2.23 and behaved
      exactly as shell theory says they should — against a closed form of 36.585 mm (beam)
      / 33.51 mm (wide plate): **S3 14.009 mm (−61.7%, linear triangles lock hard), S4
      33.131 (−9.4%), S6 35.541 (−2.9%), S8R 35.557 (−2.8%)**. The quadratic answers sit
      between the beam and plate bounds, where an aspect ratio of 5 puts them. **The S8R
      negative corner loads do not upset ccx** — that was the specific worry, and S8R gave
      the best answer of the four.

      *The half that does not.* LE3 encoded as a shell (closed pole, quarter sector,
      R = 10 000 mm, t = 40 mm, E = 68 250, ν = 0.3, full 2 kN at A and C, not halved)
      **does not reach 185 mm on any mesh**, and two readings of the symmetry edges bracket
      it without either being right:

      | edge condition | u_x(A), converged | vs 185 |
      |---|---|---|
      | uy/θx/θz on AE and ux/θy/θz on CE — the correct symmetry set | **1.21 mm** | −99.3% |
      | translations only (uy on AE, ux on CE) | **199.5 mm** | **+7.8%** |

      The pole treatment is irrelevant: z at the pole, z at A (the deck's reading) and a
      fully clamped pole all give 1.206 mm to three decimals. It is the **rotational
      constraints on the symmetry edges** that stiffen the model 150×. Both sets were
      derived from first principles — under reflection an axial vector picks up a sign, so
      symmetry about y = 0 gives θx = θz = 0 with θy free — and they agree with §2.3.
      A six-DOF `clamp` works correctly on the flat plate above, so rotational BCs do reach
      ccx; it is specifically an *edge* of them that over-constrains. The suspicion to test
      next is CalculiX's knot mechanism: a rotational DOF on an expanded shell node makes
      that node's expanded cross-section rigid, and a whole edge of those adds hoop
      stiffness to a problem whose entire subject is inextensional bending.

      **The 2% tolerance was not loosened.** +7.8% is not a pass, and the reading that
      produces it is under-constrained rather than correct — it omits a condition the
      benchmark requires, which is why it comes out soft. One mesh (tri6, 700 mm, 815
      faces) returned **7.686 mm**, a second instability worth chasing separately.

      Original entry follows. Added 2026-09-08,
      and now the **only** case in the catalogue this machine cannot eventually reach on its own
      (LE11 waits on physics, not hardware). Clearing it takes the trust page from three
      validated cases to four, and from two validated *analyses* to three — LE1 and LE10 are
      both linear-static, so LE3 is worth more per hour than either of them was.
      LE3 is fully encoded and cited in `app/verify/nafems.py` and blocked on
      `NO_SHELL_SOLVER`: the deck writer produces a shell deck and nothing on Linux can run it.
      **A1-A5 CLEARED ONE OF ITS THREE PRECONDITIONS, AND THE ESTIMATE ABOVE IS WRONG**
      (measured 2026-09-09). "Costs almost nothing once A2-A5 pass" was written from the
      belief that the shell *deck* was the obstacle. It was not the only one. What is now
      true and what is not:

      * **The deck writer and the solver are fine.** All four shell elements solve on ccx
        2.23 through `write_frame_deck`, reporting at the submitted node count (S4 9/9,
        S8R 21/21, S3 9/9, S6 25/25). `Blocker.NO_SHELL_SOLVER`'s first clause — "no solver
        here takes one" — is cleared for the *deck* path.
      * ~~**Nothing meshes a shell.**~~ **CLEARED on Linux, 2026-09-09.**
        `app.mesh.gmsh_mesher.generate_shell_mesh` meshes a curved surface in three
        dimensions into a `ShellMesh` in all four element types, and is checked against a
        revolved 90 degree spherical zone open at the pole, whose area
        it recovers to 0.1%. The midside ordering debt `app/mesh/structural.py` named is
        settled by measurement: the gmsh-to-CalculiX permutation is the **identity** for
        both shapes, re-checked by coordinate on every quadratic mesh.
      * ~~**No tributary-area distribution for a shell's faces.**~~ **CLEARED on Linux,
        2026-09-09.** `app/solve/shell_loads.py`, with the consistent-load factors per
        element type. The one to know: an **S8R face's four corners take −1/12 each**, so a
        tributary-area intuition loads it wrongly in a way that solves cleanly.
      * ~~**No `Solver` accepts a `ShellMesh`.**~~ **CLEARED on Linux, 2026-09-09.**
        `app/solve/calculix/shell.py` — `ShellSolver.solve(mesh, case, section)` joins the
        mesher, the load path, `write_frame_deck`, `run_ccx` and `frd.py` into one run and
        returns a `SolveOutput`. It is **not** a `Solver` subclass: the ABC is
        `solve(mesh: TetMesh, case: LoadCase)` and a shell needs a third argument, its
        `ShellSection`; `PlaneSolver` declined the same widening. The `.frd` reader needed
        no shell version — `displacements` and `nodal_stress_tensor` key off node count,
        and `OUTPUT=2D` (which you measured in A3) makes that the submitted numbering.
      * **A validated case needs a convergence study**, which is now buildable, because the
        mesher takes an `element_size_mm` and the load path is mesh-independent by
        construction (tested by refining). LE11 sits at `unconverged` for a related reason.

      **What is left is one measurement and one piece of research.** The deck-writing half
      is fully tested offline (`tests/test_shell_solver.py`, 16 tests: element type, section
      card, `OUTPUT=2D` on both keywords, six-DOF clamp, and the exact `*CLOAD` values
      including S8R's negative corners). **Nothing has run it**, because there is no `ccx`
      here — that is yours.

      **The geometry is sourced too, as of 2026-09-09**, to the LE11 standard and recorded in
      `docs/nafems-le3-geometry.md`: R = 10 m, t = 0.04 m, a 90 degree sector, corroborated by
      a scan of the *original* NAFEMS dimensioned figure plus a committed Abaqus deck whose
      node coordinates were checked numerically. **Read §1.5 before you encode anything.**

      > **LE3 HAS NO HOLE AT THE POLE.** The shell is *closed* at the pole and point E **is**
      > the pole. The widely repeated "hemisphere with an 18 degree hole" is a **different
      > benchmark** wearing LE3's name, and this repository's own notes asserted it from
      > memory for several hours on 2026-09-09 before the sourcing caught it. That is the
      > `C1` rule earning its keep twice in one day. Four independent sources settle it, one
      > of them by node coordinates.

      **So A6 is now a hardware measurement and nothing else** — every prerequisite it named
      has been built or sourced. What it needs from you, in order:

      1. **A shell deck through `ccx` at all.** Solve a flat plate with `ShellSolver` whose
         answer you can compute by hand before going anywhere near a benchmark.
      2. **Then LE3** — and read §6 of the geometry record first, because it names a real
         difficulty that is *not* a sourcing gap: LE3 is loaded by **point forces**, and a
         point load has no finite displacement in 3-D elasticity. In shell theory the
         divergence is extremely slow, which is why shell reproductions land within a
         percent — but **a convergence study on the loaded node would be measuring the
         singularity, not the discretisation.** Decide and write down what you are
         extrapolating *before* you sweep, exactly as LE11's residual says.
      3. Then: drop `Blocker.NO_SHELL_SOLVER`, add a `run_le3`, re-record with
         `venv/bin/python -m app.verify.recorded`, and commit the artefact.
         **Re-record last, after every source edit** — the fingerprint hashes source, so even
         a docstring change expires it and the failure surfaces as ~11 red tests across
         `test_trust`, `test_verify_recorded` and `test_verify_register`.

      Note §6's other warning: the 2% tolerance is tight, and two of six published
      reproductions miss it. **Do not loosen it to make a run pass** — a model that needs 5%
      is telling you something true.

### B. CATIA seat — needs a licensed V5 seat and the bridge

- [ ] **B1 — E1 task 7, cross-backend conformance.** The same compiled `Plan` through
      `OcctRunner` and through `app.catia.dispatch` must build the same part.
      `compare_backends` is written and exercised against two runners; its right-hand side has
      never been a real seat. **Decision 1 rests on this** and it is currently unverified
      rather than wrong.
- [ ] **B2 — E3 phase proof, measurement agreement.** Every interrogated quantity must agree
      between OCCT and CATIA to a declared tolerance. Same situation as B1, same session.
- [ ] **B3 — The four questions `docs/CATIA_BRIDGE_PROTOCOL.md` says Linux cannot answer.**
      Do CATIA's dialogs answer `WM_GETTEXT`? Is `EN_CHANGE` needed after setting an edit
      field? What are the real window classes? Does `StartCommand` ever report a failure?
      `describe_dialog` prints unrecognised controls with their class name specifically so
      this session produces answers rather than a shrug — paste its output into the report.
- [ ] **B4 — Gate G1, re-run.** Ran 2026-09-06 and **did not pass**: rung 3 failed. It is
      carried forward. G1 is now also the only thing that can verify E6 — see section A.
      Drive it from `docs/GUI_PROMPT_LADDER.md`, through the chatbot, never through
      `dispatch`, two pictures per prompt.
- [ ] **B5 — E21 task 1, the cross-implementation half of the STEP matrix, and the one
      measurement that settles a ×1000.** Kryova→STEP→Kryova is measured
      (`app/manufacture/interop.py::measure_metadata_round_trip`): names, colours, layers,
      validation properties and assembly occurrences all carry, and a **flatness tolerance
      authored at 0.05 mm reads back as 50.0 mm** because the writer tags the measure
      `SI_UNIT($,.METRE.)` while the model is `.MILLI.`. A round trip through one
      implementation cannot tell you whose defect that is — both ends share it.
      **What to do, in ten minutes:** run
      `venv/bin/python -c "import tempfile; from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox; from app.manufacture.xde import *; d=tempfile.mkdtemp(); write_step_with_metadata(BRepPrimAPI_MakeBox(10.,20.,30.).Shape(), d+'/t.step', Annotations(name='Bracket', colour_rgb=(0.2,0.4,0.9), layer='KRYOVA-PART', tolerance=Tolerance(value_mm=0.05))); print(d)"`,
      open the file **in CATIA on the seat**, and report four things: the part name, the
      colour, the layer, and **what CATIA says the flatness tolerance is — 0.05 or 50**.
      A screenshot of the tolerance in CATIA's tree is the deliverable.
      **Why it is worth a seat slot:** if CATIA reads 50, OCCT's *writer* is wrong and Kryova
      must never write semantic PMI through this path until it is fixed upstream. If CATIA
      reads 0.05, OCCT's *reader* is wrong, the file is right, and the product may claim
      semantic PMI export while its own re-import stays untrustworthy. Those are opposite
      conclusions and nothing on Linux can choose between them.

- [x] **B6 — Can Kryova drive CATIA's Analysis & Simulation instead of solving it itself?**
      **Measured 2026-09-11 on this seat. Answer: the licence is there, the automation is
      not.** Asked because the question is a fair one — CATIA has GPS, GAS, ELFINI and
      Advanced Meshing Tools, and `app/catia_kb/commands/analysis.py` already knows all of
      them — so it was settled by probing rather than by argument.
      **What is licensed here:** `Documents.Add("Analysis")` **succeeds** and returns a real
      `CATAnalysis` document. So this seat is not the constraint, and the August COM probe
      (which listed Part Design, GSD, Knowledgeware, Product, Drawing and SheetMetal) simply
      never tested it.
      **What is drivable:** almost nothing. The document's `Analysis` object is
      `AnalysisManager` and exposes `AnalysisSets`, `AnalysisModels`, `Parameters` and
      `GetItem`. It exposes **no `Compute`, no `Solve`, no `Update`**, and **no factory** —
      `AnalysisFactory`, `CreateAnalysisCase`, `AnalysisCases` and `AnalysisEntities` are all
      `AttributeError`. Compare Part Design, where `part.ShapeFactory.AddNewPad(...)` builds
      geometry and `part.Update()` rebuilds it. There is no equivalent for analysis: V5
      automation can *navigate* an existing `.CATAnalysis`, not author restraints, loads or a
      mesh, and **cannot start a solve at all**.
      **So driving GSA would mean driving its GUI**, which the Win32 bridge can technically do
      — and which would put a mesh spec, a restraint, a load and a solve behind
      one-dialog-at-a-time puppetry on a single seat, with results readable only by driving
      more GUI. A convergence study is 3–5 solves and an optimisation sweep is hundreds.
      **Kept as a target, not as an engine.** The valuable version of this is the same shape
      as Decision 1's for geometry: Kryova solves (headless, in CI, fingerprinted,
      NAFEMS-validated), and where the customer wants the study *in* CATIA it lands there.
      A GSA run would also make a genuinely independent oracle beside CalculiX for
      `app/solve/oracle.py`, which is the strongest form of the claim. Neither is scheduled;
      see the note added to E6 for what would have to be true first.
      Probe scripts are throwaway; the numbers above are the record.

### C. Not hardware — an input this machine does not have

Recorded here because the effect is the same: a Linux session cannot finish it.

- [ ] **C1 — E7.1, LE11's problem *definition*.** The catalogue is written
      (`app/verify/nafems.py`) and all five targets are sourced from vendor verification
      manuals. What is missing for LE11 is its **cylinder/taper/sphere dimensions**, which are
      in the NAFEMS publication and not in the manuals that reproduce the target. Note that the
      geometry is only half of LE11's blocker — it also needs an analysis that solves *for* a
      temperature field (E7 task 6), so a dimensioned drawing alone does not unblock it.
      **Do not transcribe a value from memory**: the
      register republishes the `source` string, and a remembered number carrying a citation is
      indistinguishable in it from a real one. That is not hypothetical — a figure recalled for
      FV52 was 4% wrong, and only reading the reference row caught it.
- [ ] **C1c — LE3's geometry: a citable source found, and a variant question that must be
      settled before any number reaches code.** Searched 2026-09-11 using the technique that
      closed C1b. Altair's **OptiStruct 2021 Verification Problems** manual, *OS-V: 0030 Radial
      Point Load on a Hemisphere*
      (`https://2021.help.altair.com/2021/hwsolvers/os/topics/solvers/os/nafems_test_problem_le3_r.htm`,
      read 2026-09-11) prints a complete definition: **radius 10 m, radial thickness 0.04 m,
      E = 68.25 GPa, ν = 0.3**, two pairs of **4000 N** point loads at the free edge at right
      angles, one pair inward and one outward, a quarter model with symmetry on edges AE and CE,
      z fixed at E, edge AC free, and the target **x-translation at A = 0.185 m** on CQUAD4.
      **Do not copy those into `nafems.py` yet.** That page describes a **closed** hemisphere and
      states no pole hole, while `CLAUDE.md` records LE3's missing geometry as "the radius,
      thickness and **hole angle**". There are two benchmarks in circulation with this shape —
      NAFEMS LE3's hemispherical shell, and the MacNeal–Harder *pinched hemisphere with an 18°
      hole* — and **they are different problems with different reference values**. Taking one
      page's numbers for the other is exactly the FV52 error (a recalled figure, 4% out, caught
      only by reading the reference row).
      **What settles it:** the NAFEMS publication itself, or a second independent vendor manual
      that names the hole explicitly one way or the other. `SOURCES["abaqus-le3"]` is already
      cited for the 185 mm target — read *that* figure and see whether it shows a hole.
      Note this is **not** LE3's blocker: that is `Blocker.NO_SHELL_SOLVER`. This row exists so
      the geometry half is not rediscovered from scratch.

- [x] ~~**C1a — LE10's ellipse semi-axes.**~~ Resolved 2026-09-08 on Linux: the Abaqus
      verification manual's LE10 entry states the four semi-axes and the thickness in full, so
      no unavailable document was needed after all. LE10 now **runs and validates**
      (−5.36376 MPa against −5.38, 0.302%). Kept as a row because the lesson generalises: check
      whether the *reproducing* manual carries the geometry before recording a case as blocked
      on a document nobody has.
- [x] ~~**C1b — LE1's ellipse semi-axes.**~~ Resolved 2026-09-08 on Linux, and it took a second
      lesson to get there. Three vendor manuals (Abaqus, Altair, DIANA) all quote LE1's target
      and **none** prints the ellipses — the Abaqus page literally says "functions defining the
      curves BC and AD are given above" beside a figure that is not in the text. The geometry was
      found instead in an **open-source verification suite that ships its input file**: FeenoX's
      `examples/nafems-le1.geo` states `a=1000, b=2750, c=3250, d=2000` with `A=(0,a)`,
      `B=(0,b)`, `C=(c,0)`, `D=(d,0)`. Those are the same four numbers the independently-sourced
      LE10 entry already carried, which is a genuine cross-check rather than a coincidence: **LE1
      and LE10 are the same plan geometry**, a 100 mm membrane and a 600 mm plate. Generalisable:
      when a reproducing manual omits the geometry, look for a free solver whose test suite
      commits the mesh input — the numbers are in the repository even when they are not in the
      prose.
- [x] ~~**C2 — record FV52's outcome into the published register.**~~ Done 2026-09-08 on Linux;
      the trust page reads 2 of 11 (FV52 and LE10).
      `venv/bin/python -m app.verify.recorded --check` belongs in
      CI: it re-runs nothing and fails the moment a solver change orphans the published evidence.

### D. Needs a model this machine does not host

- [ ] **D1 — The visual check against a real vision model.** `ollama pull llava`, set
      `AI_VISION_MODEL=llava`. Confirm you get the *refusal* first with the text-only default
      — that refusal is the guard working (Ollama drops an image handed to a text-only model
      and answers anyway, so a check that trusted it would manufacture agreement).

### E. Needs a seat to *write*, not only to verify — **this section is coding work**

These are not "run it and see". They are pieces of the product that can only be *written* on
the machine that has the thing they drive, because every line of them is a guess otherwise.
Treat each one as a normal task under this repo's rules: write the tests with the work, run
`pytest`/`ruff`/`mypy`, verify each new guard by breaking what it guards, and update the
master plan's status line in the same commit.

- [ ] **E1 — Sheet metal on the CATIA side (E17.3 task 3, written 2026-09-09).** A
      `SheetMetalPart` now builds as an OCCT solid (`app/kernel/occt/sheetmetal.py`), which is
      the open-kernel half and is verified against closed-form volumes here. The seat half —
      CATIA's SheetMetal Design workbench through the bridge, so a folded part *lands* where
      the customer works, as Decision 1 requires of everything else — does not exist and was
      deliberately not declared: a `catia_sheetmetal_wall` in the registry is a promise the
      bridge cannot keep, and the registry is read by the agent as a list of things it can do.
      What a session at the seat settles, in order: whether `AddNewWall`/`AddNewFlange` behave
      as the COM documentation says on this V5-R33 install; whether the sheet-metal parameters
      (thickness, default radius, K) can be set before the first wall or only after; and
      whether a part built by these calls unfolds in CATIA to the same blank
      `app.sheetmetal.unfold` computes — the last one being the measurement that matters,
      because the two arithmetics are independent implementations of the same standard.
      Until then the fold path is open-kernel only and says so.
      **Where the work goes:** a new `app/catia/ops/sheet_metal.py` declaring the operations
      (mirroring an existing ops module — read `part_design.py` first), the daemon-side
      handlers in `scripts/catia_bridge/`, and `app/catia_kb/commands/sheet_metal.py` already
      holds the workbench knowledge (French labels included). The OCCT side —
      `app/kernel/occt/sheetmetal.py` and `app/sheetmetal/fold.py` — is written, tested and
      is the reference for what the geometry must come out as: the folded volume is a closed
      form, so a CATIA-built part can be measured against the same arithmetic.
      Master plan: **E17.3 task 3** carries this residual in its status line.

- [ ] **E2 — Run the four gates the plan defines, in order (master plan Part 2).** G1 ran on
      2026-09-06 and did **not** pass (rung 3 failed); it is carried forward and is now also
      the only thing that can verify E6's CalculiX work. G2, G3 and G4 have never run. Each
      gate is driven from `docs/GUI_PROMPT_LADDER.md` through the chatbot in the browser —
      never through `dispatch` — with a screenshot per prompt and a dated report in
      `docs/verification-<date>/`.
      **Still open after the 2026-09-10 night run, and the box stays unchecked** — that was a
      ladder run, not a formal gate. What it settled is worth carrying: **L1, L2 and L3 pass
      and L4 does not**, so a gate attempted today would fail at the same rung. The blocker is
      master plan **E7 task 7** (added by that run): the agent states a pass/fail verdict
      against the user's stress limit from a single-grid solve whose own record says
      `converged: false`. Two Kryova defects found and fixed on the way — a streamed tool call
      discarded on every local-model turn, and two stop reasons dropped in the browser — are in
      `docs/verification-2026-09-10-night/`. **Fix E7.7 before attempting G1 again**, or the
      gate will re-measure a known failure.

- [ ] **E3 — The conduction analysis through the GUI, and against CalculiX once A2 passes.**
      Added 2026-09-09 with the work. `analysis: "thermal-conduction"` now reaches a request
      and is verified against three closed forms on Linux (linear bar to 6.6e-12 K, the
      logarithmic tube wall, the convecting-bar Biot tip temperature). What Linux cannot do
      is compare it with anything: ccx has a `*HEAT TRANSFER` step and this build does not
      federate it. Once A1–A2 pass, write the deck and point `app/solve/oracle.py` at a
      thermal case — that turns the in-house conduction solver from *verified against
      mathematics* into *cross-checked against another implementation*, which is the stronger
      claim and the one `CONDUCTION_BACKEND` was given its own setting to make possible.

---

## Expect failures on the first run, and that is the point

`tests/test_render.py`, `tests/test_vision.py`, `tests/test_design_machine_checks.py` and
`tests/test_design_sensitivity.py` — 124 tests — were written the same day as the code they
check and have only been **import-checked** (`pytest --collect-only`, which loads the files
without executing anything). They collect cleanly. Nothing more is known about them.

That is not a reason to distrust the code more than usual; it is a reason to treat the first
run as *the measurement*, not as a formality. For calibration, running things on the day the
code was written found five real defects that reading it had not — including a renderer that
drew every part upside down, which no determinism check could see because a consistently
mirrored image is still byte-identical to itself.

**When a test fails, the useful question is which of the two is wrong.** A test written from
the same understanding as the code can be wrong in the same direction.

---

## 0. Setup

```powershell
git pull
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

Idempotent — re-running it *is* the update. It reuses the venv, installs only what changed,
applies new migrations, and rebuilds the reference index only if the PDFs moved.

`-NoIndex` skips the manual indexing if you want a fast start; the assistant works without
it and says so rather than pretending.

**Expect the dependency step to be slow the first time.** `cadquery-ocp` is ~166 MB and
drags in ~640 MB of VTK.

---

## RESULTS — first run on the seat, 2026-09-09 (backend `656e598`)

**Tiers 1 and 2 are done and green. Six defects found, all fixed; four of them
were invisible on Linux by construction.** Tier 3 (seat) and tier 4 (vision) are
still open, and so is THE QUEUE.

| Tier | Result |
|---|---|
| 1 — offline | **927 passed, 0 failed**, including the 124 tests that had only ever been import-checked (`test_render`, `test_vision`, `test_design_machine_checks`, `test_design_sensitivity`). The iso render was looked at: a 60×40×20 box, right proportions, hidden lines dashed, **right way up**. |
| 2 — database | **7,244 passed, 2 skipped, 1 xpassed, 0 failed** on real PostgreSQL 18.6. `alembic check` clean at `b941a651831a`. |

The first tier-2 run was **not a Postgres run at all**, and that is the finding
worth carrying: `TEST_DATABASE_URL` was unset, so `conftest.py` fell back to
in-memory SQLite exactly as CLAUDE.md warns. It reported *23 failed, 7,204
passed, 17 skipped*. Pointed at a real `kryova_test` it reported *10 failed,
7,234 passed, 2 skipped, 1 xpassed* — fifteen tests that had been skipping
themselves started running, and the RLS test began to XPASS. **A green tick from
this tier means nothing until you have checked which engine it ran on.**

What was fixed, and where the fault was:

1. **`code_fingerprint` was not stable across checkouts**, though its docstring
   said it was — it keyed on `str(path)` (`app\solve\…` on Windows) and hashed
   raw bytes (CRLF under `core.autocrlf=true`). Every recorded validation
   outcome was therefore discarded here and the trust page published *nothing is
   validated*. **Neither normalisation alone is enough**; both together
   reproduce a Linux recording. 11 tests. *Code was wrong.*
2. **The local Postgres role was `SUPERUSER`**, so RLS was inert on this
   machine — the recipe in `docs/LOCAL_POSTGRES.md` said to create it that way.
   Recipe and machine both corrected. *Documentation was wrong.*
3. **`scripts/plan_progress.py` crashed on Windows** with `UnicodeDecodeError`:
   `read_text()` with no encoding takes the locale codec, cp1252 here, and the
   plan has em-dashes. This is the command the workflow tells every session to
   run when it finishes work. The progress block was current all along. *Code
   was wrong.*
4. **Eight `test_catia_local_bridge` tests** exercised a Windows-only branch for
   the first time. `catia_process_is_running` shells out to `tasklist` behind a
   `sys.platform` guard; the `_FakeProcess` double is not a context manager, so
   `subprocess.run` raised inside a broad handler, and the probe's own call
   shifted every positional index into the captured spawn list. Two of them also
   read the *real* probe, so **their result depended on whether CATIA happened
   to be open**. *Tests were wrong.*
5. **A conduction tolerance was absolute where it had to be relative**:
   `fixed_temperature_heat_w` is the global solve residual, and `1e-12 W`
   passed on Linux and failed here at `1.28e-12` — relative `1.7e-13`, which is
   LAPACK, not physics. Rescaled to the Robin terms that cancel. Worth knowing:
   that assertion checks *convergence*, not assembly — a 0.1% error in the film
   load is caught by the temperature assertion above it and does not move the
   residual. *Test was wrong.*
6. **`CONDUCTION_BACKEND` was undocumented in `.env.example`.** Added with E7 and
   never written down. This one fails on Linux too. *Documentation was wrong.*

---

## 1. Offline — no database, no CATIA, no model

The fast loop. If any of this fails, nothing above it is worth running yet.

```powershell
venv\Scripts\python -m pytest tests\test_solver.py tests\test_mesh.py tests\test_geometry.py -q
venv\Scripts\python -m pytest tests\test_kernel.py tests\test_interrogation.py -q
venv\Scripts\python -m pytest tests\test_design_*.py -q
venv\Scripts\python -m pytest tests\test_render.py tests\test_vision.py -q
venv\Scripts\python -m pytest tests\test_geometry_backends.py -q
venv\Scripts\python -m pytest tests\test_kernel_routes.py -q   # needs the DB (tier 2)
```

| Suite | What a pass actually proves |
|---|---|
| `test_solver` `test_mesh` `test_geometry` | The four analyses agree with closed-form answers (σ=F/A, Euler buckling, cantilever modes, σ=−EαΔT). Verified against mathematics, not recorded output. |
| `test_kernel` | The OCCT kernel builds what the design vocabulary says, including E2's Proof — a plate whose four corners carry different radii, rebuilt after a feature is inserted ahead of them. |
| `test_interrogation` | Wall thickness, draft, undercuts, curvature — all against closed-form answers, all reporting sampled-vs-exact honestly. |
| `test_design_*` | The design IR: compile, execute, diff, assertions, correction loop, **machine checks (5.1)** and **sensitivity (5.3)**. All offline by design; if these need a network, something has regressed. |
| `test_render` `test_vision` | **New and unrun.** Eight canonical views byte-identical run to run, section cuts, ink diffs, and the visual check's refusal behaviour. |

### Look at a render with your own eyes

The most direct proof E4 works, and the one that catches an orientation error a hash cannot:

```powershell
venv\Scripts\python -c "from app.kernel.occt.binding import symbol, require; require(); from app.render import render_views; box = symbol('BRepPrimAPI_MakeBox')(symbol('gp_Pnt')(0,0,0), 60.0, 40.0, 20.0).Shape(); [open(f'{name}.png','wb').write(shot.png) for name, shot in render_views(box, ('front','top','iso')).items()]"
```

Three PNGs in the repo root. **Check the front view is the right way up** — build something
obviously top-heavy if a plain box is ambiguous. That defect shipped once and no automated
check saw it.

---

## 1b. Drive the agent with **no CATIA at all**

Added 2026-09-05. Set in `.env`:

```
GEOMETRY_BACKEND=occt
```

and the agent builds geometry with the open kernel **in the API process** — no seat, no
licence, no bridge. 108 of the 201 declared operations are implemented, and the agent is
offered exactly those 108 rather than all 201, so it cannot pick a tool that will fail on a
round trip.

This is worth doing before anything CATIA-related, because it exercises Era I and II
(the whole geometry kernel and selection vocabulary) through the real product path —
chat → agent → dispatch → kernel — on a machine that needs nothing installed. Ask it for
something concrete:

> make a 60 by 40 by 20 plate and round its four vertical corners 5 mm

`GET /catia/status` (or the bridge panel) reports `backend: "occt"`, the kernel version,
and `108 / 201`. A part built here is **not saved to disk** — it lives in the worker
process, one document per conversation, eight at a time. The ninth evicts the oldest, and
the evicted conversation is *told* on its next call rather than silently continuing against
an empty document.

**See the part over HTTP** (new — this is what the frontend will consume):

```
GET /api/v1/kernel/conversations/{conversation_id}/render          # PNG, iso view
GET /api/v1/kernel/conversations/{conversation_id}/render?view=front&section=x
GET /api/v1/kernel/conversations/{conversation_id}/measure         # JSON with provenance
```

Open the render URL in a browser tab while chatting: same conversation id as the chat, and
the ETag is the render digest, so refreshing is cheap. A section (`section=x|y|z`) cuts
through the middle of that axis and hatches the cut face at 45° — a bore or pocket that is
invisible in a wireframe shows immediately. Expect a 409 with a plain-English reason when
nothing is built yet, when the document was evicted, or when the backend is `catia`.

Then switch back to `GEOMETRY_BACKEND=catia` for tier 3.

## 2. Database — needs a live Neon connection

```powershell
venv\Scripts\python -m alembic check     # models vs migrations; must be clean
venv\Scripts\python -m pytest -q         # the whole suite, ~4 min
```

Runs in a `kryova_test` schema, created and dropped per run. **Do not run two full suites
at once** — it exhausts the connection pool and fails with `TooManyConnectionsError`, which
is an infrastructure artefact, not a regression. Re-run the named tests in isolation before
believing a failure.

---

## 3. CATIA seat — the things Linux could never check

This is the tier that only exists on this machine, and two phases have been carrying a
residual waiting for it.

```powershell
venv\Scripts\python -m pytest tests\test_catia_e2e.py tests\test_catia_interactive.py -q
```

**The two blocked conformance halves.** The board records these as the outstanding residual
on E1 and E3:

- **E1** — the same compiled `Plan` executed through `OcctRunner` and through a CATIA seat
  must build the same part. The OCCT half passes; the seat half has never run.
- **E3** — every measurement must agree between OCCT and CATIA to a declared tolerance.
  Same situation.

Both matter more than an ordinary test: Decision 1 says the IR compiles to an open kernel
*first* and CATIA is one backend among several. Nothing has ever confirmed the two backends
agree, so that claim is currently unverified rather than wrong.

**What the protocol doc says Linux cannot answer** (`docs/CATIA_BRIDGE_PROTOCOL.md`, the
mock section) — write down what you observe, because the first real session is what settles
these:

- Do CATIA's dialogs answer `WM_GETTEXT`?
- Is `EN_CHANGE` needed after setting an edit field?
- What are its actual window classes? `describe_dialog` reports unrecognised controls with
  their class name specifically so this session produces an answer rather than a shrug.

---

## 4. The visual check — needs a vision model

Phase 4.2 will report `unchecked` on any machine with no vision model, which is correct
behaviour and not a pass. To exercise the real path:

```powershell
ollama pull llava
```

then set in `.env`:

```
AI_VISION_MODEL=llava
```

The shipping default (`qwen2.5-coder`) has no eyes. **Ollama does not refuse an image handed
to a text-only model — it drops it and answers anyway**, which is why the code probes
`/api/show` for a `vision` capability or a `projector_info` block and refuses by name rather
than trusting the answer. Confirm you get a refusal *before* pulling the vision model; that
refusal is the guard working.

---

## 5. Reporting back

For each failure, the useful report is three lines: the test name, the assertion output, and
whether you think the code or the test is wrong. The second question is the one that matters
— a test written from the same understanding as the code can be wrong in the same direction,
and that is exactly what a first run is for.

Lint and type-check are already clean on Linux and should stay clean here:

```powershell
venv\Scripts\python -m ruff check app\ tests\
venv\Scripts\python -m mypy app\
```
