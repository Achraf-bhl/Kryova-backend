# Kryova build plan

Working queue for the roadmap in *"everything needed to build real machines from the chat"*
(2026-09-03). **This file is a queue, not an archive**: a phase is deleted from *Queue* the
moment it is finished and tested, and one line about it is appended to *Done*. If a phase is
still below, it is still owed.

**Rules of the queue**

- One phase at a time, finished and green before the next is started.
- "Finished" means: implemented, `pytest` green, `ruff check app/ tests/` clean, and `mypy app/`
  with no *new* errors against the baseline recorded below.
- Anything that needs a real CATIA seat, a licence, or an external binary is not in the queue —
  it is in *Blocked on the physical world* at the bottom, so it is never quietly counted as done.

**Baselines at the start of this work** (commit `03d238e`)

| Check | State |
|---|---|
| `pytest` (full, incl. Neon) | 2169 passed, 4 skipped, ~161 s |
| `ruff check app/ tests/` | clean |
| `mypy app/` | **7 pre-existing errors** — `solve/loads.py:205` (×4 union-attr), `solve/{linear_static,modal,buckling}.py` (`_dof_indices` arg-type). Not mine; do not be surprised by them, do not let the count grow. |

---

## Corrections to the roadmap document

The roadmap is stale in five places. Recorded here so effort is not spent twice.

| Roadmap says | Actually true on this branch |
|---|---|
| D3 modal & buckling — *missing, 1 mo* | **Shipped.** `app/solve/modal.py`, `app/solve/buckling.py`, verified against Euler-Bernoulli and Euler critical load. |
| D7 thermal — *missing, 3 mo* | **Shipped** for thermal *stress*. `app/solve/thermal.py` via `LoadCase.delta_t_k`, verified against `σ = −EαΔT`. Transient conduction is genuinely missing. |
| C1 geometric interrogation — *missing, 1 mo* | **Mostly shipped.** `catia_measure`, `catia_measure_between`, `catia_measure_item`, `catia_analysis_part`, `catia_sketch_analysis`, `catia_surface_analysis`, `catia_assembly_analysis`, `catia_assembly_clash`. |
| A2 reference geometry — *missing, 1 mo* | **Shipped.** `catia_plane_offset`, `plane_angle`, `plane_through_points`, `plane_normal_to_curve`, `plane_tangent_to_surface`, `plane_mean`, `axis_system`, the point and line families. |
| `CATIA_V5_FULL_COVERAGE.md` is stale at 39 tools | Still stale. Regenerate it from `app.catia.ops.registry.summary()` or delete it. **Queued in Phase 9.** |
| B2 "every created entity gets a stable name at creation" | **Not reachable as written.** Only 65 of 201 operations take a `name`; every core Part Design feature takes none. Phase 1 delivers B2 with a following `catia_feature_rename` instead. Adding `name` to the ~39 backend methods is the better long-term fix and is blocked behind A1. |

The roadmap's own framing survives all five corrections: Layers **B** and **F2** are still at
zero, and they are still what decides whether this is a product.

---

## Done

- **2026-09-03 · `03d238e`** — Reconciled the branch's five hand-rolled assembly tools with
  main's 201-op generated registry. The old implementations were shadowing the new
  registry-driven methods of the same name on both `CatiaCom` and `MockCatia` rather than
  erroring.

- **2026-09-03 · Phase 1 — Layer B foundation (B1 core, B2, B3).** New `app/design/` package:
  `errors` · `names` (semantic naming) · `params` (parameter graph with dimensional checking) ·
  `spec` (the declarative design) · `compile` (deterministic lowering to a call plan). Tests
  green at **2485 passed, 4 skipped**, ruff clean, mypy unchanged at the 7-error baseline,
  99% line coverage across the new package. Also backfilled the branch's untested guard rails:
  `app/catia/ops/registry.py` 69% → **100%**, `app/catia/ops/spec.py` 91% → **100%**,
  `app/solve/selection.py` 87% → 96%.

  Three things learned that the roadmap did not know, kept here because they change Phase 2:

  1. **Only 65 of 201 operations can name what they create.** Every core Part Design feature —
     pad, pocket, hole, fillet, shell, chamfer, draft, the booleans, the transformations,
     the patterns — takes no `name` parameter, so B2 as written ("named at creation") is not
     reachable through them. The compiler follows each with a `catia_feature_rename`, using
     operations that already ship and are already tested, rather than adding a `name` to
     ~39 backend methods that nobody can validate against a real seat until A1 is done.
  2. **A plan needs exactly one late-bound value.** A rename must say which feature to rename,
     and a fresh pad is called whatever CATIA invented. Predicting `Pad.1` is the positional
     fragility Layer B exists to remove, so the plan carries `Created(feature)` and `bind()`
     resolves it from what the creating call reported. Everything else in a plan is a literal.
  3. **Sketch-internal operations are unaddressable and are reported as such.** A rectangle
     drawn inside a sketch leaves no tree row, so nothing can refer to it. Emitting a rename
     and hoping would push the failure back to the workstation.

---

## Queue

### Phase 2 — Regeneration and self-correction

*Covers B4, C3, C4, plus the executor Phase 1 stopped short of.*

- **The plan executor.** Phase 1 produces a plan and `bind()`; nothing runs one yet. Walk the
  calls through `app.catia.dispatch`, collect each result's `feature` key into the `created`
  map, stop on the first failure with the feature named. This is small and it is what turns
  the compiler from a checker into a builder.
- Spec diff and impact analysis (**B4**): "this parameter rebuilds these 40 features and
  invalidates these 3 simulations." `ResolvedParameters.dependents_of` already answers the
  parameter half; the feature half is a walk over the reference graph the compiler builds.
- Design assertions (**C3**): machine-checkable claims — *pivot centre 25 mm from datum*,
  *minimum wall ≥ 3 mm*, *mass ≤ 4.2 kg* — re-run on every regeneration. Unit testing for
  geometry. The interrogation operations it needs (`catia_measure`, `catia_analysis_part`)
  already exist.
- Self-correction loop (**C4**): assertion fails → diagnose → edit spec → regenerate → re-assert,
  with bounded retries.
- **A3 groundwork.** `SemanticName` already reserves the `feature#selector` spelling and
  refuses it with a message pointing here. Predicate selection is what makes it resolve.

### Phase 3 — The physics that actually decides

*Covers D4, D10, D11.*

- **D4 fatigue** — rainflow counting, S-N and ε-N curves, Goodman/Gerber/Soderberg mean-stress
  correction, Miner's rule, BS 7608 / Eurocode 3 weld classes, surface-finish and size factors,
  damage summation over a duty cycle. The roadmap's highest-value physics, and a press frame is
  a fatigue machine before it is anything else.
- **D10 mesh convergence** — refine until the answer stops moving; Richardson extrapolation and
  a reported GCI. An un-converged number stated with confidence is worse than no number.
- **D11 provenance** — every result bound to geometry version, mesh settings, material, load
  case, solver version and convergence evidence.

### Phase 4 — Engineering knowledge

*Covers E1, E2, E3.*

- **E1 requirements model** — target mass, duty cycle, envelope, regulatory regime, cost ceiling;
  flow-down to component constraints and validation flow-up.
- **E2 load-case library** — standardised, per-domain cases so results are comparable between runs.
- **E3 materials** — expand the 8 hard-coded keys into a real table carrying S-N data,
  temperature dependence and cost. (Buying MatWeb/Granta is the right answer eventually; the
  schema is the deliverable here.)

### Phase 5 — Sheet metal, and the stamping machine

*Covers A10 (Sheet Metal subset) and **mission 3**.*

- Sheet Metal ops: wall, bend, flange, unfold/flat pattern, bend allowance and K-factor.
- **Mission 3** — the end-to-end test prompt: a fully functional sheet-metal stamping machine
  with every parameter chosen here (press force, stroke, die set, frame, drive). It has to run
  against everything Phases 1–4 built, or those phases were decoration.

### Phase 6 — Scale

*Covers F1, F2, F3, F4.* Product structure and BOM as first-class data · hierarchical
decomposition with interface contracts (**the only way the context problem is solvable**) ·
change propagation · batched operations, because 10⁵ COM round trips is 28 hours of pure latency.

### Phase 7 — The agent layer

*Covers H1, H2, H3.* Planning into a dependency-aware task graph · retrieval over the op
registry, which is already at 201 and heading for 900 · failure diagnosis and bounded retry.

### Phase 8 — Output

*Covers G1, G2.* Drawing generation with GD&T · export suite (STEP AP242, IGES, JT, 3MF, DXF).

### Phase 9 — Housekeeping the roadmap earned

- Regenerate or delete `CATIA_V5_FULL_COVERAGE.md`; a wrong map is worse than none.
- **I4 observability** — per-op success rates and a failure taxonomy. Nothing currently measures
  op reliability, which is why A1's pass/fail matrix has nowhere to land.
- **I5 determinism** — same spec plus same version ⇒ same geometry, byte for byte.

---

## Blocked on the physical world

Not in the queue, and not counted as done, because no amount of code in this repo finishes them.

- **A1 — validate the 201 ops against the real seat.** A licensed V5-R33 is on this machine, so
  this is *possible*, but it is a long supervised run producing a pass/fail matrix, not an edit.
  It blocks any honest claim about capability. Needs Phase 9's observability to have somewhere
  to record the result.
- **D1 — CalculiX.** Needs the binary installed and on PATH. Keep `loads.py` and `selection.py`;
  swap the kernel under them.
- **D2 — NAFEMS benchmarks**, **D6 multibody**, **D8 CFD**, **E4–E7**: need an ME, a data
  licence, or both.
- Domain engineers · physical testing · homologation · professional liability · supplier
  relationships · aesthetic judgement. Listed in the roadmap for the right reason; repeated here
  so the queue above is never mistaken for the whole job.
