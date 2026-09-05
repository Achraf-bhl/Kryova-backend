# Windows verification — 2026-09-05

First execution on the Windows seat of code written on Linux and, in large part,
never run. The first run is the measurement, and it found ten defects that
reading had not — three of them Windows-only, two of them fatal to the
open-kernel product path.

Backend `df42b95` → fixes on `verify/windows-2026-09-05`. Frontend `f49e114`.
Machine: Windows 11, Python 3.14.3, RTX 5070 Laptop (8151 MiB), CATIA V5-6R2023.

---

## 0. Sync

Both repos were already at the required commits and needed no merge.

| Repo | HEAD | Required | Result |
|---|---|---|---|
| Kryova-backend | `df42b95` | ≥ `df42b95` | fast-forwarded 46 commits, clean |
| Kryova-frontend | `f49e114` | ≥ `f49e114` | fast-forwarded 1 commit, clean |

`git rev-list --left-right --count HEAD...origin/main` = `0 0` for both. No other
remote ref is newer; next-newest anywhere is `fix/catia-ownerdrawn-menus`
(2026-09-03).

## 1. Environment

**OCCT installed as an ordinary wheel and imports on Python 3.14.3.** No
installer, nothing configured by hand, as the runbook predicted.
`cadquery-ocp 7.9.3.1.1` plus `vtk 9.6.2`. Worth recording: the runbook says
"Python 3.12+" and 3.14 was untested here; it works.

`scripts/setup.ps1` **failed**, and the failure was a real Windows-only defect —
see D6. Migrations had in fact applied; `alembic current` = `a1b2c3d4e5f6 (head)`.

---

## Tier 1 — offline (no DB, no CATIA, no model)

**468 passed, 1 failed** on the first run.

| Suite | Result |
|---|---|
| `test_solver` `test_mesh` `test_geometry` | 78 passed |
| `test_kernel` `test_interrogation` | 314 passed |
| `test_design_machine_checks` `test_design_sensitivity` | 60 passed |
| `test_render` `test_vision` | 63 passed, **1 failed** (D5) |
| `test_geometry_backends` | 16 passed |

The four analyses reproduce their closed-form answers on this machine, so the
physics is not platform-dependent.

**Looking at a render with my own eyes is where D1 came from.** The runbook says
to do this because no automated check can see an orientation error. It was right,
and the error sitting there was one layer deeper than the one it warns about.

## Tier 2 — database

- `alembic check` — **clean, no drift.**
- Full suite: **3388 passed, 3 failed, 2 skipped, 152 s.**

The three failures were D7 (as one stale-file symptom), D5 and D8.

## Tier 3 — the product path with no CATIA at all

`GEOMETRY_BACKEND=occt`. `GET /catia/status` reported exactly what the runbook
predicts: `backend: "occt"`, `OCCT 7.9.3.1`, `108 / 201`.

Driving the **real chat endpoint** — not the dispatcher the tests drive — found
D2 and D3, either of which alone makes the open-kernel path unusable.

After the fixes, the path works end to end:

```
catia_new_part → catia_sketch_create → catia_sketch_rectangle → catia_pad
```

`GET /kernel/conversations/{id}/measure`:

| Quantity | Measured | Closed form |
|---|---|---|
| `volume_mm3` | **48000.0** | 60×40×20 = 48000 |
| `surface_area_mm2` | **8800.0** | 2(2400+1200+800) = 8800 |
| `centre_of_mass_mm` | [1.2e-16, 2.2e-16, 10.0] | [0, 0, 10] |
| `face_count` / `edge_count` | 6 / 12 | 6 / 12 |

`GET /kernel/conversations/{id}/render` returned HTTP 200 for `iso`, `front`,
`top` and `section=x`, each a distinct image (`occt-*.png` beside this file).
The section is correctly hatched at 45° with the outline drawn over the hatch.

**Not completed:** the four corner fillets. Blocked only by the local model's
argument handling — it sent `edges` as a JSON string, then as a list, against a
schema wanting the bare word `vertical` — not by the kernel. `catia_fillet` is
implemented and its refusal correctly names the accepted values.

---

## Defects found

Ordered by severity. Code-vs-test judgement given for each, as the runbook asks.

### D1 — Every canonical view was rendered from the wrong side. **Code.** Fixed.

`View.direction` points from the eye towards the part. `HLRAlgo_Projector` takes
the opposite sense — the `gp_Ax2` main direction points from the part *towards*
the eye, and that is what decides which side the algorithm stands on. Handing it
the un-negated direction put the eye **behind the part in all eight canonical
views**.

Nothing looked wrong. An orthographic silhouette is unchanged by viewing a part
from behind and mirroring it, so outline, extent, framing and render digest were
byte-for-byte correct — determinism held, the 4.3 diff held, and **34 of the 41
tests in `test_render.py` passed with the defect in place.** Only visible/hidden
was inverted: a pocket you are looking straight into was drawn dashed; one on the
far side of 20 mm of material was drawn solid.

Measured with a blind pocket in each of the six faces in turn. Every one reported
**0 added visible and 8 added hidden** segments in the view that faces it.

Same root cause as the upside-down render fixed earlier on 2026-09-05. That fix
negated `y` in `_flatten`, which corrected the image and left the depth ordering
wrong — the two sign errors cancelled in the 2D coordinates, and only the visible
one had a symptom anybody could see. Both are now one negation in `project`. View
millimetres are unchanged; `to_view_mm` still agrees with HLR.

Pinned by `TestTheRenderIsSeenFromTheSideItNames` — 7 tests, all of which fail
against the old signs.

### D2 — The open-kernel agent could create a part and then do nothing to it. **Code.** Fixed.

The local branch of `dispatch.call_catia` returns before reaching `_post_process`,
which is where the remote path writes the `CatiaDocument` row. So on
`GEOMETRY_BACKEND=occt`, `catia_new_part` built the document, reported success,
and **recorded nothing** — and every document-scoped tool after it was refused
one layer up in `app/ai/tools.py` with "No CATIA document is bound to this
conversation."

`tests/test_geometry_backends.py` could not see this: it calls the dispatcher
directly, and the refusal lives in the agent tool layer. Nothing tested the two
together. The master plan's "measured end to end, a 60×40×20 pad returns 48000
mm³ exactly" was measured through the dispatcher, not through the product.

`device_id` was already nullable, so no migration was needed.

### D3 — Any server restart permanently deadlocked a conversation. **Code.** Fixed.

The binding is a row in Postgres and survives everything. The open kernel's
document is live OCAF state in this process and survives nothing — not a restart,
not a worker recycle, not an LRU eviction.

With D2 fixed, the two combined into a hard deadlock:

- every scoped tool → "No document is open" (from the runner)
- `catia_new_part` → "This conversation already owns…" (from the database)

No way out of that conversation. **A plain `uvicorn` restart was enough to
trigger it**, and it also broke the eviction recovery the runner itself
advertises, which says in as many words to start again with `catia_new_part`.

The guard now asks the kernel, not the row — following the rule
`dispatch._local_document` already states: *on this backend the row is not the
truth.* A *session* is not a document either: `session_for` builds a runner on
demand, so a scoped call that failed for any other reason leaves an empty one
behind, and gating on the session alone re-deadlocks it.

### D4 — Refusals named a tool the open kernel never offers. **Code.** Fixed.

`catia_open_document` reopens a file from disk — meaningless for the open kernel,
and correctly **not** in `backends.local_tool_names()`. But both binding refusals
told the model to call it. Observed live: the model called it, was told no such
tool exists, retried `catia_new_part`, and looped until the step budget ran out.

This is the failure mode CLAUDE.md warns about for the system prompt ("a prompt
describing a tool the model was not given teaches it to hallucinate a call"),
reached through an *error message* instead.

### D5 — Kernel tolerance leaked into a user-facing message. **Code.** Fixed.

A 60 mm plate reported spanning `-1e-07 to 60 mm`. `BRepBndLib` returns that for
a face on x=0 even with the gap zeroed, and it landed in the one message whose
job is to name a range to pick from. The stated bound is now rounded to the
micron; the comparison still uses the exact value.

The test (`match="0 to 60"`) was right and the code was wrong.

### D6 — `setup.ps1` exited 1 on every *successful* run. **Code.** Windows-only. Fixed.

`alembic upgrade head 2>$null` — Windows PowerShell 5.1 wraps a native command's
redirected stderr in ErrorRecords, and `$ErrorActionPreference` set to `Stop`
makes the first one terminating. Alembic logs its progress to stderr, so setup
died *after applying the migrations correctly* and never reached the reference
index or the final instructions.

Second defect in the same file: the dependency step printed `[ok]` even when pip
had failed, because a native non-zero exit does not throw and `--quiet` hides the
error. On the one dependency big enough to fail (`cadquery-ocp`) that would have
surfaced hundreds of lines later as an import error.

### D7 — The bridge tool table could never be regenerated on Windows. **Code.** Windows-only. Fixed.

Two bugs stacked in `scripts/gen_bridge_tools.py::_formatted`:

1. It looked for ruff at the POSIX `venv/bin/ruff`. On Windows it is
   `venv/Scripts/ruff.exe`, so formatting was silently skipped — `--check`
   called the correctly formatted checked-in file **stale on every Windows
   machine** (`test_bridge_table_is_generated` could not pass here at all), and
   running the generator rewrote all ~7,000 lines in raw `repr()` style, exactly
   the diff noise the step exists to prevent.
2. With ruff found, `text=True` encoded the pipe with the **locale** encoding —
   cp1252 on this French install — so descriptions carrying `mm³` and `Ø`
   reached ruff as mojibake and it refused the stream as invalid UTF-8. Silent
   again, because the fallback returns the source unformatted.

**With both fixed the table was genuinely stale, and substantively so:**
`catia_fillet.radius_mm` and `catia_chamfer.length_mm` still declared a bare
number, so the daemon would have refused the per-entity list the registry now
accepts — which is the exact call E2's Proof makes. Regenerated (20 lines).

### D8 — `.env.example` did not document `AI_VISION_MODEL`. **Code.** Fixed.

Caught by `test_repository_hygiene`. Documented, including why a text-only model
is refused by name rather than trusted.

### D9 — A feature cannot be named through the product path. **Reported, not fixed.**

The OCCT kernel reads `arguments["name"]`
(`occt/operations/context.py::feature_name`) and uses it to name the feature —
which is what makes `feature#selector` work, the whole of E2.2. But `catia_pad`'s
declared schema has no `name` parameter, so `dispatch.validate()` rejects it
before the kernel sees it:

```
catia_pad: arguments has unknown field(s): name.
Accepted: direction, length_mm, limit, reversed, second_length_mm,
          sketch, symmetric, thickness_mm, thin, up_to
```

So from a conversation no feature can be given a name, and `feature#selector` is
unreachable — only a compiled `DesignSpec` can name features, because `compile`
renames them internally.

**Not fixed: this is a vocabulary decision, not a bug fix.** Adding `name` across
the registry changes the generated daemon table and means something different on
a CATIA seat.

Worth noting alongside it: `tests/test_geometry_backends.py` calls
`runner("catia_pad", {"name": "slab", ...})` — passing an argument the product
schema forbids. The test passes because it bypasses validation, which makes it
misleading about the path it is taken to prove. Precisely the "a test written
from the same understanding as the code can be wrong in the same direction" case
the runbook names.

### D10 — The test suite inherits the developer's backend choice. **Test hygiene. Reported.**

With `GEOMETRY_BACKEND=occt` in `.env.local`,
`tests/test_catia_api.py::test_status_reports_nothing_paired_before_any_device_exists`
and `test_status_distinguishes_paired_from_connected` fail, because the local
branch reports `connected: true` unconditionally. Both pass with the variable set
to `catia`. The suite should pin the backend rather than inherit it.

---

## The model on this workstation

**`qwen3-coder:30b` is the strongest available and also the faster of the two.**
Both were measured against the real 108-tool payload the OCCT backend produces.

| | qwen3-coder:30b | gpt-oss:20b |
|---|---|---|
| Emits tool calls at 108 tools | **yes**, correct multi-call | **no** — answered in prose |
| Wall time, same payload | **65 s** | 120 s |
| GPU residency | 30% | ~40% |

**The stored note that `qwen3-coder:30b` cannot emit tool calls is out of date.**
Re-measured today against `/api/chat`: it returns a correct structured
`tool_calls` array, both with one tool and with all 108. What is *newly* true is
the opposite finding — **`gpt-oss:20b` fails at 108 tools**, returning no
`tool_calls` and 2,152 characters of prose reasoning about which tool it ought to
call. The earlier benchmark that favoured it used ~26 tools.

It remains a weak agent: it guesses argument names not in the schema
(`face`/`depth_mm` on `catia_pad`), calls tools before their prerequisites, and
gives up into a clarifying question. Every one of those was *caught and named* by
the server's own refusals, which is the validation layer working.

### Why it is slow, honestly

Ollama **is** using the GPU. The card is 8151 MiB; the model is 20.6 GB, so only
~30% fits and the rest runs from system RAM. CATIA's viewport holds ~2 GB of the
same card. Enabling `OLLAMA_FLASH_ATTENTION=1` and `OLLAMA_KV_CACHE_TYPE=q8_0`
moved residency 28% → 30% — the bottleneck is weights, not KV cache. No setting
makes a 20.6 GB model fit in 8 GB.

The larger cost is structural and worth a product decision:
**`prompt_eval: 16386 tokens in 35.8 s`, with `prompt_eval_cached_count: 0`.**
The 108 tool schemas are ~16k tokens re-evaluated on *every* turn. Tool retrieval
(Phase 16) is what would fix this, and this is a concrete measurement in its
favour.

---

## Tier 4 — the real CATIA seat

CATIA was launched **through the product's own endpoint** (`POST /catia/launch`,
which is the sanctioned path — the daemon deliberately never starts a seat
itself, because that spends a licence nobody asked to spend). It came up as
`V5-R33`, and the bridge auto-paired and connected: `mock: false`,
`ui_language: "fr"`, capabilities `part, sketch, measure, export, capture,
checkpoint`.

The build was driven from the chat endpoint and produced a real solid on the
seat — screenshots `catia-01-launched.png` … `catia-04-hole.png` beside this
file. The seat is French, so the features come back named `Extrusion.1` and
`Poche.1`.

| Step | Result on the seat |
|---|---|
| `catia_new_part` | `MyNewPart.CATPart`, title bar confirms |
| `catia_sketch_create` / `catia_sketch_rectangle` | sketch `profile` |
| `catia_pad` | `Extrusion.1`, `volume_mm3: 48000.0` |
| `catia_hole` Ø14 through | `Poche.1`, `volume_mm3: 44921.2392` |

### E1 / E3 — the cross-backend conformance halves, discharged

The board has carried these as the outstanding residual on E1 and E3 since both
phases shipped: the same compiled plan through `OcctRunner` and through a CATIA
seat must build the same part, and every measurement must agree to a declared
tolerance. The OCCT half passed; the seat half had never run. **Both halves now
run, and they agree.**

| Quantity | OCCT | CATIA seat | Closed form |
|---|---|---|---|
| Pad volume mm³ | 48000.0 | 48000.0 | 48000 |
| After Ø14 hole mm³ | 44921.239199482 | 44921.2392 | 44921.23919948201 |
| Bounding box mm | 60.0000002 × 40.0000002 × 20.0000002 | 60.0 × 40.0 × 20.0 | 60 × 40 × 20 |

Volume agreement is **1.2 × 10⁻¹¹ relative** — the visible difference is CATIA
rounding its own reported value to four decimals. The bounding box differs by
1 × 10⁻⁷ mm, which is `BRepBndLib`'s tolerance on the OCCT side (the same
artefact as D5) and is a tenth of a nanometre.

Both backends also agree with the closed form, so this is not two
implementations agreeing on a shared mistake.

### D11 — A floating toolbar was read as an open modal dialog. **Code.** Fixed.

The most serious thing tier 4 found, and it could only be found on a real seat.

`ui_automation.active_dialog` asks Windows `GW_ENABLEDPOPUP` — "which enabled
popup does this window own" — which is the right question for a dialog and the
wrong one for CATIA. **CATIA implements an undocked toolbar as an enabled popup
owned by the main frame**, so a floating toolbar came back as the dialog CATIA
was waiting on.

Measured on this seat, where four toolbars were floating:

```
GW_ENABLEDPOPUP -> 461072
  title: 'PartDesign Feature Recognition'
  class: 'N/A [ l_CATDlgFloatingFrame ]'
  main enabled? True          <- nothing modal was up at all
```

The cost is not cosmetic. `catia_run_command` refuses while a dialog is open, so
it refused **every** call with "A CATIA dialog is already open and waiting for
input" — for as long as anything was undocked, which on a working seat is
always. The whole interactive command family was unreachable. This is exactly
the over-refusal `ui_policy` warns about: the agent's recovery from a refusal is
to try something else, and something else is how a part gets built wrong. It was
observed live doing precisely that — it tried `catia_dialog_action cancel` on a
toolbar.

Fixed by excluding the floating-frame class, and by falling back to enumerating
owned popups rather than trusting the single window `GW_ENABLEDPOPUP` names —
because a real dialog can be open *behind* a toolbar, and taking `None` for an
answer there would be the opposite and more dangerous failure. Pinned by
`TestAFloatingToolbarIsNotADialog`, including that case.

### The operations that are actually risky

A 60×40×20 pad proves the plumbing and almost nothing else. These are the four
things previously recorded on this machine as broken or unobtainable, each
checked against a closed form on an **80×40×20 rectangular** plate — a square one
hides the pattern defect.

| Operation | Previously recorded | Measured now |
|---|---|---|
| `catia_set_material` | silently falls back to steel, mass ~3× heavy | **CORRECT** — implied density 2710 kg/m³, `Aluminium` in the tree |
| `catia_fillet` | edge references unobtainable over COM | **CORRECT** — `Congé arête.1`, removed 429.204 mm³ against closed form 429.204 |
| `catia_pattern_rectangular` | copies step diagonally / none appear | **STILL BROKEN** — see below |
| `catia_shell` | hollows with no face removed | hollows, no failure; 63570.8 → 20814.9 mm³ |

Two of the three recorded defects are **no longer reproducible** — material
attaches correctly and fillets now find their edges, exactly to the closed form.
Those notes are out of date.

**D12 — `catia_pattern_rectangular` places copies diagonally. Reported, not fixed.**

The number identifies the failure mode precisely. On the 80×40×20 plate
(x −40…40, y −20…20) with a Ø10 through hole at the centre, a rectangular
pattern of `count: 3`, `spacing_mm: 20` about the `YZ` plane should place copies
at x = 0, 20, 40 and remove **two** further holes:

```
one hole            = π·5²·20 = 1570.796 mm³
expected removal    = 2 × 1570.796 = 3141.593 mm³
actual removal      =     785.398 mm³   ( = exactly half of one hole )
```

Half of one hole is what a copy landing **exactly on a corner edge** removes. That
is the diagonal step: instead of x = 20 and x = 40, the copies went to
(20, 20) and (40, 40) — the first straddling the y = 20 edge and taking half a
cylinder with it, the second entirely off the part and taking nothing. The
screenshot shows it as a half-moon notch bitten out of the side, with no second
hole anywhere on the face.

So the operation reports success (`Répétition rectangulaire.1`), the mass moves,
and the part is wrong — the worst shape a defect can take. Not fixed here: it is
a `ShapeFactory` argument problem in the COM layer (`AddNewRectPattern` takes
twelve arguments and refuses lines, directions and axis systems as direction
references), and getting it right needs its own session against the API rather
than a change made in passing.

### The three Win32 unknowns

The protocol doc lists these as unanswerable from Linux. Two are now answered.

**What are CATIA's real window classes?** — **Answered.**

| Window | Class |
|---|---|
| Main frame | `CATDlgDocument [ l_CATDlgMfcDocumentMDI ]` |
| Floating toolbar | `N/A [ l_CATDlgFloatingFrame ]` |

Both arrive *decorated* — the real class name is inside `[ ]`, with a prefix
before it — which is why D11's fix matches on a substring rather than equality.
Nobody would have guessed this shape.

**Do CATIA's dialogs answer `WM_GETTEXT`?** — **Yes, for child controls**, with a
caveat worth keeping. `describe_dialog` read real labels off the popup's
children ("Manual Feature Recognition", "Automatic Feature Recognition", "Part
Analysis", "Décomposition") and `dialog_action` correctly refused an action the
window did not offer *and listed what it did offer*. Note the labels come back
in **mixed English and French on a French seat**, which is CATIA's own
inconsistency and a good argument for `ButtonRole` + `STANDARD_CONTROL_IDS`
rather than label matching. The caveat: the window read was a floating toolbar,
so this confirms the text-reading path against real CATIA widgets but not
against a genuinely modal dialog.

**Is `EN_CHANGE` needed after setting an edit field?** — **Still unanswered.** No
edit field was filled; it needs a modal dialog with a text box open, which the
build path above never produced.

---

## Not done in this session

- **The vision check (tier 5).** `llava` not pulled; the refusal path not
  exercised.
- **The four corner fillets** on the OCCT plate (see Tier 3).
- **`EN_CHANGE`**, as above.
- **`catia_run_command` end to end.** D11 unblocks it, but no command was driven
  through the menu after the fix.
