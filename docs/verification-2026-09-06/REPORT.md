# Gate G1 — 2026-09-06, Windows seat

**Gate G1 opens after E6, the solver federation.** What becomes true at it: the
system can say a part *carries its load*, not only what shape it is. Its
verification is rung 3 of the ladder carried forward — measure and correct to a
mass target — plus a load-bearing prompt.

**Verdict: G1 does NOT pass. Rung 3 fails, five times, for five different
reasons.** Four defects in the product were found and fixed; the model's own
limits are recorded separately and are not ours. The load-bearing half was not
reached and is not claimed.

Every one of the five attempts produced a fix. That is the ladder working: rung 3
is hard enough to keep finding real defects, and each is the kind that leaves a
plausible number on a wrong part.

---

## What was measured

| | |
|---|---|
| Backend | `GEOMETRY_BACKEND=occt`, OCCT 7.9.3.1, in-process — **no CATIA seat** |
| Model | `qwen3-coder:30b` via Ollama, 40-tool payload, `AI_MAX_STEPS=45` |
| GPU | RTX 5070 Laptop, 8151 MiB. Model runs **72%/28% CPU/GPU** — see below |
| Path | real `POST /api/v1/ai/chat`, never the dispatcher |
| Commit | `f90f4be` |
| Solver | CalculiX 2.23 at `C:\tools\calculix\bin`, installed tonight |

### Ollama is not on the graphics card, and cannot be

Asked to put Ollama on the GPU, so this was measured rather than assumed.
`qwen3-coder:30b` is 30.5B at Q4_K_M — about 18 GB of weights against 8151 MiB
of VRAM. It cannot fit, and the only lever available is context size, which
frees KV cache for more layers. Measured across four context sizes:

| `num_ctx` | CPU/GPU split | VRAM used |
|---:|---|---:|
| 32768 | 72% / 28% | 6566 MiB |
| 16384 | 70% / 30% | 6526 MiB |
| 8192 | 68% / 32% | 6548 MiB |
| 4096 | 68% / 32% | 6484 MiB |

Dropping the context 8× buys four percentage points, because the card is already
full at ~6.5 GB. **The only way to be genuinely on the GPU is a model that fits.**
Published 2026 benchmarks agree on the same answer for this tier —
**Qwen3.5-9B at Q4_K_M**, the only model achieving full GPU offload at 32K
context (6.96 GB), 54–58 tok/s on an RTX 3070, and the Qwen3 family is rated the
most stable at tool calling at this size. It is downloading; whether it can drive
this system's 40-tool payload is an open question and the next gate's first
measurement. `gpt-oss:20b` is the precedent for caution: it returns prose and no
`tool_calls` at all against a large payload.

---

## Prompt 1 — a plate with a bore. **PASSED**

> Make me a steel plate 200 mm by 150 mm and 10 mm thick, with a 60 mm diameter
> bore through the centre. Then measure it and tell me its mass.

10 tool calls, zero refusals, 234 s. `catia_new_part` → sketch → rectangle →
pad → sketch → circle → pocket → `catia_set_material` → `catia_measure` →
`check_part`.

The agent reported **2.138 kg**. Closed form:

    200 x 150 x 10 - pi x 30^2 x 10 = 271725.666 mm^3
    271725.666e-9 x 7870 = 2.1385 kg

Correct to four figures. `g1-plate-iso.png`, `g1-plate-top.png`,
`g1-plate-front.png`, `g1-plate-section-z.png` — the iso view shows a genuine
through-bore with the hidden lines below it.

**One transient failure worth recording.** The first attempt of the session
returned **zero tool calls**: the model emitted a text-form
`<function=catia_new_part>…` block in its content rather than a structured
`tool_calls` response. It did not recur on a freshly loaded model. What matters
is that **the product handled it honestly** — it replied *"I wrote out the steps
below instead of running them, so nothing has actually been done"* rather than
narrating a part that did not exist. That is the honesty convention working
under a failure nobody designed for.

---

## Prompt 2 — rung 3, measure and correct to 2.4 kg. **FAILED**

> Design a steel mounting plate: 200 mm by 150 mm, with a 60 mm diameter bore
> through the centre and four 12 mm clearance holes, one 20 mm in from each
> corner. It has to weigh 2.4 kg. Pick a starting thickness, build it, measure
> the mass, and then adjust the thickness until the measured mass is within 20
> grams of 2.4 kg.

17 tool calls, 355 s. The agent's closing answer: *"Thickness: 11 mm, measured
mass: 2.391 kg. This is within 20 grams of the target."*

**Both numbers are true and the part is wrong.** `g1-rung3-iso.png` is the
evidence, and nothing else in the run was.

Measured: `solid_count: 5`, volume 303874.515 mm³, mass 2.391492 kg. The part is
a plate with a 60 mm bore and **four loose posts standing inside the bore**:

    200x150x11 - pi x 30^2 x 11 + 4 x pi x 6^2 x 11 = 303874.515 mm^3   (exact match)

Note the sign on the last term. The holes were *added*, not cut.

### Finding 1 — ours, serious, now fixed

**A part that fragmented into five disconnected solids was reported as a
finished component with a mass, and nothing objected.**

`contract.py` has described `solid_count` since it was written as *"more than
one means the operation left the part in pieces, which is usually a defect and
never a warning on its own."* Nothing acted on it. The measurement was correct,
the mass was correct, and the mass was **inside the 20 g tolerance the user
asked for — by coincidence, on a part that is not a part.**

The mass of five disconnected solids is not the mass of a component; it is the
sum of unrelated things, and a caller who does not know that is being told
something false in a true number. `measure()` now sets `in_pieces` and an
`advisory` naming the likely cause. Deliberately **not** a refusal: a multi-body
design is legitimate, and refusing it would break those. The rule is that it is
never silent again.

Fixed, with `tests/test_part_in_pieces.py` reproducing the exact gate part —
same 303874.515 mm³, same 2.391 kg — and three mutations of the guard, all
caught.

### Finding 2 — the model, not ours

It read *"20 mm in from each corner"* as `at_radius_mm: 20`, a 20 mm bolt circle
from the centre, which is inside the 60 mm bore. A spatial-reasoning failure of a
30B model, and exactly the class `CLAUDE.md` says to expect: *"a weak local model
is part of what is being tested."* The product's job is not to prevent it but to
**not report the result as success**, which is Finding 1.

### Finding 3 — the correction loop is still not used

Told to adjust the thickness, the agent did **not** call `catia_set_parameter`.
It called `catia_pad` with a `feature` argument (correctly refused), then
`catia_feature_rename` on `Pad.1` to `Old_Pad`, then padded the sketch a second
time at 11 mm. The document ends carrying two pads. The tool exists and works —
it was built on 2026-09-05 precisely for this — and the agent still reaches for
a rebuild instead. That is a prompt and tool-description problem, unfixed, and it
is the third session in a row it has appeared.

---

## Attempt 3 — after the first two fixes. **FAILED, differently**

Same prompt, holes on a 200 mm bolt circle. 15 calls, 327 s. The agent padded the
outline at 10, then 11, then 11.5 mm — three stacked pads — created a sketch
called `Hole Positions`, drew all four circles into it, and **never pocketed it**.
It then reported *"11.5 mm, 2.459 kg, within 20 grams of 2.4 kg."* 2.459 is
**59 grams** out; the model's arithmetic was simply wrong.

Measured: one solid, 11.5 mm, volume exactly `(200x150 - pi*30^2) * 11.5` — a
plate with a bore and **no holes at all**. Fourteen of fifteen calls returned
`ok`.

Two more product defects, both fixed (`41a742e`):

- **A sketch drawn and abandoned was silent.** A sketch is not geometry, so an
  unused one is silent *by construction* — there is no failed operation for
  anything to notice. But nobody draws four circles for no reason. `measure()`
  now reports `unused_sketches`; reported, not refused, because a sketch may
  legitimately be drawn before it is used.
- **The same sketch built three times was accepted.** Refused now, and narrowly:
  only when the second call differs from the first in *nothing but a dimension*.

## Attempt 4 — the fixes work. **STILL FAILED, and closer**

21 calls, 474 s. The sequence that matters:

    [ERR] catia_pad {"sketch": "Main_profile", "length_mm": 10.11}     <- new guard fired
    [ERR] catia_set_parameter {"feature": ..., "parameter": ...}        <- wrong argument names
    [ERR] catia_set_parameter {"name": "Pad.1/length_mm", "value": ...} <- no unit
    [ok ] catia_set_parameter {"name": "Pad.1/length_mm", ..., "unit": "mm"}
    [ok ] catia_set_parameter {... "value": "10.24" ...}
    [ok ] catia_set_parameter {... "value": "11.24" ...}

**The agent used `catia_set_parameter` for the first time in four sessions**, and
it did so because the refusal told it to. The correction loop ran: 10.11 → 10.24
→ 11.24 mm, converging on 2.399 kg — 0.8 g from target, and this time the
arithmetic in its answer was right. All holes were cut. One solid. Three
features, not six.

It still failed, on geometry. Asked for holes on a *200 mm bolt circle* in a
200x150 plate — which my prompt should not have asked for, since a 100 mm radius
lands on the edge of a plate spanning x ±100 — two circles cut half-moon notches
in the edges and **two missed the part entirely and cut nothing**. `g1-pass-top.png`
shows it. The volume decodes exactly:

    200*150*11.24 - pi*30^2*11.24 - 2*(half a 12 mm circle, 5 mm deep) = 304854.16

Third product defect, fixed (`01bfccc`): **a cut that removes nothing succeeded
silently.** `BRepAlgoAPI_Cut` returns the target unchanged when tool and target
do not overlap, and `IsDone()` is true. Now refused, with the reason — a boolean
cut with no overlap is a success that changes nothing — and the likely cause.
Its honest limit is pinned as its own test: a *partial* miss still passes, and
catching that needs a per-profile check that is not pretended here.

Fourth fix (`2556228`): the advice now carries the required `unit` as well as
the name, because the agent burned two calls discovering it.

## Attempt 5 — Qwen3.5-9B, the model that fits the card. **WORSE**

Pulled on the strength of five independent 2026 benchmarks naming it the best
8 GB-tier model and the most stable at tool calling. Measured here:

| | qwen3-coder:30b | qwen3.5:9b |
|---|---|---|
| GPU residency | 28% | **76%** |
| VRAM | 6566 MiB | 6872 MiB |
| Rung 3, same prompt | 474 s, 21 calls, converged on mass | **512 s, 25 calls, gave up** |

Three times the GPU residency and it was *slower and worse*: it placed the four
holes at four different wrong coordinates, re-padded the **bore** sketch turning
the hole into a boss, failed to change the thickness at all, and closed with
*"I did not manage to produce an answer."*

**`qwen3-coder:30b` stays**, running 72% on the CPU. That is a measured result
and it contradicts the benchmarks for this workload — general tool-calling
ability at 9B does not survive a 40-tool payload with interdependent geometric
state. Worth re-testing when the payload shrinks.

Both new guards fired correctly during this run too, which is the clearest
evidence they are not tuned to one model's mistakes.

## What was not verified, and why

- **No CATIA seat was involved.** `GEOMETRY_BACKEND=occt`; `/catia/status`
  reports *"no CATIA seat is involved, and none is needed."* So there is no
  `catia_capture_view` and no application-window screenshot in this report, and
  the localisation path (`Extrusion.1` / `Poche.1` on the French seat) is
  untested tonight. The four renders are the product's own deterministic HLR
  output and are the pictures that were actually looked at.
- **The load-bearing half of G1 was not reached.** `run_simulation` exists in the
  agent's vocabulary but was never exercised, because rung 3 failed first and a
  gate that half-passed did not pass. CalculiX itself is verified — σ = F/A to
  5.7e-16 against the real solver — but *through the chatbot* it is unproven.
- **Ollama was not on the GPU**, and on this card with this model it cannot be.

## Correction, 2026-09-06 later the same day — Finding 3 was ours, not the model's

Attempt 4's headline above says the agent used `catia_set_parameter` "for the
first time in four sessions, and it did so because the refusal told it to".
That is true and it is not the whole cause, and the rest of the cause is ours.

`AI_TOOL_LIMIT=40` was set on the gate server, which turns **tool selection** on.
Probing the selector that was shipped at the time against the real 110-tool OCCT
registry, with the prompts from this report:

| message | tools offered | `catia_set_parameter` |
|---|---:|---|
| rung 3, "change the thickness until it weighs 2.4 kg" | 13 / 110 | **withheld** |
| "put four M8 clearance holes on a 70 mm bolt circle" | 11 / 110 | withheld |
| "check it clears through the travel" | 8 / 110 | withheld |

**The tool the rung-3 prompt is entirely about was not being offered to the
model.** For three sessions this report and its predecessors recorded "the agent
still will not use `catia_set_parameter`" as a limitation of the model. It was
not. It was our own tool selection hiding the tool.

And the mechanism behind attempt 4's success is now exact rather than inferred.
The refusal message added in `d5e94bf` names the tool *in prose*. The selector is
lexical. So:

| the agent's context | tools offered | `catia_set_parameter` |
|---|---:|---|
| the request alone | 9 | withheld |
| the request plus the refusal text naming the tool | 43 | **present** |

The refusal fix worked, and it worked by putting the tool's name into the text
the selector reads — not by teaching the model anything. Both fixes were worth
making and neither was understood at the time.

Recorded here rather than quietly corrected in the board, because this report is
the evidence somebody would go back to, and it blamed a model for something the
product was doing.

## Attempt 6 — after the tool-selection fix. **CLOSEST YET, still failed**

Re-run once E16.1 landed, because the correction above says tool selection had
been withholding `catia_set_parameter` on this very prompt. 20 calls, 357 s.

**The behaviour changed exactly as predicted.** The agent called
`catia_list_parameters` *unprompted* as its fourth call — it has never done that
— then `catia_set_parameter`, twice with a guessed name (`thickness_mm`, refused
both times), then correctly with `Pad.1\length_mm` after re-listing. It also
reached for `catia_pattern_rectangular` instead of drawing four circles. That is
the fix working: the tools were there to be found.

Final mass **2.400500 kg** against a 2.4 kg target — **0.5 grams**, the closest
any attempt has come.

And the part is still wrong, for two reasons, one of them ours.

**Theirs:** `catia_pattern_rectangular(count=2, spacing_mm=50, second_count=2,
second_spacing_mm=50)` from a hole at (25, 25) puts all four holes in **one
quadrant**, two of them clipped by the top edge. Not "25 mm in from each corner".

**Ours, and it is the more interesting one.** The bore is drawn **dashed** in
`g1-run6-top.png` — a hidden line, because it is a *blind* hole. The pocket was
created with `depth_mm: 10` when the plate was 10 mm thick, which was correct.
Setting `Pad.1\length_mm` to 11.22 replays the part from the top, and the
pocket's depth is a **literal** in its recorded call, so it stayed at 10 mm in an
11.22 mm plate. A through bore silently became a blind one with 1.22 mm of floor.

`catia_set_parameter`'s own docstring promises that changing a dimension
"rebuilds the part from the top, so every feature that depends on it moves with
it". A literal does not move. Every number in the run was correct and the mass
landed 0.5 g from target on a plate whose main hole does not go through it.

Fixed to the extent it can be without a design change: face count is an exact
signal — a through hole contributes one cylindrical face, a blind one a cylinder
*and a floor*, so seven became eight — and the result now carries an advisory
naming the likely cause and pointing at `through_all`, which is immune and is
tested to be. Making depths follow the material is a larger change and is not
pretended here.

## Rung reached

**Rung 2 comfortably; rung 3 not passed, six attempts.** The trend is real though: attempt 6 came within 0.5 g with the correction loop running properly and the parameter tool used unprompted, and its two remaining faults are a pattern the model placed wrongly and a literal depth that our own replay does not update. Rung 3 has now failed three times, for
three different reasons, and each attempt has produced a real fix: the parameter
tool that did not exist, the inner profile padded as a boss, and tonight the part
that came apart without saying so. That is the ladder working as intended.

G1 re-runs after the fix, on the model that fits the card.

---

# Later the same day — one-by-one CATIA-seat verification, first real seat build

A separate, deliberately narrower pass, run per an explicit session instruction:
stop batching prompts, run the real chat endpoint **one prompt at a time**,
against the **real CATIA seat** (not OCCT, which is what every rung above used),
and screenshot whatever CATIA finishes. This is not a G1 re-run and does not
touch rung 3's correction loop — it is the first confirmation that a build
which works on OCCT also works, end to end, on the licensed seat.

## Environment

- CATIA V5-6R2023 (CNEXT), French UI, running locally.
- Bridge daemon re-paired this session to a dedicated `seat-verify@kryova-e2e.dev`
  account (its `CatiaDevice` holds the pairing; the real account
  `bouhlel@gmail.com` was not touched — its password was not available to this
  session and was not guessed; see the memory note left for future sessions).
  The daemon's single-instance lock had two stale duplicate processes left over
  from a prior stretch; both were killed and one clean instance relaunched.
- Server: `GEOMETRY_BACKEND=catia`, `AI_MODEL=qwen3-coder:30b` via Ollama.
- `ollama ps` showed no model resident at the start of the run (it had timed out
  since the last stretch, per the stop-gate policy); the first chat call loaded
  it on demand.

## Run 1 — build without opening CATIA first

Prompt: *"Build me a steel plate 100 mm square and 12 mm thick, with a 40 mm
diameter bore through the middle. Then measure it and tell me the mass."*

- 90.7 s, 1 tool call: `catia_new_part` — **refused**, named reason: no CATIA
  session bound yet.
- This is the weak-model behaviour CLAUDE.md documents ("it calls tools before
  their prerequisites") working as intended: a named refusal from validation,
  not a wrongly built part. Not a defect.

## Run 2 — same conversation, told to open CATIA first

Prompt: *"Please open CATIA first with a new empty part, then build the plate
as I described."*

- 155.9 s, 9 tool calls:

  | tool | result |
  |---|---|
  | `open_in_catia` | ok |
  | `catia_sketch_create` | ERR (no part bound yet — same class of refusal as run 1) |
  | `catia_open_document` | ERR |
  | `catia_new_part` | ok |
  | `catia_sketch_create` | ok |
  | `catia_sketch_rectangle` (100×100) | ok |
  | `catia_sketch_circle` (Ø40) | ok |
  | `catia_pad` (12 mm) | ok |
  | `catia_set_material` (steel-1018) | ok |

- Reported: mass 0.824674 kg, volume 104,920.3553 mm³.
- Closed-form check: `100×100×12 − π·20²·12 = 120,000 − 15,079.6447 =
  104,920.3553 mm³`; `104,920.3553 mm³ × 7,860 kg/m³ × 1e-9 = 0.824675 kg`.
  Matches to the sixth decimal digit reported.

**Minor finding, not fixed.** `open_in_catia(new_part: true)` creates an empty
part (`Part1` / `Steel-Plate-1.CATPart`), and the model's own subsequent
`catia_new_part` created a *second*, separate document (`Part2` /
`Steel-Plate-2.CATPart`) and built into that one — leaving the first orphaned
and empty, visible as a greyed inactive tab in the window screenshot. Not a
correctness bug (the built part is right), but wasteful, and worth either
deduplicating or steering the model away from calling both in one turn.

## Run 3 — same conversation, capture a view

Prompt: *"Capture an isometric view of the part so I can see it."*

- 60.5 s, 1 tool call: `catia_capture_view(view=iso)` — ok.

## Pictures (runs 1–3)

Both required, because they fail to show different things (CLAUDE.md):

- [`seat-01-viewport.png`](seat-01-viewport.png) — the part itself, taken
  through `catia_capture_view`, the product's own tool. Square plate, centred
  round bore, right way up, proportions match the request.
- [`seat-01-window.png`](seat-01-window.png) — the whole application window
  via `scripts/shot.ps1`. Confirms the French seat (`Plan xy`, `Corps
  principal`, `Démarrer`/`Fichier`/`Edition` menus) and shows the orphaned
  `Part1` tab noted above, which the viewport render cannot show.

## Run 4 — parametric change, and a real infrastructure defect

Prompt: *"Now change the bore diameter to 25 mm and tell me the new mass."*

The model never attempted `catia_set_parameter` at all. 10 tool calls, 257.2 s:

`catia_list_features` (ok) → `catia_list_parameters` (ok) → `catia_select`
(ok) → `catia_run_command("Edit Sketch")` (**ERR**, 30 s timeout) →
`catia_update` (**ERR**, "could not save a checkpoint — stopped responding to
heartbeats") → `catia_new_part` (ERR, correctly refused — a document is
already owned) → `catia_list_features` (ERR, bridge not connected) →
`open_in_catia` (ok, but `bridge_connected: false`) → `catia_list_features`
(ERR) → `catia_open_document` (ERR, "the bridge exited immediately after
starting").

**Read from the daemon's own logs (`local-bridge.log`, and the manually-run
daemon's stderr), the daemon itself wedged.** It logged every call cleanly up
to the `catia_checkpoint` that preceded `catia_run_command("Edit Sketch")`,
then produced **no further output at all** — not even a failure — while the
process stayed alive (confirmed by process list). `catia_run_command` calling
an interactive command (`"Edit Sketch"`, not one of the published
`COMMAND_IDS`) on a sketch a Pad had already consumed appears to have wedged
the daemon's COM-calling thread indefinitely. A screenshot taken right after
showed a perfectly normal, responsive part view — **but see Run 5's correction
below: that screenshot did not prove no dialog was open, it proved
`scripts/shot.ps1` cannot see one that is not owned by CNEXT's main window.**
With the daemon's worker thread hung, the server's `app.catia.local_bridge`
auto-spawn-on-demand logic then tried repeatedly to launch a *second* daemon,
which correctly refused (`bridge.lock` still held by the wedged one) and
exited immediately — which is what the model's tool errors were reporting as
"the bridge is not connected."

Recovery at the time: kill the wedged process, clear the stale lock, relaunch.
That fixed the *daemon*. It did nothing for CATIA itself, which is a separate
process and had been carrying whatever `"Edit Sketch"` opened the entire time.

## Run 5 — retried after a clean bridge restart, and the actual cause surfaces

Prompt: *"The bridge should be reconnected now. Please change the bore diameter
to 25 mm using catia_set_parameter on the existing part, and tell me the new
mass."*

27 tool calls, 532.7 s — by far the longest and most revealing run. The
prompt names `catia_set_parameter` explicitly and **the model never called it,
not once**, across 27 calls. That is worth stating plainly: this is not "the
model forgot a tool it wasn't offered" (E16.1's lexical selector would have
included it — the prompt contains the literal string). It is offered, named,
and unused.

**What actually happened, reconstructed from the full transcript:**

1. `catia_select`, `catia_switch_workbench("Part Design")` — ok.
2. `catia_run_command("Edit")`, then `("Hole")` — both refused: *"A CATIA
   dialog is already open and waiting for input."* This is the Run 4 wedge
   showing its real face: CATIA had a dialog open the whole time, left over
   from Run 4's `"Edit Sketch"`. Restarting the daemon (Run 4's fix) cannot
   close a dialog that lives in CATIA's own process.
3. `catia_describe_dialog()` correctly read it: `{"title": "Entrée clavier",
   "buttons": ["OK"], "fields": []}` — a Windows keyboard-input prompt, in
   French ("keyboard entry"), with **no fields reported**, which is itself
   suspicious for an input dialog.
4. `catia_dialog_action("ok")` reported success (`"pressed": "OK"`) — **and
   the very next `catia_run_command` still refused with the identical "a
   dialog is already open" message.** Either the click did not really land,
   or a second, identical dialog was stacked behind the first and immediately
   took its place. Either way, the documented recovery path
   (`catia_describe_dialog` → `catia_dialog_action`) did not actually recover
   the seat, even though it reported that it had.
5. Unable to proceed, the model gave up on `catia_run_command` and did
   something worse: rather than retry `catia_set_parameter` (never attempted),
   it **reused the existing, already-consumed `Plate_Sketch`** —
   `catia_sketch_create({"name": "Plate_Sketch", ...})` against a name that
   already existed in the document returned `ok` with no complaint — drew a
   *second*, redundant 100×100 rectangle and a new 25 mm circle into it
   (alongside the original rectangle and 40 mm circle still sitting there from
   the first build), and padded it, which reported success as a brand-new
   feature `Extrusion.2`.
6. `catia_measure` immediately after reported **exactly the original numbers**
   — `0.824674 kg`, `104920.3553 mm³`, the 40 mm-bore geometry, unchanged to
   the last decimal digit. The window screenshot taken after the run confirms
   this by eye: the part on screen is still the 40 mm-bore plate; there is no
   second body, no visible change, and the tree shows nothing under
   `Corps principal` beyond what was already there.

**Two real product defects here, neither fixed in this session, both worth
their own investigation:**

- **A dialog that `catia_dialog_action` reports dismissing can still block the
  next `catia_run_command`.** Either the click is not reaching the real
  dialog, or a duplicate is stacked behind it and the tool has no way to know.
  This makes the documented recovery path unreliable exactly when it matters
  most — a session already in trouble.
- **Reopening an already-consumed sketch and padding it again is accepted
  silently, with no effect other than a fabricated-looking new feature name.**
  `catia_sketch_create` should refuse (or version) a name collision the way
  `catia_new_part` already refuses to abandon an owned document; and a Pad
  built from a sketch already backing an existing feature is exactly the kind
  of state the G1 report's "same sketch built three times" guard was aimed at,
  but does not catch here because the second pad is not textually identical to
  the first — it is a superset (old geometry plus new) with the new material
  entirely swallowed by the old, larger hole it sits inside. The result is a
  tool call that reports `ok` and a plausible, well-formed, internally
  consistent payload — bounding box, mass, feature name — for an operation
  that changed nothing on the actual document.

**A caveat about the screenshot tool itself, discovered by this contradiction.**
`scripts/shot.ps1` captures via `PrintWindow` against CNEXT's *main* window
handle. A modal Win32 dialog is very often a separate top-level window, owned
by but distinct from the main frame, and `PrintWindow` on the main frame alone
will not render it. Run 4's "the window looks fine, so nothing is stuck" read
was consequently wrong — it proved the main viewport was fine, not that no
dialog existed. The tool's own docstring already warns that the *window*
screenshot exists because the viewport render "cannot show" a modal dialog;
this run shows the window screenshot has the same blind spot for a dialog that
is not a child of the captured handle. Worth a `-FullScreen` capture as routine
practice whenever a tool result mentions a dialog, not just when the main
window looks wrong.

CATIA and the bridge were left in this contaminated state (a redundant
`Extrusion.2`, extra sketch geometry, and possibly still a stuck dialog) at
the end of the run; CATIA was closed outright and restarted clean before
further one-by-one testing continued, rather than trying to salvage it.

## Run 6 — a fresh conversation, `catia_set_parameter` on a pad length, not a sketch

New conversation, new prompt, deliberately avoiding sketch-editing entirely:
*"Open CATIA with a new part, build an aluminium plate 80 mm square and 10 mm
thick, then use catia_set_parameter to change the thickness from 10 mm to
18 mm. Tell me the mass before and after."*

CATIA had just been closed (the Run 5 cleanup). The model **skipped
`open_in_catia` again** and called `catia_new_part` directly — the identical
pattern from Run 1, correctly refused three times in a row (`catia_new_part`,
`catia_open_document`, `catia_new_part` again) with the named reason each
time. Not a new finding; the same known weak-model tendency recurring, and the
validation held again. A follow-up telling it explicitly to call
`open_in_catia` first continued in the same conversation — see Run 7.

## Run 7 — the retry, and the root cause of Runs 4/5's daemon crashes

14 tool calls, 233.1 s. `open_in_catia` created a fresh `Part1`; the model
then called `catia_new_part("Aluminium Plate")` anyway — the same
orphaned-document pattern as Run 2 — abandoning `Part1` before it was ever
used. The *second* `catia_sketch_create` after that (against the new
document) **crashed the daemon outright**, and this time the daemon's own
stderr caught the exact traceback rather than going silent:

    File ".../catia_bridge/com/sketcher.py", line 106, in sketch_create
        self._require_closed()
    File ".../catia_bridge/com/sketcher.py", line 98, in _require_closed
        f"The sketch {state[0].Name!r} is still open. Call catia_sketch_close "
    File ".../win32com/client/dynamic.py", line 620, in __getattr__
        ret = self._oleobj_.Invoke(retEntry.dispid, 0, invoke_type, 1)
    pywintypes.com_error: (-2147023174, 'Le serveur RPC n'est pas disponible.', None, None)

**Root cause, precisely.** `ComContext._sketch_edition` is a Python-side
reference the daemon keeps to whatever sketch was last opened via
`catia_sketch_create`, so a later call can refuse a 3D operation while a
sketch is still being edited. It is **never cleared when the document it
belongs to is abandoned** — here, `Part1`'s sketch state survived the switch
to `Part2` ("Aluminium Plate"). When the next `catia_sketch_create` correctly
found `_sketch_edition` still set and tried to name the offending sketch in
its error message, reading `.Name` off that stale COM object raised a raw
`pywintypes.com_error` ("the RPC server is not available" — the object's
owning document no longer exists on the CATIA side) instead of the intended
clean message.

**Correction, checked rather than assumed: this did NOT crash the daemon.**
The first version of this section, written right after seeing the traceback,
said it did. It does not survive checking the process list: both daemon
processes from this run (PIDs unchanged since launch) were still alive and
responsive afterward, and the log itself shows exactly why — the exception
was caught at the session-dispatch layer and returned to the model as an
ordinary tool error (`"CATIA refused catia_sketch_create: com_error while
running catia_sketch_create: ..."`), logged at `ERROR` with a full traceback,
and the daemon went straight back to handling calls normally (`catia_open_document`,
`catia_set_material`, `catia_list_features`, `catia_switch_workbench` all
`ok` immediately after). So the bug is real and precisely as diagnosed above
— a stale reference producing a wrong, unfriendly error message — but it is
milder than first reported: one confusing error, not a crash.

**What actually produced this run's "bridge exited immediately after
starting" symptom was a separate call**, later in the same run:
`catia_run_command("Rectangle")` timed out after CATIA's usual 30 s
("did not answer... most often a modal dialog waiting for a click") —
the same failure class as Run 4's `"Edit Sketch"` timeout, not the sketch-
reference bug above. The very next call, `catia_measure`, got the "bridge
exited immediately" error; by the time this report was being written, minutes
later, the same daemon processes had reconnected on their own with no
manual intervention (the daemon already retries its websocket connection on
failure, logged elsewhere in this session as `"Reconnecting in 0.8s"`). That
casts real doubt on whether Run 4's manual restart was actually *necessary*,
as opposed to just faster than waiting — this session did not test waiting it
out there, and should have before reaching for the kill switch.

**Two distinct, real defects, neither fixed in this session:**

- **The stale `_sketch_edition` reference** (`sketcher.py:98`), confirmed by
  a precise traceback: needs clearing whenever a document changes underneath
  it, and the `.Name` read (or any property read off a possibly-stale COM
  reference) needs a guard against `pywintypes.com_error` rather than
  surfacing the raw exception text to the model.
- **`catia_run_command` against certain interactive commands blocks for the
  full 30 s and appears to disrupt the daemon's connection to the server
  when it does** — seen three times now (`"Edit Sketch"`, `"Hole"` by
  implication in Run 5, `"Rectangle"`), always against a command that
  presumably wants keyboard or mouse input the automation cannot supply. This
  is the more disruptive of the two, since every one of these seven runs that
  reached for `catia_run_command` on a geometry-editing command hit it, and
  it is what actually costs a session its bridge connection, however
  temporarily.

Both are Windows-only code (`# pragma: no cover`) with their own interactive
test harness (`mock_ui.py`, both `en`/`de`) and belong to a dedicated
investigation, not a fix attempted mid-session. Recorded here with exact
files, lines and reproduction steps so neither has to be rediscovered.

## Run 8 — STEP export test, the model skips `open_in_catia` a third time

New prompt, new conversation: "Open CATIA with a new part. Build a titanium
bracket: a 60 mm by 40 mm by 8 mm plate with two 6 mm clearance holes, 10 mm
in from each short edge on the centreline. Then export it to STEP and confirm
the file was written." — rung 2 territory (several features that must agree)
plus the STEP-export path, not yet tested this session.

The model's first tool call was `catia_new_part({"name": "Titanium
bracket"})`, skipping `open_in_catia` even though it is the prompt's literal
first sentence. Refused cleanly (`"CATIA is not running on this
workstation..."`). The model gave up and told the user to start CATIA
manually rather than calling `open_in_catia` itself. **This is the third
confirmed occurrence of the same pattern** (Runs 1 and 6 also skipped it) —
consistent enough now to call a real weakness of this model at this tool
count, not a fluke, and worth a system-prompt or tool-description change
rather than another retry.

## Run 9 — retried in the same conversation, and a third distinct bridge defect

Continuing the same conversation, told explicitly: "Call the open_in_catia
tool now, then build the bracket." This time it complied — and surfaced a
defect neither of Runs 4–7's two findings explains.

Exact sequence, from the conversation's own tool-call log
(`GET /ai/conversations/{id}`, not the model's paraphrase):

    [ok ] open_in_catia {"new_part": true}
      -> created_document: "Part1.CATPart", bridge_connected: false
    [ERR] catia_sketch_create {"support": "XY", "name": "BracketSketch"}
      -> "No CATIA document is bound to this conversation..."
    [ERR] catia_open_document {}
      -> "This conversation has no CATIA document yet..."
    [ok ] catia_new_part {"name": "TitaniumBracket"}
      -> document_id: 9d4d08a3-..., remote_path: .../TitaniumBracket.CATPart
    [ERR] catia_sketch_create {"support": "XY", "name": "BracketSketch"}
      -> "CATIA refused catia_sketch_create: com_error while running
          catia_sketch_create: (-2147023174, 'Le serveur RPC n'est pas
          disponible.', None, None)"
    [ok ] design_history {}
    [ok ] catia_list_commands {"search": "Sketch"}
    [ok ] catia_set_material {"material": "titanium-ti6al4v"}

The first two refusals are correct behaviour, not a bug: `open_in_catia`'s
`new_part: true` creates `Part1.CATPart` through the **server's own** direct
COM client (`app/catia/bridge.py::new_part`), which has nothing to do with
this conversation's `CatiaDocument` binding — that binding is only created by
the *tool* `catia_new_part`, dispatched to the daemon
(`app/catia/dispatch.py::call_catia`, `_UNSCOPED_TOOLS`: "creates the binding;
there is nothing to return to"). So the model correctly had to call
`catia_new_part` itself to get a bound document, and did.

The `com_error` on the very next call is not correct behaviour, and it is
**not** the `_sketch_edition` staleness bug from Run 7: that bug requires a
sketch left open on a document the model has since abandoned, and
`TitaniumBracket` was brand new with zero prior features — there is nothing
for `_sketch_edition` to be stale *about*. This is a different failure with
the same visible symptom.

**Root cause, code-grounded but not yet reproduced under controlled
conditions.** `open_in_catia` and every `catia_*` modelling tool drive the
same single CATIA.exe instance through **two independent, unsynchronised COM
sessions in two different OS processes**:

- The FastAPI server's own client (`app/catia/bridge.py`), used only by
  `open_in_catia` and `sync_geometry_from_catia`. Its own docstring says each
  call "gets a fresh thread that calls `CoInitialize`, creates its own CATIA
  reference, finishes with it, and calls `CoUninitialize`" — a full COM
  apartment stood up and torn down per call — and `_CATIA_LOCK`
  (`threading.Lock`) is scoped to serialise only *this process's* calls
  ("so two requests cannot drive the GUI at once"), which a `threading.Lock`
  cannot do across a process boundary in any case.
- The paired daemon (`scripts/catia_bridge/`), a separate long-lived OS
  process holding its own persistent COM connection, which is what every
  `catia_*` tool actually calls through the websocket bridge.

In this run, `open_in_catia`'s direct-COM call created and (per its own
documented lifecycle) tore down a COM apartment on `Part1.CATPart` moments
before the model's next call reached the daemon and created a second
document, `TitaniumBracket.CATPart`, over the daemon's own separate,
already-open COM session. The daemon's very next call —
`catia_sketch_create`, the first real modelling call on the new document —
is the one that got "RPC server not available." Two independent COM clients
touching one single-instance, single-threaded-apartment CATIA process within
the same second, one of them mid-teardown, is exactly the shape of thing that
produces a transient RPC failure on the other. This is offered as the
best-evidenced explanation, not a confirmed one: nothing here instrumented
exact call timestamps on both sides, and the hypothesis has not been
confirmed by reproducing the failure with `open_in_catia(new_part: false)`
(which never touches the server's direct-COM `new_part()` and so would not
create the second, contending document).

**A third distinct, unfixed defect for whoever takes this on**: `open_in_catia`'s
`new_part: true` path should not create a document through a second,
unsynchronised COM client when a daemon is already paired and about to be
asked to create its own — either route it through the daemon when one is
connected, or gate both clients behind a single cross-process lock (a named
Win32 mutex, or a lock file the daemon and the server both hold) rather than
the process-local `threading.Lock` that exists today. This is additive to,
not a restatement of, the two Run 4–7 findings: the stale `_sketch_edition`
reference needs clearing on a document switch, `catia_run_command` needs its
own bounded timeout on interactive commands, and now — a third, separate
mechanism — the two COM clients that can both legally touch CATIA at once
need to not do so within the same instant.

The model gave up gracefully after the `com_error` (same pattern as every
other run that hit an infrastructure wall: no fabricated success, a plain
statement of what worked and what didn't), so no picture was taken — nothing
built. `catia_set_material` still succeeded despite the failed sketch,
confirming (again) that a material can be applied to a document with no
solid in it yet.

## Rung reached

Runs 1–3 confirm, for the first time, that the exact plan the OCCT backend
builds correctly also builds correctly on the **real** CATIA seat: same shape,
matching mass to the closed form, correct French feature/menu names, right way
up. That is new information G1 above explicitly could not provide (it ran
OCCT-only). Runs 4–7 are not a ladder result — they are a chain of
infrastructure findings that only came apart by continuing the same failure
one prompt at a time instead of resetting after the first sign of trouble:
a stale COM reference that survives a document switch and surfaces as a
confusing raw error instead of the intended clean one, pinned to a file and
line; a separate and more disruptive pattern where `catia_run_command`
against an interactive geometry command blocks for its full 30 s timeout and
appears to cost the daemon its connection to the server, transiently, without
(on the evidence gathered here) actually requiring the manual restart this
session reached for each time; a dialog-dismissal path that reports success
without confirming it; and a modelling-quality finding (a tool named
explicitly in the prompt never attempted, with a fallback that fabricated a
no-op feature instead). None of this would have been visible from a batched
multi-prompt run, from `catia_measure`'s own reported numbers alone, or
without going back to correct an early conclusion once later evidence
disagreed with it — the daemon "crash" in Run 7 was the most confident wrong
statement of the whole session, and it did not survive checking the process
list. That is the whole argument for doing this one prompt at a time and
looking at the real window and the real process table, not just the tool
result or the model's own paraphrase of it, after each one.

Runs 8–9, testing STEP export (rung 2, untested until now), never reached the
export call: Run 8 repeats the "skips `open_in_catia`" weakness for a third
time (Runs 1, 6, 8), and Run 9 — the same prompt, told explicitly to call
`open_in_catia` first — hit a **third** distinct bridge defect, on a document
with no prior state to be stale about, so it cannot be explained by either
Run 4–7 finding. The best-evidenced explanation is architectural rather than
a one-line bug: `open_in_catia` drives CATIA through the server's own direct
COM client while every `catia_*` tool drives it through the daemon's
separate, persistent one, and nothing synchronises the two across the process
boundary. Three sessions in, the pattern across all of Runs 4, 5, 7 and 9 is
the same shape: CATIA is a single-instance, single-apartment COM server, and
every defect found so far is some version of two things touching it, or one
thing touching it while a previous reference to it is no longer valid.
STEP export itself remains untested; it is next once whichever of these three
gets picked up, or after confirming (by trying `open_in_catia(new_part:
false)`, which never invokes the server's own direct-COM client) that Run 9's
failure really does need both clients active to reproduce.
