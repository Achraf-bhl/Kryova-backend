# Kryova — everything needed to build real machines from the chat

Written 2026-09-03. This is the complete list of what has to be added for the chatbot to go
from "makes a part" to "engineers a machine". It is deliberately blunt about effort, about
what should be bought rather than built, and about what is not a software problem at all.

Effort figures are **engineer-months for a competent team**, and they assume the person doing
the work already knows the domain. They are estimates, not commitments. Where a line says
*"needs an ME"*, it means a software engineer cannot do it correctly no matter how good they
are — the knowledge is the deliverable, not the code.

---

## 0. The honest framing

"Capable of building anything via chat" is not one capability. It is seven stacked ones, and
they fail independently:

| # | Layer | Question it answers | State today |
|---|---|---|---|
| A | **Authoring** | Can it create the geometry? | Partial — 201 ops |
| B | **Regeneration** | Can it *change* the geometry without breaking it? | **Missing — the critical gap** |
| C | **Perception** | Does it know what it built? | **Missing** |
| D | **Physics** | Is the design any good? | Minimal — linear static only |
| E | **Knowledge** | How does it decide anything? | **Missing** |
| F | **Scale** | Can it hold a 2,000-part product? | **Missing** |
| G | **Output** | Can someone manufacture it? | **Missing** |

Layer A is the one everyone works on because it is visible. **Layers B and C are what actually
decide whether this becomes a product**, and they are currently at zero.

---

## 1. Where the code actually is (measured, not claimed)

```
app/catia/ops/       201 ops, 11 domains
  part_design.py  44    sketcher.py  33    surfaces.py  21
  reference.py    19    assembly.py  16    drafting.py  15
  wireframe.py    15    knowledge.py 11    infrastructure.py 10
  ui.py            9    inspection.py 8

app/solve/         1,343 LOC hand-written tet4/tet10 linear static (numpy/scipy)
app/mesh/            gmsh 4.15.2  ← industrial-grade, a genuine asset
app/ai/              agent, tools, prompts, state, resume
```

Against CATIA's real surface: **~100 workbenches, 907 documented commands, 1,080 CAA
automation objects, 5,636 methods.**

Two honest notes:

- `CATIA_V5_FULL_COVERAGE.md` is **stale** — it reports 39 tools and UI-driving via menus.
  The registry is now 201 ops on the automation API. Fix or delete that doc; a wrong map is
  worse than none.
- A meaningful share of the 201 ops are **mock-backed and never validated against real
  CATIA on Windows**. Until each one has run against the real product, op count is a
  measure of intent, not capability. **This is item 1 on the list below.**

---

## LAYER A — CAD authoring coverage

Everything here is *exposing more of CATIA*, not building new technology. This is the cheap
half of the roadmap and the half most likely to be over-estimated in importance.

### A1. Validate the 201 existing ops against real CATIA — **3 mo, blocking everything**
Every op run against a real licence on Windows, with a recorded pass/fail matrix. Mock passes
prove the schema, not the behaviour. Nothing else on this document is meaningful until this
is done and the number of *real* ops is known.

### A2. Reference geometry — **1 mo**
Offset planes, plane-on-face, plane-through-3-points, plane-normal-to-curve, user axis
systems, datum points/lines. Currently sketch planes are effectively `XY|YZ|ZX`. Nearly every
real part needs a plane that is not one of those three.

### A3. Robust selection — **2 mo, needs design thought**
The single most limiting schema constraint today. Replace named-enum picking (`all |
vertical | horizontal | top | bottom` edges; 5 named hole positions; 6 named faces) with:
- selection by geometric predicate (all edges > 10 mm, all faces normal to +Z, all holes of Ø6)
- selection by persistent semantic name assigned at creation
- per-entity parameters (per-edge fillet radius, not one radius for everything)

### A4. Part Design completion — **2 mo**
Multi-body parts, boolean operations between bodies, geometrical sets, rib/slot, stiffener,
draft with parting line, variable fillet, tritangent fillet, thread/tap, user patterns,
shell with face selection, thickness on face.

### A5. Sketcher completion — **1.5 mo**
Full constraint vocabulary (tangent, symmetric, equal, concentric, construction geometry),
projected/intersected 3D elements, sketch analysis (open profile / over-constrained
diagnostics), spline and conic control.

### A6. Surfaces / GSD completion — **3 mo**
Multi-section surface with guides and coupling, adaptive sweep, blend with continuity
control, fill, join/heal with tolerance, trim/split, extrapolate, law-driven surfaces,
curvature and connect-checker analysis. This is what bodywork needs.

### A7. Assembly Design completion — **2 mo**
Full constraint set with degrees-of-freedom reporting, flexible sub-products, publications,
component patterns, catalogue instantiation, replace-component, contextual design.

### A8. **DMU Kinematics** — **2 mo, high value**
Mechanism definition from assembly constraints, joint types, simulation over a motion range,
**swept volume**, interference-through-motion, travel-limit checks. This is how suspension
travel, steering lock and chain alignment get validated. *CATIA already has it; it is simply
not wired.*

### A9. Drafting completion — **2 mo**
Auto views from 3D, section and detail views, dimension generation, **GD&T / FTA
annotation**, BOM tables, title blocks, sheet formats, PDF/DXF export. Without this nothing
leaves the building.

### A10. Sheet metal, weldments, tubing, harness — **3 mo**
Bends, flanges, unfold/flat pattern, weld beads and symbols, tube/pipe routing, electrical
harness. A motorbike frame is a **tubular weldment** — this is not optional for that target.

### A11. Knowledge & templates — **2 mo**
Formulas, rules, checks, reactions, design tables, **PowerCopy and UDF** (user-defined
features), Product Knowledge Template. This is how a design becomes reusable rather than
one-off.

### A12. Analysis workbench (GSA) bridge — **1 mo**
Drive CATIA's own Generative Structural Analysis for quick in-CAD checks, distinct from the
external solver path.

**Layer A total: ~24 engineer-months.** All of it is integration work with a known ceiling.

---

## LAYER B — Regeneration and design intent  ⚠️ **THE CRITICAL ONE**

Everything in Layer A is worthless at scale without this. The chatbot currently edits geometry
by issuing COM calls conversationally. That model breaks on the **topological naming
problem**: a feature references an edge or face by identity, you insert a fillet upstream, and
every downstream reference shatters. At the scale of a machine, *revision is the work*, so
this is not a rough edge — it is fatal.

### B1. A declarative design IR — **6 mo, hardest design decision in the project**
Stop treating CAD as a document to be edited. Treat it as a **compilation target**.

The agent authors and edits a **design specification** — parameters, features, constraints,
references by *semantic* name — and a deterministic compiler turns that spec into CATIA
operations. On any change you **regenerate from the spec**; you never patch a feature tree.

What this buys, all of which is otherwise unobtainable:
- Version control and meaningful diffs on a design
- Deterministic replay and reproducible builds
- Automated regression tests over designs
- The agent edits **text**, which LLMs are good at, instead of blind geometry
- Immunity to topological naming, because there is no downstream edit to break

### B2. Persistent semantic naming — **2 mo**
Every created entity gets a stable name at creation (`swingarm.pivot_bore.inner_face`) that
survives regeneration. Prerequisite for A3 and B1.

### B3. Parametric constraint solver at spec level — **3 mo**
Resolve the design's own parameter graph (wheelbase → swingarm length → pivot position)
before touching CATIA, so conflicts surface as spec errors rather than CAD failures.

### B4. Regeneration diff and impact analysis — **2 mo**
"Changing this parameter rebuilds these 40 features and invalidates these 3 simulations."
Without it, every change is a full rebuild and a full re-validation.

**Layer B total: ~13 engineer-months.** This is the part that is genuinely hard and genuinely
differentiating. Nobody has shipped it well.

---

## LAYER C — Perception and verification

The agent currently cannot tell whether what it built is what it meant. It sees only what a
tool call returns. This is the second-most-serious gap.

### C1. Geometric interrogation ops — **1 mo, highest value-per-effort on this document**
Mass, volume, centre of mass, inertia tensor, bounding box, surface area, wall-thickness
scan, draft analysis, curvature/continuity check, clearance and interference queries. All
available through the CATIA API. Cheap. Transformative.

### C2. Visual verification — **2 mo**
Screenshot the model from canonical views, feed to a vision model, ask "is this what was
asked for?" Catches the whole class of gross errors — mirrored, inside-out, wrong scale —
that no numeric assertion catches.

### C3. Design assertions / regression tests — **2 mo**
Machine-checkable claims that re-run on every regeneration: *"pivot centre is 25 mm from
datum"*, *"minimum wall ≥ 3 mm"*, *"mass ≤ 4.2 kg"*, *"no interference at full travel"*.
This is unit testing for geometry, and it is what makes autonomous iteration safe.

### C4. Self-correction loop — **2 mo**
Assertion fails → agent diagnoses → edits the spec → regenerates → re-asserts. Bounded
retries. This is the loop that makes the thing feel intelligent instead of merely capable.

**Layer C total: ~7 engineer-months.** Best return in the whole document.

---

## LAYER D — Physics and simulation

### D1. Replace the hand-written solver with CalculiX — **2 mo, do this first**
The 1,343-line solver in `app/solve/` is correct-looking and well-commented, but it is a
*component* solver: no contact, no nonlinearity, no modal, no dynamics, no shells or beams,
and a direct solve that will not survive a real assembly.

CalculiX is free, open source, Abaqus-input compatible, and validated over 25 years. Keep
`gmsh`. **Keep `loads.py` and `selection.py`** — the load-case and face-selection abstraction
is the actual value, because that is what the agent drives. Swap the kernel underneath.

You go from one analysis type to eight in a quarter, instead of never.

### D2. Solver validation against NAFEMS benchmarks — **1 mo, needs an ME**
Until this exists you do not know your numbers are *right*, only that they are plausible.
For anything load-bearing that distinction is a liability, not a nicety.

### D3. Modal and buckling — **1 mo** (free once D1 lands)
Natural frequencies, mode shapes, critical loads. NVH and slender-member stability.

### D4. **Fatigue** — **4 mo, needs an ME — highest-value physics on this list**
Not a solver — post-processing. Rainflow counting, S-N and ε-N curves, mean-stress
correction (Goodman/Gerber), Miner's rule, weld classification (BS 7608 / Eurocode 3),
surface-finish and size factors, damage summation over a duty cycle.

**Structures fail from fatigue, not from a single static load.** Linear static von Mises
tells you essentially nothing about whether a frame survives 100,000 km. If you add one
physics capability, add this one.

### D5. Nonlinear and contact — **3 mo** (largely free once D1 lands, hard to make converge)
Large deformation, plasticity, bolted joints, press fits, gaskets, snap fits.

### D6. Multibody dynamics — **5 mo, needs an ME**
Project Chrono or MBDyn, or a purpose-built planar model. Suspension kinematics, load
extraction at every joint under real manoeuvres, ride and handling. **This is where the load
cases that feed FEA actually come from** — today they are hand-entered guesses.

### D7. Thermal — **3 mo**
Steady and transient conduction, convection boundary conditions, thermal stress coupling.

### D8. CFD — **6 mo, expensive, do late**
OpenFOAM. Aerodynamics, cooling flow, ducting. The meshing is the hard part, not the solve.
Consistently under-estimated by everyone who has not done it.

### D9. Optimisation — **4 mo**
Topology optimisation, parameter sweeps, DOE, response surfaces, multi-objective
(mass vs stiffness vs cost). This is the capability that makes an AI designer genuinely
better than a human one rather than merely faster.

### D10. Mesh convergence automation — **2 mo, needs an ME**
Refine until the result stops moving, automatically, and *report the convergence*. A single
un-converged FEA number presented with confidence is worse than no number.

### D11. Simulation provenance and traceability — **1 mo**
Every result permanently bound to: geometry version, mesh settings, material, load case,
solver version, convergence evidence. Non-negotiable for anything an engineer signs.

**Layer D total: ~32 engineer-months**, of which ~10 require a real analyst.

---

## LAYER E — Engineering knowledge

This is the layer nobody budgets for and the one that decides whether the output is
trustworthy. **None of it is a coding problem.**

### E1. Requirements model — **3 mo, needs an ME**
A machine is defined by a specification before it is defined by geometry: target mass, duty
cycle, envelope, regulatory regime, cost ceiling. Requirements flow *down* to component-level
constraints and validation flows *up*. Without this the agent has no basis on which to decide
anything, and "design me a motorbike" has no answerable meaning.

### E2. Load case library — **3 mo, needs an ME**
Standardised, per-domain load cases: pothole strike, panic braking, cornering, curb drop,
rider mass distribution, proof and ultimate factors. Today these are invented per
conversation, which means results are not comparable between runs.

### E3. Materials database — **2 mo**
Full elastic and plastic properties, S-N curves, temperature dependence, anisotropy, cost,
density, availability, joining compatibility. The current 8 hard-coded keys are a demo
fixture. Buy the data (MatWeb, Granta) rather than transcribing it.

### E4. Standard and supplier parts — **4 mo**
Bearings, fasteners, seals, o-rings, retaining rings, bushings, plus supplier CAD import
(TraceParts, McMaster, 3D ContentCentral). **70%+ of any real machine is bought parts.**
Without this the agent models things that should never be modelled.

### E5. Design rules and standards — **4 mo, needs an ME**
Minimum wall thickness, draft angles, fillet radii, bolt torque and preload, thread
engagement, weld sizing, DFM rules per process (cast, machined, printed, sheet, moulded).
This is the encoded judgement that separates a plausible model from a manufacturable one.

### E6. Tolerance and GD&T — **4 mo, needs an ME**
Tolerance stack-up (worst case and RSS), fit selection, datum schemes, FTA annotation in
CATIA. A drawing without tolerances is not a drawing.

### E7. Cost model — **2 mo**
Material plus process plus tooling plus assembly time. Cost is a design constraint, not an
afterthought, and an agent that ignores it will confidently design something unbuildable.

**Layer E total: ~22 engineer-months, nearly all of it requiring domain engineers.**

---

## LAYER F — Scale

Everything above assumes one part. A machine is 10³–10⁴ parts and 10⁵–10⁶ operations.

### F1. Product structure and BOM as first-class data — **3 mo**
Hierarchical assembly tree, effectivity, revisions, where-used. Not a chat transcript. Today
the product exists only as conversation history, which is not a data structure.

### F2. Hierarchical decomposition with interface contracts — **4 mo**
The agent works on one component at a time against a contract: mounting points, envelope,
mass budget, interface loads. Exactly how human teams partition work, and the only way the
context problem is solvable. **Without this, context limits alone cap the achievable
complexity — no model size fixes it.**

### F3. Change propagation — **3 mo**
A change to an interface invalidates dependents and triggers re-validation. Otherwise the
model silently drifts out of consistency and nobody knows which results are stale.

### F4. Throughput — **2 mo**
Every op is currently a COM round-trip measured in seconds. Batch operations, or generate a
CATScript and execute once. At 10⁵ operations, a 1-second round trip is 28 hours of pure
latency.

### F5. Concurrency and locking — **2 mo**
Multiple agents or users on one product without corrupting it.

### F6. Long-horizon agent memory — **4 mo**
Design decisions, rejected alternatives and their reasons, open issues, rationale. A
conversation is not a design record, and "why is this rib here" must have an answer in six
months.

**Layer F total: ~18 engineer-months.**

---

## LAYER G — Manufacturing output

### G1. Drawing generation with GD&T — **3 mo** (needs A9 + E6)
### G2. Export suite — **1 mo**
STEP AP242, IGES, Parasolid, JT, 3MF/STL, DXF flat patterns.
### G3. CAM interface — **3 mo**
Toolpath-ready output, machining features, stock definition, fixturing notes.
### G4. Inspection planning — **2 mo**
CMM points, measurement plans derived from the GD&T scheme.
### G5. Technical documentation — **2 mo**
Assembly instructions, exploded views, service manuals, parts catalogues.

**Layer G total: ~11 engineer-months.**

---

## LAYER H — The chat and agent layer itself

### H1. Planning and decomposition — **4 mo**
Turn "design a swingarm" into an ordered, dependency-aware task graph with checkpoints,
rather than a linear improvised sequence of tool calls.

### H2. Tool selection at 500+ tools — **3 mo**
Retrieval over the op registry. Current tool-calling accuracy degrades badly past a few
hundred tools; the registry is already 201 and heading for 900.

### H3. Failure recovery — **3 mo**
CATIA errors, failed features, non-converged solves. Diagnose, repair, retry with bounded
attempts, escalate to the human with a *specific* question.

### H4. Human-in-the-loop checkpoints — **2 mo**
Structured approval gates at design milestones. Not a chat message — a reviewable diff with
an explicit sign-off record.

### H5. Explanation and rationale — **2 mo**
Every decision traceable to a requirement or a standard. This is what makes the output
defensible, and it is what a signing engineer will demand.

### H6. Cost and time estimation — **1 mo**
Tell the user a task is 4 hours of compute and $X before starting it, not after.

**Layer H total: ~15 engineer-months.**

---

## LAYER I — Infrastructure

### I1. CATIA licence and session pool — **3 mo**
Multiple concurrent sessions, licence management, crash recovery, session affinity. Today one
bridge equals one user.
### I2. Compute for simulation — **2 mo**
Queue, autoscale, spot instances, result caching. FEA is not a request-path workload.
### I3. Geometry storage and versioning — **2 mo**
Content-addressed CAD versioning with meaningful diffs.
### I4. Observability — **2 mo**
Per-op success rates, agent trajectory traces, simulation timings, failure taxonomy. You
cannot improve what you do not measure, and right now nothing measures op reliability.
### I5. Determinism and reproducibility — **2 mo**
Same spec plus same version equals same geometry, byte-for-byte. Required for any regulated use.

**Layer I total: ~11 engineer-months.**

---

## NOT SOFTWARE — no amount of code fixes these

Listed because omitting them would make the roadmap dishonest.

- **Domain engineers on staff.** Fatigue methodology, load definition, mesh discipline,
  tolerancing, solver validation. An agent cannot invent these and neither can a software
  team. Without ME hires, the system will produce confident numbers that nobody should trust.
- **Physical testing.** Rigs, strain gauges, durability, correlation of model to reality. An
  unvalidated simulation is a hypothesis.
- **Homologation.** ECE, FMVSS, EU Machinery Directive, CE. Vehicles need type approval.
- **Professional liability.** Someone with a licence and insurance signs. That is a legal
  fact, not a UX decision.
- **Supplier relationships, tooling, manufacturing capacity.**
- **Aesthetic judgement.** Class-A surfacing is craft. The tools exist; the taste does not
  automate.

---

## Totals

| Layer | Engineer-months | Nature |
|---|---:|---|
| A — Authoring | 24 | Integration, known ceiling |
| B — Regeneration | 13 | **Hard, differentiating** |
| C — Perception | 7 | **Best value/effort** |
| D — Physics | 32 | Mostly buy/integrate |
| E — Knowledge | 22 | **Needs domain engineers** |
| F — Scale | 18 | Architecture |
| G — Output | 11 | Integration |
| H — Agent | 15 | Research-adjacent |
| I — Infrastructure | 11 | Ordinary engineering |
| **Total** | **~153** | ≈ 8 engineers × 1.6 years, ideal conditions |

Realistically, with hiring, rework and the discovery that always follows: **3–5 calendar
years** to a product that designs machines credibly with a human signing off.

---

## Sequencing — what to do in what order

**Phase 0 — prove one part (3–4 months). Do not skip this.**
A1 (validate ops) · D1 (CalculiX) · D2 (benchmarks) · C1 (interrogation) · B2 (semantic
naming). Target: *one bracket, spec → CATIA → mesh → static → drawing, ten times, identically.*
If this is not bulletproof, nothing above it matters.

**Phase 1 — make it re-editable (6–8 months).**
B1 (design IR) · B3 · C3 (assertions) · C4 (self-correction) · A2, A3 (references, selection).
Target: *change a parameter, everything regenerates and re-validates automatically.*

**Phase 2 — make it engineer (8–12 months).**
D4 (fatigue) · D3 · D10 · E1, E2, E3 (requirements, load cases, materials) · A4, A5, A12.
Target: *a part with a defensible fatigue life against a stated duty cycle.*

**Phase 3 — make it assemble (12–18 months).**
A7, A8 (assembly, kinematics) · D6 (MBD) · E4 (standard parts) · F1, F2 (structure,
decomposition) · G1, G2. Target: *a validated subsystem — swingarm, shock, linkage, through
full travel.*

**Phase 4 — machines (18+ months).**
A6, A10 · D5, D7, D9 · E5, E6, E7 · F3–F6 · G3–G5 · all of H and I.

---

## The scope decision that determines whether this is possible

**Nobody designs a motorbike from zero.** Small manufacturers buy the engine (Rotax, Loncin,
CFMoto), buy the suspension (KYB, Marzocchi), buy the brakes (Brembo, J.Juan). They design
the **chassis, packaging, ergonomics, bodywork and integration**.

Aim at *"designs and validates a chassis and its integration around bought major components,
produces manufacturable drawings, a licensed engineer signs off"* and this list is a real
3–5 year product.

Aim at *"designs the whole vehicle including the engine"* and you are chasing something Honda
does with two thousand engineers.

---

## The two facts to keep in view

**1. Tool capability is not agent capability.** CATIA is used worldwide to design aircraft.
That is evidence about CATIA, not about an agent driving it. Whether an LLM can hold coherent
design intent across 10⁵–10⁶ operations is unproven by anyone. Layers B, C and F2 are the
attempt to make it true; none of them is a solved problem.

**2. Unattended sign-off on a safety-critical machine is not the goal and never becomes
possible.** The honest product is *"does 80% of the engineering in a tenth of the time; a
licensed engineer signs."* That is still a very large business, and it is one that can be
sold without lying about it.
