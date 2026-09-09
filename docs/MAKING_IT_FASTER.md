# Making it faster — what actually works here, and what only looks like it does

**Read this before optimising anything in this repository.** It is not a list of general
performance advice; it is what is true about *this* application, which subsystem owns which
seconds, and which of the obvious moves are already made, already impossible, or actively
harmful. Several of the tempting ones are in the last category and the reasons are structural
rather than a matter of taste.

Written 2026-09-09. Where a number appears it was measured and is dated; where nothing has
been measured this file says **unmeasured** rather than guessing, because a performance claim
nobody timed is the same defect as an unconverged stress.

---

## Rule 0 — measure, then change one thing

The instrumentation already exists and almost nothing uses it. `app/observe/catalogue.py`
declares every timed site in the application, and each one records fields, not just a
duration:

| Span | What it records |
|---|---|
| `mesh.gmsh.wait` / `mesh.gmsh.session` | **Waiting for the gmsh lock, separately from holding it.** This one distinction answers "is meshing slow, or is meshing *queued*", and they need opposite fixes. |
| `solve.linear_static` / `solve.plane` / `solve.conduction` | `nodes`, `elements`, `degrees_of_freedom` — so a duration is comparable between two runs of different size |
| `solve.calculix.run` | `degrees_of_freedom`, `threads`, `returncode` — the federated path, across the subprocess boundary |
| `jobs.wait` / `jobs.run` | Queue depth as a number rather than a feeling |
| `kernel.rebuild` / `kernel.measure` | `operations`, `faces`, `solids`; and `detail`, `paths` |
| `media.write` / `media.read` / `media.verify` | `bytes`, `chunks`, `deduplicated` |

**A change with no before-and-after from these spans is not a performance change, it is a
rewrite with a hopeful commit message.** Record both numbers in the commit, the way a status
line records a test.

**Change one thing at a time**, for the reason the break-run discipline exists: two changes
landing together means neither is attributable, and the one that made it slower hides behind
the one that made it faster.

---

## 1. Where the time actually goes

In rough order of seconds spent by a real user, on the hardware this product runs on.

### The model, and it is not close

A single agent turn against the local model costs **4–7 minutes** on the Windows workstation
(measured 2026-09-05, `qwen3.5:9b`, 81% resident on an 8 GB card at `num_ctx=32768`). Every
other subsystem in this list is seconds. **If the product feels slow to a user, this is why**,
and no amount of threading, caching or SQL tuning touches it.

What moves it, in order of effect:

1. **The model itself.** `qwen3-coder:30b` was measured at 22 GB on an 8 GB card — 72% on the
   CPU — and took two and a half minutes to answer with a single word. The 9b returns a
   correct structured `tool_call` in 8–15 s with the full tool payload. Choosing the model is
   the single largest performance decision in the product.
2. **GPU residency.** `OLLAMA_FLASH_ATTENTION=1` and `OLLAMA_KV_CACHE_TYPE=q8_0` are what get
   the 9b to 81% on the card. Dropping the context window to 16k only reaches 86% — **the
   weights are the bulk, not the KV cache** — so the full window is kept. Check with
   `ollama ps` and `nvidia-smi` before drawing any conclusion from a timing; a gate run that
   was actually on the CPU measures patience, not the product.
3. **How many tools it is offered.** On `GEOMETRY_BACKEND=occt` the agent is offered the 108
   implemented operations rather than all 201. That is correctness first — it cannot pick a
   tool that will fail — but it is also prompt bytes, and prompt bytes are latency.
4. **How many turns.** A turn saved is minutes. This is why `app/ai/resume.py` reads
   `CatiaOperation` rather than the transcript, and why a refusal that names what to do
   instead is worth more than a refusal that is merely correct.

### Meshing

`app/mesh/gmsh_mesher.py` through `gmsh_session`. gmsh is a **process-global, non-thread-safe
singleton**, so the whole model build is serialised on `_GMSH_LOCK`. The lock is the
correctness floor, not a tuning knob — remove it and two concurrent requests corrupt each
other's model.

What is already done and must not be undone: blobs are meshed **in place** (no staging copy,
however large the part), staged by **hard link** where the bytes need no change, and
`check_mesh_request` runs *before* gmsh rather than after, because the post-mesh element check
only fires once the machine has already paid for the mesh.

### Solving

Direct sparse solve (`spsolve`, SuperLU) up to `_ITERATIVE_THRESHOLD_DOF = 100_000` degrees of
freedom, then CG with an ILU preconditioner. The crossover is documented in
`app/solve/linear_static.py` and the reasoning is **memory, not speed**: direct is O(n^1.5) in
memory, CG is O(n) with a good preconditioner. On real parts this is the wall you hit first.

Two costs that are chosen rather than incurred:

* **Element order.** tet10 is far more accurate in bending at the same element count and costs
  roughly **2.5× the degrees of freedom and solve time**. That is a physics decision, not a
  performance one — see §4.
* **A convergence study.** `grids: n` costs about **1.4^(3·(n−1))** times the single run — 5
  grids is already 64×, which is why it is capped there. The requested size is the *coarsest*
  grid deliberately, so a study can cost more time and never more memory.

### The database

**~250 ms per round trip on Neon; ~0.14 ms locally** (measured 2026-09-07/08). That ratio is
the entire reason the test suite went from four minutes of near-pure latency to well under
one. The lesson generalises past the tests: on a remote database, *the number of round trips
is the cost*, and a loop that queries per row is 1,800× worse than the same loop locally
before it is anything else.

### Geometry and measurement

`kernel.measure` is an integration over the whole shape. `Detail` exists **for latency, not
for taste** — at 10⁵ operations, measuring *is* the run rather than a detail of it, which is
why `OcctRunner` takes a detail level and a bulk replay should lower it. Volume properties are
computed once and read three times (volume, centre of mass, inertia) where the naive version
integrates three times for the same answer.

### Rendering, retrieval, media

* Rendering is hidden-line removal — arithmetic, no GPU, deterministic. Its digest is the
  ETag, so a browser polling during a build gets 304s until the geometry actually moves.
* Retrieval is BM25 over a prebuilt index: lexical, no embedding model to load. The CATIA KB
  ships **in code, not in an index**, so it is always present and costs nothing to open.
* Media is content-addressed and streamed: nothing loads a whole file into memory, not
  writing, not hashing, not serving, and identical bytes are stored once.

---

## 2. Levers that are real, cheapest first

1. **Lower `Detail` on any path that does not need the full measurement.** Free, immediate,
   and it is what the parameter is for.
2. **Stop paying the round trip.** Batch DB work; never query per row in a loop; and remember
   that background jobs own their own session, so a job that leaks queries pays for them in a
   different connection nobody is watching.
3. **Pin BLAS threads per worker** (`OMP_NUM_THREADS`). numpy/scipy's BLAS is *already*
   threaded, so `job_workers × BLAS threads` requested against a fixed core count is
   oversubscription — see §3.
4. **Process-level parallelism behind the `JobQueue` seam.** `ThreadPoolJobQueue` is
   deliberately a seam, not a destination: separate processes dodge the GIL *and* the gmsh
   lock *and* give per-job memory bounds. **Read `app/jobs/queue.py`'s docstring first** — a
   `CeleryJobQueue` used to live there that looked for an attribute no caller ever set and, on
   the `except` branch, ran the job inline, silently moving four minutes of FEA onto the
   request thread. The config validator now refuses that value outright.
5. **Reuse a factorisation** where several load cases share one stiffness matrix. Unmeasured
   here and structurally sound: the factorisation is the expensive half of a direct solve.
6. **Shorten the agent's path to the tool call** — fewer offered tools, fewer turns, shorter
   frozen prompts. Minutes, not milliseconds. Measure with the model on the GPU or not at all.

---

## 3. Things that look like speedups and are not

* **"Use more threads."** Almost nothing here waits. Requests are sync `def`, so FastAPI
  already runs them in a worker threadpool; jobs already run in a pool
  (`settings.job_workers`); BLAS is already threaded inside `spsolve`. What is left is
  serialised by something outside this process — the gmsh lock, **CATIA's COM surface (one
  in-flight call per device)**, OCCT's per-document runner — or is GPU-bound in the model.
  Adding threads to those queues the same work behind the same lock.
* **Running two big solves in one process.** FEA is RAM-bound before it is CPU-bound.
  `max_elements` exists because of this, and two concurrent direct solves is how a box OOMs
  rather than how it goes faster.
* **An async rewrite of the routes.** The request path is not I/O-starved; the work is in a
  background job by design, precisely so a request does not hold a connection for four
  minutes. Converting sync handlers to `async def` moves them *out* of the threadpool and onto
  the event loop, where one blocking call freezes every request in the process.
* **Caching a result that is cheap to recompute and expensive to invalidate.** The AI
  interpretation is generated fresh on purpose: it derives from an immutable result row, so
  there is nothing to invalidate and no stale copy to serve. Adding a cache there buys
  milliseconds and costs a class of bug.
* **Parallelising gmsh.** It is a singleton with global state. This is not a lock to optimise
  away; it is the reason the meshes are correct.
* **Dropping the equilibrium residual check.** It is a matrix-vector product against a solve.
  It is also the only thing standing between an under-constrained model and a finite,
  meaningless answer — SuperLU returns one happily.

---

## 4. What must never be traded for speed

This project's whole claim is Decision 3: verification is the product. Three "optimisations"
would each buy real seconds and each one breaks that.

1. **Coarsening the mesh or dropping to tet4 silently.** That does not make the answer faster;
   it makes it a different answer. If a run is coarse, the result must say so — which is what
   `mesh_convergence` defaulting to `single-grid` is for.
2. **Skipping the convergence study because it costs 64×.** An unconverged number is worse
   than no number. The honest cheap option is one grid *stated as one grid*, never three grids'
   confidence at one grid's price.
3. **Sampling where the code says measured.** Thickness and undercut are already upper bounds
   from a finite ray set and they say so. Widening that to save time, without moving the
   provenance basis from `measured` to `approximated`, converts a slow honest answer into a
   fast dishonest one.

---

## 5. If you do change something

* Record the before and after from the relevant `app/observe` span, with `nodes` /
  `degrees_of_freedom` / `bytes` beside the duration so the two runs are comparable.
* Say in the commit message which subsystem you moved and by how much. "Faster" is not a
  measurement.
* If the change touches `app/solve/`, `app/mesh/` or a fingerprinted `app/verify/` module,
  **re-record the validation artefact** (`venv/bin/python -m app.verify.recorded`) — the
  recorded outcomes carry a hash of every file that decides an answer, and a performance change
  is still a change to a file that decides an answer.
* If it changes what a number *means* rather than how fast it arrives, it is not a performance
  change and belongs in the master plan as a task with its own status line.
