# Kryova — Build Plan

The short-term working queue: **one phase at a time, green before the next.**
[KRYOVA_MASTER_PLAN.md](KRYOVA_MASTER_PLAN.md) is the controlling document and carries the
phase status board (Part 2) — that board is *current state*. This file is the *history*:
one line lands in **Done** for every board change that is not `not started`.

This file was created on 2026-09-05, after `CLAUDE.md` and the master plan had both been
referring to it for some time and it did not exist. Everything before that date is
reconstructed from the board and `git log`, and is marked as such — a history that says
where it came from is worth more than one that quietly implies it was written as it
happened.

---

## Now

> **Blocking gap found 2026-09-05, before the first Windows verification session:
> nothing built in Era I–II is reachable from the product.** `OcctRunner` is constructed
> only inside `app/kernel/` and its tests — `app/catia/dispatch.py` has no backend
> selection at all and goes straight to the CATIA bridge. So the entire OCCT kernel (E1,
> E2, 108 operations) can only be driven by a test, and **the agent cannot build geometry
> without a CATIA licence**, which is the exact opposite of Decision 1. `app/render/`,
> `app/ai/vision.py`, `app/design/machine_checks.py` and `app/design/sensitivity.py` have
> **zero** callers outside tests: no route, no service, no UI. A grep of `app/api/` for any
> of them returns nothing.
>
> Consequence for verification: on the Windows seat, "try what was built" currently means
> *run pytest*. The product itself looks exactly as it did before Era I began. That is a
> real result and not a small one — it says the phases are green on capability and have
> never been connected to anything.
>
> **Steps 1 and 2 of 3 landed the same day.** `GEOMETRY_BACKEND=occt` routes tool calls to
> `OcctRunner` in-process: the agent builds geometry with no seat and no licence, and a
> 60×40×20 pad measures 48000 mm³ through the real dispatch path, and
> `GET /kernel/conversations/{id}/render` + `/measure` let anyone *see* and measure the
> part a conversation is building. Still to do: **(3)** the frontend surface (E4.4 and
> part of P5) — and callers for vision / machine_checks / sensitivity.


**E5 — Assertions and self-correction.** Foundation 2026-09-04; **5.1, 5.3 and 5.4 all
landed 2026-09-05**. 5.4 did not in fact need E18's missions to exist first, which is what
the queue had assumed: what it needed was the *harness*, and M1 already built. So the
ladder now runs — 1/9 rungs green, 8 declared PENDING with the phase each waits on — and
E18 gains a rung by giving its `Mission` a spec and assertions rather than by starting from
nothing. **All that remains under E5 is 5.2**, requirement-bound assertions, genuinely
blocked on Phase 11: "meets REQ-014" needs REQ-014 to exist as an object. E5 therefore
keeps a bare number rather than a star.

**E4 is done except 4.4** (renders into the conversation), which Product Track P5 owns.
`app/render/` renders eight canonical views deterministically, cuts sections and diffs two
of them; `app/ai/vision.py` asks a vision model whether the part matches the request. 4.4 is
the only part of E4 outstanding and the only one blocked outside E4.

**E2 closed on 2026-09-05** — `*E2`, Proof green. Its capability list and its Proof are
both done; what remains under it are refusals with stated reasons, each raised where it
happens, none of which block the star:

- OCCT's own limits: `continuity='curvature'` on a fill (`BRepFill_Filling` answers "the
  continuity is not G0 G1 or G2"), a loft `closed` along a spine, more than one guide;
- `catia_extrapolate` on a freeform face (OCCT's `ExtendSurfByLength` is inert through
  these bindings) and with `up_to` (an iterative solve — extend generously and cut back
  with `catia_split`);
- `catia_draft` in reflect-line mode: the silhouette exists, but OCCT's draft takes a
  neutral *plane* where the mode pivots about a curve on the face, so it means building a
  ruled surface and replacing the face — surfacing work, not an argument;
- a reflect line at an angle other than 90° (the iso-angle contour is marched face by face
  and comes back sampled);
- a thin-walled `catia_rib`/`catia_slot`, `control="reference_surface"` on a swept
  feature, and the surface half of `catia_thickness`.

**One vocabulary decision is open and deliberately unmade**: the bare word `vertical`
matches a vertical bore's **seam**. A seam is a fact about the parameterisation — nothing
meets there and OCCT will not fillet it — so a design that rounds "the vertical edges"
gains an edge the day somebody drills a hole. But `boss#vertical` naming a cylinder's seam
is how `catia_measure_item` reports a boss's height today, and seven tests rest on
behaviour around it. Narrowing the word is a decision about what the vocabulary *means*,
not a bug fix, and it wants making on purpose rather than in passing.

**Testing moves to the Windows seat.** The user has a Windows machine with CATIA and the
bridge, and asked (2026-09-05) that the suites be run there against the real application
rather than repeatedly here. Offline work continues to be written to be testable; the
verification pass happens on the seat. That also unblocks the two things this machine
never could do — the CATIA-seat halves of E1's and E3's conformance runs.

## Next

> **Working arrangement changed 2026-09-06: coding runs in stretches, verification happens at
> gates.** The master plan's new *Stop gates* section (Part 2) names six of them and says which
> phase opens each. Inside a stretch the testing is `pytest`, `ruff` and `mypy`, and **Ollama is
> stopped** so the card is free; at a gate the whole product is driven once — real chat endpoint,
> real seat, both pictures, dated report. **The current stretch is E6, and it opens gate G1**,
> which carries rung 3 forward and adds the first load-bearing prompt. The reason is measured:
> one chatbot prompt costs four to seven minutes on this hardware, and running one after every
> commit spends the night on the model instead of on the product — while the seven defects found
> on 2026-09-05 show the gate itself cannot be skipped, only batched.


**E6 — The solver federation** is now *in progress* rather than next: the deck writer landed 2026-09-05. What is left of it is the largest single thing standing. CalculiX
across a subprocess boundary (Decision 4: GPL solvers are invoked as separate processes,
never linked), with the `Solver` ABC unchanged and the existing `loads.py`/`selection.py`
vocabulary mapped onto CalculiX sets rather than rewritten — that surface is what the agent
drives. It is also what turns three of 5.1's checks from honestly-unmeasured into measured,
and what 5.3's sensitivity can then be run over.

---

## Done

- **P9.1 — CI that is honest about the database (2026-09-06).** Backend CI split into `lint` / `offline` / `database`: the offline half (3,327 tests) gates every push and reports the 430 it deliberately skipped, the database half runs against a PostgreSQL 17 service container with `alembic upgrade head` + `alembic check`, and `scripts/pytest_split.py --database-only` refuses to start without a real PostgreSQL so conftest's SQLite fallback can never again be reported as the database suite. Fixed two things that were red on main: `mypy app tests` aborted on a duplicate module name and had been checking nothing, and `.env.example` was missing `AI_TOOL_LIMIT` / `AI_DAILY_TOKEN_BUDGET`. Frontend CI already existed (the plan was wrong about that) and was upgraded: SHA-pinned actions, `.nvmrc`, and a three-dependency guard. Decided against a tagged MSI release — the installer bakes build-machine paths, so it would ship broken.

Newest first. Each line names the board row it moved and the commit that moved it.

- **2026-09-06** — E6 → **the solver federation is reachable from the product.**
  `app/solve/calculix/` had been able to solve a real model since earlier that
  day and **nothing could ask it to**: `simulation/runner.py` constructed
  `LinearStaticSolver()` directly and the route wrote that class's name onto the
  job row as a constant, so the row named a solver nobody had consulted. That is
  the same failure this file records against the OCCT kernel on 2026-09-05 — an
  era of work green on capability and wired to nothing — and it is worth naming
  as a pattern rather than fixing twice in silence. `app/solve/registry.py` +
  `SOLVER_BACKEND`, never chosen automatically (a result computed by a solver
  nobody selected cannot be relied on), with the CalculiX import lazy and
  asserted lazy. The runner now records the solver that **ran** and its version:
  `linear-static 0.2.0+<sha>` or `calculix 2.23`, read from `ccx -v` — which
  exits **201**, its generic "did not run a job" code, so the text is read and
  the return code ignored. Both routes measured against closed form through the
  setting: σ = F/A to 5.7e-16. Five mutations of the guards, all caught.
  Migration `90957dafff41`; `alembic check` clean.

- **2026-09-06 (early hours)** — **six test-writing agents were killed by a
  session rate limit part-way through**, and each had already found a defect
  worth recording before it died: E10's optimiser reports an active constraint
  at the optimum as `NO_FEASIBLE_POINT`; E9's clearance check short-circuits in
  a way its own docstring does not admit; E13's `vocabulary.py` was written
  against a rule-engine module that does not exist. Their partial edits are on
  disk and two of them left the tree failing type-check — `app/documents/readers.py`
  reused a loop variable at two different types, and `app/manufacture/layout.py`
  passed a `leader_deg` argument to a `Dimension` that had no such field, the
  agent having been cut off between the call site and the definition. Both fixed
  here; the three defects above are **not** fixed and are the next session's
  first work, along with the tests those agents were writing.

- **2026-09-06** — **Gate G1 run five times. It did not pass, and produced four
  fixes.** Rung 3 — measure and correct to 2.4 kg — failed five ways, and every
  one left a plausible number on a wrong part, which is why none of them was
  catchable without looking at the render. (1) The part came apart into **five
  disconnected solids** — a plate with four loose posts standing in its bore —
  and the mass landed inside the user's 20 g tolerance *by coincidence*
  (`32bebe1`). (2) Told to resize, the agent was refused with *"needs a sketch to
  build from"*, so it supplied one and built a **second pad**; the refusal was
  correct and its message answered a different question from the one being asked
  (`d5e94bf`). (3) A sketch drawn with four circles in it and **never pocketed**,
  plus the same sketch padded three times, both silent (`41a742e`). (4) Two of
  four holes **cut nothing at all** — `BRepAlgoAPI_Cut` returns the target
  unchanged when tool and target do not overlap, and `IsDone()` is true
  (`01bfccc`). (5) The advice naming `catia_set_parameter` omitted the `unit` it
  requires, costing two calls (`2556228`).
  **The headline is attempt 4: the agent used `catia_set_parameter` for the first
  time in four sessions, because the refusal message told it to** — and the
  correction loop then ran properly, 10.11 → 10.24 → 11.24 mm, converging to
  within 0.8 g with all holes cut and one pad instead of three. Also measured and
  worth keeping: **Ollama cannot run on this 8 GB card** (30.5B at Q4_K_M is
  ~18 GB; dropping the context 8× moves the split only 28% → 32%), and
  **Qwen3.5-9B, which fits at 76% GPU, was slower and worse** than the 30B at
  72% CPU — it misplaced every hole, re-padded the bore sketch turning the hole
  into a boss, and gave up. Five independent 2026 benchmarks name it the best
  8 GB-tier model; for a 40-tool payload with interdependent geometric state they
  are wrong. Report and eight renders in `docs/verification-2026-09-06/`.

- **2026-09-06** — E7/E9/E10/E12/E13/E17/P4 → **~13,000 lines landed across seven
  packages, and none of them has tests.** Written in parallel by agents assigned
  by file; every one imports, ruff and mypy are clean over 297 source files, and
  the 3,580-test offline suite is green — but the agents were stopped before
  writing their own tests, so nothing here is verified and the board rows say
  **TESTS NOT WRITTEN** rather than a status that implies otherwise. Committed
  rather than discarded because considered design is worth more on a branch than
  in a lost scratch directory. The next session's first job on any of these is
  the test file, not more code. Two dependency questions were answered on the
  way: **pyLife and OpenMDAO both install and import on Python 3.14**, so E8's
  and E10's federation is real rather than a seam waiting for one. `d64ebb0`,
  `37a3bf8`.

- **2026-09-06** — **Gate G1 run, and it did not pass.** Rung 3 through the real
  chat endpoint on `qwen3-coder:30b`: *"a 200x150 steel plate, 60 mm bore, four
  12 mm holes 20 mm in from each corner, correct the thickness until it weighs
  2.4 kg."* 17 tool calls, no refusals, and the agent's answer — "11 mm,
  2.391 kg, within 20 grams" — was **two true numbers about a part that is not a
  part.** It had read "20 mm in from each corner" as a 20 mm bolt circle, which
  is *inside* the 60 mm bore, so the four little circles fell in the hole and
  came back as **four loose posts standing in the bore**: five disconnected
  solids. The volume confirms it to the digit —
  `200*150*11 - pi*30^2*11 + 4*pi*6^2*11 = 303874.515`, note the sign on the
  last term. And 2.391 kg is inside the user's 20 g tolerance **by
  coincidence.** Nothing in the run disagreed; it was caught by looking at the
  render, which is exactly what `CLAUDE.md` says a picture is for. The product
  defect — `contract.py` has always said "more than one solid means the part is
  in pieces, which is usually a defect" and *nothing acted on it* — is fixed:
  `measure()` now sets `in_pieces` and an advisory naming the likely cause, not
  a refusal, because a multi-body design is legitimate and the rule is that it
  is never silent. `tests/test_part_in_pieces.py` reproduces the gate part
  exactly and three mutations of the guard are all caught. Also recorded, and
  not ours: the model still will not use `catia_set_parameter` to change a
  dimension — it renamed `Pad.1` and padded the sketch again, leaving two pads —
  which is the third session running. And **Ollama cannot be put on this
  graphics card**: 30.5B at Q4_K_M is ~18 GB against 8151 MiB, and dropping the
  context 8x moves the split only from 28% to 32% because the card is already
  full at 6.5 GB. `qwen3.5:9b` is downloading as the model that actually fits.
  Report and four renders in `docs/verification-2026-09-06/`.

- **2026-09-06** — E6 → **CalculiX is installed and the integration met the real
  program for the first time.** `calculix_2.23_4win.zip` from dhondt.de, nothing
  vendored into the repo, `find_ccx()` resolves it with no code change. A
  10×20×60 bar at 5 kN: σ = 25.000000 MPa against F/A to 5.7e-16, δ within
  4.3e-07 of FL/AE, exit 0 in 0.03 s. tet10 through real ccx confirms
  `C3D10_MIDSIDE_ORDER = (0,1,2,3,5,4)` — a wrong permutation would have built a
  differently shaped element and returned a visibly wrong answer. `frd.describe()`
  on real output: **zero unrecognised records**, components in exactly the
  documented order. A parser written from the manual is now measured against the
  program, which is the distinction this codebase draws between a mock and a
  measurement. 6.5's oracle agrees with the in-house solver to 4.4e-09 mm and
  2.7e-13 MPa; 6.6's taxonomy matched real `*ERROR` text verbatim and
  misclassified nothing. **Three defects the real solver exposed**, none of them
  visible from reading the code: an **under-constrained model comes back exit 0,
  no `*ERROR`, no `*WARNING`, `diagnose()` returning None — and 5.4e+11 mm of
  displacement**, because CalculiX/PaStiX does not detect the singularity at all
  (the in-house solver catches this with its equilibrium residual, which the
  federated path does not have); `CcxRun.wrote_results` is not a success signal,
  because ccx echoes the mesh into the `.frd` even when it solved nothing;
  and `_ERROR_RE` is line-anchored while CalculiX wraps its messages, so
  `*ERROR in e_c3d: nonpositive jacobian` arrives without the
  `determinant in element 1` that names which one. Commits `e8877e5`, `e813f54`.

- **2026-09-06** — E7/E8/E9/E10/E11/E12/E17 → **opened, in parallel.** Seven
  phases moved off `not started` in one session by fanning the work across
  concurrent agents assigned by file rather than by topic (the approach is now a
  standing instruction in `CLAUDE.md`). Nothing here is `DONE`; each row names
  what is being built and what it rests on. The point of recording it is that a
  session which picks this file up mid-flight should not re-derive seven briefs.

- **2026-09-06** — P1 → **sessions became rows, and rotation grew the half that
  makes it worth doing.** `User.refresh_token_hash` was one hash per user. Three
  consequences, all of them live: signing in on a second device silently ended
  the session on the first (and the first found out at its next refresh, as an
  indistinguishable "Invalid refresh token"); there was no way to sign out one
  device without signing out all of them; and **a stolen token and the real one
  wrote to the same slot**, so whoever refreshed last won and nothing anywhere
  noticed that one token had been used twice. Rotation existed. The detection
  half — the half that makes rotation more than theatre — did not.
  `app/models/session.py` is now a row per device family, holding the current
  token hash *and the previous one*, with an absolute deadline rotation cannot
  extend. `app/core/sessions.py` is the state machine: a presented token matches
  the current hash (rotate), the previous hash inside a 10-second window (two
  tabs raced — serve the current token rather than rotating again, or the loser's
  next refresh looks exactly like an attack), the previous hash outside it
  (**theft: revoke the whole family**), or nothing (refused, with the same
  wording, so a refusal never tells a guesser how close they were).
  `/auth/sessions`, `/auth/sessions/{id}` and `/auth/logout-all` back P1.3's
  device list; `logout` now ends one device instead of all of them. P1.4: an
  empty `CORS_ORIGINS` in production is now refused at startup alongside the
  existing `SECRET_KEY` checks, and on a development machine
  `Settings.insecure_defaults()` is logged at every boot — the reason
  `SECRET_KEY` sat at "changeme" long enough to become a documented landmine is
  that nothing ever said so out loud. 39 tests, and the guards were verified by
  breaking them: 8 mutations, **6 caught immediately, 2 escaped and both were
  worth the finding.** Widening the grace window to a day changed nothing,
  because every theft test aged the family by `REUSE_GRACE_SECONDS + 1` and so
  followed the constant wherever it went — fixed by pinning both sides in
  engineering terms instead (a replay an hour later is theft; a replay one
  second later is a race). Revoking by row rather than by family also changed
  nothing, and that one is **honestly unpinned**: today a family is exactly one
  row, so the two are indistinguishable until the design that appends a row per
  rotation lands. Migration `6b877d045c82`; `alembic check` clean.

- **2026-09-06** — E2/E5/E16 → **rung 3 of the ladder: three more defects, and the one
  that made the rung impossible rather than hard.** Driving "the plate has to weigh 2.4 kg;
  adjust the thickness until the measured mass is within 20 grams" found, in order:
  **(1)** `catia_set_parameter` was **not implemented on the open kernel at all**
  (`b9b1cb9`), while the system prompt tells the model to prefer it over rebuilding a
  feature — so the agent had no way to change a dimension, padded the same sketch four
  times, and reported a stack of seven pads weighing 2.958 kg as the answer. A part built
  in conversation has no parameter set, so its build log is one: every mutating call is
  recorded, every numeric argument of one is a dimension addressed as `Pad.1\length_mm`,
  and setting one rewrites that call and **replays the part from the top** — `app/design/`'s
  "specification that is compiled" applied to a part assembled call by call, inheriting its
  property that a recompiled spec has no downstream edit to shatter. The replay builds into
  a *fresh* document and is swapped in only on success, so a value the geometry cannot carry
  costs a refusal and nothing else. **(2)** `Sketch.face` turned an inner profile into a
  **boss instead of a bore** (`71ac1b2`) — OCCT wants an inner wire reversed, an unreversed
  one is accepted silently and integrates as material, and the docstring had claimed the
  correct behaviour since the file was written. A 100×100 sketch with a 40 mm circle padded
  10 mm came back at 112,566 mm³ where the plate minus its bore is 87,434. Containment is
  now decided by boolean algebra in both directions, so draw order does not matter, and a
  partial overlap is refused rather than guessed at. **(3)** The parameter name was
  **untypeable** (`454d780`): the payload is JSON, so `Pad.1\length_mm` is *shown* to the
  model with the backslash escaped, it types back what it read, and all four of its calls
  were refused for punctuation. It then abandoned the loop and padded a slab over the part,
  reaching 2.401 kg — inside the tolerance asked for — with the bore and all four holes
  filled in. Every separator now folds to one key and a bare unambiguous dimension name is
  accepted. Coverage 108 → 110 of 201; 82 new tests; every guard verified by breaking it.

- **2026-09-05** — E5/E16 → **rung 2 of the ladder passes, and the part is right**
  (`docs/verification-2026-09-05-night/REPORT.md`). Not "every call returned ok": 18 calls,
  no refusals, no retries, and the finished volume and mass match the closed form to the
  last digit — 101207.4703614367 mm³ against 101207.47036143667, 0.2732601699758791 kg
  against 0.27326016997587904 — with 15 faces, one solid, and a feature list naming all
  four features. Pictures beside the report. Ollama on the GPU, confirmed resident
  mid-run at 70%/30%, 6572 MiB.
  Getting there took **three defects out of our own code, none of them visible from a
  green suite, all three found by driving the product rather than by reading it**: the
  mass cache that never noticed a material being set (`55557da`), the exact-equality rule
  that refused `solid_count == 1` (`55557da`), and the unnamed-feature collision that made
  a second pocket a regeneration of the first (`a6595ff`). Every one of them left the
  geometry correct, which is why they survived four verification runs. Not verified on a
  CATIA seat: the bridge daemon is not running and binding it to the overnight test
  account unattended is not a trade worth making — recorded in the report with the reason
  and with what can honestly be said without it.

- **2026-09-05** — E16 → **polar placement: the bolt circle could not be said at all.**
  The flange that came out wrong three times was blamed on the model twice. It was the
  vocabulary. `catia_sketch_circle` offered one way to state a position — Cartesian `at` —
  while the system prompt forbids the model from doing coordinate arithmetic, on the
  grounds that coordinate arithmetic belongs inside a tool where it can be tested. "Four
  holes on a 70 mm bolt circle, one in each corner direction" is radius 35 at 45°, which is
  (24.749, 24.749). The tool required the one thing the prompt forbade, so the model used
  the radius as a coordinate and built a 99 mm bolt circle that looked entirely plausible.
  `at_radius_mm`/`at_angle_deg` now sit beside `at` on circle, rectangle and polygon.
  The trigonometry is in one place, `app/catia/ops/placement.py`, and runs once on the
  server: the parameters are `consumed_by_server`, so `dispatch._augment` resolves them
  into `at` before either backend is reached, the workstation daemon never sees a polar
  argument, and `generated_tools.py` regenerated byte-identical. One implementation means
  one angle convention — anticlockwise from the sketch's horizontal axis, which is what
  `catia_sketch_arc` already uses; two conventions in one sketcher is how a part ends up
  mirrored with every test green. `OcctRunner` calls the same function directly, because
  the design IR and the mission ladder drive it without the dispatcher. Dispatch asks the
  schema, not a list of tool names, so declaring `*polar_placement()` on a new operation is
  the whole of the change. The prompt gained a general rule and no recipe. 43 tests,
  including the four holes landing on the circle that was asked for, through the real
  kernel, to a closed-form volume.

- **2026-09-05** — E5/E16 → **`check_part`: the agent checks its own work**, and three
  generalisations that replaced a recipe. `assertions.py` had existed since 2026-09-04 and
  could not be called from a conversation, so the only thing between "every tool returned ok"
  and "the part is right" was the model's opinion — and that gap produced a flange whose every
  call succeeded, whose bolt circle sat on a 99 mm diameter instead of 70, whose every edge was
  rounded instead of four, and which read as a complete success. `check_part` takes one claim
  per requirement and runs `check_assertions` **unchanged**, so `UNMEASURED` keeps meaning
  "nobody checked" and is said in words as well as in the structure. The measurement is the
  `catia_measure` both backends already answer, and either payload shape is read, so the caller
  never learns which backend replied. In `CORE_TOOLS`, because retrieval that could withhold
  the check would cause exactly the failure it was built to fix.

  **And the prompt work was corrected rather than extended.** A first pass had written "four
  holes on a bolt circle is exactly these three steps" into a system prompt that has to serve a
  stamping press — a recipe per shape never covers the next shape. Replaced with the four ways
  a build reports success and delivers something else: repeated features are patterned not
  placed; a dimension is read as the quantity it names (a bolt circle is a diameter); select the
  smallest group that matches, since "all" really is every edge including hole rims; and a value
  that will not build is reported, never quietly substituted. The specific knowledge moved into
  the refusal, where it generalises by construction — `Document.feature()` now tells a *sketch*
  from a nothing and names the missing step, because "No feature called 'Hole Sketch'" is true
  and sends the reader hunting for a feature that does not exist. Also corrected in the tool
  descriptions rather than the prompt: `catia_sketch_circle` was advertising `at` as the way to
  draw a bolt circle, telling the model to do the coordinate maths the prompt forbids, and the
  model obeyed the nearer instruction — that contradiction was ours. 21 tests across the two.

- **2026-09-05** — E16 → **16.1: tool retrieval, and rung 2 passes**.
  `app/ai/tool_retrieval.py`. The wall was measured here rather than read in a paper: with
  108 tools offered, the model asked for a mounting flange opened with `catia_pad` on a
  conversation holding no document, invented `profile`, invented two tools that have never
  existed, and never reached `catia_sketch_create` — on the open kernel *and* on a real
  CATIA seat, identically. With `AI_TOOL_LIMIT=40` the same request built the whole part.
  **The design decision that makes this safe is that retrieval narrows what the model is
  *shown* and never what it can *call*** — `ToolBox.schemas(only=)` filters the offer,
  `ToolBox.call` keeps every tool — so no setting of it can make a capability unreachable,
  and the worst case is a turn where the model names a tool from memory, which still works.
  Anything less would be a capability cut wearing an optimisation's clothes. Lexical, not
  embeddings, for `app/retrieval/`'s reasons: tool names are exact terms and the bilingual
  tokeniser is already built. The core modelling loop is never withheld whatever the query
  says (hiding `catia_new_part` because the user said "flange" is the measured failure from
  the other end), recently-used tools stay offered because continuity beats similarity, and
  a limit at or above the registry size is a genuine no-op rather than a reordering. Default
  is off: it changes what the model sees, so it is switched on deliberately and measured.
  **And the result is the best argument for Decision 3 this project has produced.** Every
  tool call succeeded and the part is wrong. The four holes went to (±35, ±35) — a
  bolt-circle radius of 49.5 mm where "a 70 mm bolt circle" means radius 35, the model having
  taken the number as a coordinate — and `edges="all"` rounded every edge including the bore
  and hole rims, which is exactly the 4,494 mm³ by which the measured 97,208 falls short of
  the closed-form 101,702. The render (`docs/night-2026-09-05/02-flange-top.png`) is an
  entirely plausible flange. Mechanical completion has become the easy half; nothing in the
  chat path yet checks the part against what was asked, and wiring `assertions.py` and 5.1's
  machine checks into the conversation is what 16.x owes next. 22 tests.

- **2026-09-05** — E6 → **started: the CalculiX deck**. `app/solve/calculix/deck.py`.
  Decision 2 says physics is federated rather than re-implemented and Decision 4 says how —
  GPL, so a separate process across a file/CLI boundary, never linked. The deck is the whole
  interface, and this writes it. **The loads are deliberately not re-derived**: `assemble_loads`
  already spreads a force over its region by tributary area, and the deck emits that same
  vector as `*CLOAD`. Re-deriving would duplicate the load vocabulary the master plan calls
  the real asset — and worse, it would break 6.5, where the hand-written solver is the
  *oracle*: a disagreement between two solvers only localises if both were given identical
  loads. Four traps, every one of which yields a deck CalculiX accepts and solves, each
  pinned by a test verified by breaking it. Numbering is 1-based, and an off-by-one does not
  crash — it shifts every load and restraint onto the neighbouring node and returns a
  perfectly reasonable-looking field. A C3D10's midside nodes are **not** in our order:
  `TET10_EDGES` is gmsh's type-11 ordering, Abaqus swaps the last two, so the permutation is
  `[0,1,2,3,5,4]` and the wrong one gives a valid, solvable, differently-shaped element. The
  test checks what the permutation must *achieve* against both edge tables, because asserting
  the constant equals its own literal would agree with any typo in it. Density leaves the
  mm-N-MPa system here — the one sanctioned conversion, at the boundary where the numbers
  stop being ours — into tonne/mm³, and steel's 7870 becoming 7.87e-9 is the check that it is
  right. And numbers go out at `repr` precision, since a deck rounding coordinates to six
  figures has silently re-meshed the part. Writing it also found a redundant guard: the empty
  selection was already refused by `select_nodes`, with a better message than the new one, so
  the guard became a re-raise that adds the one thing the lower layer cannot know — *which*
  fixture asked. 29 tests, 129 green across the solver suites, ruff and mypy clean. Still to
  come in 6.1: the `ccx` subprocess, the `.frd`/`.dat` parser, and the oracle comparison.

- **2026-09-05** — E5 → **5.4: the ladder becomes a suite that runs**, and E18 gets its
  harness. `app/design/missions.py`. Decision 5 lists nine machines and calls each rung a
  permanent regression test; nothing executed it, so "M1 works" was a claim from the day
  somebody last tried it by hand and there was no moment at which M1 quietly breaking would
  have been noticed. All nine rungs are declared, M1 carries a real `DesignSpec`, and its
  claims are closed forms computed from the same constants the spec is built from — a
  changed dimension moves the design and its claims together, where a hand-typed number
  stops describing the part and then fails looking like a geometry bug. M1 builds through
  the real `OcctRunner` in twelve calls and holds all eight: volume, mass, surface area,
  thickness, footprint, one solid, eleven faces, centroid at mid-thickness. The last three
  are there because **a bore that stopped short keeps the volume plausible** — only an
  independent quantity catches it. **The eight unreachable rungs are `PENDING`, never a
  pass and never a skip**, each naming the phase that owns the gap; but pending does not
  make the report red, because a suite red until M9 is a suite somebody switches off. `ok`
  is the regression question, `complete` is the programme question, and the sentence a
  human reads — "1/9 rungs pass, 8 not yet buildable" — cannot be misread as coverage.
  A rung that claims to build and does not is a **failure whatever the reason**, unlike
  `conformance.py`, which is asking a different question: there a gap says which backend is
  behind, here the mission declared it builds and an operation that regressed into
  unimplemented has falsified that. Five guards verified by breaking them; dropping the
  pending rungs flips `complete` to true, the exact false green the split prevents.
  **Writing it corrected a rationale rather than shipping it:** fillet-before-bore was
  justified as *necessary*, expecting `vertical` to catch the bore's seam. It does not —
  bore-first matches to 1e-12 on the same eleven faces. The `feature` scope is what is
  load-bearing: with a boss on the slab, scoped removes 171.68 mm³ and unscoped 386.28 mm³,
  having rounded the boss and reported success — the defect the verification found in the
  design suite's own bracket fixture. 31 tests, 743 green across design+kernel, ruff and
  mypy clean.

  Three standing rules added to `CLAUDE.md` the same day, at the user's direction: **an
  end-to-end test goes through the Ollama chatbot, never the dispatcher** (D2 and D9 are
  what a middle misses); **every CATIA result is screenshotted and looked at**, both the
  viewport via `catia_capture_view` and the whole window via the restored
  `scripts/shot.ps1`, because D11 was invisible in every viewport render; and **the chat
  prompt gets harder every session**, on a rung ladder parallel to the missions, because a
  prompt that stays at "make a plate" stops measuring anything the day it first passes.

- **2026-09-05** — **The integration gap, step 2 + two product decisions.**
  `app/api/routes/kernel.py`: `GET /kernel/conversations/{id}/render` (any canonical view,
  optional mid-axis section, hatched, the render digest as ETag) and `/measure` (payload with
  provenance) — the first callers `app/render/` ever had. OCCT-backend only, and it **refuses**
  to draw a CATIA-seat part rather than fake a picture; three distinct 409s because the
  remedies differ. The smoke run found two defects reading had not: the Face_s cast trap in
  `section_faces` (fourth occurrence of that trap), and the hatcher's `zip(..., strict=True)`
  that can never be satisfied — **hatching had never executed before this run**; its first
  output was inspected by eye and is correct. Two decisions recorded the same day, both now in
  Decision 1 / the config: **OCCT is kept, demoted to internal engine** — the only free
  industrial B-rep kernel; no customer surface, no new operations except when a test or sweep
  needs one; installs silently inside Kryova (a wheel, no executable, ~805 MB with VTK, which
  the compiled extension links directly and cannot be trimmed). And **Ollama is test-phase
  only; production is a hosted API** — with the data-flow consequence (geometry summaries go
  to the model vendor) written down where the contract discussion can find it, and the
  retrieval rationale re-based on the two arguments that survive the change.

- **2026-09-05** — **The integration gap, step 1: the agent can build without CATIA.**
  `app/geometry/backends.py` + a local branch in `dispatch.call_catia`. This is Decision 1
  made true of the *product* rather than only of the libraries — until now `OcctRunner` was
  constructed solely by tests and 108 working operations needed a licence to reach. Measured
  end to end through the real path: a 60×40×20 pad returns 48000 mm³ exactly, on Linux, with
  no seat. The seam is additive; the CATIA path keeps its validation, approval, logging and
  messages untouched, and the branch sits *after* normalisation so both backends take
  identical arguments. Three honesty rules: the offered tool list is read from the handler
  table (108, not 201) so it cannot drift; an unimplemented operation is named as a **backend
  gap**, never a geometry failure; and the backend is **never** chosen automatically, because
  a silent fallback hands you a part built by a different kernel. The module lives in
  `app/geometry/`, not `app/catia/` — filing the thing that chooses between two backends
  under one of them says the opposite of what it does.

- **2026-09-05** — E5 → **5.3: a repair that is aimed rather than guessed.**
  `app/design/sensitivity.py`. The plan's blunt version is that a validator which cannot say
  *why* is a retry counter, and that was exactly the state: assertions could say 3.1 kg over,
  `correct.py` could try something and see if the number moved, and nothing could say which of
  eleven parameters to move or how far. Finite difference per free parameter, plus `aim()` to
  turn a `gap` into a parameter and a distance. Affordable only because of Decision 1 — a probe
  was minutes of a CATIA workstation and is a headless build here. Four decisions separate a
  number from a lie, each pinned: **a failed build is not zero sensitivity** (reporting 0.0
  tells the loop to leave alone the parameter that is at its limit); **a topology change is not
  a derivative** (a fillet that swallows a face makes two different parts — counts are
  differenced too, and a payload without them is `topology_unchecked` rather than assumed);
  **only free parameters are probed**, with a derived one excluded *carrying its formula*
  rather than dropped, since absent from a ranking reads as "no influence"; and **the ranking
  is by elasticity**, because kg/mm and kg/degree cannot be compared and "which matters most"
  is meaningless until they are dimensionless. `aim` refuses rather than dividing by a
  negligible derivative, and carries its first-order caveat on every suggestion. 34 tests
  written; they run on the Windows seat.

- **2026-09-05** — E5 → **5.1: assertions for machines, not parts.**
  `app/design/machine_checks.py`. The reason it needs to exist: `assertions.py` checks a claim
  about a number already in a payload, which is right for a part and cannot express whether an
  arm clears its frame through travel or whether six tolerances still fit — **those claims must
  be produced, not read.** So a machine check is a *measurement source* that files a number
  under a path with its provenance, and the existing assertion machinery compares it. No second
  comparison language; `UNMEASURED`, `gap` and the report all come free. **Tools are injected,
  never imported**, keeping the package offline with no kernel and no solver, and a missing tool
  is `unavailable` with a reason naming what is missing — which is how 5.1 lands complete while
  the solver-backed half honestly waits on Phase 6. Eight checks. Clearance through motion is
  **sampled and says so** (a collision between two adjacent poses is invisible to it, stated in
  the note; under three samples refused as not a sweep). Stack-up carries **both** methods with
  neither silent, because worst case and RSS answer different questions and picking one quietly
  means designing to a case that never occurs or failing on the tails. A cost budget is declared
  and honestly `UNMEASURED` — Phase 13 owns cost — rather than left out of the library 5.1
  describes. 33 tests written; per the standing arrangement they run on the Windows seat.

- **2026-09-05** — E4 → **section cuts, and the upside-down renderer they found.**
  `app/render/section.py`. The vocabulary is the work: `mid_section` / `offset_section` /
  `section_named`, the normal pointing at the material that is *removed* (the convention
  `catia_split` already states — two conventions for one question is how a part ends up
  mirrored with every test green), a plane that misses the part **refused** rather than
  returning an uncut part that looks like a successful section of a solid one, and a finite
  removing box rather than `MakeHalfSpace`, whose failure mode is returning the shape
  unchanged. Cut faces found by geometry rather than boolean history, and hatched at 45° by
  the **even-odd rule across every wire at once**, so a bore falls out of the parity with
  nothing identifying it as a hole. That needs ordered wires, which HLR cannot give, so
  `face_outlines` walks with `BRepTools_WireExplorer` and asks the *wire* which way to walk
  each edge — half the edges of a rectangle are stored backwards.

  **And writing it found that 4.1 shipped upside down.** OCCT's `gp_Ax2` Y axis is
  `direction × X`, the opposite of the up vector `views.py` declares: the top of a 40 mm box
  seen from the front came back at y = −40. Nothing could see it — a consistently mirrored
  image is byte-identical to itself, a diff of two mirrored renders is still correct, and a
  plate looks plausible either way up. It is exactly the wrong-orientation error 4.1 claims a
  render hash catches. Fixed in the projection rather than the raster so view millimetres do
  not lie, and pinned by `TestTheRenderIsTheRightWayUp` in the new `tests/test_render.py`
  (37 tests) — which is also 4.1 and 4.3 finally getting the test file they never had, having
  been verified by smoke run only.

- **2026-09-05** — E4 → **4.2: the model looks at the model.** `app/ai/vision.py`, plus
  `LLMProvider.look` and the three providers that can implement it. Written around the
  phase's own stated limitation rather than in spite of it: a VLM will confidently approve
  a subtly wrong part, so `VisualReview` offers **no** `approved` or `passed` property for
  anyone to gate a release on — the flag that exists is `objected`, and a test asserts the
  others are absent. Three outcomes, and **`unchecked` is never a pass**: no vision model,
  an unreachable provider, nothing drawn, a model that says "unsure", and a model that says
  "differs" while naming nothing specific all land there with the reason in words. Nothing
  raises, on `KnowledgeService.search`'s contract — a visual check improves an answer and
  must never be why there is not one. **The trap is Ollama**, which does not refuse an image
  handed to a text-only model: it drops it and answers anyway, so the shipping default
  (`qwen2.5-coder`, no eyes) would have returned a confident description of nothing, with no
  error and no flag — a check that manufactures agreement. `_sees()` refuses on two
  structural signals and no name list: `/api/show` publishes `capabilities`, and only a
  multimodal model has a `projector_info` block at all. `AI_VISION_MODEL` names the model
  that looks, since locally it is a second pull. Images are unlabelled on the wire, so order
  is the only thing tying one to what it is a picture of — the prompt names the order and
  the code sends them in it. `num_ctx` is sized for the pictures too, because Ollama
  truncates from the front in silence. The schema puts `describes` before `verdict`, so a
  constrained decoder must say what it sees before it judges. 30 offline tests; four guards
  verified by breaking them (the blind-model refusal, the unsure fold, the blank-render
  short circuit, the unlocatable-complaint fold).

- **2026-09-05** — E4 → **4.1 and 4.3: the system can look at the model.** `app/render/`,
  five modules, no new dependency. **Hidden-line removal rather than OpenGL**, and that is
  the phase's requirement rather than a shortcut: OCP exposes the GL viewer and it comes
  up on this machine, but 4.1 wants two renders of the same geometry to be byte-identical
  so that a render hash can join mass and plan-digest as a third identity check — and a GL
  image depends on the driver, the sampling and the display server, on a project that
  develops on Linux and ships on Windows. HLR is arithmetic; the raster under it is
  integer. Eight views, each from three HLR streams per side, because taking only the
  sharp edges loses every curved silhouette. The same shape renders identically twice, a
  part rebuilt from scratch matches, a part with a pocket differs. Framing is a value, not
  a step: `render_views` fits one frame over every view so a sheet is at one scale, and
  `render_pair` puts two parts through one frame, which is the whole of what makes a diff
  mean anything. **4.3 diffs ink rather than shade** — a line that went from hidden to
  visible has not moved — with added and removed in separate colours, and refuses two
  renders that were framed differently rather than reporting the framing as the change.
  Measured: a plate gaining a Ø14 pocket is 321 pixels arrived, 0 gone, 3.3% of the ink.

- **2026-09-05** — **`*E2` — the phase Proof, written and green.** A 60×40×20 plate whose
  four vertical corners carry 2, 3, 4 and 5 mm — one call, edges chosen by predicate,
  radii matched to the selection order — compiled from a `DesignSpec` and run through the
  real `OcctRunner`; then the *same spec* with a through-notch inserted ahead of the
  fillets, recompiled and rebuilt from nothing. Volume exact against
  `blank − Σh·r²(1−π/4) − notch` both times; the corners come back with the radii the
  design gave them. The renumbering is measured rather than assumed — the plate's vertical
  edges move from 0, 1, 4, 7 to 5, 7, 19, 23 and the design still finds them.
  **Running it found four disagreements between layers that were each right alone**, none
  of which any existing test could see: `catia_fillet.radius_mm` declared a number so the
  kernel's per-edge list was unreachable from a spec (`feature_length_per_entity`);
  `catia_fillet.feature` **declared and silently dropped**, so the design suite's own
  bracket fixture was rounding every vertical edge on the part and reporting success
  (`_scoped_selector`); `Document.feature` looking up only the build name while a compiled
  design renames everything to its own, which made `feature#selector` invisible to an
  authored part; and `topology.shape_list` refusing any list longer than two on the belief
  that OCP had no iterator — it does, and a slot cut *through* a part turns one face into
  five, so an ordinary notch was unbuildable. Left deliberately unmade: whether the bare
  word `vertical` should stop matching a cylinder's seam. 2021 offline tests green, ruff
  clean, mypy clean.

- **2026-09-05** — E2 → **2.6's last capability: a run of boundary**. `catia_boundary`
  takes `limit_from` (where the run starts — an element, and the boundary edge nearest it
  is the seed), `limit_to` (where it stops) and `propagation`. The walk goes outward from
  the seed in both directions and is kept in **connection order** rather than collected as
  a set, because `limit_to` has to cut it and because anything sweeping along it needs to
  know which edge follows which. Verified on a sheet whose boundary is line → arc → line
  all tangent, with creases at the ends: tangency picks out exactly those three edges
  (25 + 15 + πr/2, exact) where point continuity takes the whole 115.708 mm loop, and
  stopping on the arc gives 25 + πr/2. Two cases refused rather than guessed, both for the
  reason `catia_split` gives about which side of a cut survives — a `limit_to` on a run
  that closes into a **loop** (two ways round, nothing chooses), and a **branch vertex**
  where three free edges meet, verified on three blades sharing a root edge where point
  continuity from one tip returns 50 mm and not a millimetre of the other two. Endpoints
  are matched on a micron grid rather than by `IsSame`, because
  `ShapeAnalysis_FreeBounds` rebuilds the boundary and a corner comes back as two vertices
  that are equal to within tolerance and identical to nothing. Six guards, each verified
  by breaking it; the branch stop did not bite until test geometry with a real branch
  existed, which is why the vane is in there. **This closes 2.6's capability list and
  opens the honest question the board now carries: E2's Proof has never been written.**

- **2026-09-05** — E2 → **2.6: `catia_extrapolate`, the last operation the phase owed**.
  Coverage 107 → **108/201**. Three cases, and the route this file recommended for them
  was wrong: `GeomLib::ExtendCurveToPoint` and `ExtendSurfByLength` are OCCT's own answer
  and are **inert through OCP**, which passes their `Handle(Geom_...)&` by value — each
  builds the extension and drops it, silently, with bounds and poles and type unchanged
  afterwards. `test_occts_own_extenders_do_nothing_through_these_bindings` measures that,
  so if a future OCP fixes it the claim in the code fails rather than quietly rotting.
  What runs instead is **widening the parameter range**, which for a conic or an analytic
  surface *is* the extension — a quarter of a Ø20 circle extended 5 mm is 5 mm more of
  that circle, no join, exact — with `GCPnts_AbscissaPoint` turning a length into the
  right parameter step (a circle's parameter is an angle and an ellipse's is neither).
  Where the basis stops at its own end, a curve gets a real piece built from its end
  conditions: a straight segment for `tangent`, an **arc of the osculating circle** for
  `curvature`, swept by `length/radius` so it is G2 and exactly the length asked for by
  construction. Analytic faces widen too, gated on the parameter running at **one speed
  along the boundary** — so a cone extends along its slant to the frustum formula and is
  refused around its axis quoting both speeds, 5 mm per unit at one end of that edge and
  20 at the other. Which end of a curve moves is the one facing what `boundary` names, for
  the reason `catia_curve_connect` states; on a face, `boundary` must pick out exactly one
  of four sides, and **being past a bound beats lying on one** — a point above a sheet sits
  exactly on a side edge's extended line when its x happens to be 0, and reading that as
  "the u edge" widened the face sideways and reported success. Seven guards, each verified
  by breaking it and watching a named test fail; the seventh (the osculating circle's
  frame) did *not* bite at first — a backwards arc is exactly as long as a forwards one —
  so the test now measures where the extension reached, not only how long it is.
  `curve_chain`/`curve_ends`/`CurveEnd` promoted out of `curves.py`'s privates, since
  "which end is the end" must have one answer. Two stale refusals corrected: `boundary`'s
  `limit_*` no longer says "once catia_split lands" (it landed the same day), and the
  module docstring's list of what is missing is current again.

- **2026-09-05** — housekeeping, no board row: **mypy is clean, and CLAUDE.md no longer lists
  errors to expect.** The seven carried in `app/solve/` were two real defects wearing a
  type-checker's clothes. `_bearing` asked `hasattr(where, "axis_point")` — which accepts
  anything that later grows the attribute and tells neither the reader nor mypy which selector
  a bearing load actually needs; it now tests `isinstance(..., CylinderSelector)`. And
  `Fixture.dofs` is `list | None` only at the boundary, since None is how "not given" is
  spelled in a request and `_resolve_dofs` fills it before any solver runs — three assembly
  routines each assumed that silently, so the invariant is now written once as `Fixture.held`.
  Both verified by breaking them: inverting the selector test fails
  `test_it_refuses_a_non_cylindrical_region` and both bearing-distribution tests; making
  `held` return all three axes fails `test_a_roller_really_does_let_the_face_slide` and nine
  others. 988 offline tests green. Frontend `eslint.config.mjs` also ignores `.remember/**` —
  flat config does not skip dot-directories the way eslintrc did, so a hook writing a bare
  timestamp into a file named `last-ndc.ts` was being linted as our source.

- **2026-09-05** — E2 → **2.6 continues: propagation and sewing**. `catia_sew_surface`
  (coverage 106 → **107/201**) and `propagation` on `catia_extract`. Tangent propagation is
  what makes "the rounded end of this part" a selection instead of an enumeration, and the
  one thing that had to be got right is **where tangency is measured**: at the shared edge,
  not between the faces' own normals. A fillet's normal at its parametric centre is 45°
  from the flat face it runs into, so `classify.edge_is_convex` — which uses centre normals
  and is right about the question it answers — calls every fillet a sharp corner here.
  Verified on a flared post whose base, quarter-round fillet and wall all meet smoothly and
  whose top rim does not: tangent propagation returns base + fillet + wall to 1e-16 against
  Pappus, point continuity adds the top disc. `catia_sew_surface` trims a solid to a
  surface, reusing `catia_split`'s stated side rule; `remove` and `reversed` each flip it
  and compose. A surface clear of the part is refused with the reason, because that is the
  case CATIA answers by *adding* material. Six guards, all six verified by breaking them —
  a seventh (three tangency samples per edge rather than one) was **removed from the
  harness and labelled in the code as unpinned**, because every analytic pair of surfaces
  that meets tangentially does so along the whole edge and nothing in the suite can tell
  one sample from three. Flagged, not fixed: `block#vertical` handed to
  `catia_fillet_edges` rounds all twelve edges of a box, not the four vertical ones.

- **2026-09-05** — E2 → **2.6 continues: the steered surfaces**. `catia_surface_loft`
  now takes a `spine` and a `guide`, and `catia_surface_fill` meets its supports
  tangentially. No new operations, so coverage stays at **106/201** — what changed is that
  three arguments the vocabulary declares stopped being refused. A spine is a different
  algorithm rather than a refinement (the sections are swept, not interpolated): two 5 mm
  circles at the ends of a quarter arc give the torus segment Pappus predicts to 1e-9,
  where the free loft of the same sections is 23% smaller. A guide flaring 5→15 over 60 mm
  gives the cone's `π(r₁+r₂)·slant` to one part in 10⁵ — and it only does so with
  `ContactOnBorder`; with `NoContact` the guide merely turns the section about the spine,
  which for a circular section changes *nothing at all* and returns the unguided surface
  reporting success. Two OCCT traps in the fill. **Handing `MakeFilling` a boundary edge
  with no parameter curve on the support segfaults** — `Add` accepts it quietly, the
  process dies inside `Build()`, and there is no exception to catch, so the check has to
  come first; boundary edges are matched to the support's own edges by geometry (length and
  midpoint), never by position in the two lists. **And OCCT reports a tangency it did not
  deliver**: a cylinder's rim asks the patch to leave straight up, the plate solver gives
  up, and it returns `IsDone()` true with a flat disc 82.5° out — where the same call on a
  spherical opening lands within 1e-4°. So the fill measures what it achieved, reports
  `tangent_error_deg`, and refuses a patch that missed. G2 is refused with what OCCT itself
  says. Also fixed: a loft section may be a bare wireframe curve and not only a sketch —
  without that, the spine and guide arguments were unreachable from the curve vocabulary.
  Seven guards, all seven verified by breaking them.

- **2026-09-05** — E2 → **2.6 continues: the reflect line, and the wireframe family closes**.
  `catia_curve_reflect_line` plus `radius_mm` on `catia_curve_polyline`. Coverage 105 →
  **106/201**, and every wireframe operation the registry declares is now implemented. A
  reflect line is the silhouette as *geometry* — the parting line a mould splits along —
  and two things separate it from the hidden-line drawing OCCT computes it with. It keeps
  the **hidden** part (`ShowAll`, not `Hide`: two fused spheres give one equator with
  visibility on and both with it off, and a parting line does not stop existing because
  something is in front of it). And it keeps only what lies on a **curved** face, because
  HLR calls a box's eight boundary edges "outline" and a box has no reflect line at all —
  a polyhedron does its turning at edges that already exist. Without that filter every
  prismatic part appears to have a parting line. Verified against a sphere's great circle
  from three directions and a cylinder's two straight edges. A general angle is refused
  *before anything is resolved*, since no correction to the surface makes an unanswerable
  angle work. The polyline now rounds its own corners, **each in the plane of its own two
  segments** — two consecutive segments always share a plane, a path that turns out of one
  does not, and rounding a 3D path in one fitted plane puts every arc slightly wrong while
  measuring exactly right. Trims accumulate along a run, the wrap-around corner of a closed
  path is rounded too, and two collinear segments are no corner rather than a failure.
  Seven guards, all seven verified by breaking them. The draft's reflect-line refusal was
  **corrected rather than removed**: the silhouette exists now, so the real reason is that
  OCCT's draft takes a neutral *plane* where the mode wants a curve on the face.

- **2026-09-05** — E2 → **2.6 continues: the joins and the spiral**.
  `catia_curve_{corner,connect,spiral}`. Coverage 102 → **105/201**, which leaves
  `catia_curve_reflect_line` as the only wireframe operation still refused. A corner is an
  arc tangent to two curves, exact against `2πr/4` between perpendicular legs, and it
  leaves both inputs untouched — `trim` decides what the *new* element contains, never
  what the old ones are, because a step that edited an earlier one would make the same
  plan mean something different the second time it ran. A connect is a Bézier of the
  lowest degree that carries the continuity asked for: 1, 3 or 5. **The curvature case is
  where the arithmetic bites** — the source states its second derivative in its own
  parameter and the join runs on [0, 1], so the affine reparameterisation factor
  `(s/|d1|)²` is load-bearing; without it the curve is out by the square of the chord
  length, invisible at unit scale and wrong by four orders on a 100 mm join. Across a 60°
  gap in a 10 mm circle the quintic carries the circle's own 0.1/mm and the cubic leaves a
  0.0068/mm step: identical in a shaded view, and exactly the break a reflection shows.
  The operation reports both numbers rather than claiming G2. A spiral is the one curve
  here no kernel holds exactly, so it is fitted and says so — and the honesty has a trap
  of its own: measured at the interpolation knots the fit error reads 1e-14 against the
  9.4e-5 mm it is really out by between them, a factor of 10⁹, so a self-measurement taken
  at the points it was given would report machine zero and be believed. That one took two
  attempts to guard: the first breakage (sampling coarsely) still landed between the
  knots because `GeomAPI_Interpolate` parameterises by chord length, and the test's floor
  of "greater than zero" passed on 1e-14. Nine guards, all nine verified by breaking them.
  Also refactored: `curve_spline` and the spiral now share one interpolator, and the
  polyline's `radius_mm` refusal names what actually exists now.

- **2026-09-05** — E2 → **2.6 continues: the associative curves and planes**.
  `catia_curve_{project,parallel,offset_3d,combine}`,
  `catia_plane_{normal_to_curve,tangent_to_surface,mean}` and `catia_planes_between`.
  Coverage 94 → **102/201**. `plane_normal_to_curve` is the one that earns the rest: it
  places a sweep profile square to its path, so the helix built earlier is now something
  a section can be swept along, and the plane's normal carries the lead angle
  `atan(p/2πr)` exactly. Nine guards, every one verified by breaking it and watching the
  test fail. Three worth naming. **A face is a trimmed piece of an unbounded surface** —
  a live defect the probe found in already-shipped code: `GeomAPI_ProjectPointOnSurf`
  answers for the surface a face was cut out of, so a point beside a cylinder projected
  onto the *infinite plane* of its top disc, 20 mm past the rim and nearer than the wall,
  and `catia_point_on_surface`, `catia_line_normal` and the new tangent plane all agreed
  on a place that is not on the part. `closest_on_surface` now measures against the real
  boundary. **An offset has a side and OCCT does not take it from the argument given** —
  it reads the wire's own winding and never sees the named support; the rule is stated
  here, measured on the built result and mirrored when it went the other way, so the same
  L offsets 77.854 mm on a support facing up and 60 mm on one facing down. The first
  attempt at that guard did not bite, because OCCT already normalises *closed* wires — the
  winding test was testing nothing, and the discriminating case is the support, not the
  curve. **A best-fit plane is an inertia question asked backwards**: the principal axis
  of greatest moment is the covariance's smallest eigenvector, so OCCT computes it exactly
  and the kernel still needs no numpy (checked against `numpy.linalg.svd` to the last
  digit). Points on one line are refused — every plane through a line fits equally well.
  A projected curve is an OCCT B-spline fit (~1 part in 10⁷) and the docstring and the
  test tolerance both say so; `curve_combine` with no directions extrudes each view along
  its own plane, checked against the Steinmetz curve (two ellipses, semi-axes r and r√2).

- **2026-09-05** — E2 → **2.6 continues: the derived anchors**.
  `catia_point_{on_curve,on_surface,centre}` and
  `catia_line_{between,direction,normal,tangent}`. Coverage 87 → **94/201**. What these
  are for is associativity: a point measured once and typed as a coordinate is right
  until the part changes and wrong silently afterwards, and `catia_point_on_curve` is
  right afterwards too. Four traps, each verified by breaking it: **a point on a curve
  walks the whole chain**, not its first edge — halfway along an L of 30 then 40 is 5 mm
  up the second leg, and the first-edge answer (15 mm along the first) is the number
  nobody would question; **the chain is walked in connection order**, because
  `topology.explore` returns edges in *build* order and the two genuinely differ (the
  test fails when swapped); both `ratio` and `distance_mm` are **arc length**, never
  parameter, since a B-spline's parameter is not proportional to its length; a normal is
  read **at the point** rather than at the face centre, identical on a flat wall and a
  different fastener axis on a cylinder; and a point offset along a surface is
  **projected back onto it**, or it is a point in the air that still reads as being on
  the face. `catia_point_centre` refuses anything without an exact centre — a straight
  line gets a refusal rather than its midpoint.
- **2026-09-05** — E2 → **2.6 continues: wireframe curves** (`occt/operations/curves.py`).
  `catia_curve_{helix,circle,polyline,spline,section,intersect,extremum}`. Coverage 80 →
  **87/201**. Until these, every curve in a design came from a planar sketch or a surface
  boundary, so a genuinely 3D path was not expressible at all — a helix cannot be
  sketched, which is the reason the registry gives this its own module. Checked against
  `n·√(pitch² + (2πr)²)` for the length and `r + h·tan(taper)` for the cone, not against
  recorded output: a helix of the wrong pitch and one of the right pitch are the same
  picture. Four traps, each verified by breaking it: **`Geom2d_Line` normalises the
  direction it is given**, so sweeping the pcurve 0 → 2πn builds the right shape and a
  16% wrong length (265.5 mm measured against 314.8); a cone's v runs along the **slant**,
  so climbing `pitch` per turn in height means climbing `pitch/cos(taper)` in v; the
  cylinder's X is aimed at `start_point` rather than left to OCCT, or the helix is right
  in shape and wrong in **phase**; and `BRepLib.BuildCurves3d` is load-bearing — without
  it the edge has no 3D curve at all, measures the right length, reports a box 1.7 mm too
  big in every direction, and makes anything that sweeps along it raise
  `Standard_NullObject` somewhere else entirely. That last one passed every test until
  `catia_measure_item` was widened to report `bounding_box_mm` for a curve, which is the
  cheapest question that tells the two apart. **Response-shape note:** that widening adds
  `bounding_box_mm` to `catia_measure_item`'s payload for an edge element; nothing is
  removed and the path is already in the measurement contract.
- **2026-09-05** — E2 → **2.6 continues: the trimming family**. `catia_split`,
  `catia_trim`, `catia_untrim`, `catia_disassemble`, `catia_healing` and
  `catia_surface_analysis`. Coverage 74 → **80/201**. The question every one of these has
  to answer is "which piece did you mean", and CATIA answers it by where the user clicked;
  there is no click here, so the rule is written down instead — cells ordered by the
  signed distance of their centre from the cutting plane, `first` the side its normal
  points away from — and a cutter with no plane is **refused**, not resolved by whichever
  piece OCCT happened to list first. Verified against the frustum closed form on each side
  of a cut cone, and 600/1000/1600 on a flat panel. Three more traps, each pinned by a test
  that fails when the fix is removed: **cells are not connected components** (a split
  shell's halves share the cut edge, so `domains` correctly returns 1 while the caller
  plainly wants the two faces — this cost a debugging session); **a side means *every*
  cell on it**, because a surface crossing the plane twice is cut into three and keeping
  the furthest one silently drops material; and **`untrim` on a plane is not refused by
  OCCT** — `MakeFace` reports success and hands back a face of area 8 × 10¹⁰⁰, which flows
  into a mass and a bounding box looking like a measurement all the way. `catia_healing`
  refuses to run without a stated `merging_distance_mm` rather than falling back to join's
  tight one and closing nothing, and `catia_surface_analysis(kind='connect')` reports the
  smallest tolerance that *would* join the pieces — the exact argument healing takes, so
  the analysis hands the repair its own parameter instead of saying "there is a gap".
- **2026-09-05** — E2 → **2.6 started**: surfaces exist, and become material only when
  asked. `PartDocument` gained a construction store separate from its bodies, so building
  a skin leaves the part's mass exactly where it was — a surface that quietly became the
  active body would report a part with no solid, which reads like a failed feature rather
  than like a skin waiting to be closed. Ten operations land: extrude, revolve, offset,
  fill, loft, join, extract, boundary, and the two crossings back into material,
  `catia_close_surface` and `catia_thick_surface`. Each checked against the closed form —
  2πrh for a revolved line, π(R+r)·slant for a lofted frustum, and a truncated cone built
  *entirely* as skin then closed into h/3·π(R²+Rr+r²) to 5e-13. Coverage 64 → **74/201**.
  Two OCCT traps found and pinned, both verified by removing the fix and watching the
  right tests fail: **`MakeThickSolidBySimple` returns the solid inside-out**, which
  `BRepCheck_Analyzer` calls valid and which makes a later fuse *silently* return the
  wrong answer (a 1,000 mm³ block fused onto the uncorrected plate measured −4,800 — no
  error, block gone), and **`MakeFilling` approximates even a dead-flat boundary**, so a
  patched circular hole measured 314.1595 mm² against πr² = 314.1593 and carried a
  bounding box half as big again as the disc. A third fix has its own guard:
  `topology.connected_pieces`, because a connexity check written as a shell count reported
  "0 pieces" for two sheets that never met — `explore` flattens, so two disconnected
  shells and one shell of two faces are indistinguishable through it. `catia_extract` is
  where `feature#selector` pays for itself, taking `block#top` off a solid as a surface of
  its own.
- **2026-09-05** — E2 → **2.5 DONE**: stiffeners and the parting draft, the two Part
  Design features whose extent their own arguments do not state. `catia_stiffener`
  thickens an open profile and grows it past the part, subtracts the part, and keeps the
  pieces the profile reaches — so the gusset is exactly the void the walls close
  (½·b·h·t, exact) and stays right when a wall moves. Which way it grows is *stated*
  (sketch normal × the profile's chord, `reversed` to flip) rather than sniffed for,
  because the corner a stiffener fills is empty and every cheap material test answers
  about somewhere the stiffener is not; what replaces the sniffing is a check with a real
  answer — a piece that reaches the far end of its own sweep never met material and is
  refused, naming `reversed` as the fix. `catia_draft` gained its `parting` element: both
  sides taper away from the plane, which is what a two-part mould needs and what one
  taper cannot express, verified against the frustum closed form per side. Built by
  drafting the whole part twice and keeping one half of each, *not* by splitting first —
  splitting would ask the face selector to match halves that did not exist when the
  design named anything. A `neutral` element may now be a planar face of the part; that
  refusal had been pointing at Phase 2.2 since before 2.2 was built. Coverage 63 →
  **64/201**. Both guards were verified by breaking what they protect: removing the
  overrun check and giving both draft halves the same pull direction each fail exactly
  one test.
- **2026-09-05** — E2 → *2.5 partial: swept features, drawn curves, threads*
  (`catia_rib`, `catia_slot`, `catia_thread`, and the open-curve sketch vocabulary —
  line, polyline, arc, three-point arc, ellipse, spline, axis). Coverage 53 → **63/201**.
  Drawn segments now chain, so four `catia_sketch_line` calls make a profile a pad can
  extrude; ribs verified against Pappus's theorem; a thread is an annotation that
  provably does not change the mass, and an unreadable designation reports no pitch
  rather than a guessed one. Measurement contract 1.1 → 1.3, and the version is now
  checked at import against the newest entry — it had already drifted once, which would
  have put a version into a provenance record in which four of its own quantities did
  not exist.
- **2026-09-05** — E2 → *2.5 continues: pad limits, multi-body, listings, solid combine*
  (`7ef04a6`). `up_to_next` / `up_to_last` / `up_to_plane` resolved against the geometry,
  all exact against hand-computed volumes. `up_to_next` means opposite things for a pad
  and a pocket and is tested both ways. Coverage 30 → 53/201.
- **2026-09-05** — E2 → *2.5 started: patterns, transforms, holes, thickness*
  (`eb4d89a`). A pattern repeats the *material a feature added*, recovered as a
  generation difference, so one implementation covers pad/pocket/shaft/boolean.
- **2026-09-05** — E1 → **DONE**, E2 → *2.1–2.4 DONE*, E3 → *OCCT side DONE*
  (`3b5faf0`). The OCCT kernel, interrogation, the measurement contract and the selection
  vocabulary land together. `feature#selector` resolves. Five regressions the phase
  introduced were caught by the first `pytest` run after it and fixed in the same commit.
- **2026-09-04** — E5 → *partial: foundation shipped*. `app/design/{assertions,diff,correct}.py`,
  109 tests, all offline. 5.1–5.4 remain open.
- **2026-09-03** — Design IR: a part is a specification the compiler builds, not a tree it
  edits (`2c54287`). This is what E2 and E5 are both built on.

### Reconstructed, not contemporaneous

Everything above 2026-09-05 was written on 2026-09-05 from the board and `git log`. The
dates and commits are real; the wording is not what was recorded at the time, because
nothing was.

---

## Known documentation gaps

Recorded here rather than fixed silently, because each is a decision someone has to make:

- `CLAUDE.md` references **`KRYOVA_PRD.md`** and **`KRYOVA_STATE_OF_THE_PROJECT.md`**.
  Neither exists in this repository. Either write them or stop pointing at them — a
  reference to a missing document sends the next reader looking for context that is not
  there, which is worse than saying the context does not exist.
- `KRYOVA_CAPABILITY_ROADMAP.md` is named as the audit E2 grew out of and is likewise
  absent from the working tree.
- `ADDED_SYMBOLS.md` is an untracked one-off dump of the symbols added between
  2026-09-01 and 2026-09-03. It is not referenced by anything; delete it or track it
  deliberately.
