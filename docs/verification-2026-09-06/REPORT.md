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
daemon's stderr), this is a real defect, not a model mistake.** The manually-run
daemon logged every call cleanly up to the `catia_checkpoint` that preceded
`catia_run_command("Edit Sketch")`, then produced **no further output at all**
— not even a failure — while the process itself stayed alive (confirmed by
process list). `catia_run_command` calling an interactive command
(`"Edit Sketch"`, not one of the published `COMMAND_IDS`) on a sketch that a
Pad already consumed appears to have wedged the daemon's COM-calling thread
indefinitely, even though **CATIA's own window was not actually stuck** — a
screenshot taken afterward showed a perfectly normal, responsive part view with
no dialog open. With the daemon's worker thread hung, the server's
`app.catia.local_bridge` auto-spawn-on-demand logic then tried repeatedly to
launch a *second* daemon, which correctly refused (`bridge.lock` still held by
the wedged one) and exited immediately — which is what the model's tool errors
were reporting as "the bridge is not connected."

**Recovery required a manual bridge restart** (kill the wedged process, clear
the stale lock, relaunch) — the interactive-dialog recovery tools
(`catia_describe_dialog`/`catia_dialog_action`) were never reached because
nothing in the model's tool sequence tried them, and in any case the CATIA GUI
itself showed no dialog to dismiss; the problem was inside the daemon's own
blocked call, not a visible modal.

**Not fixed in this session** — this is a genuine COM-threading issue in
`scripts/catia_bridge/catia_com.py` / `session.py` worth its own investigation,
not a one-line patch. Recorded here so it is not silently reproduced and
mis-attributed to "CATIA not connected" again. The immediate, low-risk mitigation
worth considering: `catia_run_command` should have its own hard timeout that
kills and restarts the underlying COM connection rather than leaving the
daemon's worker thread blocked forever.

## Run 5 — retried after a clean bridge restart

Prompt: *"The bridge should be reconnected now. Please change the bore diameter
to 25 mm using catia_set_parameter on the existing part, and tell me the new
mass."*

See the outcome appended below once the run completed.

## Rung reached

Runs 1–3 confirm, for the first time, that the exact plan the OCCT backend
builds correctly also builds correctly on the **real** CATIA seat: same shape,
matching mass to the closed form, correct French feature/menu names, right way
up. That is new information G1 above explicitly could not provide (it ran
OCCT-only). Run 4 is not a ladder result — it is an infrastructure finding, and
one that would have been invisible to a batched multi-prompt run, which is the
whole argument for doing this one prompt at a time with a look at the real
window after each one.
