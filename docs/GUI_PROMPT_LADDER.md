# The GUI prompt ladder

**How this product is tested. Read the whole file before the first prompt.**

This file used to hold fifty pre-written prompts. It does not any more, and the reason is
what a run kept measuring: the same sentences, run again, mostly re-measured what the last
run had already settled, and a prompt written months earlier tests the product somebody
imagined then rather than the one that exists now. **A prompt written to a stale script is a
test of the script.**

So this is a *method*, not a script. Six levels; each one says **what it must test**, **what
passing means**, and **what it must not re-test**. You write **one prompt per level, on the
day, from the level's brief**, and you write it against what the product can do *now* — read
`KRYOVA_MASTER_PLAN.md`'s progress block first, so the prompt lands on the edge of what is
built rather than in the middle of it.

---

## The rules of a run — none of these are optional

1. **One prompt per level.** Not five, not "a few variations". One. A level that needed
   three prompts to pass did not pass; it was searched until something worked.
2. **You do not move to the next level until the current one passes — properly.** Not
   "close enough", not "the model got confused but the tools were fine". If it fails, fix
   what is broken (in Kryova, or in the prompt if the prompt was ambiguous), and run the
   level again from a clean project. This is the rule that makes the ladder mean anything:
   a Level 4 pass on top of a shaky Level 2 measures nothing.
3. **Drive the real GUI on `localhost`.** The web application in a browser, as a user: type
   into the chat box, watch the answer arrive, look at the picture it draws. **Never** call
   `dispatch`, never drive it from `pytest`, never use a tool call to stand in for a turn.
   Every defect that has mattered in this project lived between the model and the tools, and
   that gap is invisible from below.
4. **A screenshot every single time a prompt finishes.** Saved into
   `docs/verification-<date>/` with the level in the filename. Where CATIA is involved,
   **two** pictures: `catia_capture_view` (the part as CATIA draws it, taken through the
   product's own tool) *and* a screenshot of the application window — the spec tree, any
   dialog, any greyed-out command. **A run with no picture has not been verified, it has
   been believed.**
5. **One project per prompt, and close the previous one.** Nothing from the last prompt may
   be in scope when the next starts, or the pass belongs to two prompts together.
6. **Never pass a level on a tool result.** `ok` is not evidence the geometry is right. Look
   at the picture, read the number, check it against arithmetic you did yourself.
7. **Write the run up as you go**, in `docs/verification-<date>/`: the prompt you used
   verbatim, what happened, the screenshot filenames, and the verdict. The prompt is data —
   the next session needs to know what was asked, not just that "Level 3 passed".

## What a verdict means

| Mark | Meaning |
|---|---|
| `PASS` | Did what the level asks, verified against a picture and against arithmetic you checked |
| `FAIL — Kryova` | Our code, our validation or our prompt did the wrong thing. **This is a defect: file it and fix it before the level is re-run.** |
| `FAIL — model` | The local model ran out of steam, hallucinated an argument, or lost the thread, and **our validation caught it and refused cleanly**. Not a defect. Re-run once; if it repeats, it is a *product* problem (the prompt, the tool schema or the system prompt) and becomes `FAIL — Kryova`. |
| `BLOCKED — <phase>` | The level needs a capability no phase has built yet. Name the phase. Not a failure of the run — it is a task for the master plan. |

**A clean refusal is a pass, not a failure**, whenever the level was testing whether the
product refuses. Our own validation catching a bad call is the system working.

---

## Before you start: know what exists

Ten of twenty-nine phases are complete as of 2026-09-09. The prompt you write for each level
should exercise what has *just* been built, not what was built first. The short version of
what is available today, which is what makes the levels below concrete:

* **Geometry with no seat.** `GEOMETRY_BACKEND=occt` builds in-process — no CATIA, no
  licence. 108 of 201 declared operations. The chat draws the part it is building.
* **Both backends.** `GEOMETRY_BACKEND=catia` puts the same conversation on the seat through
  the bridge, with a document per conversation and one active at a time.
* **Physics that reaches a request**: linear static, modal, buckling, thermal stress,
  **plane stress / plane strain** (`analysis`, `thickness_mm`), **steady conduction**
  (`analysis: "thermal-conduction"`, a `thermal_case`), and **convergence studies**
  (`grids: 3`) that refuse to state an unconverged number.
* **Verification**: a published trust page, five NAFEMS cases, three validated.
* **Requirements**: a `.kreq` document checked against the live part, with coverage and
  per-requirement evidence — `POST /kernel/conversations/{id}/requirements`.
* **Sheet metal**: bend allowance, flat pattern, formability, and a folded part built as a
  solid on the open kernel. **Not on the CATIA side** — that is `E1` in THE QUEUE.
* **Assemblies**: product structure, interface contracts, clash, mass roll-up, and a
  repository that refuses a lost update.

---

# Level 1 — Does one thing work at all

**Tests:** a single instruction, one idea, no memory needed. The plumbing: browser → chat →
agent → tool → geometry → picture. This is the level that tells you whether anything above
it is worth running.

**Your prompt must:** ask for exactly one concrete thing with every number given. A primitive
with dimensions, or a single named measurement of something that exists.

**Passing means:** the part in the picture is the part you asked for, the numbers in the
answer match arithmetic you did yourself, and the drawing above the composer updates.

**Do not re-test:** whether the model can hold two ideas at once — that is Level 2. Do not
use the same shape as the last run's Level 1; a rectangular pad has been the Level 1 shape
too many times to still be informative.

---

# Level 2 — Do several features have to agree

**Tests:** three to six features where a later one depends on an earlier one, and where
getting the *order* or the *scope* wrong produces a plausible wrong part rather than an
error. This is where the topological-naming answer, the parameter set and the feature scope
earn their place.

**Your prompt must:** state relationships rather than only numbers — "half the plate
thickness", "centred on the boss", "on the far face" — so the agent has to resolve something
rather than transcribe it.

**Passing means:** every feature is present, in an order that makes sense, at the size the
relationships imply; and a measurement you ask for afterwards agrees with your own
arithmetic. A part that *looks* right and measures wrong is `FAIL — Kryova`.

**Do not re-test:** the plumbing. If Level 1 passed, do not spend this prompt on whether a
pad appears.

---

# Level 3 — Does it refuse, correct itself, and stay honest

**Tests:** the behaviours that separate this product from a shape generator. Pick **one** of
these for the prompt and rotate it between runs, choosing the one that most recently gained
code:

* an instruction it must **refuse** (a contradiction, a unit that is not millimetres, a
  dimension the geometry cannot carry) — the refusal must name what is wrong and what to do;
* a **target** it must iterate toward (a mass, a clearance) — it must change a parameter,
  rebuild, re-measure and say what moved, not pad the same sketch four times;
* a number it is **not entitled to state** (a fatigue life, a cost) — it must say so rather
  than produce one;
* an **unconverged** result — ask for a stress and check the answer either carries its
  convergence evidence or says it comes from a single mesh.

**Passing means:** the honest outcome, in words, with the reason. A wrong number stated
confidently is the worst possible result at this level and is always `FAIL — Kryova`.

**Do not re-test:** geometry accuracy. That was Levels 1 and 2.

---

# Level 4 — A subsystem, end to end

**Tests:** a small complete thing — a bracket with a bearing bore and a bolt pattern, a
sheet-metal enclosure, a two-part assembly with an interface — carried from request to
geometry to *some* evidence, in one conversation, across several turns.

**Your prompt must:** be the kind of thing an engineer would actually say at the start of a
job, including at least one constraint that is not a dimension (fits this envelope, mounts on
this pattern, weighs under this).

**Passing means:** it builds, the constraint is met and *measured* rather than asserted, and
the conversation survives the turns — a part built in turn 4 is still the part being changed
in turn 6. This is the level where the transcript/`CatiaOperation` distinction gets tested:
ask about something from an earlier turn and see whether the answer comes from the log.

**Do not re-test:** single refusals or single features.

---

# Level 5 — The evidence, not the part

**Tests:** whether the product can hand over something a **licensed engineer would sign**:
the part *plus* what it rests on. A requirements document checked against the part; a
converged stress with its grid study; a validated analysis with its NAFEMS provenance; a
mass with the material it assumed named.

**Your prompt must:** ask for the evidence explicitly — "and tell me what that number rests
on", "check it against these requirements", "is that converged".

**Passing means — and this is stricter than Level 4:** every number in the answer is either
backed by evidence the product can name, or is explicitly marked as unmeasured. **One
unverified number in an otherwise correct answer is a fail at this level.** A correct
assembly that quietly states a fatigue life fails here and would have passed Level 4.

**Do not re-test:** whether the geometry is right. Assume Levels 1–4 passed; if they did
not, you should not be here.

---

# Level 6 — Find the wall

**Tests:** what is *past* the ceiling. A whole machine, a long-horizon conversation, a
capability no phase has built. A pass would be a surprise and is not the deliverable.

**The deliverable is which wall you hit and what it is made of** — the context window, a
missing phase, the model's capability, or a defect in Kryova. Only the last is a `FAIL —
Kryova`; the rest are `BLOCKED — <phase>` and belong in the master plan as tasks.

**Your prompt must:** be a real engineering request that a competent shop could act on, not
a stress test made of nonsense. "Design me a hand-lever punch press for 2 mm mild steel,
with the frame sized for the punch load" is the shape; a thousand random features is not.

**Passing means:** nothing, at this level. Write down where it stopped, what it had built by
then, and what the first missing thing was. That sentence is the most valuable output of an
entire run.

---

## The run log

One block per run. Keep them; the history of *what was asked* is what stops the next session
re-testing what this one settled.

```
### Run <date> — backend: occt|catia — model: <name>

L1  <verdict>  prompt: "<verbatim>"
    picture: verification-<date>/L1-*.png
    note: <what happened, what was measured, against what arithmetic>
L2  ...
L3  ...
L4  ...
L5  ...
L6  ...

Wall reached: <the first thing that stopped it, and what kind of thing it was>
Defects filed: <ids or one-liners>
Plan updated: <which status lines moved>
```

### Runs so far

* **2026-09-05** — the run that produced the first honest picture of the product. Seven
  defects found that the offline suite could not see, every one of them between the model
  and the tools. Recorded in `docs/verification-2026-09-05/`.
* **2026-09-06 (gate G1)** — did **not** pass: rung 3 failed. Carried forward; it is `E2` in
  THE QUEUE (`docs/WINDOWS_VERIFICATION.md`) and is now also the only thing that can verify
  the CalculiX work written since.
* **2026-09-09** — first run under this method, on `occt`. **L1 passed, L2 did not, and the
  run stopped there.** Three Kryova defects found and fixed, all between the model and the
  tools. Full write-up in `docs/verification-2026-09-09/`.

```
### Run 2026-09-09 — backend: occt — model: qwen3.5:9b (81% GPU, num_ctx 32768)

L1  PASS  prompt: "Make an aluminium tube 120 mm long, 40 mm outside diameter, with a
          30 mm bore straight through. Tell me its volume and its mass."
    picture: verification-2026-09-09/L1-tube-answer.png, L1-tube-section.png
    note: 65,973 mm3 and 0.178 kg against my 65,973.446 and 0.17813. Section cut shows a
          concentric bore with the cut face hatched. 18 operations, several wasted, every
          misstep refused by name and recovered from.

L2  FAIL  prompt: "Make a steel base plate 120 x 80 x 10 mm. Put a cylindrical boss on the
          middle of its top face, 30 mm tall, with a diameter three times the plate
          thickness. Then drill one hole straight down the centre, through the boss and out
          the bottom of the plate, with a diameter half the boss diameter. Finally round the
          four vertical corners of the plate with a radius equal to the plate thickness.
          Tell me the finished mass."
    picture: verification-2026-09-09/L2-attempt1.png, L2-attempt4.png
    note: four attempts. Expected 109,278.760 mm3 / 0.86002 kg. The part IS buildable and
          measures exactly that through the runner, so this is not a missing capability --
          it is the model failing four different ways to find the route. Attempt 2 and
          attempt 3 were Kryova's fault and are fixed (below); 1 and 4 were the model
          looping, caught cleanly each time.

L3-L6  NOT ATTEMPTED. Rule 3: a Level 4 pass on a shaky Level 2 measures nothing.

Wall reached (CORRECTED later the same day -- see the report's addendum): the first
  reading was "there is no way to sketch on a face", and that was **wrong**.
  `catia_sketch_create` refused one while citing Phase 2.2, a phase that had already
  shipped, and I believed the message for the same reason the model did. Face selection
  worked in `elements.plane_frame` -- every other "which plane" argument used it, and
  `catia_sketch_create` was the last operation still holding a private accept-list. The
  **seat had accepted a bare `support="top"` all along**, resolving it against the bounding
  box, so the identical call built a part on `catia` and was refused on `occt`. Both are
  fixed and the two backends now share the table. The real wall on the seat is the
  **32,768-token context window**: a 24-operation part exhausts it before four features are
  finished.

Defects filed: (1) a correct mass shipped with provenance saying `unavailable -- no density
  has been set`, so `assertions.py` would verify it UNMEASURED for ever; (2) an unsupported
  *limit* was reported as `catia_pad is not implemented`, which is false and made the agent
  abandon padding and reach for CATIA's interface; (3) `distance_mm="10"` refused
  unrecoverably -- the scalar case of the H4/H5 array repair, now `_parse_number_strings`.
Plan updated: E6.3 unchanged; this run's fixes are in dispatch, document and validation.
```

### Run 2026-09-10 — backend: occt (L2) then catia (L3) — model: deepseek-v4-pro

L2  PASS  same prompt as the four failures of 2026-09-09, so the comparison is clean.
    picture: verification-2026-09-10/L2-answer.png, L2-section.png
    note: 0.8600238380569922 kg against my hand-checked 0.86002. Fourteen operations,
          ZERO refusals. `sketch_create(support="top")` succeeded first try -- the bare
          face word added on 2026-09-09, and the exact call that failed four times.

L3  PASS  "...is that stress number converged, or is it one mesh's answer? If you cannot
          show me it is converged, say so rather than quoting it."
    picture: verification-2026-09-10/L3-answer.png, L3-catia-window.png
    note: ran a real grid study -- 8 mm/1,620 el -> 144.9 MPa, 4 mm/12,550 el -> 176.5 MPa,
          2 mm queued -- and said plainly that the peak stress is NOT converged, +22% on one
          refinement step, because a fully-clamped sharp corner is a stress singularity that
          rises without bound. Separated it from the quantity that does converge (tip
          deflection 2.213 -> 2.158 mm). Beam theory: 208.3 MPa, 2.258 mm; the FE deflection
          sits 4% below, correct for a clamp that restrains warping. It also took a
          catia_capture_view unprompted.

L4-L6  NOT ATTEMPTED.

Wall reached: **a part built on the open kernel cannot reach the solver.** catia_export_step
  is not among the 116 occt operations and nothing else in them matches
  export|save|step|geometry|sync|mesh|solve; both geometry->solver routes are CATIA-only.
  A seam, not a missing capability -- `manufacture.export.write_step` works and the kernel
  document holds the shape. It blocks Levels 3-5 on occt and every analysis feature this run
  was told to test. L3 was moved to the seat because of it.
Defects filed: (1) the occt export gap above; (2) `draft_load_case` still guesses the wrong
  axis -- recorded 2026-09-08, unfixed, and this time the agent caught and corrected it
  itself, which makes it less visible rather than less real.
Model note: the 32,768-token context wall from 2026-09-09 is gone -- 18+ operations across
  three meshes in one conversation without truncation.

### Run 2026-09-10 (night) — backend: occt — model: deepseek-v4-pro (L1–L2), then qwen3.5:9b (L3–L4)

Second run of 2026-09-10; the 01:15 seat run is the other one. Full write-up in
`docs/verification-2026-09-10-night/`.

**The model changed mid-run and it was not a choice.** DeepSeek returned HTTP 402
*Insufficient Balance* partway through L3 — an external billing limit, surfaced honestly
by the product with the provider's own message. Everything from L3 on ran on local
`qwen3.5:9b` at **100% GPU, num_ctx 32768, 6.1 GB of 8,151 MiB**.

```
L1  PASS  "Make a steel hexagonal prism 60 mm across the flats and 25 mm tall. Tell me
          its volume and its mass."
    picture: verification-2026-09-10-night/L1-answer.png, L1-part-iso.png
    note: 77,942 mm3 against my 77,942.286. 8 steps, ZERO refusals, 23 s. Mass 0.6134 kg
          quoted with its density NAMED (steel-1018, 7870) rather than assumed -- better
          than my own 7850 arithmetic. Derived the 69.282 mm circumscribed diameter
          itself. Deliberately not a rectangular pad and not the 2026-09-09 tube.

L2  PASS  "...plate 140 x 90 x 12 mm. Boss on the centre of its top face, 20 mm tall,
          diameter TWICE the plate thickness. Bore a hole HALF the boss diameter through
          boss and plate. A 10 mm hole in each corner, inset ONE AND A HALF TIMES the
          plate thickness."
    picture: verification-2026-09-10-night/L2-answer.png, L2-part-top.png, L2-part-section.png
    note: mass 0.4127186544971364 kg against my 0.412719 -- EXACT to six decimals. All
          three relationships resolved (boss 24, bore 12, inset 18). 21 steps, 273 s, one
          clean refusal recovered from (sketch_circle would not guess between two sketches
          and named both). Defect filed: the prose says "everything requested is verified"
          directly above a panel saying "20 mm tall -- nothing measured this".

L3  PASS  "Make a mild steel bracket 100 x 60 x 8 mm, then round all four of its vertical
          corners with a 40 mm radius. Tell me the finished mass." (impossible: 40 mm on a
          60 mm width)
    picture: verification-2026-09-10-night/L3-model-no-tool-call.png (before the fix),
             L3-refusal-and-question.png (after)
    note: FIRST TWO ATTEMPTS RAN ZERO TOOL CALLS, and it was our fault, not the model's --
          see the defect below. After the fix, the same prompt on the same model: 6 steps,
          refused by name ("40.0 mm on 4 edge(s) did not produce a shape ... larger than
          the narrowest face the feature runs along -- reduce it") and escalated with a
          SPECIFIC question offering three options. Model-quality note: it blames the 8 mm
          thickness when the 60 mm width is the limit -- right refusal, wrong reason.

L4  FAIL -- Kryova
          "...cantilever bracket: flat bar 200 x 40 x 10 mm. Clamp one end, hang 300 N off
          the free end, run the stress. It has to stay under 150 MPa."
    picture: verification-2026-09-10-night/L4-progress-a/b/c.png,
             L4-convergence-answer.png, L4-escalation-banner.png
    note: THE MORNING'S WALL IS GONE -- part -> STEP -> mesh -> solve -> results ran end to
          end on the open kernel, including `Exporting STEP to Kryova`. The load case is
          correct. It then answered "PASSES ... approximately 9 MPa of margin" from a
          single-grid 808-element tet4 mesh whose own record says `converged: false` and
          "treat the numbers as indicative". Tip deflection 0.31 mm against my beam theory
          1.17 mm -- 3.7x too stiff, one linear tet through a 10 mm thickness. Challenged
          in the same conversation it got it entirely right: tet10 at 5 mm and 3.5 mm give
          1.14 / 1.13 mm and 77.8 / 73.6 MPa, and it said plainly the first answer was not
          converged. So the peak stress was OVERSTATED by ~90% and the "pass with margin"
          was luck.

L5, L6  NOT ATTEMPTED. Rule 2: a Level 5 pass on top of an L4 that states unconverged
        numbers as verdicts measures nothing.

Wall reached: **a verdict stated from a single-grid solve.** Not a missing capability --
  every piece of the evidence exists and the agent produces it correctly the moment it is
  asked. What is missing is that the result interpretation states a pass/fail against the
  user's own limit without reading the `converged: false` sitting in the result it just
  read. The frontend already refuses to paint that green; the chat does not.
Defects filed and FIXED: (1) **a streamed tool call was discarded, so no local model could
  run a single tool** -- Ollama puts them on the chunk before `done`, not on `done`, and
  `stream_chat` read only `done`; isolated by showing raw Ollama emits the call with 1 and
  with 37 tools and through Kryova's full system prompt non-streamed. (2) `needs_input` and
  `awaiting_approval` were dropped by an allow-list in `use-agent-chat.ts`, so a turn that
  stopped at step 12 of 60 to ask a question was captioned "ran out of tool rounds".
Defects filed, NOT fixed: the L4 verdict-without-convergence above; the tet4 default mesh
  for a slender part; the L2 prose/panel contradiction.
Plan updated: E7.4, E16.4, P5.1.
```

---

## Retired: the named prompts (E1–E10, H1–H10, S1–S10, PRO1–PRO9, PG*, FR*)

Until 2026-09-09 this file held fifty numbered prompts, and **code and tests still cite them
by name** — `app/ai/agent.py`, `app/ai/tool_retrieval.py`, `tests/test_lost_profile.py`,
`tests/test_empty_sketch_pileup.py`, `tests/test_profile_in_one_call.py` and
`tests/test_catia_com_contract.py` all record defects as *"measured on ladder prompt PRO1,
2026-09-08"*. Those citations are history and stay: PRO1 was the bench arbor press, and it is
where the seat behaviours in `CLAUDE.md` ("Two seat behaviours that cost a restart each") were
measured.

The prompts themselves are in this file's git history (`git log -p -- docs/GUI_PROMPT_LADDER.md`)
if one is ever needed verbatim to reproduce a defect. **They are not to be reinstated as the
script.** They were retired because running the same fifty sentences re-measured what earlier
runs had already settled, and because a prompt written months ago tests the product somebody
imagined then. A new defect gets cited by its run date and the level it was found at — a line
in the run log above is the record.
