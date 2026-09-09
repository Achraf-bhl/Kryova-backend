# Verifying Kryova on the Windows seat

Everything in this repo up to 2026-09-05 was **written on Linux and lint/type-checked
there, and most of it has never been executed**. This is the runbook for the machine that
can actually run it: Windows, with CATIA installed and the bridge available.

Read the honest expectation first, then work down the tiers. Each tier is independent —
a failure in one does not block the next.

---

## THE QUEUE — everything a Linux session could not finish

**Maintained as of 2026-09-08. This is the list to work from when you sit at that machine.**

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

`app/solve/calculix/` is written **entirely from the CalculiX manual**, with a `[M]` citation
per keyword and an `[S]` where the manual stops and ccx's source answers. Not one keyword in
it has been through the real solver. The first run is the measurement that turns the whole
package from *documented* into *verified*.

- [ ] **A1 — Any deck at all.** Submit the solid deck `write_deck` produces for the bar in
      tension and read the `.frd` back. Everything below assumes this works.
- [ ] **A2 — The thermal cards (E6.4, written 2026-09-08).** `*INITIAL CONDITIONS,
      TYPE=TEMPERATURE`, `*EXPANSION, ZERO=` and `*TEMPERATURE` were added because
      `delta_t_k` reached the in-house solver and reached CalculiX **not at all**. Run
      `app/solve/oracle.py` on a **thermal** case — a restrained bar, `σ = −EαΔT` — and
      confirm the two solvers now agree. Nobody has ever pointed the oracle at a thermal case.
- [ ] **A3 — `OUTPUT=2D` on a shell deck and on a beam deck (E6.3).** The claim is that ccx
      accepts the parameter on both `*SHELL SECTION` and `*BEAM SECTION`, and that with it the
      `.frd` carries results at the node numbers **submitted** rather than at the expanded
      model's. This is the one that produces a plausible wrong number rather than an error:
      every node index is in range and there is no length mismatch, so
      `frd.displacements(frd, mesh.node_count)` would quietly read another model's answer.
      Submit one `write_frame_deck` shell deck and one beam deck and check the node count in
      the `.frd` against `mesh.node_count`.
- [ ] **A4 — `*BEAM SECTION` data-line order.** The deck writes the profile dimensions, then
      the normalised direction cosines of `n1`, on the following line. Confirm against a run;
      a wrong order gives a beam bent about the wrong axis, which for an RHS is a stiffness
      error of 2.25× and looks entirely believable.
- [ ] **A5 — The expansion pairs.** `EXPANSIONS` claims S3→C3D6, S4→C3D8I, S6→C3D15,
      S8R→C3D20R, B31→C3D8I, B32→C3D20R. The `.frd` node count per element type settles all
      six at once.
- [ ] **A6 — NAFEMS LE3, the hemisphere, once a shell deck actually solves.** Added 2026-09-08,
      and now the **only** case in the catalogue this machine cannot eventually reach on its own
      (LE11 waits on physics, not hardware). Clearing it takes the trust page from three
      validated cases to four, and from two validated *analyses* to three — LE1 and LE10 are
      both linear-static, so LE3 is worth more per hour than either of them was.
      LE3 is fully encoded and cited in `app/verify/nafems.py` and blocked on
      `NO_SHELL_SOLVER`: the deck writer produces a shell deck and nothing on Linux can run it.
      A2–A5 are the prerequisites, so this costs almost nothing once they pass and it is worth
      a third validated analysis on the trust page. Unblock it by giving the case a runnable
      `Case` (drop the blocker, add a `run_le3`), re-record with
      `venv/bin/python -m app.verify.recorded`, and commit the artefact.

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
