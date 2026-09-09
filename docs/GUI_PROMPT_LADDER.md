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
* **Next run** — first one under this method. Ten phases complete; write each prompt against
  what shipped between 2026-09-06 and 2026-09-09: plane analyses, conduction, convergence
  studies, the requirements check, sheet-metal folding, and the assembly repository.

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
