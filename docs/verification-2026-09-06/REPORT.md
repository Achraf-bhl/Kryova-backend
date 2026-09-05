# Gate G1 — 2026-09-06, Windows seat

**Gate G1 opens after E6, the solver federation.** What becomes true at it: the
system can say a part *carries its load*, not only what shape it is. Its
verification is rung 3 of the ladder carried forward — measure and correct to a
mass target — plus a load-bearing prompt.

**Verdict: G1 does NOT pass. Rung 3 fails.** One defect in the product was found
and fixed; two limitations of the local model are recorded and are not ours. The
load-bearing half was not reached and is not claimed.

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

## Rung reached

**Rung 2 comfortably; rung 3 not passed.** Rung 3 has now failed three times, for
three different reasons, and each attempt has produced a real fix: the parameter
tool that did not exist, the inner profile padded as a boss, and tonight the part
that came apart without saying so. That is the ladder working as intended.

G1 re-runs after the fix, on the model that fits the card.
