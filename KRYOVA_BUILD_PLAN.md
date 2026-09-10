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
> part a conversation is building. **Step 3 closed 2026-09-08.** Its first half landed on
> 2026-09-07 — the chat draws the picture a tool returns, so on the CATIA path the product
> shows its own work — and the OCCT half followed: `kernel-part-view.tsx` calls
> `/kernel/conversations/{id}/render` and pins the drawing to the live state above the
> composer, so a part built on the open kernel is no longer invisible in the product. The
> endpoint had existed since 2026-09-05 with nothing calling it.
> **Still to do from this gap: callers for vision / machine_checks / sensitivity.** Those
> three still have zero callers outside tests, so the sentence above them — phases green on
> capability and never connected to anything — remains true of that much of Era II.


**E5 — Assertions and self-correction.** Foundation 2026-09-04; **5.1, 5.3 and 5.4 all
landed 2026-09-05**. 5.4 did not in fact need E18's missions to exist first, which is what
the queue had assumed: what it needed was the *harness*, and M1 already built. So the
ladder now runs — 1/9 rungs green, 8 declared PENDING with the phase each waits on — and
E18 gains a rung by giving its `Mission` a spec and assertions rather than by starting from
nothing. **All that remains under E5 is 5.2**, requirement-bound assertions, genuinely
blocked on Phase 11: "meets REQ-014" needs REQ-014 to exist as an object. E5 therefore
keeps a bare number rather than a star.

**E4 closed 2026-09-08** — `*E4`, all four tasks done and tested. `app/render/` renders eight
canonical views deterministically, cuts sections and diffs two of them; `app/ai/vision.py` asks
a vision model whether the part matches the request; and 4.4, the last one outstanding and the
only one whose surface lived outside this repo, now draws both backends' parts in the chat.
**E3 closed the same day** — `*E3` — with one residual named in its phase proof rather than
hidden in it: cross-backend measurement agreement needs a Windows seat, the same shape as E1
task 7, and is claimed at no gate before then.

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


**E6 closed 2026-09-08** — `*E6`, all six tasks done and tested. CalculiX across a subprocess
boundary (Decision 4: GPL solvers are invoked as separate processes, never linked), with the
`Solver` ABC unchanged and the existing `loads.py`/`selection.py` vocabulary mapped onto
CalculiX sets rather than rewritten. The last two tasks closed together: 6.3's element strategy
(shells and beams, and what ccx does to them) and 6.4's thermal cards. **Three residuals are
named in the phase marker rather than hidden in it** — nothing here has been round-tripped
through a real `ccx`, nothing produces a shell or beam mesh yet, and loads on 1-D and 2-D
regions have no tributary-area rule, which is why `write_frame_deck` takes a force vector and
says so. E6 was also what turns three of 5.1's checks from honestly-unmeasured into measured,
and what 5.3's sensitivity can now be run over.

**Gate G1 is what comes next**, carrying rung 3 forward. It is the run that turns the whole of
this phase from documented into verified: every CalculiX keyword written since 2026-09-05 was
read from the manual on a machine with no solver on it.

**Everything that needs the Windows machine now lives in one checkbox list** — THE QUEUE at the
top of `docs/WINDOWS_VERIFICATION.md`, added 2026-09-08. Four groups by what each item needs
(`ccx`, a CATIA seat, a document, a vision model), ordered by what a run settles per minute.
The rule that keeps it worth reading is in `CLAUDE.md`: **a Linux session stopped by missing
hardware adds its row there in the same commit**, the way a finished task updates the master
plan. Until now those blockers were scattered across three phase statuses, a protocol document
and a gate note, and each one had to be rediscovered.

**E7 is the current stretch and it is not closed — deliberately, and the reason is now written
into the phase rather than left as a gap.** 7.2, 7.3 and 7.4 were already done; 7.1 went from
machinery-with-no-cases to a catalogue where **both benchmarks that can run validate and both
reach the published register** — **2 of 11 analyses validated** (modal against FV52,
linear-static against LE10), where that page had read 0 since it was written.

7.1 stays `PARTIAL`, and the three cases that remain blocked were audited rather than restated:
**LE1** needs plane-stress elements, **LE11** needs a per-node temperature field as well as its
geometry, and the NAFEMS *thermal* family needs the same. Each of those was a blocker recorded on
a case with **no task anywhere that would ever unblock it**, which is the silent-gap shape
`CLAUDE.md` forbids — so E7 gains **task 5** (plane stress/strain) and **task 6** (solve *for* a
temperature field, paired with E10.1 which records the same gap from the physics side). **LE3**
needs a shell solver, which cannot be settled on this machine at all — no `ccx` — so it is
**A6 in THE QUEUE**, sitting immediately after the four CalculiX items it depends on. E7
therefore carries no `✅ PHASE COMPLETE` marker and correctly so.

**Task 5 shipped the same day and LE1 validates** — see the top of *Done*. It is `PARTIAL`
rather than `DONE` for one honest reason: `PlaneSolver` is reachable from no route, job or
registry entry, so the element family exists and cannot yet be *asked* for. That is this
project's oldest failure mode (a capability built and never connected, the same finding that
opens *Now*), and the next unit of work on E7.5 is the wiring, not more physics.

**E7 after this stretch: everything Linux can close is closed.** Tasks 2, 3, 4 and 5 are done.
Task 6 has both halves — a prescribed field and a conduction solve. Task 1 is `PARTIAL` and
**stays that way until the Windows machine**: LE3 is a shell benchmark, a shell deck needs `ccx`,
there is none here, and it is `A6` in THE QUEUE. That is the whole of what stands between E7 and
its `✅ PHASE COMPLETE` marker, and the marker is withheld rather than taken on four-fifths of a
phase — one open task means no marker, however much has shipped.

**What is unwired, named rather than hidden**, because this is the failure mode that opens *Now*:
conduction is reachable by configuration but **no job type routes to it**, so it cannot yet be
asked for through a request — the last third of the gap E7.5 closed for the plane family. Its ABC,
registry entry and `observe` span landed 2026-09-09. The convergence half of this note is closed:
`grids` on a simulation runs a study through the real path.

**G1's remaining item is E7's, and it is the next thing worth building.** The gate's third open
item is that *no stress this product reports has ever been converged*: every number in both G1
runs came from a single 411-element tet4 mesh, stated without a second solve to compare against.
E7 tasks 1 and 2 built the machinery and validated it on three NAFEMS benchmarks — and nothing
in `app/simulation/runner.py` calls it. That is the same shape as E7.5's unwired `PlaneSolver`
and as the gap that opens *Now*: capability built, never connected. Until it is, the honest
thing is for a result to say in words that it comes from one mesh.

**What remains under E7 after that is task 6**, solving *for* a temperature field. It is the
only thing standing between the catalogue and LE11 plus the whole NAFEMS thermal family, and
E10.1 records the same gap from the physics side — the two move together or not at all. LE3 is
not E7's to close: it needs a shell solver, which needs `ccx`, which needs the Windows machine,
and it is **A6 in THE QUEUE**.

**2026-09-09, second stretch: two phases closed away from E7 — `*E17.3` and `*E14`.** Both were
one task short and both of those tasks were the same shape as the gap that opens *Now*: a
capability built and never connected. E17.3's sheet metal had no geometry at all, so M3 held a
hand-drawn solid *and* a fold tree and hoped they described the same object; E14's product
structure was frozen and safe while the product it described could be silently erased by the
second of two callers. Both are closed with their residuals named rather than hidden — no
sheet-metal operation on the **CATIA** side (that is `E1` in THE QUEUE, and declaring one here
would be a promise the bridge cannot keep), and the repository is in-process rather than
distributed (E15's storage question). See the top of *Done*.

**The Linux stretch ends at E7, 2026-09-09, and that is a deliberate stopping point.** Ten of
twenty-nine phases are complete, the suite is green, `ruff` and `mypy` are clean, and every
task in the plan that does **not** need hardware is closed. What is left for Linux to build
alone would be new phases (E8–E10, E12, E13, E15–E17) rather than finishing anything, and what
is left of the phases in flight is exactly what the Windows seat exists to settle. So the next
session is the Windows one, and its brief is the top of `docs/WINDOWS_VERIFICATION.md`.

> **That last sentence was overtaken within the day, and the correction is the useful part.**
> The seat ran A1–A5, and what it measured *created* Linux work rather than only consuming it:
> once a shell deck was known to solve, the reason LE3 could not run stopped being the deck and
> became a missing mesher and a missing load rule — both pure Linux. So the two machines are
> not sequential, they alternate, and the queue is the thing that hands work between them.
> A Windows measurement that reclassifies an item is expected to leave Linux something to do.

**2026-09-09, third stretch: `*E11` and `*E5`.** Nine of twenty-nine phases are now complete
and the programme is at 52% of tasks. What is left in flight is E7 (LE11's convergence, and
LE3 waiting on `ccx`), E15/E16 and the product track. The pattern that closed all four phases
today is the same one that opens *Now* and is worth stating as the working rule it has become:
**the remaining work in a "nearly done" phase is usually a connection, not a capability** —
sheet metal had no geometry, the product graph had no repository, the requirements model had no
caller, and a requirement's gap had never reached the thing that aims a repair.

**E7 is untouched by that stretch and its status is unchanged**: task 6's two halves are in hand,
LE11 is encoded and runs but its recorded outcome is still `unconverged` — the point stress at
corner A scatters with where nodes land, and the study correctly refuses to state a value from a
non-monotone triple — and task 1 stays `PARTIAL` on `A6`. The next unit of work there is LE11's
convergence, not more physics: the quantity is a point stress in a steep gradient, so the
question is whether a wider-spaced grid triple reaches the asymptotic range or whether the case
needs a different extraction stated up front rather than chosen after the sweep.

---

## Done
- **2026-09-10 — P5 and E16 closed; E15, P4, P9 and E18 advanced. Six phases in one session.**
  The thread running through all of it is that **every one of the four residuals P5 and E16 were
  carrying turned out to be a backend gap wearing a frontend label**, and naming them properly is
  what made them small.
  *Token streaming* was a provider-contract gap: `provider.py` returned a completed turn, so there
  was no shape for a partial answer. `stream_chat` yields `TextDelta`s then one `Finished`, and the
  base implementation emits **no deltas** rather than the whole answer as one — otherwise "the
  model wrote this at once" is indistinguishable from "this provider does not stream".
  *Reconnect-and-resume* needed an event cursor that survives a different worker, which is the
  mirror of P5.6's cancellation column: there the reader had to escape the runner's long
  transaction, here the writer does. `turn_events` is a ten-minute buffer, every event recorded
  before it is yielded, and the resume endpoint is a **GET** so a reconnect cannot start a second
  turn.
  *The editable spec panel* and *"edit a parameter mid-mission"* were both blocked on the same
  sentence — "`DesignSpec` is an in-memory IR with no model and no route" — and both landed once
  it was not. See `app/models/design.py`; the chain is append-only, a no-op save writes nothing,
  and there is no route that accepts a whole spec.
  **E16's three research-adjacent tasks were built deliberately small**, and the evidence is in
  the plan's own Era VIII: memory scaffolds degraded long-horizon performance in *all ten* models
  tested and additional orchestration does not consistently help. So the server holds the plan and
  refuses out-of-order moves rather than generating one; the state block carries the decisions
  rather than everything; and `recovery.py` escalates with a question built from the tool's own
  words rather than from a model call.
  **E15, P4 and P9 advanced with their residuals named rather than smoothed over.** The CATScript
  emitter has never been driven (no seat); autoscale needs a fleet; staging needs somewhere to
  deploy; the restore drill has never met a real backup; the container image has never been built.
  Each of those is written into the status as the thing that is missing, because a batch path
  nobody has run and a backup nobody has restored are the two failures this repository is most
  likely to talk itself into.
  **M6 joined the ladder by its own rule**: its declared `needs` (E14.1, E12.3) were both complete,
  so it moved from waiting to built without anybody deciding it should. It is the first rung whose
  difficulty is a *count* — the mass is computed twice, from the graph and from the pitches, and
  the two must agree, because a 12-metre conveyor built to a 6-metre bill of materials is
  arithmetic rather than geometry. Ladder now 4/9.
  Two latent defects found on the way: a **missing blob was being reported as an unsupported
  format** (advising "export it as PDF" about a file that had not finished uploading), and
  **`EnumText` autogenerates into a migration as an unimportable name**, which killed
  `alembic upgrade head` on the first new table to use it.
- **2026-09-10 — P10 closed: onboarding, the docs site, and the public status page.** All four
  tasks. The property that ties them together is that **every page derives from something that
  cannot drift**: the mission gallery from `app.design.missions.LADDER`, the API reference from
  this deployment's own OpenAPI document, the onboarding checklist from the account's real
  contents, and every guide step that names a route is checked against the running router by
  `tests/test_docs.py`. Documentation is the part of a product most likely to quietly stop being
  true, and deriving it is the only defence that does not depend on somebody remembering.
  The second thread is **publishing what is not claimed beside what is** — `Mission.unproven` in
  the gallery, `not_covered` in the guides, and a status page that refuses to infer an outage
  from a failure rate (no threshold in the module, and a test parses its AST to keep it that
  way). The one automatic transition is a critical announcement with no maintenance window, which
  is how an operator says "something is wrong and we have not gone read-only".
  The onboarding checklist has **no dismiss button and no stored flag**, on purpose: a
  flag-driven checklist can be fully ticked by somebody who has done none of it, and dismissed by
  somebody who is still stuck. A queued or failed run does not tick the last step.
  Named `app/handbook/`, not `app/docs/`, because `app/documents/` already parses customer
  drawings. Found two latent defects in earlier phases on the way — see below.
- **2026-09-10 — P5 advanced to 4/7: verification surface, approval gates, interruption, cost
  honesty.** Not closed, and the marker is deliberately absent: tasks 1, 2 and 3 stay `PARTIAL`
  with named reasons. Task 3's editable spec panel and task 6's "edit a parameter mid-mission"
  both need a persisted, addressable `DesignSpec`, and `grep` finds that type in `app/design/`,
  `app/optimise/` and `app/requirements/` but in **no model and no route**. A panel over an IR
  the server does not store would be a panel over a fiction.
  **Task 5, approval gates**, is the first reader of `Membership.domain_role`, whose docstring
  has said "16.5/P5 read this column" since P2.2 while nothing did. A gate is a row: the
  transcript is trimmed, an LLM paraphrase of an approval is not an approval, and "who signed
  this off and what did they see" is the question actually asked. The subject is pinned by digest
  so an approval cannot land on a design that moved while it was pending.
  **Task 6, interruption**, replaced a stop button that did not stop anything — it aborted the
  fetch, and its own comment admitted "stopping the stream does not stop the seat". The signal is
  a database column, because whoever presses stop and whoever is streaming are different workers,
  and an in-memory registry works perfectly under `--workers 1` and silently does nothing in
  production. Two honest partials are stated in the product rather than smoothed over: a tool
  call in flight finishes, and a solve already inside CalculiX finishes **and is still billed**.
- **2026-09-10 — Four defects found in earlier phases while building the above.**
  **(1)** The P3 maintenance check was a SELECT on every authenticated write, to read a table that
  is empty in essentially every deployment at essentially every moment. `test_projects.py`'s
  query-count tests caught it. Now a ten-second in-process cache of an immutable snapshot —
  a snapshot rather than the ORM row, because caching the row would hand a `DetachedInstanceError`
  to the next request during maintenance, on the path whose job is to explain the maintenance.
  **(2)** `scripts/plan_progress.py` counted statuses the plan had marked `<!-- superseded -->`.
  E4 task 4 shipped on 2026-09-08 with its `PARTIAL` superseded the same day, and the parser went
  on reporting the phase as carrying an open task — which obliged the phase-complete marker to
  *say* task 4 stayed `PARTIAL`, for two days after that stopped being true. A measurement tool
  that is wrong pushes its error into the document it measures, and the wrong sentence is the one
  a human then defends.
  **(3, 4)** `Announcement.level` and `ShareLink.revocation` were enum-typed columns stored as
  bare `String`, so a row loaded from the database gave a plain `str`:
  `announcement.level.value` raised `AttributeError` on any deployment that had actually
  published an announcement, while passing in every test that wrote and read one in one session.
  This is the `MessageRoleType` bug for the third and fourth time, so it now has a general fix,
  `models/types.EnumText`. No migration — the DDL is unchanged.
- **2026-09-10 — P8 closed: metering, plans, enforcement and estimates.** All four tasks.
  **(1) The last three meters are wired** and `unwired_meters()` is empty for the first time. AI
  tokens go through the one funnel every AI path already used; CATIA seat time through the one
  place every dispatch path converges, so there is no second timing site to disagree with the
  first. Both circular-import their way in — `app.core.metering` reaches `app.simulation.runner`
  which reaches back into `app.catia.dispatch` — so the import is deferred into the function, the
  pattern `core/lifecycle.py` already uses. **(2) The price list stays empty on purpose.** Plan
  allowances are now settings, all defaulting to 0/unset, because Decision 4 makes this product
  free and open and a self-hosted install has no price list at all. Inventing "the free tier gets
  3,600 solver-seconds" would have put a number where an engineer later reads it as settled — the
  fabrication Decision 3 exists to prevent, in the one place it would be billed for. **(3) A
  refusal is 402 with an envelope**, not 429 with a number: "there is none left" and "too fast"
  need different responses, and a 429 invites a retry that can only fail. **(4) An estimate is
  the same meter the bill is made of**, projected from this tenant's own rows, median not mean,
  and it carries *no number at all* when there is too little history. A cost estimate is the most
  tempting place in a product to invent a figure precisely because nobody ever checks it
  afterwards.
  Two tests had to be rewritten rather than weakened, and one of them said so in its own body:
  `test_an_unwired_meter_names_what_wiring_it_would_take` asserted at least one meter was unwired
  and instructed "if every meter is wired, delete this test rather than weaken it". It was
  deleted, and replaced with the claim that outlives the state — every meter says where its
  numbers come from, and a `MeterSite` still refuses to be unwired silently. A third test caught
  something real: breaking the share-lifetime ceiling proved nothing because the route test was
  measuring Pydantic, so the service-layer check got a test that drives it directly.
  `stripe` declared optional in `pyproject.toml` for the same reason `ezdxf` is.
- **2026-09-10 — P3 closed: the operations console.** Tasks 4, 5, 6 and 7. Four decisions worth
  carrying. **(1) `core/lifecycle.py` is the only writer of the account-state columns**, because
  a row with `suspended_at` set and `is_active` still true is visibly suspended in the console
  and fully usable through the API — and suspension revokes every session family, which is the
  difference between recording a suspension and applying one. **(2) The rollout is a hash, not a
  draw.** A random percentage flickers a feature on and off under one user, which is
  indistinguishable from a broken deployment; `sha256(key + subject)` also gives the property
  that widening a rollout only ever adds people, which is what makes a staged rollout safe to run
  forwards. The kill switch outranks every override, deliberately. **(3) Maintenance mode refuses
  with 503 and a sentence on mutating methods only.** A read-only mode that 500s is an outage
  with a nicer name; reads keep working so the application is not broken in front of the person
  waiting. The check sits in `get_current_user` rather than a middleware because it needs to know
  whether the caller is staff. `GET /platform/state` is readable during the window on purpose —
  the endpoint that explains it must not be one of the things it refuses. **(4) `success_rate` is
  `None`, not 0.0, when nothing ran**, and the dashboard says how it grouped failures, because
  the schema has no taxonomy class yet (E15.5) and a coarse grouping presented as a
  classification is how a console produces confident wrong decisions.
  Found and fixed on the way: **`components/ui/input.tsx` replaced its whole `className` when a
  caller passed one**, since `{...props}` was spread after it — the field kept its label and lost
  its border, height and focus ring, silently. `Button` had always merged. Pinned in
  `input.test.tsx` along with a test that the two primitives agree.
  Guards verified by breaking them: the kill switch, suspension-revokes-sessions, and
  maintenance-lets-reads-through. All files restored byte-identical.
- **2026-09-10 — P2 closed: sharing, hand-off, and the team surfaces.** Tasks 5 and 6, and the
  phase proof. Three findings. **(1) `create_invitation` had never invited anybody** — P2.1
  shipped the row, the token and the accept route in September and there was no mail transport,
  so the capability existed only as a declaration. It sends now, and the token is returned to
  the inviter *only* when delivery failed. Writing that branch nearly removed an existing
  security decision: the old rule withheld the token in production unconditionally, and
  "return it whenever delivery failed" would have quietly loosened it. Both rules are kept and
  the reasoning is in the code. **(2) A third `rls_statements()` exposed that the isolation test
  applied only the first migration it found.** `_p2_migration()` globbed the versions directory
  and returned on the first match, so the test schema had one migration's policies while
  production had three — and which one depended on filename sort order. The day a new migration
  sorted first, every assertion in that file would have run against tables with no policies on
  them. The fixture now applies all of them. **(3) A mutation test found an unpinned guard.**
  Breaking the share-lifetime ceiling in `core/sharing.py` did not fail anything: the route test
  was measuring `ShareLinkCreate`'s Pydantic bound, which answers 422 first. The service-layer
  check exists for callers that never see the schema, so it now has a test that drives `issue`
  directly — and that one does fail when the check is removed. Guards verified by breaking them:
  transfer-revokes-links, admin-at-the-destination, and the ceiling (after it was pinned).
- **2026-09-10 — P1 closed: identity, sessions and tokens.** Five open tasks (3, 5, 6, 7, 8) and
  the phase marker. Three findings worth carrying beyond the phase. **(1) There was no mail
  transport in this service at all** — the password-reset route said so in a TODO and everything
  else assumed one — so `app/mail/` was built here and P2's invitations, P3's impersonation
  notice and P10's status comms now have somewhere to go. Production refuses to boot on a
  transport that reaches nobody, which is the `SECRET_KEY=changeme` rule applied to the one other
  place where every component reports success and the user is silently stuck. **(2) The password
  reset was not revoking anything.** It cleared `refresh_token_hash` — the single-slot column
  task 1 replaced in September and nothing has read since — so a password changed *because* it
  was stolen left every device family alive. Found while wiring the theft notice; the reset now
  calls `revoke_all`. **(3) Task 6's status line was two-thirds stale**, describing an
  `X-Forwarded-For`-trusting limiter that had already been fixed; the real gap was that every
  limit keyed on the address even where a principal existed. Second factor is hand-written RFC
  6238 against the RFC's own vectors, with a replay guard and the secret sealed at rest. Three
  backend guards and one frontend guard verified by breaking them (production mail refusal → 2
  failures; verification gate → 1; TOTP burn → 2; `localStorage` custody → 1), all files restored
  byte-identical from full-path-keyed backups. `cryptography` declared in `requirements.txt`
  rather than left transitive, so it does not become the second `ezdxf`.
- **2026-09-10 — the open kernel's part can reach the solver, which is the defect the ladder run
  found the same day.** New E1 task 8, added because the phase was marked complete on every task
  it had and still did not deliver its own promise. On `GEOMETRY_BACKEND=occt` the agent could
  build a part and then do nothing to it: `catia_export_step` and `sync_geometry_from_catia` were
  the only geometry→solver routes in its vocabulary and both were CATIA-only, so `run_simulation`
  had nothing to mesh and ladder Levels 3–5 — plane analyses, conduction, convergence studies —
  were unreachable on the open kernel.
  **It was a seam, not a capability.** `manufacture.export.write_step` already worked and the
  kernel document already held the shape; nothing joined them. `_export_locally` writes the STEP
  and hands it to the same `import_step_export` the bridge path uses, so a version from the open
  kernel is indistinguishable downstream from one CATIA produced — which is the whole point.
  **The design call worth not undoing:** the export is served from the dispatcher via
  `backends.LOCALLY_SERVED` and is deliberately **not** in `HANDLERS`, because that table
  declares what OCCT implements of the geometry vocabulary and both `local_coverage()` and
  `compare_backends` read it as exactly that. *What is offered* and *what the kernel implements*
  are now different questions with a test pinning the difference.
  Also fixed: `import_step_export` hardcoded `"catia_bridge"` as the blob source and told users
  to "check the part in CATIA", which sends an open-kernel user to an application they are not
  running; and a multi-body document now names the bodies its export left behind instead of
  silently dropping them. Three guards verified by breaking them, 10 failures observed, all files
  restored byte-for-byte. **The class to remember: every tool worked and the suite was green —
  the gap was one layer above `dispatch`, in what the agent was *offered*.** Second time that
  class has shipped invisibly, so the new tests go through `call_catia`, not a runner.
- **2026-09-10 — ladder Levels 2 and 3 both pass for the first time, on DeepSeek.** Report:
  `docs/verification-2026-09-10/`; run log in the ladder. `AI_PROVIDER=openai_compatible`
  against `https://api.deepseek.com` with `deepseek-v4-pro` — **no provider code was needed**,
  only settings, and the key lives in the gitignored `.env.local`. A tool-calling probe returns
  a correct structured call in 1.27 s against 8–15 s locally.
  **L2 passed with the same prompt that failed four times on 2026-09-09**: 0.8600238 kg against
  a hand-checked 0.86002, fourteen operations, **zero refusals**, and
  `sketch_create(support="top")` succeeding first try — the bare face word added the day before,
  and the exact call that had failed four times.
  **L3 passed and is the best answer this product has produced.** Asked whether a peak stress
  was converged, it ran a real grid study (8 mm/1,620 el → 144.9 MPa, 4 mm/12,550 el →
  176.5 MPa) and said plainly that it is **not** converged — +22% on one refinement step —
  because a fully-clamped sharp corner is a stress singularity that rises without bound. It
  separated that from the quantity that does converge (tip deflection 2.213 → 2.158 mm), and
  offered the two real fixes. Beam theory gives 208.3 MPa and 2.258 mm; the FE deflection sits
  4% below, which is correct for a clamp that restrains warping.
  **One new defect, and it is why L3 had to move to the seat: a part built on the open kernel
  cannot reach the solver.** `catia_export_step` is not among the 116 occt operations, and
  nothing in them matches `export|save|step|geometry|sync|mesh|solve`; both geometry→solver
  routes are CATIA-only. It is a seam rather than a missing capability —
  `manufacture.export.write_step` works and the kernel document holds the shape — and it blocks
  ladder Levels 3–5 on `occt` along with every analysis feature this run was told to test. Same
  shape as CLAUDE.md's D2: the agent can build a part and then do nothing to it, invisible to
  the offline suite because the gap sits one layer above `dispatch`.
  **And `draft_load_case` still guesses the wrong axis** (recorded 2026-09-08, unfixed). The
  agent caught and corrected it itself this time, which makes it less visible rather than less
  real. The 32,768-token context wall from 2026-09-09 is gone: 18+ operations across three
  meshes in one conversation without truncation.
- **2026-09-10 — the STEP metadata half is measured, the lead left open was our own authoring,
  and the trip turns out to carry a number that is wrong by a thousand.** E21 task 1's
  remaining unit. The 2026-09-09 lead — "a flatness tolerance produced no `GEOMETRIC_TOLERANCE`
  entity with the writer returning success" — was two mistakes stacked: the tolerance had been
  bound to a label `XCAFDoc_ShapeTool` does not know, which OCCT drops silently, and **AP242
  writes the concrete subtype `FLATNESS_TOLERANCE`, so the string being grepped for is absent
  from a correct file**. Bound to the part label it writes. `app/manufacture/xde.py` is the XDE
  path (names, colours, layers, validation properties, assembly occurrences, one geometric
  tolerance) and `measure_metadata_round_trip` its matrix; **nothing in the product exports
  through it**, which is stated rather than implied.
  **Six classes moved from untried to measured and five carry**: part names, assembly
  occurrences with their names, colours, layers, validation properties. Three accessor traps
  found, each returning a wrong answer rather than an error — a colour written `ColorGen` comes
  back `ColorSurf`+`ColorCurv`; `GetLayers(shapeLabel, seq)` returns **`True` with an empty
  sequence** and the assignment lives on the layer's side; `SetPropsMode(True)` computes
  nothing and is a permission to transfer attributes that must already be there.
  **The sixth is neither carried nor lost.** A flatness tolerance authored at 0.05 mm reads
  back as **50.0 mm**: the writer emits the magnitude under `SI_UNIT($,.METRE.)` while the
  model is `.MILLI.`. A value written and read by one build through its own writer and reader
  does not survive its own round trip, which is what makes it a defect and not a convention
  misread. `Carriage.CORRUPTED` was added for it, because `LOST` is a lie in the safe
  direction — an absent tolerance gets queried, a tolerance a thousand times too loose gets
  manufactured to — and nothing compensates on the way out. `write_step_with_metadata` refuses
  a tolerance under AP214, where OCCT accepts, returns `RetDone` and writes none.
  Also found, and now in CLAUDE.md because it ends a process rather than raising:
  **`TDF_Label.FindAttribute(id, attr)` segfaults when the attribute is absent** — `IsAttribute`
  first, always. 53 tests across two files; five guards verified by breaking them, 17 failures
  and one deliberate segfault observed, three files restored byte-for-byte. THE QUEUE gained
  **B5**, the ten-minute seat measurement that decides whether the ×1000 is OCCT's writer or
  its reader — opposite conclusions, and Linux cannot choose between them.
- **2026-09-09 — a shell deck goes through ccx, and LE3 does not reach 185 mm.** Second seat
  session. Regression first and the engine checked before believing it: **7,374 passed / 2
  skipped / 1 xpassed / 0 failed** against real PostgreSQL (`TEST_DATABASE_URL` verified to
  resolve to `kryova_test`, not the SQLite fallback), ruff and mypy clean, V&V artefact current.
  **A6, the half that works.** A flat cantilever plate under pressure, solved first as the order
  requires: all four shell element types through ccx 2.23, against 36.585 mm (beam) / 33.51 mm
  (wide plate) — S3 **14.009** (−61.7%, linear triangles locking as they should), S4 **33.131**
  (−9.4%), S6 **35.541** (−2.9%), S8R **35.557** (−2.8%). The quadratic answers land between the
  beam and plate bounds. **The S8R negative corner loads do not upset ccx**, which was the
  specific thing to confirm.
  **A6, the half that does not, and the box stays unticked.** LE3 as a shell — closed pole,
  quarter sector, R = 10 000, t = 40, E = 68 250, ν = 0.3, the full 2 kN at A and C — reaches
  **1.21 mm** with the correct symmetry rotational constraints (−99.3%) and **199.5 mm** with
  translations only (+7.8%). Neither is 185. The pole treatment makes no difference at all: z at
  the pole, z at A and a clamped pole agree to three decimals. It is the **rotational constraints
  on the symmetry edges** that stiffen it 150×, and a six-DOF `clamp` works correctly on the flat
  plate, so rotational BCs do reach ccx — an *edge* of them is what over-constrains. Next
  suspicion is CalculiX's knot mechanism making each constrained node's expanded cross-section
  rigid. **The 2% tolerance was not loosened**; +7.8% comes from a reading that omits a condition
  the benchmark requires, so it is soft rather than right. One mesh returned 7.686 mm, a separate
  instability.
  Not reached this session: queue E3, tier 3 / section B, and Job 3.
- **2026-09-09 — the master plan gained Era VIII (E19–E23) from a verified research pass, and
  its first task found that this repo's AP242 claim is one OCCT cannot keep.** The era covers
  conformity (Machinery Regulation, AI Act), simulation credibility as ASME and NAFEMS define
  it, licensed data and interchange, the measured ceilings on surrogates and long-horizon
  agents, and positioning; ~28 engineer-months, priced in Part 4, so the programme total moved
  161 → 189 and the headline fell 52% → 46% — which is what writing down work that was always
  required looks like. E21 task 1 then started: `app/manufacture/interop.py` measures a
  Kryova→STEP→Kryova trip per entity class and **re-measures, rather than records**, that this
  build's `write.step.schema` takes `AP242DIS` and **refuses `AP242IS`** while accepting
  `AP214IS` — so the product may not claim a published AP242 edition, and the test says so by
  failing on purpose if OCCT ever gains the spelling. `NOT_ATTEMPTED` is kept distinct from
  `LOST` throughout: we never wrote colours, names, layers, validation properties or GD&T, and
  calling that "lost" would file a bug against OCCT for work we have not done. One lead left
  open rather than promoted to a finding: a flatness tolerance authored through XDE produced no
  tolerance entity in the file **with the writer returning success**. 19 tests, two guards
  verified by breaking them (14 failures observed, restore byte-for-byte).
- **2026-09-09 — sketching on a face was never the missing capability; one operation had been
  left behind a shipped phase.** `elements.plane_frame` is the single resolver every "which
  plane" argument goes through, and `catia_sketch_create` was the last one still holding a
  private accept-list: it refused a planar face while citing Phase 2.2, **which had already
  shipped**. So `catia_plane_offset(reference="slab#top")` resolved a face while
  `catia_sketch_create(support="slab#top")` refused one, and nothing on either side said the
  two disagreed. Worse, the **CATIA seat had accepted a bare `support="top"` all along**,
  resolving it against the part's bounding box — so the identical call built a part on
  `GEOMETRY_BACKEND=catia` and was refused on `occt`, which is the one thing Decision 1's
  conformance rests on not happening.
  Fixed: `catia_sketch_create` uses the shared resolver, and the open kernel gained the bare
  face words with the table **copied from `scripts/catia_bridge/com/_context.py`** rather than
  invented, so the two cannot drift apart by taste. Pinned by a test that checks the boss lands
  on the *right side* — a support resolving to the wrong face builds a boss hanging underneath
  and reports the same success. The unknown-plane refusal now names the syntax with a feature
  the part really has instead of a phase number, and a test that had asserted `"Phase 2.2" in
  message` was corrected: it had pinned the stale claim, written from the same belief as the
  code. Also: `catia_assembly_clash(components=[])` no longer refuses, because an empty
  *optional* list is the same as not sending it (a *required* empty list still reaches the
  validator), and the "removed no material" refusal now names its second cause — a sketch on a
  face extrudes along that face's outward normal, so a cut needs `reversed: true`.
  With those, the ladder's Level 2 part builds from the sentence as written: 109,278.760 mm³
  and 0.86002 kg, exactly the hand calculation.
  Re-run on a **rebooted CATIA** with the backend started without the `occt` override — which
  is what had sent the earlier run to the open kernel and is why nothing appeared in CATIA. It
  built on the seat for real (`Extrusion.1`, `Révolution.1`, `Poche.1`, `Congé arête.2`, seat
  timings, part in the tree) and stopped on the **32,768-token context window**, not on a
  defect. That is now the binding constraint on a seat run of this length, ahead of tool
  coverage.
- **2026-09-09 — LE3's geometry is sourced, and it does not have the hole everyone says it
  has.** `docs/nafems-le3-geometry.md`, written to `nafems-le11-geometry.md`'s pattern.
  **R = 10 m, t = 0.04 m, a 90 degree quarter sector, and the shell is _closed at the pole_.**
  The primary source is a **scan of the original NAFEMS dimensioned figure** (TechSoft3D's HOOPS
  report, p. 6), which states the radius twice — as `x²+y²+z²=100` and as `r = 10m` — and it is
  corroborated by ESRD p. 12 (which, unlike LE11, gives LE3's dimensions in *text*), Altair
  OS-V:0030, the DIANA verification manual, and a committed Abaqus deck (`c3m-labs/ImportMesh`,
  `NAFEMS TEST LE3`) whose **80 nodes were downloaded and measured**: radius 9.999935–10.000061,
  polar angle 0.0000–90.0000 degrees, azimuth 0.000–90.000.
  **The "hemisphere with an 18 degree hole at the pole" is a different benchmark wearing LE3's
  name**, and this repository's own notes — including the entry immediately below, as first
  written — asserted the hole from memory for several hours before the sourcing caught it. Node
  121 of the committed deck is `(0, 0, 10)`: the pole is a node, so there is no hole. That is
  `C1`'s "do not transcribe a value from memory" rule earning its keep twice in one day, the
  first being FV52's 4% figure.
  Three disagreements were found and resolved rather than smoothed: Altair's **4000 N** is a
  *pair* total and the load is **2 kN per point** (its own next sentence proves it); the last
  rigid-body z restraint sits at E in three sources and at A in the committed deck, shown to be
  the same model; and Altair cites NAFEMS R0015, a natural-frequency publication, which is a slip
  on that page. **Do not halve the loads for the symmetry planes** — two independent quarter-model
  sources apply the full 2 kN, and halving is the plausible-looking mistake that would report
  roughly half the displacement. One value is INFERRED and marked: that the 10 m is the
  *mid-surface* radius, which is moot if LE3 is encoded as a shell, which it should be.
  §5 carries `SOURCES` entries drafted for paste into `app/verify/nafems.py`; they are **not**
  pasted yet, because that edit belongs in the same commit as the `run_le3` it cites for, and
  `run_le3` waits on a `ccx` run. Dead ends are recorded in §7 so nobody re-spends the time —
  notably **FeenoX has no LE3 example**, so the LE1/LE11 technique does not work here.
- **2026-09-09 — a shell can now be meshed and loaded, and what stopped LE3 is down to one
  seam.** THE QUEUE A6 was reclassified that morning from a tick to a phase task, on the
  strength of the seat's finding that the shell *deck* was never the only obstacle. Two of the
  three things it then named are now built, on Linux, and A6 is back to being small.
  **The mesher**: `gmsh_mesher.generate_shell_mesh` turns a curved surface in three dimensions
  into a `ShellMesh` in all four element types — the thing `generate_tri_mesh` exists to
  refuse, and they cannot share a loader because a plane model is a cross-section that must lie
  in z = 0 and a shell is a surface whose whole point is to be curved. Checked against a
  revolved 90 degree spherical zone, open at the pole, whose exact area it recovers to 0.1%,
  converging from *below* as straight-edged elements chord a sphere. (This entry said "LE3's
  own shape" until LE3's geometry was sourced hours later. It is not: **LE3 is closed at the
  pole**, and the hole here is a fixture convenience — see below.)
  **It settled the ordering debt `app/mesh/structural.py` had carried since 6.3 was written.**
  That module adopted CalculiX's midside order rather than gmsh's, because there was no
  producer to disagree with, and named the consequence: a future mesher must permute at the
  boundary. Measured against gmsh 4.15.2 the permutation is the **identity** for both shapes —
  gmsh's 6-node triangle numbers 0-1, 1-2, 0-2 against CalculiX's 0-1, 1-2, 2-0, which are the
  same node *pairs* with the last wound the other way. It stays absent because
  `_assert_shell_midside_ordering` re-checks it by coordinate on every quadratic mesh, not
  because the two were assumed to agree; a gmsh release that renumbered fails loudly instead of
  returning a plausible, wrong stiffness matrix.
  **The load path**: `app/solve/shell_loads.py`, E6.3's other named residual — "loads on 1-D
  and 2-D regions have no tributary-area rule". The 2-D half now has one, and the fact worth
  carrying out of it is that **an S8R face's four corners take −1/12 of the area each**, not a
  positive share: the serendipity corner shape functions integrate negative, so a
  tributary-area intuition loads a quadratic quadrilateral wrongly in a way that solves
  cleanly and reports the right *resultant*. Mesh-independence is tested by refining rather
  than asserted, and the guards were checked by breaking each one and watching a named test
  fail — which is how the first draft was found to catch an equal-split regression with only
  one test, since an equal split delivers the correct resultant and passes everything else.
  66 tests across `tests/test_mesh_shell.py` and `tests/test_shell_loads.py`; ruff and mypy
  clean; V&V re-recorded, still 3/5 validated.
  **An adversarial review of the two new files found three things and they are worth carrying.**
  The eight shape-function integrals were re-derived independently, twice — once from textbook
  shape functions and once from gmsh's own `getBasisFunctions` — and all eight are right. What
  was wrong was around them. **(a) The solid refusal was bypassed for STL**: the check is an
  `elif` on file format, an STL has no topology to ask about, and a watertight box therefore
  meshed cleanly into the hollow shell the docstring promised to refuse, reporting exactly
  6200 mm². Now refused on the *mesh* — every edge shared by two faces means it encloses a
  volume — which catches a sewn-closed STEP as well. **(b) `np.allclose`'s default `rtol=1e-5`
  is relative to the absolute coordinate**, so the midside-ordering assertion silently stopped
  detecting a swapped slot on a part authored far from the origin: caught at x = 0 and x = 1e3,
  passed at x = 1e5, which is an ordinary assembly-coordinate export. Fixed with `rtol=0`; note
  the tet and plane versions of that line have the same defect and were left alone.
  **(c) There is no consistent *edge* load**, so a cantilever tip load — how most shell
  benchmarks are posed — falls into the equal-split fallback and puts 2.7× too much on an end
  corner. Warned rather than silent, and named in the module docstring rather than fixed.
  **And then the seam itself closed, the same day.** `app/solve/calculix/shell.py` —
  `ShellSolver.solve(mesh, case, section)` joins mesher, load path, deck writer, `run_ccx` and
  `frd.py` into one run returning a `SolveOutput`. It is **not** a `Solver` subclass and that is
  the decision: the ABC is `solve(mesh: TetMesh, case: LoadCase)`, a shell needs its
  `ShellSection` as a third argument, and widening the ABC would put an argument on every solver
  in the codebase that is mandatory for exactly one of them — `PlaneSolver` declined the same
  widening for the same reason. **No shell `.frd` reader turned out to be needed**: `displacements`
  and `nodal_stress_tensor` key off node count, and `OUTPUT=2D` — which the seat measured in A3 —
  makes that the submitted numbering. `write_shell_deck` is split out from `solve` deliberately,
  because that is the half a machine with no `ccx` can test, and that is the machine it was
  written on. `summarise_shell_static` shares `_summarise` with the solid path so the two cannot
  drift on what a factor of safety means. 16 more tests.
  **So what is left of A6 is one measurement and one piece of research**, both named in
  `docs/WINDOWS_VERIFICATION.md`: **nothing has ever run this through a real `ccx`**, and LE3's
  geometry is **not sourced** — the Abaqus page carries the loads, the material and the target
  but puts the radius, thickness and hole angle in a figure, so it needs the LE11 treatment and a
  `docs/nafems-le3-geometry.md` before any number reaches code.
- **2026-09-09 — the ladder's first run under the new method: L1 passes, L2 does not, three
  defects between the model and the tools.** Report: `docs/verification-2026-09-09/`; run log in
  `docs/GUI_PROMPT_LADDER.md`. Driven through the web GUI on `occt` with `qwen3.5:9b` at 81% GPU.
  **L1 passed** — an aluminium tube measured 65,973 mm³ and 0.178 kg against a hand-checked
  65,973.446 and 0.17813, with a hatched section cut proving a real bore. **L2 failed four
  times**, and the part is not the problem: driving the same sequence through the runner gives
  109,278.760 mm³ and 0.86002 kg, exactly the hand calculation. The wall is that **there is no
  way to sketch on a face** — `support="top"` is refused because face selection needs
  `feature#selector` (roadmap A3, blocked behind A1) — so the most ordinary Level 2 sentence
  there is depends on a 9B model finding a two-step offset-plane workaround from an error
  message.
  Three fixes, each verified by breaking it. **A correct mass shipped with provenance denying
  it**: the measurement cache is density-free by design and `_weighed` wrote the mass on top
  without touching the sidecar, so `catia_measure` returned 0.178 kg beside `unavailable — no
  density has been set on this part`, and `assertions.py` would have verified it UNMEASURED for
  ever — Decision 3 firing on a number that *was* measured. **A refusal denied a working tool
  existed**: `catia_pad` with `limit="up_to_surface"` came back as "catia_pad is not implemented
  in the open kernel yet", though it had succeeded two calls earlier; `OperationNotSupported`
  carries a subject precisely so a capability gap can be told from a missing operation, and
  dispatch discarded it along with a reason naming what to do instead. The agent believed it and
  went looking for CATIA's interface. **And `distance_mm="10"` was refused unrecoverably** — the
  validator is right, the model sent a string, but this is the scalar case of the H4/H5 array
  repair and now has `_parse_number_strings`, scoped so `"10 mm"`, `"1,5"`, `"ten"` and `"true"`
  still reach the validator untouched.
- **2026-09-09 — ccx has now read a deck this repo wrote, and two beams could never have
  solved.** THE QUEUE section A (A1–A5) is measured and ticked; `app/solve/calculix/` goes from
  *documented* to *verified*. **A1**: the solid bar deck solves on ccx 2.23, `.frd` back at 81/81
  nodes, σ = F/A 25.000 → 25.0888 MPa (0.355%), and the oracle agrees to six figures. **A2**, the
  first thermal comparison ever run: a restrained bar at ΔT = 50 K gives −119.925 MPa closed form,
  **119.925000 from both solvers (0.0000%)**, with `uniform_field=True` so the stress was judged
  and not merely reported — the cards written blind on 2026-09-08 are right. **A3**: `OUTPUT=2D`
  holds on `*SHELL SECTION` and `*BEAM SECTION` alike — S4 9/9, S8R 21/21, S3 9/9, S6 25/25, beam
  5/5, all at the submitted numbering. **A5**: the expansion table is confirmed by node count
  (B31→8/C3D8I, B32→20/C3D20R, S4→8/C3D8I).
  **A4 is where it earned its place.** The data-line order it asks about is correct as written —
  and running it found two defects neither offline test could see, both fatal to beams.
  `BeamMesh.connectivity` returned `[start, end, middle]`, but ccx numbers a three-node beam
  *along the member*, so the far end was taken for the midside node and the element folded back on
  itself: `*ERROR in e_c3d: nonpositive jacobian determinant`. **No quadratic beam deck this repo
  ever wrote could solve**, and the test pinning that order was wrong in the same direction as the
  code, which is exactly the failure mode `docs/WINDOWS_VERIFICATION.md` warns a first run to look
  for. Second, CalculiX carries `SECTION=BOX` and `SECTION=PIPE` on **B32R only** — swept across
  B31/B32/B32R x RECT/CIRC/PIPE/BOX and one-to-eight data values, so it is the section type that
  decides it and not the value count — while Kryova wrote B31/B32, so an RHS produced a deck
  refused at parse time. `choose_element` now takes the section and substitutes B32R, refusing a
  *linear* mesh in words because there is no three-node element to substitute. Both fixes checked
  by submitting the product's own deck to the real solver: B32R + BOX now exits 0 with 5/5 nodes
  where the same deck on B31 is still refused. Shells were unaffected, and all four are checked.
  ruff and mypy clean; 609 solver/mesh tests green.
- **2026-09-09 — the Windows seat runs the suite for the first time, and six defects fall out.**
  Tiers 1 and 2 of `docs/WINDOWS_VERIFICATION.md` are green here: **927 offline tests**
  (including the 124 that had only ever been import-checked, and an iso render looked at with
  human eyes — right way up) and **7,244 passed / 2 skipped / 1 xpassed / 0 failed** against real
  PostgreSQL 18.6 at `b941a651831a`.
  **The first database run was not a database run.** `TEST_DATABASE_URL` was unset, so
  `conftest.py` fell back to in-memory SQLite exactly as CLAUDE.md warns, and reported *23 failed,
  7,204 passed, 17 skipped*. Against a real `kryova_test`: *10 failed, 7,234 passed, 2 skipped,
  1 xpassed* — fifteen tests that had been skipping themselves started running and the RLS test
  began to XPASS. A green tick from that tier means nothing until you know which engine it ran on.
  Four of the six defects were invisible on Linux by construction. **`code_fingerprint` was not
  stable across checkouts** though its docstring said it was — it keyed on `str(path)`
  (`app\solve\...` on Windows) and hashed raw bytes (CRLF under `core.autocrlf=true`), so every
  recorded validation outcome was discarded here and the trust page published *nothing is
  validated*; neither normalisation alone reproduces a Linux recording, and both are pinned
  separately. **The local Postgres role was `SUPERUSER`**, which outranks `FORCE ROW LEVEL
  SECURITY` and left Decision 7's safety net inert on this machine — the recipe in
  `docs/LOCAL_POSTGRES.md` said to create it that way, and both recipe and machine are corrected.
  **`scripts/plan_progress.py` crashed on Windows** with `UnicodeDecodeError`, because
  `read_text()` takes the locale codec and cp1252 cannot read the plan's em-dashes — the very
  command this file tells every session to run when it finishes work; the block was current all
  along. **Eight `test_catia_local_bridge` tests** hit a `sys.platform`-guarded `tasklist` probe
  for the first time: the process double is not a context manager, so `subprocess.run` raised
  inside a broad handler, and the probe's own call shifted every positional index into the
  captured spawn list — and two of them read the *real* probe, so their result depended on whether
  CATIA happened to be open. A conduction tolerance was absolute where it had to be relative
  (`1e-12 W` vs a measured `1.28e-12`, relative `1.7e-13` — LAPACK, not physics), and
  `CONDUCTION_BACKEND` was undocumented in `.env.example`. Guards verified by breaking each
  normalisation separately and watching a named test fail, restores checked back.
- **2026-09-09 — E7 closes and the Linux stretch stops here (`*E7`, 10/29 phases).**
  Task 6's last third: **a temperature field now reaches a request.** `analysis:
  "thermal-conduction"` with a `thermal_case` solves through the queue, the mesher, the
  registry and the job row — a 60 mm bar held at 400 K and 300 K comes back at exactly those.
  The physics landed in the morning of 2026-09-09 and the seam the same day, and neither made
  it reachable; this is the third time in four days the codebase has found a capability built
  and never connected, which is why that sentence is now the working rule in *Next*.
  Migration `b941a651831a` gives the job a `thermal_case` and makes `load_case` **nullable**:
  a conduction run reads no fixture, no force and no modulus, and an empty `LoadCase` would
  put a material nobody chose into the provenance of a temperature field. Supplying one anyway
  is refused, as is a thermal case on a structural run — both would be ignored while looking
  like part of the model. `CONDUCTION_BACKEND` is its own setting, because `SOLVER_BACKEND`
  names a solver chosen for a different analysis; asking for `calculix` is still refused **by
  name**. A `grids > 1` study is refused (the study assesses peak von Mises stress and a
  temperature field has none), the stored archive carries `temperatures_k` rather than zeroed
  displacements, and the AI interpreter refuses a run with no load case instead of writing
  fluent prose about one. `tests/test_simulations.py::TestAConductionAnalysisCanBeAskedFor`
  (11); five guards verified by breaking them.

  **E7 takes its marker with LE3 named as a hardware wait**, not as unwritten work: a shell
  benchmark needs a shell deck through a real `ccx`, there is none on Linux, and it is `A6` in
  THE QUEUE with its four prerequisites listed. LE11 stays `unconverged` in the recorded
  artefact and that is published as the measurement it is — a point stress at a corner in a
  steep gradient scatters with where nodes land, and the study refuses to state a value from a
  non-monotone triple.

  **The three documents were rewritten for the handover in the same commit.**
  `docs/WINDOWS_VERIFICATION.md` opens with a brief addressed to the Windows session — three
  jobs in order, three standing rules, and what "done" means for a queue item — and THE QUEUE
  gained a **section E: work that needs a seat to *write*, not only to verify** (the CATIA side
  of sheet metal, the four stop gates, the conduction oracle against ccx).
  `docs/GUI_PROMPT_LADDER.md` is no longer fifty pre-written prompts: it is a **method**, six
  levels, each saying what it must test and what it must not re-test, with **one prompt per
  level written on the day** against what the plan says has just shipped. Its three hard rules
  — one prompt per level, a screenshot every time, no moving on until the level passes — are
  repeated in `CLAUDE.md` because they are what a hurried session drops.
- **2026-09-09 — two more phases closed, and they closed each other: `*E11` and `*E5`.**

  **E11 was mostly written and partly mis-stated.** Task 3 (traceability) read `NOT STARTED`
  while `app/requirements/trace.py` had answered both directions since 2026-09-06 — audited,
  not rewritten, and the status line corrected. What was genuinely missing was **flow-up**: a
  top-level requirement names no measurement of its own, so verified requirement by requirement
  the one the customer signed came back NOT VERIFIED for ever while every derived requirement
  under it passed. A requirement with nothing to measure and children in the set now takes its
  verdict from them, to a fixed point (the graph is already refused if cyclic, so iterating
  until nothing moves is exact and resolves a grandparent the round after its child). A derived
  verdict is **not a measurement** — `by decomposition` is its own evidence basis and its own
  coverage column, and the caveat "sound only as far as the decomposition is complete" is
  printed beside the verdict. A violated child fails its parent; a parent that names its own
  measurement keeps its own number. One construction rule moved without weakening: the "a
  requirement nothing checks is a wish" refusal is now `RequirementSet`'s, where the upward
  decomposition links are visible, so a top-level requirement no longer has to claim a missing
  capability to be writable.

  **And the product can now be handed a specification.**
  `POST /kernel/conversations/{id}/requirements` parses a `.kreq` document, measures the part
  the conversation built, and answers with the report, its coverage and its evidence —
  `app/requirements/` was 2,860 lines that nothing outside a test had ever called, the same
  integration gap `render` and `measure` closed one layer down. Closing it exposed a second:
  the OCCT measurement payload attached **no provenance at all** (only the interrogation scans
  did), so every requirement met by an exactly integrated volume reported `unrecorded` and
  `Coverage.by_measurement` was zero for every real part. `metrology.measure` now records a
  basis per path — `measured` for integrations and traversals, `approximated` for the oriented
  bounding box (the box is exact for a given orientation; the orientation is a search),
  `unavailable` with a reason for a mass with no density.

  **E5 task 2** had been `BLOCKED` on E11 since 2026-09-05. The readable half was already there
  — a requirement compiles to an assertion named after its id, so a report says "REQ-014 NOT
  MET" — and the useful half is that the same result's `gap` reaches `sensitivity.aim`. Pinned
  end to end on a part OCCT builds: a 60x40x20 steel plate weighs 0.37776 kg, REQ-014 asks for
  0.30, and `aim` answers *reduce thickness_mm to 15.882*, which rebuilds to 0.30 kg. Neither
  layer imports the other; they meet at a number. Two facts the test records rather than hides:
  which parameter to move is **named, not discovered** (mass is equally elastic in all three
  dimensions of a plate, so `most_influential` is a genuine tie), and a first-order step aimed
  at a hard bound lands *on* it — 0.30000000000000093 kg — which a zero-tolerance requirement
  correctly refuses, so the requirement carries 1 g of slack rather than the comparison being
  loosened.
  `tests/test_requirements_verification.py` (+11), `tests/test_requirements_model.py` (+2),
  `tests/test_kernel_routes.py` (+8), `tests/test_requirements_against_a_part.py` (+2); seven
  guards verified by breaking them.
- **2026-09-09 — two phases closed: sheet metal reaches geometry (`*E17.3`), and a product can
  hold two authors (`*E14`).**

  **E17.3 task 3** — a `SheetMetalPart` now builds as an OCCT solid, so the blank and the part
  are one calculation instead of two sets of numbers typed twice. `app/sheetmetal/fold.py` places
  it (a frame per flange, a cylindrical sector per bend, holes on their faces, and the closed-form
  volume) with no kernel imported, so it stays as cheap to test as the flat pattern;
  `app/kernel/occt/sheetmetal.py` builds it and measures **exactly** the analytic volume on every
  case tried. The tie is an identity rather than a tolerance: both layouts consume one
  `unfold.tangent_extents` walk — extracted for this — and the folded volume differs from
  `blank area × t` by exactly `Σ θ·t²·w·(0.5 − K)`, zero at K = 0.5, which is the closed form of
  the residual `missions.py` publishes as `flat.volume_mismatch_mm3` as M3's only evidence that
  the drawing and the part were the same object. Four traps are recorded in the code because each
  builds a plausible wrong part rather than failing: the bend centre is `t + r` beyond the frame
  plane bending up and `r` below it bending down; the sector sweeps along the **bend**, never its
  rotation axis (which for an up bend is `−v`, and sweeping it mirrors the part at identical
  volume); `gp_Ax2(P, n, u)` is the frame that fills `u×[0,L], v×[0,W], n×[0,t]`; and the arcs go
  through a midpoint, because this OCP build's two-point `GC_MakeArcOfCircle` returns the major
  arc for both senses. A declared hole is cut, not ignored; a hole in a bend zone is refused by
  the blank's own message; a part whose *blank* would overlap is **not** refused, because it folds
  perfectly well and an over-refusal is the failure mode `app/catia/` warns about. No sheet-metal
  operation was added to the CATIA registry — that would be a promise the bridge cannot keep — so
  the seat half is **E1 in THE QUEUE**. `tests/test_sheetmetal_fold.py` (27),
  `tests/test_kernel_sheetmetal.py` (18), six guards verified by breaking them.

  **E14 task 5** — `app/assembly/locking.py`. The hole it closes is one no frozen data structure
  can see: two callers read the head, each build a new structure, each store theirs, and the
  second erases the first with nothing raising. `ProductRepository.commit` is optimistic — every
  commit names the revision it was written against and a stale one is refused with what moved and
  who moved it — and `LeaseBook` is the early half, blocking another author's commit that touches
  a claimed component. Holding a lease does **not** excuse a stale base, and a test pins that.
  Leases are per component, never per occurrence; **there is no clock in the module**, since an
  expiry read from the machine's clock cannot be tested without sleeping or reasoned about across
  processes; a merge takes disjoint edits and refuses by name where both sides changed one
  component, while two identical changes do not conflict (components compare by value, so a
  rebuild-from-spec workflow stays mergeable); and a commit that changed nothing is refused,
  because a revision recording no work makes every later "what happened here" misleading. It is
  **not a distributed lock** — one in-process object, so two API workers would each be internally
  consistent and collectively wrong — and that is written into the package docstring rather than
  left to be discovered. `tests/test_assembly_locking.py` (40), six guards verified by breaking
  them.
- **2026-09-09 — "how far along are we" is now a measurement rather than an opinion.**
  `scripts/plan_progress.py` reads the status line under every task in the master plan and the
  engineer-month figures in its Part 4, and writes the roll-up into a marked block near the top of
  that file. **Nothing about the number is typed**, which is the whole point: a percentage written
  by hand is wrong from the next status change onward and stays silent about it — the same defect
  as the validation register's hand-written blocked-case count, which twice made unblocking a case
  look like a regression. Two smaller decisions. **Part 4 is parsed, not copied**, and a phase it
  fails to cover aborts the run rather than being weighted at zero, because a silently-missing
  phase would flatter the total. And **the block states what it is not** — progress against the
  plan, not against a product, with almost every `DONE` proven only by the offline suite and no
  stop gate yet run — since a bare percentage beside a plan this ambitious reads as a promise.
  First reading: **5 of 29 phases complete, 49% of tasks, 48% of the 161 engineer-months**;
  engineering 53%, product 30%; eleven phases with nothing finished in them yet.
  `--check` reports staleness; regenerate with `--write` in the commit that moves a status.
- **2026-09-09 — conduction reaches its seams: a fourth solver ABC, a parallel registry and a
  span.** Built in the morning and reachable from nothing; connected now. Two decisions carry the
  weight. **`ConductionSolver` keeps its own output type** rather than sharing `SolveOutput`:
  `PlanarSolver` shares it because a plane result genuinely *is* those four fields, while a
  temperature field would have to ride in a `displacements` array under a name that lies — the
  seam is worth more than the reuse. And **the registry gained a parallel table rather than an
  entry in `_FACTORIES`**, because `_FACTORIES` is typed to `Solver` (that is what the runner
  holds), so admitting a second ABC would make it return a union and relocate the branch the four
  ABCs exist to prevent one level up into the factory. Asking for `calculix` is refused *by name*,
  saying `*HEAT TRANSFER` exists in ccx and is not federated yet, rather than quietly returning
  the in-house solver; `solver_version` is not duplicated, since a version is a fact about a
  backend and not about an analysis.
  The concrete class became `SteadyConductionSolver` so the class name and the recorded `name`
  agree, matching `ModalSolver`/`ModalEigenSolver`. The span reports `degrees_of_freedom` even
  though it equals `nodes` here — one temperature per node against three displacements is exactly
  what makes a conduction duration comparable with a static one. Seven guards verified by
  breaking them. `tests/test_conduction.py` (58 → 69), `tests/test_solver_registry.py` (20 → 30).
  Still open: no job type routes to it, and there is no `CONDUCTION_BACKEND` setting.
- **2026-09-09 — a converged answer is now something you can ask for, which closes G1's last
  Kryova-side item.** `grids` on a simulation (migration `9b30db1018ad`): 1 is a single run, 3 or
  more solves the same case on successively finer meshes and assesses the peak stress with a Grid
  Convergence Index. E7 task 2 built that machinery and validated it on three NAFEMS benchmarks,
  and **nothing in the request path had ever called it** — which is why every stress this product
  had reported came from one mesh, and why G1 reported a factor of safety of 1303 off a single
  411-element tet4 grid with nothing beside it.
  Four decisions worth their space. **The size you give is the coarsest grid, not the finest**, so
  asking for a study can only cost more *time* than the single run — never more memory than the
  caller has already shown it can afford; refining below a chosen size is the direction that runs
  out of RAM. **The finest grid's result is what is stored and drawn**, with the verdict beside it
  rather than instead of it: a caller who paid for three grids and got a picture of the coarsest
  would rightly disbelieve all of it. **Two grids are refused by name** — two give a difference and
  no way to separate "nearly converged" from "these two meshes happen to be similar" — rather than
  silently promoted to three, because the caller asked for a specific amount of work. And the
  study's own levels ride in `mesh_stats`, because a verdict with no working shown is one nobody
  can audit. The 1.4 spacing is not a guess: it is what the NAFEMS studies settled on, and 1.2 is
  measurably inside gmsh's remeshing noise.
  Six guards verified by breaking them. `tests/test_simulations.py` (43 → 49).
- **2026-09-09 — E10 task 1's status was false and is superseded.** It read *"conduction is
  genuinely missing"*, written 2026-09-03 and true for six days. Steady conduction shipped under
  E7.6; what is *actually* missing on that task is now **transient** conduction — no time
  integration, no heat capacity, no initial condition — which is a real gap rather than a wiring
  one, and the task now says that instead. Recorded as its own line because a plan that quietly
  keeps a stale status is worse than one that never had it: the next session reads it and builds
  something that already exists.
- **2026-09-09 — a prescribed temperature *field*, which is what NAFEMS LE11 actually needed.**
  E7.6 names two different things and they had been collapsed into one. A field can be
  **prescribed** — stated as a formula of position, which is what LE11 does with
  `sqrt(x²+y²)+z` — or **solved** from boundary conditions, which is a conduction analysis. LE11
  was blocked on the first and the plan said the second; only the first is needed to run it.
  `thermal_strain`, `thermal_load` and `thermal_stress_correction` now take one number or one
  value per element, and the arithmetic is identical either way — thermal strain is a local
  quantity and always was. `LinearStaticSolver.solve` takes an optional `temperatures=` array.
  **It is a solver argument and deliberately not a field on `LoadCase`**: a case is stored as
  JSONB on the job row and meant to be read by a person, and one number per node is data the size
  of the mesh that also stops matching the mesh the moment either changes.
  Verified against closed form, and the second check exists because the first could not do the
  job. A bar held at both ends under an *axial* gradient carries **constant** stress equal to
  `-Eα` times the **mean** temperature — equilibrium forces `dσ/dx = 0` — so an implementation
  that quietly replaced the field with its own average passes it. It did: that break ran green.
  A gradient *across* the section separates them, because nothing makes σ independent of y, and
  the measured spread is 90% of `EαΔT` where an averaged field gives essentially zero. Five
  guards verified by breaking them; the fifth only after the test that could see it existed.
  One defect found on the way, by running rather than reading: the nodal stress recovery was
  still passing `case.delta_t_k` where the rest of the solve had moved to the field, so a
  prescribed field produced a load but no thermal correction — the classic thermal-stress bug
  arriving through a half-finished rename. `tests/test_thermal.py` (14 → 21).
- **2026-09-09 — E7.5 closed: a plane analysis is something you can now ask for.** It was
  `PARTIAL` for one day for a single reason — `PlaneSolver` was reachable from no route, job or
  registry — and that is this project's oldest failure mode, the same sentence that opens *Now*.
  A simulation job carries `analysis` (`solid`/`plane-stress`/`plane-strain`) and `thickness_mm`
  (migration `490d3f517ca6`, backwards-compatible by construction); the runner branches to a
  triangular mesh and the plane solver; the API takes both. **A 40 × 200 sheet pulled at 4 kN
  returns 20.0 MPa through the real route**, which is `F/(w·t)` exactly.
  Four decisions, each written where it is enforced. `analysis` is NOT NULL with a server default
  of `solid`, so every pre-existing row keeps its meaning — and the guard for that is a test that
  clears the column and reads it back, because the **Python** default is unreachable (the route
  always sets it explicitly) and breaking it changes nothing. That is worth remembering: a
  `default=` and a `server_default=` protect different things, and only one of them protects
  history. `thickness_mm` is refused rather than defaulted on a plane run and refused rather than
  ignored on a solid. The job records the solver that *ran*, not `SOLVER_BACKEND`'s choice, which
  selects between the two solid solvers and never sees a plane model. Six guards verified by
  breaking them; `tests/test_simulations.py` (33 → 43).
- **2026-09-09 — NAFEMS LE11 runs, and reports `UNCONVERGED`. That is the result.** Both its
  blockers fell in one session — the geometry was sourced from a reprinted NAFEMS figure, and
  the per-node temperature field was built — and the case now solves. Every grid lands within 2%
  of the published −105 MPa. The convergence study still refuses to state a value, and it is
  right to: point A is a corner where the inner sphere meets the base plane, so the quantity is
  a point stress in a steep gradient that scatters with where nodes happen to fall. Over a
  seven-size sweep the answer wandered between −105.3 and −107.1 MPa — every level inside the
  band, no three consecutive levels monotone.
  **The temptation here was to pick a triple that converged, and it had to be refused.** One
  exists; it is selected by choosing the answer rather than by the rule, and the rule (every
  level resolves the geometry, spacing ≥1.3 in representative size, take the finest admissible
  triple) selects a set whose observed order comes out *negative* — the change grows under
  refinement. So the honest encoding is a case that runs, is `MEASURED`, and is never a pass.
  **A case that runs and refuses is worth more than one that sits blocked**: it is a capability
  finding, and what it says is that a tetrahedral mesh cannot state this corner stress to better
  than its own noise, while the published solutions that can use curved-hex or p-version
  elements. That sentence is in the case's own references, not just here.
  Consequences worth noting: the catalogue is now 4 of 5 running, 3 validated; `Blocker` is down
  to a single member, because the test that a blocker no case backs must fail deleted both
  `NO_PLANE_STRESS_ELEMENT` and `GEOMETRY_NOT_BUILT` on the day their case started running; and
  **only LE3 is blocked, on hardware**. `tests/test_verify_nafems.py` (67 → 73),
  `tests/test_le11_geometry.py` (31), `app/verify/le11_geometry.py`.
  One OCP defect recorded on the way: `GC_MakeArcOfCircle(gp_Circ, p1, p2, sense)` returns the
  **major** arc for *both* values of `sense` on this build — the outer profile came out wrapping
  329° the wrong way. The three-point overload is correct and is what ships.
- **2026-09-09 — steady-state conduction: the product can solve *for* a temperature field.**
  `app/solve/conduction.py`, built as the other half of E7.6 — Dirichlet, convection (Robin) and
  heat-flux boundaries over the existing `Selector` vocabulary, plus a uniform volumetric source.
  Closed forms rather than recorded output: the linear bar profile to **6.6e-12 K** on both
  element orders, the logarithmic tube wall at observed order 1.79/1.64 (tet4) and 2.00 (tet10),
  the convecting-bar Biot tip temperature to 6.8e-12 K, `q''x/k` to 2.7e-11 K.
  Three things worth keeping. **tet10 is barely ahead of tet4 on the cylinder**, and that is not
  a broken element — `promote_to_tet10` puts midsides at straight-edge midpoints, so the wall
  stays a polygon and the geometric error is O(h²) whatever the interpolation does; documented,
  because the obvious reading of those two numbers is that the quadratic element is wrong. **A
  floating model is refused twice**, structurally and by heat balance, because a *declared* film
  that selected no facets is not an assembled one and a singular matrix is sometimes factored
  with a tiny pivot and no warning. And **20 of 21 injected defects are caught by a named test
  while the 21st is labelled unpinned in the source** rather than presented as verified — five of
  the twenty needed the test strengthened before they were caught, including two field-sampling
  paths that a uniform or linear field cannot separate at all.
  Conductivity is on the case rather than on `Material`, so nothing inherits an unchecked
  default; watts enter the unit system here and convert exactly once, the way density does in the
  CalculiX deck writer. `tests/test_conduction.py` (58, offline, 0.33 s).
  Wired later the same day — ABC, parallel registry, `observe` span; see the entry above. What
  remains is that no job type routes to it, so it is selectable by configuration and not yet by a
  request.
- **2026-09-09 — NAFEMS LE11's geometry is sourced, and the technique generalised twice.**
  `docs/nafems-le11-geometry.md`. LE1 taught us that when a reproducing manual omits geometry you
  read a free solver's committed test input; here that worked *and* something better turned up —
  **ESRD's benchmarks guide reprints the original NAFEMS dimensioned figure as an image**, which
  text extraction silently skips and a 500 dpi render reads straight off. Nine of ten dimensions
  agree exactly across three independent documents.
  **The tenth is the interesting one.** The figure prints the spherical band height as 0.700 m;
  FeenoX builds it as `1.0·cos45° = 0.707107`. A third source is not an independent vote — its
  number is 0.700 carried through. Settled by the figure's own other annotations: at 0.700 the
  junction sits at radius 0.994983 on a sphere the same figure gives as radius 1.0, five
  millimetres off a surface it is supposed to lie on, so 0.700 is the rounded one. Labelled
  INFERRED with the arithmetic shown, and FeenoX's −105.04 MPa is recorded as corroboration and
  explicitly **not** as proof — ESRD's own runs from the printed figure land at −105.2 to −105.5,
  so the target cannot tell the two readings apart. That distinction is the whole reason this
  file exists rather than a remembered number.
  Two things surfaced for the encoding lane: LE11's temperature field is defined in **metres**
  while this codebase is mm-N-MPa and converts nothing, so it must become
  `(sqrt(x²+y²)+z)/1000` — used unchanged it is 1000× wrong with no error anywhere; and Abaqus
  C3D20 is 1.7% low on a fine mesh, so a tolerance tighter than ~2% would fail on element
  quality rather than on correctness. The dead ends are logged too, so nobody repeats them.
- **2026-09-09 — every result now says what mesh it came from, which is G1's third open item.**
  The gate reported a factor of safety of 1303 off one 411-element tet4 mesh with nothing beside
  it saying that is all it was. `StaticResult.mesh_convergence` is **always present** and
  defaults to `single-grid`: an optional field is absent on every path nobody remembered, and
  absence reads as "fine". An empty `warnings` list could not carry it either, because empty
  means both "nothing was wrong" and "nobody looked" — and telling those apart is the whole of
  Decision 3. `converged` is never true on one grid however fine it is, since a single solve
  contains no evidence about its own discretisation error; `MeshConvergence.from_study` is the
  only route to a true, it carries the study's own verdict rather than laundering it, and it
  reports the GCI as a percentage because every reader of that field is a person. The claim also
  reaches the model that explains the run — a correct field the user never sees would have left
  G1's failure exactly where it was — and the interpret prompt requires an unconverged number to
  be called one **in the summary beside the headline**, not in a trailing caveat. Six guards
  verified by breaking them.
  Still open, and now the only part of this left: nothing *runs* a study in the request path, so
  every result honestly says `single-grid`. Making it optional-but-available is E7's to finish.
- **2026-09-09 — a break script corrupted the tree a second time, in a new way, and the lesson is
  now in `CLAUDE.md`.** This morning it was a poisoned backup; this time it was
  `/tmp/$(basename $f)` — and this tree has both `app/models/simulation.py` and
  `app/schemas/simulation.py`. One backup file for two sources: the second `cp` overwrote the
  first, and the restore wrote the *model* over the *schema*. `diff` against the backup reported
  IDENTICAL for both, and the symptom was an unrelated
  `InvalidRequestError: Table 'simulation_jobs' is already defined`. Recovered with
  `git checkout --` plus a re-apply, which worked only because the edit was small enough to
  remember. Key backups on the full path.
- **2026-09-09 — G1's two Kryova-side defects: a part can now name its own ends, and a drafted
  load case is checked against the part it was drafted for.** These are items 1 and 2 of the
  three `docs/verification-2026-09-08-G1/` left open, and they are the `!` — the gate's answer
  was wrong and *nothing in Kryova said so*. The model chose `left` and `right` on a beam whose
  long axis was Z, so the run clamped one long side face and pushed the opposite one along the
  beam's own length: two faces **20 mm apart on a 200 mm part**, factor of safety **1303**,
  reported as a result. The agent caught it unprompted. Our own validation did not.
  **The root cause was one argument.** The bounding box reached the *prompt* and stopped there —
  `realise()` never saw it — so the vocabulary could not express "the far end" and no drafted
  case could be compared against the geometry. Passing the box in answers both.
  **`far end` and `near end` are now face words**, resolved against whichever axis the part is
  longest in. The point is not that the model was told more firmly; it is that *turning a box
  into an axis was a reasoning step and the step is gone*. The words mean X on a flat plate and
  Z on this beam, and the reading is recorded as an assumption because it is a choice the
  engineer did not make. With no box they are **refused by name** — never resolved to a default
  axis, because a default axis is precisely the defect they were added to remove.
  **Three sense checks**, all measured against the box rather than guessed: a face that is both
  held and loaded; a support and a load on faces less than a tenth of the part's longest extent
  apart; and a force acting mainly along the part's length applied to a face that is not an end.
  The gate's own answer now comes back carrying *"the left face is held and the right face is
  loaded, and they are only 20 mm apart across the part's x direction — the part is 200 mm long
  in z"*, and it names the words that would fix it.
  **They warn rather than refuse, and that is the decision.** A short span is a legitimate load
  case — a bolted flange in compression — and this codebase's rule about over-refusal is that the
  agent's recovery from one is to try something else, so an over-refusal becomes a wrongly built
  part rather than a caught one. What was missing at G1 was not permission to run; it was anybody
  saying the number looked wrong.
  **One check was wrong on its first draft and the test caught it, which is the useful part.**
  It flagged any force lying in the plane of its face — which is a transverse tip load on a
  cantilever, the commonest *correct* load case there is. A warning on the right answer trains
  everyone to skip the list. Narrowed to the shape that is genuinely odd, and pinned by
  `test_a_correct_cantilever_is_not_warned_about`.
  Seven guards verified by breaking what they guard. The seventh break was too weak the first
  time — the prompt named the words twice, so deleting one occurrence proved nothing — which
  showed the test was guarding a string rather than the contract; it now reads `FaceName` off the
  schema and asserts every word the model may emit is taught, so adding a word and forgetting to
  teach it turns red. `tests/test_load_case_sketch.py` (23 → 47). **G1's third item, a
  convergence check in the request path, stays open and is E7's**: the machinery exists and is
  validated, and nothing calls it.
- **2026-09-08 — twelve pre-existing failures on `main`, and what the split turned out to be.**
  All twelve green; the suite went to 6,451 passing / 0 failing (7,141 as of 2026-09-09). The
  useful part is the breakdown, not the fix. **One was a real defect**: the verification nudge
  discarded the "nothing has actually been done" message the exhausted correction loop had just
  written, so a model that ran nothing closed the turn with "Done." and a footnote — the exact
  silent failure `test_written_tool_calls.py` exists to prevent, arriving from the other side.
  **One was a tripwire working correctly**: `KryovaFaceMap` was added to the frozen VBA library
  and the test fails on purpose until a human reviews it (reviewed, accepted). **One was a stale
  artefact**: the BM25 index predated the chunker changes; rebuilt, P@1 back to 94.7%. **The
  other nine were tests that had rotted**, each differently — a fixture whose user message
  accidentally carried a measurable requirement, a stub one call short of the code it fakes, a
  volume oracle coupled to how many times the code reads a volume, a negative probe naming a
  tool the system prompt has since started teaching, and a `caplog` assertion over every logger
  in the process rather than the one under test. **None of the nine was wrong about what it
  claimed** — every one was wrong about *how it checked it*, which is why they all failed on a
  change to something else. Moved here from `CLAUDE.md`'s landmine list on 2026-09-09: a
  struck-through entry telling you not to act on it is history, and history lives in this file.
- **2026-09-08 — E7.5: the plane-stress element family, and LE1 validated through it.** Built in
  four lanes by parallel agents on disjoint files, integrated here. `app/mesh/planar.py` holds
  `TriMesh` (tri3/tri6), `gmsh_mesher.generate_tri_mesh` meshes a planar face,
  `app/solve/plane.py` holds `PlaneState`/`PlaneCase`/`PlanarSolver`/`PlaneSolver`, and
  `app/verify/` became dimension-aware. **LE1 comes back at 92.40783 MPa against the published
  92.7, −0.315%**, GCI 0.05% over three monotone grids, in **4.1 seconds against LE10's 44** —
  the same curved boundary on a tenth of the degrees of freedom, which is the whole argument for
  a plane family, measured rather than asserted. Trust page: **3 of 5 cases**, still 2 of 11
  analyses, because LE1 and LE10 are both linear-static and the register counts what it says.
  **Three decisions worth the space.** `TriMesh` carries **(n, 3) nodes at z = 0** rather than
  (n, 2), so every geometric selector in `app/solve/selection.py` — the vocabulary Decision 2
  calls the real asset — works on a plane model with no fork at all; the third column is
  meaningless and drifting off it is refused rather than projected. `PlanarSolver` is a sibling
  of `Solver` for `ModalSolver`'s reason, but returns the **same** `SolveOutput`, because the
  input differing is what forbids a shared method and the output not differing is what forbids a
  second output type. And the convergence layer had to learn that **`h = (A/N)^(1/2)`**: the cube
  root on an area inflates the observed order by exactly 3/2, and measured on LE1's own recorded
  levels it does not merely inflate 4.69 into 7.03 — it pushes past `MAXIMUM_CREDIBLE_ORDER` and
  **refuses the case**. A wrong number that looks plausible, caught by a test written to state the
  3/2 explicitly.
  **The sourcing was half the work and generalises.** LE1 was recorded as blocked on geometry
  nobody has; three vendor manuals quote its target and none prints its ellipses. FeenoX commits
  the Gmsh input for its own LE1 case, and the four numbers in it are the ones the
  independently-sourced LE10 entry already carried — LE1 and LE10 are **one plan geometry**, now
  written once as `ANNULUS_*`. When a reproducing manual omits geometry, read a free solver's
  test inputs.
  **Two things are named rather than hidden.** The observed order is 4.69 against a formal 2, so
  the grids are not strictly asymptotic and the GCI understates the error — Richardson gives
  ~92.46 where the reference is 92.7 — so the study is handed the formal order and publishes the
  discrepancy as a caution. And **`PlaneSolver` is reachable from no route, job or registry
  entry**: the capability exists and the wiring does not, which is this project's oldest failure
  mode, so E7.5 is `PARTIAL` and says why.
  Solver verification is separate from the benchmark and stronger: σ = F/A and δ = FL/AE to 1e-9
  on both orders, `G = E/(2(1+ν))` from pure shear, both idealisations separated by SZZ and by
  the out-of-plane strain, restrained thermal stress in both, a tri6 quadratic patch test, and
  Lame's thick-walled cylinder at −0.360% converging 1.049% → 0.360% → 0.107%.
  `tests/test_solver_plane.py` (63), `tests/test_mesh_planar.py` (43),
  `tests/test_verify_convergence.py` (+26), `tests/test_verify_nafems.py` (+21). Seventeen guards
  verified by breaking what they guard. Also deleted `Blocker.NO_PLANE_STRESS_ELEMENT` the moment
  LE1 ran — a blocker no case backs fails a test, which is exactly the point of the enum.
  **One defect survived every lane's own checks and was caught only by the whole suite**, which
  is the argument for running it after an integration rather than trusting four green lanes:
  `plane.py` opened an `observe.span` named `solve.plane` that `app/observe/catalogue.py` did not
  declare, so the report would have had a timing nobody could name. No lane could have seen it.
  The two observe tests caught it in the right order — one that every span in `app/` is declared,
  then a snapshot of the wired set, so a new hook has to be *acknowledged* rather than appearing
  unannounced. The generalisation is in `CLAUDE.md`: a new module usually owes an entry to some
  registry (`observe/catalogue.py`, `solve/registry.py`, `db/models.py`, `api/router.py`), and
  each of those is "unregistered means invisible" in its own way.
- **2026-09-08 — a break-verification script poisoned its own backup, and the fingerprint caught
  it.** Worth recording because it nearly cost a false result. The script that breaks each guard
  in turn restores from a `/tmp` copy taken at start-up; run it twice, or start it on a tree an
  earlier run left dirty, and the "pristine" copy *is* the broken one. It happened: LE10's solid
  was left one-piece, so its three tests failed under every later break for a reason none of
  those breaks had caused, and three "the guard caught it" results were measuring the earlier
  mutation. `diff` against the backup said everything was fine, because the backup was wrong.
  What caught it was `code_fingerprint()` — the recorded artefact's hash of every file that
  decides an answer — reading `058b40…` where the artefact said `6b87af…`. Restoring the fused
  solid took it back to `6b87af…` exactly, which is a byte-level proof the tree is the one that
  produced the recorded result. `CLAUDE.md` now says to check the fingerprint after a break run
  rather than trusting a backup. The accident also produced a *cleaner* verification of the guard
  than the script would have: with the one-piece solid the study reports observed order **−0.339**
  on levels +6.37 / −0.28 / −5.93 and three named tests fail; with the fused solid all 46 pass.
- **2026-09-08 — E7.1, third half: LE10 validates, and the three defects it took to get there
  were all invisible to every existing test.** LE10 is the thick elliptical plate under pressure,
  and it is a harder benchmark than FV52 in the way that matters: it asks for a *stress component
  at a point* on a *curved solid*, where FV52 asks for a frequency on a box. It now reads
  **−5.36376 MPa against the published −5.38 MPa, 0.302%**, inside a ±2% band that was fixed
  while the case was still catalogued as blocked and was not touched afterwards. GCI 2.227%,
  observed order 2.31, three grids at h = 245/165/115 mm, 44 s. **The trust page reads 2 of 11.**
  Two interfaces were widened on purpose rather than worked around: `SolveOutput` gained an
  optional `nodal_stress` tensor — optional so a surrogate still satisfies the `Solver` ABC, which
  is the whole point of that seam — and the region vocabulary gained `EllipticalWallSelector`,
  which selects on **normalised radius**, because a distance tolerance on an ellipse is a
  different physical band at every point of the wall. Both are recorded in the master plan as
  decisions with what they cost. **The three defects are the reason the case is worth having**,
  and none of them looks like a defect from inside the code: **(a)** nodal stress was being built
  by averaging element-centroid values, which under-reads a plate surface in bending by ~25% and
  *shrinks with refinement*, so it reads as a converging answer rather than as an offset — tet10
  stress is now evaluated at each node's own natural coordinate, with the centroid kept as the
  superconvergent point for the headline peak; **(b)** the mid-plane support had no geometry to
  land on, because a one-piece extrusion has no edge at mid-thickness — the `uz` ring selected 2,
  38 and 5 nodes on the three meshes and the answer scattered between −3.2 and −6.1 MPa, which
  reads as solver noise and is a modelling error; the plate is now two half-thickness solids
  fused; **(c)** a coarse mesh of a curved solid is a **smaller part**, not a coarser mesh — at
  h = 300 the meshed volume was 7% short — so the run computes the exact volume from the semi-axes
  and refuses a level more than 0.5% short. The three grid sizes were chosen on two rules stated
  *before* the sweep, not picked from twenty triples afterwards: every level must resolve the
  geometry, and the spacing must be wide enough that the discretisation trend dominates gmsh's
  remeshing noise. Also: **C1 in THE QUEUE was half wrong** — LE10's semi-axes are stated in full
  in the Abaqus manual that reproduces the target, so the document nobody has was never needed;
  that row is closed with the generalisable lesson kept. Seven more guards verified by breaking
  them. `tests/test_verify_nafems.py` (31 → 46), plus new classes in `test_solver.py`,
  `test_solve_selection.py` and `test_verify_convergence.py`; ruff and mypy clean.
- **2026-09-08 — E7 gains tasks 5 and 6, and THE QUEUE gains A6, because three blockers had no
  owner.** LE1, LE3, LE11 and the NAFEMS thermal family were each recorded as blocked on a
  capability, and not one of those capabilities appeared as a task anywhere in the plan — so the
  catalogue was describing work nobody was going to do, which is exactly the shape `CLAUDE.md`
  calls a silent gap. Plane stress/strain (LE1) and solving *for* a temperature field (LE11 and
  the thermal family) are now E7 tasks 5 and 6; the shell solver (LE3) needs `ccx` and so is
  A6 in THE QUEUE, placed after the four CalculiX items it depends on. E7 stays open and now
  says why in a form somebody can act on.
- **2026-09-08 — E7.1, second half: the validated benchmark reaches the product, and expires when
  the code does.** FV52 validated in the test suite and the trust page still read *0 of 11*, which
  is this project's oldest failure mode arriving in a new place — a capability built and never
  connected. The register cannot simply run the case: it is served unauthenticated and a
  validation case is several solves, so `assert_publishable` refuses one and always will. The
  join is `app/verify/recorded.py` and a committed artefact,
  `data/verify/validation-outcomes.json`, written by `python -m app.verify.recorded`. **The
  interesting half is that a recording is a claim about the code that made it.** A page that kept
  serving Tuesday's "validated" after Wednesday's solver change would be asserting something
  nobody has checked — Decision 3 defeated through a cache rather than through a lie. So the
  artefact carries a fingerprint of every source file that decides an answer (`app/solve/`,
  `app/mesh/`, and `benchmarks`/`convergence`/`nafems`/`provenance`/`quantities`), and a mismatch
  publishes **nothing**, with the reason, so the page goes back to "nothing is validated" rather
  than to a green tick. `register.py`, `changelog.py`, `commitments.py` and `recorded.py` are
  outside the fingerprint on purpose: an artefact that expires when somebody rewords a note is one
  people regenerate without reading, and the guard is then gone while still appearing to be there.
  Every failure mode returns *no outcomes with a reason* instead of raising — missing, truncated,
  wrong schema, a `Target` that will not rebuild through its own constructor — on
  `KnowledgeService.search`'s rule, because a trust page that 500s publishes less than one that
  says the evidence could not be read. **The register now reads 1 of 11**, modal against FV52,
  with the deviation and the citation beside it. Six more guards verified by breaking them,
  including the one that matters: editing `app/solve/modal.py` without re-recording fails the
  suite. `tests/test_verify_recorded.py` (14); suite 6604 → 6618 passing, 0 failing; ruff and
  mypy clean. Three tests that had asserted "nothing is validated" were rewritten rather than
  relaxed — each carried a note saying the day it changed somebody had to come here and say so.
- **2026-09-08 — E7.1: the first benchmark this product has ever validated against a published
  number, and the two rules that stopped it being a fabricated one.** `app/verify/benchmarks.py`
  had been machinery with no instances since it was written, and the register said so — 0
  validated of 11, because there were no cases to roll up. `app/verify/nafems.py` is the
  catalogue: five standard NAFEMS cases, every target reproduced from a publicly readable vendor
  verification manual and cited with the section, the NAFEMS publication it credits, the URL and
  the date it was read. **FV52 runs and validates** — a 10 m square plate 1 m thick, held out of
  plane along the four edges of its underside only, so three rigid-body modes come first and the
  fundamental flexural mode is mode 4. Three geometrically similar tet10 grids, GCI 1.05%,
  observed order 2.05, **43.624 Hz against the published 44.092**, 1.06% inside a ±5% band fixed
  before the case was first run. **The four blocked cases are the useful half**: LE1 wants
  plane-stress elements, LE3 wants a shell solver — E6's residual, seen from the other side —
  and LE10 and LE11 want shapes nobody has authored, which is work rather than a capability gap
  and the reason says which. Each keeps its published target, because the number we must
  eventually produce is most of what a benchmark is worth. **Two rules paid for themselves
  immediately.** The catalogue's own rule that a target must be read off a document, not
  recalled: a second vendor manual quotes 45.897 Hz for the same mode, our answer converges
  through 44.10, and the disagreement is recorded on the case rather than settled by preference.
  And a live defect in the register's publisher — `_PATH_RE` matched the `s:/` inside `https://`
  and `scrub` replaces the *whole* string, so on a page whose entire value is checkable
  references every citation would have been published as "[withheld: looked like a filesystem
  path]". A drive letter is one character; the lookbehind now says so, and the same false
  positive was in `tests/test_trust.py`'s copy of the pattern, which is what caught it. The
  register now publishes the blocked half of the catalogue and filters the runnable case out
  **by construction** rather than by a rule somebody must remember: `assert_publishable` runs at
  import, so selecting the catalogue wholesale would have made the day LE10 becomes runnable the
  day the application stops booting. `tests/test_verify_nafems.py` (31 tests), nine guards
  verified by breaking what they guard; suite 6568 → 6604 passing, 0 failing; ruff and mypy
  clean. E7 stays open: 7.1 is `PARTIAL`.
- **2026-09-08 — E6 closed: shells and beams reach the deck, and a temperature change that had
  been reaching only one of the two solvers.** 6.4 first, because it was a **defect and not a
  gap**: `LoadCase.delta_t_k` was assembled into a thermal load by `linear_static` and written
  into a CalculiX deck **nowhere at all**, so the same case returned `sigma = -E alpha dT` from
  one solver and exactly zero from the other, both with success on the job row and neither with
  a warning. 6.5's oracle exists to catch exactly that and had never been pointed at a thermal
  case. The fix is three cards rather than one — `*INITIAL CONDITIONS, TYPE=TEMPERATURE`,
  `*EXPANSION, ZERO=` and `*TEMPERATURE` — because the physics reads `alpha * (T - ZERO)` and
  two of those numbers are the two halves of the subtraction; the test that pins it **moves the
  reference off zero**, since at zero a deck writer that dropped the reference entirely would
  pass. That test immediately earned itself: `initial_temperature_lines` had the reference as a
  *default argument*, which Python evaluates once at import, so one card would have gone on
  reading 0.0 while the other two moved. And a material with no expansion coefficient is now
  refused by name — ccx accepts that deck, applies the temperature to a material that cannot
  respond to it, and reports zero thermal stress, which reads as a finding rather than as a gap.

  **6.3 is the element strategy, and the interesting content is what CalculiX does *after* it
  reads the deck.** It has no shell or beam formulation: it expands them into solids
  (S3→C3D6, S4→C3D8I, S6→C3D15, S8R→C3D20R, B31→C3D8I, B32→C3D20R) and ties the expansion back
  with multiple-point constraints. Three consequences, each a plausible wrong number rather than
  an error, each now guarded. **The results file is written for the expanded model unless it is
  told otherwise** — more nodes than the mesh submitted, every number a real displacement of a
  real node, just not the node `frd.displacements(frd, mesh.node_count)` thinks; `OUTPUT=2D` on
  both output cards is the whole of the fix and there is no length mismatch that would have
  caught it. **A node of a shell or beam has six degrees of freedom**, so a `clamp` written as
  three is a *pin* — and a cantilever on a pin is a different structure, not a softer one. The
  word is now read rather than the letters (`constraints.local_dofs`), with `custom` naming
  three letters deliberately staying a pin, because `dofs` is a list of letters and letters are
  translations. **`check_restraints` needed a six-degree-of-freedom form**, and that is not
  tidiness: the three-DOF form refuses a beam clamped at one node as under-constrained *and*
  refuses a straight beam as a degenerate mesh, two refusals of correct models, which is as
  costly as accepting a wrong one. Supporting vocabulary in `app/mesh/structural.py` and
  `app/solve/sections.py`; the section properties are checked against **numerical integration
  over the real outline**, not against the formula they were written from, which is what catches
  the axis swap that makes an RHS 60x40 report a stiffness 1.94x wrong with both numbers real.
  One test found its own expectation wrong rather than the code — a warped quadrilateral's area,
  where the obvious decomposition describes a split along the *other* diagonal; rewritten with
  Heron's formula, which shares no algebra with the cross product under test.

  6,451 → 6,568 tests, `ruff` and `mypy` clean. 14 guards verified by breaking what they guard.
  **What is not claimed:** no `ccx` on this machine, so not one keyword written since 2026-09-05
  has been round-tripped through the real solver; no mesher produces a `ShellMesh` or `BeamMesh`,
  so the strategy is exercised by authored meshes; and loads on 1-D and 2-D regions have no
  tributary-area rule, so `write_frame_deck` takes a nodal force vector rather than a `LoadCase`
  and states the reason — an equal split would have run, looked right, and been mesh-dependent
  in exactly the way 6.2's rule exists to prevent.
- **2026-09-08 — gate G1 re-run: rung 3 discharged, the oracle unblocked, the gate still not
  passed.** Report: `docs/verification-2026-09-08-G1/`. Rung 3 (S1) is done with: on the open
  kernel the agent reached 2.4 kg by construction rather than by the loop, so a second turn drove
  `catia_set_parameter` directly — it refused `'Width'` *naming the real parameters*, the agent
  listed them, set width then height to 45.1 mm, and measured **3.20153 kg** against a 3.2 kg
  target, with the journal replay keeping `Pad.1` named `Pad.1`. E5 task 5 / `b9b1cb9` is verified
  end to end. E4.4's "The part so far" panel, its section cuts, and both status-chip tooltips are
  verified on the real machine (and correctly render nothing on `GEOMETRY_BACKEND=catia`).
  **The oracle check found a blocker and it is fixed here**: CalculiX reads numeric fields with a
  Fortran `f20.0` and truncates anything wider, while `deck._number` emitted `repr` — 22–23
  characters for a full-precision double — so `ccx` refused every deck built from real imported
  geometry with `*ERROR reading *NODE` and an empty card image. The offline suite could never
  catch it: every mesh in `test_solver_calculix.py` comes from `box_mesh`, whose coordinates are
  `5.0` and `200.0`; only a CATIA→STEP→OCCT transfer produces `-7.993605777301127e-15`. Narrowed
  to 13 significant digits only where `repr` does not fit, so `5.0` stays `5.0`. Four tests,
  verified by breaking them. `ccx` and `linear_static` now agree on the gate's own case —
  displacement 0.538697 vs 0.538697.
  **G1 does not pass**: the load-bearing prompt (S3) is `!`. `draft_load_case` picks faces from
  six absolute direction words (`left`→x,min …), so on a beam lying along Z it clamped and loaded
  two faces 20 mm apart on a 200 mm part and reported a factor of safety of 1303 unremarked — the
  agent caught it itself, which is the one good thing in that prompt. Open: a part-relative face
  vocabulary, a sense check on drafted load cases, and a convergence check (no stress in this run
  came from more than one 411-element tet4 mesh). Also corrected the ladder's own S3 criterion,
  which asked for δ ≈ 4 mm — that is aluminium; mild steel gives 1.56 mm.- **2026-09-08 — CI is green for the first time in the history this repo records.** Every run
  since at least 2026-09-02 had failed, and reading them turned up three failures that *only*
  happen in CI, which is why a green local suite never found them. (a) **The `database` job had
  never actually run** — 684 errors, all `ModuleNotFoundError: psycopg2`, so the half of the suite
  the whole three-job split exists to protect was erroring at fixture setup on every push. It
  passes now (9m46s). (b) **`gen_bridge_tools.py --check` reported a false "stale" on every run**:
  `_ruff()` searched only `venv/bin`, CI pip-installs into the runner's Python and has no venv, so
  the generator silently emitted *unformatted* source and compared it against the formatted
  checked-in file. It now also looks beside `sys.executable` — the same rule stated properly, "the
  ruff of the environment this generator is running in" — and `--check` **refuses** rather than
  comparing unformatted output. Verified by hiding ruff from both paths: old = exit 1 with a false
  "stale", new = exit 2 with the reason. (c) **`search_documentation` is registered only when the
  BM25 index exists**, and `data/bm25/index/` is derived and untracked, so two vocabulary tests
  passed locally and failed in CI forever — reporting a shipped capability as dead weight. They
  now read a `registrable` set (live registrations ∪ `Tool(name=…)` literals by AST) instead of
  asking a `ToolBox` built on this machine. And **mypy is fully clean** — the three `ezdxf`
  `import-not-found` lines CLAUDE.md told people to skim past were failing the mypy gate on every
  run; declared optional-and-untyped in `pyproject.toml`, so `mypy app/` now says *Success: no
  issues found in 343 source files* with no exceptions. (The `.mypy_cache` hid the fix at first,
  which is worth knowing.) DXF export still cannot run — that half is a product decision nobody
  has taken, and the override does not pretend otherwise.

- **2026-09-08 — RLS enforces in CI, and the twelve red tests on `main` are green.** The
  `postgres:17` service container makes `POSTGRES_USER` a **superuser**, which outranks both
  `ENABLE` and `FORCE ROW LEVEL SECURITY` — so for a year the eleven tenant tables had their
  policies deployed, correct, and completely inert in CI, and `test_tenancy_rls.py` passed
  vacuously against a database enforcing nothing. `ci.yml` now bootstraps as `postgres` and creates
  the application role `LOGIN … CREATEDB CREATEROLE NOBYPASSRLS`, owning both databases so `FORCE`
  does real work, plus a step that fails the job if the connecting role can ever bypass again.
  **Both configurations were run against a real `postgres:17` rather than reasoned about**: old =
  `rolsuper/rolbypassrls true true` and the RLS test xfails; new = `false false` and it XPASSes,
  with the whole database job green (`alembic upgrade head`, `alembic check`, 688 passed, 7m14s).
  The `xfail(strict=False)` marker stays — Neon's `neondb_owner` cannot drop `BYPASSRLS`, so
  production is the one place left. Guarded by three new assertions in
  `TestContinuousIntegration`, one of which was initially satisfied by the step's own `echo` line
  and had to be tightened onto the SQL — prose is not a gate.
  The twelve failures are in `4121c77`: one real defect, one tripwire firing correctly, one stale
  index, nine rotted checks. **Still open:** CI did not catch any of them for a fortnight, which
  is worth an hour with `gh run list` before this pipeline is trusted as a gate.

- **2026-09-08 — E4 closed: the open kernel's parts are finally visible in the product, and two
  defects in its existing presence there.** The CATIA half of E4.4 shipped on 2026-09-07 (it is
  in `origin/main`, five commits ahead of what this machine had checked out — worth knowing,
  because the local tree looked like the work had never happened). The OCCT half is not a copy
  of it: `GET /kernel/conversations/{id}/render` has no history, so a picture placed in the
  transcript would redraw itself into a later part on the next build. It is pinned to the live
  state above the composer instead, and what to show is decided from `GET /catia/status` — which
  the chat already polls — rather than from the render endpoint's three English 409s. An evicted
  part is said in words; a CATIA deployment and a conversation with nothing built show nothing at
  all. Found on the way: **`X-Kryova-Blank`, `X-Kryova-View` and `ETag` were set by the route and
  missing from CORS `expose_headers`**, so no browser could read them and the only way to tell an
  empty frame from a drawn part was guessing from the byte count; and **`describe()` put the
  literal string "undefined is connected, running CATIA" in the status chip** on every open-kernel
  deployment, because `CatiaStatusOnline` was the only `connected: true` shape the frontend
  modelled and the open kernel has none of the device fields. Teaching the union about the third
  shape made `tsc` refuse the same mistake in `catia-bridge-panel.tsx` too. 22 guards, all
  verified by breaking what they guard. Frontend 297 → 323 tests; E4 takes the phase-complete
  marker.

- **2026-09-08 — E3 closed: the measurement layer's *interface* pinned, and a status line that
  had been wrong twice in opposite directions.** E3.3 read "not yet wired" long after the wiring
  landed, was corrected the same morning to "covered nowhere", and that was wrong too —
  `tests/test_measurement_elements.py` had carried a `TestMeasureBetween` class since `eb4d89a`
  on 2026-09-05. Both errors came from reasoning about the code instead of opening the tests.
  The genuine gap was narrower and is closed: 11 guards over `catia_measure_between` as an
  interface rather than the geometry under it — `kind` picks the headline and does not gate the
  computation, every word the registry advertises is one the backend takes, the kind word survives
  the case and spacing a model gives it, the payload echoes what each reference resolved to,
  swapping the operands swaps the closest pair, a plane refuses an overlap volume from either side
  of the pair, a refusal names the tool that refused, and all four numbers report `MEASURED` with
  the method named. All 11 verified by breaking the guarded thing and watching the named test fail.
  It also caught a **false comment**: `_SUPPORTED_KINDS` claimed to be cross-checked against the
  registry's enum by a test that did not exist, so an analysis kind added to
  `catia_analysis_part` would have been refused at runtime as undocumented. 33 → 44 tests; E3 now
  carries the phase-complete marker, with the cross-backend agreement half of its proof left
  explicitly to a seat, the same shape as E1 task 7.

- **2026-09-08 — the plan restructured, the Linux box moved to local Postgres, and RLS shown to
  enforce for the first time.**

  **`KRYOVA_MASTER_PLAN.md` restructured** (2,076 lines, no tables at all): 29 phases as
  `##### Phase E1 #####` … `##### Phase P10 #####`, each a numbered task list where every task
  carries one of five status lines, `PARTIAL`/`DONE` naming the test file that proves it. The
  status board table is gone — its content is distributed onto the tasks it was describing, which
  is where a reader was going to look anyway. 159 task statuses: 55 done, 39 partial, 62 not
  started, 3 blocked; `E1` and `E2` carry the phase-complete marker. The file now says in its own
  header that a task may be added, split or rewritten when the work teaches something the plan did
  not know, which is what had been happening informally.

  **The Linux development machine moved to local PostgreSQL 16** (the Windows box went to 18.6 on
  2026-09-07): `kryova` and `kryova_test` databases, `.env.local` overriding `.env`, `~0.14 ms` per
  round trip against Neon's `~250 ms`. The DB suite went from ~4 minutes to under one.

  **The role was created without `SUPERUSER`, and that answered a question this project had been
  getting wrong for a year.** A superuser — and equally Neon's `neondb_owner`, which holds
  `BYPASSRLS` and cannot drop it — **outranks both `ENABLE` and `FORCE ROW LEVEL SECURITY`**, so
  the P2 policies were deployed, correct, and inert, and the first isolation run passed vacuously
  against a database enforcing nothing. With `NOBYPASSRLS`,
  `test_the_application_role_must_not_bypass_row_level_security` flips from xfail to **XPASS**: 11
  tenant tables enabled and forced, and the policies genuinely enforce. Its marker was rewritten
  rather than deleted, because RLS is **still inert in the two places that matter** — Neon, and CI,
  whose `postgres:17` container makes `POSTGRES_USER` a superuser. That CI gap is now a named open
  item under P9.

  Three defects the switch surfaced, all of which had been invisible while the suite ran on SQLite:

  1. **`TEST_DATABASE_URL` in `.env.local` looked like configuration and did nothing.**
     `app.core.config` reads `(".env", ".env.local")` and pytest does not, so the run fell back to
     in-memory SQLite and the RLS, JSONB and cascade tests skipped themselves — the exact shape of
     "one green tick standing for a Postgres suite that never touched Postgres" this repo had
     already been burned by once. `conftest.py` now loads it into `os.environ` at import, leaving
     the resolver a pure function of the environment so `monkeypatch.delenv` still means what it
     says, and a real environment variable still wins so CI is untouched.
  2. **The test engine was on the psycopg2 spelling.** A bare `postgresql://` URL routes to
     psycopg2, which is not installed, so a URL correct in every other respect died with
     `No module named 'psycopg2'` — and a URL pasted from `DATABASE_URL`, which `Settings`
     normalises, would not work here. Normalised at engine construction, not in the resolver, so
     the resolver still returns what was configured.
  3. **`test_projects.py`'s query-count assertions only held on SQLite.** They matched
     `" from users"`, and on PostgreSQL every reference is compiled schema-qualified, so the real
     statement reads `from kryova_test.users`. The captured SQL is now schema-stripped, with the
     reason written down: these tests are about how many round trips a request makes, not which
     schema it was translated into.

  **`CLAUDE.md` rewritten (937 → ~690 lines, no tables), and the audit of it was the finding.**
  **Five of its nine "known landmines" were false** — fixed weeks earlier and never removed:
  `SECRET_KEY="changeme"` boots (it is refused at startup), the rate limiter trusts
  `X-Forwarded-For` unconditionally (it honours `trust_proxy_headers` and counts from the right),
  no list endpoint paginates (all four do, capped at 100), SQLite is not really refused
  (`_require_postgres` raises), and `/health` is not a readiness probe (it probes the database and
  the media store and returns 503). A stale landmine is worse than an empty section: it sends the
  next session to re-fix something that works, and teaches it to skim the real ones. They are
  recorded as removed so nobody reinstates them from memory. Added: the layout of **both** repos
  with what each folder is for, the folder/subfolder rule, the naming conventions on both sides,
  `rg` over `grep`, the Bash-cwd trap between the two checkouts, the master-plan upkeep rules, the
  Linux-writes/Windows-proves split stated as the process rule it is, and pointers to this file
  and the prompt ladder. One new real landmine: **`ezdxf` is imported by two modules and declared
  in no requirements file**, which is why `mypy app/` prints three errors and why DXF export and
  DXF attachment reading silently never work.

  **`docs/GUI_PROMPT_LADDER.md` grown from 24 prompts to 50**, and two levels added above the
  existing four. Level 1–4 gained E7–E10, H7–H10, S7–S10 and PRO7–PRO9, each aimed at something
  the codebase has already been burned by — the scoped-fillet trap that removes 386 mm³ where the
  scoped version removes 172 and reports success, the pattern count that must come from
  arithmetic, resume-after-restart reading the operation log rather than the transcript. **Level 5
  (Programme, PG1–PG6)** asks for a machine *and its evidence*, and is scored on the evidence: a
  correct assembly whose summary holds one unverified number fails there and passes at Level 4.
  **Level 6 (Frontier, FR1–FR5)** is deliberately past the ceiling — the deliverable is which wall
  was hit, and only "a defect in Kryova" is a `!`. Every Level 5 and 6 prompt names the phase it is
  blocked on, so a bad afternoon converts into a task in the master plan.

- **2026-09-08 — Level 4 driven on the seat; five defects found and fixed; the local Postgres
  switch.** The database moved from Neon's pooled eu-west-2 endpoint to a local PostgreSQL
  **18.6** (the same major.minor Neon runs, so dev/prod parity holds and the never-SQLite rule
  is untouched): **0.135 ms per round trip against ~250 ms**. `docs/LOCAL_POSTGRES.md`, and
  `scripts/create_admin.py` for the account the API cannot create — `UserCreate` demands eight
  characters and the login form imposes none, so a short development password can be *used* but
  not registered.

  PRO4 and PRO1 were then driven through the real GUI against a real V5-R33. **PRO4 is `~`
  partial and the half that never worked now works** — the shear calculation, a lever ratio
  from it, and an explicit "I have not checked it" on stiffness. **PRO1 failed four times and
  each failure moved somewhere new**, which is what earned five fixes:

  1. `catia_pad`'s "no such sketch" refusal now names the sketches that exist. Saying only
     "use the name a sketch tool returned" is unrecoverable exactly when it fires, because an
     agent that invented a name has lost the real one.
  2. A part/product name collision now leads with "use a different name" when the *kinds*
     differ. Continuing a part cannot produce the assembly that was asked for, and the agent
     spent rounds discovering that.
  3. A turn ended for repeating itself is no longer reported as out of budget. Two exits
     reached identical closing code; the `done` event now carries `stop_reason` and the banner
     says the matching sentence. Confirmed live on the next run.
  4. **`MAX_EMPTY_DOCUMENTS`** — five empty parts in one turn tripped neither existing guard,
     because `catia_new_part` *is* a successful mutation and reset the barren counter every
     time. Opening a document is the one mutation that changes nothing about the part.
     Measured effect: one document instead of five.
  5. `catia_sketch_dimension` failed on **every** attempt on this seat — `.Dimension` raises
     `E_INVALIDARG` — and returned the raw French COM error while leaving the half-made
     constraint in the sketch, which then failed the pad three times. Now refused in words and
     cleaned up.

  Each verified by breaking what it guards. Backend `ruff`/`mypy` clean, `test_agent.py` 79,
  `test_multi_document.py` 40; frontend `tsc`/`eslint` clean, 296 tests.
  **The ceiling is now the context window, not the geometry**: PRO1's fourth run built its
  first solid and exhausted the model's 32k window with four parts still to make.
  `docs/verification-2026-09-08/REPORT.md`.
- **2026-09-07 — E4.4, the CATIA half; the integration gap's step 3 is half closed.** The
  transcript draws the picture a tool result points at instead of `JSON.stringify`-ing a media
  id and a byte count, so the standing rule that every CATIA result is *looked at* is finally
  something the product supports rather than something done beside it. In `Kryova-frontend`
  (`src/lib/tool-media.ts`, `src/components/tool-image.tsx`, `api.mediaBlob`); 293 tests,
  `tsc --noEmit` and `eslint` green on the Windows machine. **Deliberately not marked DONE:**
  `GET /kernel/conversations/{id}/render` still has no frontend caller, so a part built on the
  OCCT backend is still invisible in the product.

- **2026-09-07 — the tool-schema budget went red and was paid down rather than raised.**
  `test_naming_rule.py`'s 215,000-character ceiling, set on 2026-09-06, caught the registry
  back at 215,365 after E14 and E16 added assembly and knowledge vocabulary. Four more
  conventions moved to the frozen system prefix — the sketch 2D frame (x35), what a
  construction element is (x22), how a direction vector is read (x23), and the sketch default
  (x26) — for 215,365 → 206,337, about 2,250 tokens off every model call. It compounds with
  `DEFAULT_MAX_STEPS` 20 → 60 from the same stretch, which makes every repeated sentence three
  times as expensive as when the first measurement was taken. New ceiling 208,000.

- **2026-09-06 — E14, the seat half.** A conversation owns several CATIA documents, one active; a second `catia_new_part` on a seat adds rather than replaces, products are bound, `catia_open_document name=` switches, and an owned part's name resolves to its saved path when added to an assembly. Migration `c7e2a9d4f1b3`. Driven by ladder prompt S2 on the real seat. Tested with pytest (24 new, 413 green across the binding suites); end to end on the seat next.

- **Ladder prompt H3 passed, and the five defects that were in its way (2026-09-06).** The
  level-2 prompt "fillet all the vertical edges at R8, then cut a 40 x 20 pocket 10 deep in the
  middle of the top face, on a 100 x 60 x 30 block" failed four times on the seat, and every
  failure was ours. `catia_list_edges` could not classify a single edge, because `Measurable`
  refuses `GetCOG` on an edge and silently writes nothing for `GetDirection` -- so `kind` was
  `"unknown"` for every edge of every part and the tool's own filter matched nothing; it now
  measures through `vba.edge_map` and classifies through `scripts/catia_bridge/edges.py`, shared
  with `catia_fillet` so the two cannot disagree. `catia_select` refused the `Edge.N` ids
  `catia_list_edges` had just printed and pointed at the one tool that could not resolve them.
  A sketch left open refused the *next* `catia_sketch_create`, a refusal a person never sees,
  because leaving the Sketcher is what starting the next thing means. **`HybridBodies.Add()`
  makes the new geometrical set CATIA's in-work object and `ShapeFactory` inserts after it**, so
  one `catia_sketch_create(support="top")` left the part unable to take any solid feature at all
  and the refusal blamed the profile -- measured from bare COM, `part.InWorkObject = body` turned
  a refused pocket into 172,000 mm3. And a turn out of tool rounds ended by announcing work it
  could not do instead of reporting what it had built. Run 5: 170,352 mm3 measured against
  170,352 arithmetic, in 12 of 20 rounds, with both pictures in
  `docs/verification-2026-09-06/`. Fifteen guards, each verified by breaking what it guards.

- **Seat verification (2026-09-06).** One prompt at a time through the real chat endpoint
  against the real CATIA V5-6R2023 seat, not the dispatcher — per the standing rule that a
  test starting at `dispatch.call_catia` is testing a middle. `qwen3-coder:30b` needed two
  turns to get there: turn one called `catia_new_part` before anything was open and got a
  clean named refusal (`open_in_catia` had not run yet — a real instance of "calls tools
  before their prerequisites," caught by our own validation rather than becoming a wrongly
  built part); turn two, told explicitly to open CATIA first, built a 100×100×12 mm steel
  plate with a 40 mm bore end to end (`open_in_catia` → `catia_new_part` → sketch → rectangle
  → circle → pad → `catia_set_material`) and reported 0.824674 kg against the closed-form
  104,920.3553 mm³ × 7860 kg/m³ exactly. Both required pictures taken and kept in
  `docs/verification-2026-09-06/`: the viewport through `catia_capture_view` itself (the
  product's own tool, exercised rather than bypassed) and the whole application window via
  `scripts/shot.ps1`, confirming the French seat (`Plan xy`, `Corps principal`) and a
  right-side-up, correctly-bored part. **Minor finding, not fixed**: `open_in_catia(new_part:
  true)` followed by the model's own `catia_new_part` leaves an orphaned empty first document
  open (`Part1`) beside the one actually built into (`Steel-Plate-2.CATPart` / `Part2`) —
  worth a dedup or a clearer tool description, not a correctness bug.
- **Seat verification, continued (2026-09-06) — two real, unfixed bridge defects, found by
  continuing the same conversation rather than starting fresh each time.** Asked to change a
  built part's bore diameter, the model reached for `catia_run_command("Edit Sketch")`
  instead of `catia_set_parameter`; that call wedged the bridge daemon's COM-calling thread
  indefinitely (clean logging up to the checkpoint before it, then nothing at all, while the
  process stayed alive) and the server's auto-spawn-on-demand logic then collided with the
  wedged daemon's still-held lock, surfacing to the model as "bridge not connected." A
  restarted daemon fixed the daemon; it did nothing for CATIA itself, which is a separate
  process and had been carrying whatever the command opened the whole time. The next turn
  proved it: `catia_run_command` kept refusing with *"a dialog is already open"* even after
  `catia_describe_dialog` correctly read it (`"Entrée clavier"`, a keyboard-input prompt) and
  `catia_dialog_action("ok")` reported pressing OK successfully — **the documented recovery
  path reported success and did not actually recover the seat.** Unable to proceed, the model
  then reopened the already-consumed sketch backing the existing pad (`catia_sketch_create`
  raised no complaint about the name collision), drew a redundant second rectangle and a new
  circle into it, and padded it as a "new" feature — which reported `ok` with a fabricated-
  looking plausible payload while `catia_measure` and the window screenshot both confirmed the
  document had not changed at all (the new circle sat entirely inside the old, larger hole it
  was padded from, so the new material was swallowed by the hole already there). Two defects
  named for whoever picks this up: (1) `catia_dialog_action` needs to verify the dialog is
  actually gone before reporting success, not just that the click was sent; (2)
  `catia_sketch_create` should refuse a name already backing a built feature the way
  `catia_new_part` already refuses to abandon an owned document, and padding a sketch already
  consumed by an existing feature should be refused for the same reason a boolean cut that
  changes nothing already is. Also recorded: `scripts/shot.ps1`'s window capture cannot see a
  modal dialog that is a separate top-level window rather than a child of CNEXT's main frame —
  a "the window looks fine" screenshot proved the viewport was fine, not that nothing was
  stuck, and both need checking after any interactive-command failure. Full write-up, with the
  exact tool-call sequence, in `docs/verification-2026-09-06/REPORT.md`.
- **Seat verification, continued again (2026-09-06) — a precise root cause, and a correction
  to the entry above.** A third fresh attempt at the same parametric-change test crashed
  nothing: `catia_sketch_create` raised `pywintypes.com_error: 'Le serveur RPC n'est pas
  disponible.'` from `scripts/catia_bridge/com/sketcher.py:98`, inside `_require_closed()`'s
  own error-message construction — `ComContext._sketch_edition` (the sketch a previous
  `catia_sketch_create` left "open") is never cleared when the model abandons that document
  for a new one (`open_in_catia(new_part: true)` followed by its own separate `catia_new_part`,
  a habit seen in four of seven runs), so reading `.Name` off the orphaned sketch's now-invalid
  COM reference throws instead of naming it cleanly. **Checked, not assumed: the daemon caught
  this, logged it, and kept serving calls normally afterward** — confirmed by the process list
  and by five further successful calls in the same run. The entry above's "wedged the daemon"
  framing for the *first* incident was wrong to call a crash and has been corrected in the
  report; what actually disrupts the connection is `catia_run_command` blocking its full 30 s
  against an interactive command (three occurrences now: "Edit Sketch", "Hole", "Rectangle"),
  and the daemon's own reconnect loop appears to recover from that on its own, given time —
  this session never actually tested waiting instead of restarting. Two precise, unfixed
  defects for whoever takes this on: clear `_sketch_edition` on a document switch and guard the
  `.Name` read against `com_error`; and give `catia_run_command` its own bounded timeout on the
  COM call itself rather than only on the daemon's reply to the caller.
- **Seat verification, continued a third time (2026-09-06) — a third distinct
  bridge defect, this time architectural.** Testing moved to STEP export
  (rung 2, untested until now): the model skipped `open_in_catia` for a third
  time (Runs 1, 6, 8 — a confirmed weakness at this tool count, not a fluke),
  then, told explicitly to call it, did — and the very next `catia_sketch_create`
  on a **brand-new** document (zero prior features, so not Run 7's stale
  `_sketch_edition`) raised the same `com_error: 'Le serveur RPC n'est pas
  disponible.'` Root cause, code-grounded: `open_in_catia`'s `new_part: true`
  creates its document through the **server's own** direct-COM client
  (`app/catia/bridge.py::new_part`, a fresh `CoInitialize`/`CoUninitialize`
  apartment per call, serialised only by an in-process `threading.Lock` that
  cannot and does not reach across a process boundary), while every
  `catia_*` modelling tool drives CATIA through the **paired daemon**'s own
  separate, persistent COM connection (`app/catia/dispatch.py::call_catia`).
  Two independent, unsynchronised COM clients legally touch one single-
  instance, single-apartment CATIA process, and in this run the daemon's
  first real call landed moments after the server's client tore its apartment
  down mid-document-creation. Not yet reproduced under controlled timing —
  offered as the best-evidenced explanation, confirmable by retrying with
  `open_in_catia(new_part: false)`, which never invokes the contending
  client. Third defect named for whoever picks up the bridge work: give
  `open_in_catia` and the daemon a single cross-process lock (or route
  `open_in_catia`'s document creation through the daemon when one is already
  paired) instead of the process-local lock that exists today. STEP export
  itself remains untested. Full sequence in
  `docs/verification-2026-09-06/REPORT.md`, Runs 8–9.
- **P8 — usage metering wired to its first real consumer (2026-09-06).** Billing has existed
  as a schema and an API with nothing filling it; `app.simulation.runner` now posts every
  job's meshing and solve time to the ledger through `usage_scope`, `finally`-scoped so a
  solve that fails after nine minutes still bills the nine minutes, and through its own
  session (`LedgerSink`) so a metering fault can never roll back the result it measured.
  `app.observe.collect` gained a listener seam (`add_listener`/`remove_listener`) so
  metering is *always on* independent of tracing — a bill gated on an operator having
  enabled tracing for the request that happened to be billable would be a bill with holes in
  it — while the disabled path stays exactly what it was (`span()` returns `INERT` with no
  listener registered, unchanged allocation cost). AI tokens, CATIA seat time and kernel
  operations are declared in the schema and report a gap rather than a number until
  something calls them; no Stripe integration.
- **E17 — a test-design bug in the mirror-orientation check, found running the drawing
  suite for the first time since P8 (2026-09-06).** `TestWhereAKnownFeatureLands` checks
  that a bore's line work is round at its true centre and not at three mirrored ones; the
  "not round" checks used the single nearest line-work point to a candidate radius, and the
  bore (r=15 at (20,10)) and the deliberately-wrong probe at (20,-10) are only 20 mm apart —
  close enough that two circles of the same radius **intersect**, so a genuine vertex of the
  real bore near that intersection satisfied the wrong probe's "round" check too, with
  nothing actually circular at (20,-10). Fixed by sampling twelve angles around the
  candidate circle and taking the worst match rather than the single best one: a real
  circle satisfies all twelve, and one or two coincidental intersection points cannot.
- **P3 — the admin console and an audit log that cannot be edited (2026-09-06).** The append-only
  property is **structural in four layers, and the report says which one is worth what**: a
  `BEFORE UPDATE OR DELETE` row trigger (the one that counts — it applies to the ORM, to raw SQL,
  to psql and to the next migration), a statement-level `TRUNCATE` trigger (because `TRUNCATE`
  fires no row triggers and would otherwise empty the table past the first rule),
  `REVOKE UPDATE, DELETE, TRUNCATE ... FROM PUBLIC`, and a `before_flush` ORM guard that turns the
  mistake into a Python error at the line that caused it. Written for **both** dialects and
  installed by `create_all` as well as by migration `2f3f8aadb319`, because a guarantee that is
  absent on the dialect the suite runs on is a guarantee nobody has seen work. Verified on the
  live Neon database: UPDATE, DELETE and TRUNCATE each return
  `RestrictViolation: audit_events is append-only`. What is **not** claimed: the table's owner can
  `ALTER TABLE ... DISABLE TRIGGER`, and the application connects as that owner today — which is
  why every entry also carries the previous entry's SHA-256. The tests drop the trigger, prove the
  same UPDATE then succeeds (so the refusal is attributable to the trigger and not to a typo), and
  prove the chain reports it: an edited row breaks at its own sequence with `entry_hash` mismatch,
  a deleted row breaks at the *next* sequence with a gap — different accusations, kept apart.
  `audit_events` carries **no foreign keys**, deliberately: `ON DELETE CASCADE` would let deleting
  an account erase what was done to it, and `ON DELETE SET NULL` is an UPDATE the trigger refuses.
  Impersonation is a **row, not a token claim**, which is what makes "stop impersonating" a
  revocation rather than a note: mode is read from `impersonation_sessions` on every request, so
  escalation takes effect without a new token and ending takes effect at once. Read-only is
  refused in `get_current_user`, *before* the route function is entered, so a route that never
  heard of impersonation cannot be the one that lets a write through — and the refusal is written
  to the log on its own transaction before the 403 is raised. Both identities on every row, no
  `user_id` column to collapse them into, and `impersonation.write_refused` is its own action
  rather than an outcome. Staff standing is a separate table and grants **nothing** inside a
  tenant: `/organisations/{id}/audit` never reads a staff grant, so a platform administrator who
  is not a member gets the same 404 a stranger does. Guards verified by breaking them: the grant
  revoked and the same request stops working; the session escalated and the same POST starts
  working; the trigger dropped and the same UPDATE goes through. 65 tests.

- **E18 / M2 — the welded frame, the ladder's first product rung (2026-09-06).** `app/design/missions.py` grew an `AssemblyDesign`: a `ProductStructure`, a `DesignSpec` per leaf component, the interface contracts at the boundaries, and frame-level parameters an assertion can read. M2 is a welded portal in RHS 60×40×4 — **two components, three occurrences**, the post designed once, built once, weighed twice. The section is declared **on the interface** and `bind_into` merges it into both members (14.3), so a member on a different section is a compile error at the joint naming both parties, not a clash three weeks later. Measured, all of it: mass 12.743104 kg against the closed form, centre of mass at (400, 0, 488.18), envelope 800×40×760, both joints measured (0 mm apart, 0 mm³ interference), the two posts rejected by their bounding boxes — 3 pairs, 0 unchecked. 18 mission assertions and 6 interface claims, **none `UNMEASURED`**. Six wrong frames built in the tests, each watched to fail the guard it should: a post 10 mm short opens the joint, a header 10 mm low interpenetrates by 5,824 mm³, a solid bar weighs 41.6 kg, a section rolled a quarter turn puts 60 mm out of plane. Two findings worth the session: **`clearance_mm=0` makes the fit-up claim `UNMEASURED` rather than false** — the broad phase soundly throws away the 10 mm pair — so M2 inspects to 25 mm to get the diagnosis, not the verdict; and a **`widest_gap_mm`** is needed beside `minimum_clearance_mm`, because a header bearing on one post and floating above the other has a minimum clearance of zero. **M2 passes carrying `unproven`**: no load case (E6), no weld classification (E8.3), no weldment or cut list (E17.4), so the ladder is not `complete` and “M2 passes” cannot be read as “the welds are sized”. Four gaps in `app/assembly/` recorded rather than worked around: no combined payload (mass and clash `to_payload()` collide on `occurrence_count`/`complete`), no per-pair lookup or `ClashFinding.to_payload()`, no product envelope (`_world_boxes` is private), and no boundary payload for a contract. `tests/test_mission_m2.py` (48), `tests/test_design_missions.py` (32, coverage figure moved 1/9 → 2/9). **Unproven until a gate:** no chat run has been asked to build this frame — M2 through the harness is not M2 through the product.

- **P2 — organisations, roles and RLS tenancy (2026-09-06).** Projects are owned by an organisation, not a person; `owner_id` stays as provenance and stops being a permission. Migration `1b07f4f27e89` **backfills**: a personal organisation per user, its creator as `owner`, `projects.organisation_id` added nullable, filled, and only then made NOT NULL — 24 users, 24 organisations, 82 projects placed, 0 orphans on the live database, with the migration raising rather than continuing if any row were left behind. A `before_flush` hook extends the guarantee forward, so `app/ai/tools.py` building a `Project` directly gets a tenant without knowing organisations exist. Roles are one ordered ladder and every check is `at_least`, in `api/deps.py` alone. Invitations hash their token like the password reset does, are single-use, and are bound to the address they were sent to. Every cross-tenant miss is a 404 byte-identical to a miss. RLS is enabled **and FORCEd** on six tables, fed only by `set_config(..., is_local => true)` republished on each transaction the request opens — proved by breaking it to `false`, which leaves the tenant on the connection after COMMIT and fails the test. **The one thing that is not true yet:** Neon's `neondb_owner` holds `BYPASSRLS`, which outranks FORCE and made the first run of the isolation suite pass vacuously; it cannot alter itself to drop it. The suite now runs as a `NOBYPASSRLS` probe role so the policies are genuinely tested, and `test_the_application_role_must_not_bypass_row_level_security` is `xfail` with the fix in its reason. Thirteen guards verified by breaking what they guard.

- **E16.1 — the tool the prompt promised and the offer withheld (2026-09-06).** Tool *selection* rebuilt in `app/ai/tool_retrieval.py` and measured against the real 110-tool OCCT registry rather than a synthetic one: the shipped selector was withholding five to nine tools the frozen system prompts *name* on every realistic message, `catia_set_parameter` on all five measured — the tool rung 3 is entirely about and the one the agent failed to find for three sessions running. Prompt-named tools are now a floor, scanned out of the prompt text so it cannot fall behind an edit. Four more rules, each explaining itself: `catia_kb.recognise` supplies the domain layer (*bore* → Hole, in French too), intent families cover tasks whose words are disjoint from the tool that serves them, a registry-derived common-word filter and a name-beats-prose rule kill the noise those widenings would otherwise let in. Offers 17–39 of 110 per turn; every inclusion carries the rule and evidence that produced it, logged per turn. **Unproven until a gate: no chat run has been driven against the narrowed offer.** `app/ai/planning.py` lands 16.2's seam and one first step — the requirements of a request, held where the context window cannot trim them — tested and deliberately unwired.

- **P9.1 — CI that is honest about the database (2026-09-06).** Backend CI split into `lint` / `offline` / `database`: the offline half (3,327 tests) gates every push and reports the 430 it deliberately skipped, the database half runs against a PostgreSQL 17 service container with `alembic upgrade head` + `alembic check`, and `scripts/pytest_split.py --database-only` refuses to start without a real PostgreSQL so conftest's SQLite fallback can never again be reported as the database suite. Fixed two things that were red on main: `mypy app tests` aborted on a duplicate module name and had been checking nothing, and `.env.example` was missing `AI_TOOL_LIMIT` / `AI_DAILY_TOKEN_BUDGET`. Frontend CI already existed (the plan was wrong about that) and was upgraded: SHA-pinned actions, `.nvmrc`, and a three-dependency guard. Decided against a tagged MSI release — the installer bakes build-machine paths, so it would ship broken.

Newest first. Each line names the board row it moved and the commit that moved it.

- **2026-09-06** — E18 → **M3 green, ladder 3/9, and the gap that made it hard.**
  A 1.5 mm cold-rolled cover folded four times, 0.6166 kg, 28 closed-form claims.
  **There is no sheet-metal operation anywhere in the OCCT backend** — no wall,
  flange, bend or unfold among 116 handlers — and `SheetMetalPart` cannot compile
  to a `DesignSpec`, so M3 has to declare the same cover twice: once as a fold
  tree and once as a hand-drawn 20-segment section. The only thing holding the
  two descriptions together is `flat.volume_mismatch_mm3`, and that assertion is
  the best thing in the mission: a flat pattern conserves *neutral-axis length*
  while a constant-thickness solid conserves *thickness*, so the residual is
  **not** zero — it is `θ·t²·(0.5−K)` per mm of bend, 304.9 mm³ here — and a
  mission expecting zero would fail on a correct part. Measured mismatch 3.2e-10.
  It is also algebraically **K-invariant**, the K in the allowance cancelling the
  K in the gain, so it tests the geometry and never the judgement. Six more gaps
  reported rather than worked around, the sharpest being that
  `steel_mild_cr` (sheetmetal) and `steel-1018` (`solve.materials`, where density
  and therefore mass comes from) are **disjoint vocabularies with nothing
  relating them** — demonstrated by swapping the sheet to aluminium and watching
  the mass stay at steel density with nothing complaining.

- **2026-09-06** — E10 → **`design/sensitivity.py` has a caller at last.** It has
  had none outside a test since it was written, listed in this file beside
  `app/render/` and `app/ai/vision.py` as capability wired to nothing;
  `objective_gradient` is the caller, and a seam nobody uses is a seam nobody has
  shown to work. The gradients are checked against functions differentiated by
  hand, and the test design is the interesting part: every case uses partials
  that **differ from each other**, because a gradient that swaps two variables is
  numerically plausible — right magnitudes, right smoothness, wrong answer — and
  one case uses variables scaled a thousand apart, because a step that is really
  absolute passes the first kind of test and fails that one. An ungradable point
  is `available=False` with a reason and **no numbers at all**: a zero gradient
  tells an optimiser it has arrived, and half a gradient is worse than none,
  since a driver would use the partials that succeeded and treat the rest as
  zero. Flipping the sign fails all eight.
  Also corrected: P4's board row still said the readers were untested, which
  stopped being true two commits ago.

- **2026-09-06** — E9/P4 → **two findings that are worth more than the 163 tests
  around them.** First: **`pip install pychrono` succeeds and installs the wrong
  package.** The PyPI name is not Project Chrono — it is a 10 kB pure-Python
  wheel for "managing delays, scheduling tasks, timing functions" by an unrelated
  author, confirmed here against PyPI's own metadata. Chrono ships compiled SWIG
  bindings through conda and can never be a `py3-none-any` wheel, so anything
  installing cleanly under that name is something else, and adding it to
  `requirements.txt` would have shipped a stranger's package into every
  deployment. Now a named landmine in CLAUDE.md with the general rule: for a
  dependency chosen by name in the technology register, **a successful import is
  not evidence you got the thing you meant.**
  Second: `closures.grashof` compared `s+l == p+q` in floating point, so a
  change-point linkage of 0.1/0.3/0.5/0.7 was classified *"double-crank: both
  input and output fully rotate"* in metres and correctly in millimetres — **the
  answer depended on the unit typed** — and the confident half was the wrong
  half, since `four_bar` refuses that very linkage at 0°. Fixed with `isclose`; a
  link 1 mm off 700 mm is still told apart. Three reader defects out too, the
  worst being that text-bearing DXF entities were silently dropped: MULTILEADER
  is where "DEBURR ALL EDGES" lives and TOLERANCE *is* a feature control frame,
  so a drawing's dimensional requirements were being read as an empty document.

- **2026-09-06** — E13 → **the rule engine and GD&T tested, three real bugs
  out.** The one worth the session: `RuleResult.sampled` came only from
  `AssertionResult.approximate`, which resolves *silence* to "not approximate" —
  correct for an assertion, wrong for a rule. It is reachable with the real
  kernel, because `ThicknessReport.to_payload` writes `thinnest_point_mm` with no
  sidecar entry of its own, so **a rule on the thin spot was reading a sampled
  number as a proof**. The fallback now consults the contract's `typical_basis`
  when the payload says nothing, and can only make a verdict *more* provisional,
  never less. Also out: a refusal message that printed a literal `{listed}`
  because one of three concatenated strings had lost its `f` prefix, so the datum
  letter came out as the placeholder; and delegated messages printing the rule
  name twice. 26 mutations run, 25 caught, one a deliberate control. Recorded and
  not fixed: `FeatureControlFrame(tolerance_mm=True)` is accepted as a 1 mm zone,
  because `True` is an `int` — `engine.Rule` refuses a boolean limit for exactly
  this reason and `gdt.py` does not, which is an inconsistency rather than a test
  to write around. And **neither module has a consumer anywhere in `app/`**.

- **2026-09-06** — **gate G1, attempt 6: the tool-selection fix works, and a
  through bore silently became a blind one.** Re-running rung 3 after E16.1
  landed, the agent called `catia_list_parameters` **unprompted** as its fourth
  call — it has never done that — then used `catia_set_parameter` correctly, and
  reached for `catia_pattern_rectangular` rather than drawing four circles. Final
  mass **2.400500 kg** against 2.4 kg: **0.5 grams**, the closest any attempt has
  come. The part is still wrong, and the new reason is ours. The bore was
  pocketed `depth_mm: 10` into a 10 mm plate — correct — and then
  `Pad.1\length_mm` was set to 11.22, which replays the part; the pocket's depth
  is a **literal** in its recorded call, so it stayed at 10 in an 11.22 mm plate
  and **a through bore became blind with 1.22 mm of floor**. The render shows it
  dashed. `catia_set_parameter` promises that "every feature that depends on it
  moves with it", and a literal does not move. Face count is the exact signal — a
  through hole is one cylindrical face, a blind one a cylinder *and a floor*, so
  seven became eight — and the result now says so, names the cause and points at
  `through_all`, which is immune and tested to be. Making depths follow the
  material is a design change and is not pretended.

- **2026-09-06** — two snapshot tests corrected rather than re-pinned, after
  adding the assembly tools broke both. `test_only_two_queue_implementations_exist`
  asserted over `JobQueue.__subclasses__()`, which is every subclass alive in the
  process — so a stub defined in another test file joined it and the assertion
  **passed alone and failed in a full run**, which is the worst way for a test to
  be wrong. It now restricts to subclasses defined in `app.jobs`: what the seam
  promises is that *this package* ships two, and a test's own stub is evidence
  the seam works rather than a violation of it. And the daemon-vocabulary test
  listed its exception (`catia_status`) inline; it derives the set from the
  generated `SERVER_ONLY` now, so adding a server-only tool cannot silently widen
  what the workstation is expected to carry — plus the converse assertion, that
  no server-only tool has *reached* the daemon table, which is the direction that
  would offer the workstation something no COM method implements.

- **2026-09-06** — E15 → **the four catalogued holes are wired, and Decision 1
  finally has a number.** `app/observe/` shipped with `solve.calculix.run`,
  `solve.linear_static`, `kernel.rebuild` and `kernel.measure` declared
  `wired=False` with the exact change each needed, because they were outside
  that lane. All four are installed now, and the first measurement out of them
  is the one the whole OCCT decision rests on: **a parametric rebuild through
  `catia_set_parameter` costs 0.49 ms**, so a 200-value sweep is about a tenth
  of a second of rebuilding. The plan has argued since it was written that OCCT
  is the internal engine because a design loop needs tens of rebuilds a minute
  and a CATIA seat gives one every few seconds. That was a reasonable belief and
  is now a measurement.
  One mistake worth recording: the CalculiX span was first written to be
  *entered after* `subprocess.run` returned, which timed an empty block — the
  span read 0.00 ms while the field beside it said 37 ms, so a roll-up would
  have reported CalculiX as taking no time at all. It brackets the call now, and
  reads 28.58 ms on the bar-in-tension case. Assembly and factorisation are
  separate stages within the in-house span, because a slow assembly is an
  element-count problem and a slow factorisation is a fill-in problem and one
  span over both cannot tell them apart.

- **2026-09-06** — P2 → **orgs and RLS, and a security test that was passing
  against a database enforcing nothing.** The migration backfills in the right
  order — tables, then a nullable `organisation_id`, then a PL/pgSQL loop giving
  every user a personal org and every project a home, then `SET NOT NULL`, then
  the policies — and raises rather than continuing if a project is left
  unplaced. Verified on the live database: **24 users → 24 organisations → 24
  memberships, 82 projects, 0 orphans.** A `before_flush` hook carries the
  guarantee forward, because `app/ai/tools.py` constructs a `Project` directly
  and requiring every call site to remember the tenant is how one of them
  forgets.
  **The finding that matters: Neon's `neondb_owner` holds `BYPASSRLS`, which
  outranks both ENABLE and FORCE — so the first run of the isolation suite
  passed vacuously, every assertion green against a database enforcing nothing.**
  Confirmed here independently (`rolbypassrls = True` for `current_user`). The
  policies are correct and deployed and do nothing until `DATABASE_URL` points at
  a `NOBYPASSRLS` role, which the owner cannot grant itself. Tracked as a
  non-strict `xfail` so it flips to XPASS the day it is fixed rather than being
  quietly absent.
  `tenant_scope()` uses `set_config(..., true)` — `SET LOCAL` in function form,
  because `SET` takes no bind parameters and the value is user-derived — and it
  is **republished on `after_begin`** rather than set once, since `db.commit()`
  discards it, which is exactly the property that makes it safe on the pooled
  endpoint. Thirteen guards broken to verify; the thirteenth did **not** fail and
  says so: deleting `WITH CHECK` changes nothing because PostgreSQL defaults it
  to the `USING` expression on a `FOR ALL` policy. The clause is kept anyway,
  since that default disappears the moment anyone splits it per-command.

- **2026-09-06** — E17.3 → **sheet metal, with no default K-factor anywhere.**
  `BA=(π/180)·θ·(r+K·t)` gives 6.094689747964199 mm for the 90°/2 mm/r3/K0.44
  case, `SB=tan(θ/2)(r+t)`=5.0, `BD=2SB−BA`=3.9053102520357994, and a 40+30
  outside-mould-line part flattens to 66.0946897479642 — each recomputed
  independently here before the commit rather than taken from the report. The
  test worth keeping above all the others: **the three length conventions
  describe one part** — outside 40/30, inside 38/28 and tangent 35/25 all
  flatten to the same blank *and* place every face identically. The K-factor is
  the judgement and is treated like one: a `Bend` requires a `KFactor`, a
  `KFactor` requires a `Source` (reused from `solve.materials`, not a third
  provenance record), and the only way past that is `assumed()`, which demands a
  written reason and marks the pattern provisional. K outside `(0, 0.5]` is
  refused because the neutral axis cannot move outward past the mid-plane, and
  the message offers the Y-factor conversion since that is what a >0.5 number
  usually is. **DIN 6935 is verified by its own continuity**: `0.65+0.5·log₁₀5 =
  0.99948` against the plateau's 1.0, meeting to 2.6e-4 — a property of the
  *published constants*, so a mistyped 0.65 or 0.5 breaks it, which is a far
  better check on a transcribed formula than re-reading it. ANSI and DIN differ
  by 1.211× at r/t=1.5, moving that bend allowance 0.2447 mm. The one guard no
  input can break — the flat-length cross-check, `Σlength−ΣBD` against
  `Σleg+ΣBA`, algebraically identical — is pinned by monkeypatching the
  deduction to be wrong by 0.5 mm, so it is verified rather than unpinned.

- **2026-09-06** — E14 → **the product graph, and the rule that a partial answer
  never gets the headline name.** 14.1–14.4: components and occurrences rather
  than a tree of copies; interface contracts reusing
  `assertions.PASSED/FAILED/UNMEASURED` verbatim and adding only *who* — a
  violation names the counterparty; a conservative broad-phase clash check whose
  four skip categories are counted separately; and a mass roll-up. Three
  decisions carry it. **Occurrence numbers are declared, not positional**, so
  inserting a leg at the head of a list renumbers nothing — the same answer
  `app/design/spec.py` gives for features, one level up. **Party resolution goes
  through the graph, never string matching** — and the mutation for that was
  *initially still green*, because the first test case (`swingarm_pin` vs
  `swingarm`) was not the realistic bug; replaced with an instance *tagged*
  `rear` holding component `rear_suspension` beside a different component
  genuinely named `rear`, and both text- and tag-matching now go red. And **a
  partial result never gets the headline name**: an incomplete clash publishes
  `checked_minimum_clearance_mm` with `minimum_clearance_mm` UNAVAILABLE,
  because a partial clearance and a partial mass both err towards making the
  machine look safer and lighter. 20 mutations, all red. Geometry verified by
  hand first: two 20 mm cubes 5 mm apart measure 5.0, at 15 mm pitch overlap
  2000 mm³ exactly, four steel cubes weigh 0.25184 kg at centre (60, 0, 10).
  Seam reported and not taken: `dynamics.pose.Frame` has no `compose`/`invert`
  and is mutable, which is why the frame arithmetic lives in
  `assembly/placement.py`; making it frozen would help both packages.

- **2026-09-06** — E15 → **the observability half, and a metering bug that was
  losing every job.** `QueueMeter._finished` existed as *both* a counter
  attribute and a method, so `self._meter._finished(...)` called an integer; the
  `TypeError` died unread inside a `Future`, and **no job was ever counted as
  finished**. That is also why the ticket is deliberately not wrapped in a
  blanket `except`: a swallowed metering bug is a metering bug that ships.
  The design decisions worth keeping: the disabled path costs **~0.09 µs** per
  span, so instrumentation can be left in rather than compiled out; collection is
  a process-global rather than a `ContextVar`, because `ThreadPoolExecutor` does
  not carry a context across `submit` and a context-scoped recorder would miss
  every job-thread span — which is most of the interesting ones; the queue
  *counters* are always live even when span recording is off, because "is the
  queue backed up" is asked **after** it is backed up and a gauge starting at
  zero answers the wrong question; and `Distribution.of([])` **raises** rather
  than returning a row of zeros, since a `min_seconds` of 0.0 computed from
  nothing reads as "instant". The gmsh module lock is now timed *apart* from the
  session it guards, so contention is visible for the first time. 11 mutations,
  all caught. Not done, and named rather than implied: `solve.calculix.run`,
  `linear_static`, `kernel.rebuild` and `kernel.measure` are catalogued
  `wired=False` with the one-line change each needs, and the report prints them
  as `UNMEASURED — no hook is installed`.

- **2026-09-06** — E7 → **the provenance chain has tests, and writing them found
  two of my own that were vacuous.** Decision 3 says every result is bound to the
  geometry, mesh, material, load case and solver version that produced it, and
  the failure when it is not is silent: a number without its chain looks exactly
  like a number with one, until somebody needs to know which mesh it came from.
  The digests are pinned per *input*. That distinction is the finding: deleting
  the midside nodes from `mesh_digest`, and separately deleting the connectivity,
  left **all thirteen tests passing** — because `promote_to_tet10` appends the
  midside nodes to the *node array* too, and refining a mesh moves its nodes, so
  both of my "this changes the digest" tests were passing on the coordinates
  alone and proving nothing about the fields they named. Two new tests hold
  `nodes` fixed and change only `tets`, and only `midside`, and all three
  mutations now fail. Also pinned: a solver version that could not be read is
  `None` **with a reason** rather than guessed, and the digest names its own
  algorithm (`sha256:…`) so an old record stays readable the day the algorithm
  changes.

- **2026-09-06** — E14/E15/E16/E17.3/P2 → **five phases opened at once**, chosen
  because each unblocks something specific rather than because they were next in
  the list. **E14** is what gate G3 needs and what the ladder cannot pass rung 3
  without — M2 onwards are all assemblies, and rungs 1–3 are one part. **E17.3**
  is the sequencing exception the plan already records: M3's enclosure and M5's
  press both need sheet metal long before the rest of Phase 17. **E15** is
  observability first and storage later, on the argument that the measurement
  has to exist *before* the thing being measured — a 5,000-part clash check and
  an optimisation loop doing hundreds of rebuilds are both landing this week, and
  nothing today can answer "why was that slow". **P2** is the tenancy layer no
  external user can be let near the product without. And **E16.1** is the
  bottleneck the board already names, now with a price on it: 108 tools against
  an `AI_TOOL_LIMIT` of 40 means 68 the agent cannot see, and the 2026-09-06 gate
  measured that `qwen3.5:9b` — which fits entirely in this card's 8 GB — fails
  the 40-tool payload where the 30B model at 72% CPU succeeds. Shrink the payload
  and the faster model becomes usable, which changes what every future gate
  costs.
  All five are being built **with their tests**, which is the correction to
  2026-09-06's earlier batch: seven packages landed that night with none, and
  four sessions since have been spent paying that back.

- **2026-09-06** — E7 → **the benchmark layer cannot lie, and now it is proven.**
  `benchmarks.py` is deliberately machinery and no cases, and it is the layer
  that decides whether the cases can lie when they arrive. The failure it exists
  to prevent would be catastrophic in the one part of the codebase whose whole
  purpose is to be trusted: **a target that looks published and is not** — a
  NAFEMS number recalled from memory, or reverse-engineered from what our own
  solver happened to return, converts "we have not validated this" into "we
  validated this and it passed". So `PUBLISHED` requires a citation, `UNKNOWN`
  **forbids a value** and must say what would make it known, and a tolerance
  must carry a justification, because an unjustified band is one somebody widens
  the first time a run misses it — and the only move available to a person who
  does not know what the band was covering is to widen it. Exactly one `Outcome`
  is a pass: `MEASURED` ("we ran it, there was nothing to compare against") and
  `UNCONVERGED` are real results and are not validation. Five mutations, all
  caught — and two of them revealed that **two of my own tests were passing for
  the wrong reason**: they omitted `tolerance_reason`, so they raised on the
  missing justification rather than on the guard they named, and both are fixed.

- **2026-09-06** — E12 → **bought-in parts that say what they do not know.** A
  bolt whose mass is known but whose proof load is not cannot be checked, and
  that is the whole difference between a parts library an engineer can sign off
  and one that gets somebody hurt — the second kind is easier to build, reads
  identically, and fails only when a joint opens. The data is verified against
  **ISO 898-1 arithmetic done in the test**, not against the record: an M8's
  tensile stress area is 36.6 mm², an 8.8's proof stress is 580 MPa, so its proof
  load is their product, and 8.8 means 800 MPa ultimate with 80% of it at yield.
  A proof load that does not satisfy that is either the wrong property class or
  the wrong stress area, and both produce a believable number. The design goes
  one step past "absent" and that step is what makes it usable: **every absent
  quantity carries why.** Tightening torque is missing from every bolt not
  because nobody typed it in but because it is a property of the *joint* — thread
  and head friction vary by a factor of three between a dry zinc-plated bolt and
  a lubricated one — so it is computed from VDI 2230 with a coefficient the
  caller must defend, and a dry bolt correctly needs more torque for the same
  preload. Mutations: `require()` returning `None` instead of refusing fails 4;
  dropping the reason fails 1; dropping the list of what *is* held fails 1;
  ignoring the friction argument fails 1.

- **2026-09-06** — E17 → **the projection convention is real, not decorative,
  and now proven so.** First and third angle put the views on *opposite sides*
  of the front view, and a drawing read in the wrong one is manufactured
  mirrored with nothing about it looking wrong. The pair of tests catches a
  convention wired to nothing whichever way the default falls: ignoring
  `projection` fails two, and swapping the two conventions fails two. Also
  pinned: a dimension traced to the **parameter** that set it is distinguishable
  from one measured off the solid — the leverage `app/design/` gives this
  package that a CAD system reverse-engineering a face does not have — and
  nothing on a sheet built with no traced dimensions may claim a parameter
  provenance it does not have. One real defect fixed: `lay_out` checked for
  emptiness *after* projecting, so a document with no solid reached
  `HLRBRep_Algo.Add(None)` and raised `TypeError: incompatible function
  arguments` — an OCCT binding message about a C++ overload, for what is really
  "this part has nothing in it yet". It is refused up front now, in the register
  the rest of the codebase uses.

- **2026-09-06** — P4 → **document-borne prompt injection stops being a
  hypothetical.** `CLAUDE.md` has described it as *"a tested-against attack
  class here, not a hypothetical"* while `app/documents/` shipped without a
  single attack test. There are 32 now, and they pin the **structural** defence
  rather than any filter — because a filter that greps for "ignore your previous
  instructions" is defeated by saying it in French, which is one of the six
  attacks here. What cannot be rephrased around: `UntrustedText` deliberately
  does not subclass `str`, so `"prompt " + text` raises and `f"{text}"` yields a
  description; `"
".join([...])` raises; and `render_into_user_message` is the
  only accessor returning payload characters — its signature **requires the
  user's own message**, so there is no call anyone can write that puts
  attachment content into a system prompt assembled from constants. Also pinned:
  a payload forging its own `[attachment: trusted_spec.pdf …]` header is
  defanged while the words survive, so a reader can still see what the document
  tried; and the same boundary covers a filename, a DXF layer name and an entity
  attribute, because the type is the boundary rather than each reader being
  defensive. Mutations: making it a `str` subclass fails **all 32**; leaking the
  payload through `__str__` fails 6; removing the header defang fails 1.

- **2026-09-06** — E13 → **tolerance stack-up has tests, and the design they
  found is better than the phase asked for.** Worst case and RSS are checked
  against arithmetic done in the test — a three-part ±0.10 chain closes at 0.30
  worst case and √0.03 = 0.1414 RSS, and the two differ by an amount nobody
  could mistake for rounding. But the half worth having is the refusal:
  **`stack()` will not hand over an anonymous RSS number.** Every assumption in
  `Risk` must be established from declared capability data or acknowledged by a
  named person, because an assumption nobody signed for is an assumption nobody
  made — and `Risk.INDEPENDENCE` can never be established from numbers at all,
  since two dimensions cut on the same machine in the same setup are not
  independent and only somebody who knows how the parts are made can say so. An
  unavailable statistical result carries `half_width_mm = None` rather than a
  number a caller would use. Mutations: accepting an unsigned acknowledgement
  fails a test, granting RSS with nothing acknowledged fails a test, and giving
  worst case the statistical arithmetic fails ten of fifteen.

- **2026-09-06** — E9 → **clearance through a motion range has tests, and the
  defect they were written for.** The agent building `app/dynamics/` found,
  before a rate limit killed it, that `measured_poses` was
  `samples - len(failures)` — so a sweep that short-circuited at pose 9 of 21
  published `measured_pose_count: 21`. Twelve poses nobody looked at, counted as
  measured, in the payload an assertion reads. It had fixed it (`attempted` and
  `stopped_early` are fields now, and the provenance says the interference volume
  is the *first* found rather than the largest) and had not tested it. Restoring
  the original expression fails a test. Also pinned, and the more important half:
  **a collision only in the middle of the travel is caught** — an endpoint check
  is the natural thing to write and is exactly the check that misses, since an
  arm clears at both extremes and fouls at forty degrees. Mutating the sweep to
  look at only the first and last pose fails nine of eleven.

- **2026-09-06** — E10/E7/E12 → **the first of the missing tests, and the defect
  they were written for.** The agent building `app/optimise/` found, before a
  rate limit killed it, that **an optimum sitting on an active constraint was
  reported as `NO_FEASIBLE_POINT`** — telling the user that no design in the
  space satisfies a requirement the design in front of them satisfies to fifteen
  digits. Measured: on x = y = 1 subject to x + y >= 2, SLSQP stops at
  2 − 3.3e-15; on the OCCT plate whose answer is 100×100 subject to
  surface_area <= 24000, it stops 4.1e-4 mm² over, one part in 6e7 and exactly
  the size its own tolerance permits. Nearly every real constrained run would
  have said it, because nearly every real constrained optimum is on a
  constraint. The agent had fixed it — a back-off in the **target** the driver
  aims at, never slack in the **check** — and had not tested it.
  `tests/test_optimise_honesty.py` now pins both halves: the algebraic optimum is
  found and reported as converged, and the constraint is still evaluated exactly
  as written so a design that genuinely misses is still a miss. Removing the
  back-off reproduces the original defect and three tests fail.
  One expectation of mine was wrong and the code was right: `NO_FEASIBLE_POINT`
  means the driver *converged* on an infeasible design, while running out of
  budget is `BUDGET_SPENT`. The distinction is worth keeping, so the test pins
  the honesty rule — not converged, and `solution` is `None` — rather than a
  particular stop code that would break the day the driver changed.
  `tests/test_verify_convergence.py` and `tests/test_materials.py` also survived
  the cull (160 tests). Four of the seven packages are still `TESTS NOT WRITTEN`.

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
