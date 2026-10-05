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

A single agent turn costs **4–7 minutes** (measured 2026-09-05 on a local 9B model; the
product has since moved to hosted models only, 2026-10-04, and **no turn has yet been timed
on one** — the first gate run with a key should record the per-step `agent step … prompt tokens`
line before anyone draws a conclusion). Every other subsystem in this list is seconds. **If the
product feels slow to a user, this is why**, and no amount of threading, caching or SQL tuning
touches it.

What moves it, in order of effect:

1. **Whether the step reasons.** A reasoning step adds thousands of billed output tokens and tens
   of seconds, and a turn is ~20 steps. Agent steps run with thinking off by default
   (`AI_EFFORT_CHAT=low`); structured output and tool-less chat never reason. Raise it to buy
   judgement, and measure that it bought any.
2. **A byte-stable prefix.** The system prompt, the ~235 tool schemas (~58k tokens when every
   tool is offered) and the earlier transcript are resent every step. A hosted API bills a prefix
   it has seen at a small fraction of a fresh one, so nothing that varies per turn may sit ahead
   of the state block. `tests/test_prompt_cache_stability.py` pins it.
3. **How many tools it is offered.** (`AI_TOOL_LIMIT` retrieval, off by default, would offer
   ~22–38 % of the schema bytes — measured offline at limits 40–80 — but changes what the model
   sees, so it waits for a gate run that compares accuracy.) On `GEOMETRY_BACKEND=occt` the agent is offered the 108
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

### Memory, measured 2026-10-05 (ROAD_TO_10 6.3)

Peak resident memory of one in-house solve on a tet10 box, **each size in its own process** (Linux
x86-64, CPython 3.12, 12 logical cores) -- the table `app/simulation/memory.py` fits:

| path | degrees of freedom | peak above the mesh's own |
|---|---|---|
| direct (SuperLU) | 4,131 | 47 MB |
| direct | 12,675 | 257 MB |
| direct | 28,611 | 692 MB |
| direct | 54,243 | 1,912 MB |
| iterative (CG, Jacobi) | 143,811 | 1,465 MB |
| iterative | 212,355 | 2,185 MB |

The direct path is super-linear (fit: `50 + 1.6e-4 * DOF^1.5` MB) and is the reason a solve just
under `ITERATIVE_THRESHOLD_DOF` (100,000) needs about **5 GB** while one just *over* it needs 1.1 GB:
the estimate is deliberately not monotonic across the threshold, because the iterative method is
the cheaper one. **CalculiX's memory was not measured**; the in-house figure stands in for it and
is labelled so. The estimate is for planning an admission, never a bound.

### Cold start, measured 2026-10-05 (ROAD_TO_10 6.7)

First import in a fresh interpreter: OCP **2.2 s / 357 MB**, numpy + scipy.sparse.linalg 0.35 s,
gmsh import + initialise 0.16 s, the first BM25 search 0.06 s (a second one 1 ms). Only OCP is worth
warming, and it is done on a background thread so the boot and `/health` never wait for it.

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
   frozen prompts. Minutes, not milliseconds. Measure with the model on the hosted model or not at
   all. The biggest single instance is `build_design` (ROAD_TO_10 1.13): a recorded design is built by
   one tool call, so a twenty-feature part is one model step and one resend of the transcript, not
   twenty. It is written and proved on the open kernel; whether the model *uses* it, and whether a
   seat survives a long build, are THE QUEUE H13.

---

## 3. Things that look like speedups and are not

* **"Use more threads."** Almost nothing here waits. Requests are sync `def`, so FastAPI
  already runs them in a worker threadpool; jobs already run in a pool
  (`settings.job_workers`); BLAS is already threaded inside `spsolve`. What is left is
  serialised by something outside this process — the gmsh lock, **CATIA's COM surface (one
  in-flight call per device)**, OCCT's per-document runner — or is GPU-bound in the model.
  Adding threads to those queues the same work behind the same lock.
* **Running one step's independent read-only tool calls concurrently** (ROAD_TO_10 1.12,
  measured 2026-10-04). The premise is true — `stream_agent` runs a step's calls one after
  another — and for the lookups the saving is not there. Nine of them were timed against the local
  database: `list_projects` 0.5 ms, `get_project` 1.7, `list_simulations` 0.8, `search_documentation`
  0.7 (with no index built, so a floor), `estimate_cost` 1.4, `design_history` 1.4, `list_materials`
  and `explain_catia_term` 0.01; overlapping *k* of them saves about *(k−1)* × 1 ms against a model
  step of seconds. **Not timed**, and the only candidates that could take real time:
  `draft_load_case` (one model call), `assess_fatigue`, `check_part` and `wait_for_simulation`
  (which polls a job). It is unsafe for all of them, for two reasons that each stand alone: a
  `ToolBox` is bound to **one SQLAlchemy `Session`, which is not thread-safe**, and every one of
  those reads it (`draft_load_case` queries the project and the geometry version,
  `assess_fatigue` builds a `MediaService` on it, `check_part` goes through `_call_catia` and so the
  document binding, `wait_for_simulation` re-reads the job each poll);
  and **`Tool.mutating` is the confirmation gate, not a read/write flag** — `create_project` and
  `update_project` are `mutating=False` on purpose (reversible, so ungated) and write to the
  database, so a scheme that parallelised "the non-mutating calls" would run a write beside a read
  of the same project. The 22 read-only `catia_*` tools are serialised by the bridge's
  one-call-per-device turnstile whatever the loop does. What *would* move wall time is fewer model
  steps (1.13). Reopen only on a live `turn_metrics` showing steps that spend their time in one of
  the four tools above, and then give that tool its own session rather than sharing this one.
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
