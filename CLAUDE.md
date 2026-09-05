# Kryova Backend

## What this project is for — read this before proposing anything

**The goal is a system an engineer can talk to that designs, analyses, validates and documents a
complete working machine** — a stamping press, a gearbox, a conveyor, a robot arm, a motorcycle
chassis — to a standard a licensed engineer can review, sign, and have manufactured.

Not a chatbot that models a bracket. Not a gear generator. **A machine.**

The controlling document is **[KRYOVA_MASTER_PLAN.md](KRYOVA_MASTER_PLAN.md)** (v2) — an
Engineering Track (18 phases, 7 eras) **plus a Product Track (P1–P10: identity/sessions, orgs and
tenancy, admin+audit, file attachments, agent UX, viewer at scale, desktop, billing, delivery,
trust)**, the technology choices and why, and the honest effort. Read it before designing
anything substantial; it will usually say where the work belongs and what it must not break.
[KRYOVA_BUILD_PLAN.md](KRYOVA_BUILD_PLAN.md) is the short-term working queue (one phase at a
time, green before the next). [KRYOVA_CAPABILITY_ROADMAP.md](KRYOVA_CAPABILITY_ROADMAP.md) is the
capability audit it grew out of.

Eight decisions from the master plan that change how code here should be written. Contradicting
one is a design change, not a detail:

1. **The design IR compiles to an open kernel first; CATIA is one backend among several.**
   Geometry must be buildable headless, free, in CI — that is what makes optimisation,
   sensitivity, self-correction and geometry regression tests affordable at all. Never write
   anything that assumes CATIA is the only way to make geometry.
2. **Physics is federated, never re-implemented.** Keep `solve/loads.py`, `solve/selection.py`
   and `solve/materials.py` — the load-case and geometric-selector vocabulary is the real asset.
   Swap the kernel underneath (CalculiX and friends). Do not hand-write another solver.
3. **Verification is the product.** An unmeasured claim is never a pass; an unconverged number is
   worse than no number; every result is bound to the geometry, mesh, material, load case and
   solver version that produced it. The existing honesty conventions (a mock mass says it is a
   mock, a missing translation says it is missing) are this rule applied locally — extend them,
   never erode them.
4. **Free and open, with the licence consequences taken seriously.** GPL solvers (CalculiX,
   code_aster, OpenFOAM, gmsh) are invoked **as separate processes across a file/CLI boundary**.
   Never link one in-process, however convenient.
5. **Honest scope.** Kryova does the structural, kinematic and packaging content and integrates
   bought-in components. Unattended sign-off on a safety-critical machine is not the goal and
   never becomes it. Do not write copy, docstrings or model prompts implying otherwise.
6. **One platform.** Web and desktop share one frontend (`../Kryova-frontend`: Next.js + Tauri),
   one API, one auth. No second admin web app, no separate viewer product. Respect that repo's
   three-dependency minimalism — a new frontend dependency is a named decision, not an import.
7. **Security and tenancy are architecture.** Refresh tokens rotate per use in per-device
   families with reuse detection (a replayed rotated token kills the family). Orgs own projects;
   Postgres RLS is the safety net under application scoping, fed **only** by `SET LOCAL` inside
   an explicit transaction — the one sanctioned exception to the "never `SET` on the pooled
   endpoint" rule, safe because it dies at COMMIT. Cross-tenant is 404, never 403. Admin
   impersonation carries both identities, defaults read-only, and always lands in the
   append-only audit log.
8. **Attachments are data, never instructions.** User files are parsed locally (Docling /
   MarkItDown class, `ezdxf`, the geometry pipeline) into provenance-tagged content. Extracted
   text is quoted material — it never enters system prompts, and no tool action may be justified
   solely by attachment text without the user seeing that justification. Document-borne prompt
   injection is a tested-against attack class here, not a hypothetical.

**Keep the phase status board current — this is how sessions keep the thread.** The master plan
carries a **phase status board** (Part 2, one row per phase E1–E18/17.3 and P1–P10). When your
work completes or materially advances a phase, update its row **in the same commit as the work**:
status (`not started` / `in progress (since date)` / `partial — what shipped (date)` /
`DONE (date)`) plus the evidence column naming the code or test where the claim is checkable.
`DONE` requires the phase's Proof running green — never mark it otherwise. Also append one line
to [KRYOVA_BUILD_PLAN.md](KRYOVA_BUILD_PLAN.md)'s *Done* section (the board is current-state;
the build plan is the history). A session that starts on Kryova work should read the board
first — it is the answer to "where were we?".

FastAPI service for an AI-native CAD + FEA platform: upload geometry → mesh → linear-static
FEA → viewer-ready results. Frontend is a **separate repo** (`../Kryova-frontend`, own
CLAUDE.md). Product scope lives in [KRYOVA_PRD.md](KRYOVA_PRD.md); honest current state and
the gap to a shippable product live in [KRYOVA_STATE_OF_THE_PROJECT.md](KRYOVA_STATE_OF_THE_PROJECT.md).

## Stack

Python 3.14 · FastAPI 0.141 · SQLAlchemy 2.0 (sync) + psycopg 3 · Alembic · Pydantic v2 +
pydantic-settings · numpy/scipy (FEA) · gmsh 4.15 (meshing) · faiss-cpu (vector indexes,
storage only — no consumer yet) · bcrypt + python-jose (auth) · Neon Postgres

## Commands

```
Install:      python -m venv venv && source venv/bin/activate && pip install -r requirements-dev.txt
Migrate:      alembic upgrade head
Dev server:   uvicorn app.main:app --reload      # with --reload also set INLINE_JOBS=true
Offline test: pytest tests/test_solver.py tests/test_mesh.py tests/test_geometry.py \
              tests/test_kernel.py tests/test_interrogation.py tests/test_design_*.py \
              tests/test_render.py tests/test_vision.py          # no DB, no network
Full test:    pytest                              # needs a live Neon connection, ~4 min
Drift check:  alembic check                       # fails if models diverged from migrations
New revision: alembic revision --autogenerate -m "..."
```

**The venv exists** (`venv/`, numpy 2.5.2 / scipy 1.18.0 / SQLAlchemy 2.0.52) — this section
used to say it did not, and a session that believed it either refused to report a result or
rebuilt the environment for nothing. Always `venv/bin/python`, never the system one, whose
`numpy 1.26 / scipy 1.11 / SQLAlchemy 1.4` are the wrong versions and are not the project's.

**Lint and type-check exist now** (this section used to say they did not):
`pyproject.toml` configures ruff (`E,F,I`, line length 100) and mypy (`python_version = "3.12"`,
`mypy_path = "scripts"`), and `.github/` has workflows. Run
`venv/bin/python -m ruff check app/ tests/` and `venv/bin/python -m mypy app/` before finishing.
**Both are clean, and there is no longer a list of errors to expect.** This paragraph used to
name two in `app/catia/local_bridge.py`; those went, seven in `app/solve/` took their place and
were carried for a while as "pre-existing", and on 2026-09-05 the last of them were fixed rather
than tolerated. A tolerated error is one nobody reads, so the next real one hides behind it —
if mypy prints anything, it is yours.

**Setup and update are one script**, `scripts/setup.sh` (bash) and `scripts/setup.ps1`
(PowerShell — **the product ships on Windows**, so that one is the path that matters). Every
step is idempotent, so re-running *is* the update: the venv is reused, pip installs only what
changed, Alembic applies only new migrations, and the reference index rebuilds only when the
documents actually changed. There is deliberately no separate update script.

## Architecture

Layered and DI-driven: **route → dep (auth/ownership) → service/runner → module → DB**.
Everything is injected via `Depends`; nothing imports a session or a store directly.

```
app/
  main.py         app factory, CORS, lifespan (fails jobs orphaned by a restart), /health
  core/           config.py (pydantic-settings), database.py (engine/session), security.py (bcrypt + JWT)
  models/         SQLAlchemy ORM: User, Project, GeometryVersion, SimulationJob, Media
  schemas/        Pydantic request/response models
  api/            deps.py (auth + ownership guards), rate_limit.py, routes/<domain>.py, router.py
  media/          content-addressed local blob store, chunked/resumable uploads, FAISS indexes
  geometry/       format detection, dependency-free file inspection
  mesh/           gmsh tet meshing, quality metrics, exact primitives for tests
  solve/          Solver interface, linear-static tet4 FEA, materials, region selectors
  simulation/     runner.py — the geometry → mesh → solve job pipeline
  jobs/           JobQueue interface; ThreadPoolJobQueue today, Celery/RQ later
  design/         a part as a compilable specification, not a tree to edit (see below)
  kernel/         the open geometry kernel — OCCT, headless, free, in CI (see below)
  retrieval/      the reference manuals the agent consults (see below)
migrations/       Alembic (versions/ is the only migration path)
tests/            pytest, mirrors app/
```

### Three seams exist on purpose — respect them

- **`solve.Solver`** (ABC) — mesh in, load case in, fields out. A surrogate/neural solver must
  drop in without the API, job, or (future) AI layer knowing which ran.
- **`solve.ModalSolver`** (ABC) — a sibling, not a method on `Solver`: natural frequencies come
  from a different input (`ModalCase`, no loads, fixtures optional) and return a different
  output, so folding them into `solve()` would make every caller branch on what it got back.
  `solve/modal.py` implements it. The mass matrix is integrated **analytically** in barycentric
  coordinates, not with the stiffness assembly's four-point Gauss rule — that rule is exact only
  to degree 2 and tet10's `N^T N` is quartic, so reusing it would be wrong by a few percent:
  plausible-looking, and wrong.

### Analyses available

All three verified against closed-form solutions, not recorded output.

| Analysis | Module | Case | Checked against |
|---|---|---|---|
| Linear static | `solve/linear_static.py` | `LoadCase` | σ = F/A, δ = FL/AE |
| Modal | `solve/modal.py` | `ModalCase` | bar `f=(2n−1)/4L·√(E/ρ)`, cantilever Euler-Bernoulli modes 1–3, six rigid-body modes free-free |
| Buckling | `solve/buckling.py` | `BucklingCase` | Euler `P=π²EI/(KL)²` |
| Thermal stress | `solve/thermal.py` (via `LoadCase.delta_t_k`) | `LoadCase` | restrained bar `σ = −EαΔT` |

Three things about these that are easy to get wrong and are pinned by tests:

- **Thermal strain must be subtracted during stress recovery**, not only added as a load.
  Leaving it out reports the stress of a freely-expanding part — wrong sign and wrong size.
- **Buckling is posed as `−Kg φ = μ K φ`**, not the natural way round: `Kg` is indefinite and
  the generalised symmetric eigensolver needs the positive-definite matrix on the right.
  `λ = 1/μ`.
- **Eigenvalue tolerances must be relative.** Eigenvalues are ω² — order 1e10 for steel, 1e4
  for rubber — so any absolute threshold is simultaneously too tight for one and too loose for
  the other.

A bar in tension still returns a finite positive buckling factor (measured ~68,000×), because a
3D bar has small compressive pockets at the load introduction. That is correct, not a bug; the
meaningful statement is the ratio to the compressive case.
- **`jobs.JobQueue`** (ABC) — one method, `submit`. Moving to Celery must not touch routes.
- **`media.LocalMediaStore`** — content addressing + chunked IO behind a small surface, so an
  S3 store is a swap, not a rewrite.

Never reach around a seam. If a route needs to know which solver ran, put it on the job row.

## The design IR (`app/design/`)

A part described as a **specification that is compiled**, rather than a feature tree that is
edited. This is Layer B of `KRYOVA_CAPABILITY_ROADMAP.md`, and it exists because editing a
tree conversationally breaks on the topological naming problem: insert a fillet upstream and
every downstream reference shatters. Regenerating from a spec has no downstream edit to break.

Read `spec.py` first (what a design *is*), then `compile.py` (what happens to one). Then
`execute` runs a plan, `assertions` says whether the result is acceptable, `diff` says what an
edit reached, `correct` closes the loop between them. `names` and `params` are usable alone.

Things that will bite you:

- **Only `execute` touches anything outside the package, and it does so through an injected
  callable.** No session, no socket, no `dispatch` import. That is why all 338 tests over this
  package run offline in under a second, and it is worth keeping — the same property the
  physics tests have and for the same reason.
- **`Plan.digest()` does not answer "does this build the same part?"** despite reading like
  it. A plan carries each call's `note` — rationale travels with the design on purpose, and
  `DesignSpec.digest()` covering it is a *tested contract* — so rewriting a comment moves both
  digests and builds identical geometry. Every geometry question goes through
  `diff.builds_the_same` (tools and resolved arguments only). Do not "simplify" one into the
  other; the digests are what a provenance record wants (D11) and are deliberately coarser.
- **Impact analysis compares compiled plans, never spec text.** By compile time every
  parameter is a literal in the argument list of the calls using it, so a feature moved iff
  its resolved calls differ — exact, with no parameter-usage graph to fall out of step.
- **A reference is not always a read.** `catia_sketch_rectangle(sketch=@profile)` draws
  *into* the profile; `catia_pad(sketch=@profile)` extrudes what it finds. So a feature's
  geometry can change while its call is byte-identical, and following `@` references alone
  silently leaves every solid built on a changed sketch looking current. The two are told
  apart by `Plan.unaddressable` — a feature that creates a tree element gets an allocated
  name, and one that does not is unaddressable *because* its effect landed on something else.
- **A plan carries exactly one late-bound value**, `Created(feature)`, because a fresh pad is
  called whatever CATIA invented. `bind()` resolves it from what the creating call reported.
  Predicting `Pad.1` is the positional fragility this package exists to remove. The executor
  records the *creating* call's name with `setdefault`, not the following rename's.
- **An assertion that could not be measured is `UNMEASURED`, never a pass.** A suite that
  skips what it could not read reports green on a part nobody checked.
- **The correction loop's stopping rules are exact, not heuristic.** A repair compiling to the
  same buildable plan cannot change the outcome, so it ends the loop and is not counted as an
  attempt; a plan already tried is a cycle. Both are gifts from the compiler being
  deterministic. The attempt cap is only the backstop. A repair that *does not compile* is a
  normal attempt on purpose — the compiler's error names the feature and says what to do,
  which is the best feedback in the system, so it goes back round as the next brief.
- **`feature#selector` is parsed and refused, not resolved.** Predicate selection is roadmap
  A3 and is blocked behind A1 — a predicate is only decidable against geometry that exists.

## The geometry kernel (`app/kernel/`)

Decision 1 made real: geometry built **headless, free and in CI**, so optimisation,
sensitivity and geometry regression tests are affordable at all. OCCT via
`cadquery-ocp` (OCP) — `pythonocc-core` is not on PyPI and would have forced conda into
the deployment. `app/design/` compiles a spec to a `Plan`; `OcctRunner` executes it, and
a CATIA seat executes the same plan through the same `CallRunner` seam.

Reading order: `occt/binding.py` (the one place OCP is imported), then `occt/document.py`
(what a part *is* here), then `occt/naming.py` (the three non-obvious rules that make
names survive regeneration — break one and `Solve()` returns success while resolving to
nothing).

Above the backends sit four backend-neutral modules, and the split is load-bearing:

- **`measurement.py`** — what a measured part reports, and `Detail` levels, which exist
  for latency: a plan for a machine is 10⁵–10⁶ operations and computing the full set
  after each would dominate the run.
- **`interrogation.py` + `occt/interrogate/`** — what a part can be *made into*: wall
  thickness, draft, undercuts, curvature, continuity, validity, clearance. These have a
  premise ("pulled along +Z"), can be inapplicable, and are frequently **sampled**.
  Nothing here runs speculatively; `measure()` never calls it.
- **`contract.py`** — the written vocabulary an assertion may read, with unit and
  meaning. `undocumented_paths()` is asserted empty, which is what makes it a contract.
- **`provenance.py`** — measured / approximated / unavailable-with-a-reason, as a
  *sidecar* so `bounding_box_mm.size[2]` still resolves. `assertions.py` reads it per
  path, so an exact mass is not tainted by a ray-cast thickness beside it.

Things that will bite you:

- **`topology.explore` de-duplicates and `explore_oriented` does not**, deliberately.
  `TopExp_Explorer` visits a sub-shape once *per owning parent*, so a box explores as 24
  edges and 48 vertices. Use the first for "every edge of this part", the second only
  where a face's own boundary orientation is the point (convexity).
- **A face's outward normal is not its surface normal.** OCCT stores orientation
  separately, so a REVERSED face's normal points into the material. `face_normal_at` is
  the one place that correction lives — and it flips curvature's sign too, which is why
  concave/convex is measured against a table in `interrogate/curvature.py` rather than
  reasoned about.
- **A UV grid is not a set of points on a face.** A trimmed face reports its whole
  surface's parameter range, so most of a naive grid lands in the hole. Everything goes
  through `interrogate/sampling.py`, which classifies against the real boundary and
  refines once when a face is too narrow to catch a sample.
- **Sampled answers must never be reported as measured.** Thickness and undercut are
  upper bounds from a finite ray set, and they say so. Draft works this out per part —
  exact on planes, sampled on curves.
- **`str.capitalize()` is banned in error text.** It lowercases everything after the
  first character, turning `BRepFeat_MakePrism` into `brepfeat_makeprism`. Sentence
  casing lives in `errors._as_sentence`.
- **OCP passes `Handle(Geom_…)&` by value**, so any OCCT function that works by
  reassigning a handle is *inert* here — it builds the answer and drops it, with no
  exception and no return value. `GeomLib::ExtendCurveToPoint` and `ExtendSurfByLength`
  are both like this, which is why `catia_extrapolate` widens a parameter range instead.
- **`explore` yields base `TopoDS_Shape`.** `BRepAdaptor_Curve`, `BRepTools_WireExplorer`
  and `MakeWire.Add` are overloaded on the concrete type and refuse it — cast through
  `symbol("TopoDS").Edge_s` / `Face_s` / `Wire_s`. This has cost time four times now.
- **Only what `occt/binding.py` registers is reachable through `symbol()`.**
  `GeomAbs_CurveType`, `TopAbs_Orientation` and `Geom_Plane` are *not* registered; go
  through `classify.edge_curve_type` and `BRepAdaptor_Surface(...).Plane()`, or add the
  symbol to the registry deliberately rather than importing OCP at the call site.

## Rendering and the visual check (`app/render/`, `app/ai/vision.py`)

Phase E4. Eight canonical views rendered byte-identically run to run, section cuts, a
before/after diff, and a vision model asked whether the part matches the request.

- **Hidden-line removal, not OpenGL.** OCP exposes `V3d`/`AIS` and a viewer does come up
  here — but 4.1 needs two renders of the same geometry to be *byte-identical* so a render
  hash can join mass and plan-digest as a third identity check, and a GL image is a
  function of the driver, sampling and display server on a project that develops on Linux
  and ships on Windows. HLR is arithmetic; the raster under it is integer.
- **OCCT's `gp_Ax2` Y axis is `direction × X`** — the opposite of the up vector `views.py`
  declares — so `project._flatten` negates y. Leaving it out renders every part upside
  down and **nothing can see it**: a consistently mirrored image is still byte-identical to
  itself, so determinism holds, a diff of two mirrored renders is still correct, and a
  wireframe looks plausible either way up. It shipped inverted for one day.
- Determinism is defended at each cheap place to lose it: no anti-aliasing, `floor(v+0.5)`
  rather than banker's rounding, dash phase per polyline not per segment, curve deflection
  relative to model size, and a hand-written PNG encoder (an outside one can add a
  timestamp chunk or change its filter heuristic between versions).
- **A section's normal points at the material that is removed** — `catia_split`'s own
  convention. Two conventions for one question is how a part ends up mirrored with every
  test green. Hatching fills by **even-odd across every wire at once**, so a bore falls out
  of the parity with nothing having to identify it as a hole.
- **The visual check is a filter, never a sign-off**, so `VisualReview` deliberately has no
  `approved`/`passed` property — only `objected`. Every way it can fail to run is
  `unchecked`, which is never a pass (the rule `assertions.py` applies to an unmeasured
  assertion). Nothing in it raises.
- **Ollama does not refuse an image handed to a text-only model** — it drops it and answers
  anyway, so the check would manufacture agreement, which is worse than no check. `_sees()`
  gates on `/api/show` `capabilities` or a `projector_info` block (structural signals, no
  model-name list to rot); `AI_VISION_MODEL` names the model that looks, because locally it
  is a second pull. `num_ctx` must be sized for the images too — Ollama truncates a prompt
  from the front in silence.

## Reference manuals (`app/retrieval/`)

The agent can consult the CATIA and FEA documentation held on this machine instead of
answering about CATIA from memory, via one tool: `search_documentation`.

**It is lexical (BM25), not embeddings, and that was a decision rather than a shortcut.**
Three things about this deployment force it, all pointing the same way. The provider is
pluggable and defaults to local Ollama with no key — and **Anthropic publishes no embedding
model at all**, so a dense index would either drag a second vendor into a deployment that
deliberately chose one, or force an extra model pull onto an install whose point is that it
runs offline. The corpus is technical manuals, which is the regime where lexical retrieval is
strongest: what discriminates between passages here is exact terms (`M6`, `Ø12`, `tet4`,
`V5R21`, `Multi-sections Solid`) — precisely what embeddings blur. And the manuals are
bilingual, where accent folding does for free what no embedding quality gives you.
The honest caveat, worth keeping in mind before anyone "upgrades" this: on open-domain prose
hybrid lexical+dense beats either alone. `Corpus.search` sits behind a small surface so a
dense stage can be fused in later without the agent or tool layer knowing.

Layers, each testable alone: `analyze` (bilingual, jargon-preserving tokenizer) → `extract`
(PDF→pages, fallback chain poppler → pypdf → pdfminer) → `chunking` (pages→passages, headings
carried) → `bm25` (scorer over flat numpy arrays) → `corpus` (build/load/search) → `service`
(process-wide handle) → `language` (per-passage detection + preference).

Things that will bite you:

- **`KnowledgeService.search` cannot raise.** Missing index, corrupt index, wrong format
  version, disk gone — all return `[]`, logged once. Consulting the manuals improves an
  answer and must never be why there is not one. Keep it that way.
- **The tool and the prompt are gated on the index actually existing**, not on the setting.
  `_build_knowledge` withholds the tool and `system_prompt()` picks a variant without the
  documentation section. A prompt describing a tool the model was not given teaches it to
  hallucinate a call. There are **four** frozen system prompts for this reason (CATIA × docs).
- **Never name the mechanism in user-facing text.** The step label is "Checking the
  documentation"; the prompt tells the model to cite the document and page and *not* to
  narrate the lookup. `tests/test_retrieval.py` and the prompt-cleanliness check exist because
  "I searched my knowledge base" is a worse answer than the answer.
- **Heading detection is the most tuned code in `chunking.py`.** These manuals are almost
  entirely numbered procedures, so a bare `\d+\.` pattern labels thousands of instruction
  steps as section titles. Only keyword (`Chapter 4`) or multi-level (`3.2`) numbers count,
  and the terminal-punctuation test runs *before* the numbered test. Both orderings are
  load-bearing and both have already been got wrong once.
  **Refusing to *accept* a line is not the same as *rejecting* it** — that was the third
  time. Declining a bare `6.` only passed the line to the title-case fallback, which took
  it, because a wrapped French step (`6. Cliquez sur OK pour`) has no terminal full stop and
  is title-case by the letter of the rule: `sur`/`pour` are minor words, `Cliquez`/`OK` are
  capitalised. That labelled 1,143 of 5,003 real passages, tripling `cliquez` in the term
  stream and putting a step number in the citation the user reads. `_ENUMERATOR_RE`,
  `_PATH_FRAGMENT_RE` and the minor-word-ending test are the explicit rejections; all three
  run before the title-case fallback and all three are asserted against the real corpus.
- **Language is a boost, never a filter** (`LANGUAGE_PREFERENCE_BOOST`). CATIA's menus are
  translated, so a French user needs the French page — but a workbench documented only in
  English must still answer them. A clearly better match in the other language still wins.
  The value (1.35) is measured, not chosen: Photo Studio is English-only here and FreeStyle
  French-only, so for those the boost is not breaking a tie but demoting the only answer
  there is. Swept over the corpus eval set, 1.6 lost both (MRR 0.934) and 1.35 finds every
  case (P@3 100%, MRR 0.974); above ~1.45 it stops breaking ties and starts overriding
  relevance. Re-sweep before changing it — `tests/test_retrieval_corpus.py` is the harness.
- **Every considered file is fingerprinted, including skipped ones.** Recording only successes
  makes `is_stale` permanently true on any corpus containing one unreadable PDF.
- Builds are atomic (staging directory, swapped in), so rebuilding under a live server is safe.

```
python -m app.retrieval.build            # build or rebuild
python -m app.retrieval.build --check    # exits non-zero when a rebuild is needed
python -m app.retrieval.build --query X  # see what the agent would find
```

## CATIA V5 reference (`app/catia_kb/`)

The other half of the CATIA answer, and it does a different job from the corpus above. The
corpus knows what page 147 of the Part Design manual *says*; this knows that Edge Fillet is
in Part Design's Dress-Up Features toolbar at `Insert > Dress-Up Features > Edge Fillet`,
that it needs P1, that the French interface calls it `Congé d'arête` and the German one
`Kantenverrundung`, that it fails when the radius exceeds the narrowest adjacent face *on the
propagated tangent chain* rather than the edge you clicked, and that Tritangent Fillet is the
alternative when a whole face should disappear. ~1,600 entries: workbenches, commands, dialog
fields, file formats, `Tools > Options` settings, error messages, aerospace vocabulary,
workflows, methodology, the automation object model, and the V5R19 product trigram table.

It ships **in the code, not in an index**, so unlike the manuals it is always present. That is
why there are still four frozen system prompts and not eight — the domain section is
unconditional.

Three consumers, in order of how much they earn:

1. **Query expansion** (`recognise.expand_query`, wired into `KnowledgeService.search`). Half
   the corpus is French; without this, half of it is unreachable from an English question. A
   query for "draft angle" gets `dépouille` added before it hits BM25.
2. **`explain_catia_term`** — the lookup tool. Returns *fields*, where `search_documentation`
   returns prose, so the model states a menu path it was handed rather than one it recalls.
3. **The per-turn brief** (`state.py`) — a few lines beside the user's message naming what
   their words refer to. This is what makes a small local model get the workbench right
   without having to decide to call a tool.

Things that will bite you:

- **Precision is the hard half, not recall.** This vocabulary contains `fit`, `add`, `part`,
  `box`, `web` and `pip`. Two disjoint tiers in `recognise.py` keep them from firing:
  `NEVER_BARE` never matches alone (ordinary English that collides with a CATIA name);
  `AMBIGUOUS_WORDS` needs corroboration from something unmistakable elsewhere in the message.
  Distinctive words — `pocket`, `fillet`, `joggle`, `sketch` — are in **neither**, because
  "how do I make a pocket" carries no other signal and must still work. `TestPrecision` is the
  set of sentences that must produce nothing; add to it before widening any alias.
- **Product codes need their capitals when they collide** (`PIP`/`pip`, `FIT`, `GAS`, `EST`,
  `CUT`). Codes with no collision (`GSD`, `ASL`, `CPD`) match either way.
- **Fuzzy matching only fires once something matched exactly.** Otherwise `document` scores
  0.94 against `Documents` and every English sentence with a long word produces a hit.
- **Expansion and the coverage floor are in direct conflict** — this shipped as a bug once.
  Expansion adds *synonyms*, and a passage matches the English name or the French one, never
  both, so a floor computed over the expanded query demands breadth no passage can have.
  `Corpus.search(..., coverage_query=)` measures the floor against the user's original query.
  Never remove that argument.
- **A missing translation is reported as missing.** `localised()` returns `None` and the tool
  payload says so in words. Never fall back to the English name presented as the localised
  one — an engineer can work with "I don't have the German name, it's here in the menu", and
  cannot recover from being sent to a menu item that does not exist. Same rule for the
  informal trigrams (`WSF`, `AMT`), which say they are informal.
- **The COM automation API is not localised** (`api.localisation`). `AddNewPad` is
  `AddNewPad` on every language install; only user-typed data (feature names, materials)
  translates. This is why the CATIA bridge works on any seat, and why a macro that looks up
  `"Pad.1"` by string is the one that breaks abroad.
- **`CatiaKnowledge` cannot raise**, same contract as `KnowledgeService`. Every method returns
  empty/unchanged on failure, logged once.
- **Ambiguity is named, not resolved.** SMD vs ASL, GSD vs WSF, GPS vs GAS, generative vs
  interactive Drafting, Geometrical Set vs Body — the `Disambiguation` table forks these and
  the brief prints the fork. Picking a side is how an airframe engineer loses a day.
- Duplicate entry keys raise at import; `missing_cross_references()` and `untranslated()` are
  asserted empty by the tests, which is what catches a rename orphaning a German name.

## Driving CATIA's interface (`catia_run_command` and the dialog tools)

Eight tools reach every command on the seat rather than the thirty Kryova implements
directly: `catia_list_commands`, `catia_run_command`, `catia_describe_dialog`,
`catia_fill_dialog`, `catia_dialog_action`, `catia_press_key`, `catia_switch_workbench`,
`catia_select`. Server specs in `app/catia/tool_specs.py`, resolution in
`app/catia_kb/ui.py`, daemon in `scripts/catia_bridge/{ui_automation,ui_policy,mock_ui}.py`.
Full contract in `docs/CATIA_BRIDGE_PROTOCOL.md` ("Driving the interface").

- **It is Win32, never COM.** `GetMenu`, `EnumChildWindows`, `SendMessageTimeoutW`. Two
  consequences you must not undo: it reads the seat's *actual* labels so it works in a
  language nobody wrote a table for, and it keeps working while a modal dialog has COM
  blocked — which is the only time it matters most.
- **`OUT_OF_BAND_TOOLS` (`backend.py`) skip the COM liveness probe**, and the same tools are
  in `dispatch._NO_AUTO_CHECKPOINT`. Both for one reason: a checkpoint is a COM save, and a
  failed checkpoint refuses the call. Gate these on COM and the tools that dismiss a stuck
  dialog can only run when no dialog is stuck. `catia_run_command` is the deliberate
  exception — it starts things, COM is alive by definition when it does, and it stays
  checkpointed.
- **`StartCommand` fails silently.** Hand it a name CATIA does not know and it does nothing,
  raises nothing, and returns nothing. So the daemon tries the **live menu first** (an item
  either exists or does not, and a greyed one can be reported as greyed) and falls back to
  `StartCommand` with `verified: false`. Never report an unverified `StartCommand` as success.
- **Command labels are localised; internal command ids are not, and are undocumented.**
  `COMMAND_IDS` holds only ids with a published source. Do not add one from memory: a wrong
  id fails the same silent way and burns the candidate that would have worked.
- **Buttons are pressed by role, never by label.** `ButtonRole` + `BUTTON_LABELS` resolve
  OK/Cancel/Apply per language on the server; `STANDARD_CONTROL_IDS` (IDOK=1, IDCANCEL=2) is
  the language-proof fallback when label matching finds nothing. A Spanish seat's accept
  button reads `Aceptar`.
- **Refusals are exact-label or leading-phrase, never substring.** `FORBIDDEN_EXACT` /
  `FORBIDDEN_PREFIX`, mirrored in `ui_policy.py` and enforced on the daemon against *every*
  candidate. The split exists because a leading-word rule refused `Exit Sketcher Workbench`;
  a substring rule refuses `Copy Options`. An over-refusal is not safe — the agent's recovery
  from a refusal is to try something else, so it becomes a wrongly built part.
- **Mock mode simulates the interface** (`mock_ui.py`) and runs in a language:
  `--mock-language de`. Pressing OK on the mock Pad dialog builds a real mock Pad, so tests
  assert the outcome. Every interactive test runs against `en` and `de`.
- **What Linux cannot verify** is listed in the protocol doc's mock section: whether CATIA's
  dialogs answer `WM_GETTEXT`, whether `EN_CHANGE` is needed, what its window classes are.
  `describe_dialog` reports unrecognised controls with their class name so the first Windows
  session produces the answer instead of a shrug.

## Non-negotiable rules

These are facts that cannot be inferred by reading a single file.

**Units are mm-N-MPa everywhere, and nothing in the codebase converts.**
Length/displacement mm · force N · Young's modulus and stress MPa · density kg/m³ · **mass
output is already kilograms**. CAD files are read in their own coordinates and assumed
millimetres. Any new quantity must land in this system at the boundary, not deeper in.

**Never `SET` session state against the pooled Neon endpoint.** The `-pooler` host is PgBouncer
in transaction-pooling mode: a `SET search_path` (or timezone, or anything) survives on the
shared backend connection and is handed to the next client. This has already happened here —
a running server started resolving its tables into the test schema. Every table reference is
compiled schema-qualified via `execution_options={"schema_translate_map": {None: settings.db_schema}}`
(`app/core/database.py`), and the test fixtures do the same for `kryova_test`. Do not
"simplify" this to `search_path`.

**Cross-user access returns 404, not 403** (`get_owned_project`, `_get_job`) so ids cannot be
enumerated across accounts. Keep it that way on every new resource.

**Background jobs own their own session.** They outlive the request, whose session is closed
the moment the response is sent — use `SessionScopeDep`/`get_session_scope`, never the request
session. Commit the job row *before* `queue.submit`, because the worker looks it up by id in a
different session.

**Gmsh is a process-global, non-thread-safe singleton.** Meshing serialises on a module lock in
`app/mesh/gmsh_mesher.py`, and gmsh is initialised with `interruptible=False` — otherwise it
installs a SIGINT handler that raises off the main thread, and meshing never runs on the main
thread.

**Gmsh picks its reader from the file extension, and blobs are named by SHA-256 with no
extension**, so `gmsh_mesher.py` stages them (hard link where the bytes need no change). It
also rewrites the 80-byte STL comment header when needed: gmsh's STL sniffer skips lines
starting with NUL, so a valid binary STL with the conventional zeroed header and no `0x0A`
byte anywhere is rejected with a bare "Error loading". **The stored blob is never modified.**

**An under-constrained model is caught by the equilibrium residual, not by looking for NaNs.**
SuperLU returns a finite, meaningless vector for a singular system. Do not replace
`_residual_is_small` with a finiteness check.

**Loads are distributed by tributary area** (`solve/selection.distribute_force`), so refining
the mesh does not change the applied load. Region selection is by geometric selector
(`{"type":"face","axis":"z","side":"min"}` / `{"type":"box",...}`), never by face id — face ids
are meaningless across a re-export.

**Every heavy byte goes through `app/media/`.** Nothing loads a whole file into memory —
not writing, not hashing, not serving. Blobs are content-addressed (SHA-256, sharded
`blobs/ab/cd/…`), so two records can share one blob: deletion goes through `MediaService`,
which drops the file only once nothing references it. Small metadata rows go to Neon; a
400 MB STEP file never crosses the network.

**Migrations** live only in `migrations/versions/`. Run `alembic check` before finishing any
model change.

**A conversation acts on the document it owns, not on CATIA's `ActiveDocument`.** Every
document-scoped call frame carries `document: {doc_name, remote_path}` from the conversation's
`CatiaDocument` row, and `backend.ensure_document` activates it — reopening it from disk if
CATIA was restarted — before the operation runs. Without it, an engineer clicking another part
between two messages silently redirected the work; nothing errored, the wrong file just grew
features. The unscoped tools are enumerated with their reasons in `dispatch._UNSCOPED_TOOLS`
(the three that *establish* a binding, plus the interactive family, which runs when a modal
dialog has COM blocked and cannot afford a COM call). A backend that holds documents must
override `ensure_document`; `tests/test_document_binding.py` fails if one inherits the no-op.

**The transcript is not the record of what was done — `CatiaOperation` is.** The window trims
the oldest messages and the summary is an LLM paraphrase of what it trimmed, so neither can be
trusted about work from last week. `app/ai/resume.py` reads the log instead: a few lines in the
per-turn state block (how much ran, how long ago, and **which attempts failed and were never
made to work**) plus the `design_history` tool for the full paged account. Loose ends are keyed
on the tool alone and cleared by any later success of that tool — keying on arguments would
leave every superseded retry in the list forever. It carries no `catia_` prefix on purpose:
that prefix means "goes to the workstation", and this answers with CATIA closed
(`tests/test_tool_registry.py` enforces it).

## Known landmines in the current code

Read these before touching the relevant file — they are live defects, not style opinions.

- ~~**tet10 support is dead and broken.**~~ **Fixed and verified 2026-09-02.**
  `assemble_stiffness` dispatches on `mesh.midside`, `assemble_stiffness_tet10` integrates at
  four Gauss points, `_recover_stress` has a tet10 branch, and `TestQuadraticElements` checks
  quadratic beats linear against the closed-form cantilever at equal element count. The
  docstring now describes what the code does.
- **`SECRET_KEY` defaults to `"changeme"`** (`core/config.py`) and nothing refuses to start on
  it. Any deployment that forgets the env var signs JWTs with a public constant.
- **The rate limiter trusts `X-Forwarded-For` unconditionally** (`api/routes/auth.py::_client_ip`).
  A client that sets a random XFF per request has no rate limit at all. It is also in-process,
  so it does nothing across workers.
- **No list endpoint paginates.** `/projects`, `/projects/{id}/geometry`,
  `/projects/{id}/simulations`, `/media` all return everything.
- **The README and `.env.example` claim SQLite is refused at startup.** It is not —
  `Settings._require_postgres` returns SQLite URLs unchanged and `database.py` has a full
  SQLite branch. Fix the docs or the code, but do not trust either in isolation.
- **`/health` returns `{"status":"ok"}` unconditionally** — it does not check the database, so
  it cannot be used as a readiness probe.
- **`data/bm25/` holds ~450 MB of Dassault Systèmes / CATIA training PDFs, and they are tracked
  again on purpose** (2026-09-01) so the corpus syncs to the Windows test workstation with a
  plain `git pull`. They are third-party copyrighted material in a repo carrying its own
  LICENSE, and a later `.gitignore` cannot undo it — removing them needs a history rewrite.
  Raise this before the repository is published or cloned widely. `data/bm25/index/` is *not*
  tracked: it is derived, rewritten whole on every build, and would conflict between machines.
  The manuals sit directly in `data/bm25/`, not in `data/bm25/sources/`; both are scanned
  (`knowledge_source_dirs` walks `data/bm25/sources` **and** `data/`), so either works and
  nothing needs moving — but it does mean any `.md`/`.txt` dropped anywhere under `data/` is
  indexed as reference material.
- **Four of the 25 PDFs are scans with no text layer** (the large French `Formation-*` files,
  42–66 MB each) and cannot be indexed without OCR. The build reports them as
  `scanned, no text layer` and carries on; this is expected, not a regression. The other 21
  index in ~7s to ~4,900 passages.

## Testing

**Tests are written on Linux and run on Windows (agreed 2026-09-05).** The user has a
Windows machine with CATIA and the bridge, and that is where the suites are executed —
against the real application rather than repeatedly here. So on this machine:

- **Write the tests with the work and commit them with it.** Skipping them because they
  will not be run here is the one thing this arrangement must not turn into: the seat runs
  what exists, so a phase with no tests written is a phase that never gets verified.
- **Do not run the suites here** — not `pytest`, not a single file, not to "check it
  works". Report what was written, not what passed.
- **`ruff` and `mypy` still run here before finishing.** They are lint and type-check, not
  tests, and they are cheap. `venv/bin/python -m ruff check app/ tests/` and
  `venv/bin/python -m mypy app/`.
- Verifying a new guard by **breaking the thing it guards** is still required. Reason it
  through against the source and say so plainly in the commit; where a guard cannot be
  shown to fail, label it unpinned rather than shipping it as verified.
- A one-off *API-surface* check — does this OCCT symbol exist, does this method take these
  arguments — is not a test and is worth doing, because shipping code that calls a name
  that is not there wastes a seat session on an `AttributeError`.

- Physics tests (`test_solver.py`, `test_mesh.py`, `test_geometry.py`) never request a database
  fixture, so they open no connection and run offline in under a second. Keep it that way —
  that tight loop is the reason meshing/FEA work is bearable.
- Everything else runs against **the same Neon database in a `kryova_test` schema**, created
  and dropped per run. Testing on SQLite while shipping on Postgres is the drift that hides
  JSONB, enum and cascade bugs. The cost is real: ~250 ms per round trip, ~4 minutes for the
  DB suite. The fixtures minimise round trips — one connection per session, isolation by
  transaction rollback, no app lifespan per test. If it gets painful, the fix is a local
  Postgres, not a return to SQLite.
- The test schema is selected with `schema_translate_map`, never `SET search_path` (see above).
- `client` fixture overrides the job queue to `InlineJobQueue` so jobs run on the request
  thread inside the test's open transaction — a worker thread would use its own connection and
  see none of the uncommitted data.
- The solver is verified against **closed-form solutions**, not recorded output: a bar in pure
  tension reproduces σ = F/A and δ = FL/AE to 1e-6, including through a real gmsh mesh.
  Any new solver work must be verified the same way.
- Retrieval is verified twice, and the split matters. `test_retrieval.py` proves the machinery
  on synthetic fixtures (fast, always runs). `test_retrieval_corpus.py` measures the *real*
  index over `data/bm25` — 38 engineering questions against the manual that ought to answer
  each, scored precision@1 / precision@3 / MRR, plus corpus and citation health. Every test
  in it passed on synthetic input while the shipped index was labelling 23% of its passages
  with a procedure step, which is the whole argument for having it. It **skips** when no
  index is built (fresh clone, CI) and skips any individual case whose subject matter is not
  indexed, so curating the manuals cannot turn it red. Thresholds are floors with headroom,
  not the measured numbers pinned — currently P@1 94.7%, P@3 100%, MRR 0.974.

## Conventions

- Files `snake_case` · classes `PascalCase` · functions/vars `snake_case` · constants `UPPER_SNAKE`
- Routes: `api/routes/<domain>.py` exposing `router = APIRouter(prefix=..., tags=[...])`,
  wired in `api/router.py` — an unwired router is invisible everywhere, including `/docs`
- Schemas: `<Resource>Create` / `<Resource>Update` / `<Resource>Read`
- Errors: raise `HTTPException` with a **human-readable, actionable** `detail`. The existing
  messages tell the user what to do ("Increase element_size_mm to coarsen it", "Check that the
  fixtures remove all six rigid-body motions") — match that register, do not degrade to
  "Invalid input".
- Expected, explainable failures (`MeshError`, `SolverError`, `ValueError`) are recorded on the
  job row with their message; anything else is logged with a stack trace and recorded as
  "Unexpected solver failure".

## Do not

- Don't convert units anywhere — the whole codebase is mm-N-MPa
- Don't use `SET search_path`, or any session-level `SET`, against the pooled endpoint
- Don't borrow the request session in a background job
- Don't call gmsh off the module lock, or modify a stored blob in place
- Don't return unpaginated collections in new endpoints (the existing ones are a known debt,
  not a pattern to copy)
- Don't add migrations outside `migrations/versions/`
- Don't return 403 for another user's resource — 404
- Don't claim a capability in a docstring or README that the code does not have (this has
  already happened twice — tet10 and the SQLite refusal)
