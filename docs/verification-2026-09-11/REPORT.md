# Verification run — 2026-09-11

**Backend:** `occt` (the open kernel, in-process — no CATIA seat involved).
**Model:** `qwen3.6:27b`, local Ollama, 60%/40% CPU/GPU, `num_ctx=32768`.
**Method:** `docs/GUI_PROMPT_LADDER.md` — one prompt per level, written on the day, driven
through the real web GUI in Edge on `localhost:3000`, a picture every time.

---

## The baseline this run started from

Everything green on this machine before a single prompt was typed, so anything below is new:

| gate | result |
|---|---|
| `pytest` | **8075 passed / 0 failed / 7 skipped / 1 xpassed**, 8 m 53 s, local PostgreSQL |
| `ruff check app/ tests/` | All checks passed |
| `mypy app/` | Success: no issues found in 406 source files |
| `app.verify.recorded --check` | Recorded validation run is current |
| `scripts.plan_progress` | 18/34 phases · 123.5/188 tasks = 65.7% |
| `alembic check` | No new upgrade operations detected (head `ef4d9b93d3ca`) |

---

## The model, and a defect that only a new model could expose

`qwen3.6:27b` was pulled for this run: 17 GB, of which a **0.87 GB vision projector** — it is
multimodal, which matters below. Tool calling was probed the way the technology-register rule
demands, against the payload the product sends rather than a one-tool toy: **correct structured
`tool_calls` at 2 tools (13.4 s) and at 38 tools (31.7 s)**, `content` empty and the reasoning
in `message.thinking`, which `app/ai/providers/ollama.py` already handles.

### `AI_GPU_LAYERS=all` is a per-model value, and on this model it is 15.8× slower

`.env.local` carried `AI_GPU_LAYERS=all`, **measured and correct for `qwen3.5:9b`** (34 layers,
fits the card, 100% GPU, 58 tok/s — the sweep is in `_gpu_layers`'s docstring). The 27b has
15.7 GB of weights against a 7.9 GiB card, so it cannot fit. Asking for every layer makes
llama.cpp log `CUDA0 model buffer size = 15364.44 MiB` on an 8 GB card; **Windows WDDM does not
refuse that, it pages GPU memory over PCIe.**

Swept at `num_ctx=32768`, `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0`:

| `num_gpu` | `ollama ps` | generation |
|---|---|---|
| unset (Ollama chooses) | 78%/22% CPU/GPU | 7.73 tok/s |
| 14 | 76%/24% | 6.21 tok/s |
| 20 | 68%/32% | 6.79 tok/s |
| **26** | **60%/40%** | **8.38 tok/s** ← taken |
| 999 (what `all` sends) | **`100% GPU`** | **0.53 tok/s** |

**The trap is the last row.** The slowest configuration of the five is the one `ollama ps`
reports as `100% GPU`, and while it thrashed the run did not appear in `ollama ps` at all.
`CLAUDE.md` told every session to confirm GPU residency with exactly that command, so the check
it prescribed returns a **false pass** in the one case it exists to catch. Corrected in
`CLAUDE.md` to key on the server log's `CUDA0 model buffer size` against `nvidia-smi`'s total,
with the per-model rule for `AI_GPU_LAYERS` beside it; the full sweep is recorded as comments in
`.env.local`.

Second, smaller finding: an **empty** environment variable does **not** override `.env.local`
(`$env:AI_GPU_LAYERS=""` still resolved to `'all'`); a non-empty one does. The env-var override
trick works for setting a knob, never for unsetting one.

---

## L1 — PASS

> "Make an aluminium truncated cone 60 mm tall, 80 mm diameter at the base and 40 mm diameter at
> the top. Tell me its volume and its mass."

Deliberately a **revolved** shape: every prior L1 has been a pad, a tube or a hexagonal prism,
and the ladder forbids re-using the last run's shape.

| quantity | Kryova | my arithmetic |
|---|---|---|
| volume | 175,929 mm³ | `(πh/3)(R²+Rr+r²)` = 56000π = **175,929.1886 mm³** |
| mass | 0.4750088092227767 kg | 175,929.1886e-9 × 2700 = **0.4750088 kg** |

Exact. The density is **named** (Al 6061-T6, 2700 kg/m³) rather than assumed, and the route is
right: a trapezoidal profile revolved with `catia_shaft`, not a stack of discs.

**12 steps, 848 s (14.1 min).** Two refusals, both clean and both recovered from in one step:

- `catia_sketch_axis` — *"arguments.start is required; you sent: sketch. Accepted: end (array of
  number, required), sketch (string, optional), start (array of number, required)"*. The refusal
  names the missing argument and lists what is accepted; the next call succeeded.
- `check_part` — *"Claim 4 is not usable: 'bound'"*. Refused a malformed claim rather than
  guessing a bound; the retry passed.

Picture: `L1-front.png` (the Cut-Y section) shows a hatched trapezoid whose base:top width
measures **1.95** against the 2.0 the request implies, and `L1-answer.png` the answer table.
Also `L1-section.png`.

Verdict: **PASS.**

---

## L2 — FAIL — Kryova (two defects, both fixed; level re-run)

> "Make a mild steel spacer block 90 mm long, 60 mm wide and 20 mm thick. Cut a circular pocket
> into the centre of its top face, with a diameter equal to **half the block's width** and a depth
> equal to **half the block's thickness**. Then drill four 9 mm holes right through the block, one
> near each corner, each set in from both of its nearest edges by **the pocket's depth**. Tell me
> the finished mass."

Three relationships to resolve rather than transcribe: Ø30, 10 deep, 10 inset.

**The part came out exactly right.** Volume 95,842 mm³ against my
`108000 − π·15²·10 − 4·π·4.5²·20` = **95,842.0364**; mass 0.754 kg against **0.754277 kg**; all
three relationships correct; material named. The top view (`L2-fail-top.png`) measures 5.27 and
5.23 px/mm on the two axes, a Ø30 central pocket and four Ø9 holes at 10 mm inset.

**And it is still a `FAIL — Kryova`**, because the ladder's verdict is about what our code did
during the level, not only about the number at the end. Two Kryova defects fired, and the second
confirmed the false belief the first created. The model spent six steps rebuilding a block it
had already built.

### Defect 1 — the repeat guard punished the correct recovery

`app/ai/agent.py::_refused_before`. From the operation log (`CatiaOperation`, the record —
the transcript is not):

```
2. catia_sketch_create   ok=True   {"support": "XY"}            -> sketch "sketch", profiles 0
3. catia_pad             ok=False  {"sketch":"sketch","length_mm":20}
     "Sketch 'sketch' has no closed profile ... Draw a rectangle, circle or polygon on it first."
4. catia_sketch_rectangle ok=True  {"sketch":"sketch","width_mm":90,"height_mm":60}
                                                                -> profiles: 1
   catia_pad             BLOCKED, 0 ms, no CatiaOperation row
     "... This is the second time catia_pad has been called with these exact arguments ..."
```

Between the refusal and the retry the model did **exactly what the refusal told it to do**. The
pad that followed would have built the block. The guard turned it back unsent on the stated
grounds that *"the call did not run, so nothing has changed"* — a premise that was assumed and is
false whenever another call has landed in between.

**Fixed** by making "nothing has changed" a measurement: a refusal is remembered with the
**mutation clock** it was refused at, and only stands while that clock has not moved. A
successful mutating call moves it; a read cannot. The S1 case the guard was written for
(2026-09-06, `catia_new_part` resent byte-for-byte nine steps later) is still caught — the repeat
is dispatched once, refused again by the real tool for the real reason, and re-armed at the new
clock, so a third verbatim send is blocked. The price of not blocking a legitimate retry is one
dispatched call, and that is the right way round.

Pinned by `tests/test_agent.py::TestARefusedWriteIsNotRepeated` — three new tests, including one
driven through the real `stream_agent` loop, because the whole defect was that the guard reasoned
correctly about what it was told and was told the wrong thing. **Verified by breaking it**:
neutering the clock comparison fails both new tests and leaves the six pre-existing ones green.

### Defect 2 — the same `list_features` gap, one backend over

`app/kernel/occt/operations/document_ops.py::list_features`. Step 5 of the same run:

```
5. catia_list_features   ok=True   {"include_sketches": true}   -> {"detail": [], "features": []}
```

Sketch `'sketch'` existed and held a closed profile. The tool's own summary calls itself *"the
first call to make on any document you did not just build yourself"* and promises "the features,
**sketches** and bodies"; `include_sketches` is documented as defaulting to true. The OCCT handler
read **none** of `body`, `kind` or `include_sketches` and returned solid features only. The model
read the empty answer correctly as *"the part is empty - the sketch and rectangle weren't
saved"*, threw the sketch away and started again.

**This exact defect was found and closed on the CATIA side on 2026-09-06** (ladder prompt H2,
`tests/test_catia_list_features_options.py`, whose docstring describes the same failure almost
word for word). The open kernel was never given the same treatment. That is the divergence class
`CLAUDE.md` records from the other direction on 2026-09-09, when `catia_sketch_create` was the
last operation holding a private accept-list and the seat accepted a `support="top"` the open
kernel refused: **a capability closed on one backend and left open on the other is a product that
behaves differently depending on a setting nobody in the conversation can see.**

**Fixed**: all three options now work on the open kernel, in the shape the mock already uses —
sketches in the listing with `type: "Sketch"`, an `elements` count, and `profiles` /
`can_be_built_from`, which is the field that answers *"why will this not pad?"* before the pad is
refused a second time. `kind` filters case-insensitively and names what types are present when it
matches nothing; an unknown `body` is refused naming the ones that exist, rather than silently
answering about a different body.

Pinned by `tests/test_kernel_list_features_options.py` — 12 tests, offline, deliberately mirroring
the CATIA-side file so both backends are held to one contract. **Verified by breaking it**:
reverting the handler fails three of them.

### What the run got right, and it is most of it

- Every refusal it *did* earn was informative and recovered from in one step: `catia_pocket`
  refused with *"removed no material: the part is exactly the volume it was (108000.000 mm³) ...
  the cut fell in the air"*, and the model correctly worked out it needed `reversed: true`.
- It noticed its own first hole was 10 mm deep in a 20 mm block and went and fixed it.
- Intermediate volume after pocket + one hole read 99,659 mm³ against my 99,659.07.

`L2-fail-answer.png`, `L2-fail-top.png`, `L2-fail-section.png`.

Verdict: **FAIL — Kryova.** Both defects fixed; level re-run below.

---

## L2 (re-run, after both fixes) — PASS

Same prompt, verbatim, from a clean project, so the comparison is exact. The only thing that
changed between the two runs is the two fixes above.

| | first run (before) | re-run (after) |
|---|---|---|
| steps | 25+ | **16** |
| `catia_pad` refusals | 2 (one of them ours) | **0 — built first try** |
| document rebuilt from scratch | yes | **no** |
| `list_features` answered | `{"features": [], "detail": []}` | not needed — nothing went wrong |
| mass | 0.754 kg | **0.7542768268943137 kg** |

Against my arithmetic: **0.754277 kg** and **95,842.0364 mm³**. Exact.

It named its first sketch `Base profile`, drew the rectangle, and padded it in one go. The only
refusal in the whole run was the genuine one — `catia_pocket` *"removed no material: the part is
exactly the volume it was (108000.000 mm³) ... the cut fell in the air"* — which it read
correctly and fixed with `reversed: true` on the next call. That is the system working: a
measured refusal that names the evidence, recovered from in one step.

The answer names its density (`ρ = 7870 kg/m³`) rather than assuming one, and states each
relationship alongside the number it resolved to.

`L2-pass-answer.png`, `L2-pass-top.png`, `L2-pass-section.png`.

Verdict: **PASS.**

---

## What this run says about the method

Both defects were invisible from below and neither is *in* a tool.

- Every tool call in the first L2 returned exactly what it should have. `catia_sketch_rectangle`
  really did add the profile. `catia_pad` really did refuse an empty sketch. `catia_list_features`
  really did list every solid feature. The suite was green on all of it — 8075 tests — and the
  product still could not build a block without rebuilding it.
- The failure lived in the **space between** the calls: a guard that reasoned from an assumption
  about the world, and a listing whose contract was honoured on one backend and not the other.
  That is the third and fourth instance of the class `CLAUDE.md`'s testing rule 8 describes, and
  the ladder is the only thing that has ever found one.
- **The compounding is the lesson.** Defect 1 induced a false belief; defect 2 corroborated it.
  Either alone, the model would probably have recovered — it is visibly a competent recoverer
  elsewhere in the same run. Together they were conclusive, and the model did the rational thing
  with the evidence it was given.

