# Gate G1 — second run, 2026-09-08

**Verdict: DID NOT PASS.** One blocking defect was found, fixed and verified in this
session; a second is found, characterised and left open. Rung 3 is reached and its
mechanism is proven; the load-bearing prompt is not.

G1 first ran 2026-09-06 and did not pass (rung 3: the agent had no way to change a
dimension on the open kernel). That cause was fixed afterwards — `catia_set_parameter`,
E5 task 5, commit `b9b1cb9` — and this is the first re-run since.

| | |
|---|---|
| Backend HEAD | `a387243` |
| Frontend HEAD | `e90a9aa` |
| Model | `qwen3.5:9b`, 81% GPU / 19% CPU, `num_ctx` 32768, 6.8 GB resident |
| GPU | RTX 5070 Laptop, 8151 MiB |
| CATIA | V5-R33, French, `CNEXT` live, bridge auto-started by the backend (pid 20136) |
| CalculiX | 2.23 at `C:\tools\calculix\bin\ccx.exe`, on both the Windows and Git Bash PATH |
| BM25 index | rebuilt this session, 4,920 passages / 8,107 terms / 21 docs (4 scans skipped) |
| Postgres | local 18.6, started from `~/pgdata`; `alembic check` reports no drift |
| Driven through | `/api/v1/ai/chat` in the web GUI over CDP — never the dispatcher |

---

## Pre-flight

All five checks green before any prompt was sent.

1. **Ollama on the GPU** — `ollama ps` showed `19%/81% CPU/GPU` at `num_ctx=32768`,
   `nvidia-smi` 6536 MiB resident. Matches the figure CLAUDE.md records for this model.
2. **CalculiX** — `ccx -version` → `2.23`, present on the Windows PATH the server inherits.
3. **BM25 index** — `--check` said up to date and the `builder` fingerprint matched, but the
   index mtime (12:26) predated a commit touching `app/retrieval/corpus.py` (17:03), so it was
   rebuilt rather than trusted. The rebuild produced *identical* counts, confirming `--check`
   was right and the mtime was misleading. 8.6 s.
4. **Admin account** — `scripts/create_admin.py` re-run; `admin@admin.com` already held
   `platform_admin`.
5. **Backends** — stated per prompt below.

`AI_TOOL_LIMIT=25` is set in `.env.local`. This is deliberate, not a misconfiguration:
`app/ai/tool_retrieval.py` (master plan 16.1) exists precisely because a 9B model does not
survive a 40-tool payload, and it force-includes `catia_set_parameter` among the
prompt-taught tools. The gate therefore also exercises 16.1.

---

## Prompt 1 — S1, closed correction loop (`GEOMETRY_BACKEND=occt`)

**Backend choice.** Rung 3 is open-kernel work: E5 task 5 measured the original failure there
and `b9b1cb9` fixed it there, the loop needs tens of rebuilds a minute (Decision 1), and it
gives free verification of E4.4.

> Make a steel counterweight that is a solid rectangular block, 200 mm long, and get it to
> 2.4 kg by adjusting only its width and height, keeping them equal. Measure it and tell me
> what you ended up with.

**Rung reached: 3, on the second turn. Scored `~` on the prompt as written.**

Turn 1 — six operations, ~100 s, answer correct:

| | |
|---|---|
| Measured mass | **2.4001965 kg** (0.008% from target; the ladder allows 1%) |
| Width/height | **39.05 mm** (the ladder expects ≈39.1) |
| Volume | 304,980.5 mm³, bounding box 39.05 × 39.05 × 200 |

But the `CatiaOperation` log — the authoritative record — shows the agent went **straight to
39.05 mm on the first rectangle**. No `catia_set_parameter`, no `catia_measure`, no iteration:
it solved w = √(2.4 / (7870 × 0.2)) = 39.050 mm analytically and built it once. The ladder's
pass condition requires that the agent *measured rather than computed on paper*, and the loop
in `correct.py` — which is what S1 exists to make run — did not run. Hence `~`.

One refusal on the way, and it is a pass of the thing it tests: `catia_sketch_rectangle` was
called with an invented `name` field and came back as a named refusal listing every accepted
argument. The agent recovered on the next call.

**Turn 2 was added to test `catia_set_parameter` directly**, because that is what `b9b1cb9`
added and what this gate exists to check. Asked to bring the same block to 3.2 kg by changing
the part rather than rebuilding, the loop ran properly:

```
catia_set_parameter  name='Width'                  -> REFUSED, and the refusal names the real
                                                      parameters: set_material\density_kg_m3,
                                                      Base profile\width_mm,
                                                      Base profile\height_mm, Pad.1\length_mm
catia_list_parameters                              -> 4 parameters, with the replay note
catia_set_parameter  Base profile\width_mm  = 45.1 -> 2.77205797 kg  (previous_value 39.05)
catia_set_parameter  Base profile\height_mm = 45.1 -> 3.20153174 kg  (previous_value 39.05)
catia_measure                                      -> 3.20153174 kg, box 45.1 × 45.1 × 200
catia_list_features                                -> Pad.1
```

Final mass 3.2015 kg against a 3.2 kg target — 0.05%. The agent's own reasoning between the
two calls (*"the part got updated but the height stayed at 39.05 mm. I need to update both
dimensions equally"*) is the correction loop working. And the journal replay kept the feature
named `Pad.1`, which is the property the master plan claims for it.

**E5 task 5 / `b9b1cb9` is verified end to end on the real machine.**

Pictures: `S1-01-part-so-far.png`, `S1-02-section-cut-x.png`, `S1-03-set-parameter.png`.

---

## Step 4 — E4.4 and the status chip, verified on the real machine

E4.4 shipped with 323 unit tests and no real-machine evidence. It works.

- **On `occt`** the "The part so far" panel renders above the composer, headed
  `The part so far · Steel counterweight · OCCT 7.9.3.1`, with Front/Back/Left/Right/Top/
  Bottom/Iso/Iso (rear) and Cut X/Y/Z. The iso view draws a tall slender prism — the right
  shape for 200 × 39.05 × 39.05, about 5:1 — in hidden-line wireframe, captioned
  "Iso view of the part".
- **A section cut works.** Cut X redraws the block halved with a **hatched cut face** and
  recaptions itself "Iso view, cut through the middle of X".
- **The drawing matches what CATIA would show.** Compare `S1-01` (open kernel, HLR) with
  `S3-02-capture-view.png` (the same shape class drawn by CATIA): same proportions, same
  orientation convention, long axis along Z.
- **On `catia` the panel correctly renders nothing** — asserted in the run, not assumed.

**The status chip tooltip is fixed.** On the open kernel it reads *"Geometry is being built by
the open kernel in this process — no CATIA seat is involved, and none is needed. Set
GEOMETRY_BACKEND=catia to drive a real seat instead."* On the seat it reads *"This workstation
is connected, running V5-R33."* No `undefined` in either.

One thing that looks like a defect and is not: a document-wide `innerText` scan shows the chip
as `CATIA` then `. Geometry is being built…`. That leading `". "` is deliberate — the
`sr-only` span in `catia-chip.tsx:45` composes with the visible label so a screen reader hears
*"CATIA. Geometry is being built…"*. It is correct as written.

---

## Prompt 2 — S3, load-bearing (`GEOMETRY_BACKEND=catia`)

**Backend choice.** The gate requires the seat exercised through the bridge, and
`sync_geometry_from_catia` → `run_simulation` is the path that makes G1's claim — *the system
can say a part carries its load* — actually true of the product.

> Take a 200 x 20 x 10 mm mild steel cantilever, fix one end, hang 200 N off the free end,
> and tell me the tip deflection and the peak von Mises stress. Then tell me whether I should
> trust the number.

**Rung reached: the whole path ran; the answer is wrong. Scored `!` — a defect in Kryova.**

Ten steps, all green, in about four minutes: part → sketch → rectangle → pad (`Extrusion.1`,
the French seat naming intact) → material → **STEP export to Kryova** → load case → queued run
→ result → measure. The mechanism works end to end.

The **result was nonsense**: factor of safety **1303.41**, peak von Mises 0.284 MPa. The
drafted load case was:

```
fixture: clamp,  face axis=x side=min      the 10 × 200 long side face
load:    force,  face axis=x side=max      the opposite long side face, force [0, 0, -200]
```

The meshed geometry is X ∈ [−10, 10], Y ∈ [−5, 5], **Z ∈ [0, 200]** — the beam's long axis is
Z. So Kryova clamped one *long side face* and pushed the opposite long face **along the beam's
own length**, 20 mm apart on a 200 mm part. Nothing in the system flagged it.

**Where the fault is.** `draft_load_case` takes only plain words; the face is chosen by a
second model call from a six-word vocabulary with a hard-coded mapping in
`app/ai/load_case_sketch.py`:

```
top → z,max    bottom → z,min    left → x,min    right → x,max    front → y,min    back → y,max
```

Asked to "fix one end and load the free end", the drafting model said *left* and *right* —
which are always ±X whatever the part's orientation. On a beam lying along Z the end faces
are `bottom` and `top`. The drafting model **was given the bounding box** and still chose X.

That last point makes the immediate error the local model's. But three things around it are
Kryova's and are what turn a bad guess into a silently wrong answer:

1. **The vocabulary cannot express what was asked.** There is no word meaning "the far end"
   or "along the longest axis". The tool's own description promises the opposite — it says the
   draft is built against the part's bounding box "so 'the top face' and 'the far end' mean
   something". `the far end` means nothing; there is no such term. That is a docstring
   claiming a capability the code does not have.
2. **Nothing sanity-checks the drafted case against the geometry.** A clamp and a load on two
   faces 20 mm apart on a 200 mm part, with the force acting along the axis the part is long
   in, is a recognisable nonsense and passed unremarked.
3. **A factor of safety of 1303 was reported as a result**, not as a smell.

**What the product did right, and it is the best thing in this run:** the agent caught it
itself, and said so before being asked — *"the load case fixed the left face at X = −10 mm and
loaded the right face at X = +10 mm, giving a span of only 20 mm, not 200 mm"* — then offered
to re-run. That is Decision 3 working.

### Re-run with the orientation supplied by hand

Given the axis explicitly (clamp Z=0, 200 N in −Y at Z=200), the numbers become defensible:

| | FE | closed form |
|---|---|---|
| Tip deflection | 0.539 mm | 1.561 mm |
| Peak von Mises | 92.3 MPa | 120 MPa |
| Factor of safety | 4.01 | — |

Closed form for this case: I = bh³/12 = 20 × 10³/12 = **1666.7 mm⁴**; δ = PL³/3EI =
1.6×10⁹ / (3 × 205,000 × 1666.7) = **1.561 mm**; σ = Mc/I = 40,000 × 5 / 1666.7 = **120 MPa**.
FE under-predicts both, which is what a 411-element **tet4** mesh does in bending — tet4 locks
and reads stiff. The numbers are the right size and *not converged*.

**The agent then talked itself out of a correct result using its own bad arithmetic.** It
computed I = 166.7 mm⁴ — a factor of ten out — concluded σ_hand = 1200 MPa and δ_hand =
0.0157 mm, declared a "🚨 Major Discrepancy … RED FLAG — the model setup is wrong", and blamed
the clamped face for over-constraining the beam. The clamped face is not the problem; the
arithmetic is, and the mesh is.

**S3's pass condition is not met.** The deflection is not compared against a correct
Euler-Bernoulli answer, and **no convergence study was run or offered** — "an unconverged
number is worse than no number" is the rule, and the answer's confidence is not earned. It is
scored `!` rather than `~` because the load case that started it was Kryova's to get right.

Pictures: `S3-01-catia-window.png` (title `cantilever_beam.CATPart`, French menus, tree
`Part1 / Plan xy / Plan yz / Plan zx / Corps principal / Steel`, no dialog, no greyed command;
the beam visibly runs vertically, which is the load-case bug in a picture),
`S3-02-capture-view.png` (through `catia_capture_view`), `S3-03-gui-answer.png`.

---

## Prompt 3 — the oracle check, `ccx` vs `linear_static`

Run on the **same case the product had just solved** (job `bce84a02`), rebuilding its mesh
from its own geometry version, element size and order, and replaying its stored load case —
not a synthetic case.

### First result: UNMEASURED — a real defect

```
ran    : False
reason : calculix could not run: CalculiX refused the input deck before solving anything.
         *ERROR reading *NODE. Card image:
         *ERROR reading *CLOAD: node 88
         *ERROR in calinput: at least one fatal
agrees : UNMEASURED
```

The honesty discipline held — UNMEASURED, never a pass, and the message correctly attributed
it to the deck rather than the model.

**Cause.** CalculiX reads every numeric field with a Fortran `f20.0`: a token wider than
**20 characters** is truncated mid-number and the line fails to parse, with an empty card image
that names neither the node nor the column. `deck._number` used `repr(float(value))` — the
shortest round-tripping string, which for a full-precision double runs to 22–23 characters. The
nodes ccx named are exactly the ones carrying such tokens:

```
68,  -7.819982591610898e-15, 5.0, 0.0                      <- 22 chars
173, -4.999999999999996, -7.993605777301127e-15, 0.0       <- 22 chars
```

**Why the offline suite never saw it.** Every mesh in `tests/test_solver_calculix.py` comes
from `box_mesh`, whose coordinates are `5.0` and `200.0`. Only geometry that has been through a
real transfer carries the noise: a nominal zero arrives from CATIA→STEP→OCCT as
`-7.993605777301127e-15`. E6 task 1's verification (σ = 25.000000 MPa to 5.7e-16) passed for
the same reason — an exact primitive. **The oracle could not run on any real part, and had not
been asked to.**

**Confirmed by breaking it**, not by reading it — the same deck run twice, differing only in
number width:

| deck | ccx exit | fatal | wrote `.frd` |
|---|---|---|---|
| as Kryova wrote it | 201 | yes | no |
| identical, tokens ≤ 20 chars | **0** | no | **yes, "Job finished"** |

The as-written deck is kept here as `oracle-deck-as-written.inp`.

### Fix

`app/solve/calculix/deck.py` — `_number` keeps `repr` when it fits in 20 characters and
otherwise falls back through decreasing exponent precision (13 significant digits, far finer
than any mesh this writes). Only the values that cannot fit are rewritten, so `5.0` stays
`5.0` and a deck is still readable by a human when a solve goes wrong. The module docstring's
claim about `repr`-grade precision was corrected in the same change.

Four tests in `TestEveryNumericFieldFitsCalculixsReader`, including a whole-deck token sweep
against a mesh perturbed at the 1e-15 scale a STEP import really produces. **Verified by
breaking what they guard**: reverting `_number` to the plain `repr` fails two of the four,
naming the exact 21-character token.

### Second result: agrees

```
ran    : True
agrees : True
  volume_mm3          : 40000    vs 40000    (+0.00%, agrees)
  max_displacement_mm : 0.538697 vs 0.538697 (-0.00%, agrees)
  max_von_mises_mpa   : 92.2848  vs 30.1519  (-67.33%, reported)
  (peak stress is reported, not judged: the field is not uniform, so nodal
   smoothing legitimately reads below the element peak)
```

Displacement — the quantity `oracle.py` says must agree tightly, and the one a wrong load or
restraint moves first — agrees to six significant figures. **The oracle check passes.**

The 67% peak-stress gap is within the oracle's stated rules for a non-uniform field, but it is
large, and it is the same coarse-tet4 story as the S3 numbers. Worth a look when E6 task 3
(C3D10, shells and beams) is done.

---

## Verdict, and what re-opens the stretch

**G1 did not pass.** A gate that half-passed is a gate that did not pass.

| | |
|---|---|
| Rung 3 (S1) | reached; `catia_set_parameter` and the correction loop **verified** |
| E4.4 + status chip | **verified on the real machine**, both backends |
| Load-bearing (S3) | `!` — path works, load case wrong, answer not trustworthy |
| Oracle (`ccx` vs `linear_static`) | **passes**, after fixing a blocker found here |

**Fixed in this session:** the CalculiX 20-character field. Committed with its tests.

**Open, and what G1 waits on:**

1. **Load-case drafting has no way to say "the far end".** `FACE_SELECTORS` is six absolute
   directions; a part whose long axis is not X cannot be described. Needs either a
   part-relative vocabulary (`far end` / `near end` / `long axis`) resolved against the
   bounding box, or the drafting prompt given the box's *proportions* rather than its corners.
2. **Nothing checks a drafted load case for sense.** A clamp and a load on faces 20 mm apart
   on a 200 mm part, with the force along the long axis, should be refused or at minimum
   carried as a loud assumption. This is the `!`: our own validation let a bad call through.
3. **No convergence check exists in the path.** Every stress number in this run came from one
   411-element tet4 mesh and was reported without a second, finer solve to compare against.
   E7 task 1 owns this; until then no stress from this product is converged and it should say
   so in words.

**E6 is itself not complete**, which is worth recording because G1 opens *after* E6: task 3
(C3D10, shells and beams) is NOT STARTED and task 4 is PARTIAL. Three of the numbers above
would likely improve on C3D10. G1 was run against a phase that had not finished.

**Not re-run here:** rungs 4–6, and the rest of the ladder. Rung 3 was the carried-forward
item and is discharged; the load-bearing prompt is the new blocker.
