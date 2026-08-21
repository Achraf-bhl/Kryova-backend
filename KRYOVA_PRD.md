# Kryova — Product Requirements Document

**Status:** Draft v0.1 — early stage, pre-build
**Audience:** Claude Code (implementation), founding team
**One-liner:** An AI-native CAD + FEA simulation platform that lets small mechanical engineering teams, startups, and students go from geometry to a validated, simulated design in minutes instead of days — at a price a student or a 3-person startup can actually pay.

---

## 1. Problem

Mechanical design + simulation today is split across tools that were never built to talk to each other:

- **CAD** (SolidWorks, Fusion 360, Onshape, Creo) for geometry.
- **FEA/CFD** (Ansys, Abaqus, SimScale) for validating that geometry under load.
- **Optimization** (Altair HyperWorks, generative design add-ons) for improving it.
- **PLM/PDM** for tracking versions and decisions.

Each transition between these tools costs time: cleaning geometry for a solver, rebuilding an optimized shape back into something editable, re-entering boundary conditions, re-explaining design intent. For a large company this is an annoyance. For a 2-person hardware startup or a student with a class project, it's often a **wall**: full CAE suites cost $1,500–$2,500+/user/year (Onshape Professional), require IT setup, and assume a workflow (dedicated CAD engineer + dedicated analyst) that doesn't exist at that scale.

Meanwhile, a new generation of AI-native tools (Neural Concept, Leo AI, Synera, PhysicsX, MecAgent, Ansys Discovery, SimScale's AI assistant) is emerging — but nearly all of them are built for **enterprise retrofit**: plugging AI into an existing SolidWorks/Teamcenter/CATIA install at a company that already has all of that infrastructure. None of them are designed **CAD-in first**, agentic, and priced for someone starting from zero.

## 2. Target users

1. **Mechanical engineering startups (1–15 people)** — building a physical product, no dedicated simulation engineer, need to iterate fast and justify design decisions to investors/manufacturers without buying an Ansys seat.
2. **Small/mid engineering consultancies** — need faster turnaround on client FEA work without per-seat CAE licensing eating their margin.
3. **Engineering students & academic labs** — need real FEA/CAD capability for projects and theses, can't justify $1,500/year, currently stuck between free-but-crippled trials and pirated software.

## 3. Competitive landscape (as of 2026)

| Player | What they actually do | Where they're strong | Where they leave a gap |
|---|---|---|---|
| **Ansys Discovery / Ansys AI** | GPU-accelerated real-time FEA/CFD as you edit geometry | Near-instant feedback loop, industry trust | Expensive, enterprise-oriented, steep learning curve for non-specialists |
| **SimScale** | Cloud-native CFD/FEA/thermal, agentic AI assistant that guides simulation setup | True free Community tier (10 unrestricted sims), no install, good onboarding | AI assistant guides *setup*, doesn't generate or optimize geometry; still solver-centric, not CAD-native |
| **Onshape (+ Onshape Simulation)** | Cloud CAD with PDM built in, Professional tier adds FEA | Best-in-class cloud CAD, real version control | No permanent free commercial tier; Professional is $2,500/user/year; simulation is a bolt-on, not AI-driven |
| **Autodesk Fusion 360** | CAD + generative design + simulation extensions | Mature ecosystem, generative design is genuinely good | Full feature parity with Onshape Pro requires stacking paid extensions; generative design output still needs manual rebuild into editable CAD |
| **Neural Concept** | Geometric deep learning trained on your own CFD/FEA history, predicts physics from geometry in seconds | Extreme speedup (reported ~300x) once trained | Needs your own prior sim data to train on — useless to a team with no simulation history yet (i.e. most startups/students) |
| **Leo AI** | Retrieval layer over existing CAD vaults (SolidWorks PDM, Windchill, Teamcenter) — finds prior parts/decisions | Genuinely solves "what did we do last time" | Assumes you already have a vault full of prior work and an enterprise PLM. Zero value on day 1 for a new team |
| **Synera** | Multi-agent workflow: requirements → CAD generation → simulation, agents specialized per task | Closest existing product to a full "agentic design loop" | Early-stage, enterprise pilot-oriented, not self-serve, not priced for individuals |
| **PhysicsX / MecAgent / CognaSIM** | PINN-based neural solvers replacing/accelerating FEA (claimed ~50x) | Genuine solver speed innovation | Deep tech aimed at simulation specialists, not an integrated design tool a generalist can pick up |
| **CoLab Software** | AI-assisted design review — flags issues in CAD models/drawings during review | Good for team review workflows | Review-stage only, doesn't touch design or simulation generation |
| **Altair HyperWorks** | Automated topology/optimization loops | Powerful optimization | Traditional enterprise licensing, not AI-agentic, steep cost |

### Where the gap actually is

Nobody currently combines all of the following in one product:

1. **CAD-native from day one** (not a bolt-on to an existing vault or an existing CAD seat).
2. **A true agentic design→simulate→iterate loop** usable by a non-specialist (Synera is the closest, but not self-serve/affordable).
3. **No dependency on prior simulation data** to be useful (Neural Concept requires your own trained history; a new startup has none).
4. **Priced and packaged for 1–15 person teams and students**, not enterprise seats — meaning a real, usable free/cheap tier, not just a time-limited trial.
5. **Manufacturability/DFM awareness baked into the loop**, so a student or a startup without a manufacturing engineer doesn't design something unbuildable.

This is Kryova's opening: **"Ansys Discovery's real-time feedback + Synera's agentic loop + SimScale's free-tier accessibility, packaged for the team that has nobody dedicated to simulation."**

## 4. Product vision

Kryova is a browser-based (cloud-native) platform where a user:

1. Imports or sketches geometry (or generates a first pass from a natural-language spec + constraints).
2. Gets automatic, AI-driven meshing and boundary-condition suggestions instead of manual FEA setup.
3. Runs fast structural/thermal/modal simulations (AI-accelerated where possible, classical FEA solver as ground truth).
4. Gets an **agentic iteration loop**: Kryova proposes geometry changes to hit a target (mass, safety factor, stiffness, cost) and re-simulates automatically, showing a trade-off history — not a black box, an explainable log of what changed and why.
5. Gets a manufacturability flag (DFM basics: wall thickness, draft angles, tolerances) before the design goes further.
6. Can export standard formats (STEP, STL) and a simulation report (PDF) suitable for showing to an investor, a professor, or a manufacturing partner.

## 5. MVP scope (what to actually build first)

Keep the MVP narrow. Do **not** attempt a full CAD kernel replacement or a general-purpose PINN solver in v1.

**In scope for MVP:**
- Import CAD geometry (STEP/IGES/STL upload) — do not build a modeling kernel from scratch; use an existing open kernel (e.g. Open CASCADE) or a CAD-as-a-service API for geometry ops.
- Automatic mesh generation with sane defaults (Gmsh or similar as the engine).
- Linear static structural FEA (stress, deformation, factor of safety) using an open solver (e.g. CalculiX or a Python FEA backend) as ground truth — this is the credibility baseline.
- An AI layer on top that: (a) suggests boundary conditions/loads from context the user describes in plain language, (b) summarizes results in plain language, (c) proposes 2–3 concrete geometry modifications to hit a stated goal.
- Basic manufacturability checks (min wall thickness, sharp internal corners, draft angle for common processes).
- A results/report export (PDF) and STEP/STL export.
- Simple project workspace with version history (this is table stakes — Onshape's biggest edge is version control; don't ship without it).

**Explicitly out of scope for MVP:**
- CFD, thermal, dynamic/transient, or nonlinear FEA.
- Multi-agent orchestration (Synera-style) — start with one well-scoped assistant, not a swarm.
- Enterprise PLM integrations (Teamcenter, Windchill, etc.) — irrelevant to the target user at this stage.
- Training custom neural solvers on user data (Neural Concept's model) — needs data you won't have yet.
- Real-time as-you-drag simulation (Ansys Discovery-level) — extremely hard engineering; revisit post-MVP.

## 6. Suggested architecture

Given the existing repo layout (`Kryova-backend`, `Kryova-frontend`, Python `venv`, `requirements.txt`):

- **Backend:** Python (FastAPI recommended for async + easy OpenAPI docs). Modules:
  - `geometry/` — CAD import/export, geometry ops (Open CASCADE via `pythonocc-core` or a CAD API wrapper).
  - `mesh/` — Gmsh wrapper, mesh quality checks.
  - `solve/` — FEA solver interface (CalculiX subprocess or a Python FEA lib), abstracted behind a `Solver` interface so a faster/neural solver can be swapped in later without touching the rest of the stack.
  - `ai/` — LLM-driven layer: boundary condition inference from natural language, result summarization, iteration proposals. Keep this as a thin orchestration layer calling an LLM API with tool-calling into `geometry/`, `mesh/`, `solve/` — don't let the AI layer own geometry logic.
  - `dfm/` — rule-based manufacturability checks (start rule-based, not ML — faster to ship, easier to trust).
  - `projects/` — workspace, version history, auth.
- **Frontend:** whatever's already scaffolded (check `Kryova-frontend`) — needs a 3D viewer (three.js), a results/report panel, and a chat-style panel for the AI iteration loop.
- **Async jobs:** simulations are not instant — use a job queue (Celery/RQ or similar) so the frontend polls/websocket-subscribes to job status rather than blocking requests.
- **Storage:** object storage for CAD files/mesh/results (S3-compatible), relational DB for project/version metadata.

## 7. Differentiation summary (elevator pitch for the README)

> Every other AI-for-engineering tool today either (a) assumes you already have an enterprise CAD/PLM stack to plug into, or (b) needs your own historical simulation data to be useful. Kryova is built for the team that has neither: import geometry, describe your load case in plain language, get a real FEA result and an AI-proposed path to a better design — all in a browser, at a price a student or a first-time hardware founder can pay.

## 8. Open questions to resolve before/during build

- Which CAD kernel/API for geometry import-export: `pythonocc-core` (open source, more setup) vs. a paid CAD-as-a-service API (faster to integrate, recurring cost)?
- LLM provider and how much of the "agentic loop" is achievable with tool-calling + function results vs. needing a fine-tuned/specialized model.
- Pricing model for the free tier — flat simulation count (like SimScale's "10 unrestricted sims") is a proven pattern worth copying for the free tier.
- Which manufacturing process to optimize DFM rules for first (CNC vs. sheet metal vs. 3D printing) — pick one for MVP, don't try to cover all three.

## 9. Success metrics for MVP

- Time from CAD upload to first valid FEA result under 5 minutes for a simple part.
- At least 3 pilot users (student projects or a small startup) complete a full import → simulate → AI-suggested iteration → export loop without needing support.
- Manufacturability check catches at least the obvious DFM violations (near-zero-thickness walls, non-manufacturable internal geometry) in test parts.
