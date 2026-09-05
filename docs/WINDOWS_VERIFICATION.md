# Verifying Kryova on the Windows seat

Everything in this repo up to 2026-09-05 was **written on Linux and lint/type-checked
there, and most of it has never been executed**. This is the runbook for the machine that
can actually run it: Windows, with CATIA installed and the bridge available.

Read the honest expectation first, then work down the tiers. Each tier is independent —
a failure in one does not block the next.

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
